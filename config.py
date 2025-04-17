import os
import json
import sqlite3
from datetime import datetime

# Папка с данными (см. docker‑compose → volumes)
DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

# Файл БД
DB_FILE = os.path.join(DATA_DIR, "user_config.db")

# ─────────────────────  инициализация БД ────────────────────────────────────
def _init_db() -> None:
    """
    Создаём файл и таблицу, если их ещё нет.
    Включаем WAL, чтобы два контейнера могли одновременно писать.
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

# ─────────────────────  глобальный кэш в памяти ─────────────────────────────
user_configs: dict[str, dict] = {}


# ─────────────────────  helpers для чтения/записи ---------------------------
def _read_all_from_db() -> dict[str, dict]:
    """Считать все записи в Python‑словарь."""
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.execute("SELECT user_id, cfg_json FROM user_configs")
        return {uid: json.loads(cfg) for uid, cfg in cur.fetchall()}


def _write_to_db(data: dict[str, dict]) -> None:
    """Атомарно перезаписать все изменённые записи."""
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        for uid, cfg in data.items():
            cur.execute(
                """
                INSERT INTO user_configs (user_id, cfg_json)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET cfg_json = excluded.cfg_json
                """,
                (uid, json.dumps(cfg, ensure_ascii=False)),
            )
        conn.commit()


# ─────────────────────  прежние публичные функции ──────────────────────────
def load_user_config():
    """Заполняем глобальный кэш из базы."""
    global user_configs
    user_configs = _read_all_from_db()

    # Конвертируем старые строки в числовые uid + выставляем дефолты
    for uid, cfg in user_configs.items():
        if cfg.get("last_uid") == "null":
            cfg["last_uid"] = None
        elif isinstance(cfg.get("last_uid"), str):
            try:
                cfg["last_uid"] = int(cfg["last_uid"])
            except ValueError:
                cfg["last_uid"] = None

        cfg.setdefault("last_check_time", datetime.now().isoformat())


def save_user_config():
    """
    Пишем изменённый кэш обратно в БД.
    Логика «прочитать‑слиять‑записать» теперь внутри sqlite,
    поэтому гонок между процессами не будет.
    """
    _write_to_db(user_configs)


def _refresh():
    """
    Перечитать конфиг с диска, чтобы учесть изменения из другого процесса
    (OAuth‑колбэка и т.п.).
    """
    load_user_config()


# ─────────────────────  API, вызываемое из остальных модулей ───────────────
def ensure_user_config(user_id: int):
    """Гарантировать наличие записи о пользователе."""
    _refresh()
    global user_configs
    uid_str = str(user_id)

    if uid_str not in user_configs:
        user_configs[uid_str] = {
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
        save_user_config()


def set_email_credentials(user_id: int, email_value: str, password: str):
    ensure_user_config(user_id)
    uid_str = str(user_id)
    user_configs[uid_str]["email"]["value"] = email_value
    user_configs[uid_str]["email"]["password"] = password
    save_user_config()


def clear_email_credentials(user_id: int):
    ensure_user_config(user_id)
    uid_str = str(user_id)
    user_configs[uid_str]["email"]["value"] = None
    user_configs[uid_str]["email"]["password"] = None
    save_user_config()


def get_email_credentials(user_id: int):
    _refresh()
    uid_str = str(user_id)
    cfg = user_configs.get(uid_str, {})
    mail = cfg.get("email", {})
    return mail.get("value"), mail.get("password"), mail.get("host")


def set_jira_notification(user_id: int, event_type: str, value: bool):
    ensure_user_config(user_id)
    uid_str = str(user_id)
    if event_type in user_configs[uid_str]["notifications"]["jira"]:
        user_configs[uid_str]["notifications"]["jira"][event_type] = value
        save_user_config()


def toggle_mail_notifications(user_id: int):
    ensure_user_config(user_id)
    uid_str = str(user_id)
    cur = user_configs[uid_str]["notifications"]["mail"]
    user_configs[uid_str]["notifications"]["mail"] = not cur
    save_user_config()


def get_notifications_config(user_id: int):
    _refresh()
    ensure_user_config(user_id)
    return user_configs[str(user_id)]["notifications"]


def toggle_quiet_notifications(user_id: int):
    ensure_user_config(user_id)
    uid_str = str(user_id)
    cur = user_configs[uid_str]["notifications"].get("quiet_notifications", True)
    user_configs[uid_str]["notifications"]["quiet_notifications"] = not cur
    save_user_config()
