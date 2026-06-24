"""Provisie-rapport — interactieve weergave + bewerkingen.

Leest de basisdata uit het Google Sheet (tab 'facturen', door de lokale
generator gevuld) en de bewerkingen (tab 'overrides'). Account managers kunnen
de AM corrigeren, een notitie plaatsen, aanvinken of een factuur meetelt voor
provisie, en markeren of de provisie al is uitbetaald.

Heeft GEEN Teamleader-login nodig — alleen toegang tot het Google Sheet.
"""
import pandas as pd
import streamlit as st

import sheets_store as ss

st.set_page_config(page_title="36 Cycling - Provisie", page_icon="💶", layout="wide")

# ── Login (zelfde wachtwoord als de hoofd-app) ────────────────────────────────
if "sidebar_auth" not in st.session_state:
    st.session_state.sidebar_auth = False

if not st.session_state.sidebar_auth:
    st.title("Provisie-rapport")
    with st.form("provisie_login"):
        pw = st.text_input("Wachtwoord", type="password")
        if st.form_submit_button("Inloggen"):
            if pw == st.secrets["auth"]["password"]:
                st.session_state.sidebar_auth = True
                st.rerun()
            else:
                st.error("Ongeldig wachtwoord.")
    st.stop()

# ── Sheet-verbinding ──────────────────────────────────────────────────────────
SHEET_ID = st.secrets.get("PROVISIE_SHEET_ID", "")
if not SHEET_ID or "gcp_service_account" not in st.secrets:
    st.error(
        "Configuratie ontbreekt. Zet `PROVISIE_SHEET_ID` en het "
        "`[gcp_service_account]`-blok in de Streamlit secrets."
    )
    st.stop()


@st.cache_resource(show_spinner=False)
def _book():
    client = ss.get_client(dict(st.secrets["gcp_service_account"]))
    return ss.open_book(client, SHEET_ID)


@st.cache_data(ttl=120, show_spinner="Data laden uit Google Sheet...")
def load_data():
    book = _book()
    facturen = ss.read_facturen(book)
    overrides = ss.read_overrides(book)
    return facturen, overrides


def _to_bool(v, default=True):
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().upper() not in ("FALSE", "0", "NEE", "N", "")


# ── Data laden + samenvoegen ──────────────────────────────────────────────────
st.title("💶 Provisie-rapport")

col_a, col_b = st.columns([1, 5])
with col_a:
    if st.button("🔄 Verversen", use_container_width=True):
        load_data.clear()
        st.rerun()

facturen, overrides = load_data()
if not facturen:
    st.warning("Nog geen data in het 'facturen'-tabblad. Draai de generator + push lokaal.")
    st.stop()

df = pd.DataFrame(facturen)

# Numerieke kolommen afdwingen
for c in ["totaal", "provisie_basis", "excl_kosten", "inkoop", "marge", "marge_pct"]:
    df[c] = pd.to_numeric(df.get(c), errors="coerce")

df["invoice_id"] = df["invoice_id"].astype(str)

# Bewerkingen toepassen
def _ov(iid, key, default=""):
    return overrides.get(iid, {}).get(key, default)

df["account_manager"] = df.apply(
    lambda r: (str(_ov(r["invoice_id"], "account_manager_override")).strip()
               or r.get("account_manager_tl", "")),
    axis=1,
)
df["telt_mee"] = df["invoice_id"].apply(lambda i: _to_bool(_ov(i, "telt_mee", True)))
df["provisie_betaald_op"] = df["invoice_id"].apply(
    lambda i: str(_ov(i, "provisie_betaald_op", "")).strip())
df["notitie"] = df["invoice_id"].apply(lambda i: str(_ov(i, "notitie", "")).strip())
df["am_gecorrigeerd"] = df["invoice_id"].apply(
    lambda i: bool(str(_ov(i, "account_manager_override")).strip()))

# Effectieve provisie-basis (telt_mee schakelt de hele factuur uit)
df["provisie_telt"] = df.apply(
    lambda r: (r["provisie_basis"] if r["telt_mee"] else 0.0), axis=1)
df["betaald"] = df["provisie_betaald_op"].apply(lambda v: bool(str(v).strip()))

# ── Filters ───────────────────────────────────────────────────────────────────
st.sidebar.header("Filters")

am_opties = sorted([a for a in df["account_manager"].dropna().unique() if str(a).strip()])
sel_am = st.sidebar.multiselect("Account Manager", am_opties, default=[])

signaal_labels = {
    "ok": "OK (30-50%)", "hoog": "Hoog (>50%)", "laag": "Laag (<30%)",
    "controle": "Controle (neg. basis)", "geen_data": "Geen inkoopdata",
}
sel_sig = st.sidebar.multiselect(
    "Marge-signaal", list(signaal_labels.keys()),
    format_func=lambda k: signaal_labels.get(k, k), default=[])

betaald_keuze = st.sidebar.radio(
    "Provisie-status", ["Alles", "Nog niet betaald", "Al betaald"], index=0)

alleen_meetellend = st.sidebar.checkbox("Alleen wat meetelt voor provisie", value=False)
alleen_geen_am = st.sidebar.checkbox("Alleen zonder ingevulde AM", value=False)
zoek = st.sidebar.text_input("Zoek klant / factuur / deal")

fdf = df.copy()
if sel_am:
    fdf = fdf[fdf["account_manager"].isin(sel_am)]
if sel_sig:
    fdf = fdf[fdf["marge_flag"].isin(sel_sig)]
if betaald_keuze == "Nog niet betaald":
    fdf = fdf[~fdf["betaald"]]
elif betaald_keuze == "Al betaald":
    fdf = fdf[fdf["betaald"]]
if alleen_meetellend:
    fdf = fdf[fdf["telt_mee"]]
if alleen_geen_am:
    fdf = fdf[df["account_manager_tl"].fillna("").str.strip() == ""]
if zoek:
    z = zoek.lower()
    mask = (
        fdf["klant"].astype(str).str.lower().str.contains(z, na=False)
        | fdf["invoice_number"].astype(str).str.lower().str.contains(z, na=False)
        | fdf["dealnummer"].astype(str).str.lower().str.contains(z, na=False)
        | fdf["bestelbon"].astype(str).str.lower().str.contains(z, na=False)
    )
    fdf = fdf[mask]

# ── Samenvatting per AM (respecteert telt_mee, op huidige filter) ─────────────
st.subheader("Samenvatting per account manager")
samenvatting = (
    fdf.assign(
        inkoop_telt=fdf.apply(
            lambda r: (r["inkoop"] if (r["telt_mee"] and pd.notna(r["inkoop"])) else 0.0),
            axis=1),
    )
    .groupby("account_manager")
    .agg(
        facturen=("invoice_id", "count"),
        provisie_basis=("provisie_telt", "sum"),
        inkoop=("inkoop_telt", "sum"),
    )
    .reset_index()
)
samenvatting["marge"] = samenvatting["provisie_basis"] - samenvatting["inkoop"]
samenvatting["marge_%"] = (
    samenvatting["marge"] / samenvatting["provisie_basis"].replace(0, pd.NA))
totaal_basis = samenvatting["provisie_basis"].sum()
st.dataframe(
    samenvatting.rename(columns={
        "account_manager": "Account Manager", "facturen": "# Facturen",
        "provisie_basis": "Provisie-basis", "inkoop": "Inkoop",
        "marge": "Marge €", "marge_%": "Marge %"}),
    use_container_width=True, hide_index=True,
    column_config={
        "Provisie-basis": st.column_config.NumberColumn(format="€ %.2f"),
        "Inkoop": st.column_config.NumberColumn(format="€ %.2f"),
        "Marge €": st.column_config.NumberColumn(format="€ %.2f"),
        "Marge %": st.column_config.NumberColumn(format="%.1f%%"),
    },
)
st.caption(f"Provisie-basis (gefilterd, wat meetelt): € {totaal_basis:,.2f}  "
           f"| {len(fdf)} facturen getoond van {len(df)} totaal")

# ── Bewerkbare tabel ──────────────────────────────────────────────────────────
st.subheader("Facturen — bewerk AM, notitie, meetellen en betaald-status")

toon_cols = [
    "invoice_id", "invoice_number", "invoice_date", "dealnummer", "bestelbon",
    "klant", "verantwoordelijke", "account_manager", "marge_pct", "marge_flag",
    "provisie_basis", "inkoop", "telt_mee", "provisie_betaald_op", "notitie",
]
editable = fdf[toon_cols].copy()

edited = st.data_editor(
    editable,
    use_container_width=True,
    hide_index=True,
    height=560,
    key="provisie_editor",
    disabled=[
        "invoice_id", "invoice_number", "invoice_date", "dealnummer", "bestelbon",
        "klant", "verantwoordelijke", "marge_pct", "marge_flag",
        "provisie_basis", "inkoop",
    ],
    column_config={
        "invoice_id": None,  # verbergen
        "invoice_number": st.column_config.TextColumn("Factuur"),
        "invoice_date": st.column_config.TextColumn("Datum"),
        "dealnummer": st.column_config.TextColumn("Deal"),
        "bestelbon": st.column_config.TextColumn("Bestelbon"),
        "klant": st.column_config.TextColumn("Klant", width="medium"),
        "verantwoordelijke": st.column_config.TextColumn("Verantw."),
        "account_manager": st.column_config.SelectboxColumn(
            "Account Manager", options=am_opties, required=False),
        "marge_pct": st.column_config.NumberColumn("Marge %", format="%.1f%%"),
        "marge_flag": st.column_config.TextColumn("Signaal"),
        "provisie_basis": st.column_config.NumberColumn("Prov.-basis", format="€ %.0f"),
        "inkoop": st.column_config.NumberColumn("Inkoop", format="€ %.0f"),
        "telt_mee": st.column_config.CheckboxColumn("Telt mee", default=True),
        "provisie_betaald_op": st.column_config.TextColumn(
            "Betaald op", help="Datum of periode; leeg = nog niet betaald"),
        "notitie": st.column_config.TextColumn("Notitie", width="large"),
    },
)

# ── Opslaan ───────────────────────────────────────────────────────────────────
if st.button("💾 Bewerkingen opslaan", type="primary"):
    # Lees verse overrides (beperkt clobberen bij gelijktijdig bewerken),
    # pas de zichtbare/bewerkte rijen daarop toe.
    book = _book()
    huidige = ss.read_overrides(book)
    tl_map = dict(zip(df["invoice_id"], df["account_manager_tl"].fillna("")))

    for _, row in edited.iterrows():
        iid = str(row["invoice_id"])
        am_edit = str(row["account_manager"] or "").strip()
        am_tl = str(tl_map.get(iid, "")).strip()
        telt = bool(row["telt_mee"])
        betaald_op = str(row["provisie_betaald_op"] or "").strip()
        notitie = str(row["notitie"] or "").strip()

        am_override = am_edit if (am_edit and am_edit != am_tl) else ""
        heeft_inhoud = bool(am_override) or bool(notitie) or (not telt) or bool(betaald_op)

        if heeft_inhoud:
            huidige[iid] = {
                "invoice_id": iid,
                "account_manager_override": am_override,
                "notitie": notitie,
                "telt_mee": telt,
                "provisie_betaald_op": betaald_op,
            }
        else:
            huidige.pop(iid, None)

    ss.write_overrides(book, list(huidige.values()))
    load_data.clear()
    st.success(f"Opgeslagen. {len(huidige)} facturen met een bewerking.")
    st.rerun()
