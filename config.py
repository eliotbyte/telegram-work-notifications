import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

# Папка с данными (см. docker‑compose → volumes)
DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

DB_FILE = os.path.join(DATA_DIR, "user_config.db")

# ────────────────────── инициализация SQLite‑файла ──────────────────────────
def _init_db() -> None:
    """
    Создаёт файл БД и таблицу, если их ещё нет.
    Переключает SQLite в WAL‑режим (одновременные чтение/запись из разных процессов).
    """
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_configs (
                user_id   TEXT PRIMARY KEY,
                cfg_json  TEXT NOT NULL
            )
            """
        )
        conn.commit()


_init_db()


@contextmanager
def _conn():
    """Соединение с таймаутом ожидания блокировки (3 с)."""
    conn = sqlite3.connect(DB_FILE, timeout=5)
    try:
        conn.execute("PRAGMA busy_timeout=3000;")
        yield conn
        conn.commit()
    finally:
        conn.close()


# ─────────────────────── низкоуровневые helpers ─────────────────────────────
def _load_cfg(uid: int) -> dict | None:
    with _conn() as c:
        cur = c.execute("SELECT cfg_json FROM user_configs WHERE user_id=?", (str(uid),))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None


def _save_cfg(uid: int, cfg: dict) -> None:
    with _conn() as c:
        c.execute(
            """
            INSERT INTO user_configs (user_id, cfg_json)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET cfg_json=excluded.cfg_json
            """,
            (str(uid), json.dumps(cfg, ensure_ascii=False)),
        )


def _default_cfg() -> dict:
    """Базовая конфигурация для нового пользователя."""
    return {
        "email": {"value": None, "password": None, "host": "imap.yandex.ru"},
        "notifications": {
            "jira": {
                "created": True,
                "assigned": True,
                "update": True,
                "comment": True,
                "mention_description": True,
                "mention_comment": True,
                "worklog": False,
            },
            "mail": False,
            "quiet_notifications": True,
        },
        "last_uid": None,
        "last_check_time": datetime.now().isoformat(),
    }


# ───────────────────────── публичное API ────────────────────────────────────
def ensure_user_config(user_id: int) -> None:
    """Гарантирует наличие строки в таблице."""
    if _load_cfg(user_id) is None:
        _save_cfg(user_id, _default_cfg())


def get_user_config(user_id: int) -> dict:
    """Читает конфиг пользователя (создаёт дефолт при необходимости)."""
    ensure_user_config(user_id)
    return _load_cfg(user_id)


def update_user_config(user_id: int, cfg: dict) -> None:
    """Полностью перезаписывает конфиг (используйте осторожно)."""
    _save_cfg(user_id, cfg)


# ---- операции с e‑mail -----------------------------------------------------
def set_email_credentials(user_id: int, email_value: str, password: str) -> None:
    cfg = get_user_config(user_id)
    cfg["email"]["value"] = email_value
    cfg["email"]["password"] = password
    _save_cfg(user_id, cfg)


def clear_email_credentials(user_id: int) -> None:
    cfg = get_user_config(user_id)
    cfg["email"]["value"] = None
    cfg["email"]["password"] = None
    _save_cfg(user_id, cfg)


def get_email_credentials(user_id: int):
    cfg = get_user_config(user_id)
    mail = cfg.get("email", {})
    return mail.get("value"), mail.get("password"), mail.get("host")


# ---- операции с уведомлениями ---------------------------------------------
def get_notifications_config(user_id: int) -> dict:
    return get_user_config(user_id)["notifications"]


def toggle_mail_notifications(user_id: int) -> None:
    cfg = get_user_config(user_id)
    cfg["notifications"]["mail"] = not cfg["notifications"]["mail"]
    _save_cfg(user_id, cfg)


def toggle_quiet_notifications(user_id: int) -> None:
    cfg = get_user_config(user_id)
    cur = cfg["notifications"].get("quiet_notifications", True)
    cfg["notifications"]["quiet_notifications"] = not cur
    _save_cfg(user_id, cfg)


def set_jira_notification(user_id: int, event_type: str, value: bool) -> None:
    cfg = get_user_config(user_id)
    if event_type in cfg["notifications"]["jira"]:
        cfg["notifications"]["jira"][event_type] = value
        _save_cfg(user_id, cfg)


# ---- операции, используемые планировщиком ----------------------------------
def update_user_fields(user_id: int, **fields) -> None:
    """
    Частичное обновление полей верхнего уровня конфига
    (например, last_uid или last_check_time).
    """
    cfg = get_user_config(user_id)
    cfg.update(fields)
    _save_cfg(user_id, cfg)


def get_all_user_configs() -> list[tuple[int, dict]]:
    """[(user_id, cfg_dict), …] для всех пользователей."""
    with _conn() as c:
        rows = c.execute("SELECT user_id, cfg_json FROM user_configs").fetchall()
    return [(int(uid), json.loads(cfg)) for uid, cfg in rows]


# ---- совместимость со старым кодом ----------------------------------------
def load_user_config():   # теперь просто no‑op
    pass


def save_user_config():   # no‑op
    pass
