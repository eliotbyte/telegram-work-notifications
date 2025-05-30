import os
import logging
import asyncio
from datetime import datetime
from telegram.ext import ApplicationBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from telegram import BotCommand
from conversation import build_conversation_handler
from mail_checker import check_mail_for_all_users
from config import load_user_config, save_user_config

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
MAX_INSTANCES = int(os.getenv("MAX_INSTANCES", "1"))

LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO")
LOG_LEVEL = logging._nameToLevel.get(LOG_LEVEL_NAME.upper(), logging.INFO)

logging.basicConfig(level=LOG_LEVEL)

async def scheduled_mail_check(app):
    """
    Запуск асинхронной проверки почты (для планировщика).
    """
    await check_mail_for_all_users(app)

async def post_init(application):
    """
    Запуск планировщика после инициализации бота + установка команд.
    """
    # Устанавливаем команды для меню в Telegram
    await application.bot.set_my_commands([
        BotCommand("start", "Открыть главное меню"),
        # Можно добавить и другие команды:
        # BotCommand("help", "Вывести справку по боту"),
        # BotCommand("status", "Проверить состояние бота"),
    ])
    
    loop = asyncio.get_running_loop()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        scheduled_mail_check,
        trigger='interval',
        seconds=CHECK_INTERVAL,
        kwargs={"app": application},
        max_instances=MAX_INSTANCES
    )
    scheduler.start()
    logging.info("✅ Планировщик запущен")

def main():
    load_user_config()

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Добавляем обработчик ошибок
    app.add_error_handler(lambda u, c: logging.error(f"Ошибка: {c.error}"))

    # Регистрируем conversation handler
    conv_handler = build_conversation_handler()
    app.add_handler(conv_handler)

    logging.info("Бот запущен. Начинаем polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
