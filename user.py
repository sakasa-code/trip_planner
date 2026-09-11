"""用户 CRUD。"""
import sqlite3
import time

from .db import get_conn


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