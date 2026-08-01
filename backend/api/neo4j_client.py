"""
Python client for Constitutional Assistant Neo4j API
Usage from Llama or other Python code
"""

import requests
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class Article:
    """Article from law"""
    number: str
    title: str
    procedures: List[str]
    rights: List[str]
    violations: List[str]
    remedies: List[str]
    precedents: List[str]

@dataclass
class Violation:
    """Violation information"""
    name: str
    type: str
    impact: str
    article_number: str
    article_title: str
    governing_law: str
    remedy_procedure: str
    remedy_timeline: str
    deadline_days: int
    precedents: List[str]

@dataclass
class Template:
    """Complaint template"""
    name: str
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
# CLIENT CLASS
# ============================================================================

class ConstitutionalAssistantClient:
    """Client for Constitutional Assistant Neo4j API"""
    
    def __init__(self, base_url: str = API_URL):
        self.base_url = base_url
        self.session = requests.Session()
    
    def health_check(self) -> bool:
        """Check if API is running"""
        try:
            response = self.session.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception as e:
            print(f"❌ Health check failed: {e}")
            return False
    
    def get_bankruptcy_context(self, category: str = "bankruptcy") -> List[Article]:
        """
        Get full bankruptcy context: articles + procedures + rights + violations + precedents
        
        Args:
            category: Law category (default: "bankruptcy")
        
        Returns:
            List of Article objects with all related information
        """
        try:
            response = self.session.post(
                f"{self.base_url}/api/bankruptcy-context",
                json={"category": category}
            )
            response.raise_for_status()
            
            results = []
            for item in response.json():
                article = Article(
                    number=item.get("article_number"),
                    title=item.get("article_title"),
                    procedures=item.get("procedures", []),
                    rights=item.get("rights", []),
                    violations=item.get("violations", []),
                    remedies=item.get("remedies", []),
                    precedents=item.get("precedents", [])
                )
                results.append(article)
            
            return results
        
        except requests.exceptions.RequestException as e:
            print(f"❌ API error: {e}")
            return []
    
    def search_violation(self, violation_id: str) -> Optional[Violation]:
        """
        Search violation and find remedy
        
        Args:
            violation_id: ID of violation (e.g., "viol_lack_transparency")
        
        Returns:
            Violation object with remedy information
        """
        try:
            response = self.session.post(
                f"{self.base_url}/api/violation-search",
                json={"violation_id": violation_id}
            )
            response.raise_for_status()
            
            data = response.json()
            violation = Violation(
                name=data.get("violation_name"),
                type=data.get("violation_type"),
                impact=data.get("impact"),
                article_number=data.get("article_number"),
                article_title=data.get("article_title"),
                governing_law=data.get("governing_law"),
                remedy_procedure=data.get("remedy_procedure"),
                remedy_timeline=data.get("remedy_timeline"),
                deadline_days=data.get("deadline_days"),
                precedents=data.get("precedents", [])
            )
            
            return violation
        
        except requests.exceptions.RequestException as e:
            print(f"❌ API error: {e}")
            return None
    
    def get_template_structure(self, template_id: str = "tpl_cassation_bankruptcy") -> Optional[Template]:
        """
        Get complaint template structure
        
        Args:
            template_id: ID of template (default: "tpl_cassation_bankruptcy")
        
        Returns:
            Template object with all required sections
        """
        try:
            response = self.session.post(
                f"{self.base_url}/api/template-structure",
                json={"template_id": template_id}
            )
            response.raise_for_status()
            
            data = response.json()
            template = Template(
                name=data.get("template_name"),
                sections=data.get("sections", []),
                estimated_pages=data.get("estimated_pages"),
                procedure_name=data.get("procedure_name"),
                deadline_days=data.get("deadline_days"),
                header=data.get("header"),
                introduction=data.get("introduction"),
                facts=data.get("facts"),
                grounds=data.get("grounds"),
                requirements=data.get("requirements")
            )
            
            return template
        
        except requests.exceptions.RequestException as e:
            print(f"❌ API error: {e}")
            return None
    
    def batch_search_violations(self, violation_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Search multiple violations at once
        
        Args:
            violation_ids: List of violation IDs
        
        Returns:
            List of violation results
        """
        try:
            response = self.session.post(
                f"{self.base_url}/api/batch-violations",
                json={"violation_ids": violation_ids}
            )
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.RequestException as e:
            print(f"❌ API error: {e}")
            return []

# ============================================================================
# USAGE EXAMPLES
# ============================================================================

def example_bankruptcy_context():
    """Example: Get bankruptcy context"""
    client = ConstitutionalAssistantClient()
    
    print("📚 Getting bankruptcy context...")
    articles = client.get_bankruptcy_context()
    
    for article in articles:
        print(f"\n📋 Article {article.number}: {article.title}")
        print(f"   Procedures: {', '.join(article.procedures) if article.procedures else 'None'}")
        print(f"   Rights: {', '.join(article.rights) if article.rights else 'None'}")
        print(f"   Violations: {', '.join(article.violations) if article.violations else 'None'}")
        print(f"   How to fix: {', '.join(article.remedies) if article.remedies else 'None'}")

def example_violation_search():
    """Example: Search specific violation"""
    client = ConstitutionalAssistantClient()
    
    print("🔍 Searching violation: viol_lack_transparency...")
    violation = client.search_violation("viol_lack_transparency")
    
    if violation:
        print(f"\n⚠️  Violation: {violation.name}")
        print(f"   Type: {violation.type}")
        print(f"   Impact: {violation.impact}")
        print(f"   Article: {violation.article_number} - {violation.article_title}")
        print(f"   Law: {violation.governing_law}")
        print(f"   Remedy: {violation.remedy_procedure} (deadline: {violation.deadline_days} дней)")
        print(f"   Precedents: {', '.join(violation.precedents) if violation.precedents else 'None'}")

def example_template():
    """Example: Get template structure"""
    client = ConstitutionalAssistantClient()
    
    print("📝 Getting template structure...")
    template = client.get_template_structure()
    
    if template:
        print(f"\n📄 Template: {template.name}")
        print(f"   Procedure: {template.procedure_name} (deadline: {template.deadline_days} дней)")
        print(f"   Estimated pages: {template.estimated_pages}")
        print(f"   Required sections: {', '.join(template.sections)}")
        print(f"\n   Section structure:")
        print(f"   - Header: {template.header}")
        print(f"   - Introduction: {template.introduction}")
        print(f"   - Facts: {template.facts}")
        print(f"   - Grounds: {template.grounds}")
        print(f"   - Requirements: {template.requirements}")

def example_pipeline():
    """Example: Complete pipeline from violation detection to template"""
    client = ConstitutionalAssistantClient()
    
    print("\n" + "="*70)
    print("🚀 CONSTITUTIONAL ASSISTANT PIPELINE EXAMPLE")
    print("="*70)
    
    # Step 1: Check if API is running
    print("\n1️⃣ Health check...")
    if not client.health_check():
        print("❌ API is not running. Start it with: python fastapi_neo4j_server.py")
        return
    print("✅ API is running!")
    
    # Step 2: User says: "мне не дают информацию"
    print("\n2️⃣ User input: 'мне не дают информацию о банкротстве'")
    
    # Step 3: Llama determines violation
    violation_id = "viol_lack_transparency"
    print(f"   Llama determined: violation_id = {violation_id}")
    
    # Step 4: Search violation in Neo4j
    print("\n3️⃣ Searching violation in Neo4j...")
    violation = client.search_violation(violation_id)
    
    if violation:
        print(f"   ✅ Found violation: {violation.name}")
        print(f"   Article: {violation.article_number}")
        print(f"   Remedy: {violation.remedy_procedure}")
        print(f"   Deadline: {violation.deadline_days} days")
    
    # Step 5: Get template for complaint
    print("\n4️⃣ Getting template structure...")
    template = client.get_template_structure()
    
    if template:
        print(f"   ✅ Template: {template.name}")
        print(f"   Sections: {len(template.sections)}")
    
    # Step 6: Ready for Gemini
    print("\n5️⃣ Ready for Gemini 2.5 Pro to generate complaint...")
    print("   Input data:")
    print(f"   - Violation: {violation.name if violation else 'N/A'}")
    print(f"   - Article: {violation.article_number if violation else 'N/A'}")
    print(f"   - Template: {template.name if template else 'N/A'}")
    print(f"   - Deadline: {template.deadline_days if template else 'N/A'} days")
    
    print("\n✅ Pipeline complete! Ready to generate complaint.")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "context":
            example_bankruptcy_context()
        elif command == "violation":
            example_violation_search()
        elif command == "template":
            example_template()
        elif command == "pipeline":
            example_pipeline()
        else:
            print(f"Unknown command: {command}")
    else:
        # Run all examples
        example_pipeline()
