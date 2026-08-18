"""
Модуль загрузки, хранения и использования нормативных постановлений
Конституционного Суда РК (НП КС РК) — Constitutional Assistant.

Реализует ТЗ "Загрузка, хранение и использование НП КС РК":
- FR-1..FR-3: приём текста постановления с метаданными (номер, даты, статус)
- FR-4..FR-5: хранение в Neo4j, версионирование по статусу
- FR-6: при проверке допустимости — находить, выносил ли КС РК уже НП по этой норме
- FR-7: при генерации черновика — предлагать релевантные НП как доп. аргумент
- FR-8: не использовать утратившие силу НП без явной пометки
- NFR-1: точность — каждая ссылка проверяема (номер+дата+source_url)
- NFR-3: дата актуальности базы отображается в интерфейсе

Источники (см. ТЗ раздел 3):
- sud.gov.kz / gov.kz — основной источник, ручная или согласованная загрузка
- Әділет (adilet.zan.kz) — только сверочный, robots.txt запрещает автопарсинг,
  этот модуль НИКОГДА не скрапит Әділет автоматически
- Загрузка НП в систему производится вручную ответственным сотрудником
  (через emit_ingest_report ниже) — не массовым скрапингом, как требует ТЗ.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Callable, List, Dict, Any, Optional, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
import os

ADMIN_ACCESS_CODE = os.environ.get("ADMIN_ACCESS_CODE", "")

router = APIRouter(prefix="/api/np", tags=["normative-resolutions"])

# Переиспользует то же подключение к Neo4j, что и analytics.py/feedback.py
_run_query: Optional[Callable] = None


def init_np_module(run_query_fn: Callable):
    """Вызывается один раз из main.py при старте приложения."""
    global _run_query
    _run_query = run_query_fn


def _query(cypher: str, params: dict = None):
    if _run_query is None:
        raise HTTPException(status_code=503, detail="НП-модуль не инициализирован (init_np_module не вызван в main.py)")
    try:
        return _run_query(cypher, params or {})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Ошибка Neo4j: {str(e)}")


def _check_admin(x_admin_code: Optional[str]):
    if not ADMIN_ACCESS_CODE or x_admin_code != ADMIN_ACCESS_CODE:
        raise HTTPException(status_code=403, detail="Неверный код доступа администратора")


# ---------------------------------------------------------------------------
# Модели
# ---------------------------------------------------------------------------

ResolutionStatus = Literal["active", "amended", "superseded"]


class ArticleRef(BaseModel):
    """Норма закона/Конституции, которую рассматривает или толкует постановление."""
    law: str = Field(..., description="Например: 'Конституция', 'УПК', 'Трудовой кодекс'")
    number: str = Field(..., description="Номер статьи, например '43' или '160'")
    part: Optional[str] = Field(None, description="Часть/пункт статьи, например 'п.1', 'ч.5'")


class NormativeResolutionIn(BaseModel):
    """FR-1/FR-2: данные для загрузки одного постановления в корпус."""
    number: str = Field(..., description="Номер постановления, например '89-НП'")
    date: str = Field(..., description="Дата принятия, ISO-формат YYYY-MM-DD")
    title: str = Field(..., description="Заголовок постановления")
    full_text: str = Field(..., description="Полный текст постановления")
    summary: Optional[str] = Field(None, description="Краткое резюме сути (1-2 предложения)")
    status: ResolutionStatus = "active"
    source_url: Optional[str] = Field(None, description="Ссылка на первоисточник (sud.gov.kz/gov.kz)")
    articles: List[ArticleRef] = Field(default_factory=list, description="Нормы, которые толкует/проверяет это НП")
    cites: List[str] = Field(default_factory=list, description="Номера других НП, которые цитирует это постановление")
    language: Literal["ru", "kk"] = "ru"


class RelevanceCheckRequest(BaseModel):
    """FR-6: проверка — выносил ли КС РК уже НП по указанной норме."""
    law: str
    article_number: str


# ---------------------------------------------------------------------------
# FR-1/FR-2/FR-4: загрузка постановления в корпус (ручная, защищено кодом админа)
# ---------------------------------------------------------------------------

@router.post("/ingest")
def ingest_resolution(resolution: NormativeResolutionIn, x_admin_code: Optional[str] = Header(None)):
    """
    Ручная загрузка одного НП КС РК в базу знаний.
    ТЗ прямо требует НЕ массовый скрапинг, а контролируемую загрузку
    ответственным сотрудником — поэтому эндпоинт защищён кодом администратора,
    а не открыт публично.
    """
    _check_admin(x_admin_code)

    now = datetime.now(timezone.utc).isoformat()

    # FR-4: базовый узел постановления с метаданными
    _query(
        """
        MERGE (r:NormativeResolution {number: $number})
        SET r.date = $date,
            r.title = $title,
            r.full_text = $full_text,
            r.summary = $summary,
            r.status = $status,
            r.source_url = $source_url,
            r.language = $language,
            r.loaded_at = $loaded_at
        """,
        {
            "number": resolution.number,
            "date": resolution.date,
            "title": resolution.title,
            "full_text": resolution.full_text,
            "summary": resolution.summary or "",
            "status": resolution.status,
            "source_url": resolution.source_url or "",
            "language": resolution.language,
            "loaded_at": now,
        },
    )

    # FR-2: связи с нормами, которые это НП толкует/проверяет
    for art in resolution.articles:
        _query(
            """
            MATCH (r:NormativeResolution {number: $number})
            MERGE (a:Article {law: $law, number: $art_number})
            MERGE (r)-[:REVIEWS]->(a)
            """,
            {"number": resolution.number, "law": art.law, "art_number": art.number},
        )

    # Связи цитирования между постановлениями — ключевое для FR-6/FR-7
    for cited_number in resolution.cites:
        _query(
            """
            MATCH (r:NormativeResolution {number: $number})
            MERGE (cited:NormativeResolution {number: $cited_number})
            MERGE (r)-[:CITES]->(cited)
            """,
            {"number": resolution.number, "cited_number": cited_number},
        )

    return {"status": "ok", "number": resolution.number, "loaded_at": now}


@router.get("/corpus-status")
def corpus_status(x_admin_code: Optional[str] = Header(None)):
    """
    NFR-3: дата актуальности базы — сколько НП загружено и когда последнее обновление.
    """
    _check_admin(x_admin_code)

    rows = _query(
        """
        MATCH (r:NormativeResolution)
        RETURN count(r) AS total,
               max(r.loaded_at) AS last_loaded_at,
               max(r.date) AS latest_resolution_date
        """
    )
    row = rows[0] if rows else {}
    return {
        "total_resolutions": row.get("total", 0),
        "last_loaded_at": row.get("last_loaded_at"),
        "latest_resolution_date": row.get("latest_resolution_date"),
    }


# Нормализация названий законов — Groq может вернуть "Уголовно-процессуальный
# кодекс" вместо "УПК" (как записано в базе при ручной загрузке), поэтому
# сопоставляем по общим синонимам, а не только по точному совпадению строки.
LAW_ALIASES = {
    "упк": "УПК",
    "уголовно-процессуальный кодекс": "УПК",
    "уголовно процессуальный кодекс": "УПК",
    "гпк": "ГПК",
    "гражданский процессуальный кодекс": "ГПК",
    "тк": "Трудовой кодекс",
    "трудовой кодекс": "Трудовой кодекс",
    "конституция": "Конституция",
    "конституция рк": "Конституция",
    "конституция республики казахстан": "Конституция",
    "экологический кодекс": "Экологический кодекс",
    "закон об исполнительном производстве": "Закон об исполнительном производстве",
    "закон \"об исполнительном производстве и статусе судебных исполнителей\"": "Закон об исполнительном производстве",
}


def _normalize_law(law: str) -> str:
    if not law:
        return law
    key = law.strip().lower()
    return LAW_ALIASES.get(key, law.strip())


def _normalize_article_number(number: str) -> str:
    """Убирает слово 'статья', лишние пробелы — оставляет только номер."""
    if not number:
        return number
    cleaned = re.sub(r"(?i)\bстать[яию]\b", "", str(number)).strip()
    cleaned = cleaned.rstrip(".")
    return cleaned


# ---------------------------------------------------------------------------
# FR-6: проверка — выносил ли КС РК уже НП по этой норме (используется на шаге 03)
# ---------------------------------------------------------------------------

def find_resolutions_for_article(law: str, article_number: str) -> List[Dict[str, Any]]:
    """
    Публичная Python-функция (не HTTP-эндпоинт) — вызывается напрямую из
    основного пайплайна анализа обращения (main.py / groq_analyzer.py),
    когда нужно узнать, рассматривал ли КС РК уже конкретную статью.

    Нормализует law/article_number перед поиском (см. LAW_ALIASES выше),
    чтобы разные формулировки от Groq находили одну и ту же запись в базе.

    FR-8: сразу помечает статус, чтобы вызывающий код мог явно указать
    "утратило силу с [дата]" вместо использования как действующей позиции.
    """
    law = _normalize_law(law)
    article_number = _normalize_article_number(article_number)
    rows = _query(
        """
        MATCH (r:NormativeResolution)-[:REVIEWS]->(a:Article {law: $law, number: $article_number})
        RETURN r.number AS number, r.date AS date, r.title AS title,
               r.summary AS summary, r.status AS status, r.source_url AS source_url
        ORDER BY r.date DESC
        """,
        {"law": law, "article_number": article_number},
    )
    return rows


@router.post("/check-relevance")
def check_relevance(request: RelevanceCheckRequest, x_admin_code: Optional[str] = Header(None)):
    """
    HTTP-обёртка над find_resolutions_for_article — для ручной проверки
    администратором/юристом или для отладки без похода в основной pipeline.
    """
    _check_admin(x_admin_code)
    results = find_resolutions_for_article(request.law, request.article_number)
    return {"count": len(results), "resolutions": results}


# ---------------------------------------------------------------------------
# FR-7: релевантные ссылки для черновика обращения (используется в generate_appeal)
# ---------------------------------------------------------------------------

def get_suggested_citations(law: str, article_number: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    Вызывается из pipeline генерации черновика (groq_generator.py / main.py).
    Возвращает НП, которые можно предложить как дополнительный аргумент —
    ВСЕГДА с explicit пометкой (см. formatted_note), что это подсказка,
    а не готовый вывод (риск из ТЗ раздела 7: "риск ложных совпадений").
    """
    results = find_resolutions_for_article(law, article_number)
    active_only = [r for r in results if r.get("status") == "active"]

    suggestions = []
    for r in active_only[:max_results]:
        suggestions.append({
            "number": r["number"],
            "date": r["date"],
            "title": r["title"],
            "summary": r.get("summary"),
            "source_url": r.get("source_url"),
            # FR-7: обязательная пометка при использовании в черновике
            "note": "Это автоматическая подсказка на основе базы практики КС РК. "
                    "Требует проверки юристом перед использованием.",
        })

    # FR-8: утратившие силу тоже возвращаем, но с явной меткой,
    # чтобы вызывающий код мог решить не использовать их как позицию суда
    superseded = [r for r in results if r.get("status") != "active"]
    for r in superseded[:max_results]:
        suggestions.append({
            "number": r["number"],
            "date": r["date"],
            "title": r["title"],
            "summary": r.get("summary"),
            "source_url": r.get("source_url"),
            "note": f"⚠️ Утратило силу / изменено (статус: {r.get('status')}) — "
                    f"не использовать как действующую позицию суда.",
            "superseded": True,
        })

    return suggestions


@router.get("/suggested-citations")
def suggested_citations_endpoint(law: str, article_number: str, max_results: int = 3):
    """Публичный (не защищённый кодом) эндпоинт — вызывается фронтендом при формировании черновика."""
    return {"suggestions": get_suggested_citations(law, article_number, max_results)}


# ---------------------------------------------------------------------------
# Просмотр корпуса (для админки — будущий интерфейс НП-каталога)
# ---------------------------------------------------------------------------

@router.get("/list")
def list_resolutions(limit: int = 200, x_admin_code: Optional[str] = Header(None)):
    _check_admin(x_admin_code)
    rows = _query(
        """
        MATCH (r:NormativeResolution)
        OPTIONAL MATCH (r)-[:REVIEWS]->(a:Article)
        WITH r, collect(DISTINCT a.law + ' ст.' + a.number) AS articles
        RETURN r.number AS number, r.date AS date, r.title AS title,
               r.status AS status, r.source_url AS source_url, articles
        ORDER BY r.date DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )
    return {"count": len(rows), "resolutions": rows}


@router.get("/{number}")
def get_resolution(number: str, x_admin_code: Optional[str] = Header(None)):
    _check_admin(x_admin_code)
    rows = _query(
        """
        MATCH (r:NormativeResolution {number: $number})
        OPTIONAL MATCH (r)-[:REVIEWS]->(a:Article)
        OPTIONAL MATCH (r)-[:CITES]->(cited:NormativeResolution)
        WITH r, collect(DISTINCT {law: a.law, number: a.number}) AS articles,
             collect(DISTINCT cited.number) AS cites
        RETURN r.number AS number, r.date AS date, r.title AS title,
               r.full_text AS full_text, r.summary AS summary, r.status AS status,
               r.source_url AS source_url, r.language AS language, articles, cites
        """,
        {"number": number},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"НП №{number} не найдено в базе")
    return rows[0]


# ---------------------------------------------------------------------------
# FR-9/FR-10: заглушка для регулярного обновления корпуса
# ---------------------------------------------------------------------------
#
# ТЗ требует проверку публикации новых НП не реже раза в неделю и уведомление
# ответственного сотрудника. Поскольку Әділет запрещает автопарсинг,
# а sud.gov.kz/gov.kz рендерятся через JS (недоступны для простого HTTP-fetch),
# автоматизировать это в рамках пилота нельзя технически честно.
#
# Рекомендация (не реализовано в этой версии, требует отдельного решения):
#   - еженедельный ручной чек-лист для ответственного сотрудника:
#     зайти на gov.kz/memleket/entities/ksrk/documents, сверить список
#     с уже загруженными номерами (см. GET /api/np/list), при появлении
#     новых — скачать PDF и загрузить через POST /api/np/ingest
#   - технически можно добавить cron-задачу, которая раз в неделю дёргает
#     GET /api/np/corpus-status и присылает email/уведомление администратору
#     с числом дней с последней загрузки, если оно больше 7 — как минимальный
#     аналог FR-9 без нарушения условий использования Әділет


# ---------------------------------------------------------------------------
# INTEGRATION — как подключить в main.py
# ---------------------------------------------------------------------------
#
# 1. Скопировать этот файл в backend/api/np_resolutions.py
#
# 2. В main.py добавить рядом с остальными init_*:
#
#      from np_resolutions import (
#          router as np_router,
#          init_np_module,
#          get_suggested_citations,
#          find_resolutions_for_article,
#      )
#      app.include_router(np_router)
#      init_np_module(run_query)
#
# 3. FR-6 — в основном pipeline анализа обращения (там, где определяется
#    within_jurisdiction / violation_id), после определения оспариваемой
#    статьи можно вызвать:
#
#      from np_resolutions import find_resolutions_for_article
#      prior_resolutions = find_resolutions_for_article(law="Конституция", article_number="43")
#      # если prior_resolutions не пуст — сообщить заявителю простым языком,
#      # что КС РК уже рассматривал эту норму (см. FR-6 формулировку в ТЗ)
#
# 4. FR-7 — в generate_appeal (main.py), после определения оспариваемой
#    статьи для черновика:
#
#      from np_resolutions import get_suggested_citations
#      suggested = get_suggested_citations(law="УПК", article_number="342")
#      # добавить suggested в ответ /generate-appeal как отдельное поле
#      # "suggested_np_citations" — фронтенд показывает их с пометкой
#      # "сгенерировано автоматически, требует проверки юристом"
#
# 5. Загрузка 5 пилотных НП (№85-89-НП) — см. отдельный скрипт seed_np_corpus.py
