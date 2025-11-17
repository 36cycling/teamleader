import streamlit as st
import pandas as pd
import json
import requests
import os
from difflib import get_close_matches

# ============ PAGINA-INSTELLINGEN ============
st.set_page_config(page_title="Teamleader Offerte Tool", page_icon="📄", layout="centered")

# ============ LOGIN ============
st.sidebar.title("🔒 Inloggen")
CORRECT_PASSWORD = st.secrets["auth"]["password"]
password = st.sidebar.text_input("Voer wachtwoord in", type="password")
if password != CORRECT_PASSWORD:
    st.error("❌ Ongeldig wachtwoord. Toegang geweigerd.")
    st.stop()
st.success("✅ Toegang verleend!")

# ============ CONFIG ============
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
    """
    Return access_token or None. If auth_code provided, exchange it.
    If refresh token present, try to refresh.
    """
    data = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "redirect_uri": REDIRECT_URI}
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

    return None

# ============ HELPER FUNCTIES (API) ============
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
    return all_companies

def find_company_by_name(company_name, companies):
    names = [c.get("name","") for c in companies]
    matches = get_close_matches(company_name.strip(), names, n=1, cutoff=0.5)
    if matches:
        match = matches[0]
        return next((c for c in companies if c.get("name") == match), None)
    return None

def get_contacts_for_company(access_token, company_id, page_size=50):
    payload = {"filter": {"company_id": company_id}, "page": {"size": page_size, "number": 1}}
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    r = requests.post(f"{TEAMLEADER_API_BASE}/contacts.list", headers=headers, json=payload)
    if not r.ok:
        return None, r.text
    return r.json().get("data", []), None

def find_lead(access_token, lead_name):
    r = requests.get(f"{TEAMLEADER_API_BASE}/contacts.list", headers={"Authorization": f"Bearer {access_token}"}, params={"filter[name]": lead_name})
    if not r.ok:
        return None, None
    contacts = r.json().get("data") or []
    for c in contacts:
        fullname = c.get("full_name") or f"{c.get('first_name','')} {c.get('last_name','')}"
        if fullname.strip().lower() == str(lead_name).strip().lower():
            return c.get("id"), fullname
    return None, None

def get_users(access_token):
    """
    Haal gebruikers op uit Teamleader. Retourneert lijst van user dicts (of empty list).
    """
    r = requests.post(f"{TEAMLEADER_API_BASE}/users.list", headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"}, json={})
    if not r.ok:
        st.warning(f"⚠️ Fout bij ophalen users: {r.text}")
        return []
    return r.json().get("data", [])

def create_deal(access_token, company_id, lead_id, title, product_lines, responsible_user_id=None):
    lines_payload = [
        {
            "name": line.get("ProductName") or line.get("name"),
            "quantity": int(line.get("Quantity") or 1),
            "unit_price": float(line.get("UnitPrice") or 0),
            "vat_rate": int(line.get("VAT rate item") or 21)
        } for line in product_lines
    ]
    payload = {
        "title": title,
        "lead": {"customer": {"type": "company", "id": company_id}, "contact_person_id": lead_id},
        "source": {"type": "api"},
        "lines": lines_payload
    }
    # voeg responsible_user_id toe indien aanwezig
    if responsible_user_id:
        payload["responsible_user_id"] = responsible_user_id

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
            "description": f"{line.get('ProductName','')} {line.get('Sizes','')}".strip(),
            "extended_description": line.get("Description",""),
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

# ============ UI FLOW ============

st.title("Teamleader Offerte Generator")
st.write("Welkom — upload een Excel en maak deals + offertes aan via Teamleader.")

# --- Connect to Teamleader (once) ---
if "access_token" not in st.session_state:
    st.session_state.access_token = None
    st.session_state.connected = False

col1, col2 = st.columns([2,3])
with col1:
    auth_code = st.text_input("🔐 Voer eenmalig Teamleader Authorization Code in (alleen eerste keer):", key="authcode_input")
    if st.button("🔗 Verbinden met Teamleader"):
        token = get_access_token(auth_code if auth_code else None)
        if token:
            st.session_state.access_token = token
            st.session_state.connected = True
            st.success("✅ Verbonden met Teamleader API!")
        else:
            st.error("❌ Kon niet verbinden — controleer auth code of refresh token.")

with col2:
    if st.session_state.connected and st.session_state.access_token:
        st.info("🔌 Verbonden — ready to go")

# --- Upload Excel ---
uploaded_file = st.file_uploader("📤 Upload Excel-bestand met deals", type=["xlsx"])
if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
    except Exception as e:
        st.error(f"Fout bij inlezen van Excel: {e}")
        st.stop()
    st.session_state.df = df
    st.success("✅ Excel geladen")
    st.dataframe(df.head())

# Require connection + uploaded df to proceed
if not st.session_state.get("connected") or not st.session_state.get("access_token"):
    st.info("Maak eerst verbinding met Teamleader (boven).")
    st.stop()
if "df" not in st.session_state:
    st.info("Upload eerst een Excel-bestand.")
    st.stop()

access_token = st.session_state.access_token
df = st.session_state.df

# --- Choose which deal to process ---
deal_titles = df["DealTitle"].unique().tolist()
deal_choice = st.selectbox("📦 Kies een deal uit Excel om te verwerken (of kies 'Alle deals'):", ["-- Selecteer --"] + deal_titles + ["Alle deals"])
if deal_choice == "-- Selecteer --":
    st.info("Kies een deal om verder te gaan.")
    st.stop()

# determine list of deals to process
if deal_choice == "Alle deals":
    deals_to_process = deal_titles
else:
    deals_to_process = [deal_choice]

# Fetch companies + users once
companies = get_companies(access_token)
users = get_users(access_token)  # lijst met user dicts
user_options = []
user_map = {}
for u in users:
    # compose readable name
    uname = u.get("full_name") or f"{u.get('first_name','')} {u.get('last_name','')}".strip() or u.get("email") or u.get("id")
    user_options.append(uname)
    user_map[uname] = u.get("id")

# Show summary and allow choosing contacts per company + responsible user
st.markdown("### Stap: kies per bedrijf de contactpersoon (en verantwoordelijke gebruiker) die je wilt koppelen")
# Prepare mapping of company -> rows
companies_cache = {}  # company_name -> company object
for deal_title in deals_to_process:
    rows = df[df["DealTitle"] == deal_title]
    company_name = rows.iloc[0]["CompanyName"]
    comp = find_company_by_name(company_name, companies)
    if not comp:
        st.warning(f"Bedrijf '{company_name}' niet gevonden in Teamleader - overslaan.")
        continue
    company_id = comp["id"]
    st.write(f"**Deal:** {deal_title} — **Bedrijf:** {comp['name']}")

    contacts, err = get_contacts_for_company(access_token, company_id)
    if err:
        st.warning(f"Kon contacten niet ophalen voor {comp['name']}: {err}")
        continue
    if not contacts:
        st.warning(f"Geen contacten gevonden voor {comp['name']}")
        continue

    # build contact options
    contact_options = []
    contact_map = {}
    for c in contacts:
        full = c.get("full_name") or f"{c.get('first_name','')} {c.get('last_name','')}".strip()
        contact_options.append(full)
        contact_map[full] = c.get("id")

    c_key = f"contact_select__{company_id}"
    if c_key not in st.session_state:
        st.session_state[c_key] = "-- Selecteer --"

    selected_contact = st.selectbox(f"Kies contactpersoon voor {comp['name']}", ["-- Selecteer --"] + contact_options, key=c_key)
    if selected_contact != "-- Selecteer --":
        st.session_state[f"chosen_contact__{company_id}"] = contact_map[selected_contact]
        st.session_state[f"chosen_contact_name__{company_id}"] = selected_contact
        st.success(f"Gekozen contact: {selected_contact}")
    else:
        st.info("Nog geen contact gekozen voor dit bedrijf.")

    # Responsible user selectbox (per company)
    u_key = f"user_select__{company_id}"
    if u_key not in st.session_state:
        st.session_state[u_key] = "-- Selecteer user --"

    # show user dropdown; include an option 'Laat Teamleader kiezen' (None)
    user_choice = st.selectbox(f"Kies responsible user voor {comp['name']} (optioneel)", ["-- Laat Teamleader kiezen --"] + user_options, key=u_key)
    if user_choice != "-- Laat Teamleader kiezen --":
        st.session_state[f"chosen_user__{company_id}"] = user_map.get(user_choice)
        st.session_state[f"chosen_user_name__{company_id}"] = user_choice
        st.success(f"Gekozen responsible user: {user_choice}")
    else:
        # ensure we clear any previous choice
        st.session_state.pop(f"chosen_user__{company_id}", None)
        st.info("Teamleader kiest automatisch verantwoordelijk persoon (geen keuze).")

# --- Final action button ---
st.markdown("---")
if st.button("🚀 Maak deals + offertes aan voor geselecteerde deal(s)"):
    # perform processing for each selected deal
    for deal_title in deals_to_process:
        rows = df[df["DealTitle"] == deal_title]
        company_name = rows.iloc[0]["CompanyName"]
        comp = find_company_by_name(company_name, companies)
        if not comp:
            st.warning(f"Bedrijf '{company_name}' niet gevonden - overslaan.")
            continue
        company_id = comp["id"]
        chosen_contact_key = f"chosen_contact__{company_id}"
        if chosen_contact_key not in st.session_state:
            st.warning(f"Geen contact gekozen voor bedrijf {comp['name']} — kies een contact en probeer opnieuw.")
            continue
        lead_id = st.session_state[chosen_contact_key]
        chosen_user_key = f"chosen_user__{company_id}"
        responsible_user_id = st.session_state.get(chosen_user_key)  # may be None

        st.info(f"Aanmaken deal '{deal_title}' voor {comp['name']} met contact {st.session_state.get(f'chosen_contact_name__{company_id}','(onbekend)')}"
                + (f" en responsible user {st.session_state.get(f'chosen_user_name__{company_id}')}" if responsible_user_id else ""))

        product_lines = rows.to_dict(orient="records")
        deal_resp = create_deal(access_token, company_id, lead_id, deal_title, product_lines, responsible_user_id=responsible_user_id)
        if not deal_resp:
            st.error(f"❌ Deal '{deal_title}' kon niet worden aangemaakt voor {comp['name']}.")
            continue
        deal_id = deal_resp.get("data", {}).get("id")
        st.success(f"✅ Deal '{deal_title}' aangemaakt (ID={deal_id})")

        # create quotation
        quotation_resp = create_quotation(access_token, deal_id, deal_title, product_lines)
        if quotation_resp:
            qid = quotation_resp.get("data", {}).get("id")
            st.success(f"💡 Offerte aangemaakt voor deal '{deal_title}' (ID={qid})")
        else:
            st.warning(f"⚠️ Offerte kon niet worden aangemaakt voor deal '{deal_title}'")

    st.balloons()
