import re
import logging
from bs4 import BeautifulSoup

def parse_jira_email(subject: str, raw_html: str) -> list[str]:
    """
    Парсер писем от Jira. Возвращает список готовых текстов для Telegram, 
    если это письмо про Jira и удалось разобрать обновления.
    Если не похоже на письмо от Jira или нет понятных паттернов — вернёт пустой список.
    """
    lower_html = raw_html.lower()
    if "jira.task-cloud.ru" not in lower_html and "atlassian jira" not in lower_html:
        return []

    soup = BeautifulSoup(raw_html, "html.parser")
    parsed_messages = []

    # 1) Ищем ссылку на задачу (например, NUTRB2B-104)
    link_el = soup.find("a", href=re.compile(r"https://jira\.task-cloud\.ru/browse/[A-Z0-9]+-\d+"))
    if link_el:
        issue_url = link_el["href"]
        issue_key = link_el.get_text(strip=True)
    else:
        issue_url = None
        issue_key = None

    # 2) Извлекаем заголовок задачи из <h1>
    summary_el = soup.find("h1")
    summary = summary_el.get_text(strip=True) if summary_el else ""

    # 3) Для удобства собираем ссылку в удобном формате (если нашли)
    if issue_key and issue_url:
        if summary:
            link_text = f'<a href="{issue_url}">[{issue_key}] {summary}</a>'
        else:
            link_text = f'<a href="{issue_url}">[{issue_key}]</a>'
    else:
        # fallback, если не нашли нормальных данных
        link_text = subject

    # 4) Получаем текст письма целиком (в нижнем регистре для поиска паттернов)
    body_text = soup.get_text(separator=" ", strip=True).lower()

    # 5) Проверяем паттерны:
    assigned_to_you = "assigned to you" in body_text
    you_were_mentioned = (
        "mentioned in a comment" in body_text
        or "you've been mentioned in a comment" in body_text
    )

    # Ищем "there is 1 update" или "there are 2 updates" и т.п.
    updates_match = re.search(r"there (?:is|are) (\d+) update", body_text)
    updates_count = int(updates_match.group(1)) if updates_match else 0

    # 6) Ищем авторов изменений по фразе "Changes by <strong>ИМЯ>" 
    #    (иногда бывает несколько авторов, если письмо суммирует изменения)
    strong_tags = soup.find_all("strong")
    changes_authors = []
    for s in strong_tags:
        parent_text = s.parent.get_text(separator=" ", strip=True).lower()
        if "changes by" in parent_text:
            changes_authors.append(s.get_text(strip=True))

    # --- Формируем сообщения ---

    # CASE A: Проверяем, не единственное ли это изменение — назначили исполнителем
    # (т.е. 1 update, есть автор, и флаг assigned_to_you)
    if assigned_to_you and updates_count == 1 and len(changes_authors) == 1:
        # Единичное изменение -> сразу одно сообщение с именем автора
        author = changes_authors[0]
        parsed_messages.append(
            f"✅ {author} назначил(а) вас исполнителем задачи {link_text}"
        )
        # В этом случае дальше не генерируем "изменил(а) задачу" и т.д.
    else:
        # Если нас назначили, но апдейтов либо >1, либо нет автора, 
        # то просто говорим, что назначили исполнителем
        if assigned_to_you:
            parsed_messages.append(
                f"✅ Вас назначили исполнителем задачи {link_text}"
            )
        
        # Если есть updates_count > 0 и есть автор(ы) изменений – формируем сообщения "изменил(а) задачу"
        # (если их несколько – будет несколько сообщений)
        if updates_count > 0 and changes_authors:
            for author in changes_authors:
                parsed_messages.append(
                    f"✏️ {author} изменил(а) задачу {link_text}"
                )
        # Если апдейты есть, но мы не смогли узнать автора:
        elif updates_count > 0:
            parsed_messages.append(
                f"✏️ В задаче {link_text} есть {updates_count} обновление(ий)."
            )

    # CASE B: Если вас упомянули в комментариях
    if you_were_mentioned and issue_key and issue_url:
        # Ищем конкретного автора упоминания
        mention_author = None

        for s in strong_tags:
            p_text = s.parent.get_text(separator=" ", strip=True).lower()

            # Находим именно блок, где "mentioned in a comment"
            if "mentioned in a comment" in p_text or "you've been mentioned in a comment" in p_text:
                # Скипаем этот <strong>, поскольку в нём как раз лежит "you've been mentioned..."
                continue
            
            # Ищем "автора комментария" - возможно, рядом есть фраза "on 10/Apr" и т.п.
            # или смотрим, есть ли внутри parent_text что-то вроде "on dd/mon"
            if "on " in p_text and "changes by" not in p_text:
                mention_author = s.get_text(strip=True)
                break

        if not mention_author:
            mention_author = "Кто-то"

        parsed_messages.append(
            f"👀 {mention_author} упомянул(а) вас в задаче {link_text}"
        )


    return parsed_messages
