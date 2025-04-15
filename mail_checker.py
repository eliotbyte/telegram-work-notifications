import asyncio
import logging
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from imapclient import IMAPClient
from config import user_configs, save_user_config
from filters.jira_parser import parse_jira_email
from telegram.helpers import escape_markdown

async def check_mail_for_all_users(app):
    """
    Асинхронная проверка почты для всех пользователей.
    Вызывается планировщиком.
    """
    logging.info("=== Запуск проверки почты для всех пользователей ===")
    tasks = []
    for user_id_str, config in user_configs.items():
        tasks.append(asyncio.create_task(check_and_notify(app, int(user_id_str), config)))

    if tasks:
        await asyncio.gather(*tasks)
    logging.info("=== Завершён проход проверки почты ===")


async def check_and_notify(app, user_id: int, config: dict):
    """
    Асинхронная проверка почты конкретного пользователя.
    Учитываем флаги «mail» и «jira[event_type]».
    """
    email_value = config["email"]["value"]
    password = config["email"]["password"]
    host = config["email"]["host"]

    # Если почта не настроена, выходим
    if not email_value or not password:
        return

    logging.info(f"[{user_id}] Проверяем почту {email_value}")

    # Загружаем время последней проверки
    fromiso = config.get("last_check_time", datetime.now().isoformat())
    last_check_time = datetime.fromisoformat(fromiso)
    now_dt = datetime.now()

    if (now_dt - last_check_time) > timedelta(minutes=15):
        last_check_time = now_dt - timedelta(minutes=15)

    since_str = last_check_time.strftime("%d-%b-%Y")
    last_uid = config.get("last_uid", None)

    def fetch_new_messages():
        """
        Синхронная работа с IMAP в отдельном потоке.
        """
        result = []
        try:
            with IMAPClient(host, ssl=True) as client:
                client.login(email_value, password)
                client.select_folder("INBOX", readonly=True)

                messages = client.search(["SINCE", since_str])
                logging.info(f"[{user_id}] Найдено писем c {since_str}: {len(messages)}")

                for uid in messages:
                    if last_uid and uid <= last_uid:
                        continue
                    raw_data = client.fetch([uid], ["BODY[]"])[uid][b"BODY[]"]
                    msg = BytesParser(policy=policy.default).parsebytes(raw_data)
                    subject = msg["subject"] or "(без темы)"
                    from_ = msg["from"] or "(неизвестно)"

                    raw_html = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/html":
                                charset = part.get_content_charset() or "utf-8"
                                raw_html = part.get_payload(decode=True).decode(charset, errors="replace")
                                break
                    else:
                        if msg.get_content_type() == "text/html":
                            charset = msg.get_content_charset() or "utf-8"
                            raw_html = msg.get_payload(decode=True).decode(charset, errors="replace")

                    result.append((uid, subject, from_, raw_html))
        except Exception as e:
            logging.error(f"[{user_id}] IMAP ошибка: {e}")
        return result

    new_messages = await asyncio.to_thread(fetch_new_messages)

    # Рассылаем уведомления
    for uid, subject, from_, raw_html in new_messages:
        # Разбираем Jira
        # Чтобы учитывать включённые/выключенные типы Jira-событий —
        # лучше передать allowed_event_types:
        user_jira_conf = config["notifications"]["jira"]
        allowed = {k for k, v in user_jira_conf.items() if v}
        jira_msgs = parse_jira_email(subject, raw_html, allowed_event_types=allowed)

        if jira_msgs is None:
            # Обычное письмо
            if config["notifications"]["mail"]:
                message_text = (
                    f"📩 Новое письмо от {escape_markdown(from_)}\n"
                    f"*Тема:* {escape_markdown(subject)}"
                )
                await app.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode='Markdown'
                )
        elif len(jira_msgs) > 0:
            # Jira-события есть (и не отфильтрованы)
            for message_text in jira_msgs:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="HTML"
                )
        else:
            # Jira, но после фильтрации ничего не осталось
            pass

        # Обновляем last_uid
        config["last_uid"] = uid
        save_user_config()

    # Обновляем время
    config["last_check_time"] = now_dt.isoformat()
    save_user_config()
