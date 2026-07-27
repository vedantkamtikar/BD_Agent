import os
from typing import List, Optional
from urllib.parse import urlparse
from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from tenacity import retry, stop_after_attempt, wait_exponential

import config
from models import Company, Contact, EmailDraft


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
        # Initialize Gemini LLM for search grounding (requires API key)
        # Google search grounding tool works best with gemini-2.0-flash
        self.llm_search = ChatGoogleGenerativeAI(
            model=config.GEMINI_MODEL,
            api_key=config.GEMINI_API_KEY or "PLACEHOLDER",
            temperature=0.2
        )
        
        # Initialize Gemini LLM for structured output extraction and drafting
        self.llm_parse = ChatGoogleGenerativeAI(
            model=config.GEMINI_MODEL,
            api_key=config.GEMINI_API_KEY or "PLACEHOLDER",
            temperature=0.0
        )

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=60), reraise=True)
    def search_companies(self, niche: str, location: str, max_results: int = 5) -> List[Company]:
        """
        Step 1 (Search): Searches for real companies in the target niche/location using Google search grounding.
        Step 2 (Parse): Converts the unstructured search output into a list of Company Pydantic models.
        """
        if config.MOCK_LLM:
            print(f"[GeminiService] [MOCK MODE] Simulating company discovery for '{niche}' in '{location}'...")
            niche_slug = niche.replace(" ", "").lower()
            companies = []
            for i in range(1, max_results + 1):
                name = f"Mock {niche.title()} Corp {i}"
                domain = f"mock{niche_slug}{i}.com"
                companies.append(Company(
                    name=name,
                    domain=domain,
                    industry=niche,
                    description=f"A top-tier firm specializing in {niche} based in {location}.",
                    source="Mock Local Generator"
                ))
            print(f"[GeminiService] [MOCK MODE] Generated {len(companies)} simulated companies.")
            return companies

        print(f"[GeminiService] Step A: Searching web for {max_results} companies in '{niche}' located in '{location}'...")
        
        search_prompt = (
            f"Perform a search to find {max_results} real, active companies in the '{niche}' industry/niche "
            f"located in '{location}'. For each company, find its official name, website domain/URL, "
            f"industry category, and a short description of what they do. "
            f"You must use Google Search to verify their actual existence and get their correct website domain."
        )

        # Enable Gemini native Google Search grounding tool
        response = self.llm_search.invoke(
            search_prompt,
            tools=[{"google_search": {}}]
        )
        raw_markdown = response.content

        print("[GeminiService] Step B: Extracting structured company list from search output...")
        
        # Bind the CompanyList Pydantic schema to the parser LLM
        structured_llm = self.llm_parse.with_structured_output(CompanyList)
        
        parse_prompt = ChatPromptTemplate.from_template(
            "You are an expert data extraction assistant. Parse the following web search report "
            "about companies in the target niche into a clean list of structured company objects.\n\n"
            "Web Search Report:\n{markdown}\n\n"
            "Extract name, website/domain, industry, and description. For domains, extract the base website domain "
            "(e.g., 'stripe.com' instead of 'https://www.stripe.com/us')."
        )
        
        parser_chain = parse_prompt | structured_llm
        parsed_result: CompanyList = parser_chain.invoke({"markdown": raw_markdown})
        
        # Normalize domains post-extraction to guarantee clean matching
        normalized_companies = []
        for comp in parsed_result.companies:
            comp.domain = normalize_domain(comp.domain)
            comp.source = f"Google Grounded Search ({niche} in {location})"
            normalized_companies.append(comp)
            
        print(f"[GeminiService] Successfully discovered and structured {len(normalized_companies)} companies.")
        return normalized_companies

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=60), reraise=True)
    def get_contacts_for_company(self, company: Company, max_contacts: int = 2) -> List[Contact]:
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
        
        search_prompt = (
            f"Perform a web search to find up to {max_contacts} key contact persons (such as CEO, Founder, Co-Founder, "
            f"VP of Sales, or Marketing Director) currently working at '{company.name}' (website/domain: {company.domain or 'unknown'}). "
            f"For each contact, find their name, job title, and their professional/business email address. "
            f"Use Google Search to verify their actual names and current roles at the company."
        )

        response = self.llm_search.invoke(
            search_prompt,
            tools=[{"google_search": {}}]
        )
        raw_markdown = response.content

        print(f"[GeminiService] Step B: Extracting structured contacts for '{company.name}'...")
        
        structured_llm = self.llm_parse.with_structured_output(ContactList)
        
        parse_prompt = ChatPromptTemplate.from_template(
            "Parse the following web search report about contacts at the company '{company_name}' "
            "into a clean list of structured contact objects.\n\n"
            "Company: {company_name}\n"
            "Company Domain: {company_domain}\n\n"
            "Web Search Report:\n{markdown}\n\n"
            "Extract name, title, and email. Ensure you populate 'company_name' as '{company_name}' "
            "and 'company_domain' as '{company_domain}' for each contact. If email is not found, leave it as null."
        )
        
        parser_chain = parse_prompt | structured_llm
        parsed_result: ContactList = parser_chain.invoke({
            "markdown": raw_markdown,
            "company_name": company.name,
            "company_domain": company.domain or ""
        })
        
        # Filter out contacts that have completely empty names or couldn't be parsed
        valid_contacts = [c for c in parsed_result.contacts if c.name and c.name.lower() != "n/a"]
        print(f"[GeminiService] Discovered {len(valid_contacts)} contacts at '{company.name}'.")
        return valid_contacts

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=5, max=60), reraise=True)
    def draft_outreach_email(self, company: Company, contact: Contact) -> EmailDraft:
        """
        Drafts a highly personalized B2B cold outreach email targeting a specific contact
        at a target company, matching the EmailDraft schema.
        """
        if config.MOCK_LLM:
            print(f"[GeminiService] [MOCK MODE] Simulating outreach email for '{contact.name}' at '{company.name}'...")
            return EmailDraft(
                contact_name=contact.name,
                contact_email=contact.email or "unknown@email.com",
                subject=f"Optimizing development at {company.name}",
                body=(
                    f"Hi {contact.name},\n\n"
                    f"I came across {company.name} and noticed your work. As {contact.title or 'Executive'}, "
                    f"I thought you'd want to know how we help teams like yours accelerate growth in the {company.industry or 'tech'} vertical.\n\n"
                    f"Do you have 10 minutes for a brief call next Tuesday?\n\n"
                    f"Best,\nAlex"
                ),
                company_name=company.name
            )

        print(f"[GeminiService] Drafting outreach email to '{contact.name}' ({contact.title or 'Executive'}) at '{company.name}'...")
        
        email_prompt = (
            f"You are a professional B2B cold email copywriter. Write a highly personalized, friendly, and brief "
            f"outreach email to {contact.name} ({contact.title or 'executive'}) at {company.name} (website: {company.domain or 'unknown'}).\n\n"
            f"Use the company's description to customize your hook: {company.description or 'A business in the industry.'}\n\n"
            f"Guidelines:\n"
            f"1. Subject line: Catchy, highly relevant, and professional. Do not use spam words.\n"
            f"2. Email body: Keep it under 120 words. Open with a personalized observation about their business, "
            f"briefly introduce our value (partner growth agency), and end with a direct, low-friction call-to-action "
            f"(e.g., asking for a quick 10-minute chat next Tuesday).\n"
            f"3. Style: Personal, warm, not sounding like an automated template.\n"
            f"4. Do NOT use generic placeholders like [Your Name] or [My Company] for the sender info. "
            f"Sign off simply as 'Alex, Lead Consultant' and do not include physical addresses.\n\n"
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
