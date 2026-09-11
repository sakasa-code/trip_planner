"""推广设置 CRUD。"""
import sqlite3
import time

from .db import get_conn


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
        row = conn.execute(
            "SELECT COALESCE(SUM(balance), 0) as total FROM promotions WHERE enterprise_id = ?",
            (enterprise_id,),
        ).fetchone()
        return float(row["total"]) if row else 0.0
    finally:
        conn.close()