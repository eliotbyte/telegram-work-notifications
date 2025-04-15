import os
import logging
import asyncio
from datetime import datetime
from telegram.ext import ApplicationBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from conversation import build_conversation_handler
from mail_checker import check_mail_for_all_users
from config import load_user_config, save_user_config

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

logging.basicConfig(level=logging.INFO)

async def post_init(application):
    """
    Запуск планировщика после инициализации бота.
    """
    loop = asyncio.get_running_loop()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        lambda: loop.create_task(check_mail_for_all_users(application)),
        trigger='interval',
        seconds=CHECK_INTERVAL
    )
    scheduler.start()
    logging.info("✅ Планировщик запущен")

def main():
    load_user_config()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Регистрируем conversation handler
    conv_handler = build_conversation_handler()
    app.add_handler(conv_handler)

    logging.info("Бот запущен. Начинаем polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
