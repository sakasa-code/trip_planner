"""产品数据统计。"""
import sqlite3
import time

from .db import get_conn


def increment_product_stats(product_id: int, field: str = "recommend") -> None:
    """推荐引擎每次推荐/点击/收藏企业产品时调用。

    用一条原子 SQL 完成「跨日重置 + 累加」，
    避免 SELECT-then-UPDATE 跨请求竞态导致今日计数不准确。
    """
    if field not in ("recommend", "click", "favorite"):
        return
    conn = get_conn()
    try:
        now = int(time.time())
        today = time.strftime("%Y-%m-%d")

        conn.execute(
            "INSERT OR IGNORE INTO product_stats (product_id, stat_date, updated_at) VALUES (?, ?, ?)",
            (product_id, today, now),
        )

        column = f"{field}_count"
        if field == "favorite":
            sql = f"""UPDATE product_stats
                       SET stat_date = CASE WHEN stat_date != ? THEN ? ELSE stat_date END,
                           {column} = {column} + 1,
                           updated_at = ?
                       WHERE product_id = ?"""
            conn.execute(sql, (today, today, now, product_id))
        else:
            today_col = f"today_{field}"
            sql = f"""UPDATE product_stats
                       SET stat_date = CASE WHEN stat_date != ? THEN ? ELSE stat_date END,
                           {today_col} = CASE WHEN stat_date != ? THEN 1 ELSE {today_col} + 1 END,
                           {column} = {column} + 1,
                           updated_at = ?
                       WHERE product_id = ?"""
            conn.execute(sql, (today, today, today, now, product_id))
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