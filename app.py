import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import re
from typing import Optional, Dict, List

from tl_api import (
    post_json,
    exchange_or_refresh_token,
    teamleader_oauth_url,
    get_current_user_id,
    get_deal_source_id,
    REDIRECT_URI,
    TEAMLEADER_API_BASE,
    TOKENS_FILE,
)

# ============ PAGINA-INSTELLINGEN ============
st.set_page_config(page_title="36 Cycling - Bestelbon Tool", page_icon="🚴", layout="wide")

# ============ CONFIG ============
CORRECT_PASSWORD = st.secrets["auth"]["password"]

# ============ PRODUCT MAPPING ============
# Nederlands → Engels productname mapping (zonder geslacht)
# ============ VERTAALWOORDENBOEK ============
# Alle woorden/frases die in CSV-productnamen kunnen voorkomen
# vertaald naar hoe ze in offerte-beschrijvingen staan.
# Langere frases eerst voor greedy matching.

# Product vertalingen (NL frase → EN zoektermen, meerdere synoniemen mogelijk)
PRODUCT_TRANSLATIONS = [
    # Langere frases eerst (volgorde is belangrijk!)
    ("wielershirt pro", ["cycling jersey pro"]),
    ("wielershirt cadans", ["cycling jersey cadans"]),
    ("wielershirt", ["cycling jersey", "jersey"]),
    ("lange wielerbroek", ["bib tight", "tight"]),
    ("lange broek", ["bib tight", "tight"]),
    ("3/4 wielerbroek", ["3/4 bib tight", "3/4 tight"]),
    ("3/4 broek", ["3/4 bib tight", "3/4 tight"]),
    ("korte broek", ["bib shorts", "shorts"]),
    ("wielerbroek", ["bib shorts", "shorts"]),
    ("broek", ["bib shorts", "shorts"]),
    ("fanshirt", ["fan jersey", "running jersey", "sport jersey", "fanshirt"]),
    ("sportshirt", ["running jersey", "sport jersey", "running"]),
    ("all season jack", ["all season jacket"]),
    ("all season jas", ["all season jacket"]),
    ("windjack zonder mouwen", ["wind vest"]),
    ("windvest", ["wind vest"]),
    ("windjack", ["wind jacket"]),
    ("regenjack", ["rain jacket"]),
    ("regenjas", ["rain jacket"]),
    ("jack", ["jacket"]),
    ("jas", ["jacket"]),
    ("armstukken", ["sleeves", "arm sleeves"]),
    ("beenstukken", ["legs", "leg warmers"]),
    ("overschoenen", ["shoe covers", "overshoes"]),
    ("wielerhandschoen", ["cycling gloves"]),
    ("handschoen", ["gloves"]),
    ("bandana", ["bandana"]),
    ("sokken", ["socks"]),
    ("bidon", ["bottle"]),
    ("musette", ["musette"]),
    ("pet", ["cap"]),
    ("t-shirt", ["t-shirt", "tshirt"]),
    ("polo", ["polo jersey", "polo"]),
    ("vest", ["vest", "gilet"]),
    ("hoodie", ["hoodie"]),
    ("bodywarmer", ["bodywarmer", "body warmer"]),
    ("muts", ["beanie", "hat"]),
    ("sjaal", ["scarf", "neck warmer"]),
]

# Woord-voor-woord vertalingen (voor losse keywords)
WORD_TRANSLATIONS = {
    # Geslacht
    "vrouw": ["ladies", "women", "woman"],
    "dames": ["ladies", "women"],
    "man": ["men", "man"],
    "heren": ["men"],
    "kind": ["kids", "junior"],
    # Product-eigenschappen
    "zonder": ["without"],
    "met": ["with"],
    "lang": ["long"],
    "kort": ["short"],
    "padding": ["zeem", "padding", "chamois"],
    "zeem": ["zeem", "padding", "chamois"],
    "achterzakken": ["pockets", "back pockets"],
    "mouwen": ["sleeves"],
    # Materialen/types die hetzelfde zijn in NL en EN
    "elaspin": ["elaspin"],
    "pro": ["pro"],
    "cadans": ["cadans"],
    "light": ["light"],
    "thermo": ["thermo"],
    "race": ["race"],
    "club": ["club"],
    "aero": ["aero"],
}

# Maat-vertalingen (numerieke prefix → lettercode)
SIZE_TRANSLATIONS = {
    "3XS": "XXXS",
    "2XS": "XXS",
    "2XL": "XXL",
    "3XL": "XXXL",
    "4XL": "XXXXL",
    "5XL": "XXXXXL",
}


def translate_size(size: str) -> str:
    """Vertaal maatcodes: 2XL → XXL, 3XL → XXXL, etc."""
    return SIZE_TRANSLATIONS.get(str(size).upper(), size)


def normalize_text(text: str) -> str:
    """Normaliseer tekst voor matching: lowercase, verwijder leestekens."""
    text = str(text).lower()
    text = re.sub(r"[-/:\\,;]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text




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
if "sidebar_auth" not in st.session_state:
    st.session_state.sidebar_auth = False

st.sidebar.title("Inloggen")
if not st.session_state.sidebar_auth:
    with st.sidebar.form("login_form"):
        password = st.text_input("Wachtwoord", type="password")
        submitted = st.form_submit_button("Inloggen", use_container_width=True)
    if submitted:
        if password == CORRECT_PASSWORD:
            st.session_state.sidebar_auth = True
            st.rerun()
        else:
            st.sidebar.error("Ongeldig wachtwoord.")
    st.stop()

st.sidebar.success("Ingelogd ✓")

# Refresh-token tonen zodat de gebruiker het naar Streamlit secrets kan kopiëren.
# Eenmaal in secrets → app herverbindt automatisch na elke herstart, geen klikken nodig.
with st.sidebar.expander("⚙️ Refresh token (voor secrets)"):
    _rt = st.session_state.get("latest_refresh_token", "")
    if _rt:
        st.code(_rt, language=None)
        st.caption(
            "Plak deze waarde in Streamlit Cloud → Settings → Secrets als "
            "`TEAMLEADER_REFRESH_TOKEN = \"...\"` om herhaaldelijk inloggen te vermijden."
        )
    else:
        st.caption("Refresh-token nog niet beschikbaar in deze sessie.")

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


if not st.session_state.get("connected"):
    auth_url = teamleader_oauth_url()
    st.info("Even verbinden met Teamleader…")
    # Auto-redirect: gebruik window.top zodat het ook werkt als Streamlit in een iframe draait.
    components.html(
        f"""
        <script>
        (window.top || window).location.href = "{auth_url}";
        </script>
        <noscript>
            <a href="{auth_url}" target="_top">Klik hier om handmatig te verbinden</a>
        </noscript>
        """,
        height=0,
    )
    st.stop()




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
    """Parse de bestelbon CSV of Excel naar een gestructureerd DataFrame."""
    filename = uploaded_file.name.lower()
    if filename.endswith((".xlsx", ".xls")):
        df = pd.read_excel(uploaded_file)
    else:
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

        # Verzamel alle delen die NIET bedrijf, geslacht of besteltype zijn → productnaam
        gender = ""
        order_type = ""
        product_parts = []
        for i, part in enumerate(parts):
            if i == company_idx:
                continue
            if part.startswith("Geslacht:"):
                gender = part.replace("Geslacht:", "").strip()
            elif part.startswith("Bestelling:"):
                order_type = part.replace("Bestelling:", "").strip()
            else:
                product_parts.append(part)

        product_name_raw = " - ".join(product_parts) if product_parts else parts[product_idx]

        # Strip *NEW en andere tags
        product_name_clean = re.sub(r"\s*\*\w+", "", product_name_raw).strip()

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
def translate_to_search_terms(product_nl: str, gender_nl: str) -> Dict:
    """Vertaal een Nederlands product naar Engelse zoektermen.

    Returns dict met:
    - product_terms: lijst van Engelse productnaam-varianten
    - gender_terms: lijst van Engelse geslacht-varianten
    - extra_terms: lijst van vertaalde losse woorden
    - original_words: originele woorden die ook letterlijk kunnen matchen
    """
    product_lower = normalize_text(product_nl)

    # Stap 1: Productvertaling via frases (langste match eerst)
    product_terms = []
    matched_phrase = None
    for nl_phrase, en_terms in PRODUCT_TRANSLATIONS:
        if nl_phrase in product_lower:
            product_terms = en_terms
            matched_phrase = nl_phrase
            break

    # Stap 2: Geslacht vertalen
    gender_terms = []
    if gender_nl:
        gender_lower = gender_nl.lower()
        gender_terms = WORD_TRANSLATIONS.get(gender_lower, [gender_lower])

    # Stap 3: Alle losse woorden vertalen (voor extra keywords)
    extra_terms = []
    original_words = []
    for word in product_lower.split():
        word_clean = re.sub(r"\*\w+", "", word).strip()
        if not word_clean or len(word_clean) < 2:
            continue
        # Sla de frase-match over (al verwerkt)
        if matched_phrase and word_clean in matched_phrase:
            continue
        original_words.append(word_clean)
        if word_clean in WORD_TRANSLATIONS:
            extra_terms.extend(WORD_TRANSLATIONS[word_clean])
        else:
            extra_terms.append(word_clean)  # Woord zelf ook meenemen (bijv. "elaspin", "pro")

    return {
        "product_terms": product_terms,
        "gender_terms": gender_terms,
        "extra_terms": extra_terms,
        "original_words": original_words,
    }


def match_product_to_template(
    product_nl: str,
    gender_nl: str,
    template_products: List[Dict],
    debug: bool = False,
):
    """Match een Nederlands product naar een template offerte product.

    Aanpak:
    1. Vertaal productnaam naar Engelse zoektermen
    2. Normaliseer template-beschrijvingen (hyphens → spaties etc.)
    3. Scoor op basis van productterm (verplicht), geslacht (tiebreaker), extra woorden
    4. Hoogste score wint; geslacht voorkomt NIET een match
    """
    terms = translate_to_search_terms(product_nl, gender_nl)
    all_scores = []  # voor debug

    best_match = None
    best_score = 0.0

    for prod in template_products:
        raw_desc = prod["offerte_description"]
        desc = normalize_text(raw_desc)
        score = 0.0

        # Productterm matching (verplicht: 3 punten per match)
        product_matched = False
        for term in terms["product_terms"]:
            if normalize_text(term) in desc:
                score += 3.0
                product_matched = True
                break  # Één productmatch is genoeg

        if not product_matched:
            if debug:
                all_scores.append((raw_desc, 0.0, False))
            continue  # Geen productterm match → sla over

        # Geslacht matching (+0.5 tiebreaker, geen penalty voor mismatch)
        gender_matched = False
        for term in terms["gender_terms"]:
            if normalize_text(term) in desc:
                score += 0.5
                gender_matched = True
                break

        # Lichte voorkeur TEGEN verkeerd geslacht (maar nooit genoeg om te blokkeren)
        if terms["gender_terms"] and not gender_matched:
            other_genders = ["men", "women", "ladies", "kids", "junior"]
            for g in other_genders:
                if g in desc and g not in [normalize_text(t) for t in terms["gender_terms"]]:
                    score -= 0.3
                    break

        # Extra woorden matching (1 punt per woord)
        for term in terms["extra_terms"]:
            if normalize_text(term) in desc:
                score += 1.0

        # Originele woorden die letterlijk voorkomen (0.5 punt per woord)
        for word in terms["original_words"]:
            if len(word) >= 3 and word in desc:
                score += 0.5

        # Penalty: Tight vs shorts verwarring (producttype verwisseling)
        is_tight = any("tight" in t for t in terms["product_terms"])
        is_shorts = any("shorts" in t for t in terms["product_terms"])
        if is_tight and "shorts" in desc and "tight" not in desc:
            score -= 2.0
        if is_shorts and "tight" in desc and "shorts" not in desc:
            score -= 2.0

        if debug:
            all_scores.append((raw_desc, round(score, 2), product_matched))

        if score > best_score:
            best_score = score
            best_match = prod

    # Productterm moet gematcht zijn (gegarandeerd door de continue hierboven)
    result = best_match if (best_match and best_score >= 2.5) else None

    if debug:
        top = sorted(all_scores, key=lambda x: x[1], reverse=True)[:5]
        return result, terms, top
    return result


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
uploaded_file = st.file_uploader("Upload bestelbon (CSV of Excel)", type=["csv", "xlsx", "xls"])

if uploaded_file:
    try:
        parsed = parse_csv(uploaded_file)
        st.session_state.parsed_csv = parsed
        if len(parsed) == 0:
            st.error("Geen producten gevonden in de CSV.")
            st.stop()

        # === VERIFICATIE STAP 1: Brontelling uit Excel ===
        company = parsed["company"].iloc[0] if len(parsed) > 0 else "Onbekend"
        st.session_state.csv_company = company

        total_items_csv = int(parsed["quantity"].sum())
        unique_products_csv = len(parsed[["product_nl", "gender_nl"]].drop_duplicates())
        size_breakdown_csv = parsed.groupby("size")["quantity"].sum().to_dict()

        st.session_state.csv_check = {
            "total_items": total_items_csv,
            "unique_products": unique_products_csv,
            "size_breakdown": size_breakdown_csv,
        }

        st.success(f"**Brontelling Excel:** {total_items_csv} artikelen, {unique_products_csv} unieke producten")
        st.write(f"**Bedrijf:** {company}")

        with st.expander("Details brontelling bekijken"):
            col_check1, col_check2 = st.columns(2)
            with col_check1:
                st.write("**Verdeling per maat (Excel):**")
                size_df = pd.DataFrame([
                    {"Maat": translate_size(str(k)), "Aantal": int(v)}
                    for k, v in sorted(size_breakdown_csv.items(), key=lambda x: -x[1])
                ])
                st.dataframe(size_df, use_container_width=True, hide_index=True)
            with col_check2:
                summary = parsed.groupby(["product_nl", "gender_nl", "order_type"]).agg(
                    total_qty=("quantity", "sum"),
                    sizes=("size", lambda x: ", ".join(f"{s}" for s in x)),
                ).reset_index()
                st.write("**Producten (Excel):**")
                st.dataframe(summary, use_container_width=True, hide_index=True)
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

        # Eerste deal standaard geselecteerd (meest voor de hand liggend)
        deal_keys = list(deal_options.keys())
        selected_deal_labels = st.multiselect(
            "Selecteer template deal(s)",
            deal_keys,
            default=[deal_keys[0]] if deal_keys else [],
        )

        if not selected_deal_labels:
            st.info("Selecteer minimaal één deal.")
            st.stop()

        # Haal offertes op voor alle geselecteerde deals en verzamel producten
        all_template_products = []
        total_quotations = 0

        with st.spinner("Offertes ophalen uit geselecteerde deals..."):
            for deal_label in selected_deal_labels:
                deal_id = deal_options[deal_label]
                deal_quotations = get_deal_quotations(deal_id)

                for q in deal_quotations:
                    q_id = q.get("id")
                    if q_id:
                        products = get_quotation_products(q_id)
                        all_template_products.extend(products)
                        total_quotations += 1

        # Dedupliceer op basis van offerte_description (behoud eerste voorkomen)
        seen_descriptions = set()
        template_products = []
        for p in all_template_products:
            desc = p["offerte_description"]
            if desc not in seen_descriptions:
                seen_descriptions.add(desc)
                template_products.append(p)

        if template_products:
            st.success(f"{len(template_products)} unieke producten opgehaald uit {total_quotations} offerte(s)")

            st.subheader("Producten uit geselecteerde offertes")
            st.write("Deze producten, beschrijvingen en prijzen worden gebruikt voor matching:")
            template_db_df = pd.DataFrame([{
                "Product (offerte)": p["offerte_description"],
                "Beschrijving": p["offerte_extended_description"][:80] + "..." if len(p["offerte_extended_description"]) > 80 else p["offerte_extended_description"],
                "Prijs (excl BTW)": f"{p['offerte_unit_price']:.2f} EUR",
                "Sectie": p["offerte_section"],
            } for p in template_products])
            st.dataframe(template_db_df, use_container_width=True)

            st.session_state.template_products = template_products
        else:
            st.warning("Geen producten gevonden in de offertes van de geselecteerde deals.")
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

# Cache-sleutel: combinatie van CSV-inhoud en template-producten
# Zo hoeven matches alleen opnieuw berekend te worden bij nieuwe data, niet bij elke klik
_csv_cache_key = str(parsed[["product_nl", "gender_nl"]].drop_duplicates().values.tolist())
_tpl_cache_key = str([p["offerte_description"] for p in template_products])
_match_cache_key = (_csv_cache_key, _tpl_cache_key)

if st.session_state.get("match_cache_key") != _match_cache_key:
    # Alleen herberekenen als CSV of template is gewijzigd
    all_csv_products = []
    unique_products = parsed[["product_nl", "gender_nl"]].drop_duplicates()

    for _, row in unique_products.iterrows():
        product_nl = row["product_nl"]
        gender_nl = row["gender_nl"]

        match, match_terms, match_scores = match_product_to_template(
            product_nl, gender_nl, template_products, debug=True
        )

        display_name = f"{product_nl}"
        if gender_nl:
            display_name += f" ({gender_nl})"

        all_csv_products.append({
            "csv_product": display_name,
            "product_nl": product_nl,
            "gender_nl": gender_nl,
            "matched_to": match["offerte_description"] if match else None,
            "unit_price": match["offerte_unit_price"] if match else 0,
            "extended_description": match["offerte_extended_description"] if match else "",
            "template_product": match,
            "_debug_terms": match_terms,
            "_debug_scores": match_scores,
        })

    st.session_state.all_csv_products = all_csv_products
    st.session_state.match_cache_key = _match_cache_key

all_csv_products = st.session_state.all_csv_products

# Template-versienummer: elke keer dat het template verandert krijgen widgets nieuwe keys,
# zodat Streamlit geen oude waarden hergebruikt.
if st.session_state.get("_last_tpl_key") != _tpl_cache_key:
    st.session_state._tpl_version = st.session_state.get("_tpl_version", 0) + 1
    st.session_state._last_tpl_key = _tpl_cache_key
_tv = st.session_state._tpl_version  # kort alias voor widget-keys

# Standaard sorteervolgorde op producttype
def _product_sort_priority(product_nl: str) -> int:
    """Geeft een prioriteitsgetal op basis van de Nederlandse productnaam.
    Lager = eerder in de lijst. Langere frases eerst checken."""
    p = product_nl.lower()
    # Lange broek vóór wielerbroek zodat 'wielerbroek' niet vroegtijdig matcht
    if any(x in p for x in ("lange wielerbroek", "lange broek", "3/4 wielerbroek", "3/4 broek")):
        return 5
    if "wielershirt" in p:
        return 1
    if "wielerbroek" in p or "korte broek" in p:
        return 2
    if "all season" in p:
        return 3
    if any(x in p for x in ("windjack", "windvest", "wind jack")):
        return 4
    # 5 = lange broek (zie boven)
    if any(x in p for x in ("sportshirt", "fanshirt", "running shirt")):
        return 6
    if any(x in p for x in ("regenjack", "regenjas", "running jack")):
        return 7
    if "polo" in p:
        return 8
    return 9  # overige producten

# Volgorde: reset bij nieuw template of nieuwe CSV
_product_keys = [(m["product_nl"], m["gender_nl"]) for m in all_csv_products]
_order_key = f"product_order_{_tv}"
if st.session_state.get(f"product_order_keys_{_tv}") != _product_keys:
    # Sorteer op prioriteit, dan op originele volgorde (stabiel) als tiebreaker
    sorted_indices = sorted(
        range(len(all_csv_products)),
        key=lambda i: _product_sort_priority(all_csv_products[i]["product_nl"]),
    )
    st.session_state[_order_key] = sorted_indices
    st.session_state[f"product_order_keys_{_tv}"] = _product_keys

current_order = st.session_state[_order_key]

# ── Volgorde-popup (st.dialog = fragment: herlaadt alleen de popup, niet de hele pagina) ──
@st.dialog("Volgorde aanpassen", width="small")
def _volgorde_dialog(okey):
    """Popup met pijltjes die alleen zichzelf opnieuw rendert bij elke klik."""
    products = st.session_state.get("all_csv_products", [])
    working = st.session_state.get("_dlg_order", [])
    n = len(working)

    def _up(p):
        o = st.session_state._dlg_order
        o[p], o[p - 1] = o[p - 1], o[p]

    def _down(p):
        o = st.session_state._dlg_order
        o[p], o[p + 1] = o[p + 1], o[p]

    for pos in range(n):
        prod_idx = working[pos]
        if prod_idx >= len(products):
            continue
        m = products[prod_idx]
        c1, c2, c3 = st.columns([0.1, 0.1, 1])
        with c1:
            st.button("↑", key=f"dlg_up_{pos}", disabled=(pos == 0),
                      on_click=_up, args=(pos,))
        with c2:
            st.button("↓", key=f"dlg_down_{pos}", disabled=(pos == n - 1),
                      on_click=_down, args=(pos,))
        with c3:
            if m.get("matched_to"):
                st.write(m["csv_product"])
            else:
                st.markdown(f":red[{m['csv_product']}]")

    st.divider()
    c_ok, c_cancel = st.columns(2)
    with c_ok:
        if st.button("✅ Bevestigen", type="primary", use_container_width=True):
            st.session_state[okey] = st.session_state._dlg_order.copy()
            st.rerun()
    with c_cancel:
        if st.button("Annuleren", use_container_width=True):
            st.rerun()

# Knop om de popup te openen
if st.button("🔀 Volgorde aanpassen"):
    st.session_state._dlg_order = current_order.copy()
    _volgorde_dialog(_order_key)

# Alle producten tonen (naam + dropdown, zonder pijltjes op de hoofdpagina)
st.write("**Controleer de matches (rood = geen match, kies handmatig via de dropdown):**")
template_descriptions = ["-- Geen match --"] + [p["offerte_description"] for p in template_products]

corrected_matches = []
for display_pos, prod_idx in enumerate(current_order):
    m = all_csv_products[prod_idx]

    col_name, col_match = st.columns([1.5, 2])

    with col_name:
        if m["matched_to"]:
            st.write(f"**{display_pos + 1}. {m['csv_product']}**")
        else:
            st.write(f":red[**{display_pos + 1}. {m['csv_product']}**]")
            with st.expander("Waarom geen match?"):
                terms = m.get("_debug_terms", {})
                scores = m.get("_debug_scores", [])
                st.write(
                    f"🔍 Zoektermen: `{terms.get('product_terms', [])}` | "
                    f"geslacht: `{terms.get('gender_terms', [])}` | "
                    f"extra: `{terms.get('extra_terms', [])[:5]}`"
                )
                if scores:
                    st.write("**Top kandidaten uit template:**")
                    for name, score, *_ in scores:
                        st.write(f"- {name} → score {score}")
                else:
                    st.write("_Geen enkel template-product scoort op productterm._")
                    st.write("Voeg handmatig een match toe via de dropdown →")

    with col_match:
        if m["matched_to"] and m["matched_to"] in template_descriptions:
            default_idx = template_descriptions.index(m["matched_to"])
        else:
            default_idx = 0

        corrected = st.selectbox(
            f"Match voor {m['csv_product']}",
            template_descriptions,
            index=default_idx,
            key=f"match_{_tv}_{m['product_nl']}_{m['gender_nl']}",
            label_visibility="collapsed",
        )
        if corrected != "-- Geen match --":
            matched_prod = next(
                (p for p in template_products if p["offerte_description"] == corrected),
                None,
            )
            if matched_prod:
                corrected_matches.append({
                    **m,
                    "matched_to": corrected,
                    "unit_price": matched_prod["offerte_unit_price"],
                    "extended_description": matched_prod["offerte_extended_description"],
                    "template_product": matched_prod,
                    "_order": display_pos,
                })

# Sorteer op weergavepositie
corrected_matches.sort(key=lambda x: x.get("_order", 999))
st.session_state.final_matches = corrected_matches

if not corrected_matches:
    st.warning("Geen producten gematcht. Selecteer matches in de dropdowns hierboven.")
    st.stop()

# === VERIFICATIE STAP 2: Telling na matching ===
csv_check = st.session_state.get("csv_check", {})
matched_product_keys = set((m["product_nl"], m["gender_nl"]) for m in corrected_matches)
matched_rows = parsed[parsed.apply(lambda r: (r["product_nl"], r["gender_nl"]) in matched_product_keys, axis=1)]
total_items_matched = int(matched_rows["quantity"].sum())
unique_products_matched = len(matched_product_keys)
size_breakdown_matched = matched_rows.groupby("size")["quantity"].sum().to_dict()

# Bereken verwacht totaalbedrag
expected_total = 0.0
for m in corrected_matches:
    prod_rows = parsed[(parsed["product_nl"] == m["product_nl"]) & (parsed["gender_nl"] == m["gender_nl"])]
    qty = int(prod_rows["quantity"].sum())
    expected_total += qty * float(m["unit_price"])

items_ok = total_items_matched == csv_check.get("total_items", 0)
products_ok = unique_products_matched == csv_check.get("unique_products", 0)

st.header("Verificatie na matching")
col_v1, col_v2, col_v3 = st.columns(3)
with col_v1:
    icon = "+" if items_ok else "!"
    st.metric("Artikelen", f"{total_items_matched} / {csv_check.get('total_items', '?')}",
              delta="OK" if items_ok else f"{total_items_matched - csv_check.get('total_items', 0)} verschil",
              delta_color="normal" if items_ok else "inverse")
with col_v2:
    st.metric("Unieke producten", f"{unique_products_matched} / {csv_check.get('unique_products', '?')}",
              delta="OK" if products_ok else f"{unique_products_matched - csv_check.get('unique_products', 0)} verschil",
              delta_color="normal" if products_ok else "inverse")
with col_v3:
    st.metric("Verwacht totaalbedrag", f"{expected_total:.2f} EUR")

# Toon maat-verdeling vergelijking
if not items_ok or not products_ok:
    st.warning("De telling komt niet overeen. Controleer de matches hierboven — producten zonder match worden overgeslagen.")
    with st.expander("Maat-verdeling vergelijking"):
        all_sizes = sorted(set(list(csv_check.get("size_breakdown", {}).keys()) + list(size_breakdown_matched.keys())))
        comparison = pd.DataFrame([{
            "Maat": translate_size(str(s)),
            "Excel": int(csv_check.get("size_breakdown", {}).get(s, 0)),
            "Na matching": int(size_breakdown_matched.get(s, 0)),
            "Verschil": int(size_breakdown_matched.get(s, 0)) - int(csv_check.get("size_breakdown", {}).get(s, 0)),
        } for s in all_sizes])
        st.dataframe(comparison, use_container_width=True, hide_index=True)
else:
    st.success("Alle artikelen en producten zijn gematcht!")

# --- STAP 5: DEAL + OFFERTE AANMAKEN ---
st.header("5. Deal + Offerte aanmaken")

new_deal_title = st.text_input("Deal titel", value=f"{company_name} - Bestelling 2025")

if st.button("Maak deal + offerte aan"):
    final_matches = st.session_state.final_matches

    with st.spinner("Deal aanmaken..."):
        # Verantwoordelijke = huidige ingelogde Teamleader-gebruiker
        # Herkomst (source) = "Al eens besteld"
        user_id   = get_current_user_id()
        source_id = get_deal_source_id("Al eens besteld")

        deal_payload = {
            "title": new_deal_title,
            "lead": {"customer": {"type": "company", "id": selected_company_id}},
        }
        if user_id:
            deal_payload["responsible_user_id"] = user_id
        if source_id:
            deal_payload["source_id"] = source_id

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

        # Bouw grouped_lines: per product een sectie met subtitel (maten)
        # Volgorde komt uit de gesorteerde final_matches (stap 4)
        grouped_lines = []

        for match in final_matches:
            if not match or match.get("matched_to") == "-- Geen match --":
                continue

            product_nl = match["product_nl"]
            gender_nl = match["gender_nl"]
            product_rows = parsed[(parsed["product_nl"] == product_nl) & (parsed["gender_nl"] == gender_nl)]

            if product_rows.empty:
                continue

            # Maten samenvatting uit Totaal kolom (met maatvertaling)
            totaal_text = product_rows["totaal_text"].iloc[0] if "totaal_text" in product_rows.columns else ""
            if totaal_text:
                # Vertaal maatcodes in de totaal_text (bijv. "2 2XL" → "2 XXL")
                def _translate_size_in_text(t):
                    parts = t.split()
                    return " ".join(translate_size(p) if not p.isdigit() else p for p in parts)
                totaal_text = " / ".join(
                    _translate_size_in_text(chunk) for chunk in totaal_text.split(" / ")
                )
            if not totaal_text:
                totaal_text = " / ".join(
                    f"{r['quantity']} {translate_size(r['size'])}" for _, r in product_rows.iterrows()
                )
            total_qty = product_rows["quantity"].sum()

            # Besteltype(s) samenvatten
            order_types_str = ", ".join(sorted(product_rows["order_type"].unique()))

            # Subtitel met maten
            subtitle_text = totaal_text if totaal_text else f"{int(total_qty)} stuks"
            if order_types_str:
                subtitle_text = f"{subtitle_text} ({order_types_str})"

            # Elke product als eigen sectie met subtitel
            grouped_lines.append({
                "section": {
                    "title": str(subtitle_text),
                },
                "line_items": [{
                    "quantity": int(total_qty),
                    "description": str(match["matched_to"]),
                    "extended_description": str(match.get("extended_description", "") or ""),
                    "unit_price": {
                        "amount": float(match["unit_price"]),
                        "tax": "excluding",
                    },
                    "tax_rate_id": vat_id,
                }],
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

            # === VERIFICATIE STAP 3: Offerte ophalen en vergelijken ===
            st.header("Eindverificatie")
            with st.spinner("Aangemaakte offerte ophalen ter controle..."):
                verify_r = post_json("quotations.info", {"id": qid})

            if verify_r.ok:
                verify_data = verify_r.json().get("data", {})
                raw_total = verify_data.get("total", {})
                if isinstance(raw_total, dict):
                    tax_excl = raw_total.get("tax_exclusive", 0)
                    if isinstance(tax_excl, dict):
                        offerte_total = float(tax_excl.get("amount", 0))
                    else:
                        offerte_total = float(tax_excl or 0)
                else:
                    offerte_total = float(raw_total or 0)

                offerte_lines = []
                for group in verify_data.get("grouped_lines", []):
                    for item in group.get("line_items", []):
                        raw_up = item.get("unit_price", {})
                        up_amount = float(raw_up.get("amount", 0)) if isinstance(raw_up, dict) else float(raw_up or 0)
                        raw_lt = item.get("total", {})
                        if isinstance(raw_lt, dict):
                            lt_val = raw_lt.get("tax_exclusive", 0)
                            lt_amount = float(lt_val.get("amount", 0)) if isinstance(lt_val, dict) else float(lt_val or 0)
                        else:
                            lt_amount = float(raw_lt or 0)
                        offerte_lines.append({
                            "description": item.get("description", ""),
                            "quantity": item.get("quantity", 0),
                            "unit_price": up_amount,
                            "line_total": lt_amount,
                        })

                offerte_items_total = sum(l["quantity"] for l in offerte_lines)
                offerte_products_total = len(offerte_lines)

                csv_check = st.session_state.get("csv_check", {})
                items_match = offerte_items_total == csv_check.get("total_items", 0)
                amount_match = abs(offerte_total - expected_total) < 0.01

                col_e1, col_e2, col_e3 = st.columns(3)
                with col_e1:
                    st.metric("Artikelen offerte", offerte_items_total,
                              delta="OK" if items_match else f"Excel: {csv_check.get('total_items', '?')}",
                              delta_color="normal" if items_match else "inverse")
                with col_e2:
                    st.metric("Producten offerte", offerte_products_total)
                with col_e3:
                    st.metric("Totaalbedrag offerte", f"{offerte_total:.2f} EUR",
                              delta="OK" if amount_match else f"Verwacht: {expected_total:.2f}",
                              delta_color="normal" if amount_match else "inverse")

                if items_match and amount_match:
                    st.success("Alles klopt! De offerte komt exact overeen met de Excel.")
                    st.balloons()
                else:
                    st.warning("Er zijn afwijkingen gevonden. Controleer de offerte in Teamleader.")
                    with st.expander("Offerte line items"):
                        st.dataframe(pd.DataFrame(offerte_lines), use_container_width=True, hide_index=True)
            else:
                st.warning("Kon de aangemaakte offerte niet ophalen ter verificatie.")
                st.balloons()
        else:
            st.error(f"Fout bij aanmaken offerte: {qr.text}")
