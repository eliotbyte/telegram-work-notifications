import re
import logging
from bs4 import BeautifulSoup

def parse_jira_email(subject: str, raw_html: str) -> list[str] | None:
    """
    Парсер писем от Jira. Возвращает список готовых текстов для Telegram,
    если это письмо про Jira и удалось разобрать обновления.

    - None, если письмо НЕ похоже на Jira (тогда вызывающая сторона пошлёт "дефолтное" уведомление).
    - Пустой список [], если письмо - джировское, но мы решили, что уведомлять не о чем (и нужно
      явно пропустить дефолтную логику).
    - Один или несколько готовых текстов, если нашли события (назначение, упоминание и т.п.).
    """

    lower_html = raw_html.lower()
    # Узнаём, похоже ли письмо на Jira
    if "jira.task-cloud.ru" not in lower_html and "atlassian jira" not in lower_html:
        return None  # Не Jira — вернём None, чтобы сработала дефолтная логика

    soup = BeautifulSoup(raw_html, "html.parser")
    body_text = soup.get_text(separator=" ", strip=True).lower()

    # Ищем основную инфу — ссылку на задачу:
    link_el = soup.find("a", href=re.compile(r"https://jira\.task-cloud\.ru/browse/[A-Z0-9]+-\d+"))
    if link_el:
        issue_url = link_el["href"]
        issue_key = link_el.get_text(strip=True)
    else:
        issue_url = None
        issue_key = None

    # Парсим заголовок задачи (summary) из <h1>:
    summary_el = soup.find("h1")
    summary = summary_el.get_text(strip=True) if summary_el else ""

    # Собираем удобный кусок для ссылки:
    if issue_url and issue_key:
        if summary:
            link_text = f'<a href="{issue_url}">[{issue_key}] {summary}</a>'
        else:
            link_text = f'<a href="{issue_url}">[{issue_key}]</a>'
    else:
        # fallback, если не нашли нормальных данных
        link_text = subject

    # --- NEW LOGIC: Проверка на "Issue created" ---
    # Если письмо содержит "issue created" или "has been created", считаем, что это уведомление о новой задаче.
    # В этом случае отправляем ровно одно сообщение "📌 <РЕПОРТЕР> создала(а) задачу ..." и игнорируем остальные возможные блоки.
    created_match = re.search(r"(issue created|has been created)", body_text)
    if created_match:
        # Пытаемся вытащить имя репортера. Два способа:
        # 1) посмотреть в таблице "Reporter:"
        # 2) или найти фразу "<strong>ИМЯ</strong> created this issue on ..."

        reporter = None

        # способ 1: ищем строку "Reporter:"
        field_rows = soup.find_all("tr", class_=re.compile(r"field-update|row"))
        for row in field_rows:
            label = row.find("td", class_=re.compile(r"updates-diff-label"))
            if label and "reporter:" in label.get_text(separator=" ", strip=True).lower():
                content_td = row.find("td", class_=re.compile(r"updates-diff-content"))
                if content_td:
                    a = content_td.find("a")
                    if a:
                        reporter = a.get_text(strip=True)
                    else:
                        reporter = content_td.get_text(strip=True)
                break

        # способ 2 (fallback), если в таблице не нашли:
        if not reporter:
            strong_tags = soup.find_all("strong")
            for s in strong_tags:
                # ищем например: "Мищишина Дарина created this issue on"
                parent_text = s.parent.get_text(separator=" ", strip=True)
                if "created this issue on" in parent_text.lower():
                    reporter = s.get_text(strip=True)
                    break

        if not reporter:
            reporter = "Неизвестный репортёр"

        single_msg = f"📌 {reporter} создал(а) задачу {link_text}"
        return [single_msg]

    # --- NEW LOGIC: Если письмо говорит только о worklog'ах (и ничего больше) — не уведомлять ---
    # Пример: "There are 2 worklogs." и нет других слов "update", "comment", "assigned", "created", "mention".
    # Считаем, что такое письмо бесполезно -> возвращаем пустой список, чтобы ничего не слать.
    if "there are" in body_text and "worklog" in body_text:
        # Проверяем, нет ли других ключевых слов (update, comment, mentioned, assign, created)
        has_other_keywords = any(
            kw in body_text
            for kw in [
                "update", "updates", "comment", "comments", "assigned to you",
                "mentioned in a comment", "issue created", "has been created"
            ]
        )
        if not has_other_keywords:
            # только ворклоги -> пропускаем
            return []

    parsed_messages = []

    # Ищем, не назначили ли нас исполнителем:
    assigned_to_you = "assigned to you" in body_text

    # Ищем, не упомянули ли нас в комментариях:
    you_were_mentioned = (
        "mentioned in a comment" in body_text
        or "you've been mentioned in a comment" in body_text
    )

    # Ищем кол-во апдейтов (update/updates):
    updates_match = re.search(r"there (?:is|are) (\d+) update", body_text)
    updates_count = int(updates_match.group(1)) if updates_match else 0

    # Ищем авторов "Changes by <strong>NAME>"
    strong_tags = soup.find_all("strong")
    changes_authors = []
    for s in strong_tags:
        parent_text = s.parent.get_text(separator=" ", strip=True).lower()
        if "changes by" in parent_text:
            changes_authors.append(s.get_text(strip=True))

    # CASE A: Единственное изменение — нас назначили исполнителем
    if assigned_to_you and updates_count == 1 and len(changes_authors) == 1:
        author = changes_authors[0]
        parsed_messages.append(
            f"✅ {author} назначил(а) вас исполнителем задачи {link_text}"
        )
    else:
        # Если всё же нас назначили, но апдейтов > 1 или автора нет
        if assigned_to_you:
            parsed_messages.append(
                f"✅ Вас назначили исполнителем задачи {link_text}"
            )

        # Если есть updates_count > 0 и есть автор(ы)
        if updates_count > 0 and changes_authors:
            for author in changes_authors:
                parsed_messages.append(
                    f"✏️ {author} изменил(а) задачу {link_text}"
                )
        # Если апдейты есть, но автора нет
        elif updates_count > 0:
            parsed_messages.append(
                f"✏️ В задаче {link_text} есть {updates_count} обновление(ий)."
            )

    # CASE B: Упоминание в комментариях
    if you_were_mentioned and issue_key and issue_url:
        mention_author = None
        for s in strong_tags:
            p_text = s.parent.get_text(separator=" ", strip=True).lower()
            if "mentioned in a comment" in p_text or "you've been mentioned in a comment" in p_text:
                continue
            if "on " in p_text and "changes by" not in p_text:
                mention_author = s.get_text(strip=True)
                break
        if not mention_author:
            mention_author = "Кто-то"

        parsed_messages.append(
            f"👀 {mention_author} упомянул(а) вас в задаче {link_text}"
        )

    # --- NEW LOGIC: один обычный комментарий (без упоминания нас) ---
    # Триггер: "There is 1 comment" + отсутствие фразы о том, что нас упомянули.
    if ("there is 1 comment" in body_text) and not you_were_mentioned:
        # Пытаемся найти автора комментария в блоке "1 comment".
        comment_author = None

        # Ищем tr c классом, в котором может быть автор, например "group-header"
        group_header = soup.find("tr", class_=re.compile(r"group-header"))
        if group_header:
            strong_tag = group_header.find("strong")
            if strong_tag:
                comment_author = strong_tag.get_text(strip=True)

        if not comment_author:
            comment_author = "Кто-то"

        parsed_messages.append(
            f"💬 {comment_author} оставил(а) комментарий к задаче {link_text}"
        )

    # Если мы что-то насобирали, вернём список. Если нет — вернём пустой
    return parsed_messages
