"""
Constitutional Assistant - Модуль обратной связи

Собирает отзывы пользователей о работе сайта и сохраняет их в файл feedback.jsonl
(одна строка = один отзыв, формат JSON Lines — удобно читать и не портится при дозаписи).

Просмотр отзывов — через защищённый endpoint в main.py.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# Файл хранения рядом с модулем
FEEDBACK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback.jsonl")

MAX_MESSAGE_LENGTH = 5000


def save_feedback(
    message: str,
    contact: Optional[str] = None,
    language: str = "RU",
    page: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Сохраняет отзыв пользователя.

    message  — текст отзыва (обязательно)
    contact  — email/телефон для ответа (по желанию пользователя, необязательно)
    language — язык интерфейса, на котором оставлен отзыв
    page     — с какой страницы оставлен отзыв (citizen / judicial)
    """
    if not message or not message.strip():
        return {"success": False, "error": "Текст отзыва не может быть пустым"}

    record = {
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


def list_feedback(limit: int = 200) -> List[Dict[str, Any]]:
    """Возвращает список отзывов, начиная с самых новых."""
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
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # пропускаем повреждённые строки, не роняя весь список
    except Exception:
        return []

    records.reverse()  # новые сверху
    return records[:limit]


def count_feedback() -> int:
    """Общее количество сохранённых отзывов."""
    if not os.path.exists(FEEDBACK_FILE):
        return 0
    try:
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0
