import os
import uuid
import time
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
RUNS_TTL_SECONDS = 2 * 60 * 60  # Evict completed runs older than 2 hours


def _cleanup_old_runs():
    """Evicts completed/failed runs older than RUNS_TTL_SECONDS to prevent memory leaks."""
    now = time.time()
    expired = [
        tid for tid, run in runs_db.items()
        if run.get("status") in ("completed", "failed")
        and now - run.get("_created_at", now) > RUNS_TTL_SECONDS
    ]
    for tid in expired:
        del runs_db[tid]


class RunRequest(BaseModel):
    niche: str
    location: str = "India"
    limit: int = 3
    min_revenue: str = ""
    max_revenue: str = ""
    sender_name: str = "Alex"
    sender_title: str = "Lead Consultant"
    tone: str = "formal"
    draft_emails_enabled: bool = True
    sync_gmail_drafts: bool = True


def run_agent_workflow(thread_id: str, niche: str, location: str, limit: int, min_revenue: str, max_revenue: str, sender_name: str, sender_title: str, tone: str, draft_emails_enabled: bool = True, sync_gmail_drafts: bool = True):
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
        "draft_emails_enabled": draft_emails_enabled,
        "sync_gmail_drafts": sync_gmail_drafts,
        "companies": [],
        "contacts": [],
        "emails": [],
        "logs": []
    }

    run_config = {"configurable": {"thread_id": thread_id}}

    with runs_lock:
        _cleanup_old_runs()
        runs_db[thread_id] = {
            "status": "running",
            "_created_at": time.time(),
            "logs": [f"[{datetime.now().strftime('%H:%M:%S')}] Pipeline initialized."],
            "companies": [],
            "contacts": [],
            "emails": [],
            "error": None,
            "progress": {"percent": 5, "detail": "Initializing pipeline..."}
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
                            db["progress"] = {"percent": 20, "detail": f"Discovered {len(val)} companies."}
                        elif key == "contacts":
                            db["contacts"].extend([c.model_dump() for c in val])
                            db["logs"].append(f"[{ts}] Discovered {len(val)} contacts.")
                        elif key == "emails":
                            db["emails"].extend([e.model_dump() for e in val])
                            db["logs"].append(f"[{ts}] Generated {len(val)} outreach emails.")
                            db["progress"] = {"percent": 85, "detail": f"Generated {len(val)} outreach drafts."}
                        elif key == "logs":
                            for line in val:
                                db["logs"].append(f"[{ts}] {line}")
                                if "create_gmail_drafts" in line:
                                    db["progress"] = {"percent": 92, "detail": "Syncing drafts to Gmail..."}
                                elif "[PROGRESS]" in line:
                                    try:
                                        parts = line.split(": ", 1)
                                        if len(parts) == 2:
                                            nums = parts[1].split("/")
                                            current = int(nums[0])
                                            total = int(nums[1])
                                            step_name = parts[0].replace("[PROGRESS] ", "").strip()
                                            
                                            if step_name == "get_contacts":
                                                pct = 20 + int((current / max(total, 1)) * 40)
                                                detail = f"Searching contacts ({current}/{total} companies)..."
                                            elif step_name == "draft_emails":
                                                pct = 60 + int((current / max(total, 1)) * 25)
                                                detail = f"Drafting outreach copy ({current}/{total})..."
                                            else:
                                                pct = 50
                                                detail = f"Processing {current}/{total}..."
                                                
                                            db["progress"] = {"percent": min(pct, 95), "detail": detail}
                                    except (ValueError, IndexError):
                                        pass

        # Convert accumulated dicts back to Pydantic objects
        # Note: deduplication is already handled in graph nodes (gemini.py / graph.py)
        companies_obj = [PydanticCompany(**c) for c in runs_db[thread_id]["companies"]]
        contacts_obj = [PydanticContact(**c) for c in runs_db[thread_id]["contacts"]]
        emails_obj = [PydanticEmailDraft(**e) for e in runs_db[thread_id]["emails"]]

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
                    "LinkedIn URL": "N/A",
                    "Contact Email": "N/A",
                    "Email Subject": "N/A",
                    "Email Body": "N/A"
                })
            else:
                for contact in comp_contacts:
                    email_draft = emails_by_contact.get(contact.email)
                    lead_rows.append({
                        "Company Name": comp.name,
                        "Company Domain": comp.domain or "N/A",
                        "Industry": comp.industry or "N/A",
                        "Employees": comp.employee_count or "N/A",
                        "HQ": comp.headquarters or "N/A",
                        "Contact Name": contact.name,
                        "Contact Title": contact.title or "N/A",
                        "LinkedIn URL": getattr(contact, "linkedin_url", None) or "N/A",
                        "Contact Email": contact.email or "N/A",
                        "Email Subject": email_draft.subject if email_draft else "N/A",
                        "Email Body": email_draft.body if email_draft else "N/A"
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

@app.api_route("/", methods=["GET", "HEAD"])
def serve_index():
    path = os.path.join("static", "index.html")
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse(status_code=404, content={"message": "Frontend not found."})


@app.post("/api/run")
def trigger_run(request: RunRequest, background_tasks: BackgroundTasks):
    if not request.niche:
        raise HTTPException(status_code=400, detail="Niche is required.")

    # Concurrency guard: reject if a pipeline is already running
    with runs_lock:
        for tid, run in runs_db.items():
            if run.get("status") == "running":
                raise HTTPException(
                    status_code=409,
                    detail="A pipeline is already running. Please wait for it to complete."
                )

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
        tone=request.tone,
        draft_emails_enabled=request.draft_emails_enabled,
        sync_gmail_drafts=request.sync_gmail_drafts
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
    """Returns leads from the most recently completed run (by creation timestamp)."""
    with runs_lock:
        latest_leads = []
        latest_time = 0
        for tid, run in runs_db.items():
            if run.get("status") == "completed" and run.get("lead_rows"):
                created = run.get("_created_at", 0)
                if created >= latest_time:
                    latest_time = created
                    latest_leads = run["lead_rows"]
        return {"leads": latest_leads}


@app.get("/api/download")
def download_csv():
    if not os.path.exists("leads_log.csv"):
        raise HTTPException(status_code=404, detail="No CSV file found.")
    return FileResponse(path="leads_log.csv", filename="leads_log.csv", media_type="text/csv")
