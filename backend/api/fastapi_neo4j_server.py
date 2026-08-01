"""
FastAPI server with Neo4j endpoints for Constitutional Assistant
Retrieval queries for Llama -> Neo4j -> Gemini pipeline
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from neo4j import GraphDatabase
from typing import List, Dict, Any, Optional
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI(
    title="Constitutional Assistant Neo4j API",
    description="Retrieval queries for Constitutional Court appeals",
    version="1.0.0"
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Neo4j connection
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j+s://9538fb38.databases.neo4j.io")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "your-password-here")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# ============================================================================
# DATA MODELS
# ============================================================================

class BankruptcyContextRequest(BaseModel):
    """Request for bankruptcy context"""
    category: str = "bankruptcy"

class ViolationSearchRequest(BaseModel):
    """Request for violation search"""
    violation_id: str

class TemplateStructureRequest(BaseModel):
    """Request for template structure"""
    template_id: str = "tpl_cassation_bankruptcy"

class BankruptcyContextResponse(BaseModel):
    """Response with bankruptcy context"""
    law_name: str
    article_number: str
    article_title: str
    procedures: List[str]
    rights: List[str]
    violations: List[str]
    remedies: List[str]
    precedents: List[str]

class ViolationSearchResponse(BaseModel):
    """Response with violation search results"""
    violation_name: str
    violation_type: str
    impact: str
    article_number: str
    article_title: str
    governing_law: str
    remedy_procedure: str
    remedy_timeline: str
    deadline_days: int
    precedents: List[str]

class TemplateStructureResponse(BaseModel):
    """Response with template structure"""
    template_name: str
    sections: List[str]
    estimated_pages: int
    procedure_name: str
    deadline_days: int
    header: str
    introduction: str
    facts: str
    grounds: str
    requirements: str

# ============================================================================
# NEO4J QUERY FUNCTIONS
# ============================================================================

def query_bankruptcy_context(category: str = "bankruptcy") -> List[Dict[str, Any]]:
    """
    Query 1: Полный контекст банкротства
    Returns all articles with procedures, rights, violations, and precedents
    """
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
    
    with driver.session() as session:
        results = session.run(query, category=category)
        return [dict(record) for record in results]

def query_violation_search(violation_id: str) -> Optional[Dict[str, Any]]:
    """
    Query 2: Поиск по нарушению
    Returns specific violation with article, law, remedy, and precedents
    """
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
    
    with driver.session() as session:
        results = session.run(query, violation_id=violation_id)
        records = [dict(record) for record in results]
        return records[0] if records else None

def query_template_structure(template_id: str) -> Optional[Dict[str, Any]]:
    """
    Query 3: Шаблон жалобы
    Returns template structure with required sections and procedures
    """
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
    
    with driver.session() as session:
        results = session.run(query, template_id=template_id)
        records = [dict(record) for record in results]
        return records[0] if records else None

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "service": "Constitutional Assistant Neo4j API",
        "neo4j": "connected"
    }

@app.post("/api/bankruptcy-context", response_model=List[BankruptcyContextResponse])
async def get_bankruptcy_context(request: BankruptcyContextRequest):
    """
    Get full bankruptcy context: articles + procedures + rights + violations + precedents
    
    Usage:
    ```
    POST /api/bankruptcy-context
    {"category": "bankruptcy"}
    ```
    
    Returns list of articles with all related information
    """
    try:
        results = query_bankruptcy_context(request.category)
        if not results:
            raise HTTPException(status_code=404, detail="No bankruptcy context found")
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j query error: {str(e)}")

@app.post("/api/violation-search", response_model=ViolationSearchResponse)
async def search_violation(request: ViolationSearchRequest):
    """
    Search violation and find remedy: article + law + remedy procedure + precedents
    
    Available violations:
    - viol_lack_transparency (Отсутствие прозрачности от управляющего)
    - viol_exceeded_deadline (Превышение максимального срока банкротства)
    - viol_no_public_hearing (Отсутствие открытого судебного заседания)
    
    Usage:
    ```
    POST /api/violation-search
    {"violation_id": "viol_lack_transparency"}
    ```
    
    Returns detailed violation analysis with remedy information
    """
    try:
        result = query_violation_search(request.violation_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Violation {request.violation_id} not found")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j query error: {str(e)}")

@app.post("/api/template-structure", response_model=TemplateStructureResponse)
async def get_template_structure(request: TemplateStructureRequest):
    """
    Get complaint template structure: sections + procedure + deadline
    
    Available templates:
    - tpl_cassation_bankruptcy (Кассационная жалоба по банкротству)
    
    Usage:
    ```
    POST /api/template-structure
    {"template_id": "tpl_cassation_bankruptcy"}
    ```
    
    Returns template with all required sections and procedures
    """
    try:
        result = query_template_structure(request.template_id)
        if not result:
            raise HTTPException(status_code=404, detail=f"Template {request.template_id} not found")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j query error: {str(e)}")

# ============================================================================
# BATCH OPERATIONS
# ============================================================================

class BatchRequest(BaseModel):
    """Batch request for multiple violations"""
    violation_ids: List[str]

@app.post("/api/batch-violations")
async def batch_violation_search(request: BatchRequest):
    """
    Search multiple violations at once
    
    Usage:
    ```
    POST /api/batch-violations
    {
      "violation_ids": ["viol_lack_transparency", "viol_exceeded_deadline"]
    }
    ```
    
    Returns array of violation analyses
    """
    try:
        results = []
        for violation_id in request.violation_ids:
            result = query_violation_search(violation_id)
            if result:
                results.append(result)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neo4j query error: {str(e)}")

# ============================================================================
# STARTUP/SHUTDOWN
# ============================================================================

@app.on_event("startup")
async def startup():
    """Initialize Neo4j connection"""
    try:
        driver.verify_connectivity()
        print("✅ Neo4j connected successfully!")
    except Exception as e:
        print(f"❌ Neo4j connection failed: {e}")

@app.on_event("shutdown")
async def shutdown():
    """Close Neo4j connection"""
    driver.close()
    print("Neo4j connection closed")

# ============================================================================
# RUN SERVER
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    print("🚀 Starting Constitutional Assistant Neo4j API...")
    print(f"📍 Neo4j URI: {NEO4J_URI}")
    print("📚 API documentation: http://127.0.0.1:8000/docs")
    
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        reload=True
    )
