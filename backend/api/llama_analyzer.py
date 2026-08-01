"""
Constitutional Assistant - Анализ жалобы через локальную Llama (Ollama)

Определяет:
- относится ли жалоба к юрисдикции Конституционного суда
- какое из известных нарушений (violation_id) соответствует жалобе
- краткое обоснование на языке жалобы

Требует запущенный Ollama на http://localhost:11434 с моделью llama3.2
(установка: см. https://ollama.com, затем `ollama pull llama3.2`)
"""

import json
import re
import requests
from typing import Optional, Dict, Any

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "llama3.2"

# Известные нарушения из базы знаний Neo4j (расширяется по мере роста graph)
KNOWN_VIOLATIONS = {
    "viol_lack_transparency": "Отсутствие прозрачности от управляющего (банкротство)",
    "viol_exceeded_deadline": "Превышение максимального срока банкротства",
    "viol_no_public_hearing": "Отсутствие открытого судебного заседания",
}

ANALYSIS_PROMPT_TEMPLATE = """Ты — юридический ассистент, который анализирует обращения граждан Казахстана в Конституционный Суд.

Твоя задача — проанализировать текст жалобы и вернуть ТОЛЬКО JSON (без markdown, без пояснений) со следующими полями:

{{
  "within_jurisdiction": true/false,
  "violation_id": "один из [{violation_ids}] или null, если не подходит ни один",
  "case_type": "bankruptcy" / "employment" / "property" / "other",
  "reasoning": "твой СОБСТВЕННЫЙ анализ в 1-2 предложениях: почему ты выбрал именно такое решение (НЕ пересказывай и не повторяй текст жалобы дословно)"
}}

Правила:
- within_jurisdiction = false, если жалоба на самом деле пытается обжаловать решение суда общей юрисдикции по существу дела (это не относится к Конституционному суду)
- within_jurisdiction = true, если жалоба указывает на несоответствие нормативного акта Конституции, или на процедурное нарушение конституционных прав
- Выбирай violation_id только если он явно совпадает по смыслу с одним из известных нарушений
- Если сомневаешься — используй null
- В поле "reasoning" объясняй СВОЁ решение (например: "Жалоба не ссылается на конкретную статью Конституции, поэтому юрисдикция под вопросом"), а не повторяй слова из жалобы

Известные нарушения:
{violations_list}

Текст жалобы гражданина:
\"\"\"
{complaint_text}
\"\"\"

{document_section}

Верни только JSON, ничего больше."""


def _build_prompt(complaint_text: str, document_text: Optional[str] = None) -> str:
    violations_list = "\n".join(f"- {vid}: {desc}" for vid, desc in KNOWN_VIOLATIONS.items())
    violation_ids = ", ".join(KNOWN_VIOLATIONS.keys())

    document_section = ""
    if document_text:
        snippet = document_text[:3000]
        document_section = f'Текст приложенного судебного акта:\n"""\n{snippet}\n"""'

    return ANALYSIS_PROMPT_TEMPLATE.format(
        violation_ids=violation_ids,
        violations_list=violations_list,
        complaint_text=complaint_text,
        document_section=document_section,
    )


def _extract_json(raw_text: str) -> Optional[dict]:
    """
    Llama иногда оборачивает JSON в ```json ... ``` или добавляет текст вокруг.
    Эта функция вытаскивает первый валидный JSON-объект из ответа.
    """
    # Убираем markdown code fences, если есть
    cleaned = re.sub(r"```(?:json)?", "", raw_text).strip()

    # Пытаемся распарсить напрямую
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Ищем первый {...} блок в тексте
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    return None


def analyze_complaint(complaint_text: str, document_text: Optional[str] = None, timeout: int = 60) -> Dict[str, Any]:
    """
    Анализирует жалобу через локальную Llama.

    Returns:
        dict с ключами: within_jurisdiction, violation_id, case_type, reasoning, success, error
    """
    prompt = _build_prompt(complaint_text, document_text)

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "format": "json",  # просим Ollama вернуть валидный JSON
            },
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
        raw_output = result.get("response", "")

        parsed = _extract_json(raw_output)

        if parsed is None:
            return {
                "within_jurisdiction": None,
                "violation_id": None,
                "case_type": None,
                "reasoning": None,
                "success": False,
                "error": "Не удалось разобрать ответ Llama как JSON",
                "raw_response": raw_output,
            }

        # Валидация violation_id против известного списка
        vid = parsed.get("violation_id")
        if vid not in KNOWN_VIOLATIONS:
            vid = None

        return {
            "within_jurisdiction": parsed.get("within_jurisdiction"),
            "violation_id": vid,
            "case_type": parsed.get("case_type"),
            "reasoning": parsed.get("reasoning"),
            "success": True,
            "error": None,
        }

    except requests.exceptions.ConnectionError:
        return {
            "within_jurisdiction": None,
            "violation_id": None,
            "case_type": None,
            "reasoning": None,
            "success": False,
            "error": "Не удалось подключиться к Ollama. Убедитесь, что Ollama запущена (ollama serve) и модель llama3.2 установлена.",
        }
    except Exception as e:
        return {
            "within_jurisdiction": None,
            "violation_id": None,
            "case_type": None,
            "reasoning": None,
            "success": False,
            "error": f"Ошибка анализа: {str(e)}",
        }


def is_ollama_available() -> bool:
    """Проверяет, доступна ли Ollama по адресу localhost:11434."""
    try:
        r = requests.get("http://localhost:11434", timeout=3)
        return r.status_code == 200
    except Exception:
        return False
