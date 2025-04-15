import re
from bs4 import BeautifulSoup
from collections import defaultdict

def parse_jira_email(
    subject: str, 
    raw_html: str, 
    allowed_event_types: set[str] | None = None
) -> list[str] | None:
    """
    Парсер писем от Jira. Возвращает список из одного сообщения (в котором все события строчками),
    либо пустой список, либо None:
      - None, если письмо не похоже на Jira.
      - Пустой список ([]) — если это джировское письмо, но после фильтрации нет интересующих событий.
      - [единственная_строка] — если какие-то события есть и мы их объединили в один текст.

    Параметр allowed_event_types: набор типов событий, которые пользователь хочет получать.
      Если не передан (None), то берем все события.
      Если передан, то, например, {"mention_comment", "mention_description"} — значит отдаем
      только события об упоминании в задаче/комментариях, а всё остальное игнорируем.

    Возможные типы событий (для фильтрации):
      - "created"               (задачу создали)
      - "assigned"              (назначили исполнителем)
      - "update"                (изменили задачу)
      - "comment"               (оставили комментарий)
      - "mention_description"   (упомянули вас в описании/заголовке)
      - "mention_comment"       (упомянули вас в комментариях)
      - "worklog"               (протрекали время)
    """

    # -------------------------------------------
    # 1) Проверяем, что письмо из Jira
    # -------------------------------------------
    lower_html = raw_html.lower()
    if "jira.task-cloud.ru" not in lower_html and "atlassian jira" not in lower_html:
        return None  # Точно не Jira

    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator="\n", strip=True)

    # -------------------------------------------
    # 2) Достаём ключ задачи, ссылку, заголовок
    # -------------------------------------------
    # Ищем ссылку вида https://jira.task-cloud.ru/browse/XXX-NNN
    link_el = soup.find("a", href=re.compile(r"https://jira\.task-cloud\.ru/browse/[A-Z0-9]+-\d+"))
    if link_el:
        issue_url = link_el["href"]
        issue_key = link_el.get_text(strip=True)
    else:
        issue_url = None
        issue_key = None

    # Заголовок задачи
    summary_el = soup.find("h1")
    summary = summary_el.get_text(strip=True) if summary_el else ""

    # Если есть и ключ, и заголовок, то сформируем ссылку в виде:
    # [KEY] SUMMARY
    # И завернём это в <a href=...>
    if issue_key and issue_url:
        if summary:
            issue_link_text = f'<a href="{issue_url}">[{issue_key}] {summary}</a>'
        else:
            # Если заголовок пустой
            issue_link_text = f'<a href="{issue_url}">[{issue_key}]</a>'
    else:
        # fallback (не получилось найти данные), подставим subject
        issue_link_text = subject

    # -------------------------------------------
    # 3) Объявим контейнер для сбора событий
    #    Каждое событие хранится в виде:
    #    events[event_type] = set_of_authors
    # -------------------------------------------
    events = {
        "created": set(),
        "assigned": set(),
        "update": set(),
        "comment": set(),
        "mention_description": set(),
        "mention_comment": set(),
        "worklog": set(),
    }

    # -------------------------------------------
    # 4) Собираем признаки: что вообще в письме есть?
    # -------------------------------------------

    # 4.1) Создание задачи
    #     Смотрим фразы "Issue created" или "has been created".
    #     Парсим автора из "NAME created this issue on ..." или таблицы "Reporter:".
    created_match = re.search(r"(issue created|has been created)", text, re.IGNORECASE)
    if created_match:
        # Пытаемся найти имя в тексте вида "NAME created this issue on"
        # или в блоке "Reporter:"
        reporter = None

        # Сперва смотрим в таблице "Reporter:"
        # (часто это tr -> td( label ), td( content ), но здесь придётся искать по тексте)
        # У Jira есть паттерн, где tr с классом "field-update" или "row" может содержать ячейки с "Reporter:"
        field_rows = soup.find_all("tr", class_=re.compile(r"field-update|row", re.IGNORECASE))
        for row in field_rows:
            label_el = row.find("td", class_=re.compile(r"updates-diff-label|^label", re.IGNORECASE))
            if label_el and "reporter:" in label_el.get_text(separator=" ", strip=True).lower():
                content_td = row.find("td", class_=re.compile(r"updates-diff-content|^content", re.IGNORECASE))
                if content_td:
                    # Может быть <a> или простой текст
                    a = content_td.find("a")
                    if a:
                        reporter = a.get_text(strip=True)
                    else:
                        reporter = content_td.get_text(strip=True)
                break

        # Способ №2 (fallback) — ищем strong, внутри которого есть "NAME created this issue on"
        if not reporter:
            strong_tags = soup.find_all("strong")
            for s_tag in strong_tags:
                parent_text = s_tag.parent.get_text(separator=" ", strip=True).lower()
                if "created this issue on" in parent_text:
                    reporter = s_tag.get_text(strip=True)
                    break

        if not reporter:
            reporter = "Неизвестный репортёр"

        events["created"].add(reporter)

    # 4.2) Назначена вам
    #     Смотрим фразы: "assigned to you" или "This issue is now assigned to you"
    assigned_match = re.search(r"(assigned to you|this issue is now assigned to you)", text, re.IGNORECASE)
    if assigned_match:
        # По-хорошему, чтобы найти конкретного автора назначения, нужно смотреть, кто менял "Assignee" в апдейтах.
        # Jira часто пишет блок вида: "Changes by <strong>Имя Фамилия</strong> on <date>", внутри которого:
        #   Assignee: <старое> -> <новое>
        # Но для простоты возьмём первого "Changes by XXX", который содержит изменённое поле "Assignee".
        assigned_author = None

        # Найдём все блоки "Changes by <strong>...>"
        changes_by_blocks = soup.find_all(text=re.compile(r"Changes by", re.IGNORECASE))
        for t_node in changes_by_blocks:
            block_parent = t_node.find_parent()  # какой-то тег
            # Проверим, есть ли внутри этого блока упоминание "Assignee:"
            if block_parent and "assignee:" in block_parent.get_text(separator=" ", strip=True).lower():
                # Тогда выдёргиваем имя из <strong>...>
                s = block_parent.find("strong")
                if s:
                    assigned_author = s.get_text(strip=True)
                    break

        if not assigned_author:
            # Если не найден автор назначения, попробуем использовать автора, который создал задачу.
            if events["created"]:
                assigned_author = next(iter(events["created"]))
            else:
                # fallback: может быть, что нет структуры "changes by" и нет создателя, тогда
                # принимаем, что это "кто-то" назначил
                assigned_author = "Кто-то"

        events["assigned"].add(assigned_author)

    # 4.3) Упоминание вас в задаче (описание) или в комментариях
    #     Jira может писать "You've been mentioned in the issue description"
    #     или "mentioned in the issue description".
    #     Или "You've been mentioned in a comment", "mentioned in a comment".
    #     Для упрощения: если видим *any* "mentioned in a comment",
    #     считаем, что автор = тот, кто оставлял этот комментарий.
    #     Аналогично для description (но у Jira бывает редко).
    body_lower = text.lower()
    mention_in_desc = False
    mention_in_comment = False

    if re.search(r"(mentioned in the issue description|you've been mentioned in the issue description)", body_lower):
        mention_in_desc = True

    if re.search(r"(mentioned in a comment|you've been mentioned in a comment)", body_lower):
        mention_in_comment = True

    # Ищем авторов комментариев (список) — обычно это блок: "strong = Имя", рядом "on 25/Mar/25 5:20 PM"
    # Для упрощения найдём все комментарии, и если is_mention_in_comment = True,
    # то считаем, что автор(ы) комментариев — и есть те, кто нас упомянул.
    # (или берём только самый первый? но в ТЗ было сказано "берём первый попавшийся", однако
    #  можно взять всех — просто чтобы не упустить)
    comment_authors = set()
    comment_blocks = soup.find_all("h2", text=re.compile(r"comment", re.IGNORECASE))
    # Примерно после <h2> "1 comment" / "2 comments" идут тр-ки "Имя on Дата"
    # Но спарсить это универсально достаточно хлопотно, поэтому упрощённо:
    # найдём все strong внутри потенциальных "comment" блоков.
    for c_h2 in comment_blocks:
        # Идём "вниз" до <table>?
        table_el = c_h2.find_next("table")
        if not table_el:
            continue
        # Ищем все <strong> в этой таблице
        s_tags = table_el.find_all("strong")
        for s_tag in s_tags:
            parent_txt = s_tag.parent.get_text(separator=" ", strip=True).lower()
            if " on " in parent_txt:
                # Это похоже на "Имя Фамилия on 20/Mar/25 3:27 PM"
                comment_authors.add(s_tag.get_text(strip=True))

    if mention_in_desc:
        # Если упомянули вас в описании — кто мог это сделать?
        # Часто это либо репортёр, либо тот, кто делал update. Для упрощения берём
        # всех авторов из "Changes by" (кто менял Description?). Или fallback "Кто-то".
        mention_desc_authors = set()
        # Посмотрим, нет ли внутри "update" блока упоминания "description"
        updates_h2 = soup.find_all("h2", text=re.compile(r"update", re.IGNORECASE))
        for u_h2 in updates_h2:
            # Идём ниже по структуре, ищем "Description:"
            table_el = u_h2.find_next("table")
            if not table_el:
                continue
            # Проверяем, что в этой таблице есть "Description:"
            if "description:" in table_el.get_text(separator=" ", strip=True).lower():
                # Тогда берём автора из "Changes by <strong>NAME>"
                strong_el = table_el.find("strong")
                if strong_el:
                    mention_desc_authors.add(strong_el.get_text(strip=True))

        if not mention_desc_authors:
            # fallback
            mention_desc_authors.add("Кто-то")

        for a in mention_desc_authors:
            events["mention_description"].add(a)

    if mention_in_comment and comment_authors:
        # Значит, нас упомянули в комментариях, и автор(ы) — это комментаторы
        for ca in comment_authors:
            events["mention_comment"].add(ca)
    elif mention_in_comment and not comment_authors:
        # fallback
        events["mention_comment"].add("Кто-то")

    # 4.4) Обновления (update)
    #     Jira часто пишет "X updates" и затем "Changes by <strong>NAME>"
    #     Собираем всех авторов, которые что-то меняли
    updates_h2 = soup.find_all("h2", text=re.compile(r"update", re.IGNORECASE))
    update_authors = set()
    for h2_el in updates_h2:
        # После h2 "1 update" / "2 updates" ищем строки "Changes by <strong>Имя>"
        table_el = h2_el.find_next("table")
        if not table_el:
            continue
        changes_texts = table_el.find_all(text=re.compile(r"changes by", re.IGNORECASE))
        for ch_text in changes_texts:
            st = (ch_text.parent.find("strong") if ch_text.parent else None)
            if st:
                update_authors.add(st.get_text(strip=True))
    # Сохраняем
    for ua in update_authors:
        events["update"].add(ua)

    # 4.5) Комментарии (если не относятся к упоминанию)
    #     Если есть "X comments", внутри будут "Имя on Дата".
    #     Часть этих авторов мы уже поймали для mention_comment, но нам всё равно нужно
    #     отразить, что комментарии есть.
    #     (Важно: упоминание не исключает сам факт, что был комментарий. Но вы, возможно, захотите
    #      выводить отдельные строчки или совместить? По ТЗ написано: "упомянули вас в комментариях"
    #      — это отдельный event. Комментарий сам по себе — тоже event. Но можно схлопнуть.)
    #     В примере же хотят две разные строчки: "💬 ... оставил комментарий" и "👀 ... упомянул(а) вас..."
    comment_h2_list = soup.find_all("h2", text=re.compile(r"comment", re.IGNORECASE))
    all_comment_authors = set()
    for c_h2 in comment_h2_list:
        table_el = c_h2.find_next("table")
        if not table_el:
            continue
        # Ищем strong + "on <date>"
        s_tags = table_el.find_all("strong")
        for s_tag in s_tags:
            parent_txt = s_tag.parent.get_text(separator=" ", strip=True).lower()
            if " on " in parent_txt:
                all_comment_authors.add(s_tag.get_text(strip=True))

    # Добавляем этих авторов в events["comment"]
    # (Если кто-то уже есть в mention_comment, пусть и будет + в comments — это разные события)
    for ca in all_comment_authors:
        events["comment"].add(ca)

    # 4.6) Worklog
    #     Если встречаем "X worklog" или "worklog updates",
    #     то обычно в письме есть строки "NAME has added worklog on ...".
    #     Соберём всех таких NAME.
    worklog_authors = set()
    # Ищем все текстовые узлы "has added worklog" и берем <strong>...>?
    has_worklog_texts = soup.find_all(text=re.compile(r"has added worklog", re.IGNORECASE))
    for wt in has_worklog_texts:
        # Обычно это фрагмент типа: "<strong>Иванов Иван</strong> has added worklog on..."
        # значит автор вот этот <strong>.
        strong_el = wt.parent.find("strong") if wt.parent else None
        if strong_el:
            worklog_authors.add(strong_el.get_text(strip=True))
    # Добавляем
    for wa in worklog_authors:
        events["worklog"].add(wa)

    # -------------------------------------------
    # 5) Фильтрация по allowed_event_types
    #    (Если не передана, значит берём все)
    # -------------------------------------------
    if allowed_event_types is not None:
        for etype in list(events.keys()):
            if etype not in allowed_event_types:
                events[etype].clear()

    # Проверяем, остались ли у нас какие-то события
    has_any_event = any(len(s) > 0 for s in events.values())
    if not has_any_event:
        # Это значит, что письмо джировское, но, либо там и не было никаких событий, либо мы
        # их все отфильтровали.
        return []

    # -------------------------------------------
    # 6) Формируем один итоговый текст
    #    Теперь: 
    #      - в начале пишем "[ключ] заголовок (ссылка)"
    #      - далее группируем события по автору
    #      - порядок событий (на уровень пользователя) сохраняем:
    #        1) assigned
    #        2) created
    #        3) update
    #        4) comment
    #        5) mention_description
    #        6) mention_comment
    #        7) worklog
    # -------------------------------------------
    order = [
        "assigned",
        "created",
        "update",
        "comment",
        "mention_description",
        "mention_comment",
        "worklog",
    ]

    # Собираем: автор -> список его событий (в нужном порядке)
    author_events = defaultdict(list)
    for event_type in order:
        for author in sorted(events[event_type]):
            author_events[author].append(event_type)

    # Если ни у кого вообще нет событий, вернем пустой
    if not author_events:
        return []

    lines = []
    # Сначала строка с задачей
    lines.append(f"{issue_link_text}")
    lines.append("")

    # Затем по каждому автору (в порядке появления в author_events).
    # Если нужно другое упорядочивание — можно использовать sorted(author_events), но тогда будет алфавит.
    for author in author_events:
        lines.append(f"{author}:")
        for e_type in author_events[author]:
            if e_type == "assigned":
                lines.append("✅ назначил(а) вас исполнителем задачи")
            elif e_type == "created":
                lines.append("📌 создал(а) задачу")
            elif e_type == "update":
                lines.append("✏️ изменил(а) задачу")
            elif e_type == "comment":
                lines.append("💬 оставил(а) комментарий")
            elif e_type == "mention_description":
                lines.append("👀 упомянул(а) вас в задаче")
            elif e_type == "mention_comment":
                lines.append("👀 упомянул(а) вас в комментариях")
            elif e_type == "worklog":
                lines.append("⏱️ трекнул(а) время")
        lines.append("")  # пустая строка между авторами

    # Удаляем последнюю пустую строку
    if lines and not lines[-1].strip():
        lines.pop()

    final_message = "\n".join(lines)
    return [final_message]
