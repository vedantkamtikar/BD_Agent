import re
import logging
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

import config
from models import Company, Contact, EmailDraft
from services.serper import (
    SerperService,
    extract_emails_from_text,
    is_matching_contact_email,
    is_generic_email
)

logger = logging.getLogger("GeminiService")
logger.setLevel(logging.WARNING)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('[GeminiService] RETRY %(message)s'))
    logger.addHandler(handler)

NON_PERSON_KEYWORDS = {
    "board", "directors", "management", "team", "inc", "ltd", "corp", "llc",
    "group", "services", "solutions", "officer", "support", "inquiry", "inquiries",
    "sales", "general", "department", "company", "enterprise", "advisory",
    "investor", "relations", "leadership", "headquarters", "contact", "about",
    "staff", "careers", "press", "media", "privacy", "security", "customer",
    "service", "committee", "administration", "global", "international"
}


def is_valid_person_name(name: str) -> bool:
    """
    Validates that a contact name represents a real individual rather than
    a generic department, board, or corporate placeholder.
    """
    if not name or name.strip().lower() in ("n/a", "unknown", "none", "null"):
        return False
    # Strip common honorifics
    cleaned = re.sub(r'^(dr\.|mr\.|ms\.|mrs\.|prof\.)\s+', '', name.strip(), flags=re.IGNORECASE)
    tokens = [t.lower() for t in cleaned.split() if re.sub(r'[^a-zA-Z]', '', t)]
    if len(tokens) < 2 or len(tokens) > 5:
        return False
    # Reject if any token is a corporate placeholder / non-person keyword
    if any(tok in NON_PERSON_KEYWORDS for tok in tokens):
        return False
    return True


LINKEDIN_URL_REGEX = re.compile(r'https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/[a-zA-Z0-9%\-_]+/?', re.IGNORECASE)


def extract_linkedin_urls_from_text(text: str) -> List[str]:
    """Extracts clean LinkedIn user profile URLs from search text snippets."""
    if not text:
        return []
    matches = LINKEDIN_URL_REGEX.findall(text)
    return list(dict.fromkeys([m.rstrip('/') for m in matches]))


def get_role_priority_score(title: str) -> int:
    """Assigns priority score: CEO (1) > MD (2) > CHRO (3) > Other (4)."""
    t = (title or "").lower()
    if any(k in t for k in ["ceo", "chief executive officer", "founder"]):
        return 1
    elif any(k in t for k in ["managing director", "md"]):
        return 2
    elif any(k in t for k in ["chro", "chief human resources officer", "hr head", "vp hr", "head of human resources"]):
        return 3
    return 4




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
                companies.append(Company(
                    name=name,
                    domain=domain,
                    industry=niche,
                    employee_count="500+",
                    headquarters=location,
                    source="Mock Local Generator"
                ))
            print(f"[GeminiService] [MOCK MODE] Generated {len(companies)} simulated companies.")
            return companies

        print(f"[GeminiService] Step A: Searching web for {max_results} '{niche}' companies headquartered in '{location}'{revenue_log_str}...")
        
        query_rev = []
        if min_revenue: query_rev.append(f"minimum annual revenue {min_revenue}")
        if max_revenue: query_rev.append(f"maximum annual revenue {max_revenue}")
        revenue_term = f" {' '.join(query_rev)}" if query_rev else ""

        search_query = f"{niche} companies headquartered in {location}{revenue_term} official website"
        raw_markdown = self.serper.search(search_query, num_results=max_results * 3)

        # Phase 2: If revenue constraints are specified, run a second dedicated revenue search
        # to give the LLM actual financial data to work with
        has_revenue_filter = bool(min_revenue or max_revenue)
        if has_revenue_filter:
            rev_search_query = f"{niche} companies {location} annual revenue turnover financials"
            print(f"[GeminiService] Step A2: Running dedicated revenue search: '{rev_search_query}'...")
            revenue_markdown = self.serper.search(rev_search_query, num_results=max_results * 2)
            raw_markdown += "\n\n### Revenue & Financial Data (Supplementary Search)\n" + revenue_markdown

        print("[GeminiService] Step B: Extracting structured company list from search output...")
        
        # Bind the CompanyList Pydantic schema to the parser LLM
        structured_llm = self.llm_parse.with_structured_output(CompanyList)
        
        if min_revenue and max_revenue:
            revenue_instruction = (
                f"\n\nMANDATORY REVENUE FILTER (HARD GATE)\n"
                f"You MUST only include companies with estimated annual revenue BETWEEN '{min_revenue}' and '{max_revenue}'.\n"
                f"- If a company's revenue clearly exceeds '{max_revenue}', you MUST EXCLUDE it — even if it matches the niche/location.\n"
                f"- If a company's revenue is clearly below '{min_revenue}', you MUST EXCLUDE it.\n"
                f"- If revenue data is unavailable, use employee count as a proxy: "
                f"~50 employees ≈ ₹5-20Cr, ~200 employees ≈ ₹50-200Cr, ~1000 employees ≈ ₹200-1000Cr, ~5000+ employees ≈ ₹1000Cr+.\n"
                f"- You MUST populate the 'estimated_revenue' field for every company with your best estimate."
            )
        elif min_revenue:
            revenue_instruction = (
                f"\n\nMANDATORY REVENUE FILTER (HARD GATE)\n"
                f"You MUST only include companies with estimated annual revenue of AT LEAST '{min_revenue}'.\n"
                f"- Exclude smaller companies below this threshold, even if they match the niche/location.\n"
                f"- If revenue data is unavailable, use employee count as a proxy.\n"
                f"- You MUST populate the 'estimated_revenue' field for every company with your best estimate."
            )
        elif max_revenue:
            revenue_instruction = (
                f"\n\nMANDATORY REVENUE FILTER (HARD GATE)\n"
                f"You MUST only include companies with estimated annual revenue of NO MORE THAN '{max_revenue}'.\n"
                f"- Exclude large companies above this ceiling, even if they match the niche/location.\n"
                f"- If revenue data is unavailable, use employee count as a proxy.\n"
                f"- You MUST populate the 'estimated_revenue' field for every company with your best estimate."
            )
        else:
            revenue_instruction = ""

        parse_prompt = ChatPromptTemplate.from_template(
            "You are an expert B2B market intelligence analyst. Parse the following live Google search results "
            "about companies in the '{niche}' sector HEADQUARTERED in '{location}' into a clean list of "
            "exactly {max_results} structured company objects.{revenue_instruction}\n\n"
            "CRITICAL LOCATION CONSTRAINT: Only include companies whose corporate headquarters or registered office "
            "is located in or near '{location}'. Exclude companies merely operating in '{location}' but headquartered elsewhere.\n\n"
            "Web Search Results:\n{markdown}\n\n"
            "For each company, extract:\n"
            "- name: Official company name\n"
            "- domain: (REQUIRED) The company's official website domain extracted from search result URLs (e.g. 'tatasteel.com'). Strip 'www.' prefix. Do NOT leave blank.\n"
            "- industry: Primary business vertical\n"
            "- employee_count: Estimated number of employees (e.g. '500+', '1,000-5,000', '10,000+'). Use 'N/A' if unknown.\n"
            "- headquarters: City and state/region of headquarters (e.g. 'Pune, Maharashtra'). Use 'N/A' if unknown.\n"
            "- estimated_revenue: Estimated annual revenue (e.g. '₹50 Cr', '$10M', '₹200-500 Cr'). Use 'N/A' if unknown.\n\n"
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

        # Domain enrichment fallback: for companies with missing domains,
        # run a quick targeted search to find the official website
        for comp in unique_companies:
            if not comp.domain or comp.domain in ("", "n/a", "N/A"):
                try:
                    print(f"[GeminiService] Domain missing for '{comp.name}'. Searching for official website...")
                    domain_md = self.serper.search(f'"{comp.name}" official website', num_results=5)
                    # Extract domain from search snippets using simple URL pattern matching
                    import re as _re
                    url_matches = _re.findall(r'https?://(?:www\.)?([a-zA-Z0-9\-]+\.[a-zA-Z]{2,}(?:\.[a-zA-Z]{2,})?)', domain_md)
                    # Filter out search engines and social media
                    excluded = {"google.com", "linkedin.com", "facebook.com", "twitter.com", "youtube.com", "wikipedia.org", "instagram.com"}
                    valid_domains = [d.lower() for d in url_matches if d.lower() not in excluded]
                    if valid_domains:
                        comp.domain = valid_domains[0]
                        print(f"[GeminiService] Found domain for '{comp.name}': {comp.domain}")
                    else:
                        print(f"[GeminiService] Could not find domain for '{comp.name}'.")
                except Exception as e:
                    print(f"[GeminiService] Domain lookup error for '{comp.name}': {e}")
            
        print(f"[GeminiService] Successfully discovered and structured {len(unique_companies)} unique companies.")
        return unique_companies

    def _hunt_email_for_contact(
        self,
        contact: Contact,
        company: Company,
        logs_out: List[str]
    ) -> Contact:
        """
        Searches for a contact's email using 2 targeted queries + regex snippet scanning.
        No page scraping. No LLM evaluation. Returns 'N/A' if nothing verified.
        """
        if contact.email and "@" in contact.email and not is_generic_email(contact.email):
            log_msg = f"[Email Hunt] Verified email '{contact.email}' already present for {contact.name}."
            print(log_msg)
            logs_out.append(log_msg)
            return contact

        domain = company.domain or ""
        contact_name = contact.name
        company_name_str = company.name

        # 2-query strategy — broader, higher yield than restrictive dorks
        queries = [
            # Q1 (broad): name + company + email keyword — catches LinkedIn, press releases, team pages
            f'"{contact_name}" "{company_name_str}" email',
            # Q2 (domain-specific): name + @domain — catches direct indexed emails
            f'"{contact_name}" "@{domain}"' if domain else f'"{contact_name}" "{company_name_str}" contact',
        ]

        email_found = None
        all_domain_emails = []  # Collected for pattern inference

        for attempt, query in enumerate(queries, 1):
            log_msg = f"[Email Hunt] [{contact_name}] Query {attempt}/{len(queries)}: '{query}'"
            print(log_msg)
            logs_out.append(log_msg)

            try:
                result_markdown = self.serper.search(query, num_results=10)
            except Exception as e:
                print(f"[Email Hunt] Search error: {e}")
                continue

            # Regex scan on snippets — fast, deterministic, zero token cost
            snippet_emails = extract_emails_from_text(result_markdown, target_domain=domain, filter_generic=True)
            if snippet_emails:
                all_domain_emails.extend(snippet_emails)
                for se in snippet_emails:
                    if is_matching_contact_email(se, contact_name):
                        email_found = se
                        log_msg = f"[Email Hunt] Found matching email in snippets: '{email_found}'"
                        print(log_msg)
                        logs_out.append(log_msg)
                        break

            if email_found:
                break

            # If no name-matched email but we found domain emails, accept first non-generic one
            # only on the last query attempt (fallback)
            if not email_found and attempt == len(queries) and snippet_emails:
                email_found = snippet_emails[0]
                log_msg = f"[Email Hunt] No name-match found. Using best domain email: '{email_found}'"
                print(log_msg)
                logs_out.append(log_msg)

        # Pattern inference fallback: if we found other emails at this domain,
        # infer the pattern and synthesize a candidate, then verify it
        if not email_found and domain and all_domain_emails:
            candidate = self._try_pattern_synthesis(contact_name, domain, all_domain_emails, logs_out)
            if candidate:
                email_found = candidate

        if email_found:
            contact.email = email_found
        else:
            log_msg = f"[Email Hunt] All strategies exhausted for {contact_name}. Setting to N/A."
            print(log_msg)
            logs_out.append(log_msg)
            contact.email = "N/A"

        return contact


    def _try_pattern_synthesis(
        self,
        contact_name: str,
        domain: str,
        sample_emails: List[str],
        logs_out: List[str]
    ) -> Optional[str]:
        """
        Infers an email pattern from sample domain emails and synthesizes a candidate.
        Verifies the candidate with a live search. Returns None if unverified.
        """
        clean_domain = domain.lower().replace("www.", "").strip()
        domain_emails = [e.lower() for e in sample_emails if e.endswith(f"@{clean_domain}")]
        if not domain_emails:
            return None

        # Detect pattern from first matching email
        pattern = None
        for email in domain_emails:
            local = email.split("@")[0]
            if "." in local and len(local.split(".")) == 2:
                pattern = "first.last"
                break
            elif "_" in local and len(local.split("_")) == 2:
                pattern = "first_last"
                break

        if not pattern:
            return None

        # Synthesize candidate
        name_clean = re.sub(r'[^a-zA-Z\s]', '', contact_name).lower().strip()
        tokens = name_clean.split()
        if len(tokens) < 2:
            return None

        first, last = tokens[0], tokens[-1]
        if pattern == "first.last":
            candidate = f"{first}.{last}@{clean_domain}"
        elif pattern == "first_last":
            candidate = f"{first}_{last}@{clean_domain}"
        else:
            return None

        # Mandatory live verification
        log_msg = f"[Pattern] Inferred '{pattern}' pattern. Verifying '{candidate}'..."
        print(log_msg)
        logs_out.append(log_msg)

        try:
            verify_md = self.serper.search(f'"{candidate}"', num_results=5)
            if candidate.lower() in verify_md.lower():
                log_msg = f"[Pattern] Confirmed '{candidate}' via live search."
                print(log_msg)
                logs_out.append(log_msg)
                return candidate
            else:
                log_msg = f"[Pattern] '{candidate}' not found on the web. Skipping."
                print(log_msg)
                logs_out.append(log_msg)
        except Exception as e:
            print(f"[Pattern] Verification error: {e}")

        return None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=3, max=15), reraise=True, before_sleep=before_sleep_log(logger, logging.WARNING))
    def get_contacts_for_company(self, company: Company, max_contacts: int = 3, logs_out: Optional[List[str]] = None) -> List[Contact]:
        """
        Targeted Executive Discovery: Searches for CEO, MD, and CHRO contacts in priority order (CEO > MD > CHRO)
        including their LinkedIn profile URLs, using 1 Serper search + 1 Gemini parse per company.
        """
        if logs_out is None:
            logs_out = []

        if config.MOCK_LLM:
            print(f"[GeminiService] [MOCK MODE] Simulating contacts search for '{company.name}'...")
            if "nocontact" in company.name.lower():
                print(f"[GeminiService] [MOCK MODE] Simulating no contacts found for '{company.name}'...")
                return []
            domain_str = company.domain or "example.com"
            slug = company.name.replace(" ", "").lower()
            contacts = [
                Contact(
                    name="Sarah Jenkins",
                    title="Chief Executive Officer (CEO)",
                    email=f"sarah.jenkins@{domain_str}",
                    linkedin_url=f"https://www.linkedin.com/in/sarahjenkins-{slug}-ceo",
                    company_name=company.name,
                    company_domain=company.domain
                ),
                Contact(
                    name="Rajesh Sharma",
                    title="Managing Director (MD)",
                    email=f"rsharma@{domain_str}",
                    linkedin_url=f"https://www.linkedin.com/in/rajesh-sharma-{slug}-md",
                    company_name=company.name,
                    company_domain=company.domain
                ),
                Contact(
                    name="Anita Desai",
                    title="Chief Human Resources Officer (CHRO)",
                    email=f"anita.desai@{domain_str}",
                    linkedin_url=f"https://www.linkedin.com/in/anita-desai-{slug}-chro",
                    company_name=company.name,
                    company_domain=company.domain
                )
            ]
            print(f"[GeminiService] [MOCK MODE] Generated {len(contacts)} simulated CEO/MD/CHRO contacts for '{company.name}'.")
            return contacts[:max_contacts]

        # Single combined query targeting CEO, MD, CHRO and LinkedIn profile pages
        log_msg = f"[Agent] Searching CEO, MD & CHRO contacts + LinkedIn profiles for '{company.name}'..."
        print(log_msg)
        logs_out.append(log_msg)

        search_query = (
            f'"{company.name}" (CEO OR "Chief Executive Officer" OR "Managing Director" OR "MD" OR "CHRO" OR "Chief Human Resources Officer" OR "HR Head") '
            f'site:linkedin.com/in/ OR "linkedin.com/in"'
        )
        raw_markdown = self.serper.search(search_query, num_results=10)

        log_msg = f"[Agent] Parsing structured CEO/MD/CHRO profiles for '{company.name}'..."
        print(log_msg)
        logs_out.append(log_msg)

        # Extract LinkedIn links directly from snippets via regex as secondary fallback
        snippet_linkedins = extract_linkedin_urls_from_text(raw_markdown)

        structured_llm = self.llm_parse.with_structured_output(ContactList)

        parse_prompt = ChatPromptTemplate.from_template(
            "Parse the following web search results about leadership contacts at '{company_name}' "
            "into a clean list of structured contact objects targeting executives in priority order: "
            "1. CEO (Chief Executive Officer / Founder)\n"
            "2. MD (Managing Director)\n"
            "3. CHRO (Chief Human Resources Officer / HR Head)\n\n"
            "Company: {company_name}\n"
            "Company Domain: {company_domain}\n\n"
            "Web Search Results:\n{markdown}\n\n"
            "For each contact, extract:\n"
            "- name: Full personal name\n"
            "- title: Official role (e.g. 'CEO', 'Managing Director', 'CHRO')\n"
            "- linkedin_url: Official LinkedIn user profile URL (e.g. 'https://www.linkedin.com/in/username'). If present in snippet, extract full URL; else null.\n"
            "- email: Business email address if explicitly present in snippet; else leave blank.\n\n"
            "Ensure you populate 'company_name' as '{company_name}' and 'company_domain' as '{company_domain}'. "
            "CRITICAL: Only include real individual persons. Do not include departments or corporate board placeholders."
        )

        parser_chain = parse_prompt | structured_llm
        parsed_result: ContactList = parser_chain.invoke({
            "markdown": raw_markdown,
            "company_name": company.name,
            "company_domain": company.domain or "",
            "max_contacts": str(max_contacts)
        })

        # Filter out non-person entities
        valid_contacts = [
            c for c in parsed_result.contacts
            if c.name and is_valid_person_name(c.name)
        ]
        if not valid_contacts:
            valid_contacts = [
                c for c in parsed_result.contacts
                if c.name and c.name.lower() not in ("n/a", "none", "unknown", "null")
            ]

        # Fallback snippet matching for linkedin_url if missing from LLM parse
        for c in valid_contacts:
            if not c.linkedin_url and snippet_linkedins:
                c_first = c.name.split()[0].lower()
                for url in snippet_linkedins:
                    if c_first in url.lower():
                        c.linkedin_url = url
                        break

        # Sort contacts by target role priority (CEO=1 > MD=2 > CHRO=3 > Other=4)
        valid_contacts.sort(key=lambda c: get_role_priority_score(c.title))

        # Deduplicate contacts by name
        seen_names = set()
        unique_contacts = []
        for c in valid_contacts:
            clean_name = c.name.strip().lower()
            if clean_name not in seen_names:
                seen_names.add(clean_name)
                unique_contacts.append(c)
        valid_contacts = unique_contacts[:max_contacts]

        # Enforce company name/domain consistency
        for c in valid_contacts:
            c.company_name = company.name
            c.company_domain = company.domain

        if not valid_contacts:
            return []

        # Phase 2: Sequential email hunting for each contact
        log_msg = f"[Agent] Hunting emails for {len(valid_contacts)} executive contacts at '{company.name}'..."
        print(log_msg)
        logs_out.append(log_msg)

        enriched_contacts = []
        for contact in valid_contacts:
            try:
                result = self._hunt_email_for_contact(contact, company, logs_out)
                enriched_contacts.append(result)
            except Exception as exc:
                print(f"[Agent] Email hunt failed for {contact.name}: {exc}")
                contact.email = "N/A"
                enriched_contacts.append(contact)

        return enriched_contacts

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
        
        first_name = contact.name.split()[0] if contact.name else "there"
        email_prompt = (
            f"You are a professional B2B cold email copywriter. Write a highly personalized, extremely short, and direct "
            f"outreach email to {contact.name} ({contact.title or 'executive'}) at {company.name} (website: {company.domain or 'unknown'}).\n\n"
            f"The sender is {sender_name} ({sender_title}) from Catenon, a global executive search firm.\n\n"
            f"Guidelines:\n"
            f"1. Subject line: Catchy, highly relevant, and professional. Mention Catenon or Executive Search/Talent Acquisition.\n"
            f"2. Email body: Keep it very short, concise, and direct (under 80 words). State clearly that the sender is from Catenon (global executive search firm) and get straight to the point.\n"
            f"3. Tone & Style: {style_instruction}\n"
            f"4. FORMATTING & LINE BREAKS (CRITICAL):\n"
            f"   - Greeting on its own line: 'Dear {first_name},' or 'Hi {first_name},'\n"
            f"   - Blank line before the body paragraph.\n"
            f"   - Blank line before the sign-off.\n"
            f"   - Sign-off and sender info MUST be on separate lines:\n"
            f"     Best regards,\n"
            f"     {sender_name}\n"
            f"     {sender_title}\n\n"
            f"The output must match the EmailDraft structured output schema."
        )

        structured_llm = self.llm_parse.with_structured_output(EmailDraft)
        email_draft: EmailDraft = structured_llm.invoke(email_prompt)
        
        # Double check fields are populated correctly
        email_draft.contact_name = contact.name
        email_draft.contact_email = contact.email or "unknown@email.com"
        email_draft.company_name = company.name

        # Post-process body to guarantee proper line breaks
        if email_draft.body:
            b = email_draft.body.strip()
            # Ensure double newline before sign-off
            for signoff in ["Best regards,", "Best regards", "Warm regards,", "Kind regards,", "Best,", "Regards,"]:
                if signoff in b and f"\n\n{signoff}" not in b:
                    b = b.replace(f" {signoff}", f"\n\n{signoff}").replace(f"? {signoff}", f"?\n\n{signoff}").replace(f".{signoff}", f".\n\n{signoff}")
                # Separate sender name and title below the sign-off
                if signoff in b:
                    parts = b.rsplit(signoff, 1)
                    sig = parts[1]
                    if f"{sender_name}, {sender_title}" in sig:
                        sig = sig.replace(f"{sender_name}, {sender_title}", f"\n{sender_name}\n{sender_title}")
                    elif f"{sender_name} - {sender_title}" in sig:
                        sig = sig.replace(f"{sender_name} - {sender_title}", f"\n{sender_name}\n{sender_title}")
                    b = parts[0] + signoff + sig
            email_draft.body = b
        
        print(f"[GeminiService] Email drafted successfully (Subject: '{email_draft.subject}').")
        return email_draft
