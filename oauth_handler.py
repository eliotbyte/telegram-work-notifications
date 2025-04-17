from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import requests
import os

from config import load_user_config, set_email_credentials

load_user_config()

app = FastAPI()

YANDEX_CLIENT_ID = os.getenv("YANDEX_CLIENT_ID")
YANDEX_CLIENT_SECRET = os.getenv("YANDEX_CLIENT_SECRET")
REDIRECT_URI = os.getenv("YANDEX_REDIRECT_URI")

BOT_TOKEN = os.getenv("BOT_TOKEN")
INFO_URL = "https://login.yandex.ru/info?format=json"


def tpl(body: str, auto_close: bool = False) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Авторизация Yandex</title>
<style>
:root{{--bg:#f5f7fa;--card:#fff;--accent:#1a73e8;--txt:#2b2f33}}
*{{box-sizing:border-box}}body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Oxygen,Ubuntu,Cantarell,"Open Sans","Helvetica Neue",sans-serif;background:var(--bg)}}
.card{{max-width:420px;padding:2.5rem 3rem;background:var(--card);border-radius:1rem;box-shadow:0 10px 30px rgba(0,0,0,.08);text-align:center}}
h1{{margin:0 0 .5rem;font-size:1.6rem;color:var(--txt)}}p{{margin:.25rem 0 0;font-size:1rem;color:#575c60}}
.accent{{color:var(--accent);font-weight:600;word-break:break-all}}
</style>{'<script>setTimeout(()=>window.close(),3000);</script>' if auto_close else ''}
</head><body><div class="card">{body}</div></body></html>"""


@app.get("/callback", response_class=HTMLResponse)
async def yandex_callback(request: Request):
    code = request.query_params.get("code")
    user_id = request.query_params.get("state")

    if not code:
        return HTMLResponse(tpl("<h1>❌ Ошибка</h1><p>Не получен code</p>"), 400)

    # 1️⃣ access_token
    tok = requests.post(
        "https://oauth.yandex.ru/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": YANDEX_CLIENT_ID,
            "client_secret": YANDEX_CLIENT_SECRET,
        },
        timeout=10,
    )
    if not tok.ok:
        return HTMLResponse(
            tpl(
                "<h1>❌ Ошибка</h1><p>Не удалось получить токен:<br>"
                f"<span class='accent'>{tok.text}</span></p>"
            ),
            500,
        )

    token = tok.json().get("access_token")

    # 2️⃣ e‑mail
    info = requests.get(
        INFO_URL, headers={"Authorization": f"OAuth {token}"}, timeout=5
    )
    email = (info.json().get("default_email") if info.ok else None) or (
        f"{tok.json().get('uid', 'unknown')}@yandex.ru"
    )

    # 3️⃣ сохраняем почту/токен
    set_email_credentials(int(user_id), email, token)

    # 4️⃣ UID‑закладка  —  импортим ЛЕНИВО, чтобы избежать раннего цикла
    from mail_checker import bookmark_latest_uid  # noqa: WPS433  (локальный импорт)
    await bookmark_latest_uid(int(user_id), email, token)

    # 5️⃣ сообщение в Telegram
    if BOT_TOKEN:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": user_id,
                    "text": (
                        f"✅ Почта *{email}* подключена!\n"
                        "Уведомления о письмах включены.\n\n"
                        "Введите /start, чтобы перейти к настройкам."
                    ),
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=5,
            )
        except requests.RequestException:
            pass

    # 6️⃣ HTML‑ответ
    return HTMLResponse(
        tpl(
            f"<h1>✅ Почта <span class='accent'>{email}</span> подключена</h1>"
            "<p>Окно закроется автоматически.</p>",
            auto_close=True,
        )
    )
