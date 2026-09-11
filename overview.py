"""仪表盘汇总与推荐引擎辅助查询。"""
import json
from datetime import date, timedelta

from .db import get_conn


def get_enterprise_overview(enterprise_id: int) -> dict:
    """概览页 KPI 汇总。"""
    conn = get_conn()
    try:
        today = conn.execute(
            """SELECT COALESCE(SUM(today_recommend), 0) as rec,
                      COALESCE(SUM(today_click), 0) as clk
               FROM product_stats s
               JOIN enterprise_products p ON p.id = s.product_id
               WHERE p.enterprise_id = ?""",
            (enterprise_id,),
        ).fetchone()
        total = conn.execute(
            """SELECT COALESCE(SUM(recommend_count), 0) as rec,
                      COALESCE(SUM(click_count), 0) as clk,
                      COALESCE(SUM(favorite_count), 0) as fav
               FROM product_stats s
               JOIN enterprise_products p ON p.id = s.product_id
               WHERE p.enterprise_id = ?""",
            (enterprise_id,),
        ).fetchone()
        promo_row = conn.execute(
            "SELECT COUNT(*) as total FROM promotions WHERE enterprise_id = ?",
            (enterprise_id,),
        ).fetchone()
        product_row = conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN status = 1 THEN 1 ELSE 0 END) as active FROM enterprise_products WHERE enterprise_id = ?",
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
    """近 7 天趋势：按 product_stats 的 stat_date 字段分组统计每日曝光/点击。"""
    conn = get_conn()
    try:
        today_date = date.today()
        start = today_date - timedelta(days=6)
        rows = conn.execute(
            """SELECT s.stat_date,
                      COALESCE(SUM(s.today_recommend), 0) as exposure,
                      COALESCE(SUM(s.today_click), 0) as click
               FROM product_stats s
               JOIN enterprise_products p ON p.id = s.product_id
               WHERE p.enterprise_id = ? AND s.stat_date BETWEEN ? AND ?
               GROUP BY s.stat_date
               ORDER BY s.stat_date""",
            (enterprise_id, start.isoformat(), today_date.isoformat()),
        ).fetchall()
        day_map = {r["stat_date"]: r for r in rows}
        result = []
        for i in range(6, -1, -1):
            d = (today_date - timedelta(days=i)).isoformat()
            row = day_map.get(d) or {}
            result.append({
                "date": d,
                "views": int(row["exposure"] or 0),
                "exposure": int(row["exposure"] or 0),
                "click": int(row["click"] or 0),
            })
        return result
    finally:
        conn.close()


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
                    try:
                        p[key] = json.loads(p[key])
                    except (json.JSONDecodeError, TypeError):
                        p[key] = []
            result.append(p)
        return result
    finally:
        conn.close()