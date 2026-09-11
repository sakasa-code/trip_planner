"""数据库初始化（幂等建表）。"""
import sqlite3

from .db import get_conn


def init_db():
    """建表（幂等）。"""
    conn = get_conn()
    try:
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