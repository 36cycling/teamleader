"""Chat-interface voor het aanmaken van offertes via natuurlijke taal."""

import streamlit as st
import anthropic
import json
import os
import requests
import datetime
from typing import Optional, List, Dict

st.set_page_config(page_title="36 Cycling – Chat Offerte", page_icon="💬", layout="wide")

# =============================================
#   CONFIG
# =============================================
CORRECT_PASSWORD  = st.secrets["auth"]["password"]
CLIENT_ID         = st.secrets["CLIENT_ID"]
CLIENT_SECRET     = st.secrets["CLIENT_SECRET"]
REDIRECT_URI      = "https://www.kwatta.com/teamleader_redirect.html"
TL_AUTH_URL       = "https://focus.teamleader.eu/oauth2/access_token"
TL_API_BASE       = "https://api.focus.teamleader.eu"
TOKENS_FILE       = "teamleader_tokens.json"
ANTHROPIC_API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "")

# =============================================
#   WACHTWOORD
# =============================================
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
#   TOKEN MANAGEMENT
# =============================================
def _load_tokens():
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE) as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_tokens(access_token: str, refresh_token: str):
    with open(TOKENS_FILE, "w") as f:
        json.dump({"access_token": access_token, "refresh_token": refresh_token}, f)


def _get_valid_token() -> Optional[str]:
    tokens = _load_tokens()
    if not tokens:
        return None
    r = requests.post(TL_AUTH_URL, data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    })
    if r.ok:
        new = r.json()
        _save_tokens(new["access_token"], new["refresh_token"])
        return new["access_token"]
    return tokens.get("access_token")


def _tl(endpoint: str, payload: dict, token: str) -> requests.Response:
    return requests.post(
        f"{TL_API_BASE}/{endpoint}",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


# =============================================
#   TOKEN CHECK
# =============================================
_token = _get_valid_token()
if not _token:
    st.warning("Geen Teamleader-verbinding. Ga eerst naar de **Bestelbon**-pagina om in te loggen.")
    st.stop()

if not ANTHROPIC_API_KEY:
    st.error("**ANTHROPIC_API_KEY** ontbreekt in `.streamlit/secrets.toml`. Voeg toe: `ANTHROPIC_API_KEY = \"sk-ant-...\"`")
    st.stop()

# =============================================
#   TEAMLEADER HELPER FUNCTIES (tools)
# =============================================
def _tool_zoek_bedrijf(naam: str) -> dict:
    r = _tl("companies.list", {
        "filter": {"term": naam},
        "page": {"size": 5, "number": 1},
    }, _token)
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


def _tool_zoek_deals(bedrijf_id: str) -> dict:
    r = _tl("deals.list", {
        "filter": {"company_id": bedrijf_id},
        "page": {"size": 20, "number": 1},
        "sort": [{"field": "created_at", "order": "desc"}],
    }, _token)
    if not r.ok:
        return {"error": r.text[:200]}
    items = r.json().get("data", [])
    return {
        "deals": [
            {"id": d["id"], "titel": d.get("title", ""), "status": d.get("status", "")}
            for d in items
        ]
    }


def _tool_haal_offerte_producten(deal_id: str) -> dict:
    # Haal deal op voor offerte-referenties
    r = _tl("deals.info", {"id": deal_id}, _token)
    if not r.ok:
        return {"error": r.text[:200]}
    deal_data = r.json().get("data", {})
    q_refs = deal_data.get("quotations", [])
    if not q_refs:
        return {"error": "Geen offertes gevonden voor deze deal."}

    producten = []
    seen = set()
    for ref in q_refs:
        qr = _tl("quotations.info", {"id": ref["id"]}, _token)
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

    return {"producten": producten}


def _tool_maak_deal_en_offerte(
    bedrijf_id: str,
    bedrijf_naam: str,
    producten: list,
    deal_titel: Optional[str] = None,
) -> dict:
    titel = deal_titel or f"{bedrijf_naam} – Chat bestelling"

    # Deal aanmaken
    r = _tl("deals.create", {
        "title": titel,
        "lead": {"customer": {"type": "company", "id": bedrijf_id}},
        "source": {"type": "api"},
    }, _token)
    if not r.ok:
        return {"error": f"Deal aanmaken mislukt: {r.text[:300]}"}
    deal_id = r.json().get("data", {}).get("id")
    if not deal_id:
        return {"error": "Deal ID niet ontvangen."}

    # BTW tarief ophalen (21%)
    vat_id = None
    vr = _tl("taxRates.list", {}, _token)
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
    qr = _tl("quotations.create", {
        "deal_id": deal_id,
        "title": titel,
        "text": f"Offerte voor {bedrijf_naam}",
        "currency": {"code": "EUR", "exchange_rate": 1.0},
        "grouped_lines": grouped_lines,
    }, _token)

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
        return _tool_zoek_deals(inputs["bedrijf_id"])
    if name == "haal_offerte_producten":
        return _tool_haal_offerte_producten(inputs["deal_id"])
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
        "description": "Haal recente deals op voor een bedrijf om de template deal te identificeren.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bedrijf_id": {"type": "string"},
            },
            "required": ["bedrijf_id"],
        },
    },
    {
        "name": "haal_offerte_producten",
        "description": (
            "Haal alle producten (met prijs en beschrijving) op uit de offerte(s) van een deal. "
            "Gebruik dit op de template deal om te weten welke producten beschikbaar zijn."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "deal_id": {"type": "string"},
            },
            "required": ["deal_id"],
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
                            "beschrijving":          {"type": "string",  "description": "Exacte productnaam uit de template"},
                            "prijs":                 {"type": "number",  "description": "Eenheidsprijs excl. BTW"},
                            "extended_description":  {"type": "string",  "description": "Uitgebreide beschrijving uit de template (mag leeg zijn)"},
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
1. Zoek het bedrijf op (zoek_bedrijf). Als er meerdere treffer zijn, vraag welke.
2. Zoek de deals op (zoek_deals). Kies de meest recente of de deal met "template" in de titel.
3. Haal de beschikbare producten op uit die deal (haal_offerte_producten).
4. Match de gevraagde producten aan de template-producten op basis van onderstaande vertaalregels.
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

Geef de matched productnaam, prijs en extended_description EXACT over uit de template.
Als je een product niet kunt matchen, zeg dat dan en vraag om verduidelijking.
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

# Session state initialiseren
if "chat_ui" not in st.session_state:
    st.session_state.chat_ui = []       # [{"role": "user"|"assistant", "content": str}]
if "chat_api" not in st.session_state:
    st.session_state.chat_api = []      # berichten voor de Anthropic API (inclusief tool calls)

# Bestaande berichten tonen
for msg in st.session_state.chat_ui:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat-invoer
if prompt := st.chat_input("Maak een offerte aan…"):
    # Gebruikersbericht tonen
    st.session_state.chat_ui.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Voeg toe aan API-berichten
    st.session_state.chat_api.append({"role": "user", "content": prompt})

    # Agent uitvoeren
    with st.chat_message("assistant"):
        with st.spinner("Bezig…"):
            response_text, updated_api_msgs = run_agent(st.session_state.chat_api)
        st.markdown(response_text)

    # Opslaan
    st.session_state.chat_ui.append({"role": "assistant", "content": response_text})
    st.session_state.chat_api = updated_api_msgs

# Nieuw gesprek knop
if st.session_state.chat_ui:
    st.divider()
    if st.button("🗑️ Nieuw gesprek"):
        st.session_state.chat_ui = []
        st.session_state.chat_api = []
        st.rerun()
