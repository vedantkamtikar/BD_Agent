from pydantic import BaseModel, Field
from typing import Optional


class Company(BaseModel):
    """
    Pydantic schema representing a lead organization discovered during the search phase.
    """
    name: str = Field(description="The formal or commonly known name of the company.")
    domain: Optional[str] = Field(None, description="The official domain of the company website, normalized to lowercase (e.g., 'stripe.com').")
    industry: Optional[str] = Field(None, description="The primary industry or business vertical of the company.")
    employee_count: Optional[str] = Field(None, description="Estimated number of employees (e.g., '500+', '1,000-5,000').")
    headquarters: Optional[str] = Field(None, description="The city/state where the company is headquartered (e.g., 'Pune, Maharashtra').")
    source: Optional[str] = Field(None, description="The search query or contextual source from which the company was found.")


class Contact(BaseModel):
    """
    Pydantic schema representing an individual contact (prospect) working at a lead organization.
    """
    name: str = Field(description="The full name of the contact person.")
    title: Optional[str] = Field(None, description="The job title or professional role of the contact (e.g., 'Founder & CEO').")
    email: Optional[str] = Field(None, description="The business email address of the contact, if publicly searchable or inferred.")
    company_name: str = Field(description="The name of the company where this contact works.")
    company_domain: Optional[str] = Field(None, description="The domain of the company where this contact works.")


class EmailDraft(BaseModel):
    """
    Pydantic schema representing a generated outreach email template tailored to a specific contact.
    """
    contact_name: str = Field(description="The full name of the email recipient.")
    contact_email: str = Field(description="The email address of the email recipient.")
    subject: str = Field(description="The catchy, professional subject line of the outreach email.")
    body: str = Field(description="The fully written, personalized body content of the outreach email.")
    company_name: str = Field(description="The company name of the recipient.")
