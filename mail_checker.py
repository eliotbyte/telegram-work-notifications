import asyncio
import logging
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser

from imapclient import IMAPClient

import config
from filters.jira_parser import parse_jira_email
from telegram.helpers import escape_markdown


# ────────────────────────  ⚡ Быстрая UID‑закладка  ─────────────────────────
async def bookmark_latest_uid(
    user_id: int,
    email: str,
    token: str,
    host: str = "imap.yandex.ru",
) -> None:
    """
    • Определяет максимальный UID в INBOX
    • Сохраняет его в конфиг вместе с текущим временем.
    Нужна, чтобы при первой авторизации НЕ рассылать старые письма.
    """

    def _get_highest_uid() -> int:
        with IMAPClient(host, ssl=True) as c:
            c.oauth2_login(email, token)
            c.select_folder("INBOX", readonly=True)
            uids = c.search(["ALL"])
            return max(uids) if uids else 0

    highest_uid = await asyncio.to_thread(_get_highest_uid)

    config.update_user_fields(
        user_id,
        last_uid=highest_uid,
        last_check_time=datetime.now().isoformat(),
    )

    logging.info(f"[{user_id}] UID‑закладка выполнена ⇒ {highest_uid}")


# ───────────────────  Основная проверка почты ───────────────────────────────
async def check_mail_for_all_users(app):
    logging.info("=== Проверка почты для всех пользователей ===")

    # Берём актуальные данные прямо из SQLite
    all_users = config.get_all_user_configs()

    tasks = [
        asyncio.create_task(check_and_notify(app, uid, cfg))
        for uid, cfg in all_users
    ]
    if tasks:
        await asyncio.gather(*tasks)

    logging.info("=== Завершён проход проверки почты ===")


async def check_and_notify(app, user_id: int, cfg: dict):
    email_value = cfg["email"]["value"]
    token = cfg["email"]["password"]
    host = cfg["email"]["host"]
    if not email_value or not token:
        return

    logging.info(f"[{user_id}] Проверяем почту {email_value}")

    # --- диапазон времени ----------------------------------------------------
    now_dt = datetime.now()
    last_check_time = datetime.fromisoformat(
        cfg.get("last_check_time", now_dt.isoformat())
    )
    if (now_dt - last_check_time) > timedelta(minutes=15):
        last_check_time = now_dt - timedelta(minutes=15)

    since_str = last_check_time.strftime("%d-%b-%Y")
    last_uid = cfg.get("last_uid")

    # --- получаем письма -----------------------------------------------------
    def fetch_new():
        res = []
        try:
            with IMAPClient(host, ssl=True) as c:
                c.oauth2_login(email_value, token)
                c.select_folder("INBOX", readonly=True)
                for uid in c.search(["SINCE", since_str]):
                    if last_uid and uid <= last_uid:
                        continue
                    data = c.fetch([uid], ["BODY[]", "INTERNALDATE"])[uid]
                    if data[b"INTERNALDATE"].replace(tzinfo=None) <= last_check_time:
                        continue
                    raw_data = data[b"BODY[]"]
                    msg = BytesParser(policy=policy.default).parsebytes(raw_data)
                    subject = msg["subject"] or "(без темы)"
                    sender = msg["from"] or "(неизвестно)"
                    html = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/html":
                                html = part.get_payload(decode=True).decode(
                                    part.get_content_charset() or "utf-8",
                                    errors="replace",
                                )
                                break
                    elif msg.get_content_type() == "text/html":
                        html = msg.get_payload(decode=True).decode(
                            msg.get_content_charset() or "utf-8",
                            errors="replace",
                        )
                    res.append((uid, subject, sender, html))
        except Exception as e:
            logging.error(f"[{user_id}] IMAP/XOAUTH2 ошибка: {e}")
        return res

    new_messages = await asyncio.to_thread(fetch_new)

    # --- «тихие часы» --------------------------------------------------------
    def quiet_time() -> bool:
        msk = datetime.utcnow() + timedelta(hours=3)
        return not (0 <= msk.weekday() <= 4 and 9 <= msk.hour < 18)

    # --- рассылаем уведомления ----------------------------------------------
    last_processed_uid = last_uid
    for uid, subject, sender, html in new_messages:
        allowed = {k for k, v in cfg["notifications"]["jira"].items() if v}
        jira_msgs = parse_jira_email(subject, html, allowed_event_types=allowed)

        mute = cfg["notifications"].get("quiet_notifications", True) and quiet_time()

        if jira_msgs is None:
            if cfg["notifications"]["mail"]:
                await app.bot.send_message(
                    user_id,
                    f"📩 Письмо от {escape_markdown(sender)}\n"
                    f"*Тема:* {escape_markdown(subject)}",
                    parse_mode="Markdown",
                    disable_notification=mute,
                )
        else:
            for txt in jira_msgs:
                await app.bot.send_message(
                    user_id,
                    txt,
                    parse_mode="HTML",
                    disable_notification=mute,
                )

        last_processed_uid = max(last_processed_uid or 0, uid)

    # --- сохраняем позицию и время ------------------------------------------
    config.update_user_fields(
        user_id,
        last_uid=last_processed_uid,
        last_check_time=now_dt.isoformat(),
    )
