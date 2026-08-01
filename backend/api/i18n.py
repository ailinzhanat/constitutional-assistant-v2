"""
Constitutional Assistant - Модуль многоязычности (KZ/RU/EN)
Автоопределение языка + словарь переводов системных сообщений
"""

import re
from typing import Literal

Language = Literal["KZ", "RU", "EN"]

# ============================================================================
# АВТООПРЕДЕЛЕНИЕ ЯЗЫКА
# ============================================================================

# Уникальные казахские буквы, которых нет в русском алфавите
KAZAKH_UNIQUE_CHARS = set("әғқңөұүһі" + "ӘҒҚҢӨҰҮҺІ")

# Кириллические буквы (русский + казахский используют кириллицу)
CYRILLIC_PATTERN = re.compile(r"[а-яА-ЯёЁ]")

# Латинские буквы (английский)
LATIN_PATTERN = re.compile(r"[a-zA-Z]")


def detect_language(text: str) -> Language:
    """
    Определяет язык текста: KZ, RU или EN.
    
    Логика:
    1. Если есть уникальные казахские буквы (ә, ғ, қ, ң, ө, ұ, ү, һ, і) → KZ
    2. Если преобладает кириллица (без казахских букв) → RU
    3. Если преобладает латиница → EN
    4. По умолчанию → RU
    """
    
    if not text or not text.strip():
        return "RU"
    
    # Считаем казахские уникальные символы
    kazakh_count = sum(1 for ch in text if ch in KAZAKH_UNIQUE_CHARS)
    
    if kazakh_count > 0:
        return "KZ"
    
    cyrillic_count = len(CYRILLIC_PATTERN.findall(text))
    latin_count = len(LATIN_PATTERN.findall(text))
    
    if cyrillic_count == 0 and latin_count == 0:
        return "RU"  # по умолчанию, если нет букв (только цифры/символы)
    
    if latin_count > cyrillic_count:
        return "EN"
    
    return "RU"


def normalize_language(lang: str) -> Language:
    """Приводит любой формат языка (ru, russian, RU, ...) к стандарту KZ/RU/EN."""
    
    if not lang:
        return "RU"
    
    lang_lower = lang.strip().lower()
    
    kz_aliases = {"kz", "kaz", "kazakh", "қазақша", "казахский"}
    ru_aliases = {"ru", "rus", "russian", "русский"}
    en_aliases = {"en", "eng", "english", "английский"}
    
    if lang_lower in kz_aliases:
        return "KZ"
    if lang_lower in en_aliases:
        return "EN"
    if lang_lower in ru_aliases:
        return "RU"
    
    return "RU"


# ============================================================================
# СЛОВАРЬ ПЕРЕВОДОВ СИСТЕМНЫХ СООБЩЕНИЙ
# ============================================================================

TRANSLATIONS = {
    "welcome": {
        "KZ": "Конституциялық көмекшіге қош келдіңіз!",
        "RU": "Добро пожаловать в Конституционный ассистент!",
        "EN": "Welcome to the Constitutional Assistant!",
    },
    "upload_success": {
        "KZ": "Құжат сәтті жүктелді және танылды",
        "RU": "Документ успешно загружен и распознан",
        "EN": "Document uploaded and recognized successfully",
    },
    "upload_error": {
        "KZ": "Құжатты өңдеу кезінде қате орын алды",
        "RU": "Ошибка при обработке документа",
        "EN": "Error processing the document",
    },
    "unsupported_format": {
        "KZ": "Файл форматы қолдау таппайды",
        "RU": "Формат файла не поддерживается",
        "EN": "File format is not supported",
    },
    "violation_not_found": {
        "KZ": "Бұзушылық табылмады",
        "RU": "Нарушение не найдено",
        "EN": "Violation not found",
    },
    "consent_required": {
        "KZ": "Жалғастыру үшін дербес деректерді өңдеуге келісім қажет",
        "RU": "Для продолжения необходимо согласие на обработку персональных данных",
        "EN": "Consent to personal data processing is required to continue",
    },
    "consent_given": {
        "KZ": "Келісім берілді",
        "RU": "Согласие получено",
        "EN": "Consent recorded",
    },
    "jurisdiction_check_failed": {
        "KZ": "Сіздің өтінішіңіз Конституциялық Соттың құзыретіне жатпайды",
        "RU": "Ваше обращение не относится к юрисдикции Конституционного суда",
        "EN": "Your appeal does not fall within the jurisdiction of the Constitutional Court",
    },
    "jurisdiction_redirect_detail": {
        "KZ": "Конституциялық Сот тек нормативтік құқықтық актінің Конституцияға сәйкессіздігін қарайды. Егер сіз сот үкімімен/шешімімен келіспесеңіз, бұл — апелляциялық немесе кассациялық сот саты арқылы шешілетін мәселе, Конституциялық Сот арқылы емес.",
        "RU": "Конституционный Суд рассматривает только вопросы соответствия нормативного акта Конституции. Если вы не согласны с приговором или решением суда по существу дела — это вопрос апелляционной или кассационной инстанции, а не Конституционного Суда.",
        "EN": "The Constitutional Court only reviews whether a normative act complies with the Constitution. If you disagree with a court verdict or decision on its merits, that is a matter for an appellate or cassation court, not the Constitutional Court.",
    },
    "appeal_generated": {
        "KZ": "Өтініш сәтті жасалды",
        "RU": "Обращение успешно сформировано",
        "EN": "Appeal successfully generated",
    },
}


def t(key: str, language: str = "RU") -> str:
    """
    Возвращает перевод сообщения по ключу для указанного языка.
    Пример: t("upload_success", "KZ") → "Құжат сәтті жүктелді және танылды"
    """
    
    lang = normalize_language(language)
    entry = TRANSLATIONS.get(key)
    
    if not entry:
        return key  # если перевода нет — возвращаем сам ключ (fallback)
    
    return entry.get(lang, entry.get("RU", key))


# ============================================================================
# СПИСОК ПОДДЕРЖИВАЕМЫХ ЯЗЫКОВ (для frontend / переключателя)
# ============================================================================

SUPPORTED_LANGUAGES = [
    {"code": "KZ", "label": "Қазақша", "flag": "🇰🇿"},
    {"code": "RU", "label": "Русский", "flag": "🇷🇺"},
    {"code": "EN", "label": "English", "flag": "🇬🇧"},
]
