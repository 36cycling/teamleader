# streamlit_teamleader_optimized.py
import streamlit as st
import pandas as pd
import json
import requests
import os
from difflib import get_close_matches
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, List, Optional, Dict

# ============ PAGINA-INSTELLINGEN ============
st.set_page_config(page_title="Teamleader Offerte Tool (Geoptimaliseerd)", page_icon="📄", layout="centered")

# ============ CONFIG / SECRETS ============
# Zorg dat st.secrets de volgende keys bevat:
# st.secrets["auth"]["password"], st.secrets["CLIENT_ID"], st.secrets["CLIENT_SECRET"], st.secrets["REDIRECT_URI"]
CORRECT_PASSWORD = st.secrets["auth"]["password"]
CLIENT_ID = st.secrets["CLIENT_ID"]
CLIENT_SECRET = st.secrets["CLIENT_SECRET"]
REDIRECT_URI = st.secrets["REDIRECT_URI"]

TEAMLEADER_AUTH_URL = "https://focus.teamleader.eu/oauth2/access_token"
TEAMLEADER_API_BASE = "https://api.focus.teamleader.eu"
TOKENS_FILE = "teamleader_tokens.json"

# ============ SESSION: requests.Session hergebruiken ============
def get_session():
    if "requests_session" not in st.session_state:
        st.session_state.requests_session = requests.Session()
    return st.session_state.requests_session

# ============ TOKEN HANDLING ============
def save_tokens(access_token: str, refresh_token: str):
    try:
        with open(TOKENS_FILE, "w") as f:
            json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)
    except Exception as e:
        st.warning(f"Kon tokens niet opslaan naar bestand: {e}")

def load_tokens() -> Optional[Dict]:
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE) as f:
                return json.load(f)
        except Exception:
            return None
    return None

def exchange_or_refresh_token(auth_code: Optional[str] = None) -> Optional[str]:
    """
