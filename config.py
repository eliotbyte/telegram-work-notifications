import os
import json
import logging
from datetime import datetime

# Путь до папки с данными
DATA_DIR = "/app/data"
USER_CONFIG_FILE = os.path.join(DATA_DIR, "user_config.json")

# Глобальная структура user_configs: {str(user_id): {...}}
user_configs = {}

def load_user_config():
    global user_configs
    if os.path.exists(USER_CONFIG_FILE):
        with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
            user_configs = json.load(f)
    else:
        user_configs = {}

    # Конвертируем типы, если нужно
    for user_id, cfg in user_configs.items():
        # Поправим last_uid
        if cfg.get("last_uid") == "null":
            cfg["last_uid"] = None
        elif isinstance(cfg.get("last_uid"), str):
            try:
                cfg["last_uid"] = int(cfg["last_uid"])
            except ValueError:
                cfg["last_uid"] = None
        # last_check_time
        if "last_check_time" not in cfg:
            cfg["last_check_time"] = datetime.now().isoformat()

def save_user_config():
    global user_configs
    with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(user_configs, f, ensure_ascii=False, indent=2)

def ensure_user_config(user_id: int):
    """
    Создаёт заготовку конфига для пользователя, если отсутствует.
    """
    global user_configs

    uid_str = str(user_id)
    if uid_str not in user_configs:
        # При создании нового пользователя сразу прописываем,
        # что все Jira-уведомления включены, кроме worklog,
        # а уведомления по обычной почте — отключены (False).
        user_configs[uid_str] = {
            "email": {
                "value": None,
                "password": None,
                "host": "imap.yandex.ru",
            },
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
                # Изначально False
                "mail": False,
                # [MOD] Новая настройка "Тихие сообщения вне рабочего времени" (по умолчанию включена)
                "quiet_notifications": True,
            },
            "last_uid": None,
            "last_check_time": datetime.now().isoformat(),
        }
        save_user_config()

def set_email_credentials(user_id: int, email_value: str, password: str):
    """
    Сохранить почту и пароль в конфиг пользователя.
    """
    global user_configs
    uid_str = str(user_id)
    ensure_user_config(user_id)

    user_configs[uid_str]["email"]["value"] = email_value
    user_configs[uid_str]["email"]["password"] = password
    save_user_config()

def clear_email_credentials(user_id: int):
    """
    Удалить данные почты у пользователя.
    """
    global user_configs
    uid_str = str(user_id)
    ensure_user_config(user_id)

    user_configs[uid_str]["email"]["value"] = None
    user_configs[uid_str]["email"]["password"] = None
    save_user_config()

def get_email_credentials(user_id: int):
    """
    Получить (email, password, host) для пользователя.
    Вернёт (None, None, None), если не настроено.
    """
    global user_configs
    uid_str = str(user_id)
    cfg = user_configs.get(uid_str)
    if not cfg:
        return None, None, None

    return (
        cfg["email"].get("value"),
        cfg["email"].get("password"),
        cfg["email"].get("host"),
    )

def set_jira_notification(user_id: int, event_type: str, value: bool):
    """
    Установить флаг включения/выключения определённого события Jira.
    """
    global user_configs
    uid_str = str(user_id)
    ensure_user_config(user_id)

    if event_type in user_configs[uid_str]["notifications"]["jira"]:
        user_configs[uid_str]["notifications"]["jira"][event_type] = value
        save_user_config()

def toggle_mail_notifications(user_id: int):
    """
    Переключить флаг уведомлений по не-Jira письмам.
    """
    global user_configs
    uid_str = str(user_id)
    ensure_user_config(user_id)

    current = user_configs[uid_str]["notifications"]["mail"]
    user_configs[uid_str]["notifications"]["mail"] = not current
    save_user_config()

def get_notifications_config(user_id: int):
    """
    Получить конфиг уведомлений (jira dict, mail bool).
    """
    global user_configs
    uid_str = str(user_id)
    ensure_user_config(user_id)
    return user_configs[uid_str]["notifications"]

# [MOD] Функция переключения тихих уведомлений
def toggle_quiet_notifications(user_id: int):
    """
    Переключить флаг тихих уведомлений вне рабочего времени.
    """
    global user_configs
    uid_str = str(user_id)
    ensure_user_config(user_id)

    current = user_configs[uid_str]["notifications"].get("quiet_notifications", True)
    user_configs[uid_str]["notifications"]["quiet_notifications"] = not current
    save_user_config()
