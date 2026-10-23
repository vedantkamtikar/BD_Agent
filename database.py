import json
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
import config

import socket
from urllib.parse import urlsplit, urlunsplit

# Normalize Render/Heroku postgres:// URLs to postgresql://
db_url = config.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)


def resolve_render_internal_host(url: str) -> str:
    """
    If a Render private hostname (e.g. 'dpg-xxxx-a') fails internal DNS resolution,
    attempt to auto-resolve using Render's regional external host suffixes.
    """
    if not (url.startswith("postgresql://") or url.startswith("postgresql+")):
        return url
    try:
        parts = urlsplit(url)
        netloc = parts.netloc
        if "@" in netloc:
            auth, host_port = netloc.rsplit("@", 1)
        else:
            auth, host_port = "", netloc

        if ":" in host_port:
            host, port_str = host_port.split(":", 1)
            port_suffix = ":" + port_str
        else:
            host, port_suffix = host_port, ""

        # Bare Render hostname without dots (e.g., 'dpg-da2be8ou01pc73b24r30-a')
        if host.startswith("dpg-") and "." not in host:
            orig_timeout = socket.getdefaulttimeout()
            try:
                socket.setdefaulttimeout(1.5)
                try:
                    socket.gethostbyname(host)
                    return url  # Internal hostname resolves fine
                except Exception:
                    # Internal DNS failed; try known Render external regions
                    candidates = [
                        ".oregon-postgres.render.com",
                        ".frankfurt-postgres.render.com",
                        ".ohio-postgres.render.com",
                        ".singapore-postgres.render.com",
                        ".virginia-postgres.render.com"
                    ]
                    for suffix in candidates:
                        try:
                            candidate_host = host + suffix
                            socket.gethostbyname(candidate_host)
                            print(f"[Database] Render internal host '{host}' resolved to external host '{candidate_host}'")
                            new_netloc = f"{auth}@{candidate_host}{port_suffix}" if auth else f"{candidate_host}{port_suffix}"
                            return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))
                        except Exception:
                            continue
            finally:
                socket.setdefaulttimeout(orig_timeout)
    except Exception as exc:
        print(f"[Database] Host resolution check notice: {exc}")
    return url


db_url = resolve_render_internal_host(db_url)


def create_app_engine(url: str):
    """Initializes SQLAlchemy engine with resilient PostgreSQL driver fallback."""
    is_sqlite = url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {"connect_timeout": 10}

    try:
        eng = create_engine(url, connect_args=connect_args)
        if not is_sqlite:
            # Eagerly verify dbapi driver is importable
            _ = eng.dialect.dbapi
        return eng
    except Exception as exc:
        if "psycopg" in str(exc).lower() and url.startswith("postgresql"):
            if "postgresql+psycopg://" in url:
                fallback_url = url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
            elif "postgresql+psycopg2://" in url:
                fallback_url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
            else:
                fallback_url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
            print(f"[Database] Notice: Driver fallback triggered ({exc}). Trying: {fallback_url.split('@')[-1]}")
            return create_engine(fallback_url, connect_args=connect_args)
        raise exc


engine = create_app_engine(db_url)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class CampaignRun(Base):
    __tablename__ = "campaign_runs"

    id = Column(String(50), primary_key=True, index=True)
    niche = Column(String(255), nullable=False)
    location = Column(String(255), nullable=False)
    limit = Column(Integer, default=3)
    csr_focus = Column(String(255), default="")
    status = Column(String(50), default="running")
    created_at = Column(DateTime, default=datetime.utcnow)
    logs_json = Column(Text, default="[]")  # JSON encoded list of strings
    error = Column(Text, nullable=True)
    progress_percent = Column(Integer, default=0)
    progress_detail = Column(String(255), default="")

    leads = relationship("LeadRow", back_populates="campaign", cascade="all, delete-orphan")

    @property
    def logs(self):
        try:
            return json.loads(self.logs_json or "[]")
        except Exception:
            return []

    @logs.setter
    def logs(self, value):
        self.logs_json = json.dumps(value or [])


class LeadRow(Base):
    __tablename__ = "lead_rows"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    run_id = Column(String(50), ForeignKey("campaign_runs.id"), nullable=False)
    company_name = Column(String(255), nullable=False)
    company_domain = Column(String(255), default="N/A")
    industry = Column(String(255), default="N/A")
    employees = Column(String(50), default="N/A")
    hq = Column(String(255), default="N/A")
    csr_details = Column(Text, default="N/A")
    contact_name = Column(String(255), default="N/A")
    contact_title = Column(String(255), default="N/A")
    linkedin_url = Column(Text, default="N/A")
    contact_email = Column(String(255), default="N/A")
    email_subject = Column(Text, default="N/A")
    email_body = Column(Text, default="N/A")

    campaign = relationship("CampaignRun", back_populates="leads")


def init_db():
    global engine, db_url
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        print("\n" + "=" * 64)
        print("[DATABASE WARNING] Could not initialize PostgreSQL connection:")
        print(f"  Error: {exc}")
        print("[DATABASE NOTICE] Resilient fallback: Initializing local SQLite database ('sqlite:///leads.db').")
        print("NOTE: To use Render PostgreSQL, make sure DATABASE_URL in your Web Service")
        print("      Environment is set to the 'External Database URL' from your Postgres dashboard.")
        print("=" * 64 + "\n")
        db_url = "sqlite:///leads.db"
        engine = create_app_engine(db_url)
        SessionLocal.configure(bind=engine)
        Base.metadata.create_all(bind=engine)

    # Safe column migration check for existing SQLite and PostgreSQL databases
    try:
        with engine.connect() as conn:
            from sqlalchemy import text
            if db_url.startswith("sqlite"):
                cursor = conn.execute(text("PRAGMA table_info(lead_rows)"))
                columns = [row[1] for row in cursor.fetchall()]
                if columns and "csr_details" not in columns:
                    conn.execute(text("ALTER TABLE lead_rows ADD COLUMN csr_details TEXT DEFAULT 'N/A'"))
                    conn.commit()
                cursor = conn.execute(text("PRAGMA table_info(campaign_runs)"))
                run_columns = [row[1] for row in cursor.fetchall()]
                if run_columns and "csr_focus" not in run_columns:
                    conn.execute(text("ALTER TABLE campaign_runs ADD COLUMN csr_focus VARCHAR(255) DEFAULT ''"))
                    conn.commit()
            elif "postgres" in db_url:
                conn.execute(text("ALTER TABLE lead_rows ADD COLUMN IF NOT EXISTS csr_details TEXT DEFAULT 'N/A'"))
                conn.execute(text("ALTER TABLE campaign_runs ADD COLUMN IF NOT EXISTS csr_focus VARCHAR(255) DEFAULT ''"))
                conn.commit()
    except Exception as e:
        print(f"[DB Init Notice] Migration check: {e}")
