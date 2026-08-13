"""
Constitutional Assistant - Модуль обратной связи

Хранит два типа записей в Neo4j (узлы :Feedback и :Survey):
  - обычный текстовый отзыв
  - структурированный опросник (17 вопросов)

Перенесено с локального feedback.jsonl на Neo4j, потому что файловая
система на Render (free-tier) эфемерная — данные стирались при каждом
рестарте/redeploy сервиса. Neo4j данные не теряет даже если сама база
временно "засыпает" (нужно вручную нажать Resume в консоли Neo4j Aura).

Оба типа записей ДОПОЛНИТЕЛЬНО дублируются в Google Sheets
(sheets_writer.py) как вторая резервная копия — отзывы на вкладку
"Feedback", опросники на вкладку "Survey".
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Callable

MAX_MESSAGE_LENGTH = 5000

# Заполняется через init_feedback_module(run_query_fn) при подключении
# в main.py — переиспользует то же подключение к Neo4j, что и остальной
# проект (см. run_query в main.py), вместо отдельного соединения.
_run_query: Optional[Callable] = None


def init_feedback_module(run_query_fn: Callable):
    """Вызывается один раз из main.py при старте приложения."""
    global _run_query
    _run_query = run_query_fn


def _query(cypher: str, params: dict = None):
    if _run_query is None:
        raise Exception("feedback module не инициализирован (init_feedback_module не вызван в main.py)")
    return _run_query(cypher, params or {})


def save_feedback(
    message: str,
    contact: Optional[str] = None,
    language: str = "RU",
    page: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Сохраняет текстовый отзыв пользователя.
    1. Пишет в Neo4j (основное постоянное хранилище)
    2. Дополнительно пишет в Google Sheets (резервная копия, вкладка Feedback)
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
        _query(
            """
            CREATE (f:Feedback {
                feedback_id: $feedback_id,
                created_at: $created_at,
                message: $message,
                contact: $contact,
                language: $language,
                page: $page
            })
            """,
            record,
        )

        # Резервная копия в Google Sheets — не должна ронять весь запрос,
        # если сама запись в Neo4j (основное хранилище) уже прошла успешно.
        try:
            from sheets_writer import write_feedback_to_sheets
            sheets_result = write_feedback_to_sheets(
                message=record["message"],
                language=record["language"],
                page=record["page"],
                contact=record["contact"],
                feedback_id=record["feedback_id"],
            )
            if not sheets_result.get("success"):
                print(f"⚠️ Sheets feedback write failed: {sheets_result.get('error')}")
        except Exception as e:
            print(f"⚠️ Sheets feedback import/write error: {e}")

        return {"success": True, "feedback_id": record["feedback_id"], "error": None}
    except Exception as e:
        return {"success": False, "error": f"Не удалось сохранить отзыв: {str(e)}"}


def save_survey(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Сохраняет структурированный ответ на опросник.
    1. Пишет в Neo4j (основное постоянное хранилище)
    2. Дополнительно пишет в Google Sheets (резервная копия)
    """
    survey_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    # 1. Neo4j — данные опросника сохраняются как JSON-строка в свойстве
    #    data_json, потому что структура анкеты (17 полей) может меняться
    #    и не стоит жёстко фиксировать её в схеме узла.
    import json as _json
    try:
        _query(
            """
            CREATE (s:Survey {
                survey_id: $survey_id,
                created_at: $created_at,
                data_json: $data_json
            })
            """,
            {
                "survey_id": survey_id,
                "created_at": created_at,
                "data_json": _json.dumps(data, ensure_ascii=False),
            },
        )
    except Exception as e:
        print(f"⚠️ Neo4j survey save failed: {e}")

    # 2. Google Sheets (резервная копия, как и раньше)
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
    try:
        rows = _query(
            """
            MATCH (f:Feedback)
            RETURN f.feedback_id AS feedback_id, f.created_at AS created_at,
                   f.message AS message, f.contact AS contact,
                   f.language AS language, f.page AS page
            ORDER BY f.created_at DESC
            LIMIT $limit
            """,
            {"limit": limit},
        )
        return rows
    except Exception:
        return []


def list_surveys(limit: int = 500) -> List[Dict[str, Any]]:
    """Возвращает ответы на опросник, новые сверху."""
    import json as _json
    try:
        rows = _query(
            """
            MATCH (s:Survey)
            RETURN s.survey_id AS survey_id, s.created_at AS created_at, s.data_json AS data_json
            ORDER BY s.created_at DESC
            LIMIT $limit
            """,
            {"limit": limit},
        )
        result = []
        for row in rows:
            try:
                data = _json.loads(row["data_json"]) if row.get("data_json") else {}
            except (_json.JSONDecodeError, TypeError):
                data = {}
            result.append({
                "survey_id": row["survey_id"],
                "created_at": row["created_at"],
                "data": data,
            })
        return result
    except Exception:
        return []


def count_feedback() -> int:
    try:
        rows = _query("MATCH (f:Feedback) RETURN count(f) AS c", {})
        return rows[0]["c"] if rows else 0
    except Exception:
        return 0


def count_surveys() -> int:
    try:
        rows = _query("MATCH (s:Survey) RETURN count(s) AS c", {})
        return rows[0]["c"] if rows else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# INTEGRATION — как подключить в main.py
# ---------------------------------------------------------------------------
#
# В main.py, там же, где уже вызывается init_analytics_router(run_query),
# добавь строкой ниже:
#
#      from feedback import init_feedback_module
#      init_feedback_module(run_query)
#
# Импорты save_feedback/list_feedback/... в main.py остаются как есть —
# сигнатуры функций не изменились, изменилось только хранилище внутри.
#
# Рекомендуется (не обязательно) создать индекс в Neo4j для скорости:
#      CREATE INDEX IF NOT EXISTS FOR (f:Feedback) ON (f.created_at)
#      CREATE INDEX IF NOT EXISTS FOR (s:Survey) ON (s.created_at)
