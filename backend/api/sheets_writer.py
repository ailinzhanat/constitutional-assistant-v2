"""
Constitutional Assistant - Модуль записи в Google Sheets
- Каждый ответ НОВОГО опросника (survey.html от 09.09.2026, по Annex 1) пишется
  во вкладку "Survey_v2"
- Старая вкладка "Survey" (короткая анкета Ш1-Ш5/Q1-Q17) сохраняется как есть —
  этот модуль больше в неё не пишет, старые ответы никуда не переносятся и не
  удаляются
- Каждый текстовый отзыв пишется во вкладку "Feedback"
- Каждая новая тема форума — во вкладку "Forum_Threads", каждый ответ на
  форуме — во вкладку "Forum_Replies" (резервная копия рядом с основным
  хранилищем форума; best-effort, как и остальные вкладки)
"""
import json
import os
from typing import Dict, Any, Optional
from datetime import datetime, timezone

SPREADSHEET_ID = "1obSmVwWOYgO60DeAeHwoUo9kysPNOliCqCuynkrc9kU"

# Старая вкладка с ответами короткой анкеты (Ш1-Ш5/Q1-Q17) — только для
# истории, этот модуль в неё больше не пишет. Оставлена как константа на
# случай, если понадобится сослаться на неё (напр. при выгрузке/экспорте).
LEGACY_SHEET_NAME = "Survey"

# Новая вкладка — под текущую схему опросника (Annex 1, ICF + Блоки 1-7).
# Отдельная вкладка вместо переиспользования "Survey" означает, что старые
# строки ответов не нужно ни трогать, ни переносить: они остаются в "Survey"
# как были, а новые ответы копятся отдельно с самого начала, без риска, что
# колонки не совпадут.
SHEET_NAME = "Survey_v2"

FEEDBACK_SHEET_NAME = "Feedback"
ANALYTICS_SHEET_NAME = "Analytics"
FORUM_THREADS_SHEET_NAME = "Forum_Threads"
FORUM_REPLIES_SHEET_NAME = "Forum_Replies"

# Заголовки колонок событий аналитики (строка 1, вкладка Analytics)
ANALYTICS_HEADERS = [
    "Дата", "Session ID", "Шаг", "Тип события", "Язык", "Устройство",
    "Причина отказа (шаг 03)", "Новый посетитель", "Регион"
]

# Заголовки колонок тем форума (строка 1, вкладка Forum_Threads)
FORUM_THREAD_HEADERS = [
    "Дата", "Thread ID", "Категория", "Заголовок", "Текст", "Теги",
    "Автор", "Язык", "Привязка к шагу", "Ссылка на НП КС РК",
    "Обезличенный пример обращения", "Статус"
]

# Заголовки колонок ответов форума (строка 1, вкладка Forum_Replies)
FORUM_REPLY_HEADERS = [
    "Дата", "Reply ID", "Thread ID", "Текст", "Автор", "Цитируемый шаг", "Ссылка на НП КС РК"
]

# Заголовки колонок опросника (строка 1, вкладка Survey_v2)
# Схема соответствует survey.html от 09.09.2026, построенному по Annex 1
# (форма для ISE Committee): ICF + Блоки 1-7, ~63 позиции.
# Пишется в НОВУЮ вкладку (см. SHEET_NAME выше) — старая "Survey" и её ответы
# по короткой анкете не трогаются вообще.
SURVEY_SCALE_ITEMS = [
    (10, "2A"), (11, "2A"), (12, "2A"), (13, "2A"),
    (14, "2B"), (15, "2B"), (16, "2B"), (17, "2B"),
    (18, "2C"), (19, "2C"), (20, "2C"), (21, "2C"),
    (22, "2D"), (23, "2D"), (24, "2D"),
    (25, "2E"),
    (26, "2F"), (27, "2F"), (28, "2F"),
    (29, "2G"), (30, "2G"),
    (31, "2H"), (32, "2H"), (33, "2H"), (34, "2H"),
    (35, "2I"), (36, "2I"),
    (37, "2J"), (38, "2J"), (39, "2J"),
]
SURVEY_SCALE3_ITEMS = [(41, "3"), (42, "3"), (43, "3")]
SURVEY_SCALE5A_ITEMS = [(48, "5A"), (49, "5A"), (50, "5A")]
SURVEY_SUS_ITEMS = [(54, "SUS1"), (55, "SUS2"), (56, "SUS3"), (57, "SUS4"), (58, "SUS5"),
                     (59, "SUS6"), (60, "SUS7"), (61, "SUS8"), (62, "SUS9"), (63, "SUS10")]

HEADERS = (
    ["Дата", "Язык", "Согласие (ICF)",
     "Q1: роль", "Q1: другое",
     "Q2: обращался(лась) раньше",
     "Q3: id сессии",
     "Q4: язык интерфейса", "Q4: другое",
     "Q5: возраст", "Q6: пол", "Q7: образование",
     "Q8: частота ИИ-ассистентов", "Q9: частота eGov"]
    + [f"Q{n} ({c})" for n, c in SURVEY_SCALE_ITEMS]
    + ["Q40: затруднения (открытый)"]
    + [f"Q{n} ({c})" for n, c in SURVEY_SCALE3_ITEMS]
    + ["Q44: куда обратиться"]
    + ["Q45: время прохождения", "Q46: оценка времени", "Q46: детали (дольше)",
       "Q47: зависания", "Q47: детали (на каком шаге)"]
    + [f"Q{n} ({c})" for n, c in SURVEY_SCALE5A_ITEMS]
    + ["Q51: NPS (0-10)"]
    + ["Q52: что улучшить", "Q53: что понравилось"]
    + [f"Q{n} ({c})" for n, c in SURVEY_SUS_ITEMS]
    + ["Submitted at"]
)

# Заголовки колонок отзывов (строка 1, вкладка Feedback)
FEEDBACK_HEADERS = [
    "Дата", "Язык", "Страница", "Шаг интерфейса", "Роль автора",
    "Сообщение", "Категория", "Тональность", "Контакт", "Feedback ID"
]


def _get_service():
    """Создаёт клиент Google Sheets API через сервисный аккаунт."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    # Ключ берётся из переменной окружения GOOGLE_SERVICE_ACCOUNT_JSON
    key_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not key_json:
        raise Exception("GOOGLE_SERVICE_ACCOUNT_JSON не задан в переменных окружения")

    info = json.loads(key_json)
    creds = Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return service


def _ensure_sheet_exists(service, sheet_name: str) -> None:
    """Создаёт вкладку с указанным именем, если её ещё нет в таблице."""
    meta = service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    existing_titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if sheet_name not in existing_titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]}
        ).execute()


def ensure_headers(service) -> None:
    """
    Создаёт вкладку SHEET_NAME ("Survey_v2"), если её ещё нет, и — если она
    пустая — добавляет строку заголовков новой схемы опросника. Старую
    вкладку "Survey" не трогает.
    """
    _ensure_sheet_exists(service, SHEET_NAME)
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{SHEET_NAME}!A1:A1"
    ).execute()
    values = result.get("values", [])
    if not values:
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]}
        ).execute()


def _ensure_feedback_headers(service) -> None:
    """Если вкладка Feedback пустая — добавляет строку заголовков."""
    _ensure_sheet_exists(service, FEEDBACK_SHEET_NAME)
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{FEEDBACK_SHEET_NAME}!A1:A1"
    ).execute()
    values = result.get("values", [])
    if not values:
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{FEEDBACK_SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [FEEDBACK_HEADERS]}
        ).execute()


def write_survey_to_sheets(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Записывает один ответ НОВОГО опросника (Annex 1) в Google Sheets, во
    вкладку Survey_v2. Вкладку Survey_v2 создаёт сама (через ensure_headers),
    если её ещё нет. Старая вкладка Survey не используется и не изменяется.
    Возвращает {"success": True} или {"success": False, "error": "..."}
    """
    try:
        service = _get_service()
        ensure_headers(service)

        row = (
            [
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                data.get("language", ""),
                data.get("consent", ""),
                data.get("q1_role", ""), data.get("q1_other", ""),
                data.get("q2", ""),
                data.get("q3_session", ""),
                data.get("q4_lang", ""), data.get("q4_other", ""),
                data.get("q5_age", ""), data.get("q6_gender", ""), data.get("q7_edu", ""),
                data.get("q8_ai", ""), data.get("q9_egov", ""),
            ]
            + [data.get(f"item{n}", "") for n, _ in SURVEY_SCALE_ITEMS]
            + [data.get("q40", "")]
            + [data.get(f"item{n}", "") for n, _ in SURVEY_SCALE3_ITEMS]
            + [data.get("q44", "")]
            + [data.get("q45", ""), data.get("q46", ""), data.get("q46_text", ""),
               data.get("q47", ""), data.get("q47_text", "")]
            + [data.get(f"item{n}", "") for n, _ in SURVEY_SCALE5A_ITEMS]
            + [data.get("q51", "")]
            + [data.get("q52", ""), data.get("q53", "")]
            + [data.get(f"item{n}", "") for n, _ in SURVEY_SUS_ITEMS]
            + [data.get("submitted_at", "")]
        )

        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{SHEET_NAME}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]}
        ).execute()

        return {"success": True}

    except Exception as e:
        print(f"⚠️ Google Sheets write failed: {e}")
        return {"success": False, "error": str(e)}


def write_feedback_to_sheets(
    message: str,
    language: str = "",
    page: Optional[str] = None,
    contact: Optional[str] = None,
    feedback_id: Optional[str] = None,
    step: Optional[str] = None,
    role: Optional[str] = None,
    category: Optional[str] = None,
    sentiment: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Записывает один текстовый отзыв в Google Sheets (отдельная вкладка Feedback).
    FR-9/FR-10/FR-11: шаг, роль автора, автокатегория и тональность
    записываются вместе с отзывом, если известны.
    Возвращает {"success": True} или {"success": False, "error": "..."}
    """
    try:
        service = _get_service()
        _ensure_feedback_headers(service)

        row = [
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            language or "",
            page or "",
            step or "",
            role or "",
            message or "",
            category or "",
            sentiment or "",
            contact or "",
            feedback_id or "",
        ]

        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{FEEDBACK_SHEET_NAME}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]}
        ).execute()

        return {"success": True}

    except Exception as e:
        print(f"⚠️ Google Sheets feedback write failed: {e}")
        return {"success": False, "error": str(e)}


def _ensure_analytics_headers(service) -> None:
    """Если вкладка Analytics пустая — добавляет строку заголовков."""
    _ensure_sheet_exists(service, ANALYTICS_SHEET_NAME)
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{ANALYTICS_SHEET_NAME}!A1:A1"
    ).execute()
    values = result.get("values", [])
    if not values:
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{ANALYTICS_SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [ANALYTICS_HEADERS]}
        ).execute()


def write_analytics_event_to_sheets(
    session_id: str,
    step: str,
    event_type: str,
    language: str = "",
    device: str = "",
    jurisdiction_reason: Optional[str] = None,
    is_new_visitor: bool = True,
    region: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Записывает одно событие аналитики в Google Sheets (вкладка Analytics).
    Каждый вызов /api/analytics/event пишет одну строку.
    FR-4: регион пишется только если пользователь дал согласие (обычно пусто).
    Возвращает {"success": True} или {"success": False, "error": "..."}
    """
    try:
        service = _get_service()
        _ensure_analytics_headers(service)

        row = [
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            session_id or "",
            step or "",
            event_type or "",
            language or "",
            device or "",
            jurisdiction_reason or "",
            "да" if is_new_visitor else "нет",
            region or "",
        ]

        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{ANALYTICS_SHEET_NAME}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]}
        ).execute()

        return {"success": True}

    except Exception as e:
        print(f"⚠️ Google Sheets analytics write failed: {e}")
        return {"success": False, "error": str(e)}


def _ensure_forum_threads_headers(service) -> None:
    """Если вкладка Forum_Threads пустая — добавляет строку заголовков."""
    _ensure_sheet_exists(service, FORUM_THREADS_SHEET_NAME)
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{FORUM_THREADS_SHEET_NAME}!A1:A1"
    ).execute()
    values = result.get("values", [])
    if not values:
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{FORUM_THREADS_SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [FORUM_THREAD_HEADERS]}
        ).execute()


def _ensure_forum_replies_headers(service) -> None:
    """Если вкладка Forum_Replies пустая — добавляет строку заголовков."""
    _ensure_sheet_exists(service, FORUM_REPLIES_SHEET_NAME)
    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{FORUM_REPLIES_SHEET_NAME}!A1:A1"
    ).execute()
    values = result.get("values", [])
    if not values:
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{FORUM_REPLIES_SHEET_NAME}!A1",
            valueInputOption="RAW",
            body={"values": [FORUM_REPLY_HEADERS]}
        ).execute()


def write_forum_thread_to_sheets(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Резервная копия новой темы форума в Google Sheets (вкладка Forum_Threads).
    Вызывается best-effort из forum.create_thread() — как и остальные модули
    проекта, не должна ронять основной запрос, если Sheets недоступны.
    """
    try:
        service = _get_service()
        _ensure_forum_threads_headers(service)

        row = [
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            data.get("thread_id", ""),
            data.get("category", ""),
            data.get("title", ""),
            data.get("body", ""),
            data.get("tags", ""),
            data.get("author_display_name", ""),
            data.get("language", ""),
            data.get("linked_step", "") or "",
            data.get("np_reference", "") or "",
            data.get("attachment_text", "") or "",
            data.get("status", ""),
        ]

        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{FORUM_THREADS_SHEET_NAME}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]}
        ).execute()

        return {"success": True}

    except Exception as e:
        print(f"⚠️ Google Sheets forum thread write failed: {e}")
        return {"success": False, "error": str(e)}


def write_forum_reply_to_sheets(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Резервная копия нового ответа форума в Google Sheets (вкладка Forum_Replies).
    Вызывается best-effort из forum.create_reply().
    """
    try:
        service = _get_service()
        _ensure_forum_replies_headers(service)

        row = [
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            data.get("reply_id", ""),
            data.get("thread_id", ""),
            data.get("body", ""),
            data.get("author_display_name", ""),
            data.get("quoted_step", "") or "",
            data.get("np_reference", "") or "",
        ]

        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{FORUM_REPLIES_SHEET_NAME}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]}
        ).execute()

        return {"success": True}

    except Exception as e:
        print(f"⚠️ Google Sheets forum reply write failed: {e}")
        return {"success": False, "error": str(e)}
