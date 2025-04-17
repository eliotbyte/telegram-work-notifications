from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import requests
import os

# --- наши модули/конфиг ------------------------------------------------------
from config import load_user_config, set_email_credentials          # [OAUTH]

load_user_config()                                                  # [OAUTH]

app = FastAPI()

YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID")
YANDEX_CLIENT_SECRET = os.getenv("YANDEX_CLIENT_SECRET")
REDIRECT_URI = os.getenv("YANDEX_REDIRECT_URI")

BOT_TOKEN = os.getenv("BOT_TOKEN")                                  # [OAUTH]
INFO_URL = "https://login.yandex.ru/info?format=json"               # [OAUTH]


def _render_html(inner_html: str, *, auto_close: bool = False) -> str:
    """
    Оборачиваем переданный HTML‑контент в базовый шаблон.
    Если auto_close=True — добавляем JS, который закрывает окно через 3 с.
    """
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <title>Авторизация Yandex</title>

        <!-- базовый стиль -->
        <style>
            :root {{
                --bg:       #f5f7fa;
                --card-bg:  #ffffff;
                --accent:   #1a73e8;
                --text:     #2b2f33;
            }}

            * {{ box-sizing: border-box; }}

            body {{
                margin: 0;
                height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen,
                             Ubuntu, Cantarell, "Open Sans", "Helvetica Neue", sans-serif;
                background: var(--bg);
            }}

            .card {{
                max-width: 420px;
                padding: 2.5rem 3rem;
                background: var(--card-bg);
                border-radius: 1rem;
                box-shadow: 0 10px 30px rgba(0, 0, 0, .08);
                text-align: center;
            }}

            h1 {{
                margin: 0 0 .5rem;
                font-size: 1.6rem;
                color: var(--text);
            }}

            p {{
                margin: .25rem 0 0;
                font-size: 1rem;
                color: #575c60;
            }}

            .accent {{
                color: var(--accent);
                font-weight: 600;
                word-break: break-all;
            }}
        </style>

        {"<script>setTimeout(() => window.close(), 3000);</script>" if auto_close else ""}
    </head>
    <body>
        <div class="card">
            {inner_html}
        </div>
    </body>
    </html>
    """


@app.get("/callback", response_class=HTMLResponse)
async def yandex_callback(request: Request):
    """
    • Приходит с code и state (ID пользователя Telegram).  
    • Меняем code на access‑token, берём email через /info,  
      сохраняем в конфиг и шлём пользователю подтверждение.
    """

    code     = request.query_params.get("code")
    user_id  = request.query_params.get("state")                     # [OAUTH]

    if not code:
        return HTMLResponse(
            _render_html("<h1>❌ Ошибка</h1><p>Не получен code</p>"),
            status_code=400
        )

    # --- 1. берём access_token ----------------------------------------------
    token_resp = requests.post(
        "https://oauth.yandex.ru/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": YANDEX_CLIENT_ID,
            "client_secret": YANDEX_CLIENT_SECRET,
        },
        timeout=10,
    )

    if not token_resp.ok:
        return HTMLResponse(
            _render_html(
                f"<h1>❌ Ошибка</h1><p>Не удалось получить токен:<br>"
                f"<span class='accent'>{token_resp.text}</span></p>"
            ),
            status_code=500,
        )

    token_data   = token_resp.json()
    access_token = token_data.get("access_token")

    # --- 2. узнаём email пользователя ---------------------------------------
    email_value = None
    info_resp   = requests.get(
        INFO_URL, headers={"Authorization": f"OAuth {access_token}"}, timeout=5
    )
    if info_resp.ok:
        email_value = info_resp.json().get("default_email")

    # резервный вариант
    if not email_value:
        email_value = f"{token_data.get('uid', 'unknown')}@yandex.ru"

    # --- 3. сохраняем в общий user_config -----------------------------------
    try:
        set_email_credentials(int(user_id), email_value, access_token)  # [OAUTH]
    except Exception as e:                                              # noqa: BLE001
        return HTMLResponse(
            _render_html(
                f"<h1>❌ Ошибка</h1><p>Не удалось сохранить токен:<br>"
                f"<span class='accent'>{e}</span></p>"
            ),
            status_code=500,
        )

    # --- 4. уведомляем пользователя в Telegram ------------------------------
    if BOT_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": user_id,
                    "text": (
                        f"✅ Почта *{email_value}* подключена!\n"
                        "Уведомления о письмах включены.\n\n"
                        "Введите /start, чтобы перейти к настройкам."
                    ),
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=5,
            )
        except requests.RequestException:
            pass  # не критично: бот всё равно подхватит изменения из конфига

    # --- 5. html‑ответ пользователю -----------------------------------------
    success_html = (
        "<h1>✅ Почта <span class='accent'>{email}</span> подключена</h1>"
        "<p>Окно закроется автоматически.</p>"
    ).format(email=email_value)

    return HTMLResponse(_render_html(success_html, auto_close=True))
