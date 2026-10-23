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
    def search_companies(self, niche: str, location: str, max_results: int = 5, min_revenue: str = "", max_revenue: str = "", csr_focus: str = "") -> List[Company]:
        """
        Step 1 (Search): Searches for real companies in the target niche/location using Google search grounding,
        optionally filtering by revenue range criteria and CSR (Corporate Social Responsibility) focus areas.
        Step 2 (Parse): Converts the unstructured search output into a list of Company Pydantic models with CSR details.
        """
        rev_parts = []
        if min_revenue: rev_parts.append(f"Min: {min_revenue}")
        if max_revenue: rev_parts.append(f"Max: {max_revenue}")
        revenue_log_str = f" (Revenue Range: {', '.join(rev_parts)})" if rev_parts else ""
        csr_log_str = f" (CSR Focus: {csr_focus})" if csr_focus else ""

        if config.MOCK_LLM:
            print(f"[GeminiService] [MOCK MODE] Simulating company discovery for '{niche}' in '{location}'{revenue_log_str}{csr_log_str}...")
            niche_slug = niche.replace(" ", "").lower()
            companies = []
            mock_csr_themes = [
                "Education & Digital Literacy in Rural Schools",
                "Healthcare Access & Mobile Health Clinics",
                "Renewable Energy & Water Conservation",
                "Women Empowerment & Skill Development",
                "Sustainable Agriculture & Community Forestry"
            ]
            for i in range(1, max_results + 1):
                name = f"Mock {niche.title()} Corp {i}"
                domain = f"mock{niche_slug}{i}.com"
                csr_theme = f"{csr_focus} Initiatives" if csr_focus else mock_csr_themes[(i - 1) % len(mock_csr_themes)]
                companies.append(Company(
                    name=name,
                    domain=domain,
                    industry=niche,
                    employee_count="500+",
                    headquarters=location,
                    estimated_revenue="₹50-100 Cr",
                    csr_details=csr_theme,
                    source="Mock Local Generator"
                ))
            print(f"[GeminiService] [MOCK MODE] Generated {len(companies)} simulated companies with CSR details.")
            return companies

        print(f"[GeminiService] Step A: Searching web for {max_results} '{niche}' companies headquartered in '{location}'{revenue_log_str}{csr_log_str}...")
        
        query_rev = []
        if min_revenue: query_rev.append(f"minimum annual revenue {min_revenue}")
        if max_revenue: query_rev.append(f"maximum annual revenue {max_revenue}")
        revenue_term = f" {' '.join(query_rev)}" if query_rev else ""
        csr_query_term = f" CSR corporate social responsibility {csr_focus}" if csr_focus else ""

        search_query = f"{niche} companies headquartered in {location}{revenue_term}{csr_query_term} official website"
        raw_markdown = self.serper.search(search_query, num_results=max_results * 3)

        # Phase 2: If revenue constraints are specified, run a dedicated revenue search
        has_revenue_filter = bool(min_revenue or max_revenue)
        if has_revenue_filter:
            rev_search_query = f"{niche} companies {location} annual revenue turnover financials"
            print(f"[GeminiService] Step A2: Running dedicated revenue search: '{rev_search_query}'...")
            revenue_markdown = self.serper.search(rev_search_query, num_results=max_results * 2)
            raw_markdown += "\n\n### Revenue & Financial Data (Supplementary Search)\n" + revenue_markdown

        # Phase 3: Supplementary search for CSR focus or initiatives (always run to ensure CSR grounding)
        if csr_focus:
            csr_search_query = f"{niche} companies {location} CSR corporate social responsibility foundation {csr_focus}"
        else:
            csr_search_query = f"{niche} companies {location} CSR corporate social responsibility foundation initiatives"
        print(f"[GeminiService] Step A3: Running dedicated CSR search: '{csr_search_query}'...")
        csr_markdown = self.serper.search(csr_search_query, num_results=max_results * 2)
        raw_markdown += "\n\n### CSR & Community Initiatives (Supplementary Search)\n" + csr_markdown

        print("[GeminiService] Step B: Extracting structured company list with CSR details from search output...")
        
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

        if csr_focus:
            csr_instruction = (
                f"\n\nCSR FOCUS DIRECTIVE\n"
                f"Target / prioritize companies with active CSR (Corporate Social Responsibility) initiatives in '{csr_focus}'.\n"
                f"In the 'csr_details' field, summarize the specific CSR programs, foundation activities, or impact areas discovered."
            )
        else:
            csr_instruction = (
                f"\n\nCSR DETAILS EXTRACTION\n"
                f"In the 'csr_details' field, concisely summarize the company's notable CSR programs, foundation work, or community/sustainability initiatives (e.g. 'Education & Skill Building', 'Rural Healthcare & Sanitation', 'Renewable Energy & Afforestation'). If not found in search results, use 'N/A'."
            )

        parse_prompt = ChatPromptTemplate.from_template(
            "You are an expert B2B market intelligence analyst. Parse the following live Google search results "
            "about companies in the '{niche}' sector HEADQUARTERED in '{location}' into a clean list of "
            "exactly {max_results} structured company objects.{revenue_instruction}{csr_instruction}\n\n"
            "CRITICAL LOCATION CONSTRAINT: Only include companies whose corporate headquarters or registered office "
            "is located in or near '{location}'. Exclude companies merely operating in '{location}' but headquartered elsewhere.\n\n"
            "Web Search Results:\n{markdown}\n\n"
            "For each company, extract:\n"
            "- name: Official company name\n"
            "- domain: (REQUIRED) The company's official website domain extracted from search result URLs (e.g. 'tatasteel.com'). Strip 'www.' prefix. Do NOT leave blank.\n"
            "- industry: Primary business vertical\n"
            "- employee_count: Estimated number of employees (e.g. '500+', '1,000-5,000', '10,000+'). Use 'N/A' if unknown.\n"
            "- headquarters: City and state/region of headquarters (e.g. 'Pune, Maharashtra'). Use 'N/A' if unknown.\n"
            "- estimated_revenue: Estimated annual revenue (e.g. '₹50 Cr', '$10M', '₹200-500 Cr'). Use 'N/A' if unknown.\n"
            "- csr_details: Summary of company's CSR initiatives, foundation, or sustainability focus areas (e.g. 'Education & Vocational Training', 'Healthcare & Clean Water'). Use 'N/A' if unknown.\n\n"
            "Only include real, currently active companies. Do not invent or fabricate entries."
        )
        
        parser_chain = parse_prompt | structured_llm
        parsed_result: CompanyList = parser_chain.invoke({
            "markdown": raw_markdown,
            "niche": niche,
            "location": location,
            "max_results": str(max_results),
            "revenue_instruction": revenue_instruction,
            "csr_instruction": csr_instruction
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

        # CSR enrichment fallback: for companies where CSR was not captured in the batch search snippets,
        # run a targeted search for the company's dedicated CSR/foundation programs
        for comp in unique_companies:
            if not comp.csr_details or comp.csr_details.strip().upper() in ("", "N/A", "NONE", "UNKNOWN"):
                comp.csr_details = self._enrich_company_csr(comp, csr_focus=csr_focus)
            
        print(f"[GeminiService] Successfully discovered and structured {len(unique_companies)} unique companies.")
        return unique_companies

    def _enrich_company_csr(self, company: Company, csr_focus: str = "") -> str:
        """
        Runs a targeted, dedicated search for a company's CSR/foundation initiatives
        and extracts a concise summary of their social impact focus.
        """
        if not self.serper:
            return company.csr_details or "N/A"
        try:
            focus_term = f" {csr_focus}" if csr_focus else ""
            query = f'"{company.name}" CSR OR foundation OR "corporate social responsibility" initiatives{focus_term}'
            print(f"[GeminiService] Performing dedicated CSR lookup for '{company.name}'...")
            snippets = self.serper.search(query, num_results=3)
            if snippets and len(snippets.strip()) > 40:
                prompt = (
                    f"You are a CSR intelligence analyst. Based on the following web search snippets for the company '{company.name}', "
                    f"summarize their primary CSR (Corporate Social Responsibility) initiatives, foundation programs, or social impact areas in a single crisp phrase (5 to 15 words, e.g. 'Community empowerment, education, and rural healthcare initiatives'). "
                    f"Do NOT include preambles, citations, or quotes. If no CSR or foundation initiatives are mentioned, respond with 'N/A'.\n\n"
                    f"Search Snippets:\n{snippets}"
                )
                resp = self.llm_parse.invoke(prompt)
                raw = resp.content
                if isinstance(raw, list):
                    text = "".join([p if isinstance(p, str) else p.get("text", "") for p in raw])
                else:
                    text = str(raw)
                cleaned = text.strip().replace('"', '').replace("'", "").strip()
                if cleaned and cleaned.upper() != "N/A" and len(cleaned) > 5:
                    print(f"[GeminiService] Discovered CSR details for '{company.name}': {cleaned}")
                    return cleaned
        except Exception as err:
            print(f"[GeminiService] Dedicated CSR lookup notice for '{company.name}': {err}")
        return company.csr_details or "N/A"

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
    def draft_outreach_email(self, company: Company, contact: Contact, sender_name: str = "Alex", sender_title: str = "Outreach Lead", tone: str = "formal") -> EmailDraft:
        """
        Drafts a highly personalized CSR outreach email for an NGO outreach program
        targeting a specific contact at a target company, proposing collaboration and requesting a meeting/discussion.
        """
        # Map tone to style instructions
        tone_styles = {
            "formal": "Professional, structured, and courteous. Respectful of executive leadership while clearly articulating mission and social value.",
            "conversational": "Warm, inspiring, and human-sounding. Write with authentic passion for community impact rather than corporate jargon.",
            "bold": "Purpose-driven, direct, and compelling. Lead with high conviction around community impact and meaningful social transformation."
        }
        style_instruction = tone_styles.get(tone, tone_styles["formal"])

        has_csr = bool(company.csr_details and company.csr_details not in ("N/A", "unknown", "None", ""))
        csr_details_str = company.csr_details if has_csr else "social responsibility and community initiatives"

        if config.MOCK_LLM:
            print(f"[GeminiService] [MOCK MODE] Simulating CSR outreach email for '{contact.name}' at '{company.name}' (tone: {tone})...")
            csr_mention = f" regarding {company.name}'s inspiring work in {csr_details_str}." if has_csr else f" regarding potential CSR collaboration with {company.name}."
            return EmailDraft(
                contact_name=contact.name,
                contact_email=contact.email or "unknown@email.com",
                subject=f"CSR Partnership & Community Impact Discussion — {company.name}",
                body=(
                    f"Dear {contact.name},\n\n"
                    f"I am reaching out from our NGO outreach program{csr_mention} We lead high-impact grassroots initiatives focused on sustainable community development.\n\n"
                    f"Given your leadership at {company.name}, we would welcome the opportunity to explore synergistic CSR partnership opportunities that amplify positive social outcomes.\n\n"
                    f"Could we schedule a brief 15-minute introductory meeting or discussion next week to share our initiative overview and explore potential alignment?\n\n"
                    f"Best regards,\n{sender_name}\n{sender_title}"
                ),
                company_name=company.name
            )

        print(f"[GeminiService] Drafting CSR outreach email to '{contact.name}' ({contact.title or 'Executive'}) at '{company.name}' (tone: {tone})...")
        
        # Determine appropriate salutation name (skipping standalone initials or honorifics)
        salutation_name = contact.name.strip() if contact.name else "there"
        if contact.name:
            clean_name = re.sub(r'^(dr\.|mr\.|ms\.|mrs\.|prof\.)\s+', '', contact.name.strip(), flags=re.IGNORECASE)
            tokens = clean_name.split()
            valid_tokens = [t for t in tokens if len(re.sub(r'[^a-zA-Z]', '', t)) > 1]
            salutation_name = valid_tokens[0] if valid_tokens else contact.name.strip()

        csr_context_prompt = f"Known Company CSR Initiatives: {company.csr_details}\n" if has_csr else ""

        email_prompt = (
            f"You are a professional outreach copywriter representing an NGO outreach program. Write a highly personalized, "
            f"concise, and persuasive CSR partnership outreach email to {contact.name} ({contact.title or 'Executive'}) "
            f"at {company.name} (website: {company.domain or 'unknown'}).\n\n"
            f"{csr_context_prompt}"
            f"The sender is {sender_name} ({sender_title}) representing an NGO outreach program dedicated to grassroots social impact.\n\n"
            f"Guidelines:\n"
            f"1. Context & Purpose: Outreach on behalf of an NGO outreach program to explore a Corporate Social Responsibility (CSR) partnership or collaboration with {company.name}.\n"
            f"2. CSR Relevance: Acknowledge {company.name}'s social responsibility commitment"
            + (f" and specifically reference their initiatives in '{company.csr_details}'." if has_csr else ", connecting their values with impactful community development.")
            + f"\n3. MANDATORY MEETING REQUEST: You MUST explicitly include a clear, courteous request to schedule a brief 10 to 15-minute introductory meeting or discussion next week to explore potential collaboration and mutual community impact.\n"
            f"4. Length & Brevity: Keep the body text concise and direct (under 90 words). Avoid generic fluff or long preambles.\n"
            f"5. Subject Line: Catchy, highly relevant, and professional. Specifically mention CSR partnership, community impact, or a brief discussion for {company.name}.\n"
            f"6. Tone & Style: {style_instruction}\n"
            f"7. FORMATTING & LINE BREAKS (CRITICAL):\n"
            f"   - Greeting on its own line: 'Dear {salutation_name},' or 'Hi {salutation_name},'\n"
            f"   - Blank line before the body paragraph.\n"
            f"   - Blank line before the meeting request.\n"
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
