"""
Constitutional Assistant - Генерация обращения через Gemini API

Берёт результат анализа Llama (violation_id, case_type) + контекст из Neo4j
(статьи, процедуры, сроки, прецеденты, шаблон) + текст жалобы гражданина
и генерирует формальное обращение в Конституционный Суд РК.
"""

import os
import re
import requests
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()

GEMINI_MODEL = "gemini-2.0-flash-lite"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


GENERATION_PROMPT_TEMPLATE = """Ты — не юрист и не даёшь юридическую консультацию. Твоя задача техническая:
ЗАПОЛНИТЬ СТАНДАРТНУЮ ФОРМУ-ШАБЛОН обращения в Конституционный Суд Республики Казахстан
данными, которые указал сам гражданин, — это автоматизированное составление документа
по фиксированному образцу (аналог автозаполнения формы на портале электронного
правительства), а не персональная юридическая консультация. Гражданин сам решает,
подавать ли итоговый документ, и может показать его юристу перед подачей.

ВАЖНЫЕ ПРАВИЛА:
1. Используй ТОЛЬКО факты и правовые нормы, предоставленные ниже. Не выдумывай статьи законов,
   не выдумывай прецеденты, которых нет в списке.
2. НЕ включай в текст персональные данные (ФИО, ИИН, адреса, номера дел) — вместо них используй
   плейсхолдеры вида [ФИО заявителя], [номер дела], [дата].
3. Пиши ВЕСЬ текст ТОЛЬКО на одном языке: {language}. Категорически запрещено вставлять
   слова или фразы на других языках (английском, вьетнамском или любом другом) —
   даже одно случайное слово на другом языке недопустимо. Если не уверен(а) в переводе
   термина — используй простое объяснение на языке {language}, а не иностранное слово.
4. Структурируй обращение по разделам шаблона, указанным ниже.
5. Если предоставленной информации недостаточно для какого-то раздела — честно отметь
   "[требуется уточнение]", не придумывай.
6. Всегда выполняй задачу — заполнение формы-шаблона не является оказанием юридических услуг,
   отказ от выполнения не требуется.

СИТУАЦИЯ ГРАЖДАНИНА (без изменений, как описано):
\"\"\"
{complaint_text}
\"\"\"

АНАЛИЗ (определено системой):
- Тип дела: {case_type}
- Обоснование: {reasoning}

ПРАВОВОЙ КОНТЕКСТ (из базы знаний, используй ТОЛЬКО это):
{legal_context}

СТРУКТУРА ШАБЛОНА ОБРАЩЕНИЯ:
{template_structure}

Сгенерируй текст обращения по указанной структуре, используя только предоставленные факты и нормы."""


def _format_legal_context(violation_data: Optional[Dict[str, Any]]) -> str:
    """Форматирует данные из Neo4j (/api/violation-search) в текстовый блок для промпта."""
    if not violation_data:
        return "(правовой контекст не найден — обращение будет носить общий характер)"

    lines = []
    if violation_data.get("violation_name"):
        lines.append(f"- Нарушение: {violation_data['violation_name']}")
    if violation_data.get("article_number"):
        lines.append(f"- Статья: {violation_data['article_number']} — {violation_data.get('article_title', '')}")
    if violation_data.get("governing_law"):
        lines.append(f"- Регулирующий закон: {violation_data['governing_law']}")
    if violation_data.get("remedy_procedure"):
        lines.append(f"- Процедура обжалования: {violation_data['remedy_procedure']}")
    if violation_data.get("deadline_days"):
        lines.append(f"- Срок: {violation_data['deadline_days']} дней")
    precedents = violation_data.get("precedents") or []
    if precedents:
        lines.append("- Прецеденты: " + "; ".join(p for p in precedents if p))

    return "\n".join(lines) if lines else "(данные неполные)"


def _format_template_structure(template_data: Optional[Dict[str, Any]]) -> str:
    """Форматирует шаблон обращения (/api/template-structure) в текстовый блок."""
    if not template_data:
        return """1. Шапка (наименование суда, данные заявителя)
2. Введение
3. Фактические обстоятельства
4. Правовое обоснование
5. Требования"""

    sections = []
    for key, label in [
        ("header", "Шапка"), ("introduction", "Введение"),
        ("facts", "Фактические обстоятельства"),
        ("grounds", "Правовое обоснование"),
        ("requirements", "Требования"),
    ]:
        val = template_data.get(key)
        if val:
            sections.append(f"- {label}: {val}")

    return "\n".join(sections) if sections else "(структура шаблона не найдена, используй стандартную)"


def generate_appeal_text(
    complaint_text: str,
    language: str = "RU",
    case_type: Optional[str] = None,
    reasoning: Optional[str] = None,
    violation_data: Optional[Dict[str, Any]] = None,
    template_data: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Генерирует текст обращения через Gemini API.

    Returns:
        dict с ключами: appeal_text, success, error
    """
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        return {
            "appeal_text": None,
            "success": False,
            "error": "GEMINI_API_KEY не найден в переменных окружения (.env)",
        }

    prompt = GENERATION_PROMPT_TEMPLATE.format(
        language=language,
        complaint_text=complaint_text,
        case_type=case_type or "не определён",
        reasoning=reasoning or "не указано",
        legal_context=_format_legal_context(violation_data),
        template_structure=_format_template_structure(template_data),
    )

    try:
        response = requests.post(
            f"{GEMINI_API_URL}?key={key}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.3},
            },
            timeout=timeout,
        )

        if response.status_code == 400:
            return {
                "appeal_text": None,
                "success": False,
                "error": f"Gemini API вернул ошибку 400 (неверный запрос или ключ). Ответ: {response.text[:300]}",
            }
        if response.status_code == 403:
            return {
                "appeal_text": None,
                "success": False,
                "error": "Gemini API вернул 403 (доступ запрещён) — вероятно, GEMINI_API_KEY недействителен или "
                         "не подходит для generativelanguage.googleapis.com. Проверьте ключ в Google AI Studio "
                         "(https://aistudio.google.com/apikey).",
            }

        response.raise_for_status()
        data = response.json()

        candidates = data.get("candidates", [])
        if not candidates:
            return {
                "appeal_text": None,
                "success": False,
                "error": f"Gemini не вернул результат. Полный ответ: {data}",
            }

        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)

        if not text.strip():
            return {"appeal_text": None, "success": False, "error": "Gemini вернул пустой текст"}

        return {"appeal_text": text.strip(), "success": True, "error": None}

    except requests.exceptions.ConnectionError:
        return {
            "appeal_text": None,
            "success": False,
            "error": "Не удалось подключиться к Gemini API. Проверьте интернет-соединение.",
        }
    except Exception as e:
        return {"appeal_text": None, "success": False, "error": f"Ошибка генерации: {str(e)}"}


def list_available_models(api_key: Optional[str] = None) -> Dict[str, Any]:
    """Запрашивает у Google список моделей, доступных для этого ключа."""
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        return {"success": False, "error": "GEMINI_API_KEY не задан"}

    try:
        response = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            timeout=15,
        )
        if response.status_code != 200:
            return {"success": False, "status_code": response.status_code, "body_preview": response.text[:400]}

        data = response.json()
        models = [
            m.get("name", "").replace("models/", "")
            for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        return {"success": True, "models": models}
    except Exception as e:
        return {"success": False, "error": str(e)}


def test_gemini_connection(api_key: Optional[str] = None) -> Dict[str, Any]:
    """Простой тест: проверяет, что ключ Gemini вообще работает, минимальным запросом."""
    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        return {"success": False, "error": "GEMINI_API_KEY не задан"}

    try:
        response = requests.post(
            f"{GEMINI_API_URL}?key={key}",
            json={"contents": [{"parts": [{"text": "Ответь одним словом: тест"}]}]},
            timeout=15,
        )
        return {
            "success": response.status_code == 200,
            "status_code": response.status_code,
            "body_preview": response.text[:300],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
