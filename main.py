import sys
import uuid
import config

# 1. Validate environment configuration first
if not config.check_config():
    print("[CRITICAL ERROR] Missing configuration. Agent execution stopped.")
    print("Please copy '.env.example' to '.env' and set your GEMINI_API_KEY.")
    sys.exit(1)

# Now it is safe to import the agent
from graph import agent
from services.google_sheets import LeadLogger


def main():
    # 2. Print educational CLI intro explaining agent concepts
    print("\n" + "#" * 65)
    print("       B2B LEAD-GENERATION AGENT - INTERACTIVE CLI RUNNER")
    print("#" * 65)
    print("Welcome! This agent utilizes LangGraph to run a lead-gen pipeline.")
    print("Here is how the agent loops through the graph:")
    print("  1. 'search_companies' Node:")
    print("     - Performs a web search with Google Search grounding.")
    print("     - Extracts structured Company objects using Pydantic.")
    print("  2. 'get_contacts' Node:")
    print("     - For each company, searches for executives using grounding.")
    print("     - Extracts structured Contact objects using Pydantic.")
    print("  3. 'should_draft' Conditional Routing Edge:")
    print("     - Evaluates the accumulated contacts in state.")
    print("     - IF contacts are found -> routes to the 'draft_emails' node.")
    print("     - IF NO contacts are found -> routes directly to 'END' (skipping drafts).")
    print("  4. 'draft_emails' Node:")
    print("     - Generates custom B2B cold emails tailored to titles and company profiles.")
    print("  5. Logging Service (google_sheets.py):")
    print("     - Persists all company, contact, and email records to Google Sheets")
    print("       or falls back to a local CSV file ('leads_log.csv').")
    print("#" * 65 + "\n")

    # 3. Request user inputs or parse arguments
    import argparse
    parser = argparse.ArgumentParser(description="B2B Lead-Gen Agent CLI")
    parser.add_argument("--niche", type=str, help="Target industry or niche")
    parser.add_argument("--location", type=str, default="United States", help="Location filter")
    parser.add_argument("--limit", type=int, default=3, help="Max companies to search")
    args, _ = parser.parse_known_args()

    target_niche = args.niche
    location = args.location
    max_results = args.limit

    if not target_niche:
        try:
            target_niche = input("Enter target company niche/vertical (e.g. 'DevOps consulting agencies'): ").strip()
            if not target_niche:
                print("[Error] Target niche query cannot be empty.")
                sys.exit(1)

            location_input = input("Enter company location filter (default: 'United States'): ").strip()
            if location_input:
                location = location_input

            max_results_str = input("Enter max number of companies to discover (default: 3): ").strip()
            if max_results_str.isdigit():
                max_results = int(max_results_str)
        except KeyboardInterrupt:
            print("\nExiting CLI run...")
            sys.exit(0)

    # 4. Initialize graph state
    initial_state = {
        "target_niche": target_niche,
        "location": location,
        "max_results": max_results,
        "min_revenue": "",
        "max_revenue": "",
        "sender_name": "Alex",
        "sender_title": "Lead Consultant",
        "tone": "formal",
        "draft_emails_enabled": True,
        "sync_gmail_drafts": False,
        "companies": [],
        "contacts": [],
        "emails": [],
        "logs": []
    }

    # Generate a unique thread ID to demonstrate LangGraph checkpoint memory
    thread_id = str(uuid.uuid4())
    run_config = {"configurable": {"thread_id": thread_id}}

    print(f"\n[Runner] Booting agent workflow. Thread ID: '{thread_id}'")

    # 5. Stream the state transitions step-by-step
    try:
        for event in agent.stream(initial_state, run_config):
            for node_name, state_update in event.items():
                print(f"\n[Runner] >>> Update received from Node: '{node_name}'")
                
                # Print specific state changes for educational clarity
                for key, val in state_update.items():
                    if key == "companies":
                        print(f"   State delta (companies): discovered {len(val)} companies:")
                        for idx, item in enumerate(val, start=1):
                            print(f"     - {idx}. {item.name} ({item.domain or 'No website'}) - {item.industry or 'N/A'}")
                    elif key == "contacts":
                        print(f"   State delta (contacts): discovered {len(val)} contacts:")
                        for idx, item in enumerate(val, start=1):
                            print(f"     - {idx}. {item.name} ({item.title or 'N/A'}) - {item.email or 'No email'} at {item.company_name}")
                    elif key == "emails":
                        print(f"   State delta (emails): drafted {len(val)} personalized outreach emails.")
                    elif key == "logs":
                        for log_line in val:
                            print(f"   Log: {log_line}")
    except Exception as e:
        print(f"\n[Runner] Critical failure executing the graph: {e}")
        sys.exit(1)

    print("\n[Runner] Graph run completed. Retrieving final state from thread checkpointer memory...")
    
    # 6. Retrieve the compiled state from memory saver to show thread persistence
    final_state = agent.get_state(run_config).values

    print("\n" + "=" * 65)
    print("                          FINAL RUN METRICS                      ")
    print("=" * 65)
    print(f"  Discovered Companies : {len(final_state.get('companies', []))}")
    print(f"  Identified Contacts  : {len(final_state.get('contacts', []))}")
    print(f"  Generated Email Drafts: {len(final_state.get('emails', []))}")
    print("=" * 65)

    # 7. Persist to Sheets / CSV
    print("\n[Runner] Executing LeadLogger.log_leads() to persist data...")
    logger = LeadLogger()
    logger.log_leads(
        final_state.get("companies", []),
        final_state.get("contacts", []),
        final_state.get("emails", [])
    )

    print("\n[Runner] Pipeline run finished successfully! View results in your sheet or 'leads_log.csv'.\n")


if __name__ == "__main__":
    main()
