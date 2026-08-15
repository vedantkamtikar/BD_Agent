import os
import uuid
import threading
from datetime import datetime
from typing import Dict, Any, List
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import config
config.check_config()

from graph import agent
from models import Company as PydanticCompany, Contact as PydanticContact, EmailDraft as PydanticEmailDraft
from services.google_sheets import LeadLogger

app = FastAPI(title="B2B Lead-Gen Agent API")

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Thread-safe in-memory run database
runs_db: Dict[str, Dict[str, Any]] = {}
runs_lock = threading.Lock()


class RunRequest(BaseModel):
    niche: str
    location: str = "India"
    limit: int = 3
    min_revenue: str = ""
    max_revenue: str = ""
    sender_name: str = "Alex"
    sender_title: str = "Lead Consultant"
    tone: str = "formal"


def run_agent_workflow(thread_id: str, niche: str, location: str, limit: int, min_revenue: str, max_revenue: str, sender_name: str, sender_title: str, tone: str):
    """Executes the LangGraph agent on a background thread."""
    initial_state = {
        "target_niche": niche,
        "location": location,
        "max_results": limit,
        "min_revenue": min_revenue,
        "max_revenue": max_revenue,
        "sender_name": sender_name,
        "sender_title": sender_title,
        "tone": tone,
        "companies": [],
        "contacts": [],
        "emails": [],
        "logs": []
    }

    run_config = {"configurable": {"thread_id": thread_id}}

    with runs_lock:
        runs_db[thread_id] = {
            "status": "running",
            "logs": [f"[{datetime.now().strftime('%H:%M:%S')}] Pipeline initialized."],
            "companies": [],
            "contacts": [],
            "emails": [],
            "error": None,
            "progress": {"current": 0, "total": 0, "detail": "Initializing..."}
        }

    try:
        for event in agent.stream(initial_state, run_config):
            for node_name, state_update in event.items():
                ts = datetime.now().strftime("%H:%M:%S")

                with runs_lock:
                    db = runs_db[thread_id]
                    db["logs"].append(f"[{ts}] Entered Node: '{node_name}'")

                    for key, val in state_update.items():
                        if key == "companies":
                            db["companies"].extend([c.model_dump() for c in val])
                            db["logs"].append(f"[{ts}] Discovered {len(val)} companies.")
                            # Set progress total from discovered companies
                            db["progress"] = {"current": 0, "total": len(val), "detail": f"Discovered {len(val)} companies"}
                        elif key == "contacts":
                            db["contacts"].extend([c.model_dump() for c in val])
                            db["logs"].append(f"[{ts}] Discovered {len(val)} contacts.")
                        elif key == "emails":
                            db["emails"].extend([e.model_dump() for e in val])
                            db["logs"].append(f"[{ts}] Generated {len(val)} outreach emails.")
                        elif key == "logs":
                            for line in val:
                                db["logs"].append(f"[{ts}] {line}")
                                # Parse progress markers like "[PROGRESS] get_contacts: 3/5"
                                if "[PROGRESS]" in line:
                                    try:
                                        parts = line.split(": ", 1)
                                        if len(parts) == 2:
                                            nums = parts[1].split("/")
                                            current = int(nums[0])
                                            total = int(nums[1])
                                            step_name = parts[0].replace("[PROGRESS] ", "").strip()
                                            detail_map = {
                                                "get_contacts": f"Contacting {current}/{total} companies...",
                                                "draft_emails": f"Drafting email {current}/{total}..."
                                            }
                                            db["progress"] = {
                                                "current": current,
                                                "total": total,
                                                "detail": detail_map.get(step_name, f"Step {current}/{total}")
                                            }
                                    except (ValueError, IndexError):
                                        pass

        # Convert accumulated dicts back to Pydantic objects
        companies_obj = [PydanticCompany(**c) for c in runs_db[thread_id]["companies"]]
        contacts_obj = [PydanticContact(**c) for c in runs_db[thread_id]["contacts"]]
        emails_obj = [PydanticEmailDraft(**e) for e in runs_db[thread_id]["emails"]]

        # Deduplicate companies by domain or name
        seen_domains = set()
        unique_companies = []
        for comp in companies_obj:
            key = (comp.domain or comp.name).lower().strip()
            if key and key not in seen_domains:
                seen_domains.add(key)
                unique_companies.append(comp)
        companies_obj = unique_companies

        # Deduplicate contacts by (name, company_name)
        seen_contact_keys = set()
        unique_contacts = []
        for c in contacts_obj:
            key = (c.name.strip().lower(), c.company_name.strip().lower())
            if key not in seen_contact_keys:
                seen_contact_keys.add(key)
                unique_contacts.append(c)
        contacts_obj = unique_contacts

        # Persist to Google Sheets
        with runs_lock:
            runs_db[thread_id]["logs"].append(
                f"[{datetime.now().strftime('%H:%M:%S')}] Writing leads to Google Sheets..."
            )

        try:
            logger = LeadLogger()
            logger.log_leads(companies_obj, contacts_obj, emails_obj)
        except Exception as sheets_err:
            with runs_lock:
                runs_db[thread_id]["logs"].append(
                    f"[{datetime.now().strftime('%H:%M:%S')}] Sheets warning: {sheets_err}"
                )

        # Build formatted lead rows for the frontend table
        lead_rows = []
        contacts_by_company = {}
        for c in contacts_obj:
            contacts_by_company.setdefault(c.company_name, []).append(c)
        emails_by_contact = {}
        for e in emails_obj:
            emails_by_contact[e.contact_email] = e

        for comp in companies_obj:
            comp_contacts = contacts_by_company.get(comp.name, [])
            if not comp_contacts:
                lead_rows.append({
                    "Company Name": comp.name,
                    "Company Domain": comp.domain or "N/A",
                    "Industry": comp.industry or "N/A",
                    "Employees": comp.employee_count or "N/A",
                    "HQ": comp.headquarters or "N/A",
                    "Contact Name": "N/A (No contacts found)",
                    "Contact Title": "N/A",
                    "Contact Email": "N/A",
                    "Email Subject": "N/A (Email drafting skipped)",
                    "Email Body": "N/A"
                })
            else:
                for contact in comp_contacts:
                    draft = emails_by_contact.get(contact.email)
                    lead_rows.append({
                        "Company Name": comp.name,
                        "Company Domain": comp.domain or "N/A",
                        "Industry": comp.industry or "N/A",
                        "Employees": comp.employee_count or "N/A",
                        "HQ": comp.headquarters or "N/A",
                        "Contact Name": contact.name,
                        "Contact Title": contact.title or "N/A",
                        "Contact Email": contact.email or "N/A",
                        "Email Subject": draft.subject if draft else "N/A (No draft generated)",
                        "Email Body": draft.body if draft else "N/A"
                    })

        with runs_lock:
            db = runs_db[thread_id]
            db["status"] = "completed"
            db["lead_rows"] = lead_rows
            db["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Pipeline completed successfully.")

    except Exception as e:
        with runs_lock:
            db = runs_db[thread_id]
            db["status"] = "failed"
            db["error"] = str(e)
            db["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Fatal error: {str(e)}")



# --- Routes ---

@app.get("/")
def serve_index():
    path = os.path.join("static", "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse(status_code=404, content={"message": "Frontend not found."})


@app.post("/api/run")
def trigger_run(request: RunRequest, background_tasks: BackgroundTasks):
    if not request.niche:
        raise HTTPException(status_code=400, detail="Niche is required.")
    thread_id = str(uuid.uuid4())
    background_tasks.add_task(
        run_agent_workflow,
        thread_id=thread_id,
        niche=request.niche,
        location=request.location,
        limit=request.limit,
        min_revenue=request.min_revenue,
        max_revenue=request.max_revenue,
        sender_name=request.sender_name,
        sender_title=request.sender_title,
        tone=request.tone
    )
    return {"thread_id": thread_id, "status": "started"}


@app.get("/api/status/{thread_id}")
def get_status(thread_id: str):
    with runs_lock:
        if thread_id not in runs_db:
            raise HTTPException(status_code=404, detail="Thread not found.")
        return runs_db[thread_id]


@app.get("/api/leads")
def get_leads():
    """Returns leads from the most recent completed run."""
    with runs_lock:
        latest_leads = []
        for tid, run in runs_db.items():
            if run.get("status") == "completed" and run.get("lead_rows"):
                latest_leads = run["lead_rows"]
        return {"leads": latest_leads}


@app.get("/api/download")
def download_csv():
    if not os.path.exists("leads_log.csv"):
        raise HTTPException(status_code=404, detail="No CSV file found.")
    return FileResponse(path="leads_log.csv", filename="leads_log.csv", media_type="text/csv")
