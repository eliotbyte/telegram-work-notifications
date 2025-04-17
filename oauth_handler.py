from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import requests
import os
import json

# --- наши модули/конфиг ------------------------------------------------------
from config import load_user_config, set_email_credentials  # [OAUTH]

load_user_config()  # [OAUTH] загрузим существующие конфиги

app = FastAPI()

YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID")
YANDEX_CLIENT_SECRET = os.getenv("YANDEX_CLIENT_SECRET")
REDIRECT_URI = os.getenv("YANDEX_REDIRECT_URI")

BOT_TOKEN = os.getenv("BOT_TOKEN")                         # [OAUTH]

INFO_URL = "https://login.yandex.ru/info?format=json"      # [OAUTH]


@app.get("/callback", response_class=HTMLResponse)
async def yandex_callback(request: Request):
    """
    • Приходит с кодом и state (ID пользователя Telegram).  
    • Меняем код на access‑token, берём email через /info,  
      сохраняем в конфиг и шлём пользователю подтверждение.
    """
    code = request.query_params.get("code")
    user_id = request.query_params.get("state")            # [OAUTH]

    if not code:
        return HTMLResponse(
            "<h3>Ошибка: не получен code</h3>", status_code=400
        )

    # --- 1. берём access_token ------------------------------------------------
    token_resp = requests.post(
        "https://oauth.yandex.ru/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": YANDEX_CLIENT_ID,
            "client_secret": YANDEX_CLIENT_SECRET,
        },
    )

    if not token_resp.ok:
        return HTMLResponse(
            f"<h3>Ошибка при получении токена:<br>{token_resp.text}</h3>",
            status_code=500,
        )

    token_data = token_resp.json()
    access_token = token_data.get("access_token")

    # --- 2. узнаём email пользователя ----------------------------------------
    email_value = None
    info_resp = requests.get(
        INFO_URL, headers={"Authorization": f"OAuth {access_token}"}
    )
    if info_resp.ok:
        email_value = info_resp.json().get("default_email")

    # резервный вариант
    if not email_value:
        email_value = f"{token_data.get('uid', 'unknown')}@yandex.ru"

    # --- 3. сохраняем в общий user_config ------------------------------------
    try:
        set_email_credentials(int(user_id), email_value, access_token)  # [OAUTH]
    except Exception as e:
        # даже если что‑то пошло не так — вернём ошибку
        return HTMLResponse(f"<h3>Не удалось сохранить токен: {e}</h3>", status_code=500)

    # --- 4. уведомляем пользователя в Telegram -------------------------------
    if BOT_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": user_id,
                    "text": f"✅ Почта *{email_value}* подключена! "
                            "Уведомления о письмах включены.",
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=5,
            )
        except requests.RequestException:
            pass  # не критично, бот всё равно подхватит изменения из конфига

    # --- 5. html‑ответ пользователю ------------------------------------------
    return HTMLResponse(
        f"<h3>Успешно! Почта {email_value} привязана. Можете закрыть окно.</h3>"
    )
