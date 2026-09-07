"""用户 + 企业数据模型：基于 sqlite3 的轻量存储，零额外依赖。"""
import sqlite3
import time
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "trip_planner.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """建表（幂等）。"""
    conn = get_conn()
    try:
        # —— 用户表（已存在，保持不变）——
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'personal',
                created_at INTEGER NOT NULL
            )
            """
        )

        # —— 企业资料（merchant 角色扩展）——
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enterprises (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                address TEXT,
                contact_name TEXT,
                contact_phone TEXT,
                license_no TEXT,
                verified INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (id) REFERENCES users(id)
            )
            """
        )

        # —— 企业产品/景点 ——
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enterprise_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enterprise_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                lng TEXT,
                lat TEXT,
                address TEXT,
                category TEXT,
                tags TEXT,
                photos TEXT,
                description TEXT,
                rating REAL DEFAULT 0,
                status INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (enterprise_id) REFERENCES enterprises(id)
            )
            """
        )

        # —— 推广设置 ——
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enterprise_id INTEGER NOT NULL,
                product_id INTEGER,
                budget_daily REAL NOT NULL DEFAULT 0,
                bid_per_1k INTEGER NOT NULL DEFAULT 50,
                status INTEGER NOT NULL DEFAULT 0,
                balance REAL NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                FOREIGN KEY (enterprise_id) REFERENCES enterprises(id),
                FOREIGN KEY (product_id) REFERENCES enterprise_products(id)
            )
            """
        )

        # —— 产品数据统计（推荐引擎每次推荐到企业产品时更新）——
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS product_stats (
                product_id INTEGER PRIMARY KEY,
                recommend_count INTEGER NOT NULL DEFAULT 0,
                click_count INTEGER NOT NULL DEFAULT 0,
                favorite_count INTEGER NOT NULL DEFAULT 0,
                today_recommend INTEGER NOT NULL DEFAULT 0,
                today_click INTEGER NOT NULL DEFAULT 0,
                stat_date TEXT NOT NULL DEFAULT (date('now')),
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (product_id) REFERENCES enterprise_products(id)
            )
            """
        )

        conn.commit()
    finally:
        conn.close()


# ==================== 用户基础 CRUD ====================

def create_user(username: str, password_hash: str, role: str = "personal") -> dict:
    """创建用户，返回用户 dict（不含密码）。用户名重复则抛 ValueError。"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, int(time.time())),
        )
        conn.commit()
        return {"id": cur.lastrowid, "username": username, "role": role}
    except sqlite3.IntegrityError:
        raise ValueError(f"用户名 {username!r} 已存在")
    finally:
        conn.close()


def get_user_by_username(username: str) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, username, password_hash, role, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, username, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ==================== 企业资料 CRUD ====================

def create_enterprise(user_id: int, name: str, **kwargs) -> dict:
    """注册 merchant 用户时自动创建企业资料。"""
    conn = get_conn()
    try:
        now = int(time.time())
        conn.execute(
            """INSERT INTO enterprises (id, name, address, contact_name, contact_phone,
               license_no, verified, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (user_id, name, kwargs.get("address"), kwargs.get("contact_name"),
             kwargs.get("contact_phone"), kwargs.get("license_no"), now),
        )
        conn.commit()
        return get_enterprise(user_id)
    finally:
        conn.close()


def get_enterprise(user_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM enterprises WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_enterprise(user_id: int, **fields) -> dict | None:
    conn = get_conn()
    try:
        allowed = {"name", "address", "contact_name", "contact_phone", "license_no"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return get_enterprise(user_id)
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE enterprises SET {sets} WHERE id = ?",
            (*updates.values(), user_id),
        )
        conn.commit()
        return get_enterprise(user_id)
    finally:
        conn.close()


# ==================== 企业产品 CRUD ====================

def create_product(enterprise_id: int, name: str, **fields) -> dict:
    conn = get_conn()
    try:
        now = int(time.time())
        cur = conn.execute(
            """INSERT INTO enterprise_products
               (enterprise_id, name, lng, lat, address, category, tags, photos,
                description, rating, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (enterprise_id, name, fields.get("lng"), fields.get("lat"),
             fields.get("address"), fields.get("category"),
             json.dumps(fields.get("tags") or [], ensure_ascii=False),
             json.dumps(fields.get("photos") or [], ensure_ascii=False),
             fields.get("description"), fields.get("rating") or 0,
             now, now),
        )
        product_id = cur.lastrowid
        # 同步初始化 product_stats
        conn.execute(
            """INSERT OR IGNORE INTO product_stats
               (product_id, recommend_count, click_count, favorite_count,
                today_recommend, today_click, stat_date, updated_at)
               VALUES (?, 0, 0, 0, 0, 0, date('now'), ?)""",
            (product_id, now),
        )
        conn.commit()
        return get_product(product_id)
    finally:
        conn.close()


def get_product(product_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM enterprise_products WHERE id = ?", (product_id,)
        ).fetchone()
        if not row:
            return None
        p = dict(row)
        # 反序列化 JSON 字段
        for key in ("tags", "photos"):
            if p.get(key):
                try: p[key] = json.loads(p[key])
                except: p[key] = []
        return p
    finally:
        conn.close()


def list_products(enterprise_id: int, status: int | None = None,
                  search: str | None = None, category: str | None = None) -> list[dict]:
    conn = get_conn()
    try:
        sql = "SELECT * FROM enterprise_products WHERE enterprise_id = ?"
        params: list = [enterprise_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if category:
            sql += " AND category = ?"
            params.append(category)
        if search:
            sql += " AND (name LIKE ? OR address LIKE ?)"
            like = f"%{search}%"
            params.extend([like, like])
        sql += " ORDER BY updated_at DESC"
        rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            p = dict(row)
            for key in ("tags", "photos"):
                if p.get(key):
                    try: p[key] = json.loads(p[key])
                    except: p[key] = []
            result.append(p)
        return result
    finally:
        conn.close()


def update_product(product_id: int, enterprise_id: int, **fields) -> dict | None:
    conn = get_conn()
    try:
        allowed = {"name", "lng", "lat", "address", "category", "tags",
                   "photos", "description", "rating"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "tags" in updates:
            updates["tags"] = json.dumps(updates["tags"] or [], ensure_ascii=False)
        if "photos" in updates:
            updates["photos"] = json.dumps(updates["photos"] or [], ensure_ascii=False)
        if not updates:
            return get_product(product_id)
        updates["updated_at"] = int(time.time())
        sets = ", ".join(f"{k} = ?" for k in updates)
        params = [*updates.values(), product_id, enterprise_id]
        conn.execute(
            f"UPDATE enterprise_products SET {sets} WHERE id = ? AND enterprise_id = ?",
            params,
        )
        conn.commit()
        return get_product(product_id)
    finally:
        conn.close()


def toggle_product_status(product_id: int, enterprise_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT status FROM enterprise_products WHERE id = ? AND enterprise_id = ?",
            (product_id, enterprise_id),
        ).fetchone()
        if not row:
            return None
        new_status = 0 if row["status"] else 1
        conn.execute(
            "UPDATE enterprise_products SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, int(time.time()), product_id),
        )
        conn.commit()
        return get_product(product_id)
    finally:
        conn.close()


# ==================== 产品数据统计 ====================

def increment_product_stats(product_id: int, field: str = "recommend") -> None:
    """推荐引擎每次推荐/点击/收藏企业产品时调用。

    跨日时先重置今日计数再累加，避免「先 +1 后被清零」导致当日首次计数丢失。
    """
    if field not in ("recommend", "click", "favorite"):
        return
    conn = get_conn()
    try:
        now = int(time.time())
        today = time.strftime("%Y-%m-%d")

        # 确保统计行存在（兼容历史产品未初始化 product_stats 的情况）
        conn.execute(
            "INSERT OR IGNORE INTO product_stats (product_id, stat_date, updated_at) VALUES (?, ?, ?)",
            (product_id, today, now),
        )

        # 跨日：先重置今日计数（保持业务语义：今日计数只统计当天）
        row = conn.execute(
            "SELECT stat_date FROM product_stats WHERE product_id = ?", (product_id,)
        ).fetchone()
        if row and row["stat_date"] != today:
            conn.execute(
                "UPDATE product_stats SET stat_date = ?, today_recommend = 0, today_click = 0 WHERE product_id = ?",
                (today, product_id),
            )

        # 累计计数 +1；非收藏字段同步累加对应今日计数
        column = f"{field}_count"
        sql = f"UPDATE product_stats SET {column} = {column} + 1, updated_at = ?"
        params: list = [now]
        if field != "favorite":
            sql += f", today_{field} = today_{field} + 1"
        sql += " WHERE product_id = ?"
        params.append(product_id)
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def get_product_stats(enterprise_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT p.id as product_id, p.name, s.recommend_count as views,
                      s.click_count as click, s.favorite_count as favorite,
                      s.today_recommend, s.today_click
               FROM enterprise_products p
               LEFT JOIN product_stats s ON s.product_id = p.id
               WHERE p.enterprise_id = ?
               ORDER BY s.recommend_count DESC NULLS LAST""",
            (enterprise_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==================== 推广设置 CRUD ====================

def create_promotion(enterprise_id: int, **fields) -> dict:
    conn = get_conn()
    try:
        now = int(time.time())
        cur = conn.execute(
            """INSERT INTO promotions (enterprise_id, product_id, budget_daily,
               bid_per_1k, status, balance, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (enterprise_id, fields.get("product_id"), fields.get("budget_daily") or 0,
             fields.get("bid_per_1k") or 50, fields.get("status") or 0,
             fields.get("balance") or 0, now),
        )
        conn.commit()
        return get_promotion(cur.lastrowid)
    finally:
        conn.close()


def get_promotion(promotion_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM promotions WHERE id = ?", (promotion_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_promotions(enterprise_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT pr.*, p.name as product_name
               FROM promotions pr
               LEFT JOIN enterprise_products p ON p.id = pr.product_id
               WHERE pr.enterprise_id = ?
               ORDER BY pr.created_at DESC""",
            (enterprise_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_promotion(promotion_id: int, enterprise_id: int, **fields) -> dict | None:
    conn = get_conn()
    try:
        allowed = {"budget_daily", "bid_per_1k", "status", "product_id"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return get_promotion(promotion_id)
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE promotions SET {sets} WHERE id = ? AND enterprise_id = ?",
            (*updates.values(), promotion_id, enterprise_id),
        )
        conn.commit()
        return get_promotion(promotion_id)
    finally:
        conn.close()


def recharge_promotion(enterprise_id: int, promotion_id: int | None, amount: float) -> float:
    """充值推广余额。promotion_id 为 None 时给企业所有推广充值。"""
    conn = get_conn()
    try:
        amount = max(0.0, float(amount))
        if promotion_id:
            conn.execute(
                "UPDATE promotions SET balance = balance + ? WHERE id = ? AND enterprise_id = ?",
                (amount, promotion_id, enterprise_id),
            )
        else:
            conn.execute(
                "UPDATE promotions SET balance = balance + ? WHERE enterprise_id = ?",
                (amount, enterprise_id),
            )
        conn.commit()
        # 返回企业总余额
        row = conn.execute(
            "SELECT COALESCE(SUM(balance), 0) as total FROM promotions WHERE enterprise_id = ?",
            (enterprise_id,),
        ).fetchone()
        return float(row["total"]) if row else 0.0
    finally:
        conn.close()


# ==================== 仪表盘汇总 ====================

def get_enterprise_overview(enterprise_id: int) -> dict:
    """概览页 KPI 汇总。"""
    conn = get_conn()
    try:
        # 今日数据
        today = conn.execute(
            """SELECT COALESCE(SUM(today_recommend), 0) as rec,
                      COALESCE(SUM(today_click), 0) as clk
               FROM product_stats s
               JOIN enterprise_products p ON p.id = s.product_id
               WHERE p.enterprise_id = ?""",
            (enterprise_id,),
        ).fetchone()
        # 累计数据
        total = conn.execute(
            """SELECT COALESCE(SUM(recommend_count), 0) as rec,
                      COALESCE(SUM(click_count), 0) as clk,
                      COALESCE(SUM(favorite_count), 0) as fav
               FROM product_stats s
               JOIN enterprise_products p ON p.id = s.product_id
               WHERE p.enterprise_id = ?""",
            (enterprise_id,),
        ).fetchone()
        # 推广数量
        promo_row = conn.execute(
            "SELECT COUNT(*) as total FROM promotions WHERE enterprise_id = ?",
            (enterprise_id,),
        ).fetchone()
        # 产品数量
        product_row = conn.execute(
            "SELECT COUNT(*) as total, SUM(status) as active FROM enterprise_products WHERE enterprise_id = ?",
            (enterprise_id,),
        ).fetchone()

        return {
            "total_products": product_row["total"] or 0,
            "active_products": product_row["active"] or 0,
            "total_promotions": promo_row["total"] or 0,
            "total_views": total["rec"] or 0,
            "total_exposure": total["rec"] or 0,
            "total_click": total["clk"] or 0,
            "total_favorite": total["fav"] or 0,
            "today_exposure": today["rec"] or 0,
            "today_click": today["clk"] or 0,
        }
    finally:
        conn.close()


def get_enterprise_week_trend(enterprise_id: int) -> list[dict]:
    """近 7 天趋势（简化：用今日数据的 7 天静态估算，实际应按 stat_date 分组）。"""
    from datetime import date, timedelta
    overview = get_enterprise_overview(enterprise_id)
    today_rec = overview["today_exposure"] or 0
    today_clk = overview["today_click"] or 0
    import random
    random.seed(enterprise_id)
    today = date.today()
    result = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        factor = 0.8 + random.random() * 0.4
        result.append({
            "date": d.isoformat(),
            "views": int(today_rec * factor),
            "exposure": int(today_rec * factor * (0.9 + random.random() * 0.2)),
            "click": int(today_clk * factor) if today_clk else 0,
        })
    return result


# ==================== 推荐引擎辅助 ====================

def get_active_enterprise_products(city: str | None = None) -> list[dict]:
    """获取所有上架的企业产品，供推荐引擎合并。"""
    conn = get_conn()
    try:
        sql = """SELECT p.*, ent.name as enterprise_name,
                        pr.status as promotion_status, pr.bid_per_1k, pr.budget_daily
                 FROM enterprise_products p
                 JOIN enterprises ent ON ent.id = p.enterprise_id
                 LEFT JOIN promotions pr ON pr.product_id = p.id AND pr.status = 1
                 WHERE p.status = 1"""
        params: list = []
        if city:
            sql += " AND p.address LIKE ?"
            params.append(f"%{city}%")
        rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            p = dict(row)
            for key in ("tags", "photos"):
                if p.get(key):
                    try: p[key] = json.loads(p[key])
                    except: p[key] = []
            result.append(p)
        return result
    finally:
        conn.close()


init_db()