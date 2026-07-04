from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import os
import json
from io import BytesIO
import re
import aiohttp
import asyncio
from dotenv import load_dotenv
import json

# PDF Generation
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

# Initialize FastAPI
app = FastAPI(title="Company Research Assistant API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============ DATA MODELS ============
class CompanyInput(BaseModel):
    company_input: str
    model: str = "meta-llama/llama-3.3-70b-instruct"  # OpenRouter model
    applicant_name: Optional[str] = None
    applicant_email: Optional[str] = None

class ResearchResult(BaseModel):
    company_name: str
    website: str
    phone: Optional[str]
    address: Optional[str]
    products_services: str
    ai_pain_points: List[str]
    competitors: List[dict]
    summary: str

# ============ API KEYS & CONFIG ============
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

if not SERPER_API_KEY:
    print("⚠️  Warning: SERPER_API_KEY not set")
if not OPENROUTER_API_KEY:
    print("⚠️  Warning: OPENROUTER_API_KEY not set")

# ============ UTILITY FUNCTIONS ============

def is_valid_url(string: str) -> bool:
    """Check if string is valid URL"""
    try:
        result = urlparse(string)
        return all([result.scheme, result.netloc])
    except:
        return False

def resolve_company_website(company_input: str) -> str:
    """
    If URL given, return it.
    If company name given, use Serper to find official website.
    """
    if is_valid_url(company_input):
        # Ensure https
        if not company_input.startswith(('http://', 'https://')):
            return 'https://' + company_input
        return company_input
    
    # Search for company website using Serper
    try:
        headers = {
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "q": f"{company_input} official website"
        }
        
        response = requests.post(
            "https://google.serper.dev/search",
            headers=headers,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if "organic" in data and len(data["organic"]) > 0:
                return data["organic"][0]["link"]
        
        # Fallback: guess .com domain
        return f"https://www.{company_input.lower().replace(' ', '')}.com"
    
    except Exception as e:
        print(f"Serper search error: {e}")
        return f"https://www.{company_input.lower().replace(' ', '')}.com"

def scrape_website(url: str, max_pages: int = 5) -> dict:
    """
    Scrape company website, discover important pages
    Returns: dict with content and metadata
    """
    try:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Get main page
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract main content
        main_text = extract_text(soup)
        
        # Find important pages to crawl
        pages_to_crawl = find_important_pages(soup, url)[:max_pages]
        
        all_content = main_text
        
        # Crawl important pages
        for page_url in pages_to_crawl:
            try:
                page_response = requests.get(page_url, timeout=10, headers=headers)
                page_soup = BeautifulSoup(page_response.content, 'html.parser')
                all_content += " " + extract_text(page_soup)
            except:
                continue
        
        # Extract company info
        company_name = soup.find('title')
        company_name = company_name.string if company_name else urlparse(url).netloc
        
        # Remove duplicates and limit length
        sentences = all_content.split('.')
        unique_sentences = []
        seen = set()
        for s in sentences[:100]:
            s = s.strip()
            if s and len(s) > 10 and s not in seen:
                unique_sentences.append(s)
                seen.add(s)
        
        final_content = '. '.join(unique_sentences)[:4000]
        
        return {
            "url": url,
            "company_name": company_name,
            "content": final_content,
            "pages_crawled": len(pages_to_crawl),
            "status": "success"
        }
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error scraping website: {str(e)}")

def extract_text(soup) -> str:
    """Extract clean text from BeautifulSoup object"""
    for script in soup(['script', 'style']):
        script.decompose()
    
    text = soup.get_text()
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    return ' '.join(chunk for chunk in chunks if chunk)

def find_important_pages(soup, base_url: str) -> List[str]:
    """Find important pages like About, Products, Services, Contact, Pricing"""
    important_keywords = ['about', 'products', 'services', 'solutions', 'contact', 'pricing', 'features']
    found_urls = set()
    
    for link in soup.find_all('a', href=True):
        href = link['href'].lower()
        text = link.get_text().lower()
        
        # Check if link contains important keywords
        if any(keyword in href or keyword in text for keyword in important_keywords):
            # Convert relative to absolute URL
            absolute_url = urljoin(base_url, link['href'])
            # Only add if same domain
            if urlparse(absolute_url).netloc == urlparse(base_url).netloc:
                found_urls.add(absolute_url)
    
    return list(found_urls)[:5]

def search_serper(query: str) -> List[dict]:
    """
    Search using Serper.dev API
    Returns list of results with title, link, snippet
    """
    try:
        headers = {
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {"q": query}
        
        response = requests.post(
            "https://google.serper.dev/search",
            headers=headers,
            json=payload,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            results = []
            if "organic" in data:
                for result in data["organic"][:5]:
                    results.append({
                        "title": result.get("title", ""),
                        "link": result.get("link", ""),
                        "snippet": result.get("snippet", "")
                    })
            return results
        return []
    except Exception as e:
        print(f"Serper error: {e}")
        return []

def analyze_with_openrouter(company_name: str, website_content: str, model: str) -> dict:
    """
    Use OpenRouter API to analyze company and generate insights
    """
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = f"""You are a professional business analyst. Analyze the following website content for {company_name} and provide:

WEBSITE CONTENT:
{website_content[:3000]}

Please provide a JSON response with ONLY these fields:
{{
    "company_summary": "2-3 sentence summary of what the company does",
    "products_services": "comma-separated list of main products/services",
    "pain_points": "3-4 AI-identified pain points ... (format as bullet points)"
    "industry": "the industry/sector",
    "key_strengths": "2-3 key competitive advantages"
}}

Return ONLY valid JSON, no markdown, no extra text.
"""
        
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 1000
        }
        
        response = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            response_text = data['choices'][0]['message']['content']
            
            # Parse JSON
            try:
                start = response_text.find('{')
                end = response_text.rfind('}') + 1
                if start != -1 and end > start:
                    json_str = response_text[start:end]
                    analysis = json.loads(json_str)
                else:
                    analysis = json.loads(response_text)
            except:
                analysis = {
                    "company_summary": response_text[:200],
                    "products_services": "Information gathered from website",
                    "pain_points": "• Market analysis needed\n• Competitive positioning",
                    "industry": "Not specified",
                    "key_strengths": "Website presence and digital capabilities"
                }
            
            return analysis
        else:
            raise Exception(f"OpenRouter error: {response.text}")
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI analysis error: {str(e)}")

def find_competitors(company_name: str, industry: str) -> List[dict]:
    """
    Find competitors using Serper.dev
    """
    try:
        query = f"{industry} competitors similar to {company_name}"
        results = search_serper(query)
        
        competitors = []
        for result in results[:4]:
            # Filter out the original company
            if company_name.lower() not in result['title'].lower():
                competitors.append({
                    "name": result.get("title", "").split('-')[0].strip(),
                    "website": result.get("link", "")
                })
        
        return competitors[:3]  # Return top 3 competitors
    
    except Exception as e:
        print(f"Competitor search error: {e}")
        return []

def generate_pdf(research_result: ResearchResult, applicant_name: Optional[str] = None) -> bytes:
    """
    Generate professional PDF report
    Returns PDF as bytes
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1e40af'),
        spaceAfter=30,
        alignment=TA_CENTER
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#1e40af'),
        spaceAfter=12,
        spaceBefore=12
    )
    
    # Title
    elements.append(Paragraph("Company Research Report", title_style))
    elements.append(Spacer(1, 0.2*inch))
    
    # Company Information Section
    elements.append(Paragraph("Company Information", heading_style))
    
    normal = styles["BodyText"]

    company_data = [
        [
            Paragraph("<b>Field</b>", normal),
            Paragraph("<b>Details</b>", normal),
        ],
        [
            Paragraph("Company Name", normal),
            Paragraph(research_result.company_name, normal),
        ],
        [
            Paragraph("Website", normal),
            Paragraph(research_result.website, normal),
        ],
        [
            Paragraph("Phone", normal),
            Paragraph(research_result.phone or "N/A", normal),
        ],
        [
            Paragraph("Address", normal),
            Paragraph(research_result.address or "N/A", normal),
        ],
        [
            Paragraph("Products / Services", normal),
            Paragraph(research_result.products_services, normal),
        ],
    ]
    
    company_table = Table(company_data, colWidths=[2*inch, 4*inch])
    company_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dbeafe')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 11),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black)
    ]))
    
    elements.append(company_table)
    elements.append(Spacer(1, 0.3*inch))
    
    # Summary
    elements.append(Paragraph("Executive Summary", heading_style))
    elements.append(Paragraph(research_result.summary[:500], styles['Normal']))
    elements.append(Spacer(1, 0.2*inch))
    
    # Pain Points
    
    elements.append(Paragraph("AI-Generated Pain Points", heading_style))

    pain_points = research_result.ai_pain_points

    if isinstance(pain_points, list):
        pain_points_text = "<br/>".join([f"• {p}" for p in pain_points])
    else:
        pain_points_text = str(pain_points)

    elements.append(Paragraph(pain_points_text, styles["Normal"]))
    elements.append(Spacer(1, 0.3*inch))
    
    # Competitors
    if research_result.competitors:
        elements.append(PageBreak())
        elements.append(Paragraph("Competitor Analysis", heading_style))
        
        competitor_data = [["Competitor Name", "Website"]]
        for comp in research_result.competitors:
            competitor_data.append([comp["name"], comp["website"]])
        
        comp_table = Table(competitor_data, colWidths=[3*inch, 3*inch])
        comp_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dbeafe')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        
        elements.append(comp_table)
    
    # Footer
    elements.append(Spacer(1, 0.5*inch))
    if applicant_name:
        elements.append(Paragraph(f"Report generated for: {applicant_name}", styles['Normal']))
    elements.append(Paragraph("Generated by: Company Research Assistant", styles['Normal']))
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()

import aiohttp

async def send_discord_message(
    bot_token: str,
    channel_id: str,
    applicant_name: str,
    applicant_email: str,
    company_name: str,
    website: str,
    pdf_bytes: bytes
):
    """
    Send research report to Discord with PDF attachment.
    """

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"

    headers = {
        "Authorization": f"Bot {bot_token}"
    }

    message = f"""
📊 **Company Research Report**

👤 **Applicant:** {applicant_name}
📧 **Email:** {applicant_email}

🏢 **Company:** {company_name}
🌐 **Website:** {website}
"""

    form = aiohttp.FormData()

    form.add_field(
        "payload_json",
        json.dumps({
            "content": message
        }),
        content_type="application/json"
    )

    form.add_field(
        "files[0]",
        pdf_bytes,
        filename=f"{company_name}_Report.pdf",
        content_type="application/pdf"
    )

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            headers=headers,
            data=form
        ) as response:

            text = await response.text()

            if response.status in [200, 201]:
                print("✅ Discord message sent!")
                return {
                    "success": True,
                    "message": "Discord message sent"
                }

            print("Discord Error:", response.status)
            print(text)

            return {
                "success": False,
                "message": text
            }

# ============ API ENDPOINTS ============

@app.get("/api")
def health_check():
    return {
        "status": "Company Research Assistant API running",
        "version": "1.0"
    }

@app.get("/api/models")
def get_available_models():
    """Get list of available OpenRouter models"""
    return {
        "models": [
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
            {"id": "claude-3.5-sonnet", "name": "Claude 3.5 Sonnet"},
            {"id": "gemini-pro", "name": "Gemini Pro"},
            {"id": "mistral-small", "name": "Mistral Small"},
        ]
    }

@app.post("/api/research")
async def research_company(input_data: CompanyInput) -> dict:
    """
    Main research endpoint
    
    Input:
    {
        "company_input": "Microsoft" or "https://microsoft.com",
        "model": "gpt-4o-mini",
        "discord_token": "optional",
        "discord_channel_id": "optional",
        "applicant_name": "John Doe",
        "applicant_email": "john@example.com"
    }
    """
    
    if not input_data.company_input:
        raise HTTPException(status_code=400, detail="Company name or URL required")
    
    company_input = input_data.company_input.strip()
    
    try:
        # Step 1: Resolve company URL
        website_url = resolve_company_website(company_input)
        
        # Step 2: Scrape website
        scraped = scrape_website(website_url)
        
        # Step 3: Search for additional info
        additional_info = search_serper(f"{company_input} company info phone address")
        
        # Step 4: AI Analysis with OpenRouter
        analysis = analyze_with_openrouter(
            scraped["company_name"],
            scraped["content"],
            input_data.model
        )
        
        # Step 5: Find competitors
        industry = analysis.get("industry", "Technology")
        competitors = find_competitors(company_input, industry)
        
        # Step 6: Create result object
            
        pain_points = analysis.get("pain_points", [])

        if isinstance(pain_points, str):
            pain_points = [
                p.strip("• ").strip()
                for p in pain_points.split("\n")
                if p.strip()
            ]
        research_result = ResearchResult(
            company_name=scraped["company_name"],
            website=website_url,
            phone=None,
            address=None,
            products_services=analysis.get("products_services", "Not available"),
            ai_pain_points=pain_points,
            competitors=competitors,
            summary=analysis.get("company_summary", "Company information gathered from website")
        )
    
        
        # Step 7: Generate PDF
        pdf_bytes = generate_pdf(research_result, input_data.applicant_name)
        
        # Step 8: Send to Discord if configured
        discord_status = None
        if DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID:
            discord_status = await send_discord_message(
                DISCORD_BOT_TOKEN,
                DISCORD_CHANNEL_ID,
                input_data.applicant_name or "Unknown",
                input_data.applicant_email or "unknown@example.com",
                research_result.company_name,
                website_url,
                pdf_bytes
            )
        
        return {
            "status": "success",
            "data": research_result.dict(),
            "pdf_size": len(pdf_bytes),
            "discord": discord_status,
            "pages_crawled": scraped["pages_crawled"]
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Research error: {str(e)}")

@app.post("/api/research/pdf")
async def get_pdf(input_data: CompanyInput):
    """
    Get PDF directly without storing it
    """
    
    try:
        # Run research
        result = await research_company(input_data)
        
        # Generate PDF
        research_obj = ResearchResult(**result["data"])
        pdf_bytes = generate_pdf(research_obj, input_data.applicant_name)
        
        return {
            "status": "success",
            "pdf_base64": __import__('base64').b64encode(pdf_bytes).decode('utf-8'),
            "filename": f"{result['data']['company_name'].replace(' ', '_')}_report.pdf"
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============ LOCAL TESTING ============
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app,  port=8000)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "company_research_complete:app",
        host="0.0.0.0",
        port=8000,
        reload=True
)

