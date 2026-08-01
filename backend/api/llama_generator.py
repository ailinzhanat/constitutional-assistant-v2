"""
Constitutional Assistant - Генерация обращения через локальную Llama (Ollama)

Временная замена gemini_generator.py, пока не решён вопрос
доступа к Gemini API (региональное ограничение бесплатного тарифа).
Использует ту же логику форматирования контекста и тот же промпт-подход.
"""

import requests
from typing import Optional, Dict, Any

from gemini_generator import _format_legal_context, _format_template_structure, GENERATION_PROMPT_TEMPLATE

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "llama3.1:8b"


def generate_appeal_text(
    complaint_text: str,
    language: str = "RU",
    case_type: Optional[str] = None,
    reasoning: Optional[str] = None,
    violation_data: Optional[Dict[str, Any]] = None,
    template_data: Optional[Dict[str, Any]] = None,
    timeout: int = 180,
) -> Dict[str, Any]:
    """
    Генерирует текст обращения через локальную Llama (Ollama).
    Интерфейс идентичен gemini_generator.generate_appeal_text для лёгкой замены.

    Returns:
        dict с ключами: appeal_text, success, error
    """
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
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.2,  # ниже температура = меньше "фантазии" и смешения языков
                    "top_p": 0.85,
                },
            },
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
        text = result.get("response", "").strip()

        if not text:
            return {"appeal_text": None, "success": False, "error": "Llama вернула пустой текст"}

        return {"appeal_text": text, "success": True, "error": None}

    except requests.exceptions.ConnectionError:
        return {
            "appeal_text": None,
            "success": False,
            "error": "Не удалось подключиться к Ollama. Убедитесь, что Ollama запущена и модель llama3.2 установлена.",
        }
    except Exception as e:
        return {"appeal_text": None, "success": False, "error": f"Ошибка генерации: {str(e)}"}
