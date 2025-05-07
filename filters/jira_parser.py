import re
import logging
from bs4 import BeautifulSoup
from collections import defaultdict

logger = logging.getLogger(__name__)

# Minimal set of patterns needed for header-history events
HEADER_PATTERNS = {
    "assigned": {
        "ru": "Задача назначена вам",
        "en": "This issue is now assigned to you"
    },
    "mention_description": {
        "ru": "Вас упомянули в описании задачи",
        "en": "You've been mentioned in the issue description"
    },
    "mention_comment": {
        "ru": "Вас упомянули в комментарии",
        "en": "You've been mentioned in a comment"
    }
}

def parse_jira_email(email_content):
    """
    Парсит письмо от Jira и извлекает информацию о событиях.
    """
    if not email_content:
        return None

    # Проверяем, что письмо от Jira
    if '<body class="jira"' not in email_content:
        return None

    try:
        soup = BeautifulSoup(email_content, 'html.parser')
        text_content = soup.get_text()

        # 1. Парсим header-history для событий assigned, mention_description, mention_comment
        header_events = []
        header_history = soup.find("table", id="header-history")
        if header_history:
            header_text = header_history.get_text(separator=" ", strip=True)
            for event_type, patterns in HEADER_PATTERNS.items():
                if any(pattern in header_text for pattern in patterns.values()):
                    header_events.append({"type": event_type})

        # 2. Парсим structure для основной информации и событий
        structure = soup.find("table", class_="structure")
        if not structure:
            return None

        # 2.1 Получаем информацию о задаче
        header_updates = structure.find("td", class_="header updates")
        if not header_updates:
            return None

        # ID и ссылка задачи
        issue_key_td = header_updates.find("td", class_="issue-key")
        if not issue_key_td:
            return None
        
        issue_link = issue_key_td.find("a")
        if not issue_link:
            return None
            
        issue_key = issue_link.get_text(strip=True)
        issue_url = issue_link["href"]

        # Название задачи
        summary_td = header_updates.find("td", class_="issue-summary")
        if not summary_td:
            return None
            
        summary_h1 = summary_td.find("h1")
        if not summary_h1:
            return None
            
        summary = summary_h1.get_text(strip=True)

        # 2.2 Парсим события
        author_events = defaultdict(list)
        
        # Ищем все группы событий
        event_groups = structure.find_all("table", class_=re.compile(r"group (comments|updates|new-issue|worklogs)-group"))
        
        for group in event_groups:
            # Определяем тип события по классу группы
            group_class = group["class"][1]
            if "comments" in group_class:
                event_type = "comment"
            elif "updates" in group_class:
                event_type = "update"
            elif "new-issue" in group_class:
                event_type = "created"
            elif "worklogs" in group_class:
                event_type = "worklog"
            else:
                continue

            # Ищем автора события
            group_header = group.find("tr", class_="group-header")
            if not group_header:
                continue
                
            heading_td = group_header.find("td", class_="heading")
            if not heading_td:
                continue
                
            author_strong = heading_td.find("strong")
            if not author_strong:
                continue
                
            author = author_strong.get_text(strip=True)
            
            # Добавляем событие
            author_events[author].append({"type": event_type})

        # Добавляем события из header-history
        for event in header_events:
            author_events[None].append(event)

        if not author_events:
            return None

        return {
            'task_key': issue_key,
            'task_summary': summary,
            'task_url': issue_url,
            'author_events': dict(author_events)
        }

    except Exception as e:
        logger.error(f"Error parsing Jira email: {str(e)}")
        return None
