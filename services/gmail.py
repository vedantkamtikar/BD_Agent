import os
import base64
import logging
from email.message import EmailMessage
from typing import Optional, Dict, Any, List

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

logger = logging.getLogger(__name__)

# Scopes required for creating drafts in Gmail
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


class GmailService:
    """
    Service wrapper around Google Gmail API v1 to authenticate and push
    personalized outreach email drafts directly to the user's Gmail Drafts folder.
    """
    def __init__(
        self,
        client_secrets_file: str = config.GMAIL_CLIENT_SECRETS_FILE,
        token_file: str = config.GMAIL_TOKEN_FILE
    ):
        self.client_secrets_file = client_secrets_file
        self.token_file = token_file
        self.service = None
        self._initialize_service()

    def _initialize_service(self):
        """Authenticates via OAuth 2.0 and builds the Gmail v1 service resource."""
        if config.MOCK_LLM:
            print("[GmailService] [MOCK MODE] Gmail API initialized in mock mode.")
            return

        creds = None
        # Load token if it exists
        if os.path.exists(self.token_file):
            try:
                creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
            except Exception as e:
                print(f"[GmailService] Warning: Could not load token file '{self.token_file}': {e}")
                creds = None

        # Refresh or obtain new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    print("[GmailService] Refreshing expired OAuth2 credentials token...")
                    creds.refresh(Request())
                except Exception as exc:
                    print(f"[GmailService] Token refresh failed: {exc}. Re-authenticating...")
                    creds = None

            if not creds:
                if os.path.exists(self.client_secrets_file):
                    print(f"[GmailService] Authenticating via '{self.client_secrets_file}'...")
                    try:
                        flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_file, SCOPES)
                        creds = flow.run_local_server(port=8080)
                        # Save the credentials for next run
                        with open(self.token_file, "w") as token:
                            token.write(creds.to_json())
                        print(f"[GmailService] Saved OAuth token to '{self.token_file}'.")
                    except Exception as flow_err:
                        print(f"[GmailService] Notice: Could not open browser for interactive login ({flow_err}).")
                        print("[GmailService] If running on Render or in a headless environment, provide 'token.json' as a Secret File.")
                        creds = None
                else:
                    print(f"[GmailService] Warning: Neither '{self.token_file}' nor '{self.client_secrets_file}' found.")
                    return

        if creds:
            try:
                self.service = build("gmail", "v1", credentials=creds)
                print("[GmailService] Successfully connected to Google Gmail API.")
            except Exception as e:
                print(f"[GmailService] Error building Gmail API service: {e}")

    def create_draft(self, to_email: str, subject: str, body: str) -> Optional[Dict[str, Any]]:
        """
        Creates a draft email in the user's Gmail Drafts folder.
        """
        if config.MOCK_LLM:
            print(f"[GmailService] [MOCK MODE] Simulating Gmail draft creation for '{to_email}'...")
            return {"id": "mock_draft_id", "message": {"id": "mock_message_id"}}

        if not self.service:
            print("[GmailService] Error: Gmail API service not authenticated. Draft creation skipped.")
            return None

        try:
            # Construct MIME Email Message
            message = EmailMessage()
            message.set_content(body or "")
            message["To"] = to_email or ""
            message["Subject"] = subject or "Outreach Email"

            # Encoded raw base64 string
            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
            create_message = {"message": {"raw": encoded_message}}

            # Execute Gmail API call
            draft = self.service.users().drafts().create(
                userId="me",
                body=create_message
            ).execute()

            draft_id = draft.get("id")
            print(f"[GmailService] Draft successfully created in Gmail (Draft ID: {draft_id}) for '{to_email}'.")
            return draft

        except HttpError as error:
            print(f"[GmailService] Gmail API HttpError while creating draft for '{to_email}': {error}")
            return None
        except Exception as error:
            print(f"[GmailService] Error creating draft for '{to_email}': {error}")
            return None

    def batch_create_drafts(self, email_drafts: List[Any], logs_out: Optional[List[str]] = None) -> int:
        """
        Iterates over a list of EmailDraft objects and creates drafts in Gmail for each.
        Returns count of successfully created drafts.
        """
        if logs_out is None:
            logs_out = []

        if not email_drafts:
            log_msg = "[GmailService] No email drafts to sync."
            print(log_msg)
            logs_out.append(log_msg)
            return 0

        success_count = 0
        total = len(email_drafts)
        log_msg = f"[GmailService] Syncing {total} customized email drafts to Gmail Drafts..."
        print(log_msg)
        logs_out.append(log_msg)

        for idx, draft in enumerate(email_drafts, 1):
            to_email = getattr(draft, "contact_email", "")
            subject = getattr(draft, "subject", "")
            body = getattr(draft, "body", "")

            if not to_email or to_email == "N/A" or "@" not in to_email:
                continue

            print(f"[GmailService] [{idx}/{total}] Creating Gmail draft for '{to_email}'...")
            res = self.create_draft(to_email=to_email, subject=subject, body=body)
            if res:
                success_count += 1

        log_msg = f"[GmailService] Synced {success_count}/{total} drafts into your Gmail Drafts folder."
        print(log_msg)
        logs_out.append(log_msg)
        return success_count
