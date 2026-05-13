"""Chat-interface voor het aanmaken van offertes via natuurlijke taal."""

import streamlit as st
import anthropic
import json
from typing import Optional

from tl_api import post_json, exchange_or_refresh_token, teamleader_oauth_url

st.set_page_config(page_title="36 Cycling – Chat Offerte", page_icon="💬", layout="wide")

# =============================================
#   WACHTWOORD
# =============================================
CORRECT_PASSWORD  = st.secrets["auth"]["password"]
ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    pwd = st.text_input("Wachtwoord", type="password")
    if pwd == CORRECT_PASSWORD:
        st.session_state.authenticated = True
        st.rerun()
    elif pwd:
        st.error("Onjuist wachtwoord")
    st.stop()

# =============================================
#   TEAMLEADER VERBINDING
#   Deelt de token met de Bestelbon-pagina via session_state
# =============================================
if "access_token" not in st.session_state:
    token = exchange_or_refresh_token()
    if token:
        st.session_state.access_token = token
        st.session_state.connected = True
    else:
        st.session_state.connected = False

if not st.session_state.get("connected"):
    auth_url = teamleader_oauth_url()
    st.markdown(
        f"""
        <div style="padding:12px; background:#fff7cc; border-left:4px solid #ffa500; border-radius:5px;">
        <b>Teamleader moet autoriseren.</b><br><br>
        Ga naar de <a href="/" target="_self"><b>Bestelbon-pagina</b></a> om in te loggen,
        of <a href="{auth_url}" target="_blank"><b>klik hier om direct te verbinden</b></a>.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

if not ANTHROPIC_API_KEY:
    st.error("**ANTHROPIC_API_KEY** ontbreekt in `.streamlit/secrets.toml`. Voeg toe: `ANTHROPIC_API_KEY = \"sk-ant-...\"`")
    st.stop()

# =============================================
#   TEAMLEADER TOOL-FUNCTIES
#   Gebruikt post_json uit tl_api (zelfde token als de Bestelbon-pagina)
# =============================================
def _tool_zoek_bedrijf(naam: str) -> dict:
    r = post_json("companies.list", {
        "filter": {"term": naam},
        "page": {"size": 5, "number": 1},
    })
    if not r.ok:
        return {"error": r.text[:200]}
    items = r.json().get("data", [])
    return {
        "bedrijven": [
            {
                "id": c["id"],
                "naam": c.get("name", ""),
                "stad": (c.get("primary_address") or {}).get("city", ""),
            }
            for c in items
        ]
    }


def _tool_zoek_deals(bedrijf_id: str, bedrijf_naam: str) -> dict:
    """Haal de laatste 5 deals op voor een specifiek bedrijf.

    deals.list heeft geen company_id filter, dus we zoeken op bedrijfsnaam
    als term en filteren daarna client-side op het exacte bedrijfs-ID.
    """
    r = post_json("deals.list", {
        "filter": {"term": bedrijf_naam},
        "page": {"size": 50, "number": 1},
        "sort": [{"field": "created_at", "order": "desc"}],
    })
    if not r.ok:
        return {"error": r.text[:200]}

    alle_deals = r.json().get("data", [])

    # Filter client-side op het exacte bedrijfs-ID via lead.customer.id
    bedrijf_deals = [
        d for d in alle_deals
        if d.get("lead", {}).get("customer", {}).get("id") == bedrijf_id
    ]

    if not bedrijf_deals:
        return {"error": f"Geen deals gevonden voor bedrijf '{bedrijf_naam}' (ID: {bedrijf_id})."}

    return {
        "deals": [
            {"id": d["id"], "titel": d.get("title", ""), "status": d.get("status", "")}
            for d in bedrijf_deals[:5]
        ]
    }


def _haal_producten_uit_deal(deal_id: str, seen: set) -> list:
    """Helper: haal unieke producten uit de offerte(s) van één deal."""
    r = post_json("deals.info", {"id": deal_id})
    if not r.ok:
        return []
    q_refs = r.json().get("data", {}).get("quotations", [])
    producten = []
    for ref in q_refs:
        qr = post_json("quotations.info", {"id": ref["id"]})
        if not qr.ok:
            continue
        for group in qr.json().get("data", {}).get("grouped_lines", []):
            for item in group.get("line_items", []):
                desc = str(item.get("description", ""))
                if not desc or desc in seen:
                    continue
                seen.add(desc)
                price_raw = item.get("unit_price", {})
                price = (
                    float(price_raw.get("amount", 0))
                    if isinstance(price_raw, dict)
                    else float(price_raw or 0)
                )
                producten.append({
                    "beschrijving": desc,
                    "prijs": price,
                    "extended_description": str(item.get("extended_description", "") or ""),
                })
    return producten


def _tool_haal_recente_producten(deal_ids: list) -> dict:
    """Haal alle unieke producten op uit meerdere deals tegelijk."""
    seen: set = set()
    producten = []
    for deal_id in deal_ids:
        producten.extend(_haal_producten_uit_deal(deal_id, seen))
    if not producten:
        return {"error": "Geen producten gevonden in de opgegeven deals."}
    return {"producten": producten, "aantal_deals": len(deal_ids)}


def _tool_maak_deal_en_offerte(
    bedrijf_id: str,
    bedrijf_naam: str,
    producten: list,
    deal_titel: Optional[str] = None,
) -> dict:
    titel = deal_titel or f"{bedrijf_naam} – Chat bestelling"

    # Deal aanmaken
    r = post_json("deals.create", {
        "title": titel,
        "lead": {"customer": {"type": "company", "id": bedrijf_id}},
        "source": {"type": "api"},
    })
    if not r.ok:
        return {"error": f"Deal aanmaken mislukt: {r.text[:300]}"}
    deal_id = r.json().get("data", {}).get("id")
    if not deal_id:
        return {"error": "Deal ID niet ontvangen."}

    # BTW tarief ophalen (21%)
    vat_id = None
    vr = post_json("taxRates.list", {})
    if vr.ok:
        for t in vr.json().get("data", []):
            if abs(t.get("rate", 0) - 0.21) < 0.001:
                vat_id = t["id"]
                break

    # Grouped lines bouwen
    grouped_lines = []
    totaal_bedrag = 0.0
    for prod in producten:
        maten: dict = prod.get("maten", {})
        totaal_qty = sum(int(v) for v in maten.values())
        maten_tekst = " / ".join(f"{v} {k}" for k, v in maten.items())
        prijs = float(prod.get("prijs", 0))
        totaal_bedrag += totaal_qty * prijs

        line_item = {
            "quantity": int(totaal_qty),
            "description": prod["beschrijving"],
            "extended_description": prod.get("extended_description", ""),
            "unit_price": {"amount": prijs, "tax": "excluding"},
        }
        if vat_id:
            line_item["tax_rate_id"] = vat_id

        grouped_lines.append({
            "section": {"title": maten_tekst},
            "line_items": [line_item],
        })

    if not grouped_lines:
        return {"error": "Geen producten om toe te voegen."}

    # Offerte aanmaken
    qr = post_json("quotations.create", {
        "deal_id": deal_id,
        "title": titel,
        "text": f"Offerte voor {bedrijf_naam}",
        "currency": {"code": "EUR", "exchange_rate": 1.0},
        "grouped_lines": grouped_lines,
    })
    if not qr.ok:
        return {"error": f"Offerte aanmaken mislukt: {qr.text[:300]}"}

    q_data = qr.json().get("data", {})
    return {
        "succes": True,
        "deal_id": deal_id,
        "deal_titel": titel,
        "offerte_id": q_data.get("id", ""),
        "totaal_excl_btw": round(totaal_bedrag, 2),
        "link": f"https://app.teamleader.eu/deals/{deal_id}",
    }


def execute_tool(name: str, inputs: dict) -> dict:
    if name == "zoek_bedrijf":
        return _tool_zoek_bedrijf(inputs["naam"])
    if name == "zoek_deals":
        return _tool_zoek_deals(inputs["bedrijf_id"], inputs["bedrijf_naam"])
    if name == "haal_recente_producten":
        return _tool_haal_recente_producten(inputs["deal_ids"])
    if name == "maak_deal_en_offerte":
        return _tool_maak_deal_en_offerte(
            inputs["bedrijf_id"],
            inputs["bedrijf_naam"],
            inputs["producten"],
            inputs.get("deal_titel"),
        )
    return {"error": f"Onbekende tool: {name}"}


# =============================================
#   TOOLS SCHEMA (voor Claude API)
# =============================================
TOOLS = [
    {
        "name": "zoek_bedrijf",
        "description": "Zoek een bedrijf op naam in Teamleader Focus.",
        "input_schema": {
            "type": "object",
            "properties": {
                "naam": {"type": "string", "description": "Naam (of deel) van het bedrijf"},
            },
            "required": ["naam"],
        },
    },
    {
        "name": "zoek_deals",
        "description": "Haal de laatste 5 deals op voor een specifiek bedrijf (gesorteerd op datum, nieuwste eerst).",
        "input_schema": {
            "type": "object",
            "properties": {
                "bedrijf_id":   {"type": "string", "description": "Teamleader company ID"},
                "bedrijf_naam": {"type": "string", "description": "Naam van het bedrijf (voor zoekfilter)"},
            },
            "required": ["bedrijf_id", "bedrijf_naam"],
        },
    },
    {
        "name": "haal_recente_producten",
        "description": (
            "Haal alle unieke producten (met prijs en beschrijving) op uit meerdere deals tegelijk. "
            "Geef alle deal-IDs van de laatste bestellingen mee om het volledige productaanbod te zien."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lijst van deal-IDs waaruit producten opgehaald worden",
                },
            },
            "required": ["deal_ids"],
        },
    },
    {
        "name": "maak_deal_en_offerte",
        "description": (
            "Maak een nieuwe deal + offerte aan in Teamleader. "
            "Gebruik de exacte productnamen en prijzen uit de template offerte."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bedrijf_id":   {"type": "string"},
                "bedrijf_naam": {"type": "string"},
                "deal_titel":   {"type": "string", "description": "Optionele titel voor de deal"},
                "producten": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "beschrijving":         {"type": "string",  "description": "Exacte productnaam uit de template"},
                            "prijs":                {"type": "number",  "description": "Eenheidsprijs excl. BTW"},
                            "extended_description": {"type": "string",  "description": "Uitgebreide beschrijving (mag leeg)"},
                            "maten": {
                                "type": "object",
                                "description": "Maat → aantal, bijv. {\"L\": 2, \"XL\": 1}",
                                "additionalProperties": {"type": "integer"},
                            },
                        },
                        "required": ["beschrijving", "prijs", "maten"],
                    },
                },
            },
            "required": ["bedrijf_id", "bedrijf_naam", "producten"],
        },
    },
]

# =============================================
#   SYSTEEM PROMPT
# =============================================
SYSTEM_PROMPT = """\
Je bent een vlotte assistent voor 36 Cycling die Teamleader-offertes aanmaakt.
Je spreekt altijd Nederlands. Wees bondig – geen onnodige uitleg.

Werkwijze bij een offerte-verzoek:
1. Zoek het bedrijf op (zoek_bedrijf). Bij meerdere treffers: vraag welke.
2. Haal de laatste 5 deals op (zoek_deals).
3. Haal alle producten op uit ALLE 5 deals tegelijk (haal_recente_producten met alle deal-IDs).
   Dit geeft het volledige productaanbod van de laatste bestellingen voor dit bedrijf.
4. Match de gevraagde producten aan de beschikbare producten (gebruik onderstaande vertaalregels).
5. Toon een beknopt overzicht (product | maten | prijs) en vraag bevestiging.
6. Na "ja" of bevestiging: maak de deal en offerte aan (maak_deal_en_offerte).

Productvertaling (NL → zoek in EN template-naam):
wielershirt → cycling jersey  |  wielerbroek/korte broek → bib shorts
lange wielerbroek/lange broek → bib tight  |  3/4 broek → 3/4 bib tight
windjack → wind jacket  |  windvest → wind vest
all season jack/jas → all season jacket  |  regenjack/regenjas → rain jacket
sportshirt/fanshirt → running jersey  |  polo → polo jersey
armstukken → sleeves  |  beenstukken → legs  |  sokken → socks
Geslacht: man/heren → men  |  vrouw/dames → ladies
Maten: 2XL → XXL  |  3XL → XXXL  |  4XL → XXXXL

Geef productnaam, prijs en extended_description EXACT over uit de template.
Bij onbekend product: zeg dat en vraag om verduidelijking.
"""

# =============================================
#   CLAUDE AGENT LOOP
# =============================================
def run_agent(messages: list) -> tuple[str, list]:
    """Voer de agentic tool-loop uit. Geeft (antwoord_tekst, bijgewerkte_messages) terug."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            messages = messages + [{"role": "assistant", "content": response.content}]
            return text, messages

        if response.stop_reason == "tool_use":
            messages = messages + [{"role": "assistant", "content": response.content}]
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })
            messages = messages + [{"role": "user", "content": tool_results}]

        else:
            return "Er is iets misgegaan. Probeer opnieuw.", messages


# =============================================
#   STREAMLIT UI
# =============================================
st.title("💬 Offerte aanmaken via chat")
st.caption("Bijv: _maak offerte voor Exofex – wielershirt heren L en XL, wielerbroek dames M_")

if "chat_ui" not in st.session_state:
    st.session_state.chat_ui = []
if "chat_api" not in st.session_state:
    st.session_state.chat_api = []

for msg in st.session_state.chat_ui:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Maak een offerte aan…"):
    st.session_state.chat_ui.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    st.session_state.chat_api.append({"role": "user", "content": prompt})

    with st.chat_message("assistant"):
        with st.spinner("Bezig…"):
            response_text, updated_api_msgs = run_agent(st.session_state.chat_api)
        st.markdown(response_text)

    st.session_state.chat_ui.append({"role": "assistant", "content": response_text})
    st.session_state.chat_api = updated_api_msgs

if st.session_state.chat_ui:
    st.divider()
    if st.button("🗑️ Nieuw gesprek"):
        st.session_state.chat_ui = []
        st.session_state.chat_api = []
        st.rerun()
