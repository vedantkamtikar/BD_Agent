from typing import TypedDict, Annotated, List, Dict, Any
import operator
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from models import Company, Contact, EmailDraft
from services.gemini import GeminiService

# Initialize the Gemini service instance
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
    sender_name: str
    sender_title: str
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
    location = state.get("location", "United States")
    max_results = state.get("max_results", 5)
    
    # Discover companies
    companies = gemini_service.search_companies(niche, location, max_results)
    
    log_msg = f"search_companies: Found {len(companies)} companies in '{niche}' ({location})."
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
        
    print(f"[get_contacts] Searching contacts for {len(companies)} companies...")
    for company in companies:
        contacts = gemini_service.get_contacts_for_company(company)
        all_contacts.extend(contacts)
        
    log_msg = f"get_contacts: Discovered {len(all_contacts)} contacts total."
    print(f"\n[NODE] EXITING: get_contacts -> {log_msg}")
    print("=" * 60 + "\n")
    
    return {
        "contacts": all_contacts,
        "logs": [log_msg]
    }


def should_draft(state: LeadState) -> str:
    """
    Conditional Routing Edge Function.
    Decides whether to route to the 'draft_emails' node or bypass directly to 'END'
    depending on whether contacts were successfully found.
    """
    print("\n" + "~" * 60)
    print("   [CONDITIONAL EDGE] EVALUATING: should_draft")
    print("~" * 60)
    
    contacts = state.get("contacts", [])
    contacts_count = len(contacts)
    
    print(f"[should_draft] Total contacts in state: {contacts_count}")
    
    if contacts_count > 0:
        decision = "draft_emails"
        print(" -> Decision: Contacts exist. Routing to: 'draft_emails'")
    else:
        decision = END
        print(" -> Decision: No contacts found. Routing to: 'END' (skipping email drafts)")
        
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
    
    # Map companies by name for fast lookup
    company_map = {company.name: company for company in companies}
    
    for contact in contacts:
        company = company_map.get(contact.company_name)
        if company:
            draft = gemini_service.draft_outreach_email(company, contact, sender_name, sender_title)
            email_drafts.append(draft)
        else:
            print(f"[draft_emails] Warning: Could not find matching company '{contact.company_name}' in state.")
            
    log_msg = f"draft_emails: Generated {len(email_drafts)} customized email drafts."
    print(f"\n[NODE] EXITING: draft_emails -> {log_msg}")
    print("=" * 60 + "\n")
    
    return {
        "emails": email_drafts,
        "logs": [log_msg]
    }


# -----------------------------------------------------------------------------
# Graph Assembly
# Instantiate the state graph using the LeadState TypedDict
builder = StateGraph(LeadState)

# Register workflow nodes
builder.add_node("search_companies", search_companies_node)
builder.add_node("get_contacts", get_contacts_node)
builder.add_node("draft_emails", draft_emails_node)

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

# Connect the drafting node to the final end state
builder.add_edge("draft_emails", END)

# Initialize a thread memory checkpointer
checkpointer = MemorySaver()

# Compile the graph into an executable agent
agent = builder.compile(checkpointer=checkpointer)
