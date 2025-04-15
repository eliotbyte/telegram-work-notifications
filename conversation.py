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
    Кнопка "Почта" и "Назад в главное меню", а также "Уведомления о письмах" (Jira / не-Jira).
    Но в задаче было сказано "пока одна кнопка 'Почта'", но мы добавим и про "Mail notifications" / "Jira notifications".
    """
    buttons = [
        [InlineKeyboardButton("Почта", callback_data="mail_menu")],
        [InlineKeyboardButton("Уведомления Jira", callback_data="jira_menu")],
        [InlineKeyboardButton("Уведомления о письмах", callback_data="toggle_mail_notifications")],
        [InlineKeyboardButton("Назад", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(buttons)


# Подменю "Почта"
def mail_menu_keyboard(email_set: bool):
    """
    Кнопка "Удалить" (если почта настроена),
    Кнопка "Назад" -> в настройки.
    """
    buttons = []
    if email_set:
        buttons.append([InlineKeyboardButton("Удалить", callback_data="delete_email")])
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
    Каждая кнопка -> toggle_<event_type>, текст + статус [ON]/[OFF].
    """
    conf = get_notifications_config(user_id)
    jira_conf = conf["jira"]

    row_buttons = []
    for e_type, value in jira_conf.items():
        label = f"{e_type} [{'ON' if value else 'OFF'}]"
        row_buttons.append([InlineKeyboardButton(label, callback_data=f"toggle_jira_{e_type}")])

    # Добавляем кнопку "Назад"
    row_buttons.append([InlineKeyboardButton("Назад", callback_data="back_to_settings")])

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
        # Переходим в состояние ADD_EMAIL
        text = (
            "Введите свою почту и пароль в формате:\n\n"
            "`email@example.com пароль`\n\n"
            "Либо нажмите кнопку 'Помощь' для инструкции, или 'Отмена' чтобы вернуться."
        )
        # Построим клавиатуру с кнопками "Помощь" и "Отмена"
        kb = [
            [
                InlineKeyboardButton("Помощь", callback_data="help_email"),
                InlineKeyboardButton("Отмена", callback_data="cancel_add_email"),
            ]
        ]
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
        return ADD_EMAIL

    elif data == "settings":
        # Переходим в настройки
        await query.message.reply_text("Открываю настройки...", reply_markup=settings_menu_keyboard())
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

    # Попробуем залогиниться (imap.yandex.ru)
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
        await query.message.reply_text(text)
        return ADD_EMAIL
    elif data == "cancel_add_email":
        # Возврат в главное меню
        await query.message.reply_text(
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
        email_value, password, _ = get_email_credentials(user_id)
        email_set = bool(email_value and password)
        await query.message.reply_text("Настройки почты", reply_markup=mail_menu_keyboard(email_set))
        return MAIL_MENU

    elif data == "toggle_mail_notifications":
        toggle_mail_notifications(user_id)
        conf = get_notifications_config(user_id)
        status = "ВКЛ" if conf["mail"] else "ВЫКЛ"
        await query.message.reply_text(
            f"Уведомления по обычным письмам: {status}",
            reply_markup=settings_menu_keyboard()
        )
        return SETTINGS_MENU

    elif data == "jira_menu":
        await query.message.reply_text("Настройки Jira-уведомлений", reply_markup=jira_menu_keyboard(user_id))
        return JIRA_MENU

    elif data == "back_to_main":
        # Возврат в главное меню
        await query.message.reply_text("Главное меню", reply_markup=main_menu_keyboard(user_id))
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
        # Спрашиваем подтверждение
        await query.message.reply_text("Вы действительно хотите удалить почту?", reply_markup=confirm_delete_keyboard())
        return CONFIRM_DELETE_EMAIL
    elif data == "back_to_settings":
        await query.message.reply_text("Настройки", reply_markup=settings_menu_keyboard())
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
        # Удаляем
        clear_email_credentials(user_id)
        await query.message.reply_text("Почта удалена!", reply_markup=main_menu_keyboard(user_id))
        return MAIN_MENU
    elif data == "delete_no":
        # Возврат в MAIL_MENU
        email_value, password, _ = get_email_credentials(user_id)
        email_set = bool(email_value and password)
        await query.message.reply_text("Отмена удаления.", reply_markup=mail_menu_keyboard(email_set))
        return MAIL_MENU


async def jira_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка нажатий в меню Jira (JIRA_MENU).
    """
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    data = query.data

    if data == "back_to_settings":
        await query.message.reply_text("Настройки", reply_markup=settings_menu_keyboard())
        return SETTINGS_MENU

    if data.startswith("toggle_jira_"):
        e_type = data.replace("toggle_jira_", "")
        conf = get_notifications_config(user_id)
        current_val = conf["jira"].get(e_type, False)
        set_jira_notification(user_id, e_type, not current_val)

        # Перерисовываем клавиатуру
        await query.message.reply_text(
            f"Переключили уведомление '{e_type}' -> {not current_val}",
            reply_markup=jira_menu_keyboard(user_id)
        )
        return JIRA_MENU


async def ignore_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    В основном меню (и в настройках), если пользователь шлёт текст – игнорируем/говорим "выберите кнопку".
    """
    await update.message.reply_text("Пожалуйста, используйте кнопки меню.")


def build_conversation_handler():
    """
    Собираем ConversationHandler.
    """
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_handler),
                MessageHandler(filters.TEXT, ignore_text),
            ],
            ADD_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_email_handler_text),
                CallbackQueryHandler(add_email_handler_callback, pattern="^help_email|cancel_add_email$"),
            ],
            SETTINGS_MENU: [
                CallbackQueryHandler(settings_menu_handler),
                MessageHandler(filters.TEXT, ignore_text),
            ],
            MAIL_MENU: [
                CallbackQueryHandler(mail_menu_handler),
                MessageHandler(filters.TEXT, ignore_text),
            ],
            CONFIRM_DELETE_EMAIL: [
                CallbackQueryHandler(confirm_delete_handler),
            ],
            JIRA_MENU: [
                CallbackQueryHandler(jira_menu_handler),
            ],
        },
        fallbacks=[
            # Если что-то пошло не так, можно вернуть в MAIN_MENU
            CommandHandler("start", cmd_start),
        ],
    )
    return conv_handler
