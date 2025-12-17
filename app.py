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

# ============ CONFIG ============
CORRECT_PASSWORD = st.secrets["auth"]["password"]
CLIENT_ID = st.secrets["CLIENT_ID"]
CLIENT_SECRET = st.secrets["CLIENT_SECRET"]

REDIRECT_URI = "https://www.kwatta.com/teamleader_redirect.html"
TEAMLEADER_AUTH_URL = "https://focus.teamleader.eu/oauth2/access_token"
TEAMLEADER_API_BASE = "https://api.focus.teamleader.eu"
TOKENS_FILE = "teamleader_tokens.json"

# =============================================
#   TOKEN STORAGE
# =============================================
def save_tokens(access_token: str, refresh_token: str):
    with open(TOKENS_FILE, "w") as f:
        json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)

def load_tokens():
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE) as f:
                return json.load(f)
        except:
            return None
    return None

# =============================================
#   EXCHANGE + REFRESH TOKEN
# =============================================
def exchange_or_refresh_token(auth_code: Optional[str] = None):
    session = requests.Session()
    tokens = load_tokens()

    data_base = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI
    }

    # TRY REFRESH TOKEN
    if tokens and tokens.get("refresh_token"):
        data = dict(data_base)
        data.update({
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"]
        })
        r = session.post(TEAMLEADER_AUTH_URL, data=data)
        if r.ok:
            new = r.json()
            save_tokens(new["access_token"], new["refresh_token"])
            return new["access_token"]

    # TRY AUTHORIZATION CODE
    if auth_code:
        data = dict(data_base)
        data.update({
            "grant_type": "authorization_code",
            "code": auth_code
        })
        r = session.post(TEAMLEADER_AUTH_URL, data=data)
        if r.ok:
            new = r.json()
            save_tokens(new["access_token"], new["refresh_token"])
            return new["access_token"]
        else:
            st.error("Ongeldige Authorization Code.")
            return None

    return None


# =============================================
#   AUTOMATISCHE OAUTH VIA QUERYSTRING
# =============================================
query_params = st.query_params

if "oauth_code" in query_params:
    code = query_params["oauth_code"]
    token = exchange_or_refresh_token(code)

    if token:
        st.session_state.access_token = token
        st.session_state.connected = True
        st.success("🔐 Succesvol automatisch verbonden met Teamleader!")
    else:
        st.error("❌ Kon de ontvangen Authorization Code niet omwisselen.")

# =============================================
#   LOGIN — WACHTWOORD
# =============================================
st.sidebar.title("🔒 Inloggen")
password = st.sidebar.text_input("Voer wachtwoord in", type="password")

if password != CORRECT_PASSWORD:
    st.sidebar.error("❌ Ongeldig wachtwoord.")
    st.stop()

st.sidebar.success("✅ Ingelogd")


# =============================================
#   INITIAL TOKEN VIA REFRESH
# =============================================
if "access_token" not in st.session_state:
    token = exchange_or_refresh_token(None)
    if token:
        st.session_state.access_token = token
        st.session_state.connected = True
    else:
        st.session_state.connected = False

# =============================================
#   ZO NIET VERBONDEN → AUTOMATISCH TEAMLEADER OPENEN
# =============================================
def teamleader_oauth_url():
    return (
        "https://focus.teamleader.eu/oauth2/authorize"
        f"?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
    )

if not st.session_state.get("connected"):
    auth_url = teamleader_oauth_url()

    st.markdown(
        f"""
        <div style="padding:12px; background:#fff7cc; border-left:4px solid #ffa500; border-radius:5px;">
        <b>Teamleader moet opnieuw autoriseren.</b><br><br>
        We openen automatisch Teamleader zodat je kunt inloggen.
        Als dit niet werkt, <a href="{auth_url}" target="_blank"><b>klik hier</b></a>.
        </div>

        <script>
        try {{
            window.open("{auth_url}", "_blank");
        }} catch (e) {{
            console.log("Popup blokkade:", e);
        }}
        </script>
        """,
        unsafe_allow_html=True,
    )
    st.stop()


# =============================================
#   API HELPERS
# =============================================
def _headers(access_token, json_mode=False):
    h = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    if json_mode:
        h["Content-Type"] = "application/json"
    return h

def post_json(endpoint, access_token, payload):
    url = f"{TEAMLEADER_API_BASE}/{endpoint}"
    return requests.post(url, json=payload, headers=_headers(access_token, True))

def get_json(endpoint, access_token, params=None):
    url = f"{TEAMLEADER_API_BASE}/{endpoint}"
    return requests.get(url, params=params, headers=_headers(access_token))


# =============================================
#   CACHED API CALLS
# =============================================
@st.cache_data(show_spinner=False)
def cached_companies(access_token):
    data = []
    page = 1
    while True:
        r = post_json("companies.list", access_token, {"page": {"size": 100, "number": page}})
        if not r.ok:
            break
        rows = r.json().get("data", [])
        if not rows:
            break
        data.extend(rows)
        if len(rows) < 100:
            break
        page += 1
    return data

@st.cache_data(show_spinner=False)
def cached_users(access_token):
    r = post_json("users.list", access_token, {})
    if not r.ok:
        return []
    return r.json().get("data", [])

@st.cache_data(show_spinner=False)
def cached_contacts(access_token, company_id):
    r = post_json(
        "contacts.list",
        access_token,
        {"filter": {"company_id": company_id}, "page": {"size": 50, "number": 1}},
    )
    if not r.ok:
        return [], "Fout bij ophalen"
    return r.json().get("data", []), None


# =============================================
#   HELPER FUNCTIES
# =============================================
def fuzzy_company(name, companies):
    names = [c["name"] for c in companies]
    match = get_close_matches(name, names, n=1, cutoff=0.5)
    if match:
        return next(c for c in companies if c["name"] == match[0])
    return None

def build_deal_lines(product_lines):
    out = []
    for line in product_lines:
        qty = int(line.get("Quantity", 1))
        price = float(line.get("UnitPrice", 0))
        vat = int(line.get("VAT rate item", 21))
        out.append({
            "name": line.get("ProductName") or line.get("name"),
            "quantity": qty,
            "unit_price": price,
            "vat_rate": vat
        })
    return out

def create_deal(access_token, company_id, lead_id, title, lines, user_id=None):
    payload = {
        "title": title,
        "lead": {
            "customer": {"type": "company", "id": company_id},
        },
        "source": {"type": "api"},
        "lines": lines
    }

    # contactpersoon alleen meesturen als je er één hebt
    if lead_id:
        payload["lead"]["contact_person_id"] = lead_id

    if user_id:
        payload["responsible_user_id"] = user_id

    r = post_json("deals.create", access_token, payload)
    return r.json() if r.ok else None

def create_quotation(access_token, deal_id, deal_title, product_lines):
    # vat rate id
    vat_id = None
    r = post_json("taxRates.list", access_token, {})
    if r.ok:
        for t in r.json().get("data", []):
            if abs(t.get("rate", 0) - 0.21) < 0.001:
                vat_id = t["id"]
                break

    grouped = [{
        "section": {"title": "Maten hier in te vullen"},
        "line_items": [{
            "quantity": int(p.get("Quantity", 1)),
            "description": f"{p.get('ProductName','')} {p.get('Sizes','')}",
            "extended_description": p.get("Description", ""),
            "unit_price": {"amount": float(p.get("UnitPrice", 0)), "tax": "excluding"},
            "tax_rate_id": vat_id
        } for p in product_lines]
    }]

    payload = {
        "deal_id": deal_id,
        "title": deal_title,
        "text": f"Offerte voor deal '{deal_title}'",
        "currency": {"code": "EUR", "exchange_rate": 1.0},
        "grouped_lines": grouped
    }

    rr = post_json("quotations.create", access_token, payload)
    return rr.json() if rr.ok else None


# =============================================
#   UI — APP
# =============================================
st.title("Teamleader Offerte Generator")
st.write("Upload Excel met minimaal kolommen: **DealTitle** en **CompanyName**.")

uploaded_file = st.file_uploader("📤 Upload Excel-bestand", type=["xlsx"])

if uploaded_file:
    df = pd.read_excel(uploaded_file)
    if "DealTitle" not in df or "CompanyName" not in df:
        st.error("Excel mist kolommen: DealTitle en/of CompanyName.")
        st.stop()
    st.session_state.df = df
    st.success("Excel geladen")
    st.dataframe(df.head())

if "df" not in st.session_state:
    st.info("Upload een Excel-bestand om verder te gaan.")
    st.stop()

df = st.session_state.df
access_token = st.session_state.access_token

# DEAL SELECTIE
deal_titles = df["DealTitle"].astype(str).unique().tolist()
choice = st.selectbox("📦 Kies een deal (of 'Alle deals'):", ["-- Selecteer --"] + deal_titles + ["Alle deals"])

if choice == "-- Selecteer --":
    st.stop()

if choice == "Alle deals":
    deals = deal_titles
else:
    deals = [choice]

# LOAD COMPANIES & USERS
with st.spinner("Bedrijven en users ophalen..."):
    companies = cached_companies(access_token)
    users = cached_users(access_token)

# user dropdown
user_map = {u.get("full_name") or u.get("email") or u["id"]: u["id"] for u in users}
user_names = list(user_map.keys())

# CONTACTPERSONEN KIEZEN
st.header("📇 Contactpersonen per bedrijf kiezen")

company_lookup = {}
for deal in deals:
    cname = df[df["DealTitle"] == deal].iloc[0]["CompanyName"]
    company_lookup[deal] = cname

# fuzzy match
company_match = {name: fuzzy_company(name, companies) for name in company_lookup.values()}

# contacts fetchen
contacts_cache = {}
for comp in company_match.values():
    if comp:
        contacts_cache[comp["id"]] = cached_contacts(access_token, comp["id"])

for deal in deals:
    cname = company_lookup[deal]
    comp = company_match[cname]

    st.subheader(f"Deal: {deal}")
    if not comp:
        st.warning(f"Bedrijf '{cname}' niet gevonden.")
        continue

    company_id = comp["id"]
    st.write(f"Teamleader bedrijf: **{comp['name']}**")

    contact_list, err = contacts_cache.get(company_id, ([], None))
    if err:
        st.warning(f"Fout bij ophalen contacten: {err}")
        continue

    # contact select
    contact_names = []
    contact_map = {}
    for c in contact_list:
        nm = c.get("full_name") or f"{c.get('first_name','')} {c.get('last_name','')}"
        contact_names.append(nm)
        contact_map[nm] = c["id"]

    c_key = f"contact_{company_id}"
    chosen_contact = st.selectbox(
        f"Contactpersoon voor {comp['name']}",
        ["-- Selecteer --"] + contact_names,
        key=c_key
    )

    if chosen_contact != "-- Selecteer --":
        st.session_state[f"lead_{company_id}"] = contact_map[chosen_contact]
        st.session_state[f"leadname_{company_id}"] = chosen_contact

    # responsible user
    u_widget_key = f"user_select__{company_id}"  # widget key
    chosen_user = st.selectbox(
        f"Responsible user voor {comp['name']}",
        ["-- Laat Teamleader kiezen --"] + user_names,
        key=u_widget_key
    )

    user_id_key = f"selected_user_id__{company_id}"
    user_name_key = f"selected_user_name__{company_id}"
    
    if chosen_user != "-- Laat Teamleader kiezen --":
        st.session_state[user_id_key] = user_map[chosen_user]
        st.session_state[user_name_key] = chosen_user
    else:
        st.session_state.pop(user_id_key, None)
        st.session_state.pop(user_name_key, None)


# =============================================
#   UITVOER — DEALS AANMAKEN
# =============================================
st.markdown("---")

if st.button("🚀 Maak deals + offertes"):
    total = len(deals)
    progress = st.progress(0)
    i = 0

    with st.spinner("Deals aanmaken..."):
        for deal in deals:
            i += 1
            progress.progress(int(i / total * 100))

            rows = df[df["DealTitle"] == deal]
            cname = rows.iloc[0]["CompanyName"]
            comp = company_match[cname]

            if not comp:
                st.error(f"Bedrijf {cname} niet gevonden.")
                continue

            company_id = comp["id"]

            # contact is optioneel
            lead_id = st.session_state.get(f"lead_{company_id}", None)
            
            # responsible user (met de nieuwe key fix)
            user_id = st.session_state.get(f"selected_user_id__{company_id}", None)
            
            deal_lines = build_deal_lines(rows.to_dict(orient="records"))

            # DEAL AANMAKEN
            resp = create_deal(access_token, company_id, lead_id, deal, deal_lines, user_id=user_id)
            if not resp:
                st.error(f"❌ Deal '{deal}' kon niet worden aangemaakt.")
                continue

            deal_id = resp.get("data", {}).get("id")
            st.success(f"Deal '{deal}' aangemaakt (ID={deal_id})")

            # OFFERTE
            q = create_quotation(access_token, deal_id, deal, rows.to_dict(orient="records"))
            if q:
                qid = q.get("data", {}).get("id")
                st.success(f"Offerte aangemaakt (ID={qid})")
            else:
                st.warning("⚠️ Offerte kon niet aangemaakt worden.")

    progress.progress(100)
    st.balloons()


