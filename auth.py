import os
import hashlib
from db import get_conn


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return salt.hex() + ":" + key.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(":")
        salt = bytes.fromhex(salt_hex)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return key.hex() == key_hex
    except Exception:
        return False


def register_user(full_name: str, email: str, password: str):
    conn = get_conn()
    cur = conn.cursor()

    try:
        cur.execute(
            "INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)",
            (full_name.strip(), email.strip().lower(), hash_password(password))
        )
        conn.commit()
        return True, "המשתמש נוצר בהצלחה"
    except Exception as e:
        return False, f"שגיאה ביצירת משתמש: {e}"
    finally:
        conn.close()


def login_user(email: str, password: str):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM users WHERE email = ?",
            (email.strip().lower(),)
        )
        user = cur.fetchone()
    finally:
        conn.close()

    if user and verify_password(password, user["password_hash"]):
        return True, dict(user)
    return False, None
