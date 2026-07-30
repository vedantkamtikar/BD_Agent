import os
from typing import List, Optional
from urllib.parse import urlparse
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
import logging

logger = logging.getLogger("GeminiService")
logger.setLevel(logging.WARNING)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('[GeminiService] RETRY %(message)s'))
    logger.addHandler(handler)

import config
from models import Company, Contact, EmailDraft
from services.serper import SerperService


def normalize_domain(url: str) -> str:
    """
    Standardizes a URL or website domain to a clean domain string (e.g., 'stripe.com').
    """
    if not url:
        return ""
    url_str = url.strip().lower()
    # Add scheme if missing so urlparse works properly
    if not url_str.startswith("http://") and not url_str.startswith("https://"):
        url_str = "https://" + url_str
    try:
        parsed = urlparse(url_str)
        domain = parsed.netloc or parsed.path
        if domain.startswith("www."):
            domain = domain[4:]
        # Extract the base domain before any slash or port
        domain = domain.split("/")[0].split(":")[0]
        return domain
    except Exception:
        return url.strip().lower()


# Structured wrapper classes for list-based LLM parsing
class CompanyList(BaseModel):
    companies: List[Company] = Field(description="List of companies discovered during search")


class ContactList(BaseModel):
    contacts: List[Contact] = Field(description="List of contacts discovered for the company")


class GeminiService:
    """
    Service wrapper around ChatGoogleGenerativeAI to perform grounded web searches,
    structured schema parsing, and email drafting.
    """
    def __init__(self):
        """
        Initializes the Gemini LLM for structured output parsing
        and the Serper service for web search.
        """
        # Initialize Serper web search service
        if not config.MOCK_LLM:
            self.serper = SerperService(api_key=config.SERPER_API_KEY)
        else:
            self.serper = None
        
        # Initialize Gemini LLM for structured output extraction and drafting
        self.llm_parse = ChatGoogleGenerativeAI(
            model=config.GEMINI_MODEL,
            api_key=config.GEMINI_API_KEY or "PLACEHOLDER",
            temperature=0.0
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=3, max=15), reraise=True, before_sleep=before_sleep_log(logger, logging.WARNING))
    def search_companies(self, niche: str, location: str, max_results: int = 5, min_revenue: str = "", max_revenue: str = "") -> List[Company]:
        """
        Step 1 (Search): Searches for real companies in the target niche/location using Google search grounding,
        optionally filtering by minimum and maximum revenue range criteria.
        Step 2 (Parse): Converts the unstructured search output into a list of Company Pydantic models.
        """
        rev_parts = []
        if min_revenue: rev_parts.append(f"Min: {min_revenue}")
        if max_revenue: rev_parts.append(f"Max: {max_revenue}")
        revenue_log_str = f" (Revenue Range: {', '.join(rev_parts)})" if rev_parts else ""

        if config.MOCK_LLM:
            print(f"[GeminiService] [MOCK MODE] Simulating company discovery for '{niche}' in '{location}'{revenue_log_str}...")
            niche_slug = niche.replace(" ", "").lower()
            companies = []
            for i in range(1, max_results + 1):
                name = f"Mock {niche.title()} Corp {i}"
                domain = f"mock{niche_slug}{i}.com"
                rev_desc = f" (Revenue: {min_revenue or 'Unspecified'} to {max_revenue or 'Unspecified'})" if (min_revenue or max_revenue) else ""
                companies.append(Company(
                    name=name,
                    domain=domain,
                    industry=niche,
                    description=f"A top-tier firm specializing in {niche} based in {location}{rev_desc}.",
                    source="Mock Local Generator"
                ))
            print(f"[GeminiService] [MOCK MODE] Generated {len(companies)} simulated companies.")
            return companies

        print(f"[GeminiService] Step A: Searching web for {max_results} companies in '{niche}' located in '{location}'{revenue_log_str}...")
        
        query_rev = []
        if min_revenue: query_rev.append(f"minimum annual revenue {min_revenue}")
        if max_revenue: query_rev.append(f"maximum annual revenue {max_revenue}")
        revenue_term = f" {' '.join(query_rev)}" if query_rev else ""

        search_query = f"{niche} companies in {location}{revenue_term} official website"
        raw_markdown = self.serper.search(search_query, num_results=max_results * 3)

        print("[GeminiService] Step B: Extracting structured company list from search output...")
        
        # Bind the CompanyList Pydantic schema to the parser LLM
        structured_llm = self.llm_parse.with_structured_output(CompanyList)
        
        if min_revenue and max_revenue:
            revenue_instruction = f"\nCRITICAL REVENUE CONSTRAINT: Filter and select ONLY companies with estimated annual revenue between '{min_revenue}' and '{max_revenue}'. Exclude companies outside this range."
        elif min_revenue:
            revenue_instruction = f"\nCRITICAL REVENUE CONSTRAINT: Filter and select ONLY companies with estimated annual revenue of at least '{min_revenue}'. Exclude smaller companies below this threshold."
        elif max_revenue:
            revenue_instruction = f"\nCRITICAL REVENUE CONSTRAINT: Filter and select ONLY companies with estimated annual revenue of no more than '{max_revenue}'."
        else:
            revenue_instruction = ""

        parse_prompt = ChatPromptTemplate.from_template(
            "You are an expert B2B market intelligence analyst. Parse the following live Google search results "
            "about companies in the '{niche}' sector in '{location}' into a clean list of "
            "exactly {max_results} structured company objects.{revenue_instruction}\n\n"
            "Web Search Results:\n{markdown}\n\n"
            "For each company, extract:\n"
            "- name: Official company name\n"
            "- domain: Clean base website domain (e.g. 'tatasteel.com')\n"
            "- industry: Primary business vertical\n"
            "- description: Concise 1-sentence summary including revenue scale if mentioned\n"
            "- employee_count: Estimated number of employees (e.g. '500+', '1,000-5,000', '10,000+'). Use 'N/A' if unknown.\n"
            "- founded_year: Year the company was founded (e.g. '2005'). Use 'N/A' if unknown.\n"
            "- headquarters: City and state/region of headquarters (e.g. 'Pune, Maharashtra'). Use 'N/A' if unknown.\n\n"
            "Only include real, currently active companies. Do not invent or fabricate entries."
        )
        
        parser_chain = parse_prompt | structured_llm
        parsed_result: CompanyList = parser_chain.invoke({
            "markdown": raw_markdown,
            "niche": niche,
            "location": location,
            "max_results": str(max_results),
            "revenue_instruction": revenue_instruction
        })
        
        # Normalize domains and deduplicate companies
        seen_keys = set()
        unique_companies = []
        for comp in parsed_result.companies:
            comp.domain = normalize_domain(comp.domain)
            key = (comp.domain or comp.name).lower().strip()
            if key and key not in seen_keys:
                seen_keys.add(key)
                comp.source = f"Serper Search ({niche} in {location})"
                unique_companies.append(comp)
            
        print(f"[GeminiService] Successfully discovered and structured {len(unique_companies)} unique companies.")
        return unique_companies

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=3, max=15), reraise=True, before_sleep=before_sleep_log(logger, logging.WARNING))
    def get_contacts_for_company(self, company: Company, max_contacts: int = 1) -> List[Contact]:
        """
        Step 1 (Search): Searches for executive contacts at a company using Google search grounding.
        Step 2 (Parse): Converts the unstructured contact list into Contact Pydantic models.
        """
        if config.MOCK_LLM:
            print(f"[GeminiService] [MOCK MODE] Simulating contacts search for '{company.name}'...")
            if "nocontact" in company.name.lower():
                print(f"[GeminiService] [MOCK MODE] Simulating no contacts found for '{company.name}'...")
                return []
            contacts = []
            if max_contacts >= 1:
                contacts.append(Contact(
                    name="Sarah Jenkins",
                    title="Founder & CEO",
                    email=f"sarah.jenkins@{company.domain or 'example.com'}",
                    company_name=company.name,
                    company_domain=company.domain
                ))
            if max_contacts >= 2:
                contacts.append(Contact(
                    name="Marcus Chen",
                    title="VP of Engineering",
                    email=f"marcus.chen@{company.domain or 'example.com'}",
                    company_name=company.name,
                    company_domain=company.domain
                ))
            print(f"[GeminiService] [MOCK MODE] Generated {len(contacts)} simulated contacts for '{company.name}'.")
            return contacts

        print(f"[GeminiService] Step A: Searching web for contacts at '{company.name}' (domain: {company.domain or 'unknown'})...")
        
        search_query = f"{company.name} {company.domain or ''} CEO founder executives team leadership contact"
        raw_markdown = self.serper.search(search_query, num_results=10)

        print(f"[GeminiService] Step B: Extracting structured contacts for '{company.name}'...")
        
        structured_llm = self.llm_parse.with_structured_output(ContactList)
        
        parse_prompt = ChatPromptTemplate.from_template(
            "Parse the following web search results about contacts at the company '{company_name}' "
            "into a clean list of up to {max_contacts} structured contact objects.\n\n"
            "Company: {company_name}\n"
            "Company Domain: {company_domain}\n\n"
            "Web Search Results:\n{markdown}\n\n"
            "Extract name, title, and email. Ensure you populate 'company_name' as '{company_name}' "
            "and 'company_domain' as '{company_domain}' for each contact. "
            "If an exact email address is present in the web search results, extract it directly. "
            "If an exact email is not explicitly listed, analyze any sample email addresses in the search snippets for '{company_domain}' to infer the company's specific email naming convention (e.g. firstinitial.lastname@domain, firstname@domain, or firstname.lastname@domain) and construct the contact's email accordingly. "
            "Only include real people who actually work at this company."
        )
        
        parser_chain = parse_prompt | structured_llm
        parsed_result: ContactList = parser_chain.invoke({
            "markdown": raw_markdown,
            "company_name": company.name,
            "company_domain": company.domain or "",
            "max_contacts": str(max_contacts)
        })
        
        # Filter out contacts that have completely empty names or couldn't be parsed
        valid_contacts = [c for c in parsed_result.contacts if c.name and c.name.lower() != "n/a"]
        print(f"[GeminiService] Discovered {len(valid_contacts)} contacts at '{company.name}'.")
        return valid_contacts

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=3, max=15), reraise=True, before_sleep=before_sleep_log(logger, logging.WARNING))
    def draft_outreach_email(self, company: Company, contact: Contact, sender_name: str = "Alex", sender_title: str = "Lead Consultant", tone: str = "formal") -> EmailDraft:
        """
        Drafts a highly personalized B2B cold outreach email targeting a specific contact
        at a target company, matching the EmailDraft schema.
        """
        # Map tone to style instructions
        tone_styles = {
            "formal": "Professional, structured, and courteous. Use formal language and a respectful tone.",
            "conversational": "Friendly, warm, and human-sounding. Write like a real person, not a template. Use casual but professional language.",
            "bold": "Direct, confident, and slightly provocative. Lead with a bold statement or challenge. Be assertive and cut through the noise."
        }
        style_instruction = tone_styles.get(tone, tone_styles["formal"])

        if config.MOCK_LLM:
            print(f"[GeminiService] [MOCK MODE] Simulating outreach email for '{contact.name}' at '{company.name}' (tone: {tone})...")
            return EmailDraft(
                contact_name=contact.name,
                contact_email=contact.email or "unknown@email.com",
                subject=f"Executive Search / Talent Acquisition for {company.name}",
                body=(
                    f"Hi {contact.name},\n\n"
                    f"I'm reaching out from Catenon, a global executive search firm. I noticed {company.name}'s recent work in the {company.industry or 'tech'} sector.\n\n"
                    f"Given your role as {contact.title or 'Executive'}, I wanted to see if you have 10 minutes next Tuesday for a brief chat about your upcoming leadership/hiring needs.\n\n"
                    f"Best,\n{sender_name}\n{sender_title}"
                ),
                company_name=company.name
            )

        print(f"[GeminiService] Drafting outreach email to '{contact.name}' ({contact.title or 'Executive'}) at '{company.name}' (tone: {tone})...")
        
        email_prompt = (
            f"You are a professional B2B cold email copywriter. Write a highly personalized, extremely short, and direct "
            f"outreach email to {contact.name} ({contact.title or 'executive'}) at {company.name} (website: {company.domain or 'unknown'}).\n\n"
            f"The sender is {sender_name} ({sender_title}) from Catenon, a global executive search firm.\n\n"
            f"Guidelines:\n"
            f"1. Subject line: Catchy, highly relevant, and professional. Mention Catenon or Executive Search/Talent Acquisition.\n"
            f"2. Email body: Keep it very short, concise, and direct (under 80 words). State clearly that the sender is from Catenon (global executive search firm) and get straight to the point.\n"
            f"3. Tone & Style: {style_instruction}\n"
            f"4. Sign off with '{sender_name}, {sender_title}' and do not include physical addresses.\n\n"
            f"The output must match the EmailDraft structured output schema."
        )

        structured_llm = self.llm_parse.with_structured_output(EmailDraft)
        email_draft: EmailDraft = structured_llm.invoke(email_prompt)
        
        # Double check fields are populated correctly
        email_draft.contact_name = contact.name
        email_draft.contact_email = contact.email or "unknown@email.com"
        email_draft.company_name = company.name
        
        print(f"[GeminiService] Email drafted successfully (Subject: '{email_draft.subject}').")
        return email_draft
