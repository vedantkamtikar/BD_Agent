import re
import requests
from typing import List, Dict, Any, Tuple

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

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

    def fetch_emails_from_page(self, url: str, target_domain: str) -> List[str]:
        """
        Fetches a web page and extracts all email addresses matching @target_domain
        using a regex scan over the full HTML body.
        This is deterministic — no LLM involved, zero hallucination risk.

        Args:
            url: The full URL to fetch.
            target_domain: Only return emails ending with this domain (e.g., 'tata.com').

        Returns:
            A deduplicated list of found email addresses.
        """
        try:
            resp = requests.get(
                url,
                timeout=6,
                headers={"User-Agent": "Mozilla/5.0 (compatible; LeadAgentBot/1.0)"},
                allow_redirects=True
            )
            if resp.status_code != 200:
                return []

            all_emails = EMAIL_REGEX.findall(resp.text)
            # Filter to only emails belonging to the target domain
            domain_emails = [
                e.lower() for e in all_emails
                if e.lower().endswith(f"@{target_domain.lower()}")
            ]
            return list(set(domain_emails))

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
