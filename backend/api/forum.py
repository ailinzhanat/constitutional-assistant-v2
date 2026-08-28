"""
Форум для юридического сообщества — Constitutional Assistant.

Реализует ТЗ "Форум для юридического сообщества в составе Constitutional
Assistant": роли и доступ (гость / верифицированный юрист / гражданин /
модератор), темы и ответы с привязкой к шагу интерфейса, голосование,
статусы предложений, ссылки на базу НП КС РК, автофильтр персональных
данных, жалобы, подписки и уведомления.

Хранение: Neo4j (та же база, что и весь проект) — узлы :ForumUser,
:ForumSession, :ForumVerificationDoc, :ForumThread, :ForumReply,
:ForumVote, :ForumReport, :ForumSubscription, :ForumNotification.

Аутентификация: НЕТ внешних библиотек (bcrypt/jwt и т.п. в проекте не
установлены) — пароли хешируются встроенным hashlib.pbkdf2_hmac с солью,
токены сессии — случайная строка (secrets.token_urlsafe), проверяются
по заголовку X-Forum-Token (тот же стиль, что уже используется в проекте
для X-Admin-Code / X-Judicial-Code — простой заголовок вместо OAuth).

Роль "Модератор проекта" НЕ хранится как отдельный аккаунт форума — как и
во всех остальных админках проекта, модератором считается любой, у кого
есть ADMIN_ACCESS_CODE (тот же код, что и в analytics.py/feedback.py).
Это сознательное упрощение под пилот: "команда проекта" и так одна.

Email-уведомления (NFR-5) — best-effort через встроенный smtplib, ТОЛЬКО
если заданы переменные окружения SMTP_HOST/SMTP_PORT/SMTP_USER/
SMTP_PASSWORD/SMTP_FROM. Если их нет — уведомления работают только внутри
интерфейса (список уведомлений), это не ломает остальной функционал форума
(тот же принцип, что и с Google Sheets — резервный канал не должен ронять
основной).

Как подключить в main.py — см. комментарии внизу файла ("INTEGRATION").
"""

import hashlib
import os
import re
import secrets
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Callable, List, Optional

from fastapi import APIRouter, Header, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/forum", tags=["forum"])

ADMIN_ACCESS_CODE = os.environ.get("ADMIN_ACCESS_CODE", "")  # тот же код, что в analytics.py/feedback.py — роль "модератор"

_run_query: Optional[Callable] = None


def init_forum_module(run_query_fn: Callable):
    """Вызывается один раз из main.py при старте приложения."""
    global _run_query
    _run_query = run_query_fn


def _query(cypher: str, params: dict = None):
    if _run_query is None:
        raise HTTPException(status_code=503, detail="Forum-модуль не инициализирован (init_forum_module не вызван в main.py)")
    try:
        return _run_query(cypher, params or {})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Ошибка подключения к Neo4j: {str(e)}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# FR-1: структура форума — разделы
# ---------------------------------------------------------------------------

FORUM_CATEGORIES = [
    {"key": "admissibility", "title": "Допустимость обращений (ст. 45) — спорные случаи"},
    {"key": "form_content", "title": "Форма и содержание обращения (ст. 44) — формулировки, шаблоны"},
    {"key": "routing", "title": "Логика маршрутизации (когда КС РК не компетентен — куда направлять)"},
    {"key": "bugs", "title": "Ошибки и неточности интерфейса"},
    {"key": "proposals", "title": "Предложения по развитию (в т.ч. форма для представителей, п.15 Регламента)"},
    {"key": "general", "title": "Общее обсуждение / новости практики КС РК"},
]
_CATEGORY_KEYS = {c["key"] for c in FORUM_CATEGORIES}

ALLOWED_STATUSES = ["на рассмотрении", "принято в разработку", "внедрено", "отклонено"]

# FR-11: правила форума, показываются при первом входе (см. п.4.6 ТЗ)
FORUM_RULES_TEXT = {
    "ru": (
        "Форум предназначен для профессионального обсуждения методологии и текста "
        "Constitutional Assistant. Обсуждение конкретных дел граждан с указанием "
        "персональных данных запрещено. Мнения на форуме не являются официальной "
        "позицией Конституционного Суда РК или официальной юридической консультацией."
    ),
    "kk": (
        "Форум Constitutional Assistant әдіснамасы мен мәтінін кәсіби талқылауға "
        "арналған. Азаматтардың нақты істерін дербес деректерін көрсете отырып "
        "талқылауға тыйым салынады. Форумдағы пікірлер Қазақстан Республикасы "
        "Конституциялық Сотының ресми ұстанымы немесе ресми заң кеңесі болып "
        "табылмайды."
    ),
    "en": (
        "This forum is for professional discussion of Constitutional Assistant's "
        "methodology and text. Discussing specific citizens' cases with personal "
        "data is prohibited. Opinions on the forum are not an official position of "
        "the Constitutional Court of the RK or official legal advice."
    ),
}


# ---------------------------------------------------------------------------
# Пароли и сессии (без внешних библиотек — hashlib + secrets из стандартной библиотеки)
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 200_000


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS)
    return f"{salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, _, hex_hash = stored.partition("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITERATIONS)
        return secrets.compare_digest(dk.hex(), hex_hash)
    except Exception:
        return False


def _create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    _query(
        "CREATE (s:ForumSession {token: $token, user_id: $user_id, created_at: $created_at})",
        {"token": token, "user_id": user_id, "created_at": _now()},
    )
    return token


def _user_public(user_row: dict) -> dict:
    """Публичное представление пользователя — без password_hash."""
    return {
        "user_id": user_row.get("user_id"),
        "display_name": user_row.get("display_name"),
        "role": user_row.get("role"),
        "organization": user_row.get("organization"),
    }


def _get_user_by_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    rows = _query(
        "MATCH (s:ForumSession {token: $token}) RETURN s.user_id AS user_id",
        {"token": token},
    )
    if not rows:
        return None
    user_rows = _query(
        """
        MATCH (u:ForumUser {user_id: $user_id})
        RETURN u.user_id AS user_id, u.email AS email, u.display_name AS display_name,
               u.role AS role, u.organization AS organization,
               u.verification_status AS verification_status
        """,
        {"user_id": rows[0]["user_id"]},
    )
    return user_rows[0] if user_rows else None


def require_any_user(x_forum_token: Optional[str] = Header(None)) -> dict:
    user = _get_user_by_token(x_forum_token)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется вход в форум (неверный или отсутствующий X-Forum-Token)")
    return user


def require_verified_lawyer(x_forum_token: Optional[str] = Header(None)) -> dict:
    user = require_any_user(x_forum_token)
    if user.get("role") != "lawyer_verified":
        raise HTTPException(
            status_code=403,
            detail="Создавать темы и отвечать может только верифицированный юрист. "
                   "Гражданам для обратной связи — вкладка «Обратная связь».",
        )
    return user


def require_moderator(x_admin_code: Optional[str] = Header(None)):
    if not ADMIN_ACCESS_CODE or x_admin_code != ADMIN_ACCESS_CODE:
        raise HTTPException(status_code=403, detail="Неверный код доступа модератора")
    return True


# ---------------------------------------------------------------------------
# FR-9: автофильтр персональных данных (эвристика — см. ограничение ниже)
# ---------------------------------------------------------------------------
#
# ЧЕСТНОЕ ОГРАНИЧЕНИЕ: надёжно распознать ИИН, номер телефона и номер дела
# регулярными выражениями можно. Надёжно распознать ФИО регулярным выражением
# НЕЛЬЗЯ (нет отличия от обычных слов) — такой фильтр либо ничего не ловит,
# либо ложно блокирует половину нормального текста. Поэтому автофильтр — это
# подсказка для автора и первая линия защиты, а не гарантия (как и
# автокатегоризация отзывов в analytics.py) — окончательная проверка перед
# публикацией обезличенных примеров обращений (NFR-2) — за модератором.

_IIN_RE = re.compile(r"\b\d{12}\b")
_PHONE_RE = re.compile(r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b")
_CASE_NUMBER_RE = re.compile(r"\bдел[оа]\s*№?\s*\d[\d\-/]*\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def _detect_pii(text: str) -> List[str]:
    if not text:
        return []
    found = []
    if _IIN_RE.search(text):
        found.append("похоже на ИИН (12 цифр подряд)")
    if _PHONE_RE.search(text):
        found.append("похоже на номер телефона")
    if _CASE_NUMBER_RE.search(text):
        found.append("похоже на номер судебного дела")
    if _EMAIL_RE.search(text):
        found.append("похоже на email")
    return found


def _check_pii_or_raise(*texts: str):
    all_found = []
    for t in texts:
        all_found.extend(_detect_pii(t or ""))
    if all_found:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "possible_pii",
                "message": "Похоже, текст содержит персональные данные. Уберите их и отправьте снова.",
                "detected": sorted(set(all_found)),
            },
        )


# ---------------------------------------------------------------------------
# Модели запросов
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=8)
    display_name: str
    role_requested: str = Field(..., description="'citizen' или 'lawyer'")
    organization: Optional[str] = None  # коллегия адвокатов / палата юр.консультантов / вуз
    pdn_consent: bool = Field(..., description="NFR-1: согласие на обработку ПДн — обязательно")


class LoginRequest(BaseModel):
    email: str
    password: str


class ThreadCreateRequest(BaseModel):
    category: str
    title: str
    body: str
    tags: List[str] = []
    linked_step: Optional[str] = None  # FR-3/6: привязка к шагу/формулировке интерфейса
    np_reference: Optional[str] = None  # FR-8: номер НП КС РК
    attachment_text: Optional[str] = None  # FR-2: обезличенный пример обращения
    language: str = "ru"


class ReplyCreateRequest(BaseModel):
    body: str
    quoted_step: Optional[str] = None  # FR-3: глубокая ссылка на фрагмент, который обсуждают
    np_reference: Optional[str] = None


class VoteRequest(BaseModel):
    target_type: str  # "thread" | "reply"
    target_id: str
    value: str  # "support" | "needs_clarification"


class ReportRequest(BaseModel):
    target_type: str
    target_id: str
    reason: str


class StatusUpdateRequest(BaseModel):
    status: str
    note: Optional[str] = None
    implemented_version: Optional[str] = None  # обязателен при status == "внедрено"
    linked_step: Optional[str] = None  # обязателен при status == "внедрено" (FR-6), если не указан у темы


class VerificationReviewRequest(BaseModel):
    approve: bool
    note: Optional[str] = None


class ReportResolveRequest(BaseModel):
    action: str  # "hide" (оставить скрытым) | "restore" (вернуть)


# ---------------------------------------------------------------------------
# Регистрация / вход
# ---------------------------------------------------------------------------

@router.post("/register")
def register(body: RegisterRequest):
    if not body.pdn_consent:
        raise HTTPException(status_code=400, detail="NFR-1: требуется согласие на обработку персональных данных для регистрации на форуме")
    if body.role_requested not in ("citizen", "lawyer"):
        raise HTTPException(status_code=400, detail="role_requested должен быть 'citizen' или 'lawyer'")

    existing = _query("MATCH (u:ForumUser {email: $email}) RETURN u.user_id AS user_id", {"email": body.email.lower().strip()})
    if existing:
        raise HTTPException(status_code=409, detail="Пользователь с таким email уже зарегистрирован")

    user_id = _new_id()
    role = "citizen" if body.role_requested == "citizen" else "lawyer_pending"
    verification_status = None if body.role_requested == "citizen" else "pending"

    _query(
        """
        CREATE (u:ForumUser {
            user_id: $user_id, email: $email, password_hash: $password_hash,
            display_name: $display_name, role: $role, organization: $organization,
            verification_status: $verification_status, verification_note: null,
            pdn_consent_at: $now, created_at: $now
        })
        """,
        {
            "user_id": user_id,
            "email": body.email.lower().strip(),
            "password_hash": _hash_password(body.password),
            "display_name": body.display_name.strip()[:120],
            "role": role,
            "organization": (body.organization or "").strip()[:200] or None,
            "verification_status": verification_status,
            "now": _now(),
        },
    )
    token = _create_session(user_id)
    return {
        "status": "success",
        "token": token,
        "user": {"user_id": user_id, "display_name": body.display_name, "role": role},
        "next_step": (
            "Роль «гражданин» активна сразу — можно читать форум."
            if role == "citizen"
            else "Заявка на верификацию юриста создана. Загрузите документ (POST /api/forum/verification-document) "
                 "— модератор рассмотрит заявку в течение 3 рабочих дней (NFR-6)."
        ),
    }


@router.post("/login")
def login(body: LoginRequest):
    rows = _query(
        "MATCH (u:ForumUser {email: $email}) RETURN u.user_id AS user_id, u.password_hash AS password_hash, u.display_name AS display_name, u.role AS role",
        {"email": body.email.lower().strip()},
    )
    if not rows or not _verify_password(body.password, rows[0]["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    token = _create_session(rows[0]["user_id"])
    return {"status": "success", "token": token, "user": {"user_id": rows[0]["user_id"], "display_name": rows[0]["display_name"], "role": rows[0]["role"]}}


@router.get("/me")
def me(x_forum_token: Optional[str] = Header(None)):
    user = require_any_user(x_forum_token)
    return _user_public(user)


@router.get("/rules")
def get_rules():
    """FR-11: текст правил форума на 3 языках — показывается модалкой при первом входе."""
    return {"rules": FORUM_RULES_TEXT}


# ---------------------------------------------------------------------------
# Верификация юристов (NFR-3: документ хранится отдельно, доступ только у модератора)
# ---------------------------------------------------------------------------

@router.post("/verification-document")
async def upload_verification_document(
    file: UploadFile = File(...),
    x_forum_token: Optional[str] = Header(None),
):
    user = require_any_user(x_forum_token)
    if user.get("role") not in ("lawyer_pending", "lawyer_rejected"):
        raise HTTPException(status_code=400, detail="Загрузка документа верификации доступна только для заявки на роль юриста")

    content = await file.read()
    max_bytes = 5 * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Файл слишком большой (максимум 5 МБ)")

    import base64
    doc_id = _new_id()
    _query(
        """
        CREATE (d:ForumVerificationDoc {
            doc_id: $doc_id, user_id: $user_id, filename: $filename,
            content_type: $content_type, data_b64: $data_b64, uploaded_at: $now
        })
        """,
        {
            "doc_id": doc_id,
            "user_id": user["user_id"],
            "filename": file.filename or "document",
            "content_type": file.content_type or "application/octet-stream",
            "data_b64": base64.b64encode(content).decode("ascii"),
            "now": _now(),
        },
    )
    _query(
        "MATCH (u:ForumUser {user_id: $user_id}) SET u.verification_doc_id = $doc_id, u.verification_status = 'pending'",
        {"user_id": user["user_id"], "doc_id": doc_id},
    )
    return {"status": "success", "doc_id": doc_id}


@router.get("/moderation/pending-lawyers")
def list_pending_lawyers(x_admin_code: Optional[str] = Header(None)):
    require_moderator(x_admin_code)
    rows = _query(
        """
        MATCH (u:ForumUser)
        WHERE u.role = 'lawyer_pending'
        RETURN u.user_id AS user_id, u.email AS email, u.display_name AS display_name,
               u.organization AS organization, u.verification_doc_id AS verification_doc_id,
               u.created_at AS created_at
        ORDER BY u.created_at ASC
        """
    )
    now = datetime.now(timezone.utc)
    for r in rows:
        try:
            created = datetime.fromisoformat(r["created_at"])
            r["days_pending"] = (now - created).days
            r["sla_overdue"] = r["days_pending"] > 3  # NFR-6: не более 3 рабочих дней (упрощённо — календарных)
        except Exception:
            r["days_pending"] = None
            r["sla_overdue"] = False
    return {"count": len(rows), "items": rows}


@router.get("/moderation/verification-document/{doc_id}")
def download_verification_document(doc_id: str, x_admin_code: Optional[str] = Header(None)):
    require_moderator(x_admin_code)
    rows = _query(
        "MATCH (d:ForumVerificationDoc {doc_id: $doc_id}) RETURN d.filename AS filename, d.content_type AS content_type, d.data_b64 AS data_b64",
        {"doc_id": doc_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Документ не найден")
    import base64
    from fastapi.responses import StreamingResponse
    from io import BytesIO
    data = base64.b64decode(rows[0]["data_b64"])
    return StreamingResponse(
        BytesIO(data),
        media_type=rows[0]["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{rows[0]["filename"]}"'},
    )


@router.post("/moderation/lawyers/{user_id}/review")
def review_lawyer(user_id: str, body: VerificationReviewRequest, x_admin_code: Optional[str] = Header(None)):
    require_moderator(x_admin_code)
    new_role = "lawyer_verified" if body.approve else "lawyer_rejected"
    rows = _query(
        """
        MATCH (u:ForumUser {user_id: $user_id})
        SET u.role = $new_role, u.verification_status = $status, u.verification_note = $note
        RETURN u.user_id AS user_id
        """,
        {
            "user_id": user_id,
            "new_role": new_role,
            "status": "approved" if body.approve else "rejected",
            "note": body.note,
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return {"status": "success", "new_role": new_role}


# ---------------------------------------------------------------------------
# Темы и ответы
# ---------------------------------------------------------------------------

@router.get("/categories")
def get_categories():
    return {"categories": FORUM_CATEGORIES}


def _np_reference_lookup(number: Optional[str]) -> Optional[dict]:
    """FR-8: автоматическое подтягивание точной цитаты НП КС РК по номеру."""
    if not number:
        return None
    rows = _query(
        "MATCH (r:NormativeResolution {number: $number}) RETURN r.number AS number, r.date AS date, r.title AS title, r.summary AS summary, r.source_url AS source_url",
        {"number": number.strip()},
    )
    return rows[0] if rows else None


def _vote_counts(target_type: str, target_id: str) -> dict:
    rows = _query(
        """
        MATCH (v:ForumVote {target_type: $target_type, target_id: $target_id})
        RETURN v.value AS value, count(*) AS c
        """,
        {"target_type": target_type, "target_id": target_id},
    )
    counts = {"support": 0, "needs_clarification": 0}
    for r in rows:
        if r["value"] in counts:
            counts[r["value"]] = r["c"]
    return counts


@router.post("/threads")
def create_thread(body: ThreadCreateRequest, x_forum_token: Optional[str] = Header(None)):
    user = require_verified_lawyer(x_forum_token)
    if body.category not in _CATEGORY_KEYS:
        raise HTTPException(status_code=400, detail=f"Неизвестная категория. Допустимые: {sorted(_CATEGORY_KEYS)}")
    if not body.title.strip() or not body.body.strip():
        raise HTTPException(status_code=400, detail="Заголовок и текст темы не могут быть пустыми")

    # FR-9: автофильтр ПДн — блокируем публикацию до правки автором
    _check_pii_or_raise(body.title, body.body, body.attachment_text or "")

    np_ref = _np_reference_lookup(body.np_reference)

    thread_id = _new_id()
    now = _now()
    _query(
        """
        CREATE (t:ForumThread {
            thread_id: $thread_id, category: $category, title: $title, body: $body,
            tags: $tags, author_user_id: $author_user_id, author_display_name: $author_display_name,
            language: $language, linked_step: $linked_step, np_reference: $np_reference,
            attachment_text: $attachment_text,
            created_at: $now, status: $status, status_note: null, status_updated_at: $now,
            implemented_version: null, pinned: false, closed: false, hidden: false
        })
        """,
        {
            "thread_id": thread_id,
            "category": body.category,
            "title": body.title.strip()[:300],
            "body": body.body.strip()[:20000],
            "tags": [t.strip()[:40] for t in body.tags][:10],
            "author_user_id": user["user_id"],
            "author_display_name": user["display_name"],
            "language": body.language,
            "linked_step": body.linked_step,
            "np_reference": body.np_reference,
            "attachment_text": (body.attachment_text or "").strip()[:20000] or None,
            "now": now,
            "status": "на рассмотрении",
        },
    )
    # автор автоматически подписан на свою тему
    _query(
        "MERGE (:ForumSubscription {user_id: $user_id, thread_id: $thread_id})",
        {"user_id": user["user_id"], "thread_id": thread_id},
    )
    return {"status": "success", "thread_id": thread_id, "np_reference_resolved": np_ref}


@router.get("/threads")
def list_threads(category: Optional[str] = None, status: Optional[str] = None, tag: Optional[str] = None, limit: int = 100):
    where = ["t.hidden = false"]
    params = {"limit": limit}
    if category:
        where.append("t.category = $category")
        params["category"] = category
    if status:
        where.append("t.status = $status")
        params["status"] = status
    if tag:
        where.append("$tag IN t.tags")
        params["tag"] = tag
    where_sql = " AND ".join(where)

    rows = _query(
        f"""
        MATCH (t:ForumThread)
        WHERE {where_sql}
        OPTIONAL MATCH (r:ForumReply {{thread_id: t.thread_id, hidden: false}})
        WITH t, count(r) AS reply_count
        RETURN t.thread_id AS thread_id, t.category AS category, t.title AS title,
               t.tags AS tags, t.author_display_name AS author_display_name,
               t.created_at AS created_at, t.status AS status, t.pinned AS pinned,
               t.closed AS closed, t.linked_step AS linked_step, reply_count
        ORDER BY t.pinned DESC, t.created_at DESC
        LIMIT $limit
        """,
        params,
    )
    for r in rows:
        votes = _vote_counts("thread", r["thread_id"])
        r["votes"] = votes
    return {"count": len(rows), "items": rows}


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str):
    rows = _query(
        """
        MATCH (t:ForumThread {thread_id: $thread_id})
        RETURN t.thread_id AS thread_id, t.category AS category, t.title AS title, t.body AS body,
               t.tags AS tags, t.author_display_name AS author_display_name, t.language AS language,
               t.linked_step AS linked_step, t.np_reference AS np_reference,
               t.attachment_text AS attachment_text, t.created_at AS created_at,
               t.status AS status, t.status_note AS status_note, t.implemented_version AS implemented_version,
               t.pinned AS pinned, t.closed AS closed, t.hidden AS hidden
        """,
        {"thread_id": thread_id},
    )
    if not rows or rows[0]["hidden"]:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    thread = rows[0]
    thread["votes"] = _vote_counts("thread", thread_id)
    thread["np_reference_resolved"] = _np_reference_lookup(thread.get("np_reference"))

    replies = _query(
        """
        MATCH (r:ForumReply {thread_id: $thread_id, hidden: false})
        RETURN r.reply_id AS reply_id, r.body AS body, r.author_display_name AS author_display_name,
               r.quoted_step AS quoted_step, r.np_reference AS np_reference, r.created_at AS created_at
        ORDER BY r.created_at ASC
        """,
        {"thread_id": thread_id},
    )
    for r in replies:
        r["votes"] = _vote_counts("reply", r["reply_id"])
        r["np_reference_resolved"] = _np_reference_lookup(r.get("np_reference"))
    thread["replies"] = replies
    return thread


def _notify_subscribers(thread_id: str, message: str, exclude_user_id: Optional[str] = None):
    """NFR-5: создаёт уведомления в интерфейсе + best-effort письмо, если настроен SMTP."""
    subs = _query("MATCH (s:ForumSubscription {thread_id: $thread_id}) RETURN s.user_id AS user_id", {"thread_id": thread_id})
    for s in subs:
        if s["user_id"] == exclude_user_id:
            continue
        _query(
            """
            CREATE (n:ForumNotification {
                notification_id: $nid, user_id: $user_id, thread_id: $thread_id,
                message: $message, created_at: $now, read: false
            })
            """,
            {"nid": _new_id(), "user_id": s["user_id"], "thread_id": thread_id, "message": message, "now": _now()},
        )
        _send_email_best_effort(s["user_id"], message, thread_id)


def _send_email_best_effort(user_id: str, message: str, thread_id: str):
    smtp_host = os.environ.get("SMTP_HOST")
    if not smtp_host:
        return  # email не настроен — уведомление уже создано в интерфейсе, этого достаточно
    try:
        rows = _query("MATCH (u:ForumUser {user_id: $user_id}) RETURN u.email AS email", {"user_id": user_id})
        if not rows or not rows[0].get("email"):
            return
        msg = MIMEText(f"{message}\n\nОткрыть тему: https://constitutional-assistantkz.netlify.app/forum.html?thread={thread_id}")
        msg["Subject"] = "Constitutional Assistant — новое сообщение на форуме"
        msg["From"] = os.environ.get("SMTP_FROM", smtp_host)
        msg["To"] = rows[0]["email"]
        with smtplib.SMTP(smtp_host, int(os.environ.get("SMTP_PORT", "587")), timeout=10) as server:
            server.starttls()
            server.login(os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", ""))
            server.send_message(msg)
    except Exception as e:
        print(f"⚠️ Forum email notification failed: {e}")


@router.post("/threads/{thread_id}/replies")
def create_reply(thread_id: str, body: ReplyCreateRequest, x_forum_token: Optional[str] = Header(None)):
    user = require_verified_lawyer(x_forum_token)
    thread_rows = _query("MATCH (t:ForumThread {thread_id: $thread_id}) RETURN t.closed AS closed, t.hidden AS hidden", {"thread_id": thread_id})
    if not thread_rows or thread_rows[0]["hidden"]:
        raise HTTPException(status_code=404, detail="Тема не найдена")
    if thread_rows[0]["closed"]:
        raise HTTPException(status_code=400, detail="Тема закрыта модератором — новые ответы недоступны")
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="Текст ответа не может быть пустым")

    _check_pii_or_raise(body.body)

    reply_id = _new_id()
    _query(
        """
        CREATE (r:ForumReply {
            reply_id: $reply_id, thread_id: $thread_id, body: $body,
            author_user_id: $author_user_id, author_display_name: $author_display_name,
            quoted_step: $quoted_step, np_reference: $np_reference, created_at: $now, hidden: false
        })
        """,
        {
            "reply_id": reply_id,
            "thread_id": thread_id,
            "body": body.body.strip()[:20000],
            "author_user_id": user["user_id"],
            "author_display_name": user["display_name"],
            "quoted_step": body.quoted_step,
            "np_reference": body.np_reference,
            "now": _now(),
        },
    )
    _query("MERGE (:ForumSubscription {user_id: $user_id, thread_id: $thread_id})", {"user_id": user["user_id"], "thread_id": thread_id})
    _notify_subscribers(thread_id, f"{user['display_name']} ответил(а) в теме", exclude_user_id=user["user_id"])
    return {"status": "success", "reply_id": reply_id}


@router.post("/threads/{thread_id}/subscribe")
def subscribe_thread(thread_id: str, x_forum_token: Optional[str] = Header(None)):
    user = require_any_user(x_forum_token)
    _query("MERGE (:ForumSubscription {user_id: $user_id, thread_id: $thread_id})", {"user_id": user["user_id"], "thread_id": thread_id})
    return {"status": "success"}


@router.post("/threads/{thread_id}/unsubscribe")
def unsubscribe_thread(thread_id: str, x_forum_token: Optional[str] = Header(None)):
    user = require_any_user(x_forum_token)
    _query("MATCH (s:ForumSubscription {user_id: $user_id, thread_id: $thread_id}) DELETE s", {"user_id": user["user_id"], "thread_id": thread_id})
    return {"status": "success"}


# ---------------------------------------------------------------------------
# FR-4: голосование
# ---------------------------------------------------------------------------

@router.post("/vote")
def vote(body: VoteRequest, x_forum_token: Optional[str] = Header(None)):
    user = require_verified_lawyer(x_forum_token)
    if body.target_type not in ("thread", "reply"):
        raise HTTPException(status_code=400, detail="target_type должен быть 'thread' или 'reply'")
    if body.value not in ("support", "needs_clarification"):
        raise HTTPException(status_code=400, detail="value должен быть 'support' или 'needs_clarification'")
    _query(
        """
        MERGE (v:ForumVote {user_id: $user_id, target_type: $target_type, target_id: $target_id})
        SET v.value = $value, v.created_at = $now
        """,
        {"user_id": user["user_id"], "target_type": body.target_type, "target_id": body.target_id, "value": body.value, "now": _now()},
    )
    return {"status": "success", "votes": _vote_counts(body.target_type, body.target_id)}


# ---------------------------------------------------------------------------
# FR-10: жалобы → скрытие до решения модератора
# ---------------------------------------------------------------------------

@router.post("/report")
def report(body: ReportRequest, x_forum_token: Optional[str] = Header(None)):
    user = require_any_user(x_forum_token)
    if body.target_type not in ("thread", "reply"):
        raise HTTPException(status_code=400, detail="target_type должен быть 'thread' или 'reply'")

    label = "ForumThread" if body.target_type == "thread" else "ForumReply"
    id_field = "thread_id" if body.target_type == "thread" else "reply_id"
    rows = _query(f"MATCH (x:{label} {{{id_field}: $id}}) SET x.hidden = true RETURN x.{id_field} AS id", {"id": body.target_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Объект жалобы не найден")

    report_id = _new_id()
    _query(
        """
        CREATE (r:ForumReport {
            report_id: $report_id, target_type: $target_type, target_id: $target_id,
            reporter_user_id: $reporter_user_id, reason: $reason, created_at: $now, resolved: false
        })
        """,
        {
            "report_id": report_id,
            "target_type": body.target_type,
            "target_id": body.target_id,
            "reporter_user_id": user["user_id"],
            "reason": body.reason.strip()[:1000],
            "now": _now(),
        },
    )
    return {"status": "success", "report_id": report_id}


@router.get("/moderation/reports")
def list_reports(x_admin_code: Optional[str] = Header(None)):
    require_moderator(x_admin_code)
    rows = _query(
        "MATCH (r:ForumReport {resolved: false}) RETURN r.report_id AS report_id, r.target_type AS target_type, r.target_id AS target_id, r.reason AS reason, r.created_at AS created_at ORDER BY r.created_at ASC"
    )
    for r in rows:
        label = "ForumThread" if r["target_type"] == "thread" else "ForumReply"
        id_field = "thread_id" if r["target_type"] == "thread" else "reply_id"
        content_rows = _query(f"MATCH (x:{label} {{{id_field}: $id}}) RETURN x.body AS body, x.title AS title", {"id": r["target_id"]})
        r["content"] = content_rows[0] if content_rows else None
    return {"count": len(rows), "items": rows}


@router.post("/moderation/reports/{report_id}/resolve")
def resolve_report(report_id: str, body: ReportResolveRequest, x_admin_code: Optional[str] = Header(None)):
    require_moderator(x_admin_code)
    rows = _query("MATCH (r:ForumReport {report_id: $id}) RETURN r.target_type AS target_type, r.target_id AS target_id", {"id": report_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Жалоба не найдена")
    if body.action == "restore":
        label = "ForumThread" if rows[0]["target_type"] == "thread" else "ForumReply"
        id_field = "thread_id" if rows[0]["target_type"] == "thread" else "reply_id"
        _query(f"MATCH (x:{label} {{{id_field}: $id}}) SET x.hidden = false", {"id": rows[0]["target_id"]})
    _query("MATCH (r:ForumReport {report_id: $id}) SET r.resolved = true, r.resolution_action = $action", {"id": report_id, "action": body.action})
    return {"status": "success"}


# ---------------------------------------------------------------------------
# FR-5/FR-6: статусы тем + связь с доработкой продукта
# ---------------------------------------------------------------------------

@router.post("/threads/{thread_id}/status")
def set_thread_status(thread_id: str, body: StatusUpdateRequest, x_admin_code: Optional[str] = Header(None)):
    require_moderator(x_admin_code)
    if body.status not in ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"Недопустимый статус. Допустимые: {ALLOWED_STATUSES}")
    if body.status == "внедрено" and not body.implemented_version:
        raise HTTPException(status_code=400, detail="Для статуса «внедрено» обязательно указать implemented_version (FR-6)")

    rows = _query("MATCH (t:ForumThread {thread_id: $id}) RETURN t.linked_step AS linked_step, t.author_user_id AS author_user_id, t.title AS title", {"id": thread_id})
    if not rows:
        raise HTTPException(status_code=404, detail="Тема не найдена")

    linked_step = body.linked_step or rows[0]["linked_step"]
    if body.status == "внедрено" and not linked_step:
        raise HTTPException(status_code=400, detail="Для статуса «внедрено» нужен привязанный раздел интерфейса (linked_step) — либо уже есть у темы, либо укажите в запросе (FR-6)")

    _query(
        """
        MATCH (t:ForumThread {thread_id: $id})
        SET t.status = $status, t.status_note = $note, t.implemented_version = $implemented_version,
            t.linked_step = $linked_step, t.status_updated_at = $now
        """,
        {
            "id": thread_id,
            "status": body.status,
            "note": body.note,
            "implemented_version": body.implemented_version,
            "linked_step": linked_step,
            "now": _now(),
        },
    )
    _notify_subscribers(thread_id, f"Тема «{rows[0]['title']}» получила статус: {body.status}")
    return {"status": "success"}


@router.post("/threads/{thread_id}/pin")
def toggle_pin(thread_id: str, pinned: bool, x_admin_code: Optional[str] = Header(None)):
    require_moderator(x_admin_code)
    _query("MATCH (t:ForumThread {thread_id: $id}) SET t.pinned = $pinned", {"id": thread_id, "pinned": pinned})
    return {"status": "success"}


@router.post("/threads/{thread_id}/close")
def toggle_close(thread_id: str, closed: bool, x_admin_code: Optional[str] = Header(None)):
    require_moderator(x_admin_code)
    _query("MATCH (t:ForumThread {thread_id: $id}) SET t.closed = $closed", {"id": thread_id, "closed": closed})
    return {"status": "success"}


# ---------------------------------------------------------------------------
# FR-7: сводка "что изменилось по предложениям форума"
# ---------------------------------------------------------------------------

@router.get("/monthly-summary")
def monthly_summary(period_days: int = 30):
    """
    Считается "на лету" по последним period_days дням (а не по строгим
    календарным месяцам через cron) — так же, как дашборд аналитики: Render
    free-tier не держит фоновый планировщик надёжно, поэтому сводка всегда
    актуальна на момент открытия страницы, а не ждёт полуночи 1-го числа.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=period_days)).isoformat()
    rows = _query(
        """
        MATCH (t:ForumThread {status: 'внедрено'})
        WHERE t.status_updated_at >= $since
        RETURN t.thread_id AS thread_id, t.title AS title, t.implemented_version AS implemented_version,
               t.linked_step AS linked_step, t.status_updated_at AS status_updated_at
        ORDER BY t.status_updated_at DESC
        """,
        {"since": since},
    )
    return {"period_days": period_days, "count": len(rows), "items": rows}


# ---------------------------------------------------------------------------
# NFR-5: уведомления
# ---------------------------------------------------------------------------

@router.get("/notifications")
def list_notifications(x_forum_token: Optional[str] = Header(None)):
    user = require_any_user(x_forum_token)
    rows = _query(
        "MATCH (n:ForumNotification {user_id: $user_id}) RETURN n.notification_id AS notification_id, n.thread_id AS thread_id, n.message AS message, n.created_at AS created_at, n.read AS read ORDER BY n.created_at DESC LIMIT 100",
        {"user_id": user["user_id"]},
    )
    return {"count": len(rows), "unread_count": sum(1 for r in rows if not r["read"]), "items": rows}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, x_forum_token: Optional[str] = Header(None)):
    user = require_any_user(x_forum_token)
    _query("MATCH (n:ForumNotification {notification_id: $id, user_id: $user_id}) SET n.read = true", {"id": notification_id, "user_id": user["user_id"]})
    return {"status": "success"}


# ---------------------------------------------------------------------------
# INTEGRATION — как подключить в main.py
# ---------------------------------------------------------------------------
#
# 1. Положить этот файл в backend/api/forum.py
#
# 2. В main.py добавить рядом с остальными роутерами:
#
#      from forum import router as forum_router, init_forum_module
#      app.include_router(forum_router)
#      init_forum_module(run_query)
#
# 3. requirements.txt — новых зависимостей НЕ требуется (пароли/токены на
#    стандартной библиотеке hashlib/secrets, email — на smtplib).
#
# 4. (Необязательно, для NFR-5 email-уведомлений) в Render → Environment
#    добавить: SMTP_HOST, SMTP_PORT (обычно 587), SMTP_USER, SMTP_PASSWORD,
#    SMTP_FROM. Без них форум работает полностью, просто уведомления будут
#    видны только внутри интерфейса (список уведомлений), не по почте.
#
# 5. Рекомендуется создать индексы в Neo4j (Aura Console → Query):
#      CREATE INDEX IF NOT EXISTS FOR (u:ForumUser) ON (u.email)
#      CREATE INDEX IF NOT EXISTS FOR (s:ForumSession) ON (s.token)
#      CREATE INDEX IF NOT EXISTS FOR (t:ForumThread) ON (t.thread_id)
#      CREATE INDEX IF NOT EXISTS FOR (r:ForumReply) ON (r.thread_id)
