import json
from datetime import datetime
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
import config

# Normalize Render/Heroku postgres:// URLs to postgresql://
db_url = config.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    db_url,
    # SQLite requires connect_args={"check_same_thread": False} to run in background tasks
    connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class CampaignRun(Base):
    __tablename__ = "campaign_runs"

    id = Column(String(50), primary_key=True, index=True)
    niche = Column(String(255), nullable=False)
    location = Column(String(255), nullable=False)
    limit = Column(Integer, default=3)
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
    contact_name = Column(String(255), default="N/A")
    contact_title = Column(String(255), default="N/A")
    linkedin_url = Column(Text, default="N/A")
    contact_email = Column(String(255), default="N/A")
    email_subject = Column(Text, default="N/A")
    email_body = Column(Text, default="N/A")

    campaign = relationship("CampaignRun", back_populates="leads")


def init_db():
    Base.metadata.create_all(bind=engine)
