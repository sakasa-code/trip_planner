"""数据库连接与初始化。"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "trip_planner.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn