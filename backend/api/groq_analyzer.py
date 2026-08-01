"""
Constitutional Assistant - Анализ жалобы через Groq API (облачная Llama 70B)

Замена локального llama_analyzer.py для облачного деплоя.
Использует тот же промпт и известные нарушения, но через Groq вместо Ollama.
"""

import os
import requests
from typing import Optional, Dict, Any
from dotenv import load_dotenv

from llama_analyzer import _extract_json, KNOWN_VIOLATIONS, ANALYSIS_PROMPT_TEMPLATE, _build_prompt

load_dotenv()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


def analyze_complaint(
    complaint_text: str,
    document_text: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: int = 45,
) -> Dict[str, Any]:
    """
    Анализирует жалобу через Groq.
    Интерфейс идентичен llama_analyzer.analyze_complaint.

    Returns:
        dict: within_jurisdiction, violation_id, case_type, reasoning, success, error
    """
    key = api_key or os.getenv("GROQ_API_KEY")
    if not key:
        return {"within_jurisdiction": None, "violation_id": None, "case_type": None,
                "reasoning": None, "success": False, "error": "GROQ_API_KEY не найден в .env"}

    prompt = _build_prompt(complaint_text, document_text)

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
                "response_format": {"type": "json_object"},  # Groq умеет гарантировать JSON
            },
            timeout=timeout,
        )

        if response.status_code == 401:
            return {"within_jurisdiction": None, "violation_id": None, "case_type": None,
                    "reasoning": None, "success": False, "error": "Groq: неверный API-ключ (401)"}
        if response.status_code == 429:
            return {"within_jurisdiction": None, "violation_id": None, "case_type": None,
                    "reasoning": None, "success": False,
                    "error": "Groq: превышена квота (429). Попробуйте позже."}

        response.raise_for_status()
        data = response.json()
        raw_output = data["choices"][0]["message"]["content"]

        parsed = _extract_json(raw_output)
        if parsed is None:
            return {"within_jurisdiction": None, "violation_id": None, "case_type": None,
                    "reasoning": None, "success": False,
                    "error": "Не удалось разобрать ответ Groq как JSON", "raw_response": raw_output}

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
        return {"within_jurisdiction": None, "violation_id": None, "case_type": None,
                "reasoning": None, "success": False,
                "error": "Не удалось подключиться к Groq API. Проверьте интернет."}
    except Exception as e:
        return {"within_jurisdiction": None, "violation_id": None, "case_type": None,
                "reasoning": None, "success": False, "error": f"Ошибка анализа: {str(e)}"}
