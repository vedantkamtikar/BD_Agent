import re
import requests
from typing import List, Dict, Any, Tuple

import urllib.parse

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

# Common generic / department inbox prefixes that should NOT be attributed to individual executives
GENERIC_EMAIL_PREFIXES = {
    "info", "support", "sales", "contact", "careers", "jobs", "press", "media",
    "admin", "help", "billing", "privacy", "legal", "office", "hello", "team",
    "inquiries", "inquiry", "general", "marketing", "hr", "recruitment", "security",
    "compliance", "feedback", "service", "customerservice", "donotreply", "noreply",
    "postmaster", "webmaster", "abuse", "frontdesk", "reception"
}


def is_generic_email(email: str) -> bool:
    """Checks if an email prefix is a generic department inbox (e.g., info@, sales@)."""
    if not email or "@" not in email:
        return False
    local_part = email.split("@")[0].lower().strip()
    return local_part in GENERIC_EMAIL_PREFIXES


def is_matching_contact_email(email: str, contact_name: str) -> bool:
    """
    Heuristically checks if an email matches the given contact's name.
    Verifies first name, last name, or initials in the local part of the address.
    """
    if not email or not contact_name or "@" not in email:
        return False
    
    local_part = email.split("@")[0].lower().replace(".", "").replace("_", "").replace("-", "")
    name_clean = re.sub(r'[^a-zA-Z\s]', '', contact_name).lower().strip()
    tokens = [t for t in name_clean.split() if len(t) > 1]
    
    if not tokens:
        return False
        
    first_name = tokens[0]
    last_name = tokens[-1] if len(tokens) > 1 else ""
    
    # 1. Exact first or last name in local part
    if first_name in local_part:
        return True
    if last_name and last_name in local_part:
        return True
        
    # 2. First initial + last name (e.g. jdoe for John Doe)
    if last_name and (first_name[0] + last_name) in local_part:
        return True
        
    # 3. First name + last initial (e.g. johnd for John Doe)
    if last_name and (first_name + last_name[0]) in local_part:
        return True

    return False


def extract_emails_from_text(text: str, target_domain: str = "", filter_generic: bool = True) -> List[str]:
    """
    Extracts, de-obfuscates, and normalizes email addresses from any text/HTML snippet.
    Optionally filters by domain and excludes generic inboxes.
    """
    if not text:
        return []
        
    found_emails = set()
    
    # 1. URL-decode text to catch encoded mailto: and query strings
    try:
        decoded_text = urllib.parse.unquote(text)
    except Exception:
        decoded_text = text

    # 2. Extract standard emails from original decoded text
    for match in EMAIL_REGEX.findall(decoded_text):
        email_clean = match.lower().strip().rstrip(".")
        found_emails.add(email_clean)

    # 3. De-obfuscate common web obfuscations:
    # Safely replace [at], (at), {at} with '@', and [dot], (dot), {dot} with '.'
    # NOTE: Only matches bracketed forms — bare 'at'/'dot' words are NOT matched to avoid
    # false positives from normal English like "reach our founder at sarah.jenkins"
    deobf_text = re.sub(r'\s*(?:\[\s*(?:at|@)\s*\]|\(\s*(?:at|@)\s*\)|\{\s*(?:at|@)\s*\})\s*', '@', decoded_text, flags=re.IGNORECASE)
    deobf_text = re.sub(r'\s*(?:\[\s*(?:dot|\.)\s*\]|\(\s*(?:dot|\.)\s*\)|\{\s*(?:dot|\.)\s*\})\s*', '.', deobf_text, flags=re.IGNORECASE)

    for match in EMAIL_REGEX.findall(deobf_text):
        email_clean = match.lower().strip().rstrip(".")
        found_emails.add(email_clean)
        
    # 4. Domain & Generic Filtering
    results = []
    for email in found_emails:
        if filter_generic and is_generic_email(email):
            continue
        if target_domain:
            clean_target = target_domain.lower().replace("www.", "").strip()
            email_domain = email.split("@")[-1].lower().strip()
            # Match exact domain or subdomain
            if email_domain == clean_target or email_domain.endswith(f".{clean_target}"):
                results.append(email)
        else:
            results.append(email)
            
    return results


SERPER_API_URL = "https://google.serper.dev/search"


class SerperService:
    """
    Lightweight wrapper around the Serper.dev Google Search API.
    Performs web searches and returns formatted markdown results
    suitable for feeding into a Gemini structured parser.
    """

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("[SerperService] SERPER_API_KEY is not configured. Cannot perform web searches.")
        self.api_key = api_key
        self.headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json"
        }

    def search(self, query: str, num_results: int = 10) -> str:
        """
        Performs a Google search via Serper and returns formatted markdown text
        containing the search results (titles, snippets, and links).

        Args:
            query: The search query string.
            num_results: Number of results to request (max 100).

        Returns:
            A formatted markdown string of search results.
        """
        payload = {
            "q": query,
            "num": num_results
        }

        print(f"[SerperService] Searching: '{query}' (requesting {num_results} results)...")

        response = requests.post(
            SERPER_API_URL,
            headers=self.headers,
            json=payload,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        return self._format_results(data)

    def search_with_urls(self, query: str, num_results: int = 10) -> Tuple[str, List[str]]:
        """
        Like search(), but also returns the list of organic result URLs
        so the caller can attempt direct page scraping.

        Returns:
            (markdown_str, list_of_urls)
        """
        payload = {
            "q": query,
            "num": num_results
        }

        print(f"[SerperService] Searching: '{query}' (requesting {num_results} results)...")

        response = requests.post(
            SERPER_API_URL,
            headers=self.headers,
            json=payload,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()

        markdown = self._format_results(data)
        urls = [r.get("link", "") for r in data.get("organic", []) if r.get("link")]
        return markdown, urls

    def fetch_emails_from_page(self, url: str, target_domain: str, contact_name: str = "") -> List[str]:
        """
        Fetches a web page and extracts all email addresses matching @target_domain
        using regex scan and de-obfuscation over the full HTML body.
        Excludes generic inboxes and prioritizes emails matching contact_name.

        Args:
            url: The full URL to fetch.
            target_domain: Only return emails ending with this domain (e.g., 'tata.com').
            contact_name: Optional contact name to prioritize/match.

        Returns:
            A prioritized, deduplicated list of found email addresses.
        """
        try:
            resp = requests.get(
                url,
                timeout=6,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"},
                allow_redirects=True
            )
            if resp.status_code != 200:
                return []

            extracted = extract_emails_from_text(resp.text, target_domain=target_domain, filter_generic=True)
            
            if contact_name and extracted:
                # Prioritize emails that match the contact's name
                matching = [e for e in extracted if is_matching_contact_email(e, contact_name)]
                if matching:
                    return matching
                    
            return extracted

        except Exception as exc:
            print(f"[SerperService] Page fetch failed for {url}: {exc}")
            return []

    def _format_results(self, data: Dict[str, Any]) -> str:
        """
        Converts raw Serper JSON response into a clean markdown string
        that can be consumed by Gemini for structured data extraction.
        """
        sections = []

        # Knowledge Graph (if present)
        kg = data.get("knowledgeGraph")
        if kg:
            title = kg.get("title", "")
            description = kg.get("description", "")
            website = kg.get("website", "")
            if title:
                sections.append(f"### Knowledge Graph: {title}")
                if description:
                    sections.append(f"Description: {description}")
                if website:
                    sections.append(f"Website: {website}")
                sections.append("")

        # Organic results
        organic = data.get("organic", [])
        if organic:
            sections.append("### Search Results")
            for i, result in enumerate(organic, 1):
                title = result.get("title", "No title")
                link = result.get("link", "")
                snippet = result.get("snippet", "No description available.")
                sections.append(f"**{i}. {title}**")
                sections.append(f"   URL: {link}")
                sections.append(f"   {snippet}")
                sections.append("")

        # People Also Ask (useful for contact discovery)
        paa = data.get("peopleAlsoAsk", [])
        if paa:
            sections.append("### Related Questions")
            for item in paa:
                q = item.get("question", "")
                a = item.get("snippet", "")
                if q:
                    sections.append(f"- **Q:** {q}")
                    if a:
                        sections.append(f"  **A:** {a}")
            sections.append("")

        if not sections:
            return "No search results found."

        return "\n".join(sections)
