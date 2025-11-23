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
st.set_page_config(page_title="Teamleader Offerte Tool", page_icon="📄", layout="centered")

# ============ CONFIG / SECRETS ============
# Zorg dat st.secrets de volgende keys bevat:
# st.secrets["auth"]["password"], st.secrets["CLIENT_ID"], st.secrets["CLIENT_SECRET"], st.secrets["REDIRECT_URI"] (optioneel)
CORRECT_PASSWORD = st.secrets["auth"]["password"]
CLIENT_ID = st.secrets["CLIENT_ID"]
CLIENT_SECRET = st.secrets["CLIENT_SECRET"]

# Gebruik jouw redirect-url:
REDIRECT_URI = "https://www.kwatta.com/teamleader_redirect.html"

TEAMLEADER_AUTH_URL = "https://focus.teamleader.eu/oauth2/access_token"
TEAMLEADER_API_BASE = "https://api.focus.teamleader.eu"
TOKENS_FILE = "1teamleader_tokens.json"

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
    Probeer refresh token, anders wissel auth_code. Retourneer access_token of None.
    We maken één POST via requests.Session.
    """
    session = get_session()
    tokens = load_tokens()
    data_base = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "redirect_uri": REDIRECT_URI}

    # First try refresh
    if tokens and tokens.get("refresh_token"):
        data = dict(data_base)
        data.update({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        try:
            r = session.post(TEAMLEADER_AUTH_URL, data=data, timeout=15)
            if r.ok:
                new = r.json()
                save_tokens(new["access_token"], new["refresh_token"])
                return new["access_token"]
        except requests.RequestException as e:
            st.warning(f"Fout bij refresh token: {e}")

    # Next, exchange code
    if auth_code:
        data = dict(data_base)
        data.update({"grant_type": "authorization_code", "code": auth_code})
        try:
            r = session.post(TEAMLEADER_AUTH_URL, data=data, timeout=15)
            if r.ok:
                new = r.json()
                save_tokens(new["access_token"], new["refresh_token"])
                return new["access_token"]
            else:
                st.error(f"Fout bij token exchange: {r.text}")
        except requests.RequestException as e:
            st.error(f"Fout bij token exchange: {e}")

    return None

def build_authorization_url() -> str:
    return (
        "https://focus.teamleader.eu/oauth2/authorize"
        f"?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
    )

# ============ API HELPERS (gebruik session) ============
def _headers(access_token: str, content_json: bool = False):
    h = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if content_json:
        h["Content-Type"] = "application/json"
    return h

def post_json_session(endpoint: str, access_token: str, payload: dict):
    session = get_session()
    url = f"{TEAMLEADER_API_BASE}/{endpoint}"
    return session.post(url, headers=_headers(access_token, content_json=True), json=payload, timeout=30)

def get_json_session(path: str, access_token: str, params: dict = None):
    session = get_session()
    url = f"{TEAMLEADER_API_BASE}/{path}"
    return session.get(url, headers=_headers(access_token), params=params, timeout=30)

# ============ CACHED FETCHES (vermijd herhaalde API-calls) ============
@st.cache_data(show_spinner=False)
def cached_companies(access_token: str) -> List[dict]:
    """Haal ALLE bedrijven (paginated) — cached."""
    session = get_session()
    url = f"{TEAMLEADER_API_BASE}/companies.list"
    headers = _headers(access_token, content_json=True)
    all_companies = []
    page_number = 1
    while True:
        payload = {"page": {"size": 100, "number": page_number}}
        try:
            r = session.post(url, headers=headers, json=payload, timeout=30)
            if not r.ok:
                break
            data = r.json().get("data", [])
            if not data:
                break
            all_companies.extend(data)
            if len(data) < 100:
                break
            page_number += 1
        except requests.RequestException:
            break
    return all_companies

@st.cache_data(show_spinner=False)
def cached_users(access_token: str) -> List[dict]:
    """Haal users — cached."""
    r = post_json_session("users.list", access_token, {})
    if not r.ok:
        return []
    return r.json().get("data", [])

@st.cache_data(show_spinner=False)
def cached_contacts_for_company(access_token: str, company_id: str, page_size: int = 50) -> Tuple[List[dict], Optional[str]]:
    """Haalt contacten voor 1 company op en cached per company id."""
    payload = {"filter": {"company_id": company_id}, "page": {"size": page_size, "number": 1}}
    session = get_session()
    try:
        r = session.post(
            f"{TEAMLEADER_API_BASE}/contacts.list",
            headers=_headers(access_token, content_json=True),
            json=payload,
            timeout=30,
        )
        if not r.ok:
            return [], r.text
        return r.json().get("data", []), None
    except requests.RequestException as e:
        return [], str(e)

# ============ BUSINESS HELPERS ============
def fuzzy_find_company(company_name: str, companies: List[dict], cutoff: float = 0.5) -> Optional[dict]:
    names = [c.get("name", "") for c in companies]
    matches = get_close_matches(company_name.strip(), names, n=1, cutoff=cutoff)
    if matches:
        match = matches[0]
        return next((c for c in companies if c.get("name") == match), None)
    return None

def find_lead_by_exact(access_token: str, lead_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Zoek contact via contacts.list endpoint met filter[name]."""
    r = get_json_session("contacts.list", access_token, params={"filter[name]": lead_name})
    if not r.ok:
        return None, None
    contacts = r.json().get("data") or []
    for c in contacts:
        fullname = c.get("full_name") or f"{c.get('first_name','')} {c.get('last_name','')}".strip()
        if fullname.strip().lower() == str(lead_name).strip().lower():
            return c.get("id"), fullname
    return None, None

def build_deal_lines(product_lines: List[dict]) -> List[dict]:
    out = []
    for line in product_lines:
        try:
            qty = int(line.get("Quantity") or 1)
        except Exception:
            qty = 1
        try:
            price = float(line.get("UnitPrice") or 0)
        except Exception:
            price = 0.0
        vat = int(line.get("VAT rate item") or 21)
        out.append(
            {
                "name": line.get("ProductName") or line.get("name"),
                "quantity": qty,
                "unit_price": price,
                "vat_rate": vat,
            }
        )
    return out

def create_deal(
    access_token: str,
    company_id: str,
    lead_id: str,
    title: str,
    product_lines: List[dict],
    responsible_user_id: Optional[str] = None,
):
    payload = {
        "title": title,
        "lead": {"customer": {"type": "company", "id": company_id}, "contact_person_id": lead_id},
        "source": {"type": "api"},
        "lines": build_deal_lines(product_lines),
    }
    if responsible_user_id:
        payload["responsible_user_id"] = responsible_user_id
    r = post_json_session("deals.create", access_token, payload)
    return r.json() if r.ok else None

def create_quotation(access_token: str, deal_id: str, deal_title: str, product_lines: List[dict]):
    # first try to find VAT rate id
    r = post_json_session("taxRates.list", access_token, {})
    vat_rate_id = None
    if r.ok:
        for tr in r.json().get("data", []):
            if abs(tr.get("rate", 0) - 0.21) < 0.001:
                vat_rate_id = tr.get("id")
                break
    grouped_lines = [
        {
            "section": {"title": "Maten hier in te vullen"},
            "line_items": [
                {
                    "quantity": int(line.get("Quantity") or 1),
                    "description": f"{line.get('ProductName','')} {line.get('Sizes','')}".strip(),
                    "extended_description": line.get("Description", ""),
                    "unit_price": {"amount": float(line.get("UnitPrice") or 0), "tax": "excluding"},
                    "tax_rate_id": vat_rate_id,
                }
                for line in product_lines
            ],
        }
    ]
    payload = {
        "deal_id": deal_id,
        "title": deal_title,
        "text": f"Offerte voor deal '{deal_title}'",
        "currency": {"code": "EUR", "exchange_rate": 1.0},
        "grouped_lines": grouped_lines,
    }
    session = get_session()
    r = session.post(
        f"{TEAMLEADER_API_BASE}/quotations.create",
        headers=_headers(access_token, content_json=True),
        json=payload,
        timeout=30,
    )
    return r.json() if r.ok else None

# ============ UI / APP FLOW ============

# --- Login sidebar (wachtwoord) ---
st.sidebar.title("🔒 Inloggen")
password = st.sidebar.text_input("Voer wachtwoord in", type="password")
if password != CORRECT_PASSWORD:
    st.sidebar.error("❌ Ongeldig wachtwoord.")
    st.stop()
st.sidebar.success("✅ Ingelogd")

# --- Teamleader koppeling / tokens in sidebar ---
st.sidebar.markdown("### 🔗 Teamleader koppeling")

# veld voor handmatig invoeren van nieuwe authorization code
new_auth_code = st.sidebar.text_input("Nieuwe Teamleader Authorization Code")

if st.sidebar.button("Gebruik nieuwe code"):
    token = exchange_or_refresh_token(new_auth_code if new_auth_code else None)
    if token:
        st.session_state.access_token = token
        st.session_state.connected = True
        st.sidebar.success("✅ Verbonden met Teamleader API (nieuwe code).")
    else:
        st.sidebar.error("❌ Kon niet verbinden met deze code.")

# eerste keer: probeer via refresh als er nog geen access_token is
if "access_token" not in st.session_state:
    token = exchange_or_refresh_token(None)
    if token:
        st.session_state.access_token = token
        st.session_state.connected = True
    else:
        st.session_state.connected = False

# als we nog steeds niet connected zijn → toon uitleg + open Teamleader
if not st.session_state.get("connected"):
    auth_url = build_authorization_url()
    st.markdown(
        f"""
        <div style="padding:10px; border:1px solid #f0ad4e; background:#fcf8e3; border-radius:5px; margin-bottom:1rem;">
          ⚠️ <strong>Teamleader-token is niet ingesteld of verlopen.</strong><br><br>
          We openen Teamleader in een nieuw tabblad zodat je kunt inloggen en een nieuwe Authorization Code krijgt.<br>
          Als dat niet automatisch gebeurt, <a href="{auth_url}" target="_blank"><b>klik hier</b></a>.<br><br>
          Plak de code daarna in de linker sidebar bij <em>'Nieuwe Teamleader Authorization Code'</em> en klik op 'Gebruik nieuwe code'.
        </div>
        <script>
        // probeer automatisch een nieuw tabblad te openen met Teamleader-login
        try {{
            window.open("{auth_url}", "_blank");
        }} catch (e) {{
            console.log("Kon popup niet automatisch openen:", e);
        }}
        </script>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

# vanaf hier hebben we een geldige access_token
access_token = st.session_state.access_token

# --- Upload Excel ---
st.title("Teamleader Offerte Generator")
st.write("Upload een Excel-bestand met de DEAL gegevens (minimaal kolommen: 'DealTitle' en 'CompanyName').")

uploaded_file = st.file_uploader("📤 Upload Excel-bestand", type=["xlsx"])
if uploaded_file:
    try:
        df = pd.read_excel(uploaded_file)
        if "DealTitle" not in df.columns or "CompanyName" not in df.columns:
            st.error("Excel mist verplichte kolommen: 'DealTitle' en/of 'CompanyName'.")
            st.stop()
    except Exception as e:
        st.error(f"Fout bij inlezen van Excel: {e}")
        st.stop()
    st.session_state.df = df
    st.success("✅ Excel geladen")
    st.dataframe(df.head())

# vereisten: verbinding + df
if "df" not in st.session_state:
    st.info("Upload eerst een Excel-bestand.")
    st.stop()

df = st.session_state.df

# --- Pick deals ---
deal_titles = df["DealTitle"].astype(str).unique().tolist()
deal_choice = st.selectbox(
    "📦 Kies een deal om te verwerken (of 'Alle deals'):",
    ["-- Selecteer --"] + deal_titles + ["Alle deals"],
)
if deal_choice == "-- Selecteer --":
    st.info("Kies een deal om verder te gaan.")
    st.stop()
if deal_choice == "Alle deals":
    deals_to_process = deal_titles
else:
    deals_to_process = [deal_choice]

# --- Load companies + users (cached) ---
with st.spinner("Bedrijven en users ophalen (cache indien beschikbaar)..."):
    companies = cached_companies(access_token)
    users = cached_users(access_token)

# build user dropdown mapping
user_map: Dict[str, str] = {}
user_options: List[str] = []
for u in users:
    uname = (
        u.get("full_name")
        or f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        or u.get("email")
        or u.get("id")
    )
    user_options.append(uname)
    user_map[uname] = u.get("id")

# --- Prepare per-deal company lookups ---
st.markdown("### Stap: kies per bedrijf de contactpersoon (en verantwoordelijke user)")

unique_company_names: List[str] = []
deal_to_company: Dict[str, str] = {}
for deal_title in deals_to_process:
    rows = df[df["DealTitle"] == deal_title]
    if rows.empty:
        continue
    company_name = str(rows.iloc[0]["CompanyName"])
    deal_to_company[deal_title] = company_name
    if company_name not in unique_company_names:
        unique_company_names.append(company_name)

# fuzzy match companies once
company_matches: Dict[str, Optional[dict]] = {}
for cname in unique_company_names:
    comp = fuzzy_find_company(cname, companies)
    company_matches[cname] = comp

# fetch contacts in parallel for matched companies
contacts_cache: Dict[str, Tuple[List[dict], Optional[str]]] = {}
company_ids_to_fetch = [comp["id"] for comp in company_matches.values() if comp]
if company_ids_to_fetch:
    with st.spinner("Contacten ophalen (parallel)..."):
        with ThreadPoolExecutor(max_workers=6) as executor:
            future_map = {
                executor.submit(cached_contacts_for_company, access_token, cid): cid
                for cid in company_ids_to_fetch
            }
            for fut in as_completed(future_map):
                cid = future_map[fut]
                try:
                    contacts, err = fut.result()
                    contacts_cache[cid] = (contacts, err)
                except Exception as e:
                    contacts_cache[cid] = ([], str(e))

# UI: per deal show company, contact select, user select
for deal_title in deals_to_process:
    company_name = deal_to_company.get(deal_title)
    comp = company_matches.get(company_name)
    st.write(f"**Deal:** {deal_title} — **Bedrijf (Excel):** {company_name}")
    if not comp:
        st.warning(
            f"Bedrijf '{company_name}' niet gevonden in Teamleader - overslaan of maak handmatig aan in Teamleader."
        )
        continue
    company_id = comp["id"]
    st.markdown(f"**Gevonden in Teamleader:** {comp.get('name')} (ID: {company_id})")

    # contacts from cache
    contacts, err = contacts_cache.get(company_id, ([], None))
    if err:
        st.warning(f"Kon contacten niet ophalen voor {comp['name']}: {err}")
        continue
    if not contacts:
        st.warning(
            f"Geen contacten gevonden voor {comp['name']}. Je kunt later handmatig contact toevoegen in Teamleader."
        )
        continue

    contact_options: List[str] = []
    contact_map: Dict[str, str] = {}
    for c in contacts:
        full = c.get("full_name") or f"{c.get('first_name','')} {c.get('last_name','')}".strip()
        contact_options.append(full)
        contact_map[full] = c.get("id")

    # unieke key per bedrijf + deal
    c_key = f"contact_select__{company_id}__{deal_title}"
    if c_key not in st.session_state:
        st.session_state[c_key] = "-- Selecteer --"

    selected_contact = st.selectbox(
        f"Kies contactpersoon voor {comp['name']}",
        ["-- Selecteer --"] + contact_options,
        key=c_key,
    )
    if selected_contact != "-- Selecteer --":
        st.session_state[f"chosen_contact__{company_id}"] = contact_map[selected_contact]
        st.session_state[f"chosen_contact_name__{company_id}"] = selected_contact
        st.success(f"Gekozen contact: {selected_contact}")
    else:
        st.info("Nog geen contact gekozen voor dit bedrijf.")

    # Responsible user selectbox (per company + deal)
    u_key = f"user_select__{company_id}__{deal_title}"
    if u_key not in st.session_state:
        st.session_state[u_key] = "-- Laat Teamleader kiezen --"

    user_choice = st.selectbox(
        f"Kies responsible user voor {comp['name']} (optioneel)",
        ["-- Laat Teamleader kiezen --"] + user_options,
        key=u_key,
    )
    if user_choice != "-- Laat Teamleader kiezen --":
        st.session_state[f"chosen_user__{company_id}"] = user_map.get(user_choice)
        st.session_state[f"chosen_user_name__{company_id}"] = user_choice
        st.success(f"Gekozen responsible user: {user_choice}")
    else:
        st.session_state.pop(f"chosen_user__{company_id}", None)
        st.info("Teamleader kiest automatisch verantwoordelijk persoon (geen keuze).")

# --- Final action button ---
st.markdown("---")
if st.button("🚀 Maak deals + offertes aan voor geselecteerde deal(s)"):
    total = len(deals_to_process)
    progress = st.progress(0)
    i = 0
    with st.spinner("Verwerken..."):
        for deal_title in deals_to_process:
            i += 1
            progress.progress(int((i / total) * 100))

            rows = df[df["DealTitle"] == deal_title]
            if rows.empty:
                st.warning(f"Geen regels gevonden voor deal '{deal_title}' - overslaan.")
                continue

            company_name = rows.iloc[0]["CompanyName"]
            comp = company_matches.get(company_name)
            if not comp:
                st.warning(f"Bedrijf '{company_name}' niet gevonden - overslaan.")
                continue

            company_id = comp["id"]
            chosen_contact_key = f"chosen_contact__{company_id}"
            if chosen_contact_key not in st.session_state:
                st.warning(
                    f"Geen contact gekozen voor bedrijf {comp['name']} — kies een contact en probeer opnieuw."
                )
                continue

            lead_id = st.session_state[chosen_contact_key]
            chosen_user_key = f"chosen_user__{company_id}"
            responsible_user_id = st.session_state.get(chosen_user_key)

            st.info(
                f"Aanmaken deal '{deal_title}' voor {comp['name']} met contact "
                f"{st.session_state.get(f'chosen_contact_name__{company_id}', '(onbekend)')}"
                + (
                    f" en responsible user {st.session_state.get(f'chosen_user_name__{company_id}')}"
                    if responsible_user_id
                    else ""
                )
            )

            product_lines = rows.to_dict(orient="records")
            deal_resp = create_deal(
                access_token,
                company_id,
                lead_id,
                deal_title,
                product_lines,
                responsible_user_id=responsible_user_id,
            )
            if not deal_resp:
                st.error(
                    f"❌ Deal '{deal_title}' kon niet worden aangemaakt voor {comp['name']}."
                )
                continue

            deal_id = deal_resp.get("data", {}).get("id")
            st.success(f"✅ Deal '{deal_title}' aangemaakt (ID={deal_id})")

            quotation_resp = create_quotation(access_token, deal_id, deal_title, product_lines)
            if quotation_resp:
                qid = quotation_resp.get("data", {}).get("id")
                st.success(
                    f"💡 Offerte aangemaakt voor deal '{deal_title}' (ID={qid})"
                )
            else:
                st.warning(
                    f"⚠️ Offerte kon niet worden aangemaakt voor deal '{deal_title}'"
                )

    progress.progress(100)
    st.balloons()


