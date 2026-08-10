"""
Constitutional Assistant - Модуль обратной связи
Хранит два типа записей в feedback.jsonl:
  - type="feedback"  обычный текстовый отзыв (старый формат)
  - type="survey"    структурированный опросник (17 вопросов)
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
    """Сохраняет текстовый отзыв пользователя (старый формат)."""
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
    """Сохраняет структурированный ответ на опросник (17 вопросов)."""
    record = {
        "type": "survey",
        "survey_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    try:
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"success": True, "survey_id": record["survey_id"], "error": None}
    except Exception as e:
        return {"success": False, "error": f"Не удалось сохранить опросник: {str(e)}"}


def list_feedback(limit: int = 200) -> List[Dict[str, Any]]:
    """Возвращает текстовые отзывы (type=feedback), новые сверху."""
    return _load_by_type("feedback", limit)


def list_surveys(limit: int = 500) -> List[Dict[str, Any]]:
    """Возвращает ответы на опросник (type=survey), новые сверху."""
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
                    # совместимость со старыми записями без поля type
                    if rec.get("type", "feedback") == record_type:
                        records.append(rec)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    records.reverse()
    return records[:limit]


def count_feedback() -> int:
    """Общее количество текстовых отзывов."""
    return len(_load_by_type("feedback", 99999))


def count_surveys() -> int:
    """Общее количество ответов на опросник."""
    return len(_load_by_type("survey", 99999))
