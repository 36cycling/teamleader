"""Gedeelde Teamleader API-utilities voor alle pagina's."""

import json
import os
import requests
import streamlit as st
from typing import Optional

# =============================================
#   CONFIGURATIE
# =============================================
REDIRECT_URI        = "https://www.kwatta.com/teamleader_redirect.html"
TEAMLEADER_AUTH_URL = "https://focus.teamleader.eu/oauth2/access_token"
TEAMLEADER_API_BASE = "https://api.focus.teamleader.eu"
TOKENS_FILE         = "teamleader_tokens.json"

CLIENT_ID     = st.secrets["CLIENT_ID"]
CLIENT_SECRET = st.secrets["CLIENT_SECRET"]


# =============================================
#   TOKEN OPSLAG
# =============================================
def save_tokens(access_token: str, refresh_token: str):
    with open(TOKENS_FILE, "w") as f:
        json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)
    # Onthoud het laatste refresh-token in session_state zodat de UI het kan tonen
    # (de gebruiker kan dit naar Streamlit secrets kopiëren om opnieuw inloggen te vermijden).
    try:
        st.session_state["latest_refresh_token"] = refresh_token
    except Exception:
        pass


def load_tokens() -> Optional[dict]:
    """Laad tokens — eerst uit lokaal bestand, daarna uit Streamlit secrets als fallback.

    Streamlit Cloud wist het bestand bij elke herstart, dus `TEAMLEADER_REFRESH_TOKEN`
    in secrets fungeert als backup zodat de OAuth-dans niet steeds opnieuw nodig is.
    """
    # 1. Lokaal bestand (verdwijnt op Streamlit Cloud bij herstart)
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE) as f:
                return json.load(f)
        except Exception:
            pass

    # 2. Fallback: refresh-token uit Streamlit secrets
    try:
        secret_refresh = st.secrets.get("TEAMLEADER_REFRESH_TOKEN", "")
    except Exception:
        secret_refresh = ""
    if secret_refresh:
        return {"access_token": "", "refresh_token": secret_refresh}

    return None


# =============================================
#   OAUTH HELPERS
# =============================================
def exchange_or_refresh_token(auth_code: Optional[str] = None) -> Optional[str]:
    """Vernieuw het access token via refresh token, of wissel een auth code in."""
    tokens = load_tokens()
    data_base = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
    }
    session = requests.Session()

    # Probeer eerst te vernieuwen via refresh token
    if tokens and tokens.get("refresh_token"):
        data = {**data_base, "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}
        r = session.post(TEAMLEADER_AUTH_URL, data=data)
        if r.ok:
            new = r.json()
            save_tokens(new["access_token"], new["refresh_token"])
            return new["access_token"]

    # Wissel een eenmalige auth code in
    if auth_code:
        data = {**data_base, "grant_type": "authorization_code", "code": auth_code}
        r = session.post(TEAMLEADER_AUTH_URL, data=data)
        if r.ok:
            new = r.json()
            save_tokens(new["access_token"], new["refresh_token"])
            return new["access_token"]
        else:
            st.error("Ongeldige Authorization Code.")
            return None

    return None


def teamleader_oauth_url() -> str:
    return (
        "https://focus.teamleader.eu/oauth2/authorize"
        f"?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
    )


# =============================================
#   API HELPER
# =============================================
def post_json(endpoint: str, payload: dict) -> requests.Response:
    """POST naar Teamleader API. Herprobeert automatisch na een verlopen token (401)."""
    token = st.session_state.access_token
    url = f"{TEAMLEADER_API_BASE}/{endpoint}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    r = requests.post(url, json=payload, headers=headers)

    if r.status_code == 401:
        new_token = exchange_or_refresh_token(None)
        if new_token:
            st.session_state.access_token = new_token
            headers["Authorization"] = f"Bearer {new_token}"
            r = requests.post(url, json=payload, headers=headers)

    return r


# =============================================
#   HELPERS — INGELOGDE GEBRUIKER & DEAL-SOURCE
# =============================================
def get_current_user_id() -> Optional[str]:
    """Haal het ID van de huidige ingelogde Teamleader-gebruiker op (gecached per sessie)."""
    if st.session_state.get("current_user_id"):
        return st.session_state.current_user_id
    r = post_json("users.me", {})
    if r.ok:
        uid = r.json().get("data", {}).get("id")
        if uid:
            st.session_state.current_user_id = uid
            return uid
    return None


def get_deal_source_id(name: str) -> Optional[str]:
    """Haal het ID van een deal-source (Herkomst) op naam (case-insensitive, gecached)."""
    cache_key = f"deal_source_id::{name.lower()}"
    if st.session_state.get(cache_key):
        return st.session_state[cache_key]
    r = post_json("dealSources.list", {"page": {"size": 100, "number": 1}})
    if r.ok:
        for s in r.json().get("data", []):
            if s.get("name", "").strip().lower() == name.lower():
                st.session_state[cache_key] = s["id"]
                return s["id"]
    return None


def get_deal_custom_field_id(label: str) -> Optional[str]:
    """Haal het ID van een custom field op deal-niveau op via zijn label (case-insensitive, gecached)."""
    cache_key = f"deal_cf_id::{label.lower()}"
    if st.session_state.get(cache_key):
        return st.session_state[cache_key]
    r = post_json("customFieldDefinitions.list", {
        "filter": {"context": "deal"},
        "page": {"size": 100, "number": 1},
    })
    if r.ok:
        for cf in r.json().get("data", []):
            cf_label = (cf.get("label") or cf.get("name") or "").strip().lower()
            if cf_label == label.lower():
                st.session_state[cache_key] = cf["id"]
                return cf["id"]
    return None
