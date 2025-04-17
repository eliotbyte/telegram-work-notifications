from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
import logging
import os
from urllib.parse import urlencode

from config import (
    ensure_user_config,
    clear_email_credentials,
    get_email_credentials,
    set_jira_notification,
    toggle_mail_notifications,
    get_notifications_config,
    toggle_quiet_notifications,
)

# --------------------------------------------------------------------------- #
# OAuth‑параметры                                                              #
# --------------------------------------------------------------------------- #
YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID")
REDIRECT_URI = os.getenv("YANDEX_REDIRECT_URI")
SCOPE = "mail:imap_full login:email calendar:all"

# --------------------------------------------------------------------------- #
# Состояния                                                                    #
# --------------------------------------------------------------------------- #
MAIN_MENU, CONFIRM_DELETE_EMAIL, SETTINGS_MENU, MAIL_MENU, JIRA_MENU = range(5)

# --------------------------------------------------------------------------- #
# Клавиатуры                                                                   #
# --------------------------------------------------------------------------- #
def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Показываем «Добавить почту» или «Настройки»."""
    email, token, _ = get_email_credentials(user_id)
    buttons = (
        [[InlineKeyboardButton("Добавить почту", callback_data="add_email")]]
        if not email or not token
        else [[InlineKeyboardButton("Настройки", callback_data="settings")]]
    )
    return InlineKeyboardMarkup(buttons)


def settings_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    conf = get_notifications_config(user_id)
    quiet_on = conf.get("quiet_notifications", True)
    quiet_label = f"Уведомлять только в рабочее время [{'ДА' if quiet_on else 'НЕТ'}]"
    buttons = [
        [InlineKeyboardButton("Почта", callback_data="mail_menu")],
        [InlineKeyboardButton(quiet_label, callback_data="toggle_quiet_notifications")],
        [InlineKeyboardButton("Назад", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(buttons)


def mail_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    email_value, token, _ = get_email_credentials(user_id)
    email_set = bool(email_value and token)
    conf = get_notifications_config(user_id)

    buttons = []
    if email_set:
        buttons.append([InlineKeyboardButton("Удалить", callback_data="delete_email")])

    buttons.append([InlineKeyboardButton("Уведомления Jira", callback_data="jira_menu")])

    mail_on = conf["mail"]
    mail_label = "Уведомления о письмах [ДА]" if mail_on else "Уведомления о письмах [НЕТ]"
    buttons.append([InlineKeyboardButton(mail_label, callback_data="toggle_mail_notifications")])

    buttons.append([InlineKeyboardButton("Назад", callback_data="back_to_settings")])
    return InlineKeyboardMarkup(buttons)


def confirm_delete_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Да", callback_data="delete_yes"),
                InlineKeyboardButton("Нет", callback_data="delete_no"),
            ]
        ]
    )


def jira_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    conf = get_notifications_config(user_id)
    jira_conf = conf["jira"]
    rows = [
        [
            InlineKeyboardButton(
                f"{e_type} [{'ДА' if value else 'НЕТ'}]",
                callback_data=f"toggle_jira_{e_type}",
            )
        ]
        for e_type, value in jira_conf.items()
    ]
    rows.append([InlineKeyboardButton("Назад", callback_data="back_to_mail_menu")])
    return InlineKeyboardMarkup(rows)

# --------------------------------------------------------------------------- #
# Хэндлеры                                                                     #
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start — приветствие и главное меню."""
    user_id = update.effective_user.id
    ensure_user_config(user_id)

    await update.message.reply_text(
        "Привет! Я бот для уведомлений.\nВыберите действие из меню ниже.",
        reply_markup=main_menu_keyboard(user_id),
    )
    return MAIN_MENU


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий в главном меню."""
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    # ----------------------------------------------------------------------- #
    # «Добавить почту» → выдаём ссылку OAuth                                  #
    # ----------------------------------------------------------------------- #
    if data == "add_email":
        params = {
            "response_type": "code",
            "client_id": YANDEX_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": str(user_id),  # связываем токен с Telegram‑ID
        }
        auth_link = f"https://oauth.yandex.ru/authorize?{urlencode(params)}"

        await query.message.reply_text(
            (
                "⚡ *Шаг 1.* Нажмите ссылку ниже и подтвердите доступ к почте.\n"
                "⚡ *Шаг 2.* Вернитесь в чат — я всё сделаю сам."
            ),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        await query.message.reply_text(auth_link)
        return MAIN_MENU

    elif data == "settings":
        await query.edit_message_text(
            "Открываю настройки...", reply_markup=settings_menu_keyboard(user_id)
        )
        return SETTINGS_MENU


async def settings_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "mail_menu":
        await query.edit_message_text(
            "Настройки почты", reply_markup=mail_menu_keyboard(user_id)
        )
        return MAIL_MENU

    elif data == "back_to_main":
        await query.edit_message_text(
            "Главное меню", reply_markup=main_menu_keyboard(user_id)
        )
        return MAIN_MENU

    elif data == "toggle_quiet_notifications":
        toggle_quiet_notifications(user_id)
        conf = get_notifications_config(user_id)
        status = "ДА" if conf["quiet_notifications"] else "НЕТ"
        await query.edit_message_text(
            f"Тихие сообщения вне рабочего времени теперь: {status}",
            reply_markup=settings_menu_keyboard(user_id),
        )
        return SETTINGS_MENU


async def mail_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "delete_email":
        await query.edit_message_text(
            "Вы действительно хотите удалить почту?",
            reply_markup=confirm_delete_keyboard(),
        )
        return CONFIRM_DELETE_EMAIL

    elif data == "toggle_mail_notifications":
        toggle_mail_notifications(user_id)
        conf = get_notifications_config(user_id)
        status = "ДА" if conf["mail"] else "НЕТ"
        await query.edit_message_text(
            f"Уведомления о письмах теперь: {status}",
            reply_markup=mail_menu_keyboard(user_id),
        )
        return MAIL_MENU

    elif data == "jira_menu":
        await query.edit_message_text(
            "Настройки Jira‑уведомлений", reply_markup=jira_menu_keyboard(user_id)
        )
        return JIRA_MENU

    elif data == "back_to_settings":
        await query.edit_message_text(
            "Настройки", reply_markup=settings_menu_keyboard(user_id)
        )
        return SETTINGS_MENU


async def confirm_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "delete_yes":
        clear_email_credentials(user_id)
        await query.edit_message_text(
            "Почта удалена!", reply_markup=main_menu_keyboard(user_id)
        )
        return MAIN_MENU
    elif data == "delete_no":
        await query.edit_message_text(
            "Отмена удаления.", reply_markup=mail_menu_keyboard(user_id)
        )
        return MAIL_MENU


async def jira_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "back_to_mail_menu":
        await query.edit_message_text(
            "Настройки почты", reply_markup=mail_menu_keyboard(user_id)
        )
        return MAIL_MENU

    if data.startswith("toggle_jira_"):
        e_type = data.replace("toggle_jira_", "")
        conf = get_notifications_config(user_id)
        current_val = conf["jira"].get(e_type, False)
        set_jira_notification(user_id, e_type, not current_val)

        await query.edit_message_text(
            f"Переключили '{e_type}' -> {not current_val}",
            reply_markup=jira_menu_keyboard(user_id),
        )
        return JIRA_MENU


async def fallback_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "Не понял команду. Возвращаюсь в главное меню.",
        reply_markup=main_menu_keyboard(user_id),
    )
    return MAIN_MENU

# --------------------------------------------------------------------------- #
# Конструируем ConversationHandler                                             #
# --------------------------------------------------------------------------- #
def build_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_to_main_menu),
            ],
            SETTINGS_MENU: [
                CallbackQueryHandler(settings_menu_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_to_main_menu),
            ],
            MAIL_MENU: [
                CallbackQueryHandler(mail_menu_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_to_main_menu),
            ],
            CONFIRM_DELETE_EMAIL: [
                CallbackQueryHandler(confirm_delete_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_to_main_menu),
            ],
            JIRA_MENU: [
                CallbackQueryHandler(jira_menu_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_to_main_menu),
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
    )
