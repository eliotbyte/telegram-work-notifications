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


async def retry_imap_connect(func, max_retries=3, delay=5):
    """Повторяет попытку подключения к IMAP с задержкой."""
    for attempt in range(max_retries):
        try:
            return await asyncio.to_thread(func)
        except Exception as e:
            if attempt == max_retries - 1:  # последняя попытка
                raise
            logging.warning(f"Попытка {attempt + 1}/{max_retries} не удалась: {e}")
            await asyncio.sleep(delay)


async def check_and_notify(app, user_id: int, cfg: dict):
    email_value = cfg["email"]["value"]
    token = cfg["email"]["password"]
    host = cfg["email"]["host"]
    if not email_value or not token:
        logging.info(f"[{user_id}] Пропускаем проверку: отсутствуют учетные данные почты")
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

    try:
        new_messages = await retry_imap_connect(fetch_new)
    except Exception as e:
        logging.error(f"[{user_id}] Все попытки подключения к IMAP не удались: {e}")
        return

    if not new_messages:
        logging.info(f"[{user_id}] Новые письма не найдены")
        return

    logging.info(f"[{user_id}] Найдено {len(new_messages)} новых писем")

    # --- «тихие часы» --------------------------------------------------------
    def quiet_time() -> bool:
        msk = datetime.utcnow() + timedelta(hours=3)
        return not (0 <= msk.weekday() <= 4 and 9 <= msk.hour < 18)

    # --- рассылаем уведомления ----------------------------------------------
    last_processed_uid = last_uid
    for uid, subject, sender, html in new_messages:
        allowed = {k for k, v in cfg["notifications"]["jira"].items() if v}
        mute = cfg["notifications"].get("quiet_notifications", True) and quiet_time()
        jira_result = parse_jira_email(html)

        if jira_result is None:
            if not cfg["notifications"]["mail"]:
                logging.info(f"[{user_id}] Пропускаем письмо от {sender}: у пользователя отключены email уведомления")
                continue

            msg_text = f"📩 Письмо от {escape_markdown(sender)}\n*Тема:* {escape_markdown(subject)}"
            await app.bot.send_message(
                user_id,
                msg_text,
                parse_mode="Markdown",
                disable_notification=mute,
            )
            logging.info(f"[{user_id}] Отправлено уведомление о письме от {sender} (тихий режим: {mute})")
        else:
            # Собираем все события из author_events
            all_events = []
            for author, events in jira_result['author_events'].items():
                for event in events:
                    event['author'] = author
                    all_events.append(event)
            
            # Фильтруем события по allowed
            filtered_events = [e for e in all_events if e['type'] in allowed]
            if not filtered_events:
                logging.info(f"[{user_id}] Пропускаем Jira письмо: парсер не нашел интересующих событий")
                continue

            # Заменяем None авторов
            valid_authors = [e['author'] for e in filtered_events if e['author'] is not None]
            default_author = valid_authors[0] if valid_authors else "Кто-то"
            for event in filtered_events:
                if event['author'] is None:
                    event['author'] = default_author

            # Группируем события по автору
            events_by_author = {}
            for event in filtered_events:
                author = event['author']
                if author not in events_by_author:
                    events_by_author[author] = []
                events_by_author[author].append(event)

            # Формируем сообщение
            task_info = f"[{jira_result['task_key']}] {jira_result['task_summary']}"
            if jira_result['task_url']:
                task_info = f'<a href="{jira_result["task_url"]}">{task_info}</a>'
            msg_lines = [task_info, ""]

            for author, events in events_by_author.items():
                msg_lines.append(f"{author}:")
                for event in events:
                    event_type = event['type']

                    if event_type == "assigned":
                        msg_lines.append("✅ назначил(а) вас исполнителем задачи")
                    elif event_type == "created":
                        msg_lines.append("📌 создал(а) задачу")
                    elif event_type == "update":
                        msg_lines.append("✏️ внес(ла) изменения")
                    elif event_type == "comment":
                        msg_lines.append("💬 оставил(а) комментарий")
                    elif event_type == "mention_description":
                        msg_lines.append("👀 упомянул(а) вас в задаче")
                    elif event_type == "mention_comment":
                        msg_lines.append("👀 упомянул(а) вас в комментариях")
                    elif event_type == "worklog":
                        msg_lines.append("⏱️ трекнул(а) время")

            msg_text = "\n".join(msg_lines)
            await app.bot.send_message(
                user_id,
                msg_text,
                parse_mode="HTML",
                disable_notification=mute,
            )
            logging.info(f"[{user_id}] Отправлено Jira уведомление (тихий режим: {mute})")


        last_processed_uid = max(last_processed_uid or 0, uid)

    # --- сохраняем позицию и время ------------------------------------------
    config.update_user_fields(
        user_id,
        last_uid=last_processed_uid,
        last_check_time=now_dt.isoformat(),
    )
    logging.info(f"[{user_id}] Обновлена позиция последнего письма: {last_processed_uid}")
