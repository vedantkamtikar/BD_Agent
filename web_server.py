import os
import uuid
import time
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

from database import init_db, SessionLocal, CampaignRun, LeadRow
init_db()

app = FastAPI(title="B2B Lead-Gen Agent API")

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


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

    db = SessionLocal()
    # Initialize run in DB
    run = CampaignRun(
        id=thread_id,
        niche=niche,
        location=location,
        limit=limit,
        status="running",
        progress_percent=5,
        progress_detail="Initializing pipeline..."
    )
    run.logs = [f"[{datetime.now().strftime('%H:%M:%S')}] Pipeline initialized."]
    db.add(run)
    db.commit()

    accumulated_companies = []
    accumulated_contacts = []
    accumulated_emails = []

    try:
        for event in agent.stream(initial_state, run_config):
            for node_name, state_update in event.items():
                ts = datetime.now().strftime("%H:%M:%S")

                # Reload run to prevent detached session errors
                run = db.query(CampaignRun).filter(CampaignRun.id == thread_id).first()
                current_logs = run.logs
                current_logs.append(f"[{ts}] Entered Node: '{node_name}'")

                for key, val in state_update.items():
                    if key == "companies":
                        accumulated_companies.extend([c.model_dump() for c in val])
                        current_logs.append(f"[{ts}] Discovered {len(val)} companies.")
                        run.progress_percent = 20
                        run.progress_detail = f"Discovered {len(val)} companies."
                    elif key == "contacts":
                        accumulated_contacts.extend([c.model_dump() for c in val])
                        current_logs.append(f"[{ts}] Discovered {len(val)} contacts.")
                    elif key == "emails":
                        accumulated_emails.extend([e.model_dump() for e in val])
                        current_logs.append(f"[{ts}] Generated {len(val)} outreach emails.")
                        run.progress_percent = 85
                        run.progress_detail = f"Generated {len(val)} outreach drafts."
                    elif key == "logs":
                        for line in val:
                            current_logs.append(f"[{ts}] {line}")
                            if "create_gmail_drafts" in line:
                                run.progress_percent = 92
                                run.progress_detail = "Syncing drafts to Gmail..."
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
                                            detail = f"Drafting outreach copy ({current}/{total})...."
                                        else:
                                            pct = 50
                                            detail = f"Processing {current}/{total}..."
                                            
                                        run.progress_percent = min(pct, 95)
                                        run.progress_detail = detail
                                except (ValueError, IndexError):
                                    pass
                run.logs = current_logs
                db.commit()

        # Convert accumulated dicts back to Pydantic objects
        companies_obj = [PydanticCompany(**c) for c in accumulated_companies]
        contacts_obj = [PydanticContact(**c) for c in accumulated_contacts]
        emails_obj = [PydanticEmailDraft(**e) for e in accumulated_emails]

        # Persist to Google Sheets
        run = db.query(CampaignRun).filter(CampaignRun.id == thread_id).first()
        current_logs = run.logs
        current_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Writing leads to Google Sheets...")
        run.logs = current_logs
        db.commit()

        try:
            logger = LeadLogger()
            logger.log_leads(companies_obj, contacts_obj, emails_obj)
        except Exception as sheets_err:
            run = db.query(CampaignRun).filter(CampaignRun.id == thread_id).first()
            current_logs = run.logs
            current_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Sheets warning: {sheets_err}")
            run.logs = current_logs
            db.commit()

        # Build formatted lead rows for the database
        contacts_by_company = {}
        for c in contacts_obj:
            contacts_by_company.setdefault(c.company_name, []).append(c)
        emails_by_contact = {}
        for e in emails_obj:
            emails_by_contact[e.contact_email] = e

        for comp in companies_obj:
            comp_contacts = contacts_by_company.get(comp.name, [])
            if not comp_contacts:
                lead = LeadRow(
                    run_id=thread_id,
                    company_name=comp.name,
                    company_domain=comp.domain or "N/A",
                    industry=comp.industry or "N/A",
                    employees=comp.employee_count or "N/A",
                    hq=comp.headquarters or "N/A",
                    contact_name="N/A (No contacts found)",
                    contact_title="N/A",
                    linkedin_url="N/A",
                    contact_email="N/A",
                    email_subject="N/A",
                    email_body="N/A"
                )
                db.add(lead)
            else:
                for contact in comp_contacts:
                    email_draft = emails_by_contact.get(contact.email)
                    lead = LeadRow(
                        run_id=thread_id,
                        company_name=comp.name,
                        company_domain=comp.domain or "N/A",
                        industry=comp.industry or "N/A",
                        employees=comp.employee_count or "N/A",
                        hq=comp.headquarters or "N/A",
                        contact_name=contact.name,
                        contact_title=contact.title or "N/A",
                        linkedin_url=getattr(contact, "linkedin_url", None) or "N/A",
                        contact_email=contact.email or "N/A",
                        email_subject=email_draft.subject if email_draft else "N/A",
                        email_body=email_draft.body if email_draft else "N/A"
                    )
                    db.add(lead)

        run = db.query(CampaignRun).filter(CampaignRun.id == thread_id).first()
        run.status = "completed"
        current_logs = run.logs
        current_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Pipeline completed successfully.")
        run.logs = current_logs
        db.commit()

    except Exception as e:
        run = db.query(CampaignRun).filter(CampaignRun.id == thread_id).first()
        if run:
            run.status = "failed"
            run.error = str(e)
            current_logs = run.logs
            current_logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] Fatal error: {str(e)}")
            run.logs = current_logs
            db.commit()
    finally:
        db.close()


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
    db = SessionLocal()
    try:
        active_run = db.query(CampaignRun).filter(CampaignRun.status == "running").first()
        if active_run:
            # Check if it is stalled (older than 10 minutes)
            if (datetime.utcnow() - active_run.created_at).total_seconds() > 10 * 60:
                active_run.status = "failed"
                active_run.error = "Pipeline execution stalled / timed out."
                db.commit()
            else:
                raise HTTPException(
                    status_code=409,
                    detail="A pipeline is already running. Please wait for it to complete."
                )
    finally:
        db.close()

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
    db = SessionLocal()
    try:
        run = db.query(CampaignRun).filter(CampaignRun.id == thread_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Thread not found.")
        
        # Format output matching the expected dictionary layout of frontend
        return {
            "status": run.status,
            "logs": run.logs,
            "error": run.error,
            "progress": {
                "percent": run.progress_percent,
                "detail": run.progress_detail
            },
            "lead_rows": [
                {
                    "Company Name": l.company_name,
                    "Company Domain": l.company_domain,
                    "Industry": l.industry,
                    "Employees": l.employees,
                    "HQ": l.hq,
                    "Contact Name": l.contact_name,
                    "Contact Title": l.contact_title,
                    "LinkedIn URL": l.linkedin_url,
                    "Contact Email": l.contact_email,
                    "Email Subject": l.email_subject,
                    "Email Body": l.email_body
                }
                for l in run.leads
            ]
        }
    finally:
        db.close()


@app.get("/api/runs")
def list_runs():
    """Returns a list of all historical runs for the history sidebar."""
    db = SessionLocal()
    try:
        runs = db.query(CampaignRun).order_by(CampaignRun.created_at.desc()).limit(50).all()
        return {
            "runs": [
                {
                    "id": r.id,
                    "niche": r.niche,
                    "location": r.location,
                    "limit": r.limit,
                    "status": r.status,
                    "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                    "lead_count": len(r.leads),
                    "error": r.error
                }
                for r in runs
            ]
        }
    finally:
        db.close()


@app.get("/api/leads")
def get_leads():
    """Returns leads from the most recently completed run."""
    db = SessionLocal()
    try:
        latest_run = db.query(CampaignRun).filter(CampaignRun.status == "completed").order_by(CampaignRun.created_at.desc()).first()
        if not latest_run:
            return {"leads": []}
        
        return {
            "leads": [
                {
                    "Company Name": l.company_name,
                    "Company Domain": l.company_domain,
                    "Industry": l.industry,
                    "Employees": l.employees,
                    "HQ": l.hq,
                    "Contact Name": l.contact_name,
                    "Contact Title": l.contact_title,
                    "LinkedIn URL": l.linkedin_url,
                    "Contact Email": l.contact_email,
                    "Email Subject": l.email_subject,
                    "Email Body": l.email_body
                }
                for l in latest_run.leads
            ]
        }
    finally:
        db.close()


@app.get("/api/download")
def download_csv():
    if not os.path.exists("leads_log.csv"):
        raise HTTPException(status_code=404, detail="No CSV file found.")
    return FileResponse(path="leads_log.csv", filename="leads_log.csv", media_type="text/csv")
