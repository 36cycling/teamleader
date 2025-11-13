import streamlit as st
import pandas as pd
import json
import requests
import os
from difflib import get_close_matches

# ============ PAGINA-INSTELLINGEN ============
st.set_page_config(page_title="Teamleader Offerte Tool", page_icon="📄", layout="centered")

# ============ LOGIN =============
st.sidebar.title("🔒 Inloggen")

CORRECT_PASSWORD = st.secrets["auth"]["password"]
password = st.sidebar.text_input("Voer wachtwoord in", type="password")

if password != CORRECT_PASSWORD:
    st.error("❌ Ongeldig wachtwoord. Toegang geweigerd.")
    st.stop()

st.success("✅ Toegang verleend!")

# ============ TEAMLEADER CONFIG ============
TEAMLEADER_AUTH_URL = "https://focus.teamleader.eu/oauth2/access_token"
TEAMLEADER_API_BASE = "https://api.focus.teamleader.eu"

CLIENT_ID = st.secrets["CLIENT_ID"]
CLIENT_SECRET = st.secrets["CLIENT_SECRET"]
REDIRECT_URI = st.secrets["REDIRECT_URI"]
TOKENS_FILE = "teamleader_tokens.json"


# ============ TOKEN FUNCTIES ============
def save_tokens(access_token, refresh_token):
    with open(TOKENS_FILE, "w") as f:
        json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)

def load_tokens():
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE) as f:
            return json.load(f)
    return None

def get_access_token(auth_code=None):
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI
    }

    tokens = load_tokens()
    if tokens and "refresh_token" in tokens:
        data.update({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        r = requests.post(TEAMLEADER_AUTH_URL, data=data)
        if r.ok:
            new = r.json()
            save_tokens(new["access_token"], new["refresh_token"])
            return new["access_token"]

    if auth_code:
        data.update({"grant_type": "authorization_code", "code": auth_code})
        r = requests.post(TEAMLEADER_AUTH_URL, data=data)
        if r.ok:
            new = r.json()
            save_tokens(new["access_token"], new["refresh_token"])
            return new["access_token"]

    auth_url = f"https://focus.teamleader.eu/oauth2/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
    st.warning("⚠️ Geen geldige token gevonden. Vraag een nieuwe autorisatiecode aan:")
    st.write(auth_url)
    return None


# ============ HELPER FUNCTIES ============
def post_json(endpoint, access_token, payload):
    url = f"{TEAMLEADER_API_BASE}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    return requests.post(url, headers=headers, json=payload)

def get_companies(access_token):
    url = f"{TEAMLEADER_API_BASE}/companies.list"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    all_companies = []
    page_number = 1
    while True:
        payload = {"page": {"size": 100, "number": page_number}}
        r = requests.post(url, headers=headers, json=payload)
        if not r.ok:
            st.warning(f"⚠️ Fout bij ophalen bedrijven: {r.text}")
            break
        data = r.json().get("data", [])
        if not data:
            break
        all_companies.extend(data)
        if len(data) < 100:
            break
        page_number += 1
    st.write(f"📦 {len(all_companies)} bedrijven opgehaald.")
    return all_companies

def find_company_by_name(company_name, companies):
    names = [c["name"] for c in companies]
    matches = get_close_matches(company_name.strip(), names, n=1, cutoff=0.5)
    if matches:
        match = matches[0]
        return next(c for c in companies if c["name"] == match)
    return None

def choose_contact_for_company_ui(access_token, company_id):
    payload = {"filter": {"company_id": company_id}, "page": {"size": 50, "number": 1}}
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    r = requests.post(f"{TEAMLEADER_API_BASE}/contacts.list", headers=headers, json=payload)

    if not r.ok:
        st.warning(f"⚠️ Fout bij ophalen contacten: {r.text}")
        return None, None

    contacts = r.json().get("data", [])
    if not contacts:
        st.warning("⚠️ Geen contactpersonen gevonden bij dit bedrijf.")
        return None, None

    contact_options = {c.get("full_name"): c.get("id") for c in contacts if c.get("full_name")}
    chosen_name = st.selectbox("👤 Kies contactpersoon:", ["-- Selecteer --"] + list(contact_options.keys()), key=f"contact_{company_id}")

    if chosen_name != "-- Selecteer --":
        return contact_options[chosen_name], chosen_name
    return None, None


# ============ DEAL EN OFFERTE ============
def create_deal(access_token, company_id, lead_id, title, product_lines):
    lines_payload = [
        {
            "name": line.get("ProductName"),
            "quantity": int(line.get("Quantity") or 1),
            "unit_price": float(line.get("UnitPrice") or 0),
            "vat_rate": int(line.get("VAT rate item") or 21)
        }
        for line in product_lines
    ]

    payload = {
        "title": title,
        "lead": {"customer": {"type": "company", "id": company_id}, "contact_person_id": lead_id},
        "source": {"type": "api"},
        "lines": lines_payload
    }

    r = post_json("deals.create", access_token, payload)
    return r.json() if r.ok else None

def create_quotation(access_token, deal_id, deal_title, product_lines):
    r = requests.post(f"{TEAMLEADER_API_BASE}/taxRates.list",
                      headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                      json={})
    vat_rate_id = None
    if r.ok:
        for tr in r.json().get("data", []):
            if abs(tr.get("rate", 0) - 0.21) < 0.001:
                vat_rate_id = tr["id"]
                break

    grouped_lines = [{
        "section": {"title": "Maten hier in te vullen"},
        "line_items": [{
            "quantity": int(line.get("Quantity") or 1),
            "description": f"{line.get('ProductName', '')} {line.get('Sizes', '')}".strip(),
            "extended_description": line.get("Description", ""),
            "unit_price": {"amount": float(line.get("UnitPrice") or 0), "tax": "excluding"},
            "tax_rate_id": vat_rate_id
        } for line in product_lines]
    }]

    payload = {
        "deal_id": deal_id,
        "title": deal_title,
        "text": f"Offerte voor deal '{deal_title}'",
        "currency": {"code": "EUR", "exchange_rate": 1.0},
        "grouped_lines": grouped_lines
    }

    r = requests.post(f"{TEAMLEADER_API_BASE}/quotations.create",
                      headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                      json=payload)
    return r.json() if r.ok else None


# ============ STREAMLIT FLOW ============
st.title("📄 Teamleader Offerte Generator")

# stap 1 — upload Excel
uploaded_file = st.file_uploader("📤 Upload Excel-bestand met deals", type=["xlsx"])
if uploaded_file:
    st.session_state.df = pd.read_excel(uploaded_file)
    st.success("✅ Excel geladen!")
    st.dataframe(st.session_state.df.head())

# stap 2 — verbind met Teamleader
auth_code = st.text_input("🔐 Vul Teamleader Authorization Code in (alleen eerste keer):")
if st.button("Verbind met Teamleader"):
    token = get_access_token(auth_code)
    if token:
        st.session_state.access_token = token
        st.session_state.connected = True
        st.success("✅ Verbonden met Teamleader API!")

if not st.session_state.get("connected") or "df" not in st.session_state:
    st.stop()

access_token = st.session_state.access_token
df = st.session_state.df
companies = get_companies(access_token)

# stap 3 — selecteer deal
deal_titles = df["DealTitle"].unique().tolist()
chosen_deal = st.selectbox("📦 Kies een deal uit Excel:", deal_titles)
if not chosen_deal:
    st.stop()

deal_rows = df[df["DealTitle"] == chosen_deal]
company_name = deal_rows.iloc[0]["CompanyName"]

company = find_company_by_name(company_name, companies)
if not company:
    st.warning(f"⚠️ Bedrijf '{company_name}' niet gevonden.")
    st.stop()

company_id = company["id"]
st.info(f"🏢 Bedrijf gevonden: {company['name']}")

# stap 4 — kies contactpersoon
lead_id, lead_name = choose_contact_for_company_ui(access_token, company_id)
if lead_id:
    st.session_state.lead_id = lead_id
    st.session_state.lead_name = lead_name
    st.success(f"✅ Contactpersoon gekozen: {lead_name}")
else:
    st.stop()

# stap 5 — maak deal en offerte
if st.button("🚀 Maak deal + offerte aan"):
    product_lines = deal_rows.to_dict(orient="records")
    deal_response = create_deal(access_token, company_id, lead_id, chosen_deal, product_lines)

    if not deal_response:
        st.error(f"❌ Deal '{chosen_deal}' kon niet worden aangemaakt.")
        st.stop()

    deal_id = deal_response["data"]["id"]
    quotation_response = create_quotation(access_token, deal_id, chosen_deal, product_lines)

    if quotation_response:
        st.success(f"✅ Offerte aangemaakt voor deal '{chosen_deal}'!")
    else:
        st.warning(f"⚠️ Geen offerte aangemaakt voor '{chosen_deal}'.")
