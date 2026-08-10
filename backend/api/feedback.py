"""
Constitutional Assistant - Модуль обратной связи
Хранит два типа записей в feedback.jsonl:
  - type="feedback"  обычный текстовый отзыв
  - type="survey"    структурированный опросник (17 вопросов)

Ответы опросника ДОПОЛНИТЕЛЬНО пишутся в Google Sheets
чтобы не терялись при каждом деплое на Render.
"""
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

FEEDBACK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback.jsonl")
MAX_MESSAGE_LENGTH = 5000


def save_feedback(
    message: str,
    contact: Optional[str] = None,
    language: str = "RU",
    page: Optional[str] = None,
) -> Dict[str, Any]:
    """Сохраняет текстовый отзыв пользователя."""
    if not message or not message.strip():
        return {"success": False, "error": "Текст отзыва не может быть пустым"}
    record = {
        "type": "feedback",
        "feedback_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": message.strip()[:MAX_MESSAGE_LENGTH],
        "contact": (contact or "").strip()[:200] or None,
        "language": language,
        "page": page,
    }
    try:
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"success": True, "feedback_id": record["feedback_id"], "error": None}
    except Exception as e:
        return {"success": False, "error": f"Не удалось сохранить отзыв: {str(e)}"}


def save_survey(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Сохраняет структурированный ответ на опросник.
    1. Пишет в локальный feedback.jsonl (быстро, всегда)
    2. Пишет в Google Sheets (постоянное хранилище)
    """
    survey_id = str(uuid.uuid4())
    record = {
        "type": "survey",
        "survey_id": survey_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

    # 1. Локальный файл
    try:
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ Local save failed: {e}")

    # 2. Google Sheets
    try:
        from sheets_writer import write_survey_to_sheets
        sheets_result = write_survey_to_sheets(data)
        if not sheets_result.get("success"):
            print(f"⚠️ Sheets write failed: {sheets_result.get('error')}")
    except Exception as e:
        print(f"⚠️ Sheets import/write error: {e}")

    return {"success": True, "survey_id": survey_id, "error": None}


def list_feedback(limit: int = 200) -> List[Dict[str, Any]]:
    """Возвращает текстовые отзывы, новые сверху."""
    return _load_by_type("feedback", limit)


def list_surveys(limit: int = 500) -> List[Dict[str, Any]]:
    """Возвращает ответы на опросник из локального файла, новые сверху."""
    return _load_by_type("survey", limit)


def _load_by_type(record_type: str, limit: int) -> List[Dict[str, Any]]:
    if not os.path.exists(FEEDBACK_FILE):
        return []
    records = []
    try:
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("type", "feedback") == record_type:
                        records.append(rec)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    records.reverse()
    return records[:limit]


def count_feedback() -> int:
    return len(_load_by_type("feedback", 99999))


def count_surveys() -> int:
    return len(_load_by_type("survey", 99999))
