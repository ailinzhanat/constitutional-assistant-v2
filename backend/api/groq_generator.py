"""
Constitutional Assistant - Генерация обращения через Groq API (облачная Llama 70B)
"""

import os
import requests
from typing import Optional, Dict, Any
from dotenv import load_dotenv

from gemini_generator import _format_legal_context, _format_template_structure, GENERATION_PROMPT_TEMPLATE

load_dotenv()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


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
    Генерирует текст обращения через Groq по официальному образцу КС РК.

    Returns:
        dict с ключами: appeal_text, success, error
    """
    key = api_key or os.getenv("GROQ_API_KEY")
    if not key:
        return {"appeal_text": None, "success": False, "error": "GROQ_API_KEY не найден в .env"}

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
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": 2000,
            },
            timeout=timeout,
        )

        if response.status_code == 401:
            return {"appeal_text": None, "success": False, "error": "Groq: неверный API-ключ (401)"}
        if response.status_code == 429:
            return {"appeal_text": None, "success": False,
                    "error": "Groq: превышена квота (429). Попробуйте позже."}

        response.raise_for_status()
        data = response.json()

        text = data["choices"][0]["message"]["content"].strip()
        if not text:
            return {"appeal_text": None, "success": False, "error": "Groq вернул пустой текст"}

        return {"appeal_text": text, "success": True, "error": None}

    except requests.exceptions.ConnectionError:
        return {"appeal_text": None, "success": False,
                "error": "Не удалось подключиться к Groq API."}
    except Exception as e:
        return {"appeal_text": None, "success": False, "error": f"Ошибка генерации: {str(e)}"}
