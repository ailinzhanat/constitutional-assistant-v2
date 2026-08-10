"""
Constitutional Assistant - Unified FastAPI Server
С поддержкой загрузки файлов (PDF, Word, сканы, фото) и 3 языков (KZ/RU/EN)
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
import os
from dotenv import load_dotenv

from fastapi.responses import PlainTextResponse

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from parser import parse_file, get_supported_formats, clean_text, truncate_text
from i18n import detect_language, normalize_language, t, SUPPORTED_LANGUAGES
from consent import get_consent_text, record_consent, get_consent_record, generate_consent_document
from gov_redirect import find_relevant_organs, format_redirect_message
from groq_analyzer import analyze_complaint
from groq_generator import generate_appeal_text
from judicial_analyzer import generate_case_summary, search_precedents
from feedback import save_feedback, list_feedback, count_feedback, save_survey, list_surveys, count_surveys

load_dotenv()

app = FastAPI(title="Constitutional Assistant")

# --- Rate Limiter ---
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- CORS ---
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://constitutional-assistantkz.netlify.app"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j+s://8b6c1184.databases.neo4j.io")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

JUDICIAL_ACCESS_CODE = os.getenv("JUDICIAL_ACCESS_CODE")
ADMIN_ACCESS_CODE = os.getenv("ADMIN_ACCESS_CODE")

def check_admin_access(x_admin_code: Optional[str] = Header(None)):
    if not ADMIN_ACCESS_CODE or x_admin_code != ADMIN_ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Неверный код доступа администратора")
    return True

def check_judicial_access(x_judicial_code: Optional[str] = Header(None)):
    if not JUDICIAL_ACCESS_CODE or x_judicial_code != JUDICIAL_ACCESS_CODE:
        raise HTTPException(status_code=401, detail="Неверный код доступа для внутреннего инструмента судьи")
    return True

def get_driver():
    from neo4j import GraphDatabase
    uris_to_try = [
        NEO4J_URI.replace("neo4j+s://", "neo4j+ssc://"),
        NEO4J_URI,
        NEO4J_URI.replace("neo4j+s://", "bolt+ssc://"),
    ]
    for uri in uris_to_try:
        try:
            driver = GraphDatabase.driver(uri, auth=(NEO4J_USER, NEO4J_PASSWORD))
            driver.verify_connectivity()
            print(f"✅ Neo4j connected via: {uri}")
            return driver
        except Exception as e:
            print(f"⚠️ Failed with {uri}: {str(e)[:100]}")
            continue
    return None

driver = None

# ============================================================================
# DATA MODELS
# ============================================================================

class ProblemRequest(BaseModel):
    problem_description: str
    language: Optional[str] = None

class BankruptcyContextRequest(BaseModel):
    category: str = "bankruptcy"

class ViolationSearchRequest(BaseModel):
    violation_id: str

class TemplateStructureRequest(BaseModel):
    template_id: str = "tpl_cassation_bankruptcy"

class ConsentRequest(BaseModel):
    full_name: Optional[str] = None
    language: str = "RU"

class JudicialSummaryRequest(BaseModel):
    case_text: str
    language: str = "RU"

class JudicialPrecedentSearchRequest(BaseModel):
    keyword: str
    case_type: Optional[str] = None

# ============================================================================
# NEO4J QUERY FUNCTIONS
# ============================================================================

def run_query(query: str, params: dict = {}) -> List[Dict]:
    global driver
    if driver is None:
        driver = get_driver()
    if driver is None:
        raise Exception("Cannot connect to Neo4j")
    try:
        with driver.session() as session:
            results = session.run(query, params)
            return [dict(record) for record in results]
    except Exception as e:
        driver = get_driver()
        if driver:
            with driver.session() as session:
                results = session.run(query, params)
                return [dict(record) for record in results]
        raise e

def query_bankruptcy_context(category: str = "bankruptcy") -> List[Dict]:
    query = """
    MATCH (law:Law {category: $category})-[:HAS_ARTICLE]->(articles:Article)
    OPTIONAL MATCH (articles)-[:REGULATES]->(procedures:Procedure)
    OPTIONAL MATCH (procedures)-[:PROTECTS]->(rights:Right)
    OPTIONAL MATCH (violations:Violation)-[:VIOLATES]->(articles)
    OPTIONAL MATCH (violations)-[:REMEDIED_BY]->(remedy_procedures:Procedure)
    OPTIONAL MATCH (decisions:Decision)-[:CITES]->(articles)
    RETURN 
      law.name as law_name,
      articles.number as article_number,
      articles.title as article_title,
      collect(DISTINCT procedures.name) as procedures,
      collect(DISTINCT rights.name) as rights,
      collect(DISTINCT violations.name) as violations,
      collect(DISTINCT remedy_procedures.name) as remedies,
      collect(DISTINCT decisions.summary) as precedents
    """
    return run_query(query, {"category": category})

def query_violation_search(violation_id: str) -> Optional[Dict]:
    query = """
    MATCH (viol:Violation {id: $violation_id})-[:VIOLATES]->(articles:Article)
    OPTIONAL MATCH (viol)-[:REMEDIED_BY]->(procedures:Procedure)
    OPTIONAL MATCH (articles)<-[:HAS_ARTICLE]-(law:Law)
    OPTIONAL MATCH (decisions:Decision)-[:CITES]->(articles)
    RETURN 
      viol.name as violation_name,
      viol.type as violation_type,
      viol.impact as impact,
      articles.number as article_number,
      articles.title as article_title,
      law.name as governing_law,
      procedures.name as remedy_procedure,
      procedures.timeline as remedy_timeline,
      procedures.deadline_days as deadline_days,
      collect(DISTINCT decisions.summary) as precedents
    """
    records = run_query(query, {"violation_id": violation_id})
    return records[0] if records else None

def query_template_structure(template_id: str) -> Optional[Dict]:
    query = """
    MATCH (template:Template {id: $template_id})
    MATCH (template)-[:SUPPORTS]->(procedures:Procedure)
    RETURN 
      template.name as template_name,
      template.required_sections as sections,
      template.estimated_length_pages as estimated_pages,
      procedures.name as procedure_name,
      procedures.deadline_days as deadline_days,
      template.section_header as header,
      template.section_introduction as introduction,
      template.section_facts as facts,
      template.section_grounds as grounds,
      template.section_requirements as requirements
    """
    records = run_query(query, {"template_id": template_id})
    return records[0] if records else None

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/health")
async def health_check():
    global driver
    neo4j_status = "connected" if driver else "disconnected"
    return {"status": "ok", "service": "Constitutional Assistant", "neo4j": neo4j_status}

@app.get("/languages")
async def languages():
    return {"languages": SUPPORTED_LANGUAGES}

@app.get("/supported-formats")
async def supported_formats():
    return {
        "formats": get_supported_formats(),
        "description": "PDF, Word (.docx/.doc), изображения (OCR), TXT, ODT"
    }

@app.get("/api/consent-text")
async def consent_text(language: str = "RU"):
    lang = normalize_language(language)
    return {"language": lang, "text": get_consent_text(lang)}

@app.post("/api/consent")
@limiter.limit("10/minute")
async def give_consent(request: Request, body: ConsentRequest):
    lang = normalize_language(body.language)
    record = record_consent(full_name=body.full_name, language=lang)
    return {
        "status": "success",
        "message": t("consent_given", lang),
        "consent_id": record["consent_id"],
        "consented_at": record["consented_at"]
    }

@app.get("/api/consent/{consent_id}/download")
async def download_consent(consent_id: str):
    document = generate_consent_document(consent_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Consent record not found")
    return PlainTextResponse(
        content=document,
        headers={"Content-Disposition": f"attachment; filename=consent_{consent_id}.txt"}
    )

@app.post("/api/judicial/summary")
@limiter.limit("20/minute")
async def judicial_summary(request: Request, body: JudicialSummaryRequest, _auth: bool = Depends(check_judicial_access)):
    result = generate_case_summary(body.case_text, language=body.language)
    if not result.get("success"):
        raise HTTPException(status_code=502, detail=result.get("error", "Не удалось составить справку"))
    return result

@app.post("/api/judicial/search-precedents")
@limiter.limit("30/minute")
async def judicial_search_precedents(request: Request, body: JudicialPrecedentSearchRequest, _auth: bool = Depends(check_judicial_access)):
    global driver
    if driver is None:
        driver = get_driver()
    if driver is None:
        raise HTTPException(status_code=500, detail="Нет подключения к Neo4j")
    try:
        results = search_precedents(driver, body.keyword, body.case_type)
        return {"results": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка поиска прецедентов: {str(e)}")

@app.post("/api/feedback")
@limiter.limit("5/minute")
async def submit_feedback(request: Request, body: dict):
    """
    Принимает два типа запросов:
    - { "type": "survey", "data": { ...17 полей опросника... } }
    - { "message": "...", "contact": "...", "language": "RU", "page": "..." }
    """
    if body.get("type") == "survey":
        result = save_survey(body.get("data", {}))
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return {"status": "success", "survey_id": result["survey_id"]}
    else:
        result = save_feedback(
            message=body.get("message", ""),
            contact=body.get("contact"),
            language=body.get("language", "RU"),
            page=body.get("page"),
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return {"status": "success", "feedback_id": result["feedback_id"]}

@app.get("/api/feedback")
async def get_feedback(_auth: bool = Depends(check_admin_access)):
    items = list_feedback()
    return {"count": count_feedback(), "items": items}

@app.get("/api/surveys")
async def get_surveys(_auth: bool = Depends(check_admin_access)):
    """Возвращает все ответы на опросник (type=survey) для панели администратора."""
    items = list_surveys()
    return {"count": count_surveys(), "items": items}

@app.post("/api/upload-document")
@limiter.limit("10/minute")
async def upload_document(request: Request, file: UploadFile = File(...)):
    result = await parse_file(file)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    cleaned = clean_text(result["text"])
    truncated = truncate_text(cleaned, max_chars=15000)
    detected_lang = detect_language(cleaned)
    return {
        "filename": file.filename,
        "file_type": result["file_type"],
        "pages": result["pages"],
        "text_length": len(cleaned),
        "text": truncated,
        "detected_language": detected_lang,
        "success": True
    }

@app.post("/api/bankruptcy-context")
async def get_bankruptcy_context(request: BankruptcyContextRequest):
    try:
        results = query_bankruptcy_context(request.category)
        if not results:
            raise HTTPException(status_code=404, detail="No bankruptcy context found")
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j query error: {str(e)}")

@app.post("/api/violation-search")
async def search_violation(request: ViolationSearchRequest):
    try:
        result = query_violation_search(request.violation_id)
        if not result:
            raise HTTPException(status_code=404, detail=t("violation_not_found", "RU"))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j query error: {str(e)}")

@app.post("/api/template-structure")
async def get_template_structure(request: TemplateStructureRequest):
    try:
        result = query_template_structure(request.template_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Template {request.template_id} not found")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j query error: {str(e)}")

# ============================================================================
# ГЛАВНЫЙ ЭНДПОИНТ — ГЕНЕРАЦИЯ ОБРАЩЕНИЯ
# ============================================================================

@app.post("/generate-appeal")
@limiter.limit("5/minute")
async def generate_appeal(
    request: Request,
    text: Optional[str] = Form(None),
    problem_description: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    is_representative: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    problem_text = text or problem_description or ""
    representative = (is_representative or "").lower() == "true"
    body = ProblemRequest(problem_description=problem_text, language=language)

    lang = normalize_language(body.language) if body.language else detect_language(body.problem_description)

    analysis = analyze_complaint(body.problem_description)

    if not analysis.get("success"):
        return {
            "status": "partial",
            "language": lang,
            "analysis": analysis,
            "message": "Не удалось проанализировать жалобу.",
            "appeal_text": None,
        }

    violation_id = analysis.get("violation_id")
    case_type = analysis.get("case_type")
    reasoning = analysis.get("reasoning")
    within_jurisdiction = analysis.get("within_jurisdiction")

    if within_jurisdiction is False and not violation_id:
        redirect_info = find_relevant_organs(body.problem_description, driver)
        redirect_msg = format_redirect_message(redirect_info, lang=lang)
        return {
            "status": "not_applicable",
            "language": lang,
            "within_jurisdiction": False,
            "violation_id": None,
            "case_type": case_type,
            "reasoning": reasoning,
            "message": t("jurisdiction_check_failed", lang),
            "redirect_detail": redirect_msg,
            "suggested_organs": redirect_info.get("organs", []),
            "appeal_text": None,
        }

    violation_data = None
    template_data = None

    if violation_id:
        try:
            violation_data = query_violation_search(violation_id)
        except Exception as e:
            print(f"⚠️ Neo4j violation lookup failed: {e}")
        try:
            template_data = query_template_structure("tpl_cassation_bankruptcy")
        except Exception as e:
            print(f"⚠️ Neo4j template lookup failed: {e}")

    generation = generate_appeal_text(
        complaint_text=body.problem_description,
        language=lang,
        case_type=case_type,
        reasoning=reasoning,
        violation_data=violation_data,
        template_data=template_data,
        is_representative=representative,
    )

    return {
        "status": "success" if generation.get("success") else "partial",
        "language": lang,
        "within_jurisdiction": within_jurisdiction,
        "violation_id": violation_id,
        "case_type": case_type,
        "reasoning": reasoning,
        "legal_context_found": violation_data is not None,
        "appeal_text": generation.get("appeal_text"),
        "generation_error": generation.get("error"),
    }

# ============================================================================
# STARTUP/SHUTDOWN
# ============================================================================

@app.on_event("startup")
async def startup():
    global driver
    print("🚀 Starting Constitutional Assistant...")
    print(f"📍 Neo4j URI: {NEO4J_URI}")
    driver = get_driver()
    if driver:
        print("✅ Neo4j connected successfully!")
    else:
        print("❌ Neo4j connection failed - will retry on first request")
    print("📚 API documentation: http://127.0.0.1:8000/docs")

@app.on_event("shutdown")
async def shutdown():
    global driver
    if driver:
        driver.close()
    print("Neo4j connection closed")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
