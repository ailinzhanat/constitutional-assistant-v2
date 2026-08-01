"""
Constitutional Assistant - Модуль согласия на обработку персональных данных
По Закону РК №94-V "О персональных данных и их защите"

Функции:
- Текст согласия на 3 языках (KZ/RU/EN)
- Запись факта согласия (in-memory хранилище — для продакшена заменить на БД)
- Генерация скачиваемого подтверждения согласия (текстовый файл)
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Dict

# ============================================================================
# ТЕКСТ СОГЛАСИЯ НА 3 ЯЗЫКАХ
# ============================================================================

CONSENT_TEXT = {
    "RU": """СОГЛАСИЕ НА ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ

В соответствии с Законом Республики Казахстан от 21 мая 2013 года № 94-V
«О персональных данных и их защите», я даю согласие на сбор и обработку
моих персональных данных системой «Constitutional Assistant» исключительно
в целях подготовки и подачи обращения в Конституционный Суд Республики
Казахстан.

Я понимаю, что:
1. Мои данные будут использованы только для указанной цели и не будут
   переданы третьим лицам без моего согласия, за исключением случаев,
   предусмотренных законодательством РК.
2. Обработка данных осуществляется на серверах, физически расположенных
   на территории Республики Казахстан (принцип «закрытого контура»).
3. Я вправе в любой момент отозвать данное согласие, направив
   соответствующий запрос оператору системы.
4. Я предоставляю только минимально необходимый объём данных для
   подготовки обращения.

Нажимая «Я согласен», я подтверждаю, что ознакомлен(а) с настоящим
согласием и предоставляю его добровольно, осознанно и в своих интересах.""",

    "KZ": """ДЕРБЕС ДЕРЕКТЕРДІ ӨҢДЕУГЕ КЕЛІСІМ

Қазақстан Республикасының 2013 жылғы 21 мамырдағы № 94-V «Дербес
деректер және оларды қорғау туралы» Заңына сәйкес, мен өзімнің дербес
деректерімді «Constitutional Assistant» жүйесінің Қазақстан Республикасының
Конституциялық Сотына өтініш дайындау және беру мақсатында ғана жинауына
және өңдеуіне келісім беремін.

Мен мыналарды түсінемін:
1. Менің деректерім тек көрсетілген мақсат үшін ғана пайдаланылады және
   ҚР заңнамасында көзделген жағдайларды қоспағанда, менің келісімімсіз
   үшінші тұлғаларға берілмейді.
2. Деректерді өңдеу Қазақстан Республикасының аумағында орналасқан
   серверлерде жүзеге асырылады («жабық контур» қағидаты).
3. Мен кез келген уақытта осы келісімді жүйе операторына тиісті сұрау
   жіберу арқылы кері қайтарып алуға құқылым бар.
4. Мен өтінішті дайындау үшін тек ең қажетті көлемдегі деректерді ғана
   ұсынамын.

«Мен келісемін» түймесін басу арқылы мен осы келісіммен танысқанымды
және оны өз еркіммен, саналы түрде және өз мүддемде беретінімді
растаймын.""",

    "EN": """CONSENT TO PERSONAL DATA PROCESSING

In accordance with the Law of the Republic of Kazakhstan dated May 21,
2013 No. 94-V "On Personal Data and Their Protection", I hereby consent
to the collection and processing of my personal data by the
"Constitutional Assistant" system solely for the purpose of preparing
and submitting an appeal to the Constitutional Court of the Republic of
Kazakhstan.

I understand that:
1. My data will be used only for the stated purpose and will not be
   shared with third parties without my consent, except as required by
   the legislation of the Republic of Kazakhstan.
2. Data processing takes place on servers physically located within the
   Republic of Kazakhstan (closed-loop principle).
3. I have the right to withdraw this consent at any time by sending a
   corresponding request to the system operator.
4. I will provide only the minimum amount of data necessary to prepare
   the appeal.

By clicking "I Agree", I confirm that I have read this consent and give
it voluntarily, consciously, and in my own interest.""",
}


def get_consent_text(language: str = "RU") -> str:
    """Возвращает текст согласия на нужном языке."""
    lang = language.upper() if language.upper() in CONSENT_TEXT else "RU"
    return CONSENT_TEXT[lang]


# ============================================================================
# ХРАНИЛИЩЕ СОГЛАСИЙ (in-memory — заменить на БД в продакшене)
# ============================================================================

_consent_store: Dict[str, dict] = {}


def record_consent(
    full_name: Optional[str] = None,
    language: str = "RU",
    ip_address: Optional[str] = None,
) -> dict:
    """
    Записывает факт согласия гражданина.

    ВАЖНО: full_name хранится только с явного согласия самого пользователя
    как часть процесса подачи обращения — не персональные данные третьих лиц.
    """
    consent_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc)

    record = {
        "consent_id": consent_id,
        "full_name": full_name,
        "language": language,
        "consented_at": timestamp.isoformat(),
        "ip_address": ip_address,
        "consent_text_snapshot": get_consent_text(language),
    }

    _consent_store[consent_id] = record
    return record


def get_consent_record(consent_id: str) -> Optional[dict]:
    """Возвращает запись о согласии по ID."""
    return _consent_store.get(consent_id)


def generate_consent_document(consent_id: str) -> Optional[str]:
    """
    Генерирует текстовый документ-подтверждение согласия для скачивания.
    Возвращает None, если запись не найдена.
    """
    record = get_consent_record(consent_id)
    if not record:
        return None

    header = {
        "RU": "ПОДТВЕРЖДЕНИЕ СОГЛАСИЯ НА ОБРАБОТКУ ПЕРСОНАЛЬНЫХ ДАННЫХ",
        "KZ": "ДЕРБЕС ДЕРЕКТЕРДІ ӨҢДЕУГЕ КЕЛІСІМДІ РАСТАУ",
        "EN": "CONFIRMATION OF CONSENT TO PERSONAL DATA PROCESSING",
    }.get(record["language"], "CONSENT CONFIRMATION")

    labels = {
        "RU": {"id": "Идентификатор согласия", "name": "ФИО", "date": "Дата и время согласия"},
        "KZ": {"id": "Келісім идентификаторы", "name": "Аты-жөні", "date": "Келісім берілген күні мен уақыты"},
        "EN": {"id": "Consent ID", "name": "Full name", "date": "Date and time of consent"},
    }.get(record["language"], {"id": "Consent ID", "name": "Full name", "date": "Date"})

    doc = f"""{header}
{'=' * len(header)}

{labels['id']}: {record['consent_id']}
{labels['name']}: {record['full_name'] or '—'}
{labels['date']}: {record['consented_at']}

{'-' * 60}

{record['consent_text_snapshot']}
"""
    return doc
