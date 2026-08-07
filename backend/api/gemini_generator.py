"""
Constitutional Assistant - Генерация обращения через Gemini API

Берёт результат анализа (violation_id, case_type) + контекст из Neo4j
+ текст жалобы гражданина и генерирует формальное обращение в КС РК
по официальному образцу Конституционного Суда Республики Казахстан.
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
данными, которые указал сам гражданин. Это автоматизированное составление документа
по фиксированному официальному образцу КС РК — аналог автозаполнения формы на портале
электронного правительства, а не персональная юридическая консультация.
Гражданин сам решает, подавать ли итоговый документ, и может показать его юристу перед подачей.

ВАЖНЫЕ ПРАВИЛА:
1. Используй ТОЛЬКО факты и правовые нормы, предоставленные ниже. Не выдумывай статьи законов.
2. НЕ включай в текст персональные данные — вместо них используй плейсхолдеры:
   [ФИО заявителя], [место жительства], [ИИН], [номер телефона], [email],
   [ФИО представителя], [адрес представителя], [номер дела], [дата судебного акта].
3. Пиши ВЕСЬ текст ТОЛЬКО на языке: {language}. Никаких слов на других языках.
4. Если информации недостаточно для раздела — пиши [требуется уточнение], не придумывай.
5. Строго следуй структуре официального образца ниже.

СИТУАЦИЯ ГРАЖДАНИНА:
\"\"\"
{complaint_text}
\"\"\"

АНАЛИЗ (определено системой):
- Тип дела: {case_type}
- Обоснование: {reasoning}

ПРАВОВОЙ КОНТЕКСТ (из базы знаний, используй ТОЛЬКО это):
{legal_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
СГЕНЕРИРУЙ ОБРАЩЕНИЕ СТРОГО ПО СЛЕДУЮЩЕМУ ОФИЦИАЛЬНОМУ ОБРАЗЦУ КС РК:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Конституционный Суд
Республики Казахстан

Данные обращающегося лица:
Ф.И.О.: [ФИО заявителя]
Место жительства: [место жительства]
ИИН: [ИИН]
Мобильный телефон (при наличии): [номер телефона]
Электронная почта (при наличии): [email]

Данные представителя (при наличии):
Ф.И.О.: [ФИО представителя]
Адрес: [адрес представителя]
Мобильный телефон (при наличии): [номер телефона представителя]
Электронная почта (при наличии): [email представителя]

ОБРАЩЕНИЕ
о проверке на соответствие Конституции нормативного правового акта,
непосредственно затрагивающего права и свободы

I. Конкретная ситуация, являющаяся основанием для обращения:

[Опиши конкретную ситуацию из жалобы гражданина: какое дело рассматривалось,
какой суд, какое решение вынесено, когда вступило в законную силу, какая норма применена.
Только факты, никаких оценок.]

II. Сущность обращения, позиция лица, подающего обращение, и его правовое обоснование:

Нарушение моих прав и свобод, закреплённых Конституцией Республики Казахстан, состоит в следующем:
[Опиши в чём конкретно состоит нарушение прав заявителя.]

Согласно статье [номер] Конституции Республики Казахстан каждый имеет право на [содержание права].

Из этого следует, что [вывод о том, как конституционное право должно быть реализовано].

Вместе с тем, нормативный правовой акт [наименование НПА, номер, дата принятия, источник опубликования],
отдельные его положения [статья, часть, пункт, подпункт] ущемляют моё конституционное право на
[название права], поскольку [позиция заявителя и правовое обоснование несоответствия Конституции].

На основании изложенного, руководствуясь пунктом 3 статьи 73 Конституции Республики Казахстан,
подпунктом 3) пункта 4 статьи 23 и статьёй 45 Конституционного закона Республики Казахстан
«О Конституционном Суде Республики Казахстан»

ПРОШУ:

Рассмотреть на соответствие Конституции Республики Казахстан [статья, часть, пункт, подпункт,
наименование закона или иного нормативного правового акта].

Свои интересы при рассмотрении данного обращения в Конституционном Суде буду представлять
{representative_line}.

Перечень прилагаемых документов:
1) копия текста закона и иного НПА, указанного в обращении;
2) судебный акт, подтверждающий применение указанного в обращении закона и иного НПА
   в конкретном деле (с момента вступления судебного акта в законную силу не должно
   пройти более одного года);
3) доверенность или иной документ о полномочиях представителя (если обращение подаётся
   представителем);
4) документы, подтверждающие полномочия представителя:
   - адвокат представляет удостоверение адвоката, письменное уведомление о защите
     (представительстве);
   - юридический консультант представляет документ, подтверждающий членство в палате
     юридических консультантов;
   - законный представитель представляет документы, удостоверяющие его полномочия;
   - уполномоченное лицо организации представляет копии устава, положения, свидетельства
     или справки о государственной регистрации (перерегистрации);
5) иные материалы, подтверждающие позицию лица, обращающегося в Конституционный Суд.

Ф.И.О.: [ФИО заявителя]          Подпись: _______________

Дата: [дата подачи обращения]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Заполни квадратные скобки на основе предоставленных данных. Где данных нет — оставь плейсхолдер.
Не добавляй никаких пояснений от себя после текста обращения."""


def _format_legal_context(violation_data: Optional[Dict[str, Any]]) -> str:
    """Форматирует данные из Neo4j в текстовый блок для промпта."""
    if not violation_data:
        return "(правовой контекст не найден — используй только данные из жалобы гражданина)"

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
    """Не используется — структура теперь жёстко задана в промпте по официальному образцу КС РК."""
    return ""


def generate_appeal_text(
    complaint_text: str,
    language: str = "RU",
    case_type: Optional[str] = None,
    reasoning: Optional[str] = None,
    violation_data: Optional[Dict[str, Any]] = None,
    template_data: Optional[Dict[str, Any]] = None,
    is_representative: bool = False,
    api_key: Optional[str] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """
    Генерирует текст обращения через Gemini API по официальному образцу КС РК.

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

    representative_line = (
        "через представителя [ФИО представителя]"
        if is_representative
        else "лично"
    )

    prompt = GENERATION_PROMPT_TEMPLATE.format(
        language=language,
        complaint_text=complaint_text,
        case_type=case_type or "не определён",
        reasoning=reasoning or "не указано",
        legal_context=_format_legal_context(violation_data),
        representative_line=representative_line,
    )

    try:
        response = requests.post(
            f"{GEMINI_API_URL}?key={key}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2},
            },
            timeout=timeout,
        )

        if response.status_code == 400:
            return {
                "appeal_text": None,
                "success": False,
                "error": f"Gemini API вернул ошибку 400. Ответ: {response.text[:300]}",
            }
        if response.status_code == 403:
            return {
                "appeal_text": None,
                "success": False,
                "error": "Gemini API вернул 403 — проверьте GEMINI_API_KEY в Google AI Studio.",
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
