import os
import csv
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
    location: str = "United States"
    limit: int = 3


def run_agent_workflow(thread_id: str, niche: str, location: str, limit: int):
    """Executes the LangGraph agent on a background thread."""
    initial_state = {
        "target_niche": niche,
        "location": location,
        "max_results": limit,
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
            "error": None
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
                        elif key == "contacts":
                            db["contacts"].extend([c.model_dump() for c in val])
                            db["logs"].append(f"[{ts}] Discovered {len(val)} contacts.")
                        elif key == "emails":
                            db["emails"].extend([e.model_dump() for e in val])
                            db["logs"].append(f"[{ts}] Generated {len(val)} outreach emails.")
                        elif key == "logs":
                            for line in val:
                                db["logs"].append(f"[{ts}] {line}")

        # Persist results
        companies_obj = [PydanticCompany(**c) for c in runs_db[thread_id]["companies"]]
        contacts_obj = [PydanticContact(**c) for c in runs_db[thread_id]["contacts"]]
        emails_obj = [PydanticEmailDraft(**e) for e in runs_db[thread_id]["emails"]]

        with runs_lock:
            runs_db[thread_id]["logs"].append(
                f"[{datetime.now().strftime('%H:%M:%S')}] Writing leads to persistence layer..."
            )

        logger = LeadLogger()
        logger.log_leads(companies_obj, contacts_obj, emails_obj)

        with runs_lock:
            db = runs_db[thread_id]
            db["status"] = "completed"
            db["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Pipeline completed successfully.")

    except Exception as e:
        with runs_lock:
            db = runs_db[thread_id]
            db["status"] = "failed"
            db["error"] = str(e)
            db["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Fatal error: {str(e)}")


def read_csv_leads() -> List[Dict[str, str]]:
    """Reads leads_log.csv as a list of dicts."""
    if not os.path.exists("leads_log.csv"):
        return []
    leads = []
    try:
        with open("leads_log.csv", mode="r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                leads.append(row)
    except Exception as e:
        print(f"[API] Error reading leads_log.csv: {e}")
    return leads


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
        limit=request.limit
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
    return {"leads": read_csv_leads()}


@app.get("/api/download")
def download_csv():
    if not os.path.exists("leads_log.csv"):
        raise HTTPException(status_code=404, detail="No CSV file found.")
    return FileResponse(path="leads_log.csv", filename="leads_log.csv", media_type="text/csv")
