# B2B Lead-Generation & CSR Outreach Agentic Pipeline

An intelligent, autonomous B2B and CSR lead-generation pipeline powered by **LangGraph**, **Gemini API** (for reasoning & structured parsing), **Serper.dev** (Google Search grounding), **Pydantic (v2)**, **SQLAlchemy**, and **Gmail API**.

The agent automates end-to-end prospecting: discovering companies matching niche/location/revenue filters, identifying CSR & foundation initiatives, finding verified executive contacts & emails, drafting personalized partnership outreach emails with meeting requests, and syncing leads to Google Sheets, local CSV, and PostgreSQL / SQLite databases.

---

## 🛠️ Tech Stack & Key Capabilities

- **LangGraph**: Orchestrates the multi-step state graph with dynamic reducers, checkpoint memory, and conditional routing.
- **Gemini API**: Generates structured Pydantic extractions, company intelligence, and context-aware outreach emails tailored for NGO/CSR partnerships.
- **Serper.dev API**: Fast, reliable Google Search API used for company discovery, executive contact hunting, and deep CSR foundation enrichment.
- **SQLAlchemy & Database Layer**: Supports both **PostgreSQL** (e.g. on Render/Railway) and local **SQLite** (`leads.db`) with dynamic failover and automatic column migrations.
- **Pydantic (v2)**: Strict schema validation across `Company`, `Contact`, `EmailDraft`, and `LeadState` models.
- **Gmail API (OAuth2)**: Automatically creates drafts in your connected Gmail account ready for review and sending.
- **Google Sheets API**: Appends new leads in real-time to your Google Spreadsheet with local CSV (`leads_log.csv`) fallback.
- **FastAPI & Modern Web Dashboard**: Real-time interactive UI with live streaming logs, campaign management, lead exports, and progress tracking.

---

## 📂 Project Architecture

```text
BD_Agent/
├── config.py              # Environment variable loading, validation & startup diagnostics
├── models.py              # Pydantic schemas (Company, Contact, EmailDraft, LeadState)
├── database.py            # SQLAlchemy models, Render PostgreSQL auto-repair & SQLite fallback
├── graph.py               # LangGraph compiled pipeline (Nodes, Edges, State Checkpointer)
├── main.py                # Command-line interface (CLI) with interactive & headless modes
├── web_server.py          # FastAPI application serving UI, REST APIs & background task runners
├── run_web.py             # Uvicorn entrypoint script with port & browser auto-launch logic
├── services/
│   ├── gemini.py          # Grounded company discovery, CSR enrichment, contact hunt & email drafting
│   ├── serper.py          # Serper.dev Google Search service wrapper
│   ├── gmail.py           # Gmail OAuth2 service for drafting outreach emails directly in Gmail
│   └── google_sheets.py   # Google Sheets API client with local CSV backup
├── static/                # Modern Web Dashboard frontend (HTML5, Vanilla CSS, Vanilla JS)
│   ├── index.html         # Lead generation dashboard & campaign view
│   ├── style.css          # Dark-mode styling, animations, responsive tables
│   └── app.js             # Real-time state polling, log streams, search filter controls
├── Dockerfile             # Production-ready Docker container configuration
├── docker-compose.yml     # Local multi-container development configuration
├── Procfile               # Cloud deployment process file (Render / Railway / Heroku)
├── requirements.txt       # Production dependencies with pinned versions
└── README.md              # Project documentation
```

---

## 🌟 Key Features

### 1. 🤝 CSR (Corporate Social Responsibility) & NGO Outreach
- **Targeted CSR Discovery**: Filter companies by specific CSR focus areas (e.g. *Education, Healthcare, Rural Development, Sustainability*).
- **Per-Company CSR Deep Lookup**: Automatically queries company foundations (e.g. *Tata Trusts, Infosys Foundation, Persistent Foundation*) to extract real impact initiatives instead of generic placeholders.
- **NGO Partnership Emails**: Generates customized outreach emails requesting a 10–15 minute introductory meeting to discuss joint social impact programs.

### 2. 🎯 Precision Targeting & Financial Filtering
- **Revenue Bounds**: Filter companies by minimum and maximum annual revenue (e.g., `₹50 Cr`, `$10M`, `₹500 Cr`).
- **HQ Location Enforcement**: Strictly filters out branch offices to target corporate headquarters and decision-making centers.

### 3. 🔍 Multi-Phase Contact & Verified Email Hunt
- **Executive Discovery**: Prioritizes C-suite leadership (CEO > MD > CHRO > Director of CSR).
- **Email Discovery**: Executes multi-pattern search and regex validation to find verified business emails, skipping generic mailboxes (`info@`, `support@`).

### 4. 🗄️ Resilient Cloud & Local Storage
- **PostgreSQL on Render / Railway**: Seamlessly connects via `DATABASE_URL`.
- **Render Host Auto-Resolution**: Automatically detects and repairs private Render hostnames (`dpg-...-a`) that fail cross-region DNS.
- **Zero-Downtime SQLite Fallback**: If PostgreSQL is temporarily unreachable or unconfigured, the app falls back to `sqlite:///leads.db` automatically without crashing the server.

---

## ⚙️ Installation & Setup

### 1. Prerequisites
- Python 3.11+
- [Serper.dev API Key](https://serper.dev) (for Google Search)
- [Google Gemini API Key](https://aistudio.google.com/)

### 2. Virtual Environment Setup
```bash
# Clone the repository
git clone https://github.com/vedantkamtikar/BD_Agent.git
cd BD_Agent

# Create & activate a virtual environment
python -m venv bd_agent
# Windows:
.\bd_agent\Scripts\activate
# Linux/macOS:
source bd_agent/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env`:
```bash
# Windows
copy .env.example .env
# Linux/macOS
cp .env.example .env
```

Edit `.env` and fill in your keys:
```env
# Required API Keys
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-1.5-flash
SERPER_API_KEY=your_serper_api_key_here

# Database Configuration (Defaults to SQLite if omitted)
DATABASE_URL=sqlite:///leads.db
# Or PostgreSQL: postgresql://user:password@host:5432/dbname

# Google Sheets Logging (Optional)
GOOGLE_SHEET_ID=your_spreadsheet_id
GOOGLE_APPLICATION_CREDENTIALS=service_account.json

# Gmail OAuth2 Integration (Optional)
GMAIL_CLIENT_SECRETS_FILE=client_secrets.json
GMAIL_TOKEN_FILE=token.json

# Simulation Mode (Set to true to test UI without consuming API credits)
MOCK_LLM=false
```

---

## 🚀 Running the Application

### 1. Interactive Web Dashboard (Recommended)
```bash
python run_web.py
```
Open your browser at `http://localhost:8000` to launch new campaigns, view live execution logs, and export leads to CSV.

### 2. Command-Line Interface (CLI)
- **Interactive Mode**:
  ```bash
  python main.py
  ```
- **Automated / Headless Mode**:
  ```bash
  python main.py --niche "Renewable Energy" --location "Mumbai" --limit 5 --csr-focus "Rural Electrification"
  ```

### 3. Docker
```bash
docker compose up --build
```

---

## ☁️ Deployment on Render

This project is pre-configured for one-click deployment on **Render**:

1. **Create a Web Service**:
   - Connect your GitHub repository.
   - **Environment**: `Python`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python run_web.py`
2. **Set Environment Variables**:
   - `GEMINI_API_KEY` = your Gemini key
   - `SERPER_API_KEY` = your Serper.dev key
   - `DATABASE_URL` = your PostgreSQL connection string (use the **External Database URL** from your Render Postgres dashboard if services reside in different regions).
   - `PORT` = `10000`
3. Deploy! The service will initialize the database tables and start listening on port 10000.

---

## 📄 License
This project is open-source and available under the [MIT License](LICENSE).
