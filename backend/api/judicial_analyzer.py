"""
Constitutional Assistant - Judicial Analyst (внутренний модуль для судей)

Реализует функции из proposal (раздел 2.3, "Judicial Analyst"):
1. Составление нейтральной "Справки" по делу (факты, правовые аргументы, конституционные вопросы)
2. Поиск прецедентов и связанного законодательства в базе знаний
3. Human-in-the-loop: ИИ только помечает вопросы для проверки судьёй, не принимает решений
"""

import os
import requests
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

from llama_analyzer import _extract_json

load_dotenv()

# Groq (облачный) для деплоя. Для локального режима можно вернуть Ollama.
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# ВАЖНО: llama-3.3-70b-versatile объявлена Groq устаревшей (deprecation
# announced 17 июня 2026) и была снята с обслуживания, из-за чего запросы
# возвращали 404. openai/gpt-oss-120b — официально рекомендованная Groq
# замена (см. https://console.groq.com/docs/deprecations).
GROQ_MODEL = "openai/gpt-oss-120b"


SPRAVKA_PROMPT_TEMPLATE = """Ты — технический помощник судьи Конституционного Суда Республики Казахстан.
Ты НЕ принимаешь решений и НЕ даёшь юридических оценок от своего имени — ты составляешь
нейтральную техническую справку (Spravka), которая помогает судье быстрее ознакомиться с делом.
Судья сам, единолично, принимает все решения по существу дела — это "human-in-the-loop" принцип,
который ты обязан соблюдать.

Верни ТОЛЬКО JSON (без markdown, без пояснений) со следующими полями:

{{
  "key_facts": "нейтральное изложение фактических обстоятельств дела, без оценок, 3-5 предложений",
  "legal_arguments": "аргументы сторон, как они изложены в деле, без собственной оценки их обоснованности",
  "constitutional_issues": "какие конституционные вопросы/статьи затрагиваются в деле, на основе текста",
  "procedural_flags": ["список", "конкретных", "процедурных вопросов для проверки судьёй, например: пропущен срок подачи, отсутствует подпись представителя, и т.д. Пустой список [] если явных проблем не видно."]
}}

Правила:
- Пиши на языке: {language}
- НЕ выдумывай факты, которых нет в тексте дела
- НЕ давай собственного мнения о том, кто прав — только нейтральное изложение
- Каждый пункт procedural_flags должен быть основан на конкретном месте в тексте, не на догадках

Текст дела:
\"\"\"
{case_text}
\"\"\"

Верни только JSON."""


def generate_case_summary(case_text: str, language: str = "RU", timeout: int = 120) -> Dict[str, Any]:
    """
    Составляет нейтральную справку (Spravka) по материалам дела.

    Returns:
        dict с ключами: key_facts, legal_arguments, constitutional_issues,
        procedural_flags (list), success, error
    """
    prompt = SPRAVKA_PROMPT_TEMPLATE.format(language=language, case_text=case_text[:8000])

    key = os.getenv("GROQ_API_KEY")
    if not key:
        return {"key_facts": None, "legal_arguments": None, "constitutional_issues": None,
                "procedural_flags": [], "success": False, "error": "GROQ_API_KEY не найден в .env"}

    try:
        response = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
        raw_output = result["choices"][0]["message"]["content"]

        parsed = _extract_json(raw_output)

        if parsed is None:
            return {
                "key_facts": None,
                "legal_arguments": None,
                "constitutional_issues": None,
                "procedural_flags": [],
                "success": False,
                "error": "Не удалось разобрать ответ модели как JSON",
            }

        flags = parsed.get("procedural_flags", [])
        if not isinstance(flags, list):
            flags = [str(flags)] if flags else []

        constitutional_issues = parsed.get("constitutional_issues")
        if isinstance(constitutional_issues, list):
            constitutional_issues = "; ".join(str(i) for i in constitutional_issues)

        return {
            "key_facts": parsed.get("key_facts"),
            "legal_arguments": parsed.get("legal_arguments"),
            "constitutional_issues": constitutional_issues,
            "procedural_flags": flags,
            "success": True,
            "error": None,
        }

    except requests.exceptions.ConnectionError:
        return {
            "key_facts": None, "legal_arguments": None, "constitutional_issues": None,
            "procedural_flags": [], "success": False,
            "error": "Не удалось подключиться к Groq API. Проверьте интернет.",
        }
    except Exception as e:
        return {
            "key_facts": None, "legal_arguments": None, "constitutional_issues": None,
            "procedural_flags": [], "success": False,
            "error": f"Ошибка составления справки: {str(e)}",
        }


def search_precedents(driver, keyword: str, case_type: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Поиск прецедентов (Decision) и связанных статей в Neo4j по ключевому слову.
    driver — уже открытый neo4j driver (переиспользуем подключение из main.py).
    """
    query = """
    MATCH (dec:Decision)
    WHERE toLower(dec.summary) CONTAINS toLower($keyword)
       OR toLower(coalesce(dec.case_type, '')) CONTAINS toLower($keyword)
    OPTIONAL MATCH (dec)-[:CITES]->(article:Article)
    RETURN
      dec.court AS court,
      dec.date AS date,
      dec.summary AS summary,
      dec.case_type AS case_type,
      dec.precedent_strength AS precedent_strength,
      collect(DISTINCT article.number) AS cited_articles
    LIMIT $limit
    """
    with driver.session() as session:
        results = session.run(query, keyword=keyword, limit=limit)
        return [dict(record) for record in results]
