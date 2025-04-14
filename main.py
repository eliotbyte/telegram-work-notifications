import os
import json
import asyncio
import logging
from email import policy
from email.parser import BytesParser
from imapclient import IMAPClient
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Импортируем наш парсер Jira
from filters.jira_parser import parse_jira_email

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
DATA_DIR = "/app/data"
USER_CONFIG_FILE = os.path.join(DATA_DIR, "user_config.json")

logging.basicConfig(level=logging.INFO)

user_configs = {}

def load_user_config():
    """Загружает конфиг пользователей из JSON-файла, конвертирует last_uid из str в int,
       при отсутствии last_check_time добавляет текущее время."""
    global user_configs
    if os.path.exists(USER_CONFIG_FILE):
        with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
            user_configs = json.load(f)

            for cfg in user_configs.values():
                # last_uid: "null" -> None, строка -> int
                if cfg["last_uid"] == "null":
                    cfg["last_uid"] = None
                elif isinstance(cfg["last_uid"], str):
                    cfg["last_uid"] = int(cfg["last_uid"])

                # Если нет поля last_check_time – ставим текущее время
                if "last_check_time" not in cfg:
                    cfg["last_check_time"] = datetime.now().isoformat()
    else:
        user_configs = {}

def save_user_config():
    """Сохраняет конфиг пользователей в JSON-файл."""
    with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(user_configs, f, ensure_ascii=False, indent=2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    await update.message.reply_text("Привет! Отправь /connect, чтобы привязать почту через IMAP.")

async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /connect."""
    await update.message.reply_text(
        "Введи свою почту в формате:\n\n`email@example.com пароль`\n\n⚠️ Пароль хранится в открытом виде.",
        parse_mode='Markdown'
    )

async def handle_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода почты и пароля. Пытаемся залогиниться, сохраняем в конфиг при успехе."""
    if not update.message:
        return

    try:
        email_addr, password = update.message.text.strip().split()

        # Валидируем учётные данные, пытаясь залогиниться
        try:
            with IMAPClient("imap.yandex.ru", ssl=True) as client:
                client.login(email_addr, password)
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось войти в почту:\n{e}")
            logging.warning(f"[{update.effective_user.id}] Невалидная почта: {e}")
            return

        # Сохраняем данные пользователя
        user_id_str = str(update.effective_user.id)
        user_configs[user_id_str] = {
            "email": email_addr,
            "password": password,
            "last_uid": None,
            # При первом сохранении ставим текущее время
            "last_check_time": datetime.now().isoformat()
        }
        save_user_config()

        await update.message.reply_text("✅ Почта сохранена! Проверим письма в ближайшее время.")
        logging.info(f"[{update.effective_user.id}] Почта сохранена: {email_addr}")

    except ValueError:
        await update.message.reply_text("❌ Формат неверный. Пример: `email@example.com пароль`", parse_mode="Markdown")

async def check_mail_for_all_users(app: Application):
    """
    Проверяем почту для всех пользователей (асинхронный запуск из планировщика).
    Внутри для каждого пользователя вызываем асинхронную функцию check_and_notify.
    """
    tasks = []
    for user_id_str, config in user_configs.items():
        tasks.append(asyncio.create_task(check_and_notify(app, int(user_id_str), config)))

    # Ждём завершения всех задач
    if tasks:
        await asyncio.gather(*tasks)

async def check_and_notify(app: Application, user_id: int, config: dict):
    """
    Асинхронная проверка новых писем пользователя с учётом времени последней проверки и last_uid.
    Уведомляет о новых письмах, которые пришли после last_check_time и имеют UID > last_uid.
    """
    HOST = 'imap.yandex.ru'
    email_addr = config["email"]

    logging.info(f"[{user_id}] Проверяем почту {email_addr}")

    # Загружаем время последней проверки
    last_check_time = datetime.fromisoformat(config.get("last_check_time", datetime.now().isoformat()))
    now_dt = datetime.now()

    # Если прошло больше 15 минут между now и последней проверкой — смещаем last_check_time
    if (now_dt - last_check_time) > timedelta(minutes=15):
        last_check_time = now_dt - timedelta(minutes=15)

    # Формируем дату (без учёта времени) для IMAP-фильтра SINCE
    since_str = last_check_time.strftime("%d-%b-%Y")

    # Синхронную работу с IMAP выносим в отдельную функцию, чтобы вызвать её через asyncio.to_thread(...)
    def fetch_new_messages():
        """
        Подключаемся к IMAP, ищем письма, возвращаем список (uid, subject, from_, raw_html).
        """
        result = []
        try:
            with IMAPClient(HOST, ssl=True) as client:
                client.login(config["email"], config["password"])
                client.select_folder("INBOX", readonly=True)  # <-- ВАЖНО: readonly режим!

                messages = client.search(["SINCE", since_str])
                logging.info(f"[{user_id}] Найдено новых писем (по дате {since_str}): {len(messages)}")

                last_uid = config.get("last_uid", None)
                for uid in messages:
                    if last_uid and uid <= last_uid:
                        continue

                    # читаем письмо
                    raw_data = client.fetch([uid], ["BODY[]"])[uid][b"BODY[]"]
                    msg = BytesParser(policy=policy.default).parsebytes(raw_data)
                    subject = msg["subject"] or "(без темы)"
                    from_ = msg["from"] or "(неизвестно)"

                    # Ищем HTML-контент (перебираем части письма)
                    raw_html = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ctype = part.get_content_type()
                            if ctype == "text/html":
                                charset = part.get_content_charset() or "utf-8"
                                raw_html = part.get_payload(decode=True).decode(charset, errors="replace")
                                break
                    else:
                        # Если письмо не multipart, но вдруг HTML
                        if msg.get_content_type() == "text/html":
                            charset = msg.get_content_charset() or "utf-8"
                            raw_html = msg.get_payload(decode=True).decode(charset, errors="replace")

                    result.append((uid, subject, from_, raw_html))
        except Exception as e:
            logging.error(f"[{user_id}] IMAP ошибка: {e}")
        return result

    # Выполняем fetch в отдельном потоке (чтобы не блокировать event loop)
    new_messages = await asyncio.to_thread(fetch_new_messages)

    # Перебираем новые письма и рассылаем уведомления
    for uid, subject, from_, raw_html in new_messages:
        # Пытаемся получить специальные сообщения Jira
        jira_msgs = []
        if raw_html:
            jira_msgs = parse_jira_email(subject, raw_html)

        if jira_msgs:
            # Если парсер вернул список сообщений — отправим их все.
            for message_text in jira_msgs:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="HTML"
                )
        else:
            # Иначе — дефолтная логика
            await app.bot.send_message(
                chat_id=user_id,
                text=f"📩 Новое письмо от {from_}\n<b>Тема:</b> {subject}",
                parse_mode='HTML'
            )

        # Обновляем last_uid
        config["last_uid"] = uid
        save_user_config()

    # В любом случае, фиксируем текущее время проверки
    config["last_check_time"] = now_dt.isoformat()
    save_user_config()

def schedule_check_mail(app: Application, loop):
    """
    Синхронная обёртка для планировщика: получает текущий event loop
    и через него создаёт асинхронную задачу check_mail_for_all_users(app).
    """
    loop.create_task(check_mail_for_all_users(app))

async def post_init(application: Application):
    """Запуск планировщика после инициализации бота."""
    loop = asyncio.get_running_loop()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        schedule_check_mail,
        trigger='interval',
        seconds=CHECK_INTERVAL,
        args=[application, loop]
    )
    scheduler.start()
    logging.info("✅ Планировщик запущен")

if __name__ == "__main__":
    load_user_config()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("connect", connect))

    # Обработка любых текстовых сообщений, кроме команд, — в handle_credentials
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_credentials))

    app.run_polling()
