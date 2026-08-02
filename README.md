# B2B Lead-Generation Agentic Pipeline

An agentic B2B lead-generation pipeline built using **LangGraph**, **Gemini API** (with Google Search Grounding), **Pydantic**, and **Tenacity**. 

Unlike rigid linear workflows, this agent evaluates findings dynamically to adjust its routing path—for example, skipping the outreach email drafting node if no executive contacts are discovered for a company.

---

## 🛠️ Tech Stack & Concepts Covered
- **LangGraph**: Orchestrates the state graph, nodes, normal edges, conditional edges, and state checkpoint memory.
- **Pydantic (v2)**: Enforces structured schema validation (`Company`, `Contact`, and `EmailDraft` models) to prevent data corruption.
- **Google Search Grounding**: Connects the Gemini model directly to Google Search to discover real-time companies, domains, and contact records.
- **Tenacity**: Implements exponential-backoff retry logic to handle Gemini's free-tier rate limits (15 RPM).
- **Google Sheets API**: Logs leads into a Google Spreadsheet, with automatic fallback to a local `leads_log.csv` file.

---

## 📂 Project Architecture

```text
BD_Agent/
├── config.py              # Environment variable loading & diagnostic verification
├── models.py              # Pydantic schemas for Company, Contact, and EmailDraft
├── graph.py               # Compiles the LangGraph nodes, routing edges, and checkpoint memory
├── main.py                # Command-line interface supporting interactive prompts & arguments
├── services/
│   ├── gemini.py          # Grounded search, structured parsing, and email copy generation
│   └── google_sheets.py   # Handles Google Sheets logging with local CSV fallback
├── .env.example           # Configuration template file
└── README.md              # Project onboarding guide (this file)
```

---

## ⚙️ Local Installation & Setup

1. **Verify Python & Virtual Environment**:
   The virtual environment is pre-configured with Python 3.13 in the `bd_agent` folder.

2. **Install Dependencies**:
   Install the libraries via `uv` in your virtual environment:
   ```bash
   uv pip install --python .\bd_agent\Scripts\python.exe langchain-google-genai langgraph pydantic tenacity google-api-python-client google-auth-httplib2 google-auth-oauthlib python-dotenv requests
   ```

3. **Configure Environment Variables**:
   Copy `.env.example` to a new `.env` file:
   ```bash
   copy .env.example .env
   ```
   Open `.env` and fill in your details:
   - **`GEMINI_API_KEY`**: Your Google Gemini Developer API key.
   - **`MOCK_LLM`**: Set to `true` to run a fully simulated agent workflow locally (no keys required, zero rate-limiting).
   - **`GOOGLE_SHEET_ID`** & **`GOOGLE_APPLICATION_CREDENTIALS`**: Provide a spreadsheet ID and path to service account key JSON to enable Google Sheets logging. (If omitted, the agent automatically logs to a local `leads_log.csv` file).

---

## 🚀 How to Run the Agent

### 1. Web UI Mode (Recommended)
Launch the interactive web-based dashboard:
```bash
.\bd_agent\Scripts\python.exe run_web.py
```
Open your browser and navigate to `http://localhost:8000`.

### 2. Interactive CLI Mode
Launch the runner directly inside your terminal and follow the prompts:
```bash
.\bd_agent\Scripts\python.exe main.py
```

### 3. Non-Interactive CLI Mode (Argument-based)
Run the agent in a single command (ideal for automated cron jobs or background tests):
```bash
.\bd_agent\Scripts\python.exe main.py --niche "DevOps consulting agencies" --location "United States" --limit 2
```

---

## ✨ Features Added
- **🏢 Company Enrichment**: Automatically extracts and displays company employee count, founding year, and headquarters details.
- **🎭 Multiple Outreach Tones**: Selectable tone dropdown in the UI (Formal, Conversational, Bold) to dynamically adjust the writing style of generated cold email drafts.
- **📊 Real-time Progress Bar**: Interactive visual progress bar displaying real-time task status updates (e.g. *"Contacting 2/3 companies..."*).

---

## 🚀 Deployment (Railway / Render)
This project is pre-configured with a `Procfile` for seamless cloud hosting.
1. Connect your repository to **Railway** or **Render**.
2. Set the build command to `pip install -r requirements.txt`.
3. Set the start command to `python run_web.py`.
4. Configure key environment variables:
   - `GEMINI_API_KEY`
   - `SERPER_API_KEY`
   - `HOST` = `0.0.0.0`
   - `PORT` = `8080` (or `10000` for Render)
   - `NO_BROWSER` = `true`

---

## 🎓 Core Agentic Concepts for Learning

### 1. Centralized State & Reducers
The agent stores its memory in a unified state schema. To prevent subsequent nodes from overwriting lists (like adding contacts from different companies), we define **Reducers** via LangGraph's list accumulation syntax:
```python
companies: Annotated[List[Company], operator.add]
```
Whenever a node returns `{"companies": [new_company]}`: LangGraph appends the new company to the existing list rather than replacing it.

### 2. Search vs. Structuring Separation
Gemini API does not support mixing native tools (like Google Search grounding) with structured output schema validation in the same call.
To circumvent this, we implement a **two-step parsing cycle**:
1. **Search Phase**: Call Gemini with `tools=[{"google_search": {}}]` to search the web and return an unstructured Markdown report.
2. **Structuring Phase**: Pass the report to Gemini (without tools) using `.with_structured_output(...)` to extract the records cleanly into our Pydantic schemas.

### 3. Checkpointing and State Persistence
We compile the LangGraph workflow using `MemorySaver` as a checkpointer:
```python
checkpointer = MemorySaver()
agent = builder.compile(checkpointer=checkpointer)
```
Checkpointers store a history of state transitions indexed by a `thread_id`. This lets you inspect states, pause execution, or resume a specific agent thread from the checkpointer database.
