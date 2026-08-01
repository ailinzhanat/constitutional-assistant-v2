"""
Constitutional Assistant - Анализ жалобы через локальную Llama (Ollama)

Определяет:
- относится ли жалоба к юрисдикции Конституционного суда (на основе РЕАЛЬНЫХ
  оснований возврата обращений по ст.47 Конституционного закона)
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
    "viol_dismissal_health_grounds": "Неопределённость нормы об учёте состояния здоровья при увольнении сотрудника правоохранительной службы (п.3 ст.80 Закона РК «О правоохранительной службе»)",
}

# Реальные основания ВОЗВРАТА обращений по п.2 ст.47 Конституционного закона
# «О Конституционном Суде Республики Казахстан» — составлено на основе
# фактических ответов Аппарата КС РК (обезличенная выборка, июль 2026)
RETURN_GROUNDS_REFERENCE = """
ОСНОВАНИЯ ДЛЯ ВОЗВРАТА ОБРАЩЕНИЯ (п.2 ст.47 Конституционного закона) — проверяй жалобу на КАЖДОЕ из них:

Подпункт 1) — вопрос НЕ относится к компетенции КС (ст.73 Конституции). Обращение
НЕ ПРИНИМАЕТСЯ, если гражданин по сути просит:
  - пересмотреть судебный акт по существу дела (это апелляция/кассация, не КС)
  - дать разъяснение нормы обычного закона (не вопрос соответствия Конституции)
  - устранить пробел в законодательстве (это не компетенция КС)
  - оценить фактические обстоятельства дела (доказательства, содержание под стражей,
    законность действий судебного исполнителя/госоргана) — это компетенция судов/прокуратуры
  - оценить практику применения (или неприменения) судами норм законодательства при
    рассмотрении конкретных дел — это тоже не вопрос конституционности нормы
  - применить закон об амнистии к конкретному случаю (не вопрос конституционности)
  - защитить права человека в общем смысле — это вопрос Уполномоченного по правам человека

Подпункт 2) — обращение подано представителем БЕЗ надлежащего оформления:
  - нет доверенности от самого гражданина на подачу обращения представителем
  - представитель не является законным представителем, адвокатом, либо юридическим
    консультантом — членом палаты юридических консультантов (п.4 ст.44)

Подпункт 3) — обращение не соответствует иным требованиям закона:
  - НЕНАДЛЕЖАЩИЙ СУБЪЕКТ обращения — например, юридическое лицо (ТОО, компания) обращается
    от СВОЕГО имени; по ст.73 Конституции субъектами обращения являются граждане, а не
    юридические лица напрямую
  - повторное обращение с тем же неустранённым недостатком (уже возвращали по этой же
    причине, а её не исправили)

Подпункт 5) — обращение НЕ соответствует условиям допустимости по ст.45:
  - оспариваемый акт НЕ был применён судом именно в деле САМОГО заявителя
    (например, акт применён в отношении ИНОГО лица, а не самого обратившегося)
  - по делу ещё НЕТ вступившего в законную силу судебного акта
  - с момента вступления судебного акта в законную силу прошло БОЛЕЕ ОДНОГО ГОДА

Обращение ДОПУСТИМО (within_jurisdiction = true), только если гражданин чётко просит
проверить конкретную норму закона/НПА на соответствие Конституции, эта норма была
применена судом именно в его деле, судебный акт вступил в силу не более года назад,
и представительство (если есть) оформлено надлежащим образом.
"""

ANALYSIS_PROMPT_TEMPLATE = """Ты — юридический ассистент, который анализирует обращения граждан Казахстана в Конституционный Суд.

Твоя задача — проанализировать текст жалобы и вернуть ТОЛЬКО JSON (без markdown, без пояснений) со следующими полями:

{{
  "within_jurisdiction": true/false,
  "return_ground": "если within_jurisdiction=false — номер подпункта п.2 ст.47, который подходит (1, 2, 3 или 5), иначе null",
  "violation_id": "один из [{violation_ids}] или null, если не подходит ни один",
  "case_type": "bankruptcy" / "employment" / "property" / "other",
  "reasoning": "твой СОБСТВЕННЫЙ анализ в 1-2 предложениях: почему ты выбрал именно такое решение (НЕ пересказывай и не повторяй текст жалобы дословно)"
}}

{return_grounds_reference}

Известные нарушения (используй для поля violation_id, только если явно совпадает по смыслу):
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
        return_grounds_reference=RETURN_GROUNDS_REFERENCE,
        complaint_text=complaint_text,
        document_section=document_section,
    )


def _extract_json(raw_text: str) -> Optional[dict]:
    """
    Llama иногда оборачивает JSON в ```json ... ``` или добавляет текст вокруг.
    Эта функция вытаскивает первый валидный JSON-объект из ответа.
    """
    cleaned = re.sub(r"```(?:json)?", "", raw_text).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

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
        dict с ключами: within_jurisdiction, return_ground, violation_id, case_type,
        reasoning, success, error
    """
    prompt = _build_prompt(complaint_text, document_text)

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "format": "json",
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
                "return_ground": None,
                "violation_id": None,
                "case_type": None,
                "reasoning": None,
                "success": False,
                "error": "Не удалось разобрать ответ Llama как JSON",
                "raw_response": raw_output,
            }

        vid = parsed.get("violation_id")
        if vid not in KNOWN_VIOLATIONS:
            vid = None

        return {
            "within_jurisdiction": parsed.get("within_jurisdiction"),
            "return_ground": parsed.get("return_ground"),
            "violation_id": vid,
            "case_type": parsed.get("case_type"),
            "reasoning": parsed.get("reasoning"),
            "success": True,
            "error": None,
        }

    except requests.exceptions.ConnectionError:
        return {
            "within_jurisdiction": None,
            "return_ground": None,
            "violation_id": None,
            "case_type": None,
            "reasoning": None,
            "success": False,
            "error": "Не удалось подключиться к Ollama. Убедитесь, что Ollama запущена (ollama serve) и модель llama3.2 установлена.",
        }
    except Exception as e:
        return {
            "within_jurisdiction": None,
            "return_ground": None,
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
