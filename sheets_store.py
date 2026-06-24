"""Gedeelde Google Sheets-opslag voor het provisie-rapport.

Werkt zowel lokaal (generator/pusher, credentials uit een JSON-bestand) als in
Streamlit Cloud (credentials uit st.secrets). Importeert GEEN streamlit, zodat
het lokaal bruikbaar blijft.

Twee worksheets in één Google Sheet:
  - 'facturen'  : basisdata, door de generator overschreven bij elke run.
  - 'overrides' : handmatige bewerkingen, gekoppeld op invoice_id, blijven staan.
"""
from __future__ import annotations

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Kolomvolgorde van de basisdata (worksheet 'facturen')
FACTUUR_COLS = [
    "invoice_id", "invoice_number", "invoice_date", "klant",
    "deal_id", "dealnummer", "bestelbon",
    "verantwoordelijke", "account_manager_tl",
    "totaal", "provisie_basis", "excl_kosten",
    "inkoop", "marge", "marge_pct", "marge_flag",
]

# Kolommen van de bewerkingen (worksheet 'overrides')
OVERRIDE_COLS = [
    "invoice_id", "account_manager_override", "notitie",
    "telt_mee", "provisie_betaald_op",
]

FACTUREN_WS = "facturen"
OVERRIDES_WS = "overrides"


# ── Verbinding ────────────────────────────────────────────────────────────────
def get_client(sa_info: dict) -> gspread.Client:
    """Maak een gspread-client van een service-account dict."""
    creds = Credentials.from_service_account_info(dict(sa_info), scopes=SCOPES)
    return gspread.authorize(creds)


def open_book(client: gspread.Client, sheet_id: str) -> gspread.Spreadsheet:
    return client.open_by_key(sheet_id)


def _cell(v):
    """Converteer een Python-waarde naar iets dat de Sheets-API accepteert."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return v


def _ensure_ws(book: gspread.Spreadsheet, title: str, header: list[str],
               rows: int = 1000, cols: int = 26) -> gspread.Worksheet:
    """Geef het worksheet terug; maak het aan (met header) als het ontbreekt."""
    try:
        return book.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=title, rows=rows, cols=max(cols, len(header)))
        ws.update([header], value_input_option="RAW")
        return ws


# ── Basisdata (facturen) ──────────────────────────────────────────────────────
def write_facturen(book: gspread.Spreadsheet, invoices: list[dict]) -> int:
    """Overschrijf het 'facturen'-worksheet volledig met de nieuwe basisdata."""
    ws = _ensure_ws(book, FACTUREN_WS, FACTUUR_COLS,
                    rows=len(invoices) + 20, cols=len(FACTUUR_COLS))
    data = [FACTUUR_COLS]
    for inv in invoices:
        data.append([_cell(inv.get(c)) for c in FACTUUR_COLS])
    ws.clear()
    ws.update(data, value_input_option="RAW")
    return len(invoices)


def read_facturen(book: gspread.Spreadsheet) -> list[dict]:
    ws = _ensure_ws(book, FACTUREN_WS, FACTUUR_COLS)
    return ws.get_all_records()


# ── Bewerkingen (overrides) ───────────────────────────────────────────────────
def read_overrides(book: gspread.Spreadsheet) -> dict[str, dict]:
    """Lees de overrides als {invoice_id: {kolom: waarde}}."""
    ws = _ensure_ws(book, OVERRIDES_WS, OVERRIDE_COLS)
    out: dict[str, dict] = {}
    for r in ws.get_all_records():
        iid = str(r.get("invoice_id", "")).strip()
        if iid:
            out[iid] = r
    return out


def write_overrides(book: gspread.Spreadsheet, override_rows: list[dict]) -> int:
    """Overschrijf het 'overrides'-worksheet met de meegegeven rijen.

    Alleen rijen met minstens één ingevulde bewerking horen hier in te staan.
    """
    ws = _ensure_ws(book, OVERRIDES_WS, OVERRIDE_COLS,
                    rows=len(override_rows) + 50, cols=len(OVERRIDE_COLS))
    data = [OVERRIDE_COLS]
    for r in override_rows:
        data.append([_cell(r.get(c, "")) for c in OVERRIDE_COLS])
    ws.clear()
    ws.update(data, value_input_option="RAW")
    return len(override_rows)
