"""企业资料 CRUD。"""
import sqlite3
import time

from .db import get_conn


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