from typing import TypedDict, Annotated, List, Dict, Any
import operator
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from models import Company, Contact, EmailDraft
from services.gemini import GeminiService
from services.gmail import GmailService

# Initialize service instances
gemini_service = GeminiService()


class LeadState(TypedDict):
    """
    State schema for the Lead Generation Agent.
    Uses annotators with operator.add to automatically append elements to lists
    across node executions (reducer pattern).
    """
    target_niche: str
    location: str
    max_results: int
    min_revenue: str
    max_revenue: str
    sender_name: str
    sender_title: str
    tone: str
    draft_emails_enabled: bool
    sync_gmail_drafts: bool
    companies: Annotated[List[Company], operator.add]
    contacts: Annotated[List[Contact], operator.add]
    emails: Annotated[List[EmailDraft], operator.add]
    logs: Annotated[List[str], operator.add]


def search_companies_node(state: LeadState) -> Dict[str, Any]:
    """
    Node: search_companies
    Executes search grounding to locate companies in the target niche and location,
    and updates the state with Company Pydantic models.
    """
    print("\n" + "=" * 60)
    print("   [NODE] ENTERING: search_companies")
    print("=" * 60)
    
    niche = state.get("target_niche")
    location = state.get("location", "India")
    max_results = state.get("max_results", 5)
    min_revenue = state.get("min_revenue", "")
    max_revenue = state.get("max_revenue", "")
    
    # Discover companies
    companies = gemini_service.search_companies(niche, location, max_results, min_revenue, max_revenue)
    
    rev_parts = []
    if min_revenue: rev_parts.append(f"Min: {min_revenue}")
    if max_revenue: rev_parts.append(f"Max: {max_revenue}")
    revenue_info = f" (Revenue Range: {', '.join(rev_parts)})" if rev_parts else ""
    
    log_msg = f"search_companies: Found {len(companies)} companies in '{niche}' ({location}){revenue_info}."
    print(f"\n[NODE] EXITING: search_companies -> {log_msg}")
    print("=" * 60 + "\n")
    
    return {
        "companies": companies,
        "logs": [log_msg]
    }


def get_contacts_node(state: LeadState) -> Dict[str, Any]:
    """
    Node: get_contacts
    Loops over discovered companies and retrieves key contacts (executives/managers)
    using grounded search for each one, updating the state.
    """
    print("\n" + "=" * 60)
    print("   [NODE] ENTERING: get_contacts")
    print("=" * 60)
    
    companies = state.get("companies", [])
    all_contacts = []
    
    if not companies:
        log_msg = "get_contacts: Skipped contact enrichment. No companies in state."
        print(f"\n[NODE] EXITING: get_contacts -> {log_msg}")
        print("=" * 60 + "\n")
        return {"contacts": [], "logs": [log_msg]}
        
    total = len(companies)
    print(f"[get_contacts] Searching contacts for {total} companies...")
    node_logs = []
    for idx, company in enumerate(companies, 1):
        print(f"[get_contacts] [{idx}/{total}] Searching contacts at '{company.name}'...")
        contacts = gemini_service.get_contacts_for_company(company, max_contacts=3, logs_out=node_logs)
        if contacts:
            all_contacts.extend(contacts)

    # Deduplicate contacts by (name, company_name) to prevent duplicate rows
    seen_keys = set()
    unique_contacts = []
    for c in all_contacts:
        key = (c.name.strip().lower(), c.company_name.strip().lower())
        if key not in seen_keys:
            seen_keys.add(key)
            unique_contacts.append(c)
    all_contacts = unique_contacts
        
    verified_count = sum(1 for c in all_contacts if c.email and c.email != "N/A" and "@" in c.email)
    log_msg = f"get_contacts: Discovered {len(all_contacts)} contacts total ({verified_count} with verified email)."
    print(f"\n[NODE] EXITING: get_contacts -> {log_msg}")
    print("=" * 60 + "\n")
    
    return {
        "contacts": all_contacts,
        "logs": [f"[PROGRESS] get_contacts: {idx}/{total}" for idx in range(1, total + 1)] + node_logs + [log_msg]
    }


def should_draft(state: LeadState) -> str:
    """
    Conditional Routing Edge Function.
    Decides whether to route to the 'draft_emails' node or bypass directly to 'END'
    depending on whether draft_emails_enabled is True AND at least one contact has a verified email.
    """
    print("\n" + "~" * 60)
    print("   [CONDITIONAL EDGE] EVALUATING: should_draft")
    print("~" * 60)
    
    draft_enabled = state.get("draft_emails_enabled", True)
    contacts = state.get("contacts", [])
    verified_contacts = [c for c in contacts if c.email and c.email != "N/A" and "@" in c.email]
    
    print(f"[should_draft] Draft emails enabled: {draft_enabled} | Total contacts: {len(contacts)} (Verified emails: {len(verified_contacts)})")
    
    if not draft_enabled:
        decision = END
        print(" -> Decision: Draft outreach emails option is DISABLED. Routing to: 'END' (bypassing email drafting)")
    elif len(verified_contacts) > 0:
        decision = "draft_emails"
        print(f" -> Decision: {len(verified_contacts)} verified contacts found. Routing to: 'draft_emails'")
    else:
        decision = END
        print(" -> Decision: No verified contact emails found. Routing to: 'END' (skipping email drafts)")
        
    print("~" * 60 + "\n")
    return decision


def draft_emails_node(state: LeadState) -> Dict[str, Any]:
    """
    Node: draft_emails
    Drafts outreach emails for all verified contacts using company description context.
    """
    print("\n" + "=" * 60)
    print("   [NODE] ENTERING: draft_emails")
    print("=" * 60)
    
    companies = state.get("companies", [])
    contacts = state.get("contacts", [])
    email_drafts = []
    
    sender_name = state.get("sender_name", "Alex")
    sender_title = state.get("sender_title", "Lead Consultant")
    tone = state.get("tone", "formal")
    
    # Map companies by name for fast lookup
    company_map = {company.name: company for company in companies}
    total = len(contacts)
    
    for idx, contact in enumerate(contacts, 1):
        company = company_map.get(contact.company_name)
        if not company:
            print(f"[draft_emails] Warning: Could not find matching company '{contact.company_name}' in state.")
            continue

        # Change 4: Skip drafting when no verified email was found
        if not contact.email or contact.email == "N/A" or "@" not in contact.email:
            print(f"[draft_emails] [{idx}/{total}] Skipping draft for '{contact.name}' — no verified email found.")
            continue

        print(f"[draft_emails] [{idx}/{total}] Drafting email for '{contact.name}' at '{company.name}'...")
        draft = gemini_service.draft_outreach_email(company, contact, sender_name, sender_title, tone)
        email_drafts.append(draft)
            
    log_msg = f"draft_emails: Generated {len(email_drafts)} customized email drafts."
    print(f"\n[NODE] EXITING: draft_emails -> {log_msg}")
    print("=" * 60 + "\n")
    
    return {
        "emails": email_drafts,
        "logs": [f"[PROGRESS] draft_emails: {idx}/{total}" for idx in range(1, total + 1)] + [log_msg]
    }


def create_gmail_drafts_node(state: LeadState) -> Dict[str, Any]:
    """
    Node: create_gmail_drafts
    Syncs generated email drafts directly into the user's Gmail Drafts folder using Gmail API.
    """
    print("\n" + "=" * 60)
    print("   [NODE] ENTERING: create_gmail_drafts")
    print("=" * 60)
    
    sync_enabled = state.get("sync_gmail_drafts", True)
    emails = state.get("emails", [])
    
    if not sync_enabled:
        log_msg = "create_gmail_drafts: Skipped. Sync to Gmail Drafts option is DISABLED."
        print(f"\n[NODE] EXITING: create_gmail_drafts -> {log_msg}")
        print("=" * 60 + "\n")
        return {"logs": [log_msg]}
        
    if not emails:
        log_msg = "create_gmail_drafts: Skipped. No email drafts present in state."
        print(f"\n[NODE] EXITING: create_gmail_drafts -> {log_msg}")
        print("=" * 60 + "\n")
        return {"logs": [log_msg]}
        
    gmail_service = GmailService()
    node_logs = []
    synced_count = gmail_service.batch_create_drafts(emails, logs_out=node_logs)
    
    log_msg = f"create_gmail_drafts: Synced {synced_count} drafts to Gmail Drafts folder."
    print(f"\n[NODE] EXITING: create_gmail_drafts -> {log_msg}")
    print("=" * 60 + "\n")
    
    return {
        "logs": node_logs + [log_msg]
    }


# -----------------------------------------------------------------------------
# Graph Assembly
# Instantiate the state graph using the LeadState TypedDict
builder = StateGraph(LeadState)

# Register workflow nodes
builder.add_node("search_companies", search_companies_node)
builder.add_node("get_contacts", get_contacts_node)
builder.add_node("draft_emails", draft_emails_node)
builder.add_node("create_gmail_drafts", create_gmail_drafts_node)

# Set the start node
builder.set_entry_point("search_companies")

# Define the normal edge (always move from search to contacts)
builder.add_edge("search_companies", "get_contacts")

# Define the conditional edge using should_draft to decide next node after get_contacts
builder.add_conditional_edges(
    "get_contacts",
    should_draft,
    {
        "draft_emails": "draft_emails",
        END: END
    }
)

# Connect the drafting node to the Gmail draft sync node, then END
builder.add_edge("draft_emails", "create_gmail_drafts")
builder.add_edge("create_gmail_drafts", END)

# Initialize a thread memory checkpointer
checkpointer = MemorySaver()

# Compile the graph into an executable agent
agent = builder.compile(checkpointer=checkpointer)
