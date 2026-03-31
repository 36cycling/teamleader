import streamlit as st
import pandas as pd
import json
import requests
import os
import re
from typing import Optional, Dict, List
from difflib import SequenceMatcher

# ============ PAGINA-INSTELLINGEN ============
st.set_page_config(page_title="36 Cycling - Bestelbon Tool", page_icon="🚴", layout="wide")

# ============ CONFIG ============
CORRECT_PASSWORD = st.secrets["auth"]["password"]
CLIENT_ID = st.secrets["CLIENT_ID"]
CLIENT_SECRET = st.secrets["CLIENT_SECRET"]

REDIRECT_URI = "https://www.kwatta.com/teamleader_redirect.html"
TEAMLEADER_AUTH_URL = "https://focus.teamleader.eu/oauth2/access_token"
TEAMLEADER_API_BASE = "https://api.focus.teamleader.eu"
TOKENS_FILE = "teamleader_tokens.json"

# ============ PRODUCT MAPPING ============
# Nederlands → Engels productname mapping (zonder geslacht)
PRODUCT_MAP = {
    "wielershirt pro": "Cycling Jersey Pro",
    "wielershirt cadans": "Cycling Jersey Cadans",
    "wielershirt": "Cycling Jersey",
    "wielerbroek": "Bib Shorts",
    "lange wielerbroek": "Bib Tight",
    "3/4 wielerbroek": "3/4 Bib Tight",
    "all season jack": "All Season Jacket",
    "windjack zonder mouwen elaspin": "Wind Vest Elaspin",
    "windjack zonder mouwen": "Wind Vest",
    "windjack": "Wind Jacket",
    "armstukken": "Arm Warmers",
    "beenstukken": "Leg Warmers",
    "overschoenen": "Shoe Covers",
    "wielerhandschoen": "Cycling Gloves",
    "bandana": "Bandana",
    "sokken": "Socks",
    "bidon": "Bottle",
    "musette": "Musette",
    "pet": "Cap",
}

GENDER_MAP = {
    "man": "Men",
    "vrouw": "Women",
    "kind": "Kids",
}


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
        except Exception:
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
        "redirect_uri": REDIRECT_URI,
    }

    if tokens and tokens.get("refresh_token"):
        data = dict(data_base)
        data.update({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        r = session.post(TEAMLEADER_AUTH_URL, data=data)
        if r.ok:
            new = r.json()
            save_tokens(new["access_token"], new["refresh_token"])
            return new["access_token"]

    if auth_code:
        data = dict(data_base)
        data.update({"grant_type": "authorization_code", "code": auth_code})
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
        st.success("Succesvol verbonden met Teamleader!")
    else:
        st.error("Kon de Authorization Code niet omwisselen.")

# =============================================
#   LOGIN
# =============================================
st.sidebar.title("Inloggen")
password = st.sidebar.text_input("Wachtwoord", type="password")

if password != CORRECT_PASSWORD:
    st.sidebar.error("Ongeldig wachtwoord.")
    st.stop()

st.sidebar.success("Ingelogd")

# =============================================
#   INITIAL TOKEN
# =============================================
if "access_token" not in st.session_state:
    token = exchange_or_refresh_token(None)
    if token:
        st.session_state.access_token = token
        st.session_state.connected = True
    else:
        st.session_state.connected = False


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
        <a href="{auth_url}" target="_blank"><b>Klik hier om te verbinden</b></a>.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()


# =============================================
#   API HELPERS
# =============================================
def post_json(endpoint, payload):
    token = st.session_state.access_token
    url = f"{TEAMLEADER_API_BASE}/{endpoint}"
    r = requests.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    if r.status_code == 401:
        # Token expired, try refresh
        new_token = exchange_or_refresh_token(None)
        if new_token:
            st.session_state.access_token = new_token
            r = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {new_token}", "Content-Type": "application/json"},
            )
    return r


# =============================================
#   CSV PARSER
# =============================================
def parse_csv(uploaded_file) -> pd.DataFrame:
    """Parse de bestelbon CSV naar een gestructureerd DataFrame."""
    df = pd.read_csv(uploaded_file, sep=";", encoding="utf-8")

    # Eerste kolom is "Product"
    size_cols = [c for c in df.columns if c not in ("Product", "Totaal")]

    rows = []
    for _, row in df.iterrows():
        product_raw = str(row.get("Product", ""))
        if not product_raw or product_raw == "nan":
            continue

        # Parse productnaam: "Moore DRV - Wielershirt PRO *NEW - Geslacht:Man - Bestelling:Cliënt"
        parts = [p.strip() for p in product_raw.split(" - ")]
        if len(parts) < 2:
            continue

        company_name = parts[0]  # "Moore DRV"
        product_name_raw = parts[1]  # "Wielershirt PRO *NEW"

        # Strip *NEW en andere tags
        product_name_clean = re.sub(r"\s*\*\w+", "", product_name_raw).strip()

        # Zoek geslacht
        gender = ""
        order_type = ""
        for part in parts[2:]:
            if part.startswith("Geslacht:"):
                gender = part.replace("Geslacht:", "").strip()
            elif part.startswith("Bestelling:"):
                order_type = part.replace("Bestelling:", "").strip()

        # Quantities per maat
        for size_col in size_cols:
            qty = row.get(size_col)
            if pd.notna(qty) and str(qty).strip() and str(qty).strip() != "0":
                try:
                    qty_int = int(float(str(qty).strip()))
                    if qty_int > 0:
                        rows.append({
                            "company": company_name,
                            "product_nl": product_name_clean,
                            "gender_nl": gender,
                            "order_type": order_type,
                            "size": size_col,
                            "quantity": qty_int,
                            "product_raw": product_raw,
                        })
                except ValueError:
                    pass

    return pd.DataFrame(rows)


# =============================================
#   PRODUCT MATCHING
# =============================================
def match_product_to_template(product_nl: str, gender_nl: str, template_lines: List[Dict]) -> Optional[Dict]:
    """Match een Nederlands product naar een template line item."""

    # Stap 1: Probeer de mapping tabel
    product_lower = product_nl.lower().strip()
    english_base = None
    for nl_key, en_value in PRODUCT_MAP.items():
        if nl_key in product_lower:
            english_base = en_value
            break

    # Stap 2: Voeg geslacht toe
    gender_en = GENDER_MAP.get(gender_nl.lower(), "") if gender_nl else ""
    if english_base and gender_en:
        search_term = f"{english_base} - {gender_en}"
    elif english_base:
        search_term = english_base
    else:
        search_term = product_nl  # fallback

    # Stap 3: Fuzzy match tegen template line items
    best_match = None
    best_score = 0.0

    for line in template_lines:
        desc = line.get("description", "")
        # Exact match
        if search_term.lower() in desc.lower():
            return line

        # Fuzzy score
        score = SequenceMatcher(None, search_term.lower(), desc.lower()).ratio()

        # Bonus als basisprodcutnaam erin zit
        if english_base and english_base.lower() in desc.lower():
            score += 0.3

        # Bonus als geslacht matcht
        if gender_en and gender_en.lower() in desc.lower():
            score += 0.2

        if score > best_score:
            best_score = score
            best_match = line

    if best_score >= 0.4:
        return best_match

    return None


def get_template_lines(deal_id: str) -> List[Dict]:
    """Haal alle line items op uit de offerte(s) van een deal."""
    r = post_json("quotations.list", {"filter": {"deal_id": deal_id}, "page": {"size": 10, "number": 1}})
    if not r.ok:
        return []

    quotations = r.json().get("data", [])
    all_lines = []

    for q in quotations:
        qr = post_json("quotations.info", {"id": q["id"]})
        if not qr.ok:
            continue
        for group in qr.json().get("data", {}).get("grouped_lines", []):
            for item in group.get("line_items", []):
                all_lines.append(item)

    return all_lines


# =============================================
#   DEAL SEARCH
# =============================================
@st.cache_data(show_spinner=False)
def search_deals(access_token, term):
    """Zoek deals op basis van een zoekterm."""
    r = post_json("deals.list", {"filter": {"term": term}, "page": {"size": 20, "number": 1}})
    if not r.ok:
        return []
    return r.json().get("data", [])


@st.cache_data(show_spinner=False)
def search_companies(access_token, term):
    """Zoek bedrijven."""
    r = post_json("companies.list", {"filter": {"term": term}, "page": {"size": 10, "number": 1}})
    if not r.ok:
        return []
    return r.json().get("data", [])


@st.cache_data(show_spinner=False)
def get_deal_info(access_token, deal_id):
    """Haal deal details op."""
    r = post_json("deals.info", {"id": deal_id})
    if not r.ok:
        return None
    return r.json().get("data")


# =============================================
#   UI — HOOFDAPP
# =============================================
st.title("36 Cycling - Bestelbon Tool")
st.write("Upload een bestelbon CSV, selecteer een template deal, en maak automatisch een nieuwe deal + offerte aan.")

# --- STAP 1: CSV UPLOAD ---
st.header("1. Upload bestelbon")
uploaded_file = st.file_uploader("Upload CSV bestand", type=["csv"])

if uploaded_file:
    try:
        parsed = parse_csv(uploaded_file)
        st.session_state.parsed_csv = parsed
        if len(parsed) == 0:
            st.error("Geen producten gevonden in de CSV.")
            st.stop()
        st.success(f"{len(parsed)} productregels gevonden")

        # Toon samenvatting
        company = parsed["company"].iloc[0] if len(parsed) > 0 else "Onbekend"
        st.session_state.csv_company = company
        st.write(f"**Bedrijf:** {company}")

        # Groepeer per product + geslacht
        summary = parsed.groupby(["product_nl", "gender_nl", "order_type"]).agg(
            total_qty=("quantity", "sum"),
            sizes=("size", lambda x: ", ".join(f"{s}" for s in x)),
        ).reset_index()
        st.dataframe(summary, use_container_width=True)
    except Exception as e:
        st.error(f"Fout bij het parsen van de CSV: {e}")
        st.stop()

if "parsed_csv" not in st.session_state:
    st.info("Upload een CSV bestand om te beginnen.")
    st.stop()

parsed = st.session_state.parsed_csv
company_name = st.session_state.csv_company

# --- STAP 2: BEDRIJF ZOEKEN ---
st.header("2. Bedrijf in Teamleader")
with st.spinner("Bedrijf zoeken..."):
    companies = search_companies(st.session_state.access_token, company_name)

if companies:
    company_options = {c["name"]: c["id"] for c in companies}
    selected_company_name = st.selectbox("Selecteer bedrijf", list(company_options.keys()))
    selected_company_id = company_options[selected_company_name]
    st.success(f"Bedrijf: **{selected_company_name}**")
else:
    st.warning(f"Bedrijf '{company_name}' niet gevonden. Zoek handmatig:")
    manual_search = st.text_input("Zoek bedrijf")
    if manual_search:
        companies = search_companies(st.session_state.access_token, manual_search)
        if companies:
            company_options = {c["name"]: c["id"] for c in companies}
            selected_company_name = st.selectbox("Selecteer bedrijf", list(company_options.keys()))
            selected_company_id = company_options[selected_company_name]
        else:
            st.error("Geen bedrijf gevonden.")
            st.stop()
    else:
        st.stop()

# --- STAP 3: TEMPLATE DEAL ---
st.header("3. Template deal selecteren")
st.write("Zoek de deal die als template dient (producten, beschrijvingen en prijzen worden overgenomen).")

deal_search = st.text_input("Zoek deal (naam of nummer)", value=f"{company_name.split()[0]} TEMPLATE" if company_name else "")

if deal_search:
    with st.spinner("Deals zoeken..."):
        deals = search_deals(st.session_state.access_token, deal_search)

    if deals:
        deal_options = {}
        for d in deals:
            ref = d.get("reference", "")
            label = f"Deal {ref}: {d['title']}" if ref else d["title"]
            deal_options[label] = d["id"]

        selected_deal_label = st.selectbox("Selecteer template deal", list(deal_options.keys()))
        template_deal_id = deal_options[selected_deal_label]

        # Haal template line items op
        with st.spinner("Offerte line items ophalen..."):
            template_lines = get_template_lines(template_deal_id)

        if template_lines:
            st.success(f"{len(template_lines)} producten gevonden in de template offerte")
            with st.expander("Template producten bekijken"):
                for line in template_lines:
                    price = line.get("unit_price", {}).get("amount", 0)
                    st.write(f"- **{line['description']}** — {price:.2f} EUR")
            st.session_state.template_lines = template_lines
            st.session_state.template_deal_id = template_deal_id
        else:
            st.warning("Geen offerte/line items gevonden in deze deal.")
            st.stop()
    else:
        st.warning("Geen deals gevonden.")
        st.stop()
else:
    st.stop()

if "template_lines" not in st.session_state:
    st.stop()

# --- STAP 4: PRODUCT MATCHING ---
st.header("4. Product matching")
st.write("Hieronder zie je hoe de CSV-producten gematcht worden met de template producten.")

template_lines = st.session_state.template_lines
matches = []
unmatched = []

# Unieke producten uit CSV
unique_products = parsed[["product_nl", "gender_nl"]].drop_duplicates()

for _, row in unique_products.iterrows():
    product_nl = row["product_nl"]
    gender_nl = row["gender_nl"]

    match = match_product_to_template(product_nl, gender_nl, template_lines)

    display_name = f"{product_nl}"
    if gender_nl:
        display_name += f" ({gender_nl})"

    if match:
        matches.append({
            "csv_product": display_name,
            "product_nl": product_nl,
            "gender_nl": gender_nl,
            "matched_to": match["description"],
            "unit_price": match.get("unit_price", {}).get("amount", 0),
            "extended_description": match.get("extended_description", ""),
            "template_line": match,
        })
    else:
        unmatched.append(display_name)

if matches:
    st.write("**Gevonden matches:**")
    match_df = pd.DataFrame([{
        "CSV Product": m["csv_product"],
        "Template Match": m["matched_to"],
        "Prijs": f"{m['unit_price']:.2f} EUR",
    } for m in matches])
    st.dataframe(match_df, use_container_width=True)

    # Handmatige correctie mogelijk maken
    st.write("**Pas matches aan indien nodig:**")
    template_descriptions = ["-- Geen match --"] + [l["description"] for l in template_lines]

    corrected_matches = []
    for m in matches:
        col1, col2 = st.columns([1, 2])
        with col1:
            st.write(f"**{m['csv_product']}**")
        with col2:
            default_idx = template_descriptions.index(m["matched_to"]) if m["matched_to"] in template_descriptions else 0
            corrected = st.selectbox(
                f"Match voor {m['csv_product']}",
                template_descriptions,
                index=default_idx,
                key=f"match_{m['product_nl']}_{m['gender_nl']}",
                label_visibility="collapsed",
            )
            if corrected != "-- Geen match --":
                matched_line = next(l for l in template_lines if l["description"] == corrected)
                corrected_matches.append({
                    **m,
                    "matched_to": corrected,
                    "unit_price": matched_line.get("unit_price", {}).get("amount", 0),
                    "extended_description": matched_line.get("extended_description", ""),
                    "template_line": matched_line,
                })
            else:
                corrected_matches.append(m)

    st.session_state.final_matches = corrected_matches

if unmatched:
    st.warning(f"Niet gematcht: {', '.join(unmatched)}")

if "final_matches" not in st.session_state or not st.session_state.final_matches:
    st.stop()

# --- STAP 5: DEAL + OFFERTE AANMAKEN ---
st.header("5. Deal + Offerte aanmaken")

new_deal_title = st.text_input("Deal titel", value=f"{company_name} - Bestelling 2025")

# Groepeer per bestelling type
order_types = parsed["order_type"].unique().tolist()
selected_order_types = st.multiselect("Welke bestellingen meenemen?", order_types, default=order_types)

if st.button("Maak deal + offerte aan"):
    final_matches = st.session_state.final_matches
    filtered_csv = parsed[parsed["order_type"].isin(selected_order_types)]

    with st.spinner("Deal aanmaken..."):
        # Maak deal aan
        deal_payload = {
            "title": new_deal_title,
            "lead": {"customer": {"type": "company", "id": selected_company_id}},
            "source": {"type": "api"},
        }
        r = post_json("deals.create", deal_payload)
        if not r.ok:
            st.error(f"Fout bij aanmaken deal: {r.text}")
            st.stop()

        new_deal_id = r.json().get("data", {}).get("id")
        st.success(f"Deal aangemaakt: **{new_deal_title}** (ID: {new_deal_id})")

    with st.spinner("Offerte aanmaken..."):
        # BTW tarief ophalen
        vat_id = None
        vr = post_json("taxRates.list", {})
        if vr.ok:
            for t in vr.json().get("data", []):
                if abs(t.get("rate", 0) - 0.21) < 0.001:
                    vat_id = t["id"]
                    break

        # Bouw line items per bestelling type
        grouped_lines = []

        for order_type in selected_order_types:
            type_rows = filtered_csv[filtered_csv["order_type"] == order_type]
            line_items = []

            # Groepeer per product
            for (product_nl, gender_nl), product_rows in type_rows.groupby(["product_nl", "gender_nl"]):
                # Zoek de match
                match = None
                for m in final_matches:
                    if m["product_nl"] == product_nl and m["gender_nl"] == gender_nl:
                        match = m
                        break

                if not match or match["matched_to"] == "-- Geen match --":
                    continue

                # Maten samenvatting
                sizes_detail = " / ".join(
                    f"{r['quantity']} {r['size']}" for _, r in product_rows.iterrows()
                )
                total_qty = product_rows["quantity"].sum()

                line_items.append({
                    "quantity": total_qty,
                    "description": match["matched_to"],
                    "extended_description": f"{match.get('extended_description', '')}\n\nMaten: {sizes_detail}".strip(),
                    "unit_price": {
                        "amount": match["unit_price"],
                        "tax": "excluding",
                    },
                    "tax_rate_id": vat_id,
                })

            if line_items:
                grouped_lines.append({
                    "section": {"title": f"Bestelling: {order_type}"},
                    "line_items": line_items,
                })

        if not grouped_lines:
            st.error("Geen line items om toe te voegen.")
            st.stop()

        q_payload = {
            "deal_id": new_deal_id,
            "title": new_deal_title,
            "text": f"Offerte voor {company_name}",
            "currency": {"code": "EUR", "exchange_rate": 1.0},
            "grouped_lines": grouped_lines,
        }

        qr = post_json("quotations.create", q_payload)
        if qr.ok:
            qid = qr.json().get("data", {}).get("id")
            st.success(f"Offerte aangemaakt (ID: {qid})")
            st.balloons()
        else:
            st.error(f"Fout bij aanmaken offerte: {qr.text}")
