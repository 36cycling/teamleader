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
def detect_company_position(df) -> tuple:
    """Detecteer of het bedrijf het eerste of tweede deel is van de productnaam.

    Het bedrijf is het deel dat HETZELFDE is in alle regels.
    Het product is het deel dat VERSCHILT per regel.
    Returns: (company_index, product_index) — bijv. (0, 1) of (1, 0)
    """
    first_parts = []
    second_parts = []

    for _, row in df.iterrows():
        product_raw = str(row.get("Product", ""))
        if not product_raw or product_raw == "nan":
            continue
        parts = [p.strip() for p in product_raw.split(" - ")]
        if len(parts) >= 2:
            # Strip tags zoals *NEW, Geslacht:, Bestelling: voor vergelijking
            first_parts.append(re.sub(r"\s*\*\w+", "", parts[0]).strip())
            second_parts.append(re.sub(r"\s*\*\w+", "", parts[1]).strip())

    if not first_parts:
        return 0, 1

    # Tel unieke waarden: het deel met MINDER unieke waarden is het bedrijf
    unique_first = len(set(first_parts))
    unique_second = len(set(second_parts))

    if unique_first <= unique_second:
        return 0, 1  # Eerste deel = bedrijf (bijv. "Moore DRV - Wielershirt PRO")
    else:
        return 1, 0  # Tweede deel = bedrijf (bijv. "Wielershirt Pro - TC Zevenhuizen")


def parse_csv(uploaded_file) -> pd.DataFrame:
    """Parse de bestelbon CSV naar een gestructureerd DataFrame."""
    df = pd.read_csv(uploaded_file, sep=";", encoding="utf-8")

    # Eerste kolom is "Product"
    size_cols = [c for c in df.columns if c not in ("Product", "Totaal")]

    # Detecteer of bedrijf het eerste of tweede deel is
    company_idx, product_idx = detect_company_position(df)

    rows = []
    for _, row in df.iterrows():
        product_raw = str(row.get("Product", ""))
        if not product_raw or product_raw == "nan":
            continue

        # Parse productnaam
        parts = [p.strip() for p in product_raw.split(" - ")]
        if len(parts) < 2:
            continue

        company_name = parts[company_idx]
        product_name_raw = parts[product_idx]

        # Strip *NEW en andere tags
        product_name_clean = re.sub(r"\s*\*\w+", "", product_name_raw).strip()

        # Zoek geslacht en besteltype in alle overige delen
        gender = ""
        order_type = ""
        for i, part in enumerate(parts):
            if i == company_idx or i == product_idx:
                continue
            if part.startswith("Geslacht:"):
                gender = part.replace("Geslacht:", "").strip()
            elif part.startswith("Bestelling:"):
                order_type = part.replace("Bestelling:", "").strip()

        # Maten samenvatting uit Totaal kolom (bijv. "1 L / 1 2XL")
        totaal_text = str(row.get("Totaal", "")).strip()
        if totaal_text == "nan":
            totaal_text = ""

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
                            "totaal_text": totaal_text,
                            "product_raw": product_raw,
                        })
                except ValueError:
                    pass

    return pd.DataFrame(rows)


# =============================================
#   PRODUCT MATCHING
# =============================================
def match_product_to_template(product_nl: str, gender_nl: str, template_products: List[Dict]) -> Optional[Dict]:
    """Match een Nederlands product naar een template offerte product.

    Matcht ALLEEN tegen offerte_description uit de template offerte,
    NIET tegen de Teamleader productcatalogus.
    """

    # Stap 1: Probeer de mapping tabel voor NL→EN vertaling
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

    # Stap 3: Fuzzy match tegen offerte beschrijvingen
    best_match = None
    best_score = 0.0

    for prod in template_products:
        desc = prod["offerte_description"]

        # Exact match
        if search_term.lower() in desc.lower():
            return prod

        # Fuzzy score
        score = SequenceMatcher(None, search_term.lower(), desc.lower()).ratio()

        # Bonus als basisproductnaam erin zit
        if english_base and english_base.lower() in desc.lower():
            score += 0.3

        # Bonus als geslacht matcht
        if gender_en and gender_en.lower() in desc.lower():
            score += 0.2

        if score > best_score:
            best_score = score
            best_match = prod

    if best_score >= 0.4:
        return best_match

    return None


def get_deal_quotations(deal_id: str) -> List[Dict]:
    """Haal alle offertes op voor een specifieke deal.

    quotations.list heeft geen betrouwbaar deal_id filter,
    daarom halen we de deal info op en gebruiken we de quotations referenties daaruit.
    """
    # Haal deal info op - deze bevat referenties naar gekoppelde offertes
    deal_r = post_json("deals.info", {"id": deal_id})
    if not deal_r.ok:
        return []

    deal_data = deal_r.json().get("data", {})

    # Zoek quotation referenties in de deal
    quotation_refs = deal_data.get("quotations", [])

    if not quotation_refs:
        # Fallback: probeer quotations.list met deal filter
        # en filter handmatig op deal_id
        all_quotations = []
        page = 1
        while page <= 10:
            r = post_json("quotations.list", {"page": {"size": 100, "number": page}})
            if not r.ok:
                break
            batch = r.json().get("data", [])
            if not batch:
                break
            for q in batch:
                if q.get("deal", {}).get("id") == deal_id:
                    all_quotations.append(q)
            if len(batch) < 100:
                break
            page += 1
        return all_quotations

    # Haal info op voor elke gekoppelde offerte
    quotations = []
    for ref in quotation_refs:
        q_id = ref.get("id") if isinstance(ref, dict) else ref
        qr = post_json("quotations.info", {"id": q_id})
        if qr.ok:
            quotations.append(qr.json().get("data", {}))

    return quotations


def get_quotation_products(quotation_id: str) -> List[Dict]:
    """Haal alle line items op uit EEN specifieke offerte.

    Returns een lijst van dicts met alleen de velden die we nodig hebben,
    expliciet uit de offerte gehaald (NIET uit de productcatalogus).
    """
    qr = post_json("quotations.info", {"id": quotation_id})
    if not qr.ok:
        return []

    q_data = qr.json().get("data", {})
    template_products = []

    for group in q_data.get("grouped_lines", []):
        section_title = group.get("section", {}).get("title", "")
        for item in group.get("line_items", []):
            template_products.append({
                "offerte_description": str(item.get("description", "")),
                "offerte_extended_description": str(item.get("extended_description", "") or ""),
                "offerte_unit_price": float(item.get("unit_price", {}).get("amount", 0)),
                "offerte_currency": item.get("unit_price", {}).get("currency", "EUR"),
                "offerte_quantity": item.get("quantity", 0),
                "offerte_section": section_title,
            })

    return template_products


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

# Slim zoeken: probeer eerst de volledige naam, dan losse woorden
company_search = st.text_input("Zoek bedrijf", value=company_name)

companies = []
if company_search:
    # Eerste poging: zoek op volledige naam
    companies = search_companies(st.session_state.access_token, company_search)

    # Tweede poging: zoek op elk woord apart en combineer resultaten
    if not companies:
        words = company_search.strip().split()
        seen_ids = set()
        for word in words:
            if len(word) >= 2:
                partial = search_companies(st.session_state.access_token, word)
                for c in partial:
                    if c["id"] not in seen_ids:
                        companies.append(c)
                        seen_ids.add(c["id"])

if companies:
    company_options = {c["name"]: c["id"] for c in companies}
    selected_company_name = st.selectbox("Selecteer bedrijf", list(company_options.keys()))
    selected_company_id = company_options[selected_company_name]
    st.success(f"Bedrijf: **{selected_company_name}**")
else:
    if company_search:
        st.warning(f"Geen bedrijf gevonden voor '{company_search}'. Probeer een andere zoekterm.")
    st.stop()

# --- STAP 3: TEMPLATE DEAL ---
st.header("3. Template deal selecteren")
st.write("Zoek de deal die als template dient (producten, beschrijvingen en prijzen worden overgenomen).")

# Slim suggereren: probeer TEMPLATE zoektermen op basis van bedrijfsnaam
default_search = ""
if selected_company_name:
    # Probeer korte naam af te leiden (eerste woord, of afkorting)
    parts = selected_company_name.split()
    if len(parts) >= 2:
        default_search = f"{parts[0]} TEMPLATE"
    else:
        default_search = f"{selected_company_name} TEMPLATE"

deal_search = st.text_input("Zoek deal (naam, nummer of zoekterm)", value=default_search)

if deal_search:
    with st.spinner("Deals zoeken..."):
        deals = search_deals(st.session_state.access_token, deal_search)

    # Als geen resultaten, probeer losse woorden
    if not deals:
        words = deal_search.strip().split()
        for word in words:
            if len(word) >= 3:
                deals = search_deals(st.session_state.access_token, word)
                if deals:
                    break

    if deals:
        deal_options = {}
        for d in deals:
            ref = d.get("reference", "")
            label = f"Deal {ref}: {d['title']}" if ref else d["title"]
            deal_options[label] = d["id"]

        selected_deal_label = st.selectbox("Selecteer template deal", list(deal_options.keys()))
        template_deal_id = deal_options[selected_deal_label]

        # Haal offertes op voor deze deal
        with st.spinner("Offertes ophalen voor deze deal..."):
            deal_quotations = get_deal_quotations(template_deal_id)

        if not deal_quotations:
            st.warning("Geen offertes gevonden voor deze deal.")
            st.stop()

        # Laat gebruiker de specifieke offerte kiezen
        q_options = {}
        for q in deal_quotations:
            q_name = q.get("name", "Naamloos")
            q_status = q.get("status", "?")
            q_total = q.get("total", {}).get("tax_exclusive", "?")
            label = f"{q_name} (status: {q_status}, totaal: {q_total} EUR)"
            q_options[label] = q["id"]

        st.write(f"**{len(deal_quotations)} offerte(s) gevonden voor deze deal:**")
        selected_q_label = st.selectbox("Selecteer de template offerte", list(q_options.keys()))
        selected_q_id = q_options[selected_q_label]

        # Haal line items op uit de GESELECTEERDE offerte
        with st.spinner("Producten ophalen uit de geselecteerde offerte..."):
            template_products = get_quotation_products(selected_q_id)

        if template_products:
            st.success(f"{len(template_products)} producten opgehaald uit offerte: **{selected_q_label}**")

            # STAP 3b: Toon de "template database" - expliciet uit de offerte
            st.subheader("Template productdatabase (uit geselecteerde offerte)")
            st.write("Deze producten, beschrijvingen en prijzen komen **uit de hierboven geselecteerde offerte** en worden gebruikt voor matching:")
            template_db_df = pd.DataFrame([{
                "Product (offerte)": p["offerte_description"],
                "Beschrijving": p["offerte_extended_description"][:80] + "..." if len(p["offerte_extended_description"]) > 80 else p["offerte_extended_description"],
                "Prijs (excl BTW)": f"{p['offerte_unit_price']:.2f} EUR",
                "Sectie": p["offerte_section"],
            } for p in template_products])
            st.dataframe(template_db_df, use_container_width=True)

            st.session_state.template_products = template_products
            st.session_state.template_deal_id = template_deal_id
        else:
            st.warning("Geen producten gevonden in deze offerte.")
            st.stop()
    else:
        st.warning("Geen deals gevonden.")
        st.stop()
else:
    st.stop()

if "template_products" not in st.session_state:
    st.stop()

# --- STAP 4: PRODUCT MATCHING ---
st.header("4. Product matching (CSV → Offerte)")
st.write("Hieronder zie je hoe de CSV-producten gematcht worden met producten **uit de template offerte**.")

template_products = st.session_state.template_products
matches = []
unmatched = []

# Unieke producten uit CSV
unique_products = parsed[["product_nl", "gender_nl"]].drop_duplicates()

for _, row in unique_products.iterrows():
    product_nl = row["product_nl"]
    gender_nl = row["gender_nl"]

    match = match_product_to_template(product_nl, gender_nl, template_products)

    display_name = f"{product_nl}"
    if gender_nl:
        display_name += f" ({gender_nl})"

    if match:
        matches.append({
            "csv_product": display_name,
            "product_nl": product_nl,
            "gender_nl": gender_nl,
            "matched_to": match["offerte_description"],
            "unit_price": match["offerte_unit_price"],
            "extended_description": match["offerte_extended_description"],
            "template_product": match,
        })
    else:
        unmatched.append(display_name)

if matches:
    st.write("**Gevonden matches (bron: template offerte):**")
    match_df = pd.DataFrame([{
        "CSV Product": m["csv_product"],
        "Offerte Product": m["matched_to"],
        "Prijs (offerte)": f"{m['unit_price']:.2f} EUR",
    } for m in matches])
    st.dataframe(match_df, use_container_width=True)

    # Handmatige correctie mogelijk maken
    st.write("**Pas matches aan indien nodig:**")
    template_descriptions = ["-- Geen match --"] + [p["offerte_description"] for p in template_products]

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
                matched_prod = next(p for p in template_products if p["offerte_description"] == corrected)
                corrected_matches.append({
                    **m,
                    "matched_to": corrected,
                    "unit_price": matched_prod["offerte_unit_price"],
                    "extended_description": matched_prod["offerte_extended_description"],
                    "template_product": matched_prod,
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

                # Maten samenvatting uit Totaal kolom
                totaal_text = product_rows["totaal_text"].iloc[0] if "totaal_text" in product_rows.columns else ""
                if not totaal_text:
                    totaal_text = " / ".join(
                        f"{r['quantity']} {r['size']}" for _, r in product_rows.iterrows()
                    )
                total_qty = product_rows["quantity"].sum()

                # Maten voor de beschrijving zetten
                description = f"{totaal_text} - {match['matched_to']}" if totaal_text else match["matched_to"]

                line_items.append({
                    "quantity": int(total_qty),
                    "description": str(description),
                    "extended_description": str(match.get("extended_description", "") or ""),
                    "unit_price": {
                        "amount": float(match["unit_price"]),
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
