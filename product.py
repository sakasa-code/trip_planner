"""企业产品 CRUD。"""
import json
import sqlite3
import time

from .db import get_conn


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
        for key in ("tags", "photos"):
            if p.get(key):
                try:
                    p[key] = json.loads(p[key])
                except (json.JSONDecodeError, TypeError):
                    p[key] = []
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
                    try:
                        p[key] = json.loads(p[key])
                    except (json.JSONDecodeError, TypeError):
                        p[key] = []
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