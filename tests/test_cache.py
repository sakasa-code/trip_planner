"""缓存层单元测试：进程内 LRU 与统一 CacheService（内存后端）。"""
import asyncio
import time
import unittest

from trip_planner.cache import CacheService, _LRUMemory


class LRUMemoryTest(unittest.TestCase):
    def test_set_get_roundtrip(self):
        m = _LRUMemory(max_entries=10)
        m.set("a", {"x": 1})
        self.assertEqual(m.get("a"), {"x": 1})

    def test_ttl_expiry(self):
        m = _LRUMemory()
        m.set("a", 1, ttl=-1)  # 负 TTL 表示已过期
        self.assertIsNone(m.get("a"))

    def test_no_ttl_means_persistent(self):
        m = _LRUMemory()
        m.set("a", 1)  # 无 TTL
        self.assertEqual(m.get("a"), 1)

    def test_missing_key(self):
        m = _LRUMemory()
        self.assertIsNone(m.get("nope"))

    def test_lru_eviction(self):
        m = _LRUMemory(max_entries=2)
        m.set("a", 1)
        m.set("b", 2)
        m.get("a")          # 让 a 成为最近使用
        m.set("c", 3)       # 淘汰最久未使用的 b
        self.assertIsNotNone(m.get("a"))
        self.assertIsNone(m.get("b"))
        self.assertIsNotNone(m.get("c"))

    def test_delete(self):
        m = _LRUMemory()
        m.set("a", 1)
        m.set("b", 2)
        self.assertEqual(m.delete("a", "c"), 1)
        self.assertIsNone(m.get("a"))


class CacheServiceMemoryTest(unittest.TestCase):
    def setUp(self):
        self.cache = CacheService()  # 未 init，后端为 memory

    def test_set_get_json_roundtrip(self):
        value = {"name": "故宫", "list": [1, 2, 3]}
        asyncio.run(self.cache.set("k", value, ttl=60))
        self.assertEqual(asyncio.run(self.cache.get("k")), value)

    def test_get_missing_returns_none(self):
        self.assertIsNone(asyncio.run(self.cache.get("missing")))

    def test_backend_property(self):
        self.assertEqual(self.cache.backend, "memory")
        self.assertFalse(self.cache.is_redis)


if __name__ == "__main__":
    unittest.main()
