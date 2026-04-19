import gspread
from google.oauth2 import service_account
import json
import os
import datetime
import traceback
import sys
import streamlit as st

# THE MASTER TELEMETRY BRIDGE (CACHED SYNC VERSION)
@st.cache_resource
def get_sheets_client(creds_dict_json=None):
    try:
        JSON_KEY_PATH = "rooty-leaderboard-firebase-adminsdk-fbsvc-ebf80e2d1b.json"
        
        # Unpack the frozen JSON if provided
        creds_dict = json.loads(creds_dict_json) if creds_dict_json else None
        
        # Priority 1: Direct Handover
        if creds_dict:
            pass
        # Priority 2: Local File (Dev Environment)
        elif os.path.exists(JSON_KEY_PATH):
            with open(JSON_KEY_PATH, "r") as f:
                creds_dict = json.load(f)
        else:
            return None, "No credentials provided or found."
        
        # Memory-Only Authorization
        creds = service_account.Credentials.from_service_account_info(creds_dict)
        scoped_creds = creds.with_scopes([
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ])
        return gspread.authorize(scoped_creds), "Success"
    except Exception as e:
        return None, str(e)

def test_connection(creds_dict=None):
    """Surgical test for UI diagnostics"""
    client, error = get_sheets_client(creds_dict)
    if not client:
        return False, f"Connection Failed: {error}"
    
    try:
        SHEET_ID = "1A87ZWYHEKS1j1KQlTwoeSEYkLCJ0EncuaofTR74FZns"
        spreadsheet = client.open_by_key(SHEET_ID)
        worksheet = spreadsheet.get_worksheet(0)
        # Verify access
        worksheet.cell(1, 1).value
        return True, "Successfully connected and read spreadsheet!"
    except Exception as e:
        traceback.print_exc()
        return False, f"Auth Success, but Sheet Error: {str(e)}"

def log_event(data, creds_dict=None):
    """Designed to run synchronously with a cached client."""
    try:
        # We pass JSON string to cache key to ensure it's hashable
        creds_json = json.dumps(creds_dict) if creds_dict else None
        client, error = get_sheets_client(creds_json)
        if not client: 
            print(f"Background Log Error: {error}", file=sys.stderr)
            return False
        
        SHEET_ID = "1A87ZWYHEKS1j1KQlTwoeSEYkLCJ0EncuaofTR74FZns"
        spreadsheet = client.open_by_key(SHEET_ID)
        worksheet = spreadsheet.get_worksheet(0)

        row = [
            data.get("id", {}).get("nickname", "unknown"),
            data.get("timing", {}).get("end_ts_utc0") or data.get("timing", {}).get("start_ts_utc0") or data.get("timing", {}).get("ts_utc0") or "n/a",
            data.get("id", {}).get("session_id", "unknown"),
            json.dumps(data)
        ]
        
        worksheet.append_row(row)
        print("✅ Background Log Success", file=sys.stdout)
        return True
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return False
