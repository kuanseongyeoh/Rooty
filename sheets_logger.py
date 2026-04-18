import gspread
from google.oauth2 import service_account
import json
import os

# THE MASTER TELEMETRY BRIDGE (LIGHTNING FAST / BACKGROUND READY)
# Removed streamlit dependency to allow background threading without UI blocks.
def get_sheets_client(secrets=None):
    try:
        JSON_KEY_PATH = "rooty-leaderboard-firebase-adminsdk-fbsvc-ebf80e2d1b.json"
        
        # Priority 1: Local File (Dev Environment)
        if os.path.exists(JSON_KEY_PATH):
            with open(JSON_KEY_PATH, "r") as f:
                creds_dict = json.load(f)
        # Priority 2: Passed Secrets (Cloud Prod)
        elif secrets and "FIREBASE_SERVICE_ACCOUNT" in secrets:
            creds_dict = secrets["FIREBASE_SERVICE_ACCOUNT"]
            if isinstance(creds_dict, str):
                creds_dict = json.loads(creds_dict)
        else:
            return None
        
        # Memory-Only Authorization (Proven Success)
        creds = service_account.Credentials.from_service_account_info(creds_dict)
        scoped_creds = creds.with_scopes([
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ])
        return gspread.authorize(scoped_creds)
    except:
        return None

def log_event(data, secrets=None):
    """
    Designed to run in a background thread. 
    Does not use st.toast or any blocking UI calls.
    """
    try:
        client = get_sheets_client(secrets)
        if not client: return False
        
        # Spreadsheet Surgical Strike
        SHEET_ID = "1A87ZWYHEKS1j1KQlTwoeSEYkLCJ0EncuaofTR74FZns"
        spreadsheet = client.open_by_key(SHEET_ID)
        worksheet = spreadsheet.get_worksheet(0)

        # Map the row (Nickname, Timestamp, Session ID, Full Data)
        row = [
            data.get("id", {}).get("nickname", "unknown"),
            data.get("timing", {}).get("end_ts_utc0") or data.get("timing", {}).get("start_ts_utc0") or data.get("timing", {}).get("ts_utc0") or "n/a",
            data.get("id", {}).get("session_id", "unknown"),
            json.dumps(data)
        ]
        
        worksheet.append_row(row)
        return True
    except:
        return False
