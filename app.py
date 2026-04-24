import streamlit as st
import textwrap
from google.cloud import firestore
from google.oauth2 import service_account
import datetime
import random
import time
import math
import os
import uuid
import sheets_logger
import requests
import json
import threading
import traceback
import streamlit.components.v1 as components

# Firestore Config
JSON_KEY_PATH = "rooty-leaderboard-firebase-adminsdk-fbsvc-ebf80e2d1b.json"

# ==========================================
# --- 1. GLOBAL GAME CONFIGURATION ---
# ==========================================
TIME_LIMIT_SECONDS = 5.0
LATENCY_GRACE_PERIOD = 0.5
INSTANT_GAME_OVER_ON_WRONG = 0 

# HUD & Aesthetic Styling
HUD_BG = "#333333"
HUD_BORDER = "#444444"
HUD_LABEL_COLOR = "#cccccc"
HUD_VALUE_COLOR = "#cccccc"
HUD_LABEL_SIZE = "min(4.4vw, 25px)"
HUD_VALUE_SIZE = "min(6vw, 22px)"
ROOTY_COLOR = "#ffd54f"
GAME_OVER_COLOR = "#607d8b"
PRIMARY_BTN_COLOR = "#ef6c00"

# ==========================================
# --- 2. CORE MATHEMATICAL LOGIC ---
# ==========================================
def calculate_digital_root(number):
    if number == 0:
        return 0
    return 1 + (number - 1) % 9

def get_current_time_limit():
    """Calculates time allowance: 5s base for 2 digits, +0.5s per extra digit."""
    digits = st.session_state.curr_meta.get('digits', 2)
    return TIME_LIMIT_SECONDS + (digits - 2) * 0.5

def generate_target_number(round_count):
    GAME_CONFIG = {
        2: [{"max_round": 1, "pool": (0, 9), "sum": (1, 9)}, {"max_round": 2, "pool": (0, 9)}],
        3: [{"max_round": 1, "pool": (0, 5), "sum": (1, 9)}, {"max_round": 3, "pool": (0, 7), "sum": (9, 21)}, {"max_round": 6, "pool": (0, 9)}],
        4: [{"max_round": 3, "pool": (0, 5), "sum": (1, 9)}, {"max_round": 6, "pool": (0, 7), "sum": (8, 28)}, {"max_round": 11, "pool": (0, 9), "subset": (7, 9), "subset_count": (1, 2)}],
        5: [{"max_round": 2, "pool": (0, 5), "sum": (1, 15)}, {"max_round": 4, "pool": (0, 5), "sum": (5, 25)}, {"max_round": 9, "pool": (0, 7), "subset": (5, 7), "subset_count": (1, 2)}, {"max_round": 17, "pool": (0, 9), "subset": (5, 9), "subset_count": (1, 2)}],
        6: [{"max_round": 2, "pool": (0, 5), "sum": (1, 20)}, {"max_round": 5, "pool": (0, 5), "sum": (5, 30)}, {"max_round": 10, "pool": (0, 7), "subset": (5, 7), "subset_count": (1, 2)}, {"max_round": 16, "pool": (0, 9), "subset": (7, 9), "subset_count": (1, 2)}, {"max_round": 24, "pool": (0, 9), "subset": (5, 9), "subset_count": (1, 3)}],
        7: [{"max_round": 2, "pool": (0, 5), "sum": (1, 15)}, {"max_round": 5, "pool": (0, 5), "sum": (5, 35)}, {"max_round": 10, "pool": (0, 7), "subset": (5, 7), "subset_count": (1, 2)}, {"max_round": 16, "pool": (0, 9), "subset": (7, 9), "subset_count": (1, 3)}, {"max_round": 24, "pool": (0, 9), "subset": (5, 9), "subset_count": (1, 4)}, {"max_round": float('inf'), "pool": (0, 9)}]
    }
    accumulated_rounds = 0
    active_digits = 2
    active_rule = None
    for d in sorted(GAME_CONFIG.keys()):
        rules = GAME_CONFIG[d]
        total_d_rounds = rules[-1]["max_round"]
        if total_d_rounds == float('inf') or round_count < accumulated_rounds + total_d_rounds:
            active_digits = d
            local_round = round_count - accumulated_rounds + 1
            for rule in rules:
                if local_round <= rule["max_round"]:
                    active_rule = rule
                    break
            break
        accumulated_rounds += total_d_rounds

    def build_number():
        pool_min, pool_max = active_rule["pool"]
        if "subset" in active_rule:
            subset_min, subset_max = active_rule["subset"]
            count_min, count_max = active_rule["subset_count"]
            k = random.randint(count_min, count_max)
            positions = list(range(active_digits))
            subset_positions = set(random.sample(positions, k))
            num_str = ""
            for i in range(active_digits):
                if i in subset_positions:
                    d_min, d_max = (max(1, subset_min) if i == 0 else subset_min), subset_max
                else:
                    d_min, d_max = (max(1, pool_min) if i == 0 else pool_min), subset_min - 1
                num_str += str(random.randint(d_min, d_max))
            return num_str
        else:
            num_str = ""
            for i in range(active_digits):
                d_min, d_max = (max(1, pool_min) if i == 0 else pool_min), pool_max
                num_str += str(random.randint(d_min, d_max))
            return num_str

    while True:
        num_str = build_number()
        if "sum" in active_rule:
            sum_min, sum_max = active_rule["sum"]
            if not (sum_min <= sum(int(ch) for ch in num_str) <= sum_max): continue
        
        # Return number + difficulty metadata
        meta = {
            "digits": active_digits,
            "tier_label": f"{active_digits}-Dig (Rule {rules.index(active_rule)+1})"
        }
        return int(num_str), meta

def generate_practice_number(digits):
    """Simple generator for practice mode: random digits with no progression rules."""
    num_str = ""
    for i in range(digits):
        d_min = 1 if i == 0 else 0
        num_str += str(random.randint(d_min, 9))
    return int(num_str), {"digits": digits}

# ==========================================
# --- 3. STATE & NAVIGATION HELPERS ---
# ==========================================
def init_state():
    if 'game_status' not in st.session_state: st.session_state.game_status = 'home'
    if 'round_count' not in st.session_state: st.session_state.round_count = 0
    if 'total_score' not in st.session_state: st.session_state.total_score = 0
    if 'nickname' not in st.session_state: st.session_state.nickname = ""
    if 'nick_error' not in st.session_state: st.session_state.nick_error = False
    if 'round_start_time' not in st.session_state: st.session_state.round_start_time = time.time()
    if 'feedback_state' not in st.session_state: st.session_state.feedback_state = 'neutral'
    if 'round_log' not in st.session_state: st.session_state.round_log = []
    if 'max_digits_hit' not in st.session_state: st.session_state.max_digits_hit = 2
    if 'current_game_id' not in st.session_state: st.session_state.current_game_id = ""
    if 'game_active' not in st.session_state: st.session_state.game_active = False
    if 'practice_digits' not in st.session_state: st.session_state.practice_digits = 2
    init_analytics()

@st.cache_resource
def get_db():
    try:
        # Priority 1: Local File (Dev Environment)
        if os.path.exists(JSON_KEY_PATH):
            creds = service_account.Credentials.from_service_account_file(JSON_KEY_PATH)
        # Priority 2: Streamlit Secrets (Production Cloud)
        elif "FIREBASE_SERVICE_ACCOUNT" in st.secrets:
            # Handle both raw strings and pre-parsed TOML dictionaries
            creds_info = st.secrets["FIREBASE_SERVICE_ACCOUNT"]
            if isinstance(creds_info, str):
                creds_info = json.loads(creds_info)
            creds = service_account.Credentials.from_service_account_info(creds_info)
        else:
            return None
        return firestore.Client(credentials=creds)
    except: return None

def get_frozen_creds():
    """Surgically extract and freeze secrets for background threads"""
    try:
        # Priority 1: Cloud Secrets
        if "FIREBASE_SERVICE_ACCOUNT" in st.secrets:
            cinfo = st.secrets["FIREBASE_SERVICE_ACCOUNT"]
            # Convert to plain dict if it's a Streamlit proxy or string
            if hasattr(cinfo, "to_dict"): cinfo = cinfo.to_dict()
            elif isinstance(cinfo, str):
                cinfo = json.loads(cinfo)
            else:
                cinfo = dict(cinfo)
            # Standardizing: Ensure it's a clean dict with no proxy remnants
            return json.loads(json.dumps(cinfo))
        return None
    except: return None

def background_log_sheets(summary, frozen_creds):
    """Bridge for sheets_logger to run safely in a thread with frozen creds"""
    sheets_logger.log_event(summary, frozen_creds)


def get_weekly_cid():
    # ISO week starts on Monday. This ensures global sync at 00:00 UTC Monday.
    now = datetime.datetime.now(datetime.timezone.utc)
    yr, wk, _ = now.isocalendar()
    return f"scores_{yr}_W{wk:02d}"

@st.cache_data(ttl=3600)
def get_geo_info():
    """Privacy-safe: Get country from IP then discard IP."""
    try:
        resp = requests.get("https://ipapi.co/json/", timeout=2)
        data = resp.json()
        return {
            "country": data.get("country_name"),
            "region": data.get("region"),
            "city": data.get("city"),
            "tz": data.get("timezone")
        }
    except: return {}

def get_utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def get_local_now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def extract_ua_info():
    """Capture raw UA for analyst-driven parsing."""
    try:
        ua = st.context.headers.get("User-Agent", "")
        return {"raw_ua": ua}
    except: return {}

def _bg_geo_lookup():
    """Background thread to fetch geo info without blocking the UI."""
    try:
        st.session_state.geo = get_geo_info()
    except: pass

def init_analytics():
    if 'session_id' not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
        st.session_state.device_id = f"gen_{st.session_state.session_id[:8]}" 
        st.session_state.geo = {} # Start empty
        st.session_state.hw_specs = extract_ua_info()
        # Launch geo lookup in background so it doesn't block the "Play Game" button
        threading.Thread(target=_bg_geo_lookup, daemon=True).start()

def log_session_start():
    db = get_db()
    if not db or not st.session_state.current_game_id: return
    try:
        # Pull specs from the bridge
        hw = st.session_state.hw_specs
        doc_data = {
            "id": {
                "game_id": st.session_state.current_game_id,
                "session_id": st.session_state.session_id,
                "nickname": st.session_state.nickname
            },
            "env": {
                "device_id": st.session_state.get('device_id', 'unknown'),
                "geo": st.session_state.get('geo', {}),
                "screen_size": hw.get("screen", "unknown"),
                "viewport_size": hw.get("viewport", "unknown"),
                "platform": hw.get("platform", "unknown"),
                "hw_details": hw
            },
            "timing": {
                "start_ts_utc0": get_utc_now(),
                "start_ts_local": get_local_now(),
                "end_ts_utc0": None,
                "end_ts_local": None,
                "duration_sec": 0
            },
            "perf": {
                "status": "active",
                "score": 0,
                "rounds_cleared": 0,
                "max_digits": 2
            },
            "logs": {
                "rounds": []
            }
        }
        # Surgical Switch: Firestore remains for live tracking, 
        # but we disable Sheets here to ensure "One Game = One Row" (at the end).
        # sheets_logger.log_event(doc_data)
    except: pass

def sync_hw_bridge():
    if st.session_state.hw_bridge_input:
        try:
            specs = json.loads(st.session_state.hw_bridge_input)
            st.session_state.hw_specs.update(specs)
            if 'did' in specs: st.session_state.device_id = specs['did']
            # Re-log start if specs arrive late to ensure screen size is captured
            # CRITICAL: Only log once per session to prevent 1.5s lag on every sync
            if not st.session_state.get('session_start_complete'):
                log_session_start()
                st.session_state.session_start_complete = True
        except: pass

def log_session_end(reason="completed"):
    db = get_db()
    if not db or not st.session_state.current_game_id: return
    try:
        end_time_str = get_utc_now()
        duration = int(time.time() - st.session_state.get('game_start_time', time.time()))
        
        doc_ref = db.collection("sessions").document(st.session_state.current_game_id)
        
        # Build a complete summary that includes identity and environment for the spreadsheet
        summary = {
            "id": {
                "game_id": st.session_state.current_game_id,
                "session_id": st.session_state.session_id,
                "nickname": st.session_state.nickname
            },
            "env": {
                "device_id": st.session_state.get('device_id', 'unknown'),
                "geo": st.session_state.get('geo', {}),
                "hw": st.session_state.hw_specs
            },
            "timing": {
                "end_ts_utc0": end_time_str,
                "end_ts_local": get_local_now(),
                "duration_sec": duration
            },
            "perf": {
                "status": reason,
                "score": st.session_state.total_score,
                "rounds_cleared": st.session_state.round_count,
                "max_digits": st.session_state.max_digits_hit
            },
            "logs": {
                "rounds": st.session_state.round_log
            }
        }
        # Lightning Snapshot: Convert secrets to plain dict for thread safety
        frozen_creds = get_frozen_creds()
        # Restore Background Threading for UI Snappiness
        threading.Thread(target=background_log_sheets, args=(summary, frozen_creds)).start()
    except: pass

def _bg_submit_score(name, score):
    """Background: write score to Firestore using cached client."""
    try:
        db = get_db()
        if not db: return
        cid = get_weekly_cid()
        doc_ref = db.collection(cid).document(name)
        existing = doc_ref.get()
        new_data = {
            "name": name,
            "score": score,
            "level": math.ceil(score/100),
            "ts_utc0": get_utc_now(),
            "ts_local": get_local_now()
        }
        if not existing.exists or score > existing.to_dict().get('score', 0):
            doc_ref.set(new_data)
    except: pass

def submit_score(name, score):
    if not name or score <= 0: return
    # IRONCLAD SENTRY: Atomic One-Shot Guard
    if not st.session_state.get('game_active', False):
        return
    
    try:
        st.session_state.game_active = False
        # 1. ALWAYS log session end
        log_session_end("game_over")

        # 2. Background Score Write (non-blocking, leaderboard uses optimistic rendering)
        threading.Thread(target=_bg_submit_score, args=(name, score)).start()
    except: pass

def fetch_leaderboard_data(nickname, session_score=0):
    db = get_db()
    if not db: return [], None, 0
    try:
        cid = get_weekly_cid()
        # 1. Fetch Top 100 + Total Players (2 queries)
        docs = db.collection(cid).order_by("score", direction=firestore.Query.DESCENDING).limit(100).stream()
        top_100 = [d.to_dict() for d in docs]
        total_players = db.collection(cid).count().get()[0][0].value
        
        # 2. Optimistic User Stats (0 extra queries if in top 100, 1 if not)
        user_stats = None
        if nickname:
            # Check if user is already in the top 100 results
            for i, entry in enumerate(top_100):
                if entry.get('name') == nickname:
                    user_stats = dict(entry)
                    user_stats['rank'] = i + 1
                    break
            
            if not user_stats and session_score > 0:
                # User not in top 100: build optimistic stats from session data
                # Use 1 count query for rank instead of 2 queries (doc fetch + count)
                rank_query = db.collection(cid).where("score", ">", session_score).count().get()
                user_stats = {
                    'name': nickname,
                    'score': session_score,
                    'level': math.ceil(session_score / 100),
                    'rank': rank_query[0][0].value + 1
                }
        
        return top_100, user_stats, total_players
    except: return [], None, 0

@st.cache_data(ttl=60)
def get_world_record():
    db = get_db()
    if not db: return 0
    try:
        docs = db.collection(get_weekly_cid()).order_by("score", direction=firestore.Query.DESCENDING).limit(1).stream()
        for d in docs: return d.to_dict().get('score', 0)
    except: pass
    return 0

def start_game():
    # 1. Resolve nickname: prefer what user just typed, fall back to stored value
    input_nick = st.session_state.get('nickname_input', '').strip()
    current_nick = st.session_state.get('nickname', '').strip()
    
    # 2. Validation: input field wins if present (allows name changes)
    final_nick = input_nick if input_nick else current_nick
    
    if not final_nick:
        st.session_state.game_status = 'home'
        st.session_state.nick_error = True
        return
    
    # 3. Lock-in the identity & uniquely ID this specific game
    st.session_state.nickname = final_nick[:15]
    st.session_state.nick_error = False
    st.session_state.current_game_id = f"game_{get_weekly_cid()}_{str(uuid.uuid4())[:13]}"
        
    st.session_state.game_status = 'playing'
    st.session_state.round_count = 0
    st.session_state.total_score = 0
    st.session_state.round_log = []
    st.session_state.max_digits_hit = 2
    
    n, m = generate_target_number(0)
    st.session_state.target_number = n
    st.session_state.curr_meta = m
    
    st.session_state.game_start_time = time.time()
    st.session_state.round_start_time = time.time()
    st.session_state.feedback_state = 'neutral'
    st.session_state.game_active = True
    log_session_start()

def go_home():
    st.session_state.game_status = 'home'

def go_tutorial():
    st.session_state.game_status = 'tutorial'

def go_leaderboard():
    st.session_state.game_status = 'leaderboard'

def go_practice():
    st.session_state.game_status = 'practice'
    if not st.session_state.get('target_number'):
        n, m = generate_practice_number(st.session_state.get('practice_digits', 2))
        st.session_state.target_number = n
        st.session_state.curr_meta = m

def set_practice_digits(d):
    st.session_state.practice_digits = d
    n, m = generate_practice_number(d)
    st.session_state.target_number = n
    st.session_state.curr_meta = m
    st.session_state.feedback_state = 'neutral'

def set_nickname():
    if st.session_state.nick_input:
        st.session_state.nickname = st.session_state.nick_input[:15]

# ==========================================
# --- 4. CALLBACKS & EVENT HANDLERS ---
# ==========================================
def handle_guess(guess):
    if st.session_state.game_status == 'practice':
        expected = calculate_digital_root(st.session_state.target_number)
        if guess == expected:
            st.session_state.feedback_state = 'correct'
            n, m = generate_practice_number(st.session_state.practice_digits)
            st.session_state.target_number = n
            st.session_state.curr_meta = m
        else:
            st.session_state.feedback_state = 'incorrect'
        return

    limit = get_current_time_limit()
    elapsed = time.time() - st.session_state.round_start_time
    is_timeout = elapsed > (limit + LATENCY_GRACE_PERIOD)
    expected = calculate_digital_root(st.session_state.target_number)
    
    if is_timeout:
        submit_score(st.session_state.get('nickname'), st.session_state.total_score)
        st.session_state.game_status = 'leaderboard'
        st.session_state.feedback_state = 'incorrect'
        return

    if guess == expected:
        # Log this round
        st.session_state.round_log.append({
            "round": st.session_state.round_count + 1,
            "challenge": st.session_state.target_number,
            "guess": guess,
            "correct": True,
            "latency": round(elapsed, 3),
            "meta": st.session_state.curr_meta
        })

        # Score calculation: 10 pts base * digits multiplier * speed bonus
        digits = st.session_state.curr_meta['digits']
        base = 10 * (2**(digits-2))
        limit = get_current_time_limit()
        speed = max(1.0, (limit - elapsed))
        st.session_state.total_score += math.ceil(base * speed)
        
        st.session_state.round_count += 1
        st.session_state.feedback_state = 'correct'
        
        # New target
        n, m = generate_target_number(st.session_state.round_count)
        st.session_state.target_number = n
        st.session_state.curr_meta = m
        if m['digits'] > st.session_state.max_digits_hit:
            st.session_state.max_digits_hit = m['digits']
            
        st.session_state.round_start_time = time.time()
    else:
        # Log incorrect guess
        st.session_state.round_log.append({
            "round": st.session_state.round_count + 1,
            "challenge": st.session_state.target_number,
            "guess": guess,
            "correct": False,
            "latency": round(elapsed, 3),
            "meta": st.session_state.curr_meta
        })
        st.session_state.feedback_state = 'incorrect'
        if INSTANT_GAME_OVER_ON_WRONG == 1: 
            submit_score(st.session_state.get('nickname'), st.session_state.total_score)
            st.session_state.game_status = 'leaderboard'
        else: 
            st.session_state.round_start_time -= 0.5

def handle_timeout_js():
    """Triggered by the JS bridge when the timer hits zero."""
    submit_score(st.session_state.get('nickname'), st.session_state.total_score)
    st.session_state.game_status = 'leaderboard'
    st.session_state.feedback_state = 'incorrect'

# ==========================================
# --- 5. UI STYLING & INJECTION ---
# ==========================================
def inject_global_styles():
    st.markdown("""
    <style>
        /* [1] GLOBAL VIEWPORT RESET: Freeze the screen to a fixed 100dvh box */
        [data-testid="stHeader"], footer, .viewerBadge_container { display: none !important; }
        
        html, body, .stApp, .main, [data-testid="stAppViewBlockContainer"] {
            position: fixed !important; top: 0; bottom: 0; left: 0; right: 0;
            overflow: hidden !important; height: 100dvh !important; width: 100vw !important;
            margin: 0 !important; padding: 0 !important;
            overscroll-behavior: none !important;
        }

        .main .block-container, [data-testid="stAppViewBlockContainer"] {
            padding-top: 0.5dvh !important; padding-bottom: 8dvh !important;
            padding-left: 0px !important; padding-right: 0px !important;
            height: 100dvh !important; width: 100vw !important;
            display: flex; flex-direction: column; justify-content: space-between; align-items: center;
        }

        /* [1.5] NUKE GHOST MARGINS: Stop Streamlit from adding random 1rem gaps */
        [data-testid="stVerticalBlock"] > div { margin: 0 !important; padding: 0 !important; }
        [data-testid="stVerticalBlock"] { gap: 0 !important; }

        /* [1.6] SILENCE THE SPINNER: Hide the 'Running' indicator and Header */
        header, [data-testid="stHeader"] { 
            visibility: hidden !important; 
            height: 0 !important; 
            padding: 0 !important; 
            display: none !important;
        }
        .stStatusWidget, [data-testid="stStatusWidget"] { 
            display: none !important; 
        }

        /* [2] DIALPAD GRID: Force exact 3-column array with zero gaps or padding */
        div[data-testid="stHorizontalBlock"] {
            display: grid !important;
            grid-template-columns: 1fr 1fr 1fr !important;
            width: 100% !important;
            gap: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
        }
        
        div[data-testid="stHorizontalBlock"] > div { width: 100% !important; min-width: 100% !important; }
        
        /* [3] BUTTON MAXIMIZATION & CLEAN LOOK */
        div[data-testid="stButton"], div[data-testid="stButton"] button {
            width: 100% !important;
            margin: 0 !important;
            padding: 0 !important;
        }

        div[data-testid="stHorizontalBlock"] button {
            height: 12dvh !important; 
            border-radius: 0 !important; 
            background-color: #333; 
            color: white; 
            border: 1px solid #1a1a1a !important;
            display: flex !important;
            justify-content: center !important;
            align-items: center !important;
        }

        /* [3.1] Target the actual text label inside the buttons */
        div[data-testid="stHorizontalBlock"] button p {
            font-size: 32px !important;
            font-weight: 800 !important;
        }

        /* [4] MENU CONTAINER: All children share centered layout */
        .menu-btn-container div[data-testid="stButton"] button,
        .menu-btn-container div[data-testid="stTextInput"] {
            width: 85vw !important;
            margin-left: auto !important;
            margin-right: auto !important;
        }

        /* Gaps between menu items */
        .menu-btn-container div[data-testid="stButton"] {
            margin-bottom: 1.2dvh !important;
        }

        .menu-btn-container div[data-testid="stTextInput"] {
            margin-bottom: 5dvh !important;
        }

        .menu-btn-container div[data-testid="stButton"] button {
            height: 9dvh !important;
            border-radius: 12px !important;
            background-color: #333 !important;
            border: 2px solid #444 !important;
            font-size: 22px !important;
            font-weight: 700 !important;
            box-shadow: 0 4px 10px rgba(0,0,0,0.3) !important;
        }

        /* Target Number & Flash FX */
        .challenge-number {
            font-size: var(--target-font); font-weight: 800; text-align: center; color: white;
            letter-spacing: 0.125em; padding: 10px 40px; border-radius: 20px;
        }
        
        /* [7] TEXT INPUT APPEARANCE (global, layout handled by container) */
        div[data-testid="stTextInput"] input {
            text-align: center !important;
            font-size: 20px !important;
            font-weight: 700 !important;
            color: #888 !important;
            background: transparent !important;
            border: none !important;
            border-bottom: 2px solid #444 !important;
            border-radius: 0 !important;
        }

        div[data-testid="stTextInput"] input::placeholder {
            font-weight: 400 !important;
            opacity: 0.5 !important;
        }
        
        div[data-testid="stTextInput"] input:focus {
            border-bottom: 2px solid #ffd54f !important;
            box-shadow: none !important;
        }

        /* [8] ATOMIC REMOVAL OF ALL WIDGET PROMPTS (Press Enter, etc) */
        [data-testid="stWidgetInstructions"], 
        [data-testid="InputInstructions"] {
            display: none !important;
            opacity: 0 !important;
            height: 0 !important;
            visibility: hidden !important;
        }

        /* Hide the Analytics Data Bridge Widget */
        div[data-testid="stTextInput"]:has(input[aria-label="HW_BRIDGE"]) {
            display: none !important;
        }
    </style>
    """, unsafe_allow_html=True)
    components.html("""<script>
    const p = window.parent.document;
    
    // [1] SURGICAL TOUCH CONTROLLER
    p.addEventListener('touchmove', (e) => {
        const scroller = p.getElementById('rooty-rank-scroller');
        const isInside = scroller && scroller.contains(e.target);
        if (!isInside) e.preventDefault();
    }, { passive: false });
    p.addEventListener('scroll', (e) => { e.preventDefault(); window.parent.scrollTo(0,0); });

    // [2] HARDWARE SCOUT BRIDGE
    const probe = () => {
        try {
            const specs = {
                screen: `${window.screen.width}x${window.screen.height}`,
                viewport: `${window.innerWidth}x${window.innerHeight}`,
                platform: navigator.platform,
                ratio: window.devicePixelRatio,
                ua: navigator.userAgent
            };
            const bridge = p.querySelector('input[aria-label="HW_BRIDGE"]');
            if (bridge) {
                bridge.value = JSON.stringify(specs);
                bridge.dispatchEvent(new Event('input', {bubbles:true}));
            }
        } catch(e) { console.warn("Rooty: Probe failed", e); }
    };
    probe(); // Run immediately
    setTimeout(probe, 2000); // Run again later to catch any delayed Streamlit renders

    // [3] THE WIDGET PURGER (Ironclad Sentry - React Friendly)
    // This hides the elements without deleting them to avoid React crashes
    const purge = () => {
        p.querySelectorAll('[data-testid="InputInstructions"], [data-testid="stWidgetInstructions"]').forEach(el => {
            el.style.display = 'none';
            el.style.opacity = '0';
            el.style.height = '0';
            el.style.pointerEvents = 'none';
        });
    };
    const observer = new MutationObserver(purge);
    observer.observe(p, { childList: true, subtree: true });
    purge(); // Run once immediately
    </script>""", height=0, width=0)

# ==========================================
# --- 6. PAGE RENDERERS ---
# ==========================================
def render_home():
    st.markdown(f"""
    <div style="text-align: center; margin-top: 5vh; width: 100%;">
        <p style="color: {ROOTY_COLOR}; font-size: 24vw; margin: 0; font-weight: 800; line-height: 1; letter-spacing: -0.05em;">Rooty!</p>
        <p style="color: #FFF; font-size: 5vw; margin: 0; margin-top: 5px;">Challenge the Math</p>
    </div>
    """, unsafe_allow_html=True)

    # Nickname Error Alert (Floating ABOVE the container to avoid shifting layout)
    alert_html = ""
    if st.session_state.get('nick_error'):
        alert_html = '<div style="position: absolute; top: -40px; left: 0; right: 0; text-align: center; color: #ff5252; font-weight: 700; font-size: 12px;">⚠️ PLEASE ENTER A NICKNAME TO PLAY</div>'
    
    st.markdown(f'<div class="menu-btn-container" style="margin-top: calc(15dvh); position: relative;">{alert_html}', unsafe_allow_html=True)

    # --- [7] CLEAN MINIMALIST NICKNAME ENTRY ---
    st.text_input("NICKNAME", 
                key="nickname_input", 
                value=st.session_state.nickname,
                placeholder="Enter nickname",
                label_visibility="collapsed")
    
    # Vertical Spacer for Gap
    st.markdown('<div style="margin-bottom: 5dvh;"></div>', unsafe_allow_html=True)
    
    st.button("PLAY GAME", on_click=start_game, use_container_width=True, type="primary")
    st.button("PRACTICE MODE", on_click=go_practice, use_container_width=True)
    st.button("TUTORIAL", on_click=go_tutorial, use_container_width=True)
    st.button("LEADERBOARD", on_click=go_leaderboard, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # Privacy Disclaimer Footer
    st.markdown(f"""
    <div style="position: fixed; bottom: 1vh; left: 0; right: 0; padding: 10px 15px; text-align: center; opacity: 0.4;">
        <p style="font-size: 10px; color: #888; line-height: 1.4; margin: 0;">
            By playing, you agree to our <b>Privacy Policy</b> and data collection for global rankings and game analytics.<br>
            <span style="font-size: 9px;">ID: {st.session_state.get('session_id', '???')}</span>
        </p>
    </div>
    """, unsafe_allow_html=True)

@st.fragment
def render_gameplay_shard():
    """HIGH-SPEED FRAGMENT: Handles the keypad, challenge display, and score HUD."""
    unique_id = random.randint(100000, 999999)
    limit = get_current_time_limit()
    elapsed = time.time() - st.session_state.round_start_time
    remaining = max(0, limit - elapsed)
    fraction = min(1.0, elapsed / limit)
    current_feedback = st.session_state.get('feedback_state', 'neutral')
    
    # HUD & Target
    st.markdown(f"""
    <style>
        @keyframes flashG_{unique_id} {{ 0%, 100% {{ background: transparent; }} 50% {{ background: #2e7d32; }} }}
        @keyframes flashR_{unique_id} {{ 0%, 100% {{ background: transparent; }} 50% {{ background: #c62828; }} }}
        .flash-correct {{ animation: flashG_{unique_id} 0.8s; }}
        .flash-incorrect {{ animation: flashR_{unique_id} 0.8s; }}
        @keyframes shrink_{unique_id} {{ 0% {{ transform: scaleX({1.0 - fraction}); }} 100% {{ transform: scaleX(0); }} }}
    </style>
    <div style="width: 100%; text-align: center; --target-font: min(9vw, 9vh, 70px);">
        <div style="display: flex; justify-content: space-between; margin-bottom: 5px; gap: 8px; padding: 0 10px;">
            <div style="flex: 1; background: {HUD_BG}; border: 1.5px solid {HUD_BORDER}; border-radius: 8px; padding: 8px;">
                <div style="color: {HUD_LABEL_COLOR}; font-size: {HUD_LABEL_SIZE}; font-weight: 800; line-height: 1;">Score</div>
                <div style="color: {HUD_VALUE_COLOR}; font-size: {HUD_VALUE_SIZE}; font-weight: 800;">{st.session_state.total_score:,}</div>
            </div>
            <div style="flex: 1; background: {HUD_BG}; border: 1.5px solid {HUD_BORDER}; border-radius: 8px; padding: 8px;">
                <div style="color: {HUD_LABEL_COLOR}; font-size: {HUD_LABEL_SIZE}; font-weight: 800; line-height: 1;">Your Best</div>
                <div id="pb-val-{unique_id}" style="color: {HUD_VALUE_COLOR}; font-size: {HUD_VALUE_SIZE}; font-weight: 800;">--</div>
            </div>
            <div style="flex: 1; background: {HUD_BG}; border: 1.5px solid {HUD_BORDER}; border-radius: 8px; padding: 8px;">
                <div style="color: {HUD_LABEL_COLOR}; font-size: {HUD_LABEL_SIZE}; font-weight: 800; line-height: 1;">World Rec</div>
                <div style="color: {ROOTY_COLOR}; font-size: {HUD_VALUE_SIZE}; font-weight: 800;">{get_world_record():,}</div>
            </div>
        </div>
        <h2 style="color: {ROOTY_COLOR}; font-weight: 800; font-size: calc(var(--target-font) * 0.8); margin: 0;">Rooty!</h2>
        <div style="width: 60%; height: 3px; background: #222; border-radius: 2px; margin: 8px auto; overflow: hidden;">
            <div style="height: 100%; background: #4caf50; transform-origin: left; transform: scaleX({1.0 - fraction}); animation: shrink_{unique_id} {remaining}s linear forwards;"></div>
        </div>
        <div class="challenge-number {'flash-correct' if current_feedback=='correct' else 'flash-incorrect' if current_feedback=='incorrect' else ''}" 
             style="margin-top: -10px; margin-bottom: 20px;">
            {st.session_state.target_number}
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Keypad
    cols = st.columns(3)
    with cols[0]:
        st.button("1", key="n1", use_container_width=True, on_click=handle_guess, args=(1,))
        st.button("4", key="n4", use_container_width=True, on_click=handle_guess, args=(4,))
        st.button("7", key="n7", use_container_width=True, on_click=handle_guess, args=(7,))
    with cols[1]:
        st.button("2", key="n2", use_container_width=True, on_click=handle_guess, args=(2,))
        st.button("5", key="n5", use_container_width=True, on_click=handle_guess, args=(5,))
        st.button("8", key="n8", use_container_width=True, on_click=handle_guess, args=(8,))
    with cols[2]:
        st.button("3", key="n3", use_container_width=True, on_click=handle_guess, args=(3,))
        st.button("6", key="n6", use_container_width=True, on_click=handle_guess, args=(6,))
        st.button("9", key="n9", use_container_width=True, on_click=handle_guess, args=(9,))

    # JS BRIDGE: Timer, PB & Keys (Dynamic key forces re-mount on new round)
    js_code = """<script>
    const p = window.parent.document;
    const remaining = REMAINING_TIME;
    
    // 1. Clear stale timeouts, then set a new one using ACTUAL remaining time
    if(window.parent._drTmr) window.parent.clearTimeout(window.parent._drTmr);
    window.parent._drTmr = window.parent.setTimeout(() => {
        const b = Array.from(p.querySelectorAll('button')).find(btn => btn.textContent.trim()==='timeout_trigger');
        if(b) b.click();
    }, Math.max(0, remaining * 1000));

    // 2. PB Update
    try {
        let pb = parseInt(window.localStorage.getItem('digitalRootPB') || '0', 10);
        const score = CURR_SCORE;
        if (score > pb) { pb = score; window.localStorage.setItem('digitalRootPB', pb); }
        const pbEl = p.getElementById('pb-val-UNIQUE_ID'); if(pbEl) pbEl.innerText = pb.toLocaleString();
    } catch(e) { console.warn("Rooty: PB storage blocked"); }
    
    // 3. Keyboard Support
    if(window.parent._kbClean) window.parent._kbClean();
    const kbHandler = (e) => {
        if (e.key >= '1' && e.key <= '9') {
            const btns = Array.from(p.querySelectorAll('button'));
            const target = btns.find(b => b.textContent.trim() === e.key);
            if (target) target.click();
        }
    };
    p.addEventListener('keydown', kbHandler);
    window.parent._kbClean = () => p.removeEventListener('keydown', kbHandler);
    </script>"""
    js_code = js_code.replace("REMAINING_TIME", str(round(remaining, 3)))
    js_code = js_code.replace("CURR_SCORE", str(st.session_state.total_score))
    js_code = js_code.replace("UNIQUE_ID", str(unique_id))
    
    # DYNAMIC RENDER: The unique_id in the script ensures it fresh environment
    components.html(js_code, height=0, width=0)

    # Cleanup state after render
    if st.session_state.get('feedback_state') != 'neutral':
        st.session_state.feedback_state = 'neutral'

def render_gameplay():
    """MAIN APP LAYER: Fixed-position elements (Sentry)."""
    sync_hw_bridge()
    # 1. High-speed shard
    render_gameplay_shard()

    # 2. Timeout Sentry Trigger
    st.button("timeout_trigger", key="ht", on_click=handle_timeout_js)

    # 3. Button Hider
    components.html("""<script>
    const p = window.parent.document;
    const loop = setInterval(() => {
        const b = Array.from(p.querySelectorAll('button')).find(btn => btn.textContent.trim()==='timeout_trigger');
        if(b) {
            b.closest('[data-testid="stButton"]').style.cssText = 'opacity:0;position:absolute;pointer-events:none;height:0;';
            clearInterval(loop);
        }
    }, 50);
    </script>""", height=0, width=0)

def render_game_over():
    st.markdown(f"""
    <div style="text-align: center; margin-top: 2vh; width: 100%;">
        <h1 style="color: {GAME_OVER_COLOR}; font-size: 15vw; font-weight: 700; margin: 0;">Game Over</h1>
        <h2 style="color: {GAME_OVER_COLOR}; font-size: 11vw; font-weight: 900; margin: 0;">{st.session_state.total_score:,} PTS</h2>
        <p style="color: #888; font-size: 4vw; margin-top: 5px;">Rounds Cleared: {st.session_state.round_count}</p>
    </div>
    <div class="menu-btn-container">
    """, unsafe_allow_html=True)
    st.button("Try Again!!", use_container_width=True, on_click=start_game, type="primary")
    st.button("Main Menu", use_container_width=True, on_click=go_home)
    st.markdown("</div>", unsafe_allow_html=True)
    
    components.html("""<script>
    const p = window.parent.document;
    if(window.parent._kbClean) window.parent._kbClean(); // Purge gameplay listener
    
    const goHandler = (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
            const btn = Array.from(p.querySelectorAll('button')).find(b => b.textContent.trim().includes('Try Again'));
            if (btn) btn.click();
        }
    };
    p.addEventListener('keydown', goHandler);
    window.parent._kbGoClean = () => p.removeEventListener('keydown', goHandler);
    </script>""", height=0, width=0)

def render_tutorial():
    sync_hw_bridge()
    st.markdown(textwrap.dedent(f"""
    <div style="text-align: center; margin-top: 5vh; width: 100%;">
        <h2 style="color: {ROOTY_COLOR}; font-size: 8vw; margin-bottom: 1.5vh; font-weight: 800;">How to Play</h2>
    </div>
    
    <div style="width: 90vw; margin: 0 auto; margin-bottom: 3vh; color: #eee; font-size: 15px; line-height: 1.2;">
        <div style="display: flex; align-items: flex-start; margin-bottom: 6px;">
            <div style="margin-right: 10px;">🎯</div>
            <div><b>Sum it Up</b>: Squash digits until only 1 remains!</div>
        </div>
        <div style="display: flex; align-items: flex-start; margin-bottom: 6px;">
            <div style="margin-right: 10px;">⚡</div>
            <div><b>Quick Blitz</b>: 5s per round. Think fast!</div>
        </div>
        <div style="display: flex; align-items: flex-start; margin-bottom: 6px;">
            <div style="margin-right: 10px;">🚫</div>
            <div><b>Time Zap</b>: Wrong answers deduct 0.5s.</div>
        </div>
        <div style="display: flex; align-items: flex-start; margin-bottom: 6px;">
            <div style="margin-right: 10px;">💎</div>
            <div><b>Speed Bonus</b>: Fast answers = Bigger scores!</div>
        </div>
        <div style="display: flex; align-items: flex-start; margin-bottom: 4px;">
            <div style="margin-right: 10px;">⌨️</div>
            <div><b>Keyboard Pro</b>: Use your numpad for speed!</div>
        </div>
    </div>

    <div style="width: 85vw; margin: 0; margin-bottom: 3vh; color: #FFF; font-family: monospace;">
        <div style="background: #222; padding: 10px 15px; border-radius: 10px; margin-bottom: 0.8vh; border-left: 4px solid {ROOTY_COLOR};">
            <div style="font-size: 16px;">35 &rarr; 3+5 = <b style="color: {ROOTY_COLOR};">8</b></div>
        </div>
        <div style="background: #222; padding: 10px 15px; border-radius: 10px; margin-bottom: 0.8vh; border-left: 4px solid {ROOTY_COLOR};">
            <div style="font-size: 16px;">68 &rarr; 14 &rarr; 1+4 = <b style="color: {ROOTY_COLOR};">5</b></div>
        </div>
        <div style="background: #222; padding: 10px 15px; border-radius: 10px; margin-bottom: 1vh; border-left: 4px solid {ROOTY_COLOR};">
            <div style="font-size: 16px;">736 &rarr; 16 &rarr; 1+6 = <b style="color: {ROOTY_COLOR};">7</b></div>
        </div>
    </div>
    <div class="menu-btn-container" style="position: relative;">
    """).strip(), unsafe_allow_html=True)
    st.button("MAIN MENU", on_click=go_home, use_container_width=True)
    st.button("PLAY GAME", on_click=start_game, use_container_width=True)
    st.button("LEADERBOARD", on_click=go_leaderboard, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

@st.fragment
def render_practice_shard():
    """HIGH-SPEED FRAGMENT: Practice mode layout."""
    unique_id = random.randint(100000, 999999)
    current_feedback = st.session_state.get('feedback_state', 'neutral')
    
    # Digit Selector HUD
    st.markdown(f"""
    <style>
        @keyframes flashG_{unique_id} {{ 0%, 100% {{ background: transparent; }} 50% {{ background: #2e7d32; }} }}
        @keyframes flashR_{unique_id} {{ 0%, 100% {{ background: transparent; }} 50% {{ background: #c62828; }} }}
        .flash-correct {{ animation: flashG_{unique_id} 0.8s; }}
        .flash-incorrect {{ animation: flashR_{unique_id} 0.8s; }}

        /* SURGICAL LOCAL OVERRIDE: Forces Practice buttons into one row without st.columns */
        #practice-pill-box {{
            width: 100%; text-align: center; margin: 10px 0; display: block;
        }}
        #practice-pill-box div[data-testid="stButton"] {{
            display: inline-block !important; width: 14vw !important; margin: 0 4px !important;
        }}
        #practice-pill-box button {{
            height: 6dvh !important; border-radius: 12px !important; background: #222 !important; border: 1.5px solid #444 !important;
        }}
        #practice-pill-box button p {{
            font-size: 20px !important; font-weight: 800 !important; color: white !important;
        }}
        /* Active highlight */
        #practice-pill-box .active-pill button {{
            background: {ROOTY_COLOR} !important; border-color: {ROOTY_COLOR} !important;
        }}
        #practice-pill-box .active-pill button p {{
            color: #1a1a1a !important;
        }}
    </style>
    <div style="width: 100%; text-align: center; margin-bottom: 5px;">
        <div style="color: {HUD_LABEL_COLOR}; font-size: 28px; font-weight: 800; text-transform: uppercase;">Practice Mode</div>
    </div>
    """, unsafe_allow_html=True)

    # NATIVE PILLS: Rendered individually and floated into a row via local CSS
    st.markdown('<div id="practice-pill-box">', unsafe_allow_html=True)
    for d in range(2, 8):
        is_active = st.session_state.practice_digits == d
        st.markdown(f'<div class="{"active-pill" if is_active else ""}">', unsafe_allow_html=True)
        # Use a very specific key "LEN_x" to avoid confusion with the answer keys
        st.button(str(d), key=f"LEN_{d}_{unique_id}", on_click=set_practice_digits, args=(d,))
        st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(f"""
    <div style="width: 100%; text-align: center; margin-top: 15px; --target-font: min(9vw, 9vh, 70px);">
        <div class="challenge-number {'flash-correct' if current_feedback=='correct' else 'flash-incorrect' if current_feedback=='incorrect' else ''}" 
             style="margin-bottom: 20px;">
            {st.session_state.target_number}
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Keypad (Uses original handle_guess for answers)
    kcols = st.columns(3)
    with kcols[0]:
        st.button("1", key=f"ANS_1_{unique_id}", use_container_width=True, on_click=handle_guess, args=(1,))
        st.button("4", key=f"ANS_4_{unique_id}", use_container_width=True, on_click=handle_guess, args=(4,))
        st.button("7", key=f"ANS_7_{unique_id}", use_container_width=True, on_click=handle_guess, args=(7,))
    with kcols[1]:
        st.button("2", key=f"ANS_2_{unique_id}", use_container_width=True, on_click=handle_guess, args=(2,))
        st.button("5", key=f"ANS_5_{unique_id}", use_container_width=True, on_click=handle_guess, args=(5,))
        st.button("8", key=f"ANS_8_{unique_id}", use_container_width=True, on_click=handle_guess, args=(8,))
    with kcols[2]:
        st.button("3", key=f"ANS_3_{unique_id}", use_container_width=True, on_click=handle_guess, args=(3,))
        st.button("6", key=f"ANS_6_{unique_id}", use_container_width=True, on_click=handle_guess, args=(6,))
        st.button("9", key=f"ANS_9_{unique_id}", use_container_width=True, on_click=handle_guess, args=(9,))

    # JS BRIDGE: Keyboard Support (Restricted ONLY to Answer Dialpad Rows)
    js_code = f"""<script>
    const p = window.parent.document;
    if(window.parent._kbClean) window.parent._kbClean();
    const kbHandler = (e) => {{
        if (e.key >= '1' && e.key <= '9') {{
            const btns = Array.from(p.querySelectorAll('button'));
            // ONLY target buttons that are inside a column grid (data-testid="stHorizontalBlock")
            // This excludes the "Floated" top bar buttons.
            const target = btns.find(b => {{
                return b.innerText && b.innerText.trim() === e.key && 
                       b.closest('[data-testid="stHorizontalBlock"]');
            }});
            if (target) target.click();
        }}
    }};
    p.addEventListener('keydown', kbHandler);
    window.parent._kbClean = () => p.removeEventListener('keydown', kbHandler);
    </script>"""
    components.html(js_code, height=0, width=0)

    # Cleanup state after render
    if st.session_state.get('feedback_state') != 'neutral':
        st.session_state.feedback_state = 'neutral'

def render_practice():
    """Practice mode container."""
    sync_hw_bridge()
    render_practice_shard()
    st.markdown('<div class="menu-btn-container" style="margin-top: 30px;">', unsafe_allow_html=True)
    st.button("MAIN MENU", on_click=go_home, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

def render_leaderboard():
    sync_hw_bridge()
    nick = st.session_state.get('nickname', '')
    session_score = st.session_state.get('total_score', 0)
    top_entries, user_stats, total_players = fetch_leaderboard_data(nick, session_score)
    
    # Optimistic merge: background thread may not have written the score yet,
    # so inject the user's session score at the correct sorted position
    if nick and session_score > 0 and top_entries:
        # Find existing entry score (if any)
        existing_score = 0
        for e in top_entries:
            if e.get('name') == nick:
                existing_score = e.get('score', 0)
                break
        
        # Only merge if session score is higher (avoid replacing a real high score)
        if session_score >= existing_score:
            # Remove stale entry
            top_entries = [e for e in top_entries if e.get('name') != nick]
            # Insert at correct sorted position
            opt_entry = {'name': nick, 'score': session_score, 'level': math.ceil(session_score / 100)}
            inserted = False
            for i, entry in enumerate(top_entries):
                if session_score >= entry.get('score', 0):
                    top_entries.insert(i, opt_entry)
                    inserted = True
                    break
            if not inserted:
                top_entries.append(opt_entry)
            top_entries = top_entries[:100]
            
            # Recalculate user_stats from merged list
            user_stats = None
            for i, e in enumerate(top_entries):
                if e.get('name') == nick:
                    user_stats = dict(e)
                    user_stats['rank'] = i + 1
                    break
    # Build the full leaderboard as a single block to avoid Streamlit ghost gaps
    leaderboard_html = textwrap.dedent(f"""
    <div style="text-align: center; width: 100%; padding-bottom: 5px; border-bottom: 1px solid #333; margin-bottom: 10px;">
        <p style="color: {ROOTY_COLOR}; font-size: 40px; font-weight: 800; margin-bottom: 0;">Leaderboard</p>
        <p style="color: #FFF; font-size: 15px;">Weekly Reset &bull; {total_players:,} players</p>
    </div>
    """).strip()

    if not top_entries:
        leaderboard_html += '<div style="width: 95vw; height: 52dvh; margin: 0 auto; background: #1a1a1a; border-radius: 8px; border: 1px solid #333;">'
        leaderboard_html += '<p style="text-align:center; padding: 20px; color:#444;">No entries yet!</p>'
        leaderboard_html += '</div>'
    else:
        # [1] MASTER TABLE CONTAINER (Single scroller, sticky header)
        leaderboard_html += textwrap.dedent(f"""
        <div id="rooty-rank-scroller" style="
            width: 90vw; 
            height: 50dvh; 
            overflow-y: auto; 
            -webkit-overflow-scrolling: touch; 
            margin: 0 auto; 
            background: #1a1a1a; 
            border-radius: 8px; 
            border: 1px solid #333;
        ">
        <table style="width: 100%; border-collapse: collapse; font-family: monospace; font-size: 12px; table-layout: fixed;">
            <thead>
                <tr style="color:#FFFFFF; text-transform:uppercase; background: #222; position: sticky; top: 0; z-index: 10; border-bottom: 2px solid #333;">
                    <th style="padding:10px 6px; text-align:center; width:10%;">#</th>
                    <th style="padding:10px 6px 10px 15px; text-align:left; width:40%;">Nickname</th>
                    <th style="padding:10px 6px; text-align:center; width:35%;">Score</th>
                    <th style="padding:10px 6px; text-align:center; width:15%;">Level</th>
                </tr>
            </thead>
            <tbody>
        """).strip()
        
        user_in_top = False
        for i, entry in enumerate(top_entries):
            is_me = nick and entry.get('name') == nick
            if is_me: user_in_top = True
            
            bg = "rgba(255, 213, 79, 0.15)" if is_me else ("#1a1a1a" if i % 2 == 0 else "transparent")
            crown = "👑" if i == 0 else f"{i+1}"
            color = ROOTY_COLOR if (i == 0 or is_me) else "#FFF"
            name_label = f"{entry.get('name', 'Anon')[:15]}{' (You)' if is_me else ''}"
            
            pts = f"{entry.get('score', 0):,}"
            lvl = f"{entry.get('level', 0)}"
            
            row_id = ' id="rooty-user-row"' if is_me else ''
            leaderboard_html += textwrap.dedent(f"""
                <tr{row_id} style="background:{bg}; border-bottom:1px solid #222;">
                    <td style="padding:10px 6px; color:{ROOTY_COLOR}; font-weight:bold; width:10%; text-align:center;">{crown}</td>
                    <td style="padding:10px 6px 10px 15px; color:{color}; text-align:left; width:40%; white-space:nowrap; overflow:hidden;">{name_label}</td>
                    <td style="padding:10px 6px; color:{ROOTY_COLOR}; text-align:center; width:35%; font-weight:bold;">{pts}</td>
                    <td style="padding:10px 6px; color:#FFF; text-align:center; width:15%;">{lvl}</td>
                </tr>
            """).strip()
        
        # Tail row for user outside top entries
        if not user_in_top and user_stats:
            my_rank = user_stats.get("rank", "?")
            my_pts = f"{user_stats.get('score', 0):,}"
            my_lvl = f"{user_stats.get('level', 0)}"
            
            leaderboard_html += textwrap.dedent(f"""
                <tr><td colspan="4" style="padding:4px; text-align:center; color:#444; font-size:10px; border-top:1px dashed #444;">&#8226; &#8226; &#8226;</td></tr>
                <tr id="rooty-user-row" style="background:rgba(255, 213, 79, 0.15);">
                    <td style="padding:10px 6px; color:{ROOTY_COLOR}; font-weight:bold; width:10%; text-align:center;">{my_rank}</td>
                    <td style="padding:10px 6px 10px 15px; color:{ROOTY_COLOR}; text-align:left; width:40%;">{nick} (You)</td>
                    <td style="padding:10px 6px; color:{ROOTY_COLOR}; text-align:center; width:35%; font-weight:bold;">{my_pts}</td>
                    <td style="padding:10px 6px; color:#FFF; text-align:center; width:15%;">{my_lvl}</td>
                </tr>
            """).strip()
            
        leaderboard_html += '</tbody></table></div>'
    
    # No more cleanup needed here as each block was dedented individually
    st.markdown(leaderboard_html, unsafe_allow_html=True)
    
    # Auto-scroll to user's row
    if nick and (user_stats or any(e.get('name') == nick for e in top_entries)):
        components.html("""<script>
        const p = window.parent.document;
        setTimeout(() => {
            const row = p.getElementById('rooty-user-row');
            const scroller = p.getElementById('rooty-rank-scroller');
            if (row && scroller) {
                const rowTop = row.offsetTop - scroller.offsetTop;
                const center = rowTop - (scroller.clientHeight / 2) + (row.clientHeight / 2);
                scroller.scrollTo({ top: Math.max(0, center), behavior: 'smooth' });
            }
        }, 150);
        </script>""", height=0, width=0)

    # Sticky Footer Buttons
    st.markdown('<div class="menu-btn-container" style="margin-top: 30px; padding-top: 10px; border-top: 1px solid #333; width: 100%;">', unsafe_allow_html=True)
    st.button("MAIN MENU", on_click=go_home, use_container_width=True)
    st.button("PLAY GAME", on_click=start_game, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# --- 7. MAIN MASTER NAVIGATOR ---
# ==========================================
st.set_page_config(page_title="Rooty!", layout="centered")

# Hidden Global Analytics Bridge (Data processed via sync_hw_bridge)
st.text_input("HW_BRIDGE", key="hw_bridge_input", on_change=sync_hw_bridge, label_visibility="collapsed")

init_state()
inject_global_styles()

if st.session_state.game_status == 'home':
    render_home()
elif st.session_state.game_status == 'playing':
    render_gameplay()
elif st.session_state.game_status == 'practice':
    render_practice()
elif st.session_state.game_status == 'game_over':
    render_game_over()
elif st.session_state.game_status == 'tutorial':
    render_tutorial()
elif st.session_state.game_status == 'leaderboard':
    render_leaderboard()

app = None
