"""统一缓存服务：Redis 优先，不可用时自动降级进程内 LRU。

设计原则：
- 单例 CacheService，全局共享
- 初始化时尝试连接 Redis，ping 失败则降级
- 所有方法返回 None 表示未命中，不抛异常
- 序列化用 json.dumps/json.loads，POI 数据类型足够
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Optional


class _LRUMemory:
    """进程内 LRU 缓存，Redis 不可用时的降级方案。

    用 OrderedDict 实现 O(1) 存取和淘汰。TTL 检查在 get 时做。
    """

    def __init__(self, max_entries: int = 5000):
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expire_ts, val = item
            if expire_ts and expire_ts < time.time():
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return val

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        expire_ts = (time.time() + ttl) if ttl else None
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (expire_ts, value)
            if len(self._store) > self._max:
                self._store.popitem(last=False)

    def delete(self, *keys: str) -> int:
        with self._lock:
            count = 0
            for k in keys:
                if self._store.pop(k, None) is not None:
                    count += 1
            return count

    def expire(self, key: str, ttl: int) -> None:
        with self._lock:
            item = self._store.get(key)
            if item is not None:
                _, val = item
                self._store[key] = (time.time() + ttl, val)

    def geoadd(self, key: str, lng: float, lat: float, member: str) -> None:
        with self._lock:
            # 简化实现：存 dict 格式，不支持复杂 GEO 操作
            geo_map = self._store.get(key)
            if geo_map is None or (geo_map[0] and geo_map[0] < time.time()):
                geo_map = (None, {})  # (expire_ts, {member: (lng, lat)})
            geo_map[1][member] = (lng, lat)
            self._store[key] = geo_map


class CacheService:
    """统一缓存服务。Redis 可用时走 Redis，否则自动降级进程内 LRU。"""

    _instance: Optional["CacheService"] = None
    _initialized: bool = False

    def __init__(self):
        self._redis = None   # 真实 Redis 客户端（redis.asyncio）
        self._fake = None    # fakeredis 同步客户端
        self._memory = _LRUMemory()
        self._backend = "memory"  # 初始假设降级；init() 后更新
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "CacheService":
        """获取单例，未初始化时返回未初始化的实例（init 会自动被调用）。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def init(self, redis_url: Optional[str] = None) -> None:
        """初始化缓存后端，幂等。

        三级降级：
        1. REDIS_URL 配置 → 连接真实 Redis（多实例共享、持久化）
        2. 无 REDIS_URL 但 fakeredis 可用 → 自动启用 fakeredis（纯 Python，随服务器启动即就绪，零额外进程）
        3. 最后兜底 → 进程内 LRU
        """
        with self._lock:
            if self._initialized:
                return
            self._initialized = True

            # 1. 真实 Redis（用户显式配置了连接串）
            if redis_url:
                try:
                    import redis.asyncio as aioredis  # type: ignore
                    self._redis = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
                    await self._redis.ping()
                    self._backend = "redis"
                    return
                except Exception:
                    self._redis = None

            # 2. fakeredis 自动兜底（零配置、随 Python 进程启动）
            try:
                import fakeredis
                server = fakeredis.FakeServer()
                self._fake = fakeredis.FakeRedis(server=server, encoding="utf-8", decode_responses=True)
                self._backend = "fakeredis"
                return
            except ImportError:
                self._fake = None

            # 3. 最终兜底：进程内 LRU
            self._backend = "memory"

    # —— fakeredis 同步→async 适配 ——
    async def _fget(self, key):
        return await asyncio.to_thread(self._fake.get, key)

    async def _fsetex(self, key, ttl, val):
        return await asyncio.to_thread(self._fake.setex, key, ttl, val)

    async def _fset(self, key, val):
        return await asyncio.to_thread(self._fake.set, key, val)

    async def _fdelete(self, *keys):
        return await asyncio.to_thread(self._fake.delete, *keys)

    async def _fexpire(self, key, ttl):
        return await asyncio.to_thread(self._fake.expire, key, ttl)

    async def _fgeoadd(self, key, lng, lat, member):
        """fakeredis geoadd：redis-py 新版签名接收扁平数组 [lng, lat, name, ...]"""
        return await asyncio.to_thread(self._fake.geoadd, key, [lng, lat, member])

    # —— 通用键值操作 ——

    async def get(self, key: str) -> Optional[Any]:
        """获取缓存值。返回 None 表示未命中或已过期。"""
        try:
            if self._backend == "redis" and self._redis:
                raw = await self._redis.get(key)
                if raw is None:
                    return None
                return json.loads(raw)
            if self._backend == "fakeredis" and self._fake:
                raw = await self._fget(key)
                if raw is None:
                    return None
                return json.loads(raw)
        except Exception:
            self._backend = "memory"
        return self._memory.get(key)

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        try:
            payload = json.dumps(value, ensure_ascii=False)
            if self._backend == "redis" and self._redis:
                if ttl:
                    await self._redis.setex(key, ttl, payload)
                else:
                    await self._redis.set(key, payload)
                return
            if self._backend == "fakeredis" and self._fake:
                if ttl:
                    await self._fsetex(key, ttl, payload)
                else:
                    await self._fset(key, payload)
                return
        except Exception:
            self._backend = "memory"
        self._memory.set(key, value, ttl)

    async def delete(self, *keys: str) -> int:
        try:
            if self._backend == "redis" and self._redis:
                return await self._redis.delete(*keys)
            if self._backend == "fakeredis" and self._fake:
                return await self._fdelete(*keys)
        except Exception:
            pass
        return self._memory.delete(*keys)

    async def expire(self, key: str, ttl: int) -> None:
        try:
            if self._backend == "redis" and self._redis:
                await self._redis.expire(key, ttl)
                return
            if self._backend == "fakeredis" and self._fake:
                await self._fexpire(key, ttl)
                return
        except Exception:
            pass
        self._memory.expire(key, ttl)

    # —— GEO 操作 ——

    async def geoadd(self, key: str, lng: float, lat: float, member: str, ttl: Optional[int] = None) -> None:
        try:
            if self._backend == "redis" and self._redis:
                await self._redis.geoadd(key, [lng, lat, member])
                if ttl:
                    await self._redis.expire(key, ttl)
                return
            if self._backend == "fakeredis" and self._fake:
                await self._fgeoadd(key, lng, lat, member)
                if ttl:
                    await self._fexpire(key, ttl)
                return
        except Exception:
            self._backend = "memory"
        self._memory.geoadd(key, lng, lat, member)
        if ttl:
            self._memory.expire(key, ttl)

    async def delete_geo(self, key: str) -> None:
        """重建 GEO 索引前先删除旧 key。"""
        await self.delete(key)

    # —— 状态查询 ——

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def is_redis(self) -> bool:
        return self._backend in ("redis", "fakeredis")
