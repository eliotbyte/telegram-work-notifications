from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from config import (
    ensure_user_config,
    set_email_credentials,
    clear_email_credentials,
    get_email_credentials,
    set_jira_notification,
    toggle_mail_notifications,
    get_notifications_config,
)
import logging

# Состояния
MAIN_MENU, ADD_EMAIL, CONFIRM_DELETE_EMAIL, SETTINGS_MENU, MAIL_MENU, JIRA_MENU = range(6)

# ---- Кнопки ----

# Главное меню
def main_menu_keyboard(user_id: int):
    """
    Возвращаем inline-клавиатуру для главного меню:
      - Добавить почту (если почта не настроена)
      - Настройки (если почта уже есть)
    """
    email, password, _ = get_email_credentials(user_id)
    buttons = []
    if not email or not password:
        buttons.append([InlineKeyboardButton("Добавить почту", callback_data="add_email")])
    else:
        buttons.append([InlineKeyboardButton("Настройки", callback_data="settings")])

    markup = InlineKeyboardMarkup(buttons)
    return markup


# Подменю "Настройки"
def settings_menu_keyboard():
    """
    Кнопка "Почта" и "Назад в главное меню".
    """
    buttons = [
        [InlineKeyboardButton("Почта", callback_data="mail_menu")],
        [InlineKeyboardButton("Назад", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(buttons)


# Подменю "Почта"
def mail_menu_keyboard(user_id: int):
    """
    В меню "Почта" теперь:
      - "Удалить"
      - "Уведомления Jira"
      - "Уведомления о письмах [ДА/НЕТ]"
      - "Назад"
    """
    email_value, password, _ = get_email_credentials(user_id)
    email_set = bool(email_value and password)
    conf = get_notifications_config(user_id)

    buttons = []
    if email_set:
        buttons.append([InlineKeyboardButton("Удалить", callback_data="delete_email")])

    # Кнопка "Уведомления Jira"
    buttons.append([InlineKeyboardButton("Уведомления Jira", callback_data="jira_menu")])

    # Кнопка "Уведомления о письмах [ДА/НЕТ]"
    mail_on = conf["mail"]
    mail_label = "Уведомления о письмах [ДА]" if mail_on else "Уведомления о письмах [НЕТ]"
    buttons.append([InlineKeyboardButton(mail_label, callback_data="toggle_mail_notifications")])

    # Кнопка "Назад" -> в настройки
    buttons.append([InlineKeyboardButton("Назад", callback_data="back_to_settings")])

    return InlineKeyboardMarkup(buttons)


def confirm_delete_keyboard():
    buttons = [
        [
            InlineKeyboardButton("Да", callback_data="delete_yes"),
            InlineKeyboardButton("Нет", callback_data="delete_no"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


# Подменю Jira-уведомлений
def jira_menu_keyboard(user_id: int):
    """
    Генерируем кнопки для каждого типа событий: "created", "assigned" и т.д.
    Каждая кнопка -> toggle_<event_type>, текст + статус [ДА]/[НЕТ].
    """
    conf = get_notifications_config(user_id)
    jira_conf = conf["jira"]

    row_buttons = []
    for e_type, value in jira_conf.items():
        # ДА/НЕТ вместо ON/OFF
        label = f"{e_type} [{'ДА' if value else 'НЕТ'}]"
        row_buttons.append([InlineKeyboardButton(label, callback_data=f"toggle_jira_{e_type}")])

    # Добавляем кнопку "Назад"
    row_buttons.append([InlineKeyboardButton("Назад", callback_data="back_to_mail_menu")])

    return InlineKeyboardMarkup(row_buttons)

# ---- Хэндлеры ----

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Команда /start - просто приветствие и показ главного меню.
    """
    user_id = update.effective_user.id
    ensure_user_config(user_id)

    text = "Привет! Я бот для уведомлений.\nВыбирайте действие из меню ниже."
    await update.message.reply_text(
        text,
        reply_markup=main_menu_keyboard(user_id),
    )
    return MAIN_MENU


async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка нажатий в главном меню (callback_data).
    """
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    data = query.data
    if data == "add_email":
        # Переходим в состояние ADD_EMAIL, редактируем текущее сообщение
        text = (
            "Введите свою почту и пароль в формате:\n\n"
            "`email@example.com пароль`\n\n"
            "Либо нажмите кнопку 'Помощь' для инструкции, или 'Отмена' чтобы вернуться."
        )
        kb = [
            [
                InlineKeyboardButton("Помощь", callback_data="help_email"),
                InlineKeyboardButton("Отмена", callback_data="cancel_add_email"),
            ]
        ]
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return ADD_EMAIL

    elif data == "settings":
        # Переходим в настройки
        await query.edit_message_text(
            "Открываю настройки...",
            reply_markup=settings_menu_keyboard()
        )
        return SETTINGS_MENU


async def add_email_handler_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Пользователь прислал текст с логином и паролем (состояние ADD_EMAIL).
    """
    user_id = update.effective_user.id
    text_received = update.message.text.strip()

    # Парсим "email password"
    parts = text_received.split()
    if len(parts) != 2:
        await update.message.reply_text("❌ Формат неверный. Попробуйте ещё раз или нажмите 'Отмена'.")
        return ADD_EMAIL

    email_value, password = parts

    from imapclient import IMAPClient
    try:
        with IMAPClient("imap.yandex.ru", ssl=True) as client:
            client.login(email_value, password)
    except Exception as e:
        logging.warning(f"[{user_id}] Ошибка логина в почту: {e}")
        await update.message.reply_text(f"❌ Не удалось войти в почту:\n{e}\n\nПопробуйте снова или нажмите 'Отмена'.")
        return ADD_EMAIL

    # Если успешно, сохраняем
    set_email_credentials(user_id, email_value, password)
    await update.message.reply_text(
        "✅ Почта сохранена! Возвращаю вас в главное меню.",
        reply_markup=main_menu_keyboard(user_id)
    )
    return MAIN_MENU


async def add_email_handler_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка нажатий на кнопки "Помощь" / "Отмена" в состоянии ADD_EMAIL.
    """
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "help_email":
        text = (
            "1. Перейдите по ссылке: "
            "https://mail.yandex.ru/?#setup/client "
            "и убедитесь, что установлены флажки:\n"
            "- С сервера imap.yandex.ru по протоколу IMAP\n"
            "- Пароли приложений и OAuth-токены\n\n"
            "2. Перейдите по ссылке: "
            "https://id.yandex.ru/security/app-passwords\n"
            "и создайте пароль приложения, назвав его 'Чат-бот уведомления'."
        )
        await query.edit_message_text(text)
        return ADD_EMAIL
    elif data == "cancel_add_email":
        # Возврат в главное меню
        await query.edit_message_text(
            "Отмена. Возвращаюсь в главное меню.",
            reply_markup=main_menu_keyboard(user_id)
        )
        return MAIN_MENU


async def settings_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка нажатий в меню "Настройки" (состояние SETTINGS_MENU).
    """
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "mail_menu":
        await query.edit_message_text(
            "Настройки почты",
            reply_markup=mail_menu_keyboard(user_id)
        )
        return MAIL_MENU

    elif data == "back_to_main":
        # Возврат в главное меню
        await query.edit_message_text(
            "Главное меню",
            reply_markup=main_menu_keyboard(user_id)
        )
        return MAIN_MENU


async def mail_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка нажатий в меню "Почта" (MAIL_MENU).
    """
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "delete_email":
        await query.edit_message_text(
            "Вы действительно хотите удалить почту?",
            reply_markup=confirm_delete_keyboard()
        )
        return CONFIRM_DELETE_EMAIL

    elif data == "toggle_mail_notifications":
        toggle_mail_notifications(user_id)
        conf = get_notifications_config(user_id)
        mail_on = conf["mail"]
        status = "ДА" if mail_on else "НЕТ"
        await query.edit_message_text(
            f"Уведомления о письмах теперь: {status}",
            reply_markup=mail_menu_keyboard(user_id)
        )
        return MAIL_MENU

    elif data == "jira_menu":
        await query.edit_message_text(
            "Настройки Jira-уведомлений",
            reply_markup=jira_menu_keyboard(user_id)
        )
        return JIRA_MENU

    elif data == "back_to_settings":
        await query.edit_message_text(
            "Настройки",
            reply_markup=settings_menu_keyboard()
        )
        return SETTINGS_MENU


async def confirm_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка подтверждения удаления почты (CONFIRM_DELETE_EMAIL).
    """
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "delete_yes":
        clear_email_credentials(user_id)
        await query.edit_message_text(
            "Почта удалена!",
            reply_markup=main_menu_keyboard(user_id)
        )
        return MAIN_MENU
    elif data == "delete_no":
        await query.edit_message_text(
            "Отмена удаления.",
            reply_markup=mail_menu_keyboard(user_id)
        )
        return MAIL_MENU


async def jira_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка нажатий в меню Jira (JIRA_MENU).
    """
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "back_to_mail_menu":
        await query.edit_message_text(
            "Настройки почты",
            reply_markup=mail_menu_keyboard(user_id)
        )
        return MAIL_MENU

    if data.startswith("toggle_jira_"):
        e_type = data.replace("toggle_jira_", "")
        conf = get_notifications_config(user_id)
        current_val = conf["jira"].get(e_type, False)
        set_jira_notification(user_id, e_type, not current_val)

        await query.edit_message_text(
            f"Переключили '{e_type}' -> {not current_val}",
            reply_markup=jira_menu_keyboard(user_id)
        )
        return JIRA_MENU


async def fallback_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Если пользователь написал что-то рандомное не в ADD_EMAIL,
    то переводим его в главное меню.
    """
    user_id = update.effective_user.id
    await update.message.reply_text(
        "Не понял команду. Возвращаюсь в главное меню.",
        reply_markup=main_menu_keyboard(user_id)
    )
    return MAIN_MENU


def build_conversation_handler():
    """
    Собираем ConversationHandler.
    """
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler),
                # Любой текст -> fallback
                MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_to_main_menu),
            ],
            ADD_EMAIL: [
                # В ADD_EMAIL читаем текст как почта+пароль
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_email_handler_text),
                CallbackQueryHandler(add_email_handler_callback, pattern="^help_email|cancel_add_email$"),
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
        fallbacks=[
            # Если что-то пошло не так, можно вернуть в MAIN_MENU
            CommandHandler("start", cmd_start),
        ],
    )
    return conv_handler
