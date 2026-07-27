import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Gemini API configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
MOCK_LLM = os.getenv("MOCK_LLM", "false").lower() == "true"

# Google Sheets API configuration
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# LangSmith / LangChain tracing configuration
LANGCHAIN_TRACING_V2 = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY")
LANGCHAIN_PROJECT = os.getenv("LANGCHAIN_PROJECT", "b2b-lead-generation-agent")


def check_config() -> bool:
    """
    Validates essential environment variables and logs status to the console.
    Returns True if critical configurations (like Gemini) are set, False otherwise.
    """
    print("\n" + "=" * 50)
    print("           AGENT ENVIRONMENT CONFIGURATION          ")
    print("=" * 50)

    has_critical_error = False

    # Check Gemini API Key / Mock Mode
    if MOCK_LLM:
        print("[OK] MOCK_LLM is enabled. Gemini API calls will be simulated locally.")
    else:
        if not GEMINI_API_KEY:
            print("[ERROR] GEMINI_API_KEY is not set.")
            print("        -> Please configure it in your '.env' file.")
            print("        -> Or set MOCK_LLM=true to run without a real key.")
            has_critical_error = True
        else:
            if GEMINI_API_KEY.startswith("AQ."):
                print("[WARN] GEMINI_API_KEY format matches a temporary token (starts with AQ.).")
                print("       -> It may have expired. If you see 401 errors, set MOCK_LLM=true in '.env'.")
            print("[OK] GEMINI_API_KEY is configured.")

    # Check Google Sheets Logging
    if not GOOGLE_SHEET_ID or not GOOGLE_APPLICATION_CREDENTIALS:
        print("[INFO] Google Sheets credentials or Spreadsheet ID not found.")
        print("       -> The agent will write leads locally to 'leads_log.csv' instead.")
    else:
        print(f"[OK] Google Sheets integration active (Sheet ID: {GOOGLE_SHEET_ID})")

    # Check LangSmith Tracing
    if LANGCHAIN_TRACING_V2:
        if not LANGCHAIN_API_KEY:
            print("[WARN] LangSmith tracing enabled (LANGCHAIN_TRACING_V2=true) but LANGCHAIN_API_KEY is missing.")
            print("       -> Tracing will not be active.")
        else:
            print(f"[OK] LangSmith Tracing active. Project: '{LANGCHAIN_PROJECT}'")
    else:
        print("[INFO] LangSmith Tracing is inactive. (To enable, set LANGCHAIN_TRACING_V2=true)")

    print("=" * 50 + "\n")
    return not has_critical_error
