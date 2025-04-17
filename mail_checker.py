import asyncio
import base64
import logging
from datetime import datetime, timedelta
from email import policy
from email.parser import BytesParser
from imapclient import IMAPClient
import config
from config import save_user_config
from filters.jira_parser import parse_jira_email
from telegram.helpers import escape_markdown


async def check_mail_for_all_users(app):
    """
    Асинхронная проверка почты для всех пользователей.
    Вызывается планировщиком.
    """
    logging.info("=== Запуск проверки почты для всех пользователей ===")
    tasks = []
    for user_id_str, config_item in config.user_configs.items():
        tasks.append(asyncio.create_task(check_and_notify(app, int(user_id_str), config_item)))

    if tasks:
        await asyncio.gather(*tasks)
    logging.info("=== Завершён проход проверки почты ===")


async def check_and_notify(app, user_id: int, config: dict):
    """
    Асинхронная проверка почты конкретного пользователя.
    Учитываем флаги «mail» и «jira[event_type]».
    """
    email_value = config["email"]["value"]
    token = config["email"]["password"]        # [OAUTH] раньше был «password»
    host = config["email"]["host"]

    # Если почта не настроена, выходим
    if not email_value or not token:
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
                # ------------------------------------------------------------------
                # [OAUTH] авторизация через встроенную обёртку oauth2_login()
                #         (механизм по‑умолчанию — ‘XOAUTH2’)
                # ------------------------------------------------------------------
                client.oauth2_login(email_value, token)
                # ------------------------------------------------------------------

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
            logging.error(f"[{user_id}] IMAP/XOAUTH2 ошибка: {e}")   # [OAUTH] уточнили тип ошибки
        return result

    new_messages = await asyncio.to_thread(fetch_new_messages)

    # [MOD] Функция определения "тихого времени"
    def is_quiet_time() -> bool:
        """
        Возвращает True, если сейчас нерабочее время (с учётом МСК 9-18 Пн-Пт).
        """
        moscow_dt = datetime.utcnow() + timedelta(hours=3)  # UTC+3 для Москвы
        # Понедельник=0, ..., Воскресенье=6
        weekday = moscow_dt.weekday()  # 0..6
        hour = moscow_dt.hour
        # Рабочие дни: 0..4 (Пн..Пт), рабочие часы: 9..17
        if 0 <= weekday <= 4 and 9 <= hour < 18:
            return False
        return True

    # Рассылаем уведомления
    for uid, subject, from_, raw_html in new_messages:
        # Разбираем Jira
        user_jira_conf = config["notifications"]["jira"]
        allowed = {k for k, v in user_jira_conf.items() if v}
        jira_msgs = parse_jira_email(subject, raw_html, allowed_event_types=allowed)

        # [MOD] Если включен "quiet_notifications" и сейчас тихое время —
        #       то disable_notification=True
        quiet_enabled = config["notifications"].get("quiet_notifications", True)
        disable_notif = quiet_enabled and is_quiet_time()

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
                    parse_mode='Markdown',
                    disable_notification=disable_notif  # [MOD]
                )
        elif len(jira_msgs) > 0:
            # Jira‑события есть (и не отфильтрованы)
            for message_text in jira_msgs:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="HTML",
                    disable_notification=disable_notif  # [MOD]
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
