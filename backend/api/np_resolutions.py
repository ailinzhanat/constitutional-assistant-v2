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
    _ensure_fulltext_index()
def _ensure_fulltext_index():
    """
    FR-4: полнотекстовый (Lucene, встроен в Neo4j) индекс по title/summary/
    full_text — используется как fallback-семантический поиск, когда точное
    совпадение по (закон, статья) ничего не находит (см. find_resolutions_fuzzy
    ниже). Это НЕ векторные embeddings — честно: полноценный семантический
    поиск на embeddings потребовал бы внешнего embeddings-API (OpenAI/Cohere/
    Voyage и т.п.), которого сейчас нет в стеке проекта, и это отдельное
    решение (выбор провайдера, стоимость), которое должен принять владелец
    проекта. Полнотекстовый индекс — честный практичный шаг в сторону FR-4
    без добавления нового внешнего платного сервиса.
    IF NOT EXISTS — безопасно вызывать при каждом старте приложения.
    """
    try:
        _query(
            """
            CREATE FULLTEXT INDEX np_fulltext IF NOT EXISTS
            FOR (r:NormativeResolution) ON EACH [r.title, r.summary, r.full_text]
            """
        )
    except Exception as e:
        print(f"⚠️ Не удалось создать полнотекстовый индекс np_fulltext: {e}")
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
# Сортировка дат в формате ДД.ММ.ГГГГ
# ---------------------------------------------------------------------------
# ВАЖНО: в базе даты (r.date) хранятся как текст "ДД.ММ.ГГГГ" (так исторически
# сложилось при ручной загрузке всех 89 постановлений), а не как настоящий
# ISO-формат, который изначально описан в NormativeResolutionIn. Обычный
# Cypher "ORDER BY r.date DESC" / "max(r.date)" сравнивает такие строки
# посимвольно как текст — это даёт НЕВЕРНЫЙ хронологический порядок (например,
# "31.08.2023" оказывается "больше" любой даты 2026 года, потому что первый
# символ '3' > '0'). Этот фрагмент строит временный сортируемый ключ
# "ГГГГММДД" прямо в запросе, не меняя сами данные в базе.
_DATE_SORT_KEY_CYPHER = """
    CASE
        WHEN r.date =~ '\\\\d{2}\\\\.\\\\d{2}\\\\.\\\\d{4}'
            THEN substring(r.date, 6, 4) + substring(r.date, 3, 2) + substring(r.date, 0, 2)
        WHEN r.date =~ '\\\\d{4}-\\\\d{2}-\\\\d{2}'
            THEN substring(r.date, 0, 4) + substring(r.date, 5, 2) + substring(r.date, 8, 2)
        ELSE r.date
    END
"""
def _sort_key_to_display_date(sort_key: Optional[str]) -> Optional[str]:
    """Обратное преобразование "ГГГГММДД" -> "ДД.ММ.ГГГГ" для ответа API."""
    if sort_key and len(sort_key) == 8 and sort_key.isdigit():
        return f"{sort_key[6:8]}.{sort_key[4:6]}.{sort_key[0:4]}"
    return sort_key
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
    """FR-1/FR-2/FR-3: данные для загрузки одного постановления в корпус."""
    number: str = Field(..., description="Номер постановления, например '89-НП'")
    date: str = Field(..., description="Дата принятия, формат ДД.ММ.ГГГГ (согласовано с текущей загрузкой корпуса)")
    title: str = Field(..., description="Заголовок постановления (на языке из поля language, обычно ru)")
    full_text: str = Field(..., description="Полный текст постановления")
    summary: Optional[str] = Field(None, description="Краткое резюме сути (1-2 предложения)")
    status: ResolutionStatus = "active"
    source_url: Optional[str] = Field(None, description="Ссылка на первоисточник (sud.gov.kz/gov.kz)")
    articles: List[ArticleRef] = Field(default_factory=list, description="Нормы, которые толкует/проверяет это НП")
    cites: List[str] = Field(default_factory=list, description="Номера других НП, которые цитирует это постановление")
    language: Literal["ru", "kk", "en"] = "ru"
    # FR-3: переводы заголовка/резюме на казахский и английский — необязательные,
    # заполняются отдельным проходом поверх уже загруженной записи (title/summary
    # остаются "исходным" русским текстом независимо от этих полей). Если для
    # конкретного языка перевод не передан — при выдаче используется fallback на ru.
    title_kk: Optional[str] = Field(None, description="Заголовок на казахском (FR-3)")
    summary_kk: Optional[str] = Field(None, description="Резюме на казахском (FR-3)")
    title_en: Optional[str] = Field(None, description="Заголовок на английском (FR-3)")
    summary_en: Optional[str] = Field(None, description="Резюме на английском (FR-3)")
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
    # FR-3: title_kk/summary_kk/title_en/summary_en используют COALESCE — если
    # в этом конкретном запросе перевод не передан (None), уже сохранённый
    # перевод из предыдущей загрузки НЕ затирается пустой строкой. Это позволяет
    # сначала загрузить запись на русском, а переводы добавить отдельным
    # проходом (повторным POST того же number с заполненными title_kk и т.д.),
    # не потеряв уже существующие переводы, если они уже были загружены.
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
            r.loaded_at = $loaded_at,
            r.title_kk = COALESCE($title_kk, r.title_kk),
            r.summary_kk = COALESCE($summary_kk, r.summary_kk),
            r.title_en = COALESCE($title_en, r.title_en),
            r.summary_en = COALESCE($summary_en, r.summary_en)
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
            "title_kk": resolution.title_kk,
            "summary_kk": resolution.summary_kk,
            "title_en": resolution.title_en,
            "summary_en": resolution.summary_en,
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
            # Нормализуем ЗДЕСЬ теми же функциями, что и на стороне поиска
            # (см. _normalize_law/_normalize_article_number ниже) — иначе
            # закон, введённый как "Уголовно-процессуальный кодекс РК от
            # 4 июля 2014 года", не совпадёт с тем, что Groq извлечёт из
            # обращения гражданина как "УПК" или "Уголовно-процессуальный
            # кодекс", и FR-6/FR-7 никогда не найдёт эту связь.
            {
                "number": resolution.number,
                "law": _normalize_law(art.law),
                "art_number": _normalize_article_number(art.number),
            },
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
        f"""
        MATCH (r:NormativeResolution)
        WITH r, {_DATE_SORT_KEY_CYPHER} AS date_sort
        RETURN count(r) AS total,
               max(r.loaded_at) AS last_loaded_at,
               max(date_sort) AS latest_sort
        """
    )
    row = rows[0] if rows else {}
    return {
        "total_resolutions": row.get("total", 0),
        "last_loaded_at": row.get("last_loaded_at"),
        "latest_resolution_date": _sort_key_to_display_date(row.get("latest_sort")),
    }
# Нормализация названий законов — Groq может вернуть "Уголовно-процессуальный
# кодекс" вместо "УПК" (как записано в базе при ручной загрузке), поэтому
# сопоставляем по ключевым словам-подстрокам (не по точному совпадению
# строки), т.к. Groq и ручная загрузка могут называть один и тот же закон
# по-разному ("ТК", "Трудовой кодекс", "Трудовой кодекс Республики Казахстан
# от 23 ноября 2015 года" — всё должно свестись к одному каноническому виду).
# Порядок важен: более специфичные формулировки проверяются раньше более
# общих (например "уголовно-процессуальный" раньше "уголовный кодекс"),
# иначе специфичный закон ошибочно попадёт в общую категорию.
_LAW_KEYWORD_RULES = [
    ("уголовно-процессуальн", "УПК"),
    ("уголовно-исполнительн", "УИК"),
    ("уголовный кодекс", "УК"),
    ("гражданско-процессуальн", "ГПК"),
    ("гражданский процессуальный", "ГПК"),
    ("гражданский кодекс", "ГК"),
    ("административный процедурно-процессуальн", "АППК"),
    ("административных правонарушен", "КоАП"),
    ("трудовой кодекс", "Трудовой кодекс"),
    ("земельный кодекс", "Земельный кодекс"),
    ("социальный кодекс", "Социальный кодекс"),
    ("налогов", "Налоговый кодекс"),
    ("браке", "Кодекс о браке и семье"),
    ("супружеств", "Кодекс о браке и семье"),
    ("исполнительном производстве", "Закон об исполнительном производстве"),
    ("экологический кодекс", "Экологический кодекс"),
    ("о государственной службе", "Закон о государственной службе"),
    ("о правоохранительной службе", "Закон о правоохранительной службе"),
    ("о воинской службе", "Закон о воинской службе"),
    ("о судебной системе", "Закон о судебной системе"),
    ("о выборах", "Закон о выборах"),
    ("о доступе к информации", "Закон о доступе к информации"),
    ("о противодействии коррупции", "Закон о противодействии коррупции"),
    ("о пенсионном обеспечении", "Закон о пенсионном обеспечении"),
    ("средствах массовой информации", "Закон о СМИ"),
    ("о связи", "Закон о связи"),
    ("о банках", "Закон о банках"),
    ("арбитраже", "Закон об арбитраже"),
    ("дорожном движении", "Закон о дорожном движении"),
    ("мирных собраний", "Закон о мирных собраниях"),
    ("о религиозной деятельности", "Закон о религиозной деятельности"),
    ("незаконно приобретенных активов", "Закон о возврате государству незаконно приобретенных активов"),
    ("социальной защите граждан", "Закон о социальной защите пострадавших от ядерных испытаний"),
    ("обязательном страховании гражданско-правовой", "Закон об обязательном страховании ГПО владельцев ТС"),
    ("специальном правовом режиме города алатау", "Конституционный закон о специальном правовом режиме г.Алатау"),
    ("административной реформы", "Закон об административной реформе"),
]
def _normalize_law(law: str) -> str:
    """
    Приводит любую формулировку названия закона к единому каноническому
    виду по ключевым словам-подстрокам (см. _LAW_KEYWORD_RULES выше), а не
    по точному совпадению строки целиком — это устойчиво к тому, что Groq
    и ручная загрузка НП называют один и тот же закон по-разному (с датой,
    без даты, с "Республики Казахстан" или без). Используется И при
    сохранении связи REVIEWS (ingest), И при поиске (find_resolutions_for_article) —
    поэтому обе стороны всегда приходят к одному и тому же каноническому имени.
    Если ни одно ключевое слово не подошло — возвращает исходную (обрезанную)
    строку как есть, чтобы точное совпадение всё ещё было возможно.
    """
    if not law:
        return law
    t = law.strip().lower()
    for keyword, canonical in _LAW_KEYWORD_RULES:
        if keyword in t:
            return canonical
    if "конституция" in t and "конституционный закон" not in t \
            and "конституционного совета" not in t and "конституционного суда" not in t:
        return "Конституция"
    return law.strip()
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
        f"""
        MATCH (r:NormativeResolution)-[:REVIEWS]->(a:Article {{law: $law, number: $article_number}})
        WITH r, {_DATE_SORT_KEY_CYPHER} AS date_sort
        RETURN r.number AS number, r.date AS date, r.title AS title,
               r.summary AS summary, r.status AS status, r.source_url AS source_url,
               r.title_kk AS title_kk, r.summary_kk AS summary_kk,
               r.title_en AS title_en, r.summary_en AS summary_en
        ORDER BY date_sort DESC
        """,
        {"law": law, "article_number": article_number},
    )
    return rows
def find_resolutions_fuzzy(query_text: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """
    FR-4 (частично, см. _ensure_fulltext_index): семантический fallback —
    когда точное совпадение (закон, статья) в find_resolutions_for_article
    ничего не находит (например, норма извлечена Groq неточно, или НП
    касается вопроса без чёткой привязки к одной статье), ищем по смыслу
    описания жалобы через полнотекстовый Lucene-индекс по title/summary/
    full_text. Результаты этого поиска ВСЕГДА помечаются как "возможно
    релевантно" (risk из ТЗ раздела 7: "риск ложных совпадений") — это
    менее надёжно, чем точное совпадение по статье, и никогда не должно
    подаваться как эквивалент точного результата.
    """
    if not query_text or not query_text.strip():
        return []
    try:
        rows = _query(
            """
            CALL db.index.fulltext.queryNodes('np_fulltext', $query_text)
            YIELD node AS r, score
            WHERE r.status = 'active'
            RETURN r.number AS number, r.date AS date, r.title AS title,
                   r.summary AS summary, r.status AS status, r.source_url AS source_url,
                   r.title_kk AS title_kk, r.summary_kk AS summary_kk,
                   r.title_en AS title_en, r.summary_en AS summary_en, score
            ORDER BY score DESC
            LIMIT $max_results
            """,
            {"query_text": query_text[:1000], "max_results": max_results},
        )
        return rows
    except Exception as e:
        print(f"⚠️ Полнотекстовый поиск np_fulltext не удался: {e}")
        return []
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
def _localized_title_summary(r: Dict[str, Any], language: str) -> Dict[str, Any]:
    """
    FR-3: выбирает title/summary на запрошенном языке (kk/en), с откатом
    на русский (title/summary всегда заполнены — это исходные поля), если
    перевод для этого конкретного НП ещё не загружен. Так интерфейс никогда
    не показывает пустую карточку из-за отсутствующего перевода.
    """
    if language == "kk":
        return {
            "title": r.get("title_kk") or r["title"],
            "summary": r.get("summary_kk") or r.get("summary"),
        }
    if language == "en":
        return {
            "title": r.get("title_en") or r["title"],
            "summary": r.get("summary_en") or r.get("summary"),
        }
    return {"title": r["title"], "summary": r.get("summary")}
_NOTE_BY_LANGUAGE = {
    "ru": "Это автоматическая подсказка на основе базы практики КС РК. Требует проверки юристом перед использованием.",
    "kk": "Бұл — КС РК тәжірибесі базасы негізіндегі автоматты ұсыныс. Пайдаланар алдында заңгердің тексеруін қажет етеді.",
    "en": "This is an automated suggestion based on the Constitutional Court's case-law database. It requires review by a lawyer before use.",
}
_SUPERSEDED_NOTE_BY_LANGUAGE = {
    "ru": "Утратило силу / изменено (статус: {status}) — не использовать как действующую позицию суда.",
    "kk": "Күшін жойған / өзгертілген (мәртебесі: {status}) — соттың қолданыстағы позициясы ретінде пайдаланбау керек.",
    "en": "No longer in force / amended (status: {status}) — do not use as the Court's current position.",
}
_FUZZY_NOTE_BY_LANGUAGE = {
    "ru": "Возможно релевантно (найдено по смыслу описания, а не по точному совпадению нормы) — требует особо внимательной проверки юристом.",
    "kk": "Мүмкін өзекті (норманың дәл сәйкестігі бойынша емес, сипаттаманың мағынасы бойынша табылған) — заңгердің аса мұқият тексеруін қажет етеді.",
    "en": "Possibly relevant (found by meaning of the description, not an exact match on the provision) — requires especially careful review by a lawyer.",
}
def get_suggested_citations(
    law: str,
    article_number: str,
    max_results: int = 3,
    language: str = "ru",
    complaint_text: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Вызывается из pipeline генерации черновика (groq_generator.py / main.py).
    Возвращает НП, которые можно предложить как дополнительный аргумент —
    ВСЕГДА с explicit пометкой (см. formatted_note), что это подсказка,
    а не готовый вывод (риск из ТЗ раздела 7: "риск ложных совпадений").
    FR-3: language ("ru"/"kk"/"en") выбирает язык title/summary/note в ответе.
    FR-4: если точное совпадение по (law, article_number) ничего не находит
    и передан complaint_text — пробуем полнотекстовый (fuzzy) поиск как
    fallback, результаты помечаются "possibly_relevant" и отдельной пометкой
    с более низкой уверенностью (см. _FUZZY_NOTE_BY_LANGUAGE).
    """
    if language not in ("ru", "kk", "en"):
        language = "ru"
    results = find_resolutions_for_article(law, article_number)
    active_only = [r for r in results if r.get("status") == "active"]
    suggestions = []
    for r in active_only[:max_results]:
        loc = _localized_title_summary(r, language)
        suggestions.append({
            "number": r["number"],
            "date": r["date"],
            "title": loc["title"],
            "summary": loc["summary"],
            "source_url": r.get("source_url"),
            # FR-7: обязательная пометка при использовании в черновике
            "note": _NOTE_BY_LANGUAGE[language],
        })
    # FR-8: утратившие силу тоже возвращаем, но с явной меткой,
    # чтобы вызывающий код мог решить не использовать их как позицию суда
    superseded = [r for r in results if r.get("status") != "active"]
    for r in superseded[:max_results]:
        loc = _localized_title_summary(r, language)
        suggestions.append({
            "number": r["number"],
            "date": r["date"],
            "title": loc["title"],
            "summary": loc["summary"],
            "source_url": r.get("source_url"),
            "note": "⚠️ " + _SUPERSEDED_NOTE_BY_LANGUAGE[language].format(status=r.get("status")),
            "superseded": True,
        })
    # FR-4: точное совпадение по статье ничего не дало — пробуем fuzzy fallback
    if not suggestions and complaint_text:
        fuzzy = find_resolutions_fuzzy(complaint_text, max_results)
        for r in fuzzy:
            loc = _localized_title_summary(r, language)
            suggestions.append({
                "number": r["number"],
                "date": r["date"],
                "title": loc["title"],
                "summary": loc["summary"],
                "source_url": r.get("source_url"),
                "note": _FUZZY_NOTE_BY_LANGUAGE[language],
                "possibly_relevant": True,
            })
    return suggestions
@router.get("/suggested-citations")
def suggested_citations_endpoint(
    law: str,
    article_number: str,
    max_results: int = 3,
    language: str = "ru",
    complaint_text: Optional[str] = None,
):
    """Публичный (не защищённый кодом) эндпоинт — вызывается фронтендом при формировании черновика."""
    return {"suggestions": get_suggested_citations(law, article_number, max_results, language, complaint_text)}
@router.get("/last-updated")
def last_updated():
    """
    NFR-3: публичный (без кода администратора) эндпоинт с датой актуальности
    базы НП КС РК — вызывается фронтендом (chat.html) для отображения
    "База практики КС РК актуальна на: ...". В отличие от /corpus-status
    (админский, отдаёт больше деталей) этот эндпоинт отдаёт только то,
    что безопасно показывать публично.
    """
    rows = _query(
        f"""
        MATCH (r:NormativeResolution)
        WITH r, {_DATE_SORT_KEY_CYPHER} AS date_sort
        RETURN count(r) AS total,
               max(r.loaded_at) AS last_loaded_at,
               max(date_sort) AS latest_sort
        """
    )
    row = rows[0] if rows else {}
    last_loaded_at = row.get("last_loaded_at")
    # Отдаём только дату (без времени) в формате ДД.ММ.ГГГГ — этого достаточно
    # для NFR-3 ("дата актуальности базы") и не раскрывает лишних деталей.
    updated_display = None
    if last_loaded_at:
        try:
            dt = datetime.fromisoformat(last_loaded_at)
            updated_display = dt.strftime("%d.%m.%Y")
        except Exception:
            updated_display = None
    return {
        "total_resolutions": row.get("total", 0),
        "updated_at": updated_display,
        "latest_resolution_date": _sort_key_to_display_date(row.get("latest_sort")),
    }
# ---------------------------------------------------------------------------
# Просмотр корпуса (для админки — будущий интерфейс НП-каталога)
# ---------------------------------------------------------------------------
@router.get("/list")
def list_resolutions(limit: int = 200, x_admin_code: Optional[str] = Header(None)):
    _check_admin(x_admin_code)
    rows = _query(
        f"""
        MATCH (r:NormativeResolution)
        OPTIONAL MATCH (r)-[:REVIEWS]->(a:Article)
        WITH r, collect(DISTINCT a.law + ' ст.' + a.number) AS articles
        WITH r, articles, {_DATE_SORT_KEY_CYPHER} AS date_sort
        RETURN r.number AS number, r.date AS date, r.title AS title,
               r.status AS status, r.source_url AS source_url, articles
        ORDER BY date_sort DESC
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
