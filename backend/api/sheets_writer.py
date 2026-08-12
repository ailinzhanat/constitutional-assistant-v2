"""
Constitutional Assistant - Модуль записи в Google Sheets
Каждый ответ опросника сразу пишется в таблицу Google Sheets.
"""
import json
import os
from typing import Dict, Any, Optional
from datetime import datetime, timezone

SPREADSHEET_ID = "1obSmVwWOYgO60DeAeHwoUo9kysPNOliCqCuynkrc9kU"
SHEET_NAME = "Survey"

# Заголовки колонок (строка 1)
HEADERS = [
    "Дата", "Язык", "Роль", "Опыт обращений",
    "Ш1: Понятность шагов", "Ш2: Понятность вопросов", "Ш3: Без сторонней помощи",
    "Ш4: Загрузка актов", "Ш5: Язык интерфейса",
    "Затруднения (Q6)",
    "Q7: Разница обжалование/оспаривание", "Q8: Компетенция КС",
    "Q9: Куда обратиться", "Q10: Черновик обоснования",
    "Q11: Время прохождения", "Q12: Оценка времени",
    "Q13: Зависания", "Q13: Детали зависания",
    "Q14: Удовлетворённость (1-5)", "Q15: NPS (0-10)",
    "Q16: Что улучшить", "Q17: Что понравилось",
    "Consent", "Submitted at"
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


def ensure_headers(service) -> None:
    """Если таблица пустая — добавляет строку заголовков."""
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


def write_survey_to_sheets(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Записывает один ответ опросника в Google Sheets.
    Возвращает {"success": True} или {"success": False, "error": "..."}
    """
    try:
        service = _get_service()
        ensure_headers(service)

        row = [
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            data.get("language", ""),
            data.get("q1", ""),
            data.get("q2", ""),
            data.get("sc1", ""), data.get("sc2", ""), data.get("sc3", ""),
            data.get("sc4", ""), data.get("sc5", ""),
            data.get("q6", ""),
            data.get("q7", ""), data.get("q8", ""),
            data.get("q9", ""), data.get("q10", ""),
            data.get("q11", ""), data.get("q12", ""),
            data.get("q13", ""), data.get("q13d", ""),
            data.get("q14", ""), data.get("q15", ""),
            data.get("q16", ""), data.get("q17", ""),
            data.get("consent", ""),
            data.get("submitted_at", ""),
        ]

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
