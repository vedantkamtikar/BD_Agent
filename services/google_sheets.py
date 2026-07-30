import os
import csv
from datetime import datetime
from typing import List, Any
from tenacity import retry, stop_after_attempt, wait_exponential
import config

# Define headers for the spreadsheet or CSV log
HEADERS = [
    "Timestamp", 
    "Company Name", 
    "Company Domain", 
    "Industry", 
    "Company Description",
    "Employees",
    "Founded",
    "HQ",
    "Contact Name", 
    "Contact Title", 
    "Contact Email", 
    "Email Subject", 
    "Email Body"
]


class LeadLogger:
    """
    Service to log lead generation results (companies, contacts, and emails).
    Supports logging to Google Sheets and gracefully falls back to a local CSV.
    """
    def __init__(self):
        self.use_sheets = False
        self.service = None
        self.sheet_id = config.GOOGLE_SHEET_ID
        self.creds_path = config.GOOGLE_APPLICATION_CREDENTIALS

        if self.sheet_id and self.creds_path:
            try:
                if os.path.exists(self.creds_path):
                    import json
                    from googleapiclient.discovery import build
                    
                    # Read the credentials file to check its type
                    with open(self.creds_path, 'r') as f:
                        creds_data = json.load(f)
                    
                    scopes = ['https://www.googleapis.com/auth/spreadsheets']
                    
                    if creds_data.get("type") == "service_account":
                        # Standard Service Account Flow
                        from google.oauth2 import service_account
                        creds = service_account.Credentials.from_service_account_file(
                            self.creds_path,
                            scopes=scopes
                        )
                        print("[GoogleSheetsLogger] Using Service Account authentication credentials.")
                    else:
                        # User OAuth 2.0 Web/Installed Flow
                        from google.oauth2.credentials import Credentials
                        from google.auth.transport.requests import Request
                        from google_auth_oauthlib.flow import InstalledAppFlow
                        
                        token_path = 'token.json'
                        creds = None
                        
                        # Load previous login session token if it exists
                        if os.path.exists(token_path):
                            creds = Credentials.from_authorized_user_file(token_path, scopes)
                        
                        # Trigger authorization flow if token is expired or missing
                        if not creds or not creds.valid:
                            if creds and creds.expired and creds.refresh_token:
                                creds.refresh(Request())
                            else:
                                flow = InstalledAppFlow.from_client_secrets_file(
                                    self.creds_path,
                                    scopes=scopes
                                )
                                flow.redirect_uri = 'http://localhost:5678/rest/oauth2-credential/callback'
                                print("[GoogleSheetsLogger] Redirecting to Google account authentication flow in your web browser...")
                                creds = flow.run_local_server(port=5678)
                            
                            # Save credentials session for future runs
                            with open(token_path, 'w') as token_file:
                                token_file.write(creds.to_json())
                        
                        print("[GoogleSheetsLogger] Using User OAuth 2.0 authentication credentials.")
                    
                    self.service = build('sheets', 'v4', credentials=creds)
                    self.use_sheets = True
                    print("[GoogleSheetsLogger] Initialized Google Sheets API service successfully.")
                    self._initialize_sheets_header_if_empty()
                else:
                    print(f"[GoogleSheetsLogger] Key file not found at '{self.creds_path}'. Using local CSV fallback.")
            except Exception as e:
                print(f"[GoogleSheetsLogger] Failed to initialize Google Sheets service: {e}. Using local CSV fallback.")
        else:
            print("[GoogleSheetsLogger] Google Sheets configuration missing (Sheet ID or key path). Using local CSV logging.")

    def _initialize_sheets_header_if_empty(self):
        """
        Initializes the header row on Sheet1 if the sheet is empty.
        """
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range="Sheet1!A1:A1"
            ).execute()
            values = result.get('values', [])
            if not values:
                # The sheet is brand new or empty; write headers to cell A1
                self.service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range="Sheet1!A1",
                    valueInputOption="USER_ENTERED",
                    body={"values": [HEADERS]}
                ).execute()
                print("[GoogleSheetsLogger] Initialized header row in Google Sheet.")
        except Exception as e:
            print(f"[GoogleSheetsLogger] Warning: Could not initialize Sheets headers: {e}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=False)
    def _log_to_sheets_api(self, rows: List[List[Any]]) -> bool:
        """
        Appends the rows to the Google Sheet. Retries automatically on temporary errors (like rate limits).
        """
        if not self.use_sheets or not self.service:
            return False

        self.service.spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range="Sheet1!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows}
        ).execute()
        return True

    def log_leads(self, companies: List[Any], contacts: List[Any], emails: List[Any]):
        """
        Aligns companies, contacts, and emails, and logs them to Sheets or falls back to CSV.
        
        Args:
            companies: List of Company Pydantic models.
            contacts: List of Contact Pydantic models.
            emails: List of EmailDraft Pydantic models.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = []

        # Create quick lookups
        contacts_by_company = {}
        for contact in contacts:
            contacts_by_company.setdefault(contact.company_name, []).append(contact)

        emails_by_contact = {}
        for email in emails:
            # Index by name or email
            emails_by_contact[email.contact_email] = email

        # Deduplicate companies by domain or name
        seen_companies = set()
        unique_companies = []
        for c in companies:
            key = (c.domain or c.name).lower().strip()
            if key and key not in seen_companies:
                seen_companies.add(key)
                unique_companies.append(c)
        companies = unique_companies

        # Align the entities for tabular format
        for company in companies:
            comp_contacts = contacts_by_company.get(company.name, [])

            if not comp_contacts:
                # Scenario: No contacts found for this company (conditional skip triggered)
                row = [
                    timestamp,
                    company.name,
                    company.domain or "N/A",
                    company.industry or "N/A",
                    company.description or "N/A",
                    company.employee_count or "N/A",
                    company.founded_year or "N/A",
                    company.headquarters or "N/A",
                    "N/A (No contacts found)",
                    "N/A",
                    "N/A",
                    "N/A (Email drafting skipped)",
                    "N/A"
                ]
                rows.append(row)
            else:
                # Scenario: Contacts found. Write a row for each contact and draft
                for contact in comp_contacts:
                    draft = emails_by_contact.get(contact.email)
                    row = [
                        timestamp,
                        company.name,
                        company.domain or "N/A",
                        company.industry or "N/A",
                        company.description or "N/A",
                        company.employee_count or "N/A",
                        company.founded_year or "N/A",
                        company.headquarters or "N/A",
                        contact.name,
                        contact.title or "N/A",
                        contact.email or "N/A",
                        draft.subject if draft else "N/A (No draft generated)",
                        draft.body if draft else "N/A"
                    ]
                    rows.append(row)

        if not rows:
            print("[GoogleSheetsLogger] No lead data to log.")
            return

        # Attempt to write to Google Sheets
        sheets_success = False
        if self.use_sheets:
            try:
                sheets_success = self._log_to_sheets_api(rows)
                if sheets_success:
                    print(f"[GoogleSheetsLogger] Successfully logged {len(rows)} rows to Google Sheet.")
            except Exception as e:
                print(f"[GoogleSheetsLogger] Failed to write to Google Sheets: {e}. Falling back to CSV.")

        # Local CSV Fallback
        if not sheets_success:
            self._log_to_csv(rows)

    def _log_to_csv(self, rows: List[List[Any]]):
        """
        Appends the rows to a local CSV file, initializing it with headers if new.
        """
        csv_file = "leads_log.csv"
        file_exists = os.path.exists(csv_file)

        try:
            with open(csv_file, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(HEADERS)
                writer.writerows(rows)
            print(f"[GoogleSheetsLogger] Successfully logged {len(rows)} rows to local CSV: '{os.path.abspath(csv_file)}'.")
        except Exception as e:
            print(f"[GoogleSheetsLogger] Critical Error writing to local CSV: {e}")
