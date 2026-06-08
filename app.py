import streamlit as st
import requests
import os
import time
import json
import re
import asyncio
from pathlib import Path
from streamlit_folium import st_folium
from dotenv import load_dotenv
from streamlit_js_eval import get_geolocation

import html
import math
import random
import unicodedata
from data_store import (
    add_itinerary_item,
    get_connection,
    get_itinerary_items,
    get_places,
    init_db,
    normalize_place,
    remove_itinerary_item,
    seed_places,
)
from persistence import (
    PROFIL_BUDSJETT,
    PROFIL_REISE_FOLGE,
    STANDARD_PROFIL,
    lagre_data,
    last_inn_data,
    normaliser_profil,
    persist_reiseplan,
    sync_reiseplan_to_sqlite,
)
from ui_cards import (
    behandle_affiliate_pending,
    injiser_mork_modus_css,
    render_affiliate_lenker as _ui_render_affiliate_lenker,
    render_hero_html,
    render_land_kort_html,
    render_travel_card_html as _ui_render_travel_card_html,
    vis_sted_foto as _ui_vis_sted_foto,
    vis_tom_tilstand,
    vis_travel_card_html,
)
from kart_utils import (
    filtrer_data,
    lag_chat_oppdag_kart,
    lag_radar_kart,
    lag_reiseplan_rute_kart,
    lag_stedskart,
    optimaliser_reiserute_naermeste_nabo,
    regn_ut_avstand_km,
)
from ui_panels import render_sank_ki_panel as _render_sank_ki_panel
from translations import TEKSTER
from place_images import hent_sted_bilde_url
from transport_planner import (
    bygg_eksterne_planleggere,
    bygg_stedvalg_fra_database,
)
import place_quality as _place_quality
from place_quality import (
    MIN_UNIKHETSGRAD,
    _RESTAURANT_STERKE_ORD,
    filtrer_steder_for_app,
    oppfyller_visning_kriterier,
    godkjent_hotel_kandidat as _godkjent_hotel_kandidat,
    godkjent_restaurant_kandidat as _godkjent_restaurant_kandidat,
    klassifiser_source_type_fra_perle as _klassifiser_source_type_fra_perle,
    normaliser_saerhetsscore as _normaliser_saerhetsscore,
    score_saerhetstekst as _score_saerhetstekst,
)

# Cron-job: ?ping_cron vekker databasen uten å laste hele UI-en
if "ping_cron" in st.query_params:
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1")
        st.write("Database våken!")
    except Exception as e:
        st.write(f"Feil ved vekking: {e}")
    st.stop()


def _er_velbesokt_museum(sted) -> bool:
    """Velbesøkt museum-sjekk via place_quality (unngår navngitt import)."""
    checker = getattr(_place_quality, "er_velbesokt_museum", None)
    if checker is not None:
        return bool(checker(sted))
    checker = getattr(_place_quality, "er_velbesøkt_museum", None)
    return bool(checker and checker(sted))


def _er_mainstream_turistdestinasjon(sted) -> bool:
    """Klassisk turistby — ikke skjult helgeperle."""
    checker = getattr(_place_quality, "er_mainstream_turistdestinasjon", None)
    return bool(checker and checker(sted))


def _er_blant_landets_storste_byer(sted) -> bool:
    """Blant landets fem største byer — ikke helgeby."""
    checker = getattr(_place_quality, "er_blant_landets_storste_byer", None)
    return bool(checker and checker(sted))


def _sla_sammen_steder_etter_id(*lister):
    """Slår sammen steder; første forekomst av id vinner."""
    by_id = {}
    for liste in lister:
        for sted in liste:
            sid = sted.get("id")
            if sid and sid not in by_id:
                by_id[sid] = sted
    return list(by_id.values())


def _sla_sammen_steder_etter_nokkel(*lister):
    """Slår sammen steder; første forekomst av navn+by+land vinner."""
    by_key = {}
    for liste in lister:
        for sted in liste:
            key = (
                f"{(sted.get('navn') or '').strip().lower()}|"
                f"{(sted.get('by') or '').strip().lower()}|"
                f"{(sted.get('land') or '').strip().lower()}"
            )
            if key not in by_key:
                by_key[key] = sted
    return list(by_key.values())


def _hent_kuratert_overnatting_for_visning():
    """Kuratert overnatting fra database.py (leser fersk liste, ingen privat data_store-import)."""
    import importlib

    import database as _db

    importlib.reload(_db)
    kilde = list(
        getattr(_db, "UNIKE_OVERNATTING", None)
        or getattr(_db, "UNIKE_HOTELLER", [])
    )
    return [normalize_place(p, "hotel") for p in kilde]


def _perle_nokkel(navn, by, land):
    return f"{(navn or '').strip().lower()}|{(by or '').strip().lower()}|{(land or '').strip().lower()}"


def _effektiv_kilde_type(sted):
    """type-felt (f.eks. overnatting) veier tyngre enn feil source_type i lagret JSON."""
    if not sted:
        return "hidden_gem"
    type_hint = (sted.get("type") or "").strip().lower()
    if type_hint in ("hotell", "hotel", "overnatting", "lodging"):
        return "hotel"
    if type_hint in ("gastronomi", "restaurant", "mat"):
        return "restaurant"
    return (sted.get("source_type") or "hidden_gem").strip().lower()


def _ensure_places_seeded():
    """Seeder kuratert data én gang per sesjon — ikke ved hver rerun."""
    if st.session_state.get("_places_seeded"):
        return
    seed_places()
    st.session_state._places_seeded = True


def _last_lokale_stedlister():
    """Oppfrisker lister fra SQLite ved hver Streamlit-kjøring."""
    _ensure_places_seeded()
    kuratert_overnatting = _hent_kuratert_overnatting_for_visning()
    alle_db = get_places(seed=False)
    return (
        filtrer_steder_for_app(
            [s for s in alle_db if _effektiv_kilde_type(s) == "hidden_gem"]
        ),
        filtrer_steder_for_app(
            [s for s in alle_db if _effektiv_kilde_type(s) == "restaurant"]
        ),
        _sla_sammen_steder_etter_nokkel(
            kuratert_overnatting,
            filtrer_steder_for_app(
                [s for s in alle_db if _effektiv_kilde_type(s) == "hotel"]
            ),
        ),
    )


_APP_ROOT = Path(__file__).resolve().parent
# Last inn nøkler fra .env (override=True: secrets.toml-plassholder skal ikke vinne over .env lokalt)
load_dotenv(_APP_ROOT / ".env", override=True)

_OPENROUTER_PLACEHOLDERS = frozenset(
    {"", "DIN_OPENROUTER_API_KEY_HER", "din_openrouter_api_key_her"}
)

def _er_gyldig_openrouter_nokkel(nokkel):
    n = (nokkel or "").strip()
    if not n or n in _OPENROUTER_PLACEHOLDERS:
        return False
    if "DIN_OPENROUTER" in n.upper():
        return False
    return len(n) >= 20


def hent_openrouter_api_key():
    """Hent OpenRouter-nøkkel fra .env (via load_dotenv) eller Streamlit secrets."""
    kandidater = [os.getenv("OPENROUTER_API_KEY", "")]
    try:
        kandidater.append(st.secrets.get("OPENROUTER_API_KEY", ""))
    except Exception:
        pass
    for raw in kandidater:
        if _er_gyldig_openrouter_nokkel(raw):
            return raw.strip()
    return ""


def _openrouter_fejl_tekst(response):
    """Leser OpenRouter-feil (404 = ukjent/utgått modell-ID)."""
    try:
        data = response.json()
        err = data.get("error") or {}
        melding = err.get("message") or response.reason or "Ukjent feil"
        kode = err.get("code", response.status_code)
        return f"{melding} (HTTP {kode})"
    except Exception:
        return f"HTTP {response.status_code}: {response.text[:200]}"


def _openrouter_post(payload, timeout=30, *, stream=False):
    api_key = hent_openrouter_api_key()
    if not api_key:
        raise RuntimeError(tr("sank_mangler_api"))
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost:8501",
        "X-Title": "Hemmelige Europa",
    }
    response = requests.post(
        URL, headers=headers, json=payload, timeout=timeout, stream=stream
    )
    if response.status_code == 401:
        raise RuntimeError(tr("openrouter_401"))
    if response.status_code == 404:
        raise RuntimeError(
            f"OpenRouter-modellen «{payload.get('model', '?')}» finnes ikke. "
            f"{_openrouter_fejl_tekst(response)} "
            "Velg en annen modell i sidemenyen (f.eks. Gemini 2.5 Flash)."
        )
    if response.status_code == 402:
        raise RuntimeError(tr("openrouter_402"))
    response.raise_for_status()
    return response

# ========================================
# PERSISTENT LAGRING (CHAT, PROFIL, REISEPLAN)
# ========================================
_normaliser_profil = normaliser_profil

# Last inn data ved oppstart og klargjør session_state
lagrede_data = last_inn_data()
if "reise_chat" not in st.session_state:
    st.session_state.reise_chat = lagrede_data["reise_chat"]
if "reiseplan" not in st.session_state:
    st.session_state.reiseplan = lagrede_data.get("reiseplan", [])
if "profil" not in st.session_state:
    st.session_state.profil = normaliser_profil(lagrede_data.get("profil"))
if "_reiseplan_synced" not in st.session_state:
    sync_reiseplan_to_sqlite(st.session_state.reiseplan)
    st.session_state._reiseplan_synced = True
if "bilde_autoload_wiki" not in st.session_state:
    st.session_state.bilde_autoload_wiki = False
if "_profil_lagret_snapshot" not in st.session_state:
    st.session_state._profil_lagret_snapshot = json.dumps(
        st.session_state.profil, sort_keys=True, ensure_ascii=False
    )

# ========================================
# KONFIGURASJON & DESIGN
# ========================================
st.set_page_config(page_title="Hemmelige Europa", layout="wide", initial_sidebar_state="expanded")


def _inject_styles():
    """Leser style.css og injiserer CSS i appen."""
    css_path = Path(__file__).with_name("style.css")
    if not css_path.exists():
        return
    css = css_path.read_text(encoding="utf-8")
    if css.strip():
        st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


_inject_styles()

# ========================================
# SPRÅKSYSTEM (sidebar-selektor + ordbok)
# ========================================
spraak = st.sidebar.segmented_control(
    "Språk / Language",
    options=["NO", "EN"],
    default=st.session_state.get("spraak", "NO") if st.session_state.get("spraak", "NO") in ["NO", "EN"] else "NO",
    key="spraak",
    selection_mode="single",
)

_spraak = spraak if spraak in TEKSTER else "NO"
behandle_affiliate_pending(_spraak)

# Sikrer overnatting-tekster selv om eldre translations.py mangler nøkler
_HOTELL_TEKST_FALLBACK = {
    "NO": {
        "hjem_metric_oppdagelser": "✨ Oppdagelser",
        "fane_hotell": "🛏️ Overnatting",
        "fane_mat": "🍽️ Mat-oppdagelser",
        "hjem_metric_hoteller": "🛏️ Overnatting",
        "hotell_header": "🛏️ Overnatting-oppdagelser",
        "hotell_sok": "🔍 Søk overnatting (navn eller by)",
        "hotell_sorter_type": "Sorter etter type",
        "hotell_pris": "💰 Prisnivå:",
        "hotell_ingen_treff": "Ingen overnatting-oppdagelser matchet søket ditt.",
        "hotell_i_db": "{0} overnatting-oppdagelser i databasen",
        "hotell_kart_expander": "🗺️ Kart over overnatting",
        "hotell_pa_kart": "{0} overnatting-oppdagelser på kartet",
        "type_hotell": "Overnatting-oppdagelse",
        "type_perle": "Oppdagelse",
        "favoritt_knapp": "Lagre oppdagelse i reiseplanen",
        "sank_expander": "Finn nye oppdagelser (med KI)",
        "reiseplan_fane": "✨ Dine oppdagelser",
        "sank_min_score_fast": "Oppdagelser: minimum {0}/10 unikhet.",
        "hotell_min_unikhet": "Overnatting: minimum {0}/10 unikhet.",
        "mat_min_unikhet": "Mat: minimum {0}/10 unikhet.",
        "sank_rapport": "Foreslått: {0} · Vises: {1} · Allerede i appen: {2} · Lav score: {3} · Uten kart: {4} · Ugyldig felt: {5}",
        "sank_ingen_detalj": "(lav score: {0}, allerede i appen: {1}, ugyldig felt: {2})",
        "sank_allerede_i_db": "✓ Finnes allerede i appen — lagring hoppes over.",
        "sank_uten_koordinater": "⚠️ Kartposisjon ikke funnet ennå — kan lagres og oppdateres senere.",
        "sank_ingen_tom": "KI returnerte ingen forslag for dette området.",
    },
    "EN": {
        "hjem_metric_oppdagelser": "✨ Discoveries",
        "fane_hotell": "🛏️ Stays",
        "fane_mat": "🍽️ Food discoveries",
        "hjem_metric_hoteller": "🛏️ Stays",
        "hotell_header": "🛏️ Stay discoveries",
        "hotell_sok": "🔍 Search stays (name or city)",
        "hotell_sorter_type": "Filter by type",
        "hotell_pris": "💰 Price level:",
        "hotell_ingen_treff": "No stay discoveries matched your search.",
        "hotell_i_db": "{0} stay discoveries in the database",
        "hotell_kart_expander": "🗺️ Map of stays",
        "hotell_pa_kart": "{0} stay discoveries on the map",
        "type_hotell": "Stay discovery",
        "type_perle": "Discovery",
        "favoritt_knapp": "Save discovery to your plan",
        "sank_expander": "Find new discoveries (with AI)",
        "reiseplan_fane": "✨ Your discoveries",
        "sank_min_score_fast": "Discoveries: minimum {0}/10 uniqueness.",
        "hotell_min_unikhet": "Stays: minimum {0}/10 uniqueness.",
        "mat_min_unikhet": "Food: minimum {0}/10 uniqueness.",
        "sank_rapport": "Suggested: {0} · Shown: {1} · Already in app: {2} · Low score: {3} · No map: {4} · Invalid fields: {5}",
        "sank_ingen_detalj": "(low score: {0}, already in app: {1}, invalid fields: {2})",
        "sank_allerede_i_db": "✓ Already in the app — save skipped.",
        "sank_uten_koordinater": "⚠️ Map coordinates not found yet — you can still save and update later.",
        "sank_ingen_tom": "AI returned no suggestions for this area.",
    },
}
for _lang, _keys in _HOTELL_TEKST_FALLBACK.items():
    for _k, _v in _keys.items():
        TEKSTER[_lang][_k] = _v

T: dict[str, str] = {**TEKSTER["NO"], **TEKSTER[_spraak]}

# Oppfrisk stedlister etter språk/tekster er satt (hver script rerun)
SKJULTE_PERLER_DB, LOKALE_SPISESTEDER_DB, LOKALE_HOTELLER_DB = _last_lokale_stedlister()


def _oppfrisk_lokale_stedlister():
    """Leser stedlister på nytt fra SQLite (etter lagring i DB)."""
    global SKJULTE_PERLER_DB, LOKALE_SPISESTEDER_DB, LOKALE_HOTELLER_DB
    SKJULTE_PERLER_DB, LOKALE_SPISESTEDER_DB, LOKALE_HOTELLER_DB = _last_lokale_stedlister()


def tr(key: str, default: str | None = None) -> str:
    """Oversettelse: aktivt språk med norsk fallback."""
    if default is not None:
        val = T.get(key, default)
        return val if isinstance(val, str) else default
    val = T.get(key)
    if isinstance(val, str):
        return val
    fallback = TEKSTER["NO"].get(key, key)
    return fallback if isinstance(fallback, str) else key


# AI-modell (OpenRouter) — velg i sidemeny eller sett OPENROUTER_MODEL i secrets.toml
# Kun modell-ID-er som OpenRouter faktisk tilbyr (404 hvis utgått)
MODELL_ALTERNATIVER = [
    ("google/gemini-2.5-flash", "Gemini 2.5 Flash"),
    ("openai/gpt-4o-mini", "GPT-4o Mini"),
    ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B"),
]


def _standard_modell():
    try:
        return st.secrets.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
    except Exception:
        return os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")


_modell_ids = [m[0] for m in MODELL_ALTERNATIVER]
_modell_labels: dict[str, str] = {m[0]: m[1] for m in MODELL_ALTERNATIVER}
_standard_modell_id = _standard_modell()
if _standard_modell_id not in _modell_ids:
    _standard_modell_id = _modell_ids[0]
if st.session_state.get("openrouter_model") not in _modell_ids:
    st.session_state["openrouter_model"] = _standard_modell_id
_modell_default_idx = _modell_ids.index(_standard_modell_id)

st.sidebar.selectbox(
    tr("profil_ai_modell"),
    options=_modell_ids,
    format_func=lambda mid: _modell_labels.get(mid, mid),
    index=_modell_default_idx,
    key="openrouter_model",
    help=tr("profil_ai_modell_help"),
)
st.sidebar.checkbox(
    tr("bilde_autoload_label"),
    value=st.session_state.get("bilde_autoload_wiki", False),
    key="bilde_autoload_wiki",
    help=tr("bilde_autoload_help"),
)
st.sidebar.checkbox(
    tr("mork_modus_label"),
    value=st.session_state.get("mork_modus", False),
    key="mork_modus",
    help=tr("mork_modus_help"),
)
if st.session_state.get("mork_modus"):
    injiser_mork_modus_css()
st.sidebar.checkbox(
    tr("perf_debug_label"),
    value=st.session_state.get("vis_perf_debug", False),
    key="vis_perf_debug",
    help=tr("perf_debug_help"),
)

if not hent_openrouter_api_key():
    st.sidebar.error(tr("openrouter_mangler_env"))

if st.session_state.get("vis_perf_debug"):
    _env_rå = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    _nokkel_ok = bool(hent_openrouter_api_key())
    st.sidebar.caption(
        f"OPENROUTER_API_KEY i .env: {'JA' if _env_rå else 'NEI'} · "
        f"Gyldig for appen: {'JA' if _nokkel_ok else 'NEI'}"
    )
    if _env_rå and not _nokkel_ok:
        st.sidebar.caption(
            "Nøkkel funnet, men avvist (plassholder, for kort, eller feil navn i .env)."
        )


def _lagre_profil_ved_endring(ny_profil):
    """Lagrer profil diskret til JSON når brukeren endrer et valg."""
    st.session_state.profil = _normaliser_profil(ny_profil)
    snapshot = json.dumps(st.session_state.profil, sort_keys=True, ensure_ascii=False)
    if st.session_state.get("_profil_lagret_snapshot") != snapshot:
        lagre_data(
            st.session_state.reise_chat,
            st.session_state.profil,
            st.session_state.get("reiseplan"),
        )
        st.session_state._profil_lagret_snapshot = snapshot


with st.sidebar.expander(tr("snarveier_expander"), expanded=False):
    st.caption(tr("snarveier_hjelp"))
    for snarvei in (
        tr("fane_hjem"),
        tr("fane_ki"),
        tr("fane_mat"),
        tr("fane_hotell"),
        tr("reiseplan_fane"),
        tr("fane_chat"),
        tr("fane_transport"),
    ):
        st.markdown(f"• {snarvei}")

with st.sidebar.expander(tr("profil_expander"), expanded=False):
    _profil = _normaliser_profil(st.session_state.profil)
    ny_reise_folge = st.selectbox(
        tr("profil_reise_folge"),
        options=PROFIL_REISE_FOLGE,
        index=PROFIL_REISE_FOLGE.index(_profil["reise_folge"]),
        key="profil_reise_folge",
    )
    ny_budsjett = st.selectbox(
        tr("profil_budsjett"),
        options=PROFIL_BUDSJETT,
        index=PROFIL_BUDSJETT.index(_profil["budsjett"]),
        key="profil_budsjett",
    )
    _lagre_profil_ved_endring(
        {
            "reise_folge": ny_reise_folge,
            "budsjett": ny_budsjett,
        }
    )
    st.caption(tr("profil_lagres_auto"))

MODEL = st.session_state.get("openrouter_model", _standard_modell_id)
URL = "https://openrouter.ai/api/v1/chat/completions"

# ========================================
# FUNKSJONER
# ========================================


@st.cache_data(ttl=3600)
def sok_wikivoyage(sted):
    """Henter informasjon fra Wikivoyage om et sted med caching"""
    try:
        url = "https://en.wikivoyage.org/w/api.php"
        params = {
            "action": "query",
            "format": "json",
            "titles": sted,
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "redirects": 1,
        }
        r = requests.get(
            url, params=params, headers={"User-Agent": "HemmeligeEuropa/1.0"}, timeout=5
        )
        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        for page_id, page in pages.items():
            if page_id != "-1":
                tekst = page.get("extract", "")
                return tekst[:800] + "..." if len(tekst) > 800 else tekst
        return f"Ingen Wikivoyage-side funnet for '{sted}'."
    except Exception as e:
        return f"Kunne ikke hente informasjon fra Wikivoyage: {str(e)}"


def hent_koordinater_for_sok(sted):
    """Slår opp koordinater for et stedsnavn gratis via OpenStreetMap Nominatim"""
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {"q": sted, "format": "json", "limit": 1}
        headers = {"User-Agent": "HemmeligeEuropaReiseApp/1.0"}
        r = requests.get(url, params=params, headers=headers, timeout=5)
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None, None


async def sok_wikivoyage_async(sted):
    """Async wrapper (kjører sync-kall parallelt i tråd)."""
    return await asyncio.to_thread(sok_wikivoyage, sted)


def _parse_wiki_stedsnavn(sporsmal):
    """Henter stedsnavn fra «wiki Berlin», «wikivoyage Roma», «søk Tallinn», osv."""
    tekst = (sporsmal or "").strip()
    lav = tekst.lower()
    for prefiks in ("wiki ", "wikivoyage ", "wv ", "søk ", "sok "):
        if lav.startswith(prefiks):
            return tekst[len(prefiks) :].strip()
    return None


def _wikivoyage_treff_gyldig(tekst):
    """Sjekk om Wikivoyage returnerte ekte innhold (ikke feilmelding)."""
    if not (tekst or "").strip():
        return False
    if tekst.startswith("Ingen Wikivoyage-side funnet"):
        return False
    if tekst.startswith("Kunne ikke hente informasjon fra Wikivoyage"):
        return False
    return True


def _hent_wikivoyage_for_chat(sted):
    """Returnerer (tekst, lat, lon, feil_tekst)."""
    sted = (sted or "").strip()
    if not sted:
        return None, None, None, tr("chat_wiki_mangler_sted")
    lat, lon = None, None
    try:
        from wikivoyage_client import hent_artikkel_med_koordinater

        art = hent_artikkel_med_koordinater(sted, pause=0.2)
        if art:
            tekst = (art.get("beskrivelse") or "").strip()
            lat = art.get("latitude")
            lon = art.get("longitude")
            if tekst and _wikivoyage_treff_gyldig(tekst):
                if len(tekst) > 800:
                    tekst = tekst[:800] + "..."
                return tekst, lat, lon, None
    except Exception:
        pass
    tekst = sok_wikivoyage(sted)
    if _wikivoyage_treff_gyldig(tekst):
        if lat is None or lon is None:
            lat, lon = hent_koordinater_for_sok(sted)
        return tekst, lat, lon, None
    return None, None, None, tekst


async def _hent_wikivoyage_for_chat_async(sted):
    return await asyncio.to_thread(_hent_wikivoyage_for_chat, sted)


async def hent_koordinater_for_sok_async(sted):
    """Async wrapper (kjører sync-kall parallelt i tråd)."""
    return await asyncio.to_thread(hent_koordinater_for_sok, sted)


async def _hent_forste_geotreff_async(sokekandidater):
    """
    Slår opp flere geokodingskandidater parallelt og returnerer første treff
    i samme prioriterte rekkefølge som input-listen.
    """
    if not sokekandidater:
        return None, None, None
    resultater = await asyncio.gather(
        *(hent_koordinater_for_sok_async(navn) for navn in sokekandidater)
    )
    for navn, (lat, lon) in zip(sokekandidater, resultater):
        if lat is not None and lon is not None:
            return lat, lon, navn
    return None, None, None


def _run_async(coro):
    """
    Kjør en coroutine fra sync Streamlit-kode.
    Streamlit kjører vanligvis uten aktiv event loop i tråden, så asyncio.run er ok.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # Fallback: kjør i ny event loop i egen tråd.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(lambda: asyncio.run(coro))
            return future.result()
    return asyncio.run(coro)


def _slug_tekst(tekst):
    return re.sub(r"[^a-z0-9]+", "-", (tekst or "").lower()).strip("-")


AGENT_PERLE_MARKER = "||PERLE_JSON||"


def synlig_ai_svar(ai_tekst):
    """Fjerner skjult JSON-blokk fra tekst som vises i chatten."""
    if not ai_tekst:
        return ""
    if AGENT_PERLE_MARKER in ai_tekst:
        return ai_tekst.split(AGENT_PERLE_MARKER, 1)[0].rstrip()
    return ai_tekst.rstrip()


def _hotel_ki_hint(strict_mode=False):
    base = (
        f" Overnight stays / lodging (source_type hotel): quirky, historic, design-led or nature — "
        f"not only classic hotels (e.g. lighthouse keeper, farm, cave, parsonage, hostel). "
        f"Include pris with double-room price when known (e.g. 2800 kr). "
        f"Uniqueness score must be {MIN_UNIKHETSGRAD}/10 (only truly exceptional stays). "
        "No international chains or generic business hotels."
    )
    if strict_mode:
        base += " Reject mainstream luxury resorts unless the stay itself is the attraction."
    return base


def _sank_ki_regler_hint(strict_mode=False):
    """Regler som alltid sendes til KI ved «Finn nye perler med KI»."""
    if _spraak == "EN":
        base = (
            f" Rules for this search: minimum saerhetsscore {MIN_UNIKHETSGRAD} for gems, "
            f"{MIN_UNIKHETSGRAD}/10 for restaurants, "
            f"Any overnight stay must have source_type hotel; include pris when known. "
            f"Overnight stays require saerhetsscore {MIN_UNIKHETSGRAD}/10."
        )
    else:
        base = (
            f" Regler for dette søket: minimum unikhetsgrad {MIN_UNIKHETSGRAD} for perler, "
            f"{MIN_UNIKHETSGRAD}/10 for mat/restaurant, "
            f"Overnatting krever {MIN_UNIKHETSGRAD}/10 og source_type hotel; "
            f"inkluder dobbeltrom-pris når den er kjent."
        )
    return base + _restaurant_ki_hint(strict_mode)


def _restaurant_ki_hint(strict_mode=False):
    base = (
        f" Restaurants: only include genuinely local, historic or chef-driven venues with clear uniqueness "
        f"(saerhetsscore {MIN_UNIKHETSGRAD}/10 minimum). "
        "Never chains, hotel lobby dining, airport food or generic tourist traps. "
        "Use source_type restaurant only for real dining gems; otherwise use hidden_gem."
    )
    if strict_mode:
        base += (
            " Descriptions must explain why the food or setting is special."
        )
    return base + _hotel_ki_hint(strict_mode)


def _berik_rå_sank_kandidat(rå, omrade=""):
    """Fyller inn manglende felt fra KI-svar (vanlig ved land/område-søk)."""
    data = dict(rå) if isinstance(rå, dict) else {}
    navn = (data.get("navn") or data.get("name") or data.get("title") or "").strip()
    by = (
        data.get("by")
        or data.get("city")
        or data.get("region")
        or data.get("sted")
        or ""
    ).strip()
    land = (data.get("land") or data.get("country") or "").strip()
    if not land:
        land = (omrade or "").strip()
    if navn:
        data["navn"] = navn
    if by:
        data["by"] = by
    if land:
        data["land"] = land
    return data


def _sank_passer_kvalitet(kandidat, strict_mode=False):
    """Lett kvalitetssjekk kun for KI-sanking (ikke samme strenge regler som visning)."""
    from place_quality import er_kjede_hotell, er_kjede_restaurant, tekst_for_sted_sjekk

    tekst = tekst_for_sted_sjekk(kandidat)
    if er_kjede_restaurant(tekst) or er_kjede_hotell(tekst):
        return False
    if _er_velbesokt_museum(kandidat):
        return False
    source = kandidat.get("source_type")
    if source == "hotel":
        min_score = MIN_UNIKHETSGRAD
    elif source == "restaurant":
        min_score = MIN_UNIKHETSGRAD
    else:
        min_score = MIN_UNIKHETSGRAD
    if kandidat.get("saerhetsscore", 0) < min_score:
        return False
    if strict_mode and kandidat.get("source_type") == "restaurant":
        if len((kandidat.get("beskrivelse") or "").strip()) < 20:
            return False
    return True


def _normaliser_agent_perle(perle, fallback_tekst=""):
    if not isinstance(perle, dict):
        return None
    navn = (perle.get("navn") or perle.get("name") or perle.get("title") or "").strip()
    by = (
        perle.get("by")
        or perle.get("city")
        or perle.get("region")
        or perle.get("sted")
        or ""
    ).strip()
    land = (perle.get("land") or perle.get("country") or "").strip()
    if not navn or not by or not land:
        return None

    beskrivelse = (perle.get("beskrivelse") or perle.get("description") or "").strip()
    score = _normaliser_saerhetsscore(
        perle.get("saerhetsscore", perle.get("uniqueness_score")),
        f"{navn} {by} {land} {beskrivelse} {fallback_tekst}",
    )

    type_hint = (perle.get("type") or "kultur").strip().lower()
    source_type = (perle.get("source_type") or "").strip().lower()
    source_type = _klassifiser_source_type_fra_perle(
        perle, navn, beskrivelse, type_hint, source_type
    )
    if source_type == "hotel" or type_hint in ("hotell", "hotel", "overnatting", "lodging"):
        sted_type = "overnatting"
    elif type_hint in ("gastronomi", "restaurant", "mat"):
        sted_type = "gastronomi"
    elif type_hint in ("natur", "golf", "sport"):
        sted_type = type_hint
    else:
        sted_type = "kultur"

    return {
        "navn": navn,
        "by": by,
        "land": land,
        "beskrivelse": beskrivelse,
        "saerhetsscore": score,
        "source_type": source_type,
        "type": sted_type,
        "agent_id": f"agent-{_slug_tekst(navn)}-{_slug_tekst(by)}-{_slug_tekst(land)}",
    }


def _parse_agent_perle_json_linje(json_tekst):
    raw = (json_tekst or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    data = json.loads(raw)
    perle = data.get("perle", data) if isinstance(data, dict) else None
    return _normaliser_agent_perle(perle, raw)


def parse_agent_perle_fra_ai_svar(ai_tekst):
    """Henter strukturert perle fra AI-svar (JSON etter markør)."""
    if not ai_tekst or AGENT_PERLE_MARKER not in ai_tekst:
        return None
    _, _, json_del = ai_tekst.partition(AGENT_PERLE_MARKER)
    try:
        return _parse_agent_perle_json_linje(json_del)
    except (json.JSONDecodeError, TypeError):
        return None


def _juster_agent_perle_fra_chat_tekst(kandidat, ai_tekst=""):
    """Gjenkjenner overnatting/mat i reiseekspert-svar (JSON sier ofte feil type)."""
    if not kandidat:
        return kandidat
    from place_quality import er_kjede_hotell, er_kjede_restaurant

    blob = " ".join(
        str(kandidat.get(f, "") or "")
        for f in ("navn", "beskrivelse", "type")
    )
    if ai_tekst:
        blob += " " + synlig_ai_svar(ai_tekst)
    blob_lc = blob.lower()
    type_hint = (kandidat.get("type") or "").lower()
    eksplisitt = (kandidat.get("source_type") or "").strip().lower()

    hotel_signal = eksplisitt == "hotel" or type_hint in (
        "hotell",
        "hotel",
        "overnatting",
        "lodging",
    ) or any(
        ordlyd in blob_lc
        for ordlyd in (
            "hotell",
            "hotel",
            "overnatting",
            "gjestgiveri",
            "pensjonat",
            "hostel",
            "suite",
            "fyrvokter",
            "grottehotell",
            "bed and breakfast",
            "bnb",
        )
    )
    if hotel_signal and not er_kjede_hotell(blob_lc):
        kandidat["source_type"] = "hotel"
        kandidat["type"] = "overnatting"
        return kandidat

    mat_signal = eksplisitt == "restaurant" or type_hint in (
        "gastronomi",
        "restaurant",
        "mat",
    ) or any(ordlyd in blob_lc for ordlyd in _RESTAURANT_STERKE_ORD)
    if mat_signal and not er_kjede_restaurant(blob_lc):
        kandidat["source_type"] = "restaurant"
        kandidat["type"] = "gastronomi"
    return kandidat


def _berik_agent_kandidat_for_lagring(kandidat):
    """Fyller inn manglende felt før DB-lagring fra reiseekspert/KI."""
    kandidat = dict(kandidat)
    score = kandidat.get("saerhetsscore")
    if not (kandidat.get("beskrivelse") or "").strip():
        kandidat["beskrivelse"] = (
            f"Oppdaget av KI-agent i chatten. Særhetsscore: {score}/10."
            if score is not None
            else "Oppdaget av KI-agent i chatten."
        )
    if not (kandidat.get("pris") or "").strip() and kandidat.get("source_type") == "hotel":
        kandidat["pris"] = "€€"
    return kandidat


def _chat_lagre_tekster(kandidat):
    """Knapp- og toast-tekst avhengig av hva som lagres."""
    source = _effektiv_kilde_type(kandidat)
    if source == "hotel":
        return tr("chat_lagre_hotell"), tr("chat_lagre_toast_hotell")
    if source == "restaurant":
        return tr("chat_lagre_mat"), tr("chat_lagre_toast_mat")
    return tr("chat_lagre_db"), tr("chat_lagre_toast")


def _chat_agent_oppdaget_tekst(kandidat):
    source = (kandidat or {}).get("source_type", "hidden_gem")
    if source == "hotel":
        nøkkel = "chat_agent_oppdaget_hotell"
    elif source == "restaurant":
        nøkkel = "chat_agent_oppdaget_mat"
    else:
        nøkkel = "chat_agent_oppdaget"
    return tr(nøkkel).format(
        kandidat["navn"],
        kandidat["by"],
        kandidat["land"],
        kandidat["saerhetsscore"],
    )


def _match_kandidat_til_db_sted(kandidat):
    """Kobler KI-forslag til eksisterende sted i databasen når navn+by matcher."""
    if not kandidat:
        return None
    navn = (kandidat.get("navn") or "").strip().lower()
    by = (kandidat.get("by") or "").strip().lower()
    if not navn or not by:
        return None
    for sted in _alle_steder_i_databasen():
        if (sted.get("navn") or "").strip().lower() == navn and (
            sted.get("by") or ""
        ).strip().lower() == by:
            return dict(sted)
    return None


def _berik_kandidat_fra_db(kandidat):
    """Fyller KI-kandidat med database-data når stedet finnes i appen."""
    db_sted = _match_kandidat_til_db_sted(kandidat)
    if not db_sted:
        return kandidat
    return {
        **kandidat,
        "id": db_sted.get("id", kandidat.get("id")),
        "beskrivelse": db_sted.get("beskrivelse") or kandidat.get("beskrivelse", ""),
        "tips": db_sted.get("tips") or kandidat.get("tips", ""),
        "latitude": db_sted.get("latitude", kandidat.get("latitude")),
        "longitude": db_sted.get("longitude", kandidat.get("longitude")),
        "image_url": db_sted.get("image_url") or kandidat.get("image_url", ""),
        "source_type": db_sted.get("source_type", kandidat.get("source_type")),
        "type": db_sted.get("type", kandidat.get("type")),
        "fra_database": True,
    }


def detekter_perle_fra_ai_svar(ai_tekst):
    """Finner mulig sted i AI-svaret (JSON først, deretter regex)."""
    if not ai_tekst:
        return None

    kandidat = parse_agent_perle_fra_ai_svar(ai_tekst)
    if kandidat:
        kandidat = _juster_agent_perle_fra_chat_tekst(kandidat, ai_tekst)
        kandidat = _berik_kandidat_fra_db(kandidat)
        if kandidat.get("saerhetsscore", 0) >= MIN_UNIKHETSGRAD:
            return kandidat

    synlig = synlig_ai_svar(ai_tekst)
    mønstre = [
        r"([A-ZÆØÅ][\w'’\-\s]{2,60})\s*\(([^,()]{2,40}),\s*([^)]+)\)",
        r"([A-ZÆØÅ][\w'’\-]{2,40})\s+i\s+([A-ZÆØÅ][\w'’\-\s]{2,40}),\s*([A-ZÆØÅ][\w'’\-\s]{2,40})",
    ]
    funn = None
    for mønster in mønstre:
        treff = re.search(mønster, synlig)
        if treff:
            funn = {
                "navn": treff.group(1).strip(),
                "by": treff.group(2).strip(),
                "land": treff.group(3).strip(),
            }
            break
    if not funn:
        return None

    score = _score_saerhetstekst(synlig)
    if score < MIN_UNIKHETSGRAD:
        return None

    synlig_lc = synlig.lower()
    funn["saerhetsscore"] = score
    funn["beskrivelse"] = ""
    funn["agent_id"] = f"agent-{_slug_tekst(funn['navn'])}-{_slug_tekst(funn['by'])}-{_slug_tekst(funn['land'])}"
    funn = _juster_agent_perle_fra_chat_tekst(funn, ai_tekst)
    funn = _berik_kandidat_fra_db(funn)
    if funn.get("source_type") == "restaurant" and score < MIN_UNIKHETSGRAD:
        return None
    if funn.get("source_type") not in ("hotel", "restaurant"):
        funn["source_type"] = "hidden_gem"
        funn["type"] = "kultur"
    return funn


def agent_perle_til_reiseplan_sted(kandidat):
    """Gjør agent-perle om til sted-dict for reiseplanen."""
    lat, lon = hent_koordinater_for_sok(f"{kandidat['by']}, {kandidat['land']}")
    beskrivelse = (kandidat.get("beskrivelse") or "").strip()
    if not beskrivelse:
        beskrivelse = (
            f"Anbefalt av reiseeksperten. Særhetsscore: {kandidat['saerhetsscore']}/10."
        )
    return {
        "id": f"chat-{kandidat['agent_id']}",
        "navn": kandidat["navn"],
        "by": kandidat["by"],
        "land": kandidat["land"],
        "type": kandidat.get("type", "kultur"),
        "beskrivelse": beskrivelse,
        "latitude": lat,
        "longitude": lon,
        "tips": "",
        "beste_tid": "",
        "pris": "",
        "image_url": "",
        "country_code": "",
        "source_type": kandidat.get("source_type", "hidden_gem"),
    }


def render_chat_agent_perle_handlinger(kandidat, key_suffix, chat_tekst=""):
    """Viser lagre-i-db og legg-i-reiseplan for en oppdaget perle."""
    kandidat = _juster_agent_perle_fra_chat_tekst(dict(kandidat), chat_tekst)
    lagre_knapp, lagre_toast = _chat_lagre_tekster(kandidat)
    visning = _berik_kandidat_fra_db(dict(kandidat))
    if not visning.get("id"):
        visning["id"] = visning.get("agent_id", f"chat_{key_suffix}")
    st.caption(_chat_agent_oppdaget_tekst(kandidat))
    vis_sted_foto(visning, key_suffix=f"chat_{key_suffix}", autoload=True)
    chat_tittel = sted_tittel_med_profil(visning, _sted_emoji(visning))
    _vis_travel_card(visning, chat_tittel)
    col_plan, col_db = st.columns([1, 1])
    with col_plan:
        render_reiseplan_knapp_agent(kandidat, f"chat_{key_suffix}")
    with col_db:
        if st.button(
            lagre_knapp,
            key=f"chat_save_db_{key_suffix}",
            use_container_width=True,
        ):
            lagret = lagre_agent_perle_i_db(kandidat)
            _legg_lagret_sted_i_lokale_lister(lagret)
            st.toast(lagre_toast)
            st.rerun()


def lagre_agent_perle_i_db(kandidat):
    """Lagrer agent-forslag permanent i places-tabellen."""
    kandidat = _synk_kandidat_kilde_type(dict(kandidat))
    kandidat = _berik_agent_kandidat_for_lagring(kandidat)
    if kandidat.get("source_type") == "restaurant" and not _godkjent_restaurant_kandidat(
        kandidat, False
    ):
        kandidat["source_type"] = "hidden_gem"
        if kandidat.get("type") == "gastronomi":
            kandidat["type"] = "kultur"
    if kandidat.get("source_type") == "hotel" and not _godkjent_hotel_kandidat(
        kandidat, False
    ):
        kandidat["source_type"] = "hidden_gem"
        if kandidat.get("type") in ("hotell", "overnatting"):
            kandidat["type"] = "kultur"
    lat = kandidat.get("latitude")
    lon = kandidat.get("longitude")
    if lat is None or lon is None:
        lat, lon = hent_koordinater_for_sok(f"{kandidat['by']}, {kandidat['land']}")
    sted = {
        "id": f"{kandidat['source_type']}-agent-{_slug_tekst(kandidat['navn'])}-{_slug_tekst(kandidat['by'])}-{_slug_tekst(kandidat['land'])}",
        "navn": kandidat["navn"],
        "by": kandidat["by"],
        "land": kandidat["land"],
        "type": kandidat["type"],
        "profil_kategori": (
            "Mat & Vin"
            if kandidat["source_type"] == "restaurant"
            else "Kultur & Historie"
        ),
        "beskrivelse": (kandidat.get("beskrivelse") or "").strip()
        or f"Oppdaget av KI-agent i chatten. Særhetsscore: {kandidat['saerhetsscore']}/10.",
        "tips": "KI-agentforslag: sjekk stedet lokalt.",
        "beste_tid": "",
        "pris": (kandidat.get("pris") or "").strip() or "€€",
        "latitude": lat,
        "longitude": lon,
        "image_url": "",
        "country_code": "",
    }
    if kandidat.get("saerhetsscore") is not None:
        sted["saerhetsscore"] = kandidat["saerhetsscore"]
    normalisert = normalize_place(sted, kandidat["source_type"])
    _slett_sted_med_nokkel_i_db(kandidat["navn"], kandidat["by"], kandidat["land"])
    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO places (
                id, name, city, country, country_code, category, description,
                tips, best_time, price, latitude, longitude, source_type,
                search_key, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalisert["id"],
                normalisert["navn"],
                normalisert["by"],
                normalisert["land"],
                normalisert["country_code"],
                normalisert["type"],
                normalisert["beskrivelse"],
                normalisert["tips"],
                normalisert["beste_tid"],
                normalisert["pris"],
                normalisert["latitude"],
                normalisert["longitude"],
                normalisert["source_type"],
                normalisert["search_key"],
                json.dumps(normalisert, ensure_ascii=False),
            ),
        )
        conn.commit()
    return normalisert


def _legg_lagret_sted_i_lokale_lister(lagret):
    """Oppdaterer tellere og faner etter DB-lagring."""
    _oppfrisk_lokale_stedlister()


def _kilde_type_visning(kandidat):
    kilde = _effektiv_kilde_type(kandidat)
    if kilde == "hotel":
        return tr("type_hotell")
    if kilde == "restaurant":
        return tr("type_mat", "Spisested")
    return tr("type_perle", "Oppdagelse")


def _synk_kandidat_kilde_type(kandidat):
    """Sikrer at source_type og type stemmer før lagring/duplikatsjekk."""
    if not kandidat:
        return kandidat
    kilde = _effektiv_kilde_type(kandidat)
    kandidat["source_type"] = kilde
    if kilde == "hotel":
        kandidat["type"] = "overnatting"
    elif kilde == "restaurant":
        kandidat["type"] = "gastronomi"
    return kandidat


def _finn_synlig_sted(navn, by, land, onsket_kilde=None):
    """Finner sted som faktisk telles i appen (Utforsk-tallene)."""
    key = _perle_nokkel(navn, by, land)
    treff = [
        sted
        for sted in _alle_steder_i_databasen()
        if _perle_nokkel(sted.get("navn"), sted.get("by"), sted.get("land")) == key
    ]
    if not treff:
        return None
    if onsket_kilde:
        for sted in treff:
            if _effektiv_kilde_type(sted) == onsket_kilde:
                return sted
    return treff[0]


def _kandidat_lagringsstatus(kandidat):
    """Om KI-forslag kan lagres, allerede finnes synlig, eller bør oppgraderes."""
    kandidat = _synk_kandidat_kilde_type(dict(kandidat))
    ktype = _effektiv_kilde_type(kandidat)
    synlig = _finn_synlig_sted(
        kandidat.get("navn"), kandidat.get("by"), kandidat.get("land"), onsket_kilde=ktype
    ) or _finn_synlig_sted(kandidat.get("navn"), kandidat.get("by"), kandidat.get("land"))
    if not synlig:
        return {"kan_lagre": True, "allerede_synlig": False, "erstatter": False}
    eks_type = _effektiv_kilde_type(synlig)
    if eks_type == ktype:
        melding = {
            "hotel": "sank_allerede_i_db_hotell",
            "restaurant": "sank_allerede_i_db_mat",
        }.get(eks_type, "sank_allerede_i_db_perle")
        return {
            "kan_lagre": False,
            "allerede_synlig": True,
            "melding_nokkel": melding,
            "synlig_kategori": _kilde_type_visning(synlig),
        }
    erstatter_nokkel = {
        ("hidden_gem", "hotel"): "sank_oppgrader_til_hotell",
        ("hotel", "hidden_gem"): "sank_oppgrader_til_perle",
        ("hidden_gem", "restaurant"): "sank_oppgrader_til_mat",
        ("restaurant", "hidden_gem"): "sank_oppgrader_til_perle",
    }.get((eks_type, ktype), "sank_oppgraderer_type")
    return {
        "kan_lagre": True,
        "allerede_synlig": False,
        "erstatter": True,
        "erstatter_type": eks_type,
        "melding_nokkel": erstatter_nokkel,
    }


def _slett_sted_med_nokkel_i_db(navn, by, land):
    """Fjerner skjulte/utdaterte rader med samme navn+sted før ny lagring."""
    key = _perle_nokkel(navn, by, land)
    init_db()
    with get_connection() as conn:
        rows = conn.execute("SELECT id, name, city, country FROM places").fetchall()
        for row in rows:
            if _perle_nokkel(row[1], row[2], row[3]) == key:
                conn.execute("DELETE FROM places WHERE id = ?", (row[0],))
        conn.commit()


def _fiks_los_json_tekst(rå):
    """Retter vanlige AI-avvik før json.loads."""
    rå = (
        rå.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    return re.sub(r",(\s*[}\]])", r"\1", rå)


def _parse_json_array_fra_pos(tekst, start_idx):
    """Parser en JSON-liste ved bracket-matching fra '['."""
    depth = 0
    for i in range(start_idx, len(tekst)):
        tegn = tekst[i]
        if tegn == "[":
            depth += 1
        elif tegn == "]":
            depth -= 1
            if depth == 0:
                snippet = _fiks_los_json_tekst(tekst[start_idx : i + 1])
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError:
                    return None
    return None


def _parse_json_innhold(tekst):
    rå = (tekst or "").strip()
    if not rå:
        return None
    if rå.startswith("```"):
        rå = re.sub(r"^```(?:json)?\s*", "", rå, flags=re.IGNORECASE)
        rå = re.sub(r"\s*```\s*$", "", rå, flags=re.DOTALL)
    rå = _fiks_los_json_tekst(rå)
    for kandidat in (rå,):
        try:
            return json.loads(kandidat)
        except json.JSONDecodeError:
            pass
    start = rå.find("{")
    slutt = rå.rfind("}")
    if start != -1 and slutt != -1 and slutt > start:
        try:
            return json.loads(rå[start : slutt + 1])
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    for i, tegn in enumerate(rå):
        if tegn == "{":
            try:
                obj, _ = decoder.raw_decode(rå, i)
                return obj
            except json.JSONDecodeError:
                continue
    return None


def _hent_sank_kandidatliste(parsed, rå_tekst=""):
    """Henter kandidatliste fra parsede AI-svar (tåler varianter i nøkkelnavn)."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("kandidater", "candidates", "places", "results", "steder"):
            val = parsed.get(key)
            if isinstance(val, list):
                return val
    tekst = rå_tekst or ""
    for mønster in (r'"kandidater"\s*:\s*\[', r'"candidates"\s*:\s*\['):
        treff = re.search(mønster, tekst, flags=re.IGNORECASE)
        if treff:
            liste = _parse_json_array_fra_pos(tekst, treff.end() - 1)
            if isinstance(liste, list):
                return liste
    return None


def sanke_perler_for_omrade(omrade, antall=8, strict_mode=False):
    """Henter og kvalitetssikrer flere perlekandidater automatisk."""
    total_start = time.perf_counter()
    if not hent_openrouter_api_key():
        raise RuntimeError(tr("sank_mangler_api"))

    antall = max(3, min(20, int(antall)))
    min_score = MIN_UNIKHETSGRAD
    if _spraak == "EN":
        strict_hint = (
            " Strict mode is ON: prioritize lesser-known places and avoid obvious landmarks or mainstream resorts."
            if strict_mode
            else ""
        )
        system_prompt = (
            "You curate off-the-beaten-path places in Europe. "
            "Return strict JSON only with this shape: "
            '{"kandidater":[{"navn":"...","by":"...","land":"...","beskrivelse":"...",'
            f'"saerhetsscore":{MIN_UNIKHETSGRAD},"type":"kultur","source_type":"hidden_gem","pris":""}}]}} '
            f"Include exactly the requested number of candidates with saerhetsscore>={MIN_UNIKHETSGRAD}, "
            "avoid mainstream landmarks and well-visited blockbuster museums, and keep descriptions factual and concise. "
            "For overnight stays set type overnatting, source_type hotel, and pris with EUR double-room price "
            f"(e.g. 2800 kr) and saerhetsscore {MIN_UNIKHETSGRAD}. "
            f"Never suggest hotels below {MIN_UNIKHETSGRAD}/10 uniqueness."
            f"{strict_hint}"
            f"{_sank_ki_regler_hint(strict_mode)}"
        )
        user_prompt = f"Area: {omrade}. Number of candidates: {antall}."
    else:
        strict_hint = (
            " Streng modus er PÅ: prioriter mindre kjente steder og unngå åpenbare landemerker og mainstream-resorter."
            if strict_mode
            else ""
        )
        system_prompt = (
            "Du kuraterer skjulte perler i Europa. "
            "Returner kun gyldig JSON med format: "
            '{"kandidater":[{"navn":"...","by":"...","land":"...","beskrivelse":"...",'
            f'"saerhetsscore":{MIN_UNIKHETSGRAD},"type":"kultur","source_type":"hidden_gem","pris":""}}]}} '
            f"Gi nøyaktig antall kandidater med saerhetsscore>={MIN_UNIKHETSGRAD}, unngå mainstream landemerker og velbesøkte museer, "
            "og hold beskrivelser korte og faktabaserte. "
            "For overnatting: type overnatting, source_type hotel, og pris med dobbeltrom i EUR "
            f"(f.eks. 2800 kr) og saerhetsscore {MIN_UNIKHETSGRAD}. "
            f"Ikke foreslå hotell under {MIN_UNIKHETSGRAD}/10 unikhet."
            f"{strict_hint}"
            f"{_sank_ki_regler_hint(strict_mode)}"
        )
        user_prompt = f"Område: {omrade}. Antall kandidater: {antall}."

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": min(4096, 500 + antall * 320),
        "response_format": {"type": "json_object"},
    }
    try:
        response = _openrouter_post(payload, timeout=60)
    except requests.HTTPError as exc:
        if (
            exc.response is not None
            and exc.response.status_code == 400
            and "response_format" in payload
        ):
            payload_uten = {
                k: v for k, v in payload.items() if k != "response_format"
            }
            response = _openrouter_post(payload_uten, timeout=60)
        else:
            raise
    content = (
        response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    )
    parsed = _parse_json_innhold(content)
    rå_liste = _hent_sank_kandidatliste(parsed, content)
    if not isinstance(rå_liste, list):
        feil = tr("sank_parse_feil")
        if st.session_state.get("vis_perf_debug"):
            feil = f"{feil} (rå svar: {(content or '')[:400]})"
        raise RuntimeError(feil)

    nye_nokler = set()
    godkjente = []
    forkastet_duplikat = 0
    forkastet_score = 0
    forkastet_geo = 0
    forkastet_normalisering = 0
    kandidater_for_geo = []

    for rå in rå_liste:
        if not isinstance(rå, dict):
            forkastet_normalisering += 1
            continue
        rå = _berik_rå_sank_kandidat(rå, omrade)
        kandidat = _normaliser_agent_perle(rå, json.dumps(rå, ensure_ascii=False))
        if not kandidat:
            forkastet_normalisering += 1
            continue
        kandidat = _juster_agent_perle_fra_chat_tekst(
            kandidat, json.dumps(rå, ensure_ascii=False)
        )
        kandidat = _synk_kandidat_kilde_type(kandidat)

        ai_score = rå.get("saerhetsscore", rå.get("uniqueness_score"))
        if ai_score is not None:
            kandidat["saerhetsscore"] = _normaliser_saerhetsscore(ai_score, "")
        elif (
            kandidat.get("source_type") not in ("hotel", "restaurant")
            and kandidat.get("saerhetsscore", 0) < min_score
        ):
            kandidat["saerhetsscore"] = min_score

        if not _sank_passer_kvalitet(kandidat, strict_mode):
            forkastet_score += 1
            continue

        key = _perle_nokkel(kandidat["navn"], kandidat["by"], kandidat["land"])
        lagringsstatus = _kandidat_lagringsstatus(kandidat)
        kandidat["lagringsstatus"] = lagringsstatus
        if lagringsstatus["allerede_synlig"]:
            forkastet_duplikat += 1
            kandidat["allerede_i_db"] = True
        elif key in nye_nokler:
            forkastet_duplikat += 1
            kandidat["allerede_i_db"] = True
        else:
            nye_nokler.add(key)
            kandidat["allerede_i_db"] = False
        kandidater_for_geo.append({"kandidat": kandidat, "key": key})

    async def _geokod_kandidat_async(kandidat):
        lat, lon = await hent_koordinater_for_sok_async(
            f"{kandidat['navn']}, {kandidat['by']}, {kandidat['land']}"
        )
        if lat is None or lon is None:
            lat, lon = await hent_koordinater_for_sok_async(f"{kandidat['by']}, {kandidat['land']}")
        return lat, lon

    async def _geokod_alle_kandidater_async(items, max_parallel=1):
        sem = asyncio.Semaphore(max_parallel)

        async def _worker(item):
            async with sem:
                lat, lon = await _geokod_kandidat_async(item["kandidat"])
                await asyncio.sleep(1.1)
                return item, lat, lon

        return await asyncio.gather(*(_worker(item) for item in items))

    geo_start = time.perf_counter()
    geokodede = _run_async(_geokod_alle_kandidater_async(kandidater_for_geo))
    geo_elapsed_s = time.perf_counter() - geo_start
    for item, lat, lon in geokodede:
        kandidat = item["kandidat"]
        if lat is None or lon is None:
            forkastet_geo += 1
        else:
            kandidat["latitude"] = lat
            kandidat["longitude"] = lon
        godkjente.append(kandidat)

    rapport = {
        "foreslaatt": len(rå_liste),
        "godkjent": len(godkjente),
        "forkastet_duplikat": forkastet_duplikat,
        "forkastet_score": forkastet_score,
        "forkastet_geo": forkastet_geo,
        "forkastet_normalisering": forkastet_normalisering,
        "tid_geo_s": round(geo_elapsed_s, 3),
        "tid_total_s": round(time.perf_counter() - total_start, 3),
    }
    return godkjente, rapport


def _helgeby_nokkel(by, land):
    return f"{(by or '').strip().lower()}|{(land or '').strip().lower()}"


def _hent_helgeby_kandidatliste(parsed, rå_tekst=""):
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("helgebyer", "byer", "towns", "kandidater", "candidates"):
            val = parsed.get(key)
            if isinstance(val, list):
                return val
    tekst = rå_tekst or ""
    for mønster in (r'"helgebyer"\s*:\s*\[', r'"byer"\s*:\s*\[', r'"towns"\s*:\s*\['):
        treff = re.search(mønster, tekst, flags=re.IGNORECASE)
        if treff:
            liste = _parse_json_array_fra_pos(tekst, treff.end() - 1)
            if isinstance(liste, list):
                return liste
    return None


def _berik_rå_helgeby_kandidat(rå, omrade=""):
    data = _berik_rå_sank_kandidat(rå, omrade)
    bynavn = (
        data.get("by")
        or data.get("navn")
        or data.get("name")
        or data.get("city")
        or data.get("town")
        or ""
    ).strip()
    if bynavn:
        data["navn"] = bynavn
        data["by"] = bynavn
    data["type"] = "by"
    data["source_type"] = "hidden_gem"
    hvorfor = (data.get("hvorfor_helg") or data.get("weekend_why") or "").strip()
    if hvorfor and not (data.get("beskrivelse") or "").strip():
        data["beskrivelse"] = hvorfor
    return data


def _hent_helgeby_hotell_liste_fra_rå(rå):
    """Henter hotell-liste fra ulike KI-feltnavn."""
    if not isinstance(rå, dict):
        return []
    for key in (
        "hoteller",
        "hotels",
        "overnatting",
        "overnattinger",
        "stays",
        "accommodation",
        "accommodations",
        "hotell",
    ):
        val = rå.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            return [val]
    return []


def _normaliser_helgeby_hotell(rå_hotell, by, land, omrade=""):
    if isinstance(rå_hotell, str):
        navn = rå_hotell.strip()
        if not navn:
            return None
        rå_hotell = {"navn": navn, "beskrivelse": navn}
    if not isinstance(rå_hotell, dict):
        return None
    data = _berik_rå_sank_kandidat(rå_hotell, omrade)
    navn = (
        data.get("navn")
        or data.get("name")
        or data.get("hotel")
        or data.get("title")
        or ""
    ).strip()
    if not navn:
        return None
    data["navn"] = navn
    data["by"] = by
    data["land"] = land
    data["type"] = "overnatting"
    data["source_type"] = "hotel"
    if not (data.get("beskrivelse") or data.get("description") or "").strip():
        data["beskrivelse"] = (
            rå_hotell.get("hvorfor")
            or rå_hotell.get("why")
            or rå_hotell.get("beskrivelse")
            or rå_hotell.get("description")
            or navn
        )
    kandidat = _normaliser_agent_perle(data, json.dumps(data, ensure_ascii=False))
    if not kandidat:
        return None
    kandidat["type"] = "overnatting"
    kandidat["source_type"] = "hotel"
    ai_score = rå_hotell.get("saerhetsscore", rå_hotell.get("uniqueness_score"))
    if ai_score is not None:
        kandidat["saerhetsscore"] = _normaliser_saerhetsscore(ai_score, "")
    pris = (rå_hotell.get("pris") or rå_hotell.get("price") or "").strip()
    if pris:
        kandidat["pris"] = pris
    return kandidat


def _normaliser_helgeby_hoteller(rå, by, land, omrade=""):
    hoteller = []
    for rå_hotell in _hent_helgeby_hotell_liste_fra_rå(rå):
        hotell = _normaliser_helgeby_hotell(rå_hotell, by, land, omrade)
        if hotell:
            hoteller.append(hotell)
    return hoteller


def _godkjent_helgeby_hotell(hotell, strict_mode=False):
    """Helgeby-hotell: litt mildere enn generell overnatting, men fortsatt unike steder."""
    from place_quality import er_kjede_hotell, tekst_for_sted_sjekk

    if not isinstance(hotell, dict) or hotell.get("source_type") != "hotel":
        return False
    tekst = tekst_for_sted_sjekk(hotell)
    if er_kjede_hotell(tekst):
        return False
    if hotell.get("saerhetsscore", 0) < MIN_UNIKHETSGRAD:
        return False
    min_besk = 25 if strict_mode else 15
    if len((hotell.get("beskrivelse") or "").strip()) < min_besk:
        return False
    return True


def _helgeby_hoteller_passer(hoteller, strict_mode=False):
    """Krever nøyaktig to ulike, godkjente overnattingssteder."""
    if not isinstance(hoteller, list) or len(hoteller) != 2:
        return False
    navn_set = set()
    for hotell in hoteller:
        if not _godkjent_helgeby_hotell(hotell, strict_mode):
            return False
        navn_key = _slug_tekst(hotell.get("navn", ""))
        if not navn_key or navn_key in navn_set:
            return False
        navn_set.add(navn_key)
    return True


def _normaliser_helgeby_kandidat(rå, omrade=""):
    data = _berik_rå_helgeby_kandidat(rå, omrade)
    kandidat = _normaliser_agent_perle(data, json.dumps(data, ensure_ascii=False))
    if not kandidat:
        return None
    kandidat["type"] = "by"
    kandidat["hvorfor_helg"] = (
        rå.get("hvorfor_helg") or rå.get("weekend_why") or kandidat.get("beskrivelse") or ""
    ).strip()
    highlights = rå.get("highlights") or rå.get("hoydepunkter") or []
    if isinstance(highlights, list):
        kandidat["highlights"] = [str(h).strip() for h in highlights if str(h).strip()]
    else:
        kandidat["highlights"] = []
    kandidat["beste_tid"] = (rå.get("beste_tid") or rå.get("best_time") or "").strip()
    kandidat["hoteller"] = _normaliser_helgeby_hoteller(
        rå, kandidat["by"], kandidat["land"], omrade
    )
    return kandidat


def _helgeby_kvalitet_grunn(kandidat, strict_mode=False):
    if _er_mainstream_turistdestinasjon(kandidat):
        return "mainstream"
    if _er_blant_landets_storste_byer(kandidat):
        return "storby"
    if _er_velbesokt_museum(kandidat):
        return "score"
    if kandidat.get("saerhetsscore", 0) < MIN_UNIKHETSGRAD:
        return "score"
    beskrivelse = (kandidat.get("beskrivelse") or kandidat.get("hvorfor_helg") or "").strip()
    min_len = 50 if strict_mode else 35
    if len(beskrivelse) < min_len:
        return "score"
    if not _helgeby_hoteller_passer(kandidat.get("hoteller"), strict_mode):
        return "hotell"
    return None


def _helgeby_passer_kvalitet(kandidat, strict_mode=False):
    return _helgeby_kvalitet_grunn(kandidat, strict_mode) is None


def sanke_helgebyer_for_omrade(omrade, antall=5, strict_mode=False):
    """Finner små/mellomstore byer verdt en helg — utenfor mainstream turisme."""
    total_start = time.perf_counter()
    if not hent_openrouter_api_key():
        raise RuntimeError(tr("sank_mangler_api"))

    antall = max(2, min(10, int(antall)))
    hotell_min = MIN_UNIKHETSGRAD
    hotell_json = (
        '"hoteller":[{"navn":"Unique stay name","beskrivelse":"Why it is special and worth a weekend stay...",'
        '"saerhetsscore":' + str(hotell_min) + ',"pris":"EUR 95","type":"overnatting","source_type":"hotel"},'
        '{"navn":"Second unique stay","beskrivelse":"A different style of lodging with local character...",'
        '"saerhetsscore":' + str(hotell_min) + ',"pris":"EUR 110","type":"overnatting","source_type":"hotel"}]'
    )

    helgeby_json_eksempel = (
        '{"helgebyer":[{"navn":"Town name","land":"Country","beskrivelse":"...",'
        '"hvorfor_helg":"Why it works for a weekend","saerhetsscore":'
        + str(MIN_UNIKHETSGRAD)
        + ',"highlights":["walk","food","nature"],"beste_tid":"May–Sep","type":"by",'
        + hotell_json
        + "}]}"
    )

    if _spraak == "EN":
        system_prompt = (
            "You find off-the-beaten-path towns and small cities in Europe worth a 2–3 night weekend. "
            "Return strict JSON only: "
            + helgeby_json_eksempel
            + " "
            f"Suggest exactly {antall} towns with saerhetsscore>={MIN_UNIKHETSGRAD}. "
            "Each entry must be a whole town/city (navn = by). "
            f"Each town MUST include exactly 2 completely different unique stays in hoteller "
            f"(source_type hotel, type overnatting, saerhetsscore>={hotell_min}, no chains, "
            "quirky/historic/design — e.g. parsonage, cave hotel, lighthouse). "
            "The two hotels must be genuinely different places, not the same brand or property. "
            "Prefer walkable old towns, local food, nature nearby, festivals or crafts — "
            "places with real character but NOT mass tourism. "
            "NEVER suggest capitals or bucket-list cities (Paris, Rome, Barcelona, Amsterdam, Venice, Prague, etc.). "
            "NEVER suggest any of a country's 5 largest cities (e.g. in Italy: Rome, Milan, Naples, Turin, Palermo)."
        )
        user_prompt = f"Region: {omrade}. Number of towns: {antall}."
    else:
        helgeby_json_eksempel = (
            '{"helgebyer":[{"navn":"Bynavn","land":"Land","beskrivelse":"...",'
            '"hvorfor_helg":"Hvorfor det passer som helg","saerhetsscore":'
            + str(MIN_UNIKHETSGRAD)
            + ',"highlights":["gåtur","mat","natur"],"beste_tid":"mai–sep","type":"by",'
            + hotell_json
            + "}]}"
        )
        system_prompt = (
            "Du finner off-the-beaten-path byer og tettsteder i Europa som er verdt en helg (2–3 netter). "
            "Returner kun gyldig JSON: "
            + helgeby_json_eksempel
            + " "
            f"Foreslå nøyaktig {antall} byer med saerhetsscore>={MIN_UNIKHETSGRAD}. "
            "Hvert forslag skal være en hel by (navn = by). "
            f"Hver by MÅ ha nøyaktig 2 helt ulike overnattingssteder i hoteller-listen "
            f"(source_type hotel, type overnatting, saerhetsscore>={hotell_min}, ingen kjeder, "
            "særegen historie/design — f.eks. prestegård, grottehotell, fyrvokterbolig). "
            "De to hotellene skal være forskjellige steder, ikke samme merke eller eiendom. "
            "Prioriter gåbare gamlebyer, lokal mat, natur i nærheten, håndverk eller festivaler — "
            "steder med sjel, men IKKE masseturisme. "
            "ALDRI foreslå hovedsteder eller bucket-list-byer (Paris, Roma, Barcelona, Amsterdam, Venezia, Praha osv.). "
            "ALDRI foreslå noen av landets 5 største byer (f.eks. i Italia: Roma, Milano, Napoli, Torino, Palermo)."
        )
        user_prompt = f"Region: {omrade}. Antall byer: {antall}."

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.45,
        "max_tokens": min(4096, 500 + antall * 720),
        "response_format": {"type": "json_object"},
    }
    try:
        response = _openrouter_post(payload, timeout=60)
    except requests.HTTPError as exc:
        if (
            exc.response is not None
            and exc.response.status_code == 400
            and "response_format" in payload
        ):
            payload_uten = {k: v for k, v in payload.items() if k != "response_format"}
            response = _openrouter_post(payload_uten, timeout=60)
        else:
            raise

    content = (
        response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    )
    parsed = _parse_json_innhold(content)
    rå_liste = _hent_helgeby_kandidatliste(parsed, content)
    if not isinstance(rå_liste, list):
        raise RuntimeError(tr("sank_parse_feil"))

    eksisterende = _hent_eksisterende_helgeby_nokler()
    nye_nokler = set()
    godkjente = []
    forkastet_mainstream = 0
    forkastet_storby = 0
    forkastet_hotell = 0
    forkastet_score = 0
    forkastet_normalisering = 0
    kandidater_for_geo = []

    for rå in rå_liste:
        if not isinstance(rå, dict):
            forkastet_normalisering += 1
            continue
        kandidat = _normaliser_helgeby_kandidat(rå, omrade)
        if not kandidat:
            forkastet_normalisering += 1
            continue
        ai_score = rå.get("saerhetsscore", rå.get("uniqueness_score"))
        if ai_score is not None:
            kandidat["saerhetsscore"] = _normaliser_saerhetsscore(ai_score, "")
        grunn = _helgeby_kvalitet_grunn(kandidat, strict_mode)
        if grunn:
            if grunn == "mainstream":
                forkastet_mainstream += 1
            elif grunn == "storby":
                forkastet_storby += 1
            elif grunn == "hotell":
                forkastet_hotell += 1
            else:
                forkastet_score += 1
            continue
        key = _helgeby_nokkel(kandidat["by"], kandidat["land"])
        if key in eksisterende or key in nye_nokler:
            kandidat["allerede_i_db"] = True
        else:
            nye_nokler.add(key)
        kandidater_for_geo.append(kandidat)

    async def _geokod_by_async(kandidat):
        lat, lon = await hent_koordinater_for_sok_async(
            f"{kandidat['by']}, {kandidat['land']}"
        )
        return lat, lon

    async def _geokod_alle_async(items, max_parallel=1):
        sem = asyncio.Semaphore(max_parallel)

        async def _worker(kandidat):
            async with sem:
                lat, lon = await _geokod_by_async(kandidat)
                await asyncio.sleep(1.1)
                return kandidat, lat, lon

        return await asyncio.gather(*(_worker(k) for k in items))

    geokodede = _run_async(_geokod_alle_async(kandidater_for_geo))
    for kandidat, lat, lon in geokodede:
        if lat is not None and lon is not None:
            kandidat["latitude"] = lat
            kandidat["longitude"] = lon
        godkjente.append(kandidat)

    rapport = {
        "foreslaatt": len(rå_liste),
        "godkjent": len(godkjente),
        "forkastet_mainstream": forkastet_mainstream,
        "forkastet_storby": forkastet_storby,
        "forkastet_hotell": forkastet_hotell,
        "forkastet_score": forkastet_score,
        "forkastet_normalisering": forkastet_normalisering,
        "tid_total_s": round(time.perf_counter() - total_start, 3),
    }
    return godkjente, rapport


def _hent_eksisterende_helgeby_nokler():
    keys = set()
    for sted in _alle_steder_i_databasen():
        by = (sted.get("by") or "").strip()
        land = (sted.get("land") or "").strip()
        if by and land:
            keys.add(_helgeby_nokkel(by, land))
    return keys


def generer_reiseekspert_stream(sporsmal, kontekst=""):
    """Generator-funksjon for å streame AI-svar fra OpenRouter m/ RAG-databasekobling"""
    if not hent_openrouter_api_key():
        yield f"⚠️ **Systemmelding:** {tr('sank_mangler_api')}"
        return

    profil = _normaliser_profil(st.session_state.get("profil"))
    reise_folge = profil["reise_folge"]
    budsjett = profil["budsjett"]

    intern_kontekst = _bygg_rag_kontekst(sporsmal)

    if _spraak == "EN":
        json_instruks = (
            "Always end your answer with this exact line on its own: ||PERLE_JSON|| "
            "Then one line of valid JSON (no markdown). Pick one main recommendation and match source_type: "
            '{"perle":{"navn":"...","by":"...","land":"...","beskrivelse":"...","saerhetsscore":9,'
            '"type":"kultur","source_type":"hidden_gem"}} OR for a stay: '
            '{"perle":{"navn":"...","by":"...","land":"...","beskrivelse":"...","saerhetsscore":9,'
            '"type":"overnatting","source_type":"hotel","pris":"EUR 95"}} OR for food: '
            '"type":"gastronomi","source_type":"restaurant". '
            f"Use saerhetsscore below {MIN_UNIKHETSGRAD} for mainstream places."
        )
        system_melding = (
            "You are the AI agent for Hidden Europe: hidden gems and eccentric destinations. "
            f"The user travels as {reise_folge} on a {budsjett} budget. "
            "Suggest off-the-beaten-path places with local character. "
            "Avoid mainstream tourism, iconic defaults like the Eiffel Tower, and well-visited blockbuster museums. "
            "Reply briefly and enthusiastically (max 5 sentences). "
            "Mention places as: Name (City, Country). "
            "If database context lists places, prefer recommending one of them with exact name and city — do not invent similar places. "
            f"{_restaurant_ki_hint(False)}"
            f"{json_instruks}"
            f"{intern_kontekst}"
        )
    else:
        json_instruks = (
            "Avslutt ALLTID svaret med nøyaktig denne linjen alene: ||PERLE_JSON|| "
            "Deretter én linje gyldig JSON (uten markdown). Velg én hovedanbefaling og riktig source_type: "
            '{"perle":{"navn":"...","by":"...","land":"...","beskrivelse":"...","saerhetsscore":9,'
            '"type":"kultur","source_type":"hidden_gem"}} ELLER for overnatting: '
            '{"perle":{"navn":"...","by":"...","land":"...","beskrivelse":"...","saerhetsscore":9,'
            '"type":"overnatting","source_type":"hotel","pris":"EUR 95"}} ELLER for mat: '
            '"type":"gastronomi","source_type":"restaurant". '
            f"Bruk saerhetsscore under {MIN_UNIKHETSGRAD} for mainstream-steder."
        )
        system_melding = (
            "Du er KI-agenten for Hemmelige Europa: skjulte perler og eksentriske reisemål. "
            f"Brukeren reiser som {reise_folge} med et {budsjett}-budsjett. "
            "Foreslå aktivt off-the-beaten-path-steder med lokal karakter, særegen historie eller quirky opplevelser. "
            "Unngå mainstream turisme, typiske turistfeller, store resorter, velbesøkte museer og ikoniske standardvalg som Eiffeltårnet. "
            "Svar kort, engasjerende, spesifikt og entusiastisk (maks 5 setninger). "
            "Nevn gjerne konkrete steder med formatet: Sted (By, Land). "
            "Hvis database-kontekst lister steder, prioriter å anbefale ett av dem med eksakt navn og by — ikke finn opp lignende steder. "
            f"{_restaurant_ki_hint(False)}"
            f"{json_instruks}"
            f"{intern_kontekst}"
        )

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_melding},
            {"role": "user", "content": f"{kontekst}\n\n{sporsmal}"},
        ],
        "max_tokens": 650,
        "stream": True,
    }

    try:
        response = _openrouter_post(payload, timeout=30, stream=True)

        accum = ""
        yielded_len = 0
        for line in response.iter_lines():
            if line:
                cleaned_line = line.decode("utf-8").replace("data: ", "")
                if cleaned_line.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(cleaned_line)
                    content = chunk["choices"][0]["delta"].get("content", "")
                    if not content:
                        continue
                    accum += content
                    synlig = (
                        accum.split(AGENT_PERLE_MARKER, 1)[0]
                        if AGENT_PERLE_MARKER in accum
                        else accum
                    )
                    if len(synlig) > yielded_len:
                        yield synlig[yielded_len:]
                        yielded_len = len(synlig)
                except Exception:
                    continue
    except Exception as e:
        yield f"Kunne ikke kontakte reiseeksperten. Feilmelding: {str(e)}"


def analyser_reisestil(historikk):
    if not historikk:
        return "Ingen reiser registrert."
    typer = [r["type"] for r in historikk]
    land = [r.get("land", "") for r in historikk if r.get("land")]
    favoritt_type = max(set(typer), key=typer.count) if typer else "variert"
    favoritt_land = max(set(land), key=land.count) if land else "flere"
    return f"🎯 **Din reisestil:** Fokus på *{favoritt_type}*-opplevelser, med forkjærlighet for *{favoritt_land}*. ({len(historikk)} reiser registrert)"


def finn_lignende_steder(historikk, alle_steder):
    if not historikk:
        return []
    favoritt_typer = list(set(r["type"] for r in historikk))
    anbefalinger = []
    for sted in alle_steder:
        if sted["type"] in favoritt_typer:
            if not any(r["navn"] == sted["navn"] for r in historikk):
                sted["match_arsak"] = f"Matcher din interesse for **{sted['type']}**"
                anbefalinger.append(sted)
    return anbefalinger[:5]


@st.cache_data(ttl=60 * 60 * 24 * 30, show_spinner=False)
def _hent_sted_bilde_url_cached(sted_id, navn, by, land, latitude, longitude, sted_type, profil_kategori, image_url):
    """Cachet bildeoppslag per sted (30 dager)."""
    return hent_sted_bilde_url(
        {
            "id": sted_id,
            "navn": navn,
            "by": by,
            "land": land,
            "latitude": latitude,
            "longitude": longitude,
            "type": sted_type,
            "profil_kategori": profil_kategori,
            "image_url": image_url or "",
        }
    )


def _hent_sted_bilde_for_visning(sted):
    pid = sted.get("id", "")
    cache = st.session_state.setdefault("_bilde_url_cache", {})
    if pid and pid in cache:
        return cache[pid]
    url = _hent_sted_bilde_url_cached(
        pid,
        sted.get("navn", ""),
        sted.get("by", ""),
        sted.get("land", ""),
        sted.get("latitude"),
        sted.get("longitude"),
        sted.get("type", ""),
        sted.get("profil_kategori", ""),
        sted.get("image_url", ""),
    )
    if pid and url:
        cache[pid] = url
    return url


def render_affiliate_lenker(sted, source_view, index=None):
    _ui_render_affiliate_lenker(
        sted, source_view, index, tr, _effektiv_kilde_type, _spraak
    )


def vis_sted_foto(sted, key_suffix="", *, autoload=False):
    _ui_vis_sted_foto(
        sted, key_suffix, tr, _hent_sted_bilde_for_visning, autoload=autoload
    )


def render_reiseplan_knapp(place, key_prefix, index=None):
    """Kompakt pill-knapp for å legge sted i reiseplanen."""
    key = f"plan_{key_prefix}_{place['id']}"
    if index is not None:
        key = f"{key}_{index}"
    if st.button(
        tr("favoritt_knapp"),
        key=key,
        type="secondary",
    ):
        add_itinerary_item(place)
        persist_reiseplan()
        st.toast(tr("favoritt_lagt_til"))


def render_reiseplan_knapp_agent(kandidat, key_suffix):
    if st.button(
        tr("favoritt_knapp"),
        key=f"plan_{key_suffix}",
        type="secondary",
    ):
        add_itinerary_item(agent_perle_til_reiseplan_sted(kandidat))
        persist_reiseplan()
        st.toast(tr("favoritt_lagt_til"))


def beregn_radar_filtrering(
    alle_steder,
    *,
    soke_metode,
    metode_sted,
    metode_gps,
    metode_land,
    maks_avstand,
    vis_alle_perler,
    sted_sok="",
    valgt_land=None,
    gps_sentrum_navn="GPS",
):
    """Beregner radartreff, søkesentrum og nærmeste-perle-tekst for startsiden."""
    filtrert = filtrer_data(alle_steder)
    treff = []
    sentrum = None
    sentrum_navn = ""
    naermeste_perle_tekst = ""

    if soke_metode == metode_sted:
        sted_sok = (sted_sok or "").strip()
        if not sted_sok:
            return treff, sentrum, sentrum_navn, naermeste_perle_tekst
        lat, lon = hent_koordinater_for_sok(sted_sok)
        sentrum_navn = sted_sok
        if lat is None or lon is None:
            return treff, sentrum, sentrum_navn, naermeste_perle_tekst
        sentrum = (lat, lon)
        for perle in filtrert:
            plat, plon = perle.get("latitude"), perle.get("longitude")
            if plat is None or plon is None:
                continue
            avstand = regn_ut_avstand_km(lat, lon, float(plat), float(plon))
            if vis_alle_perler or avstand <= maks_avstand:
                treff.append({"data": perle, "avstand": round(avstand, 1)})
    elif soke_metode == metode_gps:
        geo = get_geolocation()
        if not (geo and "coords" in geo):
            return treff, sentrum, sentrum_navn, naermeste_perle_tekst
        lat = geo["coords"]["latitude"]
        lon = geo["coords"]["longitude"]
        sentrum = (lat, lon)
        sentrum_navn = gps_sentrum_navn
        for perle in filtrert:
            plat, plon = perle.get("latitude"), perle.get("longitude")
            if plat is None or plon is None:
                continue
            avstand = regn_ut_avstand_km(lat, lon, float(plat), float(plon))
            if vis_alle_perler or avstand <= maks_avstand:
                treff.append({"data": perle, "avstand": round(avstand, 1)})
    else:
        if not valgt_land:
            return treff, sentrum, sentrum_navn, naermeste_perle_tekst
        sentrum_navn = valgt_land
        land_perler = [p for p in filtrert if p["land"] == valgt_land]
        if not land_perler:
            naermeste_perle_tekst = tr("radar_ingen_perler_land")
            return treff, sentrum, sentrum_navn, naermeste_perle_tekst
        geo = get_geolocation()
        if geo and "coords" in geo:
            gps_lat = geo["coords"]["latitude"]
            gps_lon = geo["coords"]["longitude"]
            min_avstand = None
            naermeste_sted = None
            for perle in land_perler:
                plat, plon = perle.get("latitude"), perle.get("longitude")
                if plat is None or plon is None:
                    continue
                avstand = regn_ut_avstand_km(gps_lat, gps_lon, float(plat), float(plon))
                if min_avstand is None or avstand < min_avstand:
                    min_avstand = avstand
                    naermeste_sted = perle
                treff.append({"data": perle, "avstand": round(avstand, 1)})
            if naermeste_sted and min_avstand is not None:
                naermeste_perle_tekst = tr("radar_naermeste_perle").format(
                    naermeste_sted["by"], int(min_avstand)
                )
        else:
            naermeste_perle_tekst = tr("radar_gps_mangler_naermeste")
            for perle in land_perler:
                treff.append({"data": perle, "avstand": 0})

    if treff and sentrum is None:
        lat_sum = sum(float(t["data"]["latitude"]) for t in treff)
        lon_sum = sum(float(t["data"]["longitude"]) for t in treff)
        n = len(treff)
        sentrum = (lat_sum / n, lon_sum / n)

    treff.sort(key=lambda x: x["avstand"])
    return treff, sentrum, sentrum_navn, naermeste_perle_tekst


def lag_reiseplan_html(items):
    rows = []
    for item in items:
        rows.append(
            f"<li><strong>{item['navn']}</strong> – {item['by']}, {item['land']}<br>{item['beskrivelse']}</li>"
        )
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; padding: 32px;">
        <h1>Hemmelige Europa – reiseplan</h1>
        <ol>{''.join(rows)}</ol>
    </body>
    </html>
    """


def generer_ai_reiserute(items, dager):
    if not items:
        yield T["reiseplan_tom"]
        return

    steder = "\n".join(
        [
            f"- {item['navn']} ({item['by']}, {item['land']}): {item['beskrivelse']} Tips: {item.get('tips', '')}"
            for item in items
        ]
    )
    prompt = (
        f"Lag en konkret {dager}-dagers reiserute basert på disse lagrede Radar/favoritt-stedene. "
        "Prioriter korte transportetapper, skjulte perler og lokale spisesteder. "
        f"Svar på {'norsk' if spraak == 'NO' else 'English'}.\n\n{steder}"
    )
    yield from generer_reiseekspert_stream(prompt)


def _alle_steder_i_databasen():
    return SKJULTE_PERLER_DB + LOKALE_SPISESTEDER_DB + LOKALE_HOTELLER_DB


def _sted_emoji(sted):
    if sted.get("source_type") == "restaurant":
        return "🍽️"
    if sted.get("source_type") == "hotel":
        return "🛏️"
    return "🏛️"


def _normaliser_for_sok(tekst):
    """Aksent-uavhengig søk (malaga matcher Málaga)."""
    tekst = (tekst or "").lower().strip()
    tekst = unicodedata.normalize("NFD", tekst)
    return "".join(c for c in tekst if unicodedata.category(c) != "Mn")


def _sted_matcher_sok(sted, soketekst):
    if not soketekst:
        return True
    norm_sok = _normaliser_for_sok(soketekst)
    if not norm_sok:
        return True
    blob = _normaliser_for_sok(
        " ".join(
            str(sted.get(felt, "") or "")
            for felt in ("navn", "by", "land", "beskrivelse")
        )
    )
    return norm_sok in blob


def _filtrer_perler_liste(soketekst, type_filter, alle_type_label, radar_treff=None):
    """Perlesøk i hele databasen; uten søk vises alle skjulte perler."""
    kandidater = SKJULTE_PERLER_DB
    filtrert = []
    for perle in kandidater:
        if type_filter != alle_type_label and perle.get("type") != type_filter:
            continue
        if not _sted_matcher_sok(perle, soketekst):
            continue
        filtrert.append(perle)
    return filtrert


def _grupper_oppdagelser_pa_land(steder):
    """Grupperer oppdagelser alfabetisk på land."""
    grupper = {}
    for sted in steder:
        land = (sted.get("land") or "").strip() or tr("radar_region_default")
        grupper.setdefault(land, []).append(sted)
    return {
        land: sorted(steder_i_land, key=lambda s: (s.get("navn") or "").lower())
        for land, steder_i_land in sorted(grupper.items(), key=lambda x: x[0].lower())
    }


def _hent_tilfeldig_oppdagelse(steder, land_filter=None, alle_land_label=None):
    """Velger én tilfeldig oppdagelse, valgfritt filtrert på land."""
    alle_land_label = alle_land_label or T["perler_alle"]
    pool = list(steder)
    if land_filter and land_filter != alle_land_label:
        pool = [s for s in pool if s.get("land") == land_filter]
    if not pool:
        return None
    return random.choice(pool)


def _matcher_overnatting_type(valgt_type, sted_type):
    """Typefilter på overnatting-fanen (hotell/overnatting er samme kategori)."""
    if valgt_type == T["perler_alle"]:
        return True
    st = (sted_type or "").strip().lower()
    v = (valgt_type or "").strip().lower()
    if v in ("hotell", "overnatting", "hotel", "lodging") and st in (
        "hotell",
        "overnatting",
        "hotel",
        "lodging",
    ):
        return True
    return st == v


def vis_sted_type(sted):
    """Viser pene type-etiketter i stedet for rå databaseverdier."""
    raw = (sted.get("profil_kategori") or sted.get("type") or "").strip().lower()
    mapping = {
        "kultur": "type_kultur",
        "natur": "type_natur",
        "gastronomi": "type_gastronomi",
        "hotell": "type_hotell",
        "overnatting": "type_hotell",
        "hotel": "type_hotell",
        "historie": "type_historie",
        "eventyr": "type_eventyr",
        "museum": "type_museum",
        "arkitektur": "type_arkitektur",
        "mat & vin": "type_gastronomi",
        "kultur & historie": "type_kultur",
        "natur & aktivitet": "type_natur",
    }
    key = mapping.get(raw)
    if key:
        return tr(key)
    if raw:
        return raw.replace("_", " ").capitalize()
    return ""


def bygg_radar_ki_innsikt(valgt_land, radar_treff):
    """Lager dynamisk innsikt for radar ved landvalg."""
    if not valgt_land:
        return ""
    antall = len(radar_treff or [])
    if antall == 0:
        return tr("radar_innsikt_tom").format(valgt_land)

    return tr("radar_innsikt_treff").format(valgt_land, antall)


def sted_tittel_med_profil(sted, standard_emoji):
    """Tittel med valgfri emoji foran stedsnavn."""
    navn = sted.get("navn", "")
    emoji_del = f"{standard_emoji} " if standard_emoji else ""
    return f"{emoji_del}{navn}".strip()


def _render_travel_card_html(sted, tittel, *, pris_tekst=None):
    return _ui_render_travel_card_html(
        sted, tittel, tr, vis_sted_type, pris_tekst=pris_tekst
    )


def _vis_travel_card(sted, tittel, *, pris_tekst=None):
    vis_travel_card_html(_render_travel_card_html(sted, tittel, pris_tekst=pris_tekst))


# ========================================
# RAG: GEO + PROFIL (REISECHAT)
# ========================================
RAG_RADIUS_KM = 100
RAG_MAX_TREFF = 4


def _sted_har_koordinater(sted):
    try:
        lat = sted.get("latitude")
        lon = sted.get("longitude")
        return lat is not None and lon is not None
    except Exception:
        return False


def _ekstraher_stedsnavn_fra_sporsmal(sporsmal):
    """Finner mest sannsynlig stedsnavn nevnt i spørsmålet via databasen."""
    sporsmal_lav = (sporsmal or "").lower()
    if not sporsmal_lav:
        return None

    kandidater = []
    for sted in _alle_steder_i_databasen():
        for felt in ("by", "land", "navn"):
            navn = (sted.get(felt) or "").strip()
            if len(navn) >= 3 and navn.lower() in sporsmal_lav:
                kandidater.append((len(navn), navn))

    if not kandidater:
        return None
    return max(kandidater, key=lambda x: x[0])[1]


def _hent_lokasjon_fra_sporsmal(sporsmal):
    """Returnerer (lat, lon, stedsnavn) — maks to raske Nominatim-oppslag."""
    sporsmal = (sporsmal or "").strip()
    if not sporsmal:
        return None, None, None

    søkekandidater = []
    db_navn = _ekstraher_stedsnavn_fra_sporsmal(sporsmal)
    if db_navn:
        søkekandidater.append(db_navn)
    if sporsmal not in søkekandidater:
        søkekandidater.append(sporsmal[:120])

    return _run_async(_hent_forste_geotreff_async(søkekandidater))


def _format_rag_linje(sted, avstand_km=None):
    if sted.get("source_type") == "restaurant":
        prefiks = "Spisested"
    elif sted.get("source_type") == "hotel":
        prefiks = "Overnatting"
    else:
        prefiks = "Skjult perle"
    er_spisested = sted.get("source_type") == "restaurant"
    linje = f"- {prefiks}: {sted['navn']} ({sted['by']}, {sted['land']})"
    if avstand_km is not None:
        linje += f" — ca. {avstand_km} km unna"
    linje += f": {sted.get('beskrivelse', '')}"
    if sted.get("tips"):
        linje += f" (Tips: {sted['tips']})"
    if er_spisested and sted.get("pris"):
        linje += f" (Pris: {sted['pris']})"
    if sted.get("type"):
        linje += f" [Kategori: {sted['type']}]"
    return linje


def _unike_rag_treff(treff):
    sett = set()
    unike = []
    for t in treff:
        sted = t["sted"]
        nøkkel = (sted.get("id") or sted.get("navn"), sted.get("by"), sted.get("land"))
        if nøkkel in sett:
            continue
        sett.add(nøkkel)
        unike.append(t)
    return unike


def _hent_geo_rag_treff(sentrum_lat, sentrum_lon):
    treff = []
    for sted in _alle_steder_i_databasen():
        if not _sted_har_koordinater(sted):
            continue
        try:
            avstand = regn_ut_avstand_km(
                sentrum_lat,
                sentrum_lon,
                float(sted["latitude"]),
                float(sted["longitude"]),
            )
        except (TypeError, ValueError):
            continue
        if avstand <= RAG_RADIUS_KM:
            treff.append({"sted": sted, "avstand": avstand})

    treff.sort(key=lambda t: t["avstand"])
    return _unike_rag_treff(treff)[:RAG_MAX_TREFF]


def _hent_fallback_rag_treff():
    alle = _alle_steder_i_databasen()
    return sorted(alle, key=lambda s: (s.get("navn") or "").lower())[:RAG_MAX_TREFF]


def _bygg_rag_kontekst(sporsmal):
    """Bygger RAG-kontekst: geo+nærhet ved lokasjon, ellers tilfeldig utvalg fra databasen."""
    lat, lon, stedsnavn = _hent_lokasjon_fra_sporsmal(sporsmal)
    linjer = []
    geo_modus = False

    if lat is not None and lon is not None:
        geo_treff = _hent_geo_rag_treff(lat, lon)
        if geo_treff:
            geo_modus = True
            for t in geo_treff:
                linjer.append(_format_rag_linje(t["sted"], round(t["avstand"], 1)))

    if not linjer:
        for sted in _hent_fallback_rag_treff():
            linjer.append(_format_rag_linje(sted))

    if not linjer:
        return ""

    body = "\n".join(linjer)
    if geo_modus:
        omrade = stedsnavn or "området brukeren spør om"
        return (
            f"\n\nDu har tilgang til følgende eksklusive, skjulte perler fra vår interne database "
            f"som ligger i umiddelbar nærhet av området brukeren spør om ({omrade}):\n{body}\n\n"
            f"Du SKAL flette disse spesifikke anbefalingene naturlig inn i svaret ditt, og fremheve dem "
            f"som skreddersydde innsidertips fra databasen."
        )

    return (
        f"\n\nDu har tilgang til følgende eksklusive perler fra vår interne database:\n{body}\n\n"
        f"Du SKAL flette disse spesifikke anbefalingene naturlig inn i svaret ditt, og fremheve dem "
        f"som skreddersydde innsidertips fra databasen."
    )


# ========================================
# APPLIKASJONSSTRUKTUR (UI) — faner nederst i skriptet
# ========================================
st.title(T["app_tittel"])
st.caption(T["app_caption"])
st.markdown(
    f'<p class="he-mobil-fane-hint">{html.escape(tr("mobil_fane_hint"))}</p>',
    unsafe_allow_html=True,
)

fane0, fane_ki, fane1, fane2, fane3, fane4, fane5 = st.tabs(
    [
        tr("fane_hjem"),
        tr("fane_ki"),
        tr("fane_mat"),
        tr("fane_hotell"),
        tr("fane_chat"),
        tr("reiseplan_fane"),
        tr("fane_transport"),
    ]
)


# --- FANE 0: HJEM & RADAR (felles startside) ---
with fane0:
    st.header(T["hjem_header"])
    alle_oppdagelser = _alle_steder_i_databasen()
    antall_land = len(set(s["country_code"] or s["land"] for s in alle_oppdagelser))
    st.markdown(
        render_hero_html(len(alle_oppdagelser), antall_land, tr),
        unsafe_allow_html=True,
    )

    unike_land_hjem = sorted({s["land"] for s in alle_oppdagelser if s.get("land")})
    rand_c1, rand_c2 = st.columns([2, 1])
    with rand_c1:
        tilfeldig_land = st.selectbox(
            tr("hjem_filter_land"),
            [T["perler_alle"]] + unike_land_hjem,
            key="tilfeldig_land_filter",
        )
    with rand_c2:
        st.write("")
        if st.button(
            tr("hjem_knapp_tilfeldig"),
            key="tilfeldig_knapp",
            use_container_width=True,
        ):
            valgt = _hent_tilfeldig_oppdagelse(
                alle_oppdagelser, tilfeldig_land, T["perler_alle"]
            )
            if valgt:
                st.session_state["tilfeldig_oppdagelse"] = valgt
            else:
                st.session_state.pop("tilfeldig_oppdagelse", None)
                st.warning(tr("hjem_tilfeldig_ingen"))

    tilfeldig_sted = st.session_state.get("tilfeldig_oppdagelse")
    if tilfeldig_sted:
        with st.container(border=True):
            tilfeldig_tittel = sted_tittel_med_profil(tilfeldig_sted, _sted_emoji(tilfeldig_sted))
            vis_sted_foto(tilfeldig_sted, key_suffix=f"tilfeldig_{tilfeldig_sted['id']}")
            _vis_travel_card(tilfeldig_sted, tilfeldig_tittel)
            tilfeldig_view = (
                "mat"
                if tilfeldig_sted.get("source_type") == "restaurant"
                else "overnatting"
                if tilfeldig_sted.get("source_type") == "hotel"
                else "perle"
            )
            render_reiseplan_knapp(tilfeldig_sted, tilfeldig_view, index="tilfeldig")
            render_affiliate_lenker(tilfeldig_sted, "tilfeldig", index="tilfeldig")

    with st.expander(tr("perler_reisemal_header"), expanded=False):
        land_grupper = _grupper_oppdagelser_pa_land(alle_oppdagelser)
        if not land_grupper:
            vis_tom_tilstand("🗺️", tr("perler_ingen_reisemal"), tr("perler_ingen_reisemal"))
        else:
            land_kort_html = '<div class="he-land-grid">'
            for land, steder_i_land in land_grupper.items():
                eksempel = steder_i_land[0] if steder_i_land else {}
                land_kort_html += render_land_kort_html(
                    land,
                    tr("land_kort_antall").format(len(steder_i_land)),
                    eksempel.get("navn", ""),
                    eksempel.get("country_code", ""),
                )
            land_kort_html += "</div>"
            st.markdown(land_kort_html, unsafe_allow_html=True)
            for land, steder_i_land in land_grupper.items():
                with st.expander(f"{land} ({len(steder_i_land)})", expanded=False):
                    for sted in steder_i_land:
                        by_del = f" — {sted['by']}" if sted.get("by") else ""
                        type_del = vis_sted_type(sted)
                        type_tekst = f" · {type_del}" if type_del else ""
                        st.markdown(f"**{sted['navn']}**{by_del}{type_tekst}")

    st.divider()
    st.subheader(T["radar_tittel"])
    st.caption(T["radar_sub"])

    alle_steder_i_db = _alle_steder_i_databasen()
    filtrert_for_radar = filtrer_data(alle_steder_i_db)

    if not filtrert_for_radar:
        st.write(T["radar_ingen_data"])
    else:
        with st.container(border=True):
            soke_metode = st.radio(
                T["radar_metode"],
                options=[T["radar_sted_sok"], T["radar_gps"], T["radar_land_sok"]],
                horizontal=True,
                key="start_radar_metode",
            )
            sted_sok = ""
            valgt_land = None
            rad_c1, rad_c2 = st.columns([2, 1])
            with rad_c1:
                if soke_metode == T["radar_sted_sok"]:
                    sted_sok = st.text_input(
                        T["radar_sted_input"],
                        placeholder=T["radar_sted_placeholder"],
                        key="radar_sted_sok_input",
                    )
                elif soke_metode == T["radar_land_sok"]:
                    unike_land = sorted({p["land"] for p in filtrert_for_radar})
                    valgt_land = st.selectbox(
                        T["radar_velg_land"], unike_land, key="radar_land"
                    )
                else:
                    st.caption(tr("radar_gps_hint"))
            with rad_c2:
                vis_alle_perler = st.checkbox(
                    tr("radar_vis_alle"),
                    value=False,
                    key="radar_vis_alle_perler",
                )
                if vis_alle_perler:
                    st.caption(tr("radar_vis_alle_hint"))
                    st.markdown(
                        f'<p style="margin:0 0 0.35rem 0;color:#9CA3AF;text-decoration:line-through;'
                        f'font-size:0.9rem;">{T["radar_radius"]}</p>',
                        unsafe_allow_html=True,
                    )
                    maks_avstand = st.slider(
                        " ",
                        min_value=10,
                        max_value=500,
                        value=150,
                        step=10,
                        key="radar_radius_slider",
                        disabled=True,
                        label_visibility="collapsed",
                    )
                else:
                    maks_avstand = st.slider(
                        T["radar_radius"],
                        min_value=10,
                        max_value=500,
                        value=150,
                        step=10,
                        key="radar_radius_slider",
                    )

        if soke_metode == T["radar_gps"]:
            with st.spinner(T["radar_spinner"]):
                radar_treff, soke_sentrum, soke_sentrum_navn, naermeste_perle_tekst = (
                    beregn_radar_filtrering(
                        alle_steder_i_db,
                        soke_metode=soke_metode,
                        metode_sted=T["radar_sted_sok"],
                        metode_gps=T["radar_gps"],
                        metode_land=T["radar_land_sok"],
                        maks_avstand=maks_avstand,
                        vis_alle_perler=vis_alle_perler,
                        sted_sok="",
                        valgt_land=None,
                        gps_sentrum_navn=T["radar_sentrum_gps"],
                    )
                )
        else:
            radar_treff, soke_sentrum, soke_sentrum_navn, naermeste_perle_tekst = (
                beregn_radar_filtrering(
                    alle_steder_i_db,
                    soke_metode=soke_metode,
                    metode_sted=T["radar_sted_sok"],
                    metode_gps=T["radar_gps"],
                    metode_land=T["radar_land_sok"],
                    maks_avstand=maks_avstand,
                    vis_alle_perler=vis_alle_perler,
                    sted_sok=sted_sok,
                    valgt_land=valgt_land,
                    gps_sentrum_navn=T["radar_sentrum_gps"],
                )
            )

        if soke_metode == T["radar_land_sok"] and valgt_land:
            st.success(bygg_radar_ki_innsikt(valgt_land, radar_treff))

        if naermeste_perle_tekst:
            st.markdown(naermeste_perle_tekst)

        treff_steder = [t["data"] for t in radar_treff]

        if treff_steder:
            kart_sted = soke_sentrum_navn or tr("radar_region_default")
            kart_tittel = (
                tr("radar_kart_tittel_alle")
                if vis_alle_perler
                else tr("radar_kart_tittel")
            )
            st.markdown(kart_tittel.format(len(radar_treff), kart_sted))
            if vis_alle_perler:
                st.markdown(
                    f'<p style="color:#9CA3AF;text-decoration:line-through;font-size:0.85rem;'
                    f'margin:0 0 0.5rem 0;">{tr("radar_avstand_deaktivert").format(maks_avstand)}</p>',
                    unsafe_allow_html=True,
                )
            kart_zoom = 7 if soke_sentrum else 5
            perler_kart = lag_radar_kart(
                radar_treff,
                sentrum=soke_sentrum,
                sentrum_navn=soke_sentrum_navn or tr("radar_region_default"),
                zoom_start=kart_zoom,
            )
            kart_resultat = st_folium(
                perler_kart,
                width="stretch",
                height=430,
                returned_objects=["last_object_clicked"],
                key="perler_folium_kart",
            )
            klikket = (kart_resultat or {}).get("last_object_clicked")
            if klikket and klikket.get("lat") is not None and klikket.get("lng") is not None:
                klikk_lat = float(klikket["lat"])
                klikk_lon = float(klikket["lng"])
                valgt_treff = min(
                    radar_treff,
                    key=lambda t: regn_ut_avstand_km(
                        klikk_lat,
                        klikk_lon,
                        float(t["data"]["latitude"]),
                        float(t["data"]["longitude"]),
                    ),
                )
                valgt_perle = valgt_treff["data"]
                st.markdown(tr("kart_valgt_sted"))
                with st.container(border=True):
                    valgt_tittel = sted_tittel_med_profil(
                        valgt_perle, _sted_emoji(valgt_perle)
                    )
                    vis_sted_foto(
                        valgt_perle,
                        key_suffix=f"radar_valgt_{valgt_perle.get('id', 'x')}",
                        autoload=True,
                    )
                    _vis_travel_card(valgt_perle, valgt_tittel)
                    if valgt_treff["avstand"] > 0:
                        st.caption(f"{valgt_treff['avstand']} km {T['radar_unna']}")
                    valgt_view = (
                        "mat"
                        if valgt_perle.get("source_type") == "restaurant"
                        else "overnatting"
                        if valgt_perle.get("source_type") == "hotel"
                        else "perle"
                    )
                    render_reiseplan_knapp(valgt_perle, valgt_view, index="radar_valgt")
        elif soke_metode == T["radar_sted_sok"] and not (sted_sok or "").strip():
            st.caption(tr("radar_skriv_sted_hint"))
        elif soke_metode != T["radar_land_sok"] or valgt_land:
            vis_tom_tilstand(
                "📡",
                tr("tom_radar_tittel"),
                tr("tom_radar_tekst"),
            )

        st.write("---")
        st.subheader(T["perler_header"])
        perler_med_koords = filtrer_data(SKJULTE_PERLER_DB)
        with st.expander(tr("perler_kart_expander"), expanded=False):
            if perler_med_koords:
                perler_alle_kart = lag_stedskart(perler_med_koords)
                st_folium(
                    perler_alle_kart,
                    width="stretch",
                    height=420,
                    returned_objects=[],
                    key="perler_alle_folium_kart",
                )
                st.caption(
                    tr("perler_kart_caption").format(
                        len(perler_med_koords), len(SKJULTE_PERLER_DB)
                    )
                )
            else:
                st.info(T["perler_ingen_koordinater"])

        col_s1, col_s2 = st.columns(2)
        with col_s1:
            sok_perle = st.text_input(T["perler_sok"], "", key="perler_sok_input").strip()
        with col_s2:
            alle_typer = sorted({p["type"] for p in SKJULTE_PERLER_DB})
            type_perle = st.selectbox(
                T["perler_sorter_type"],
                [T["perler_alle"]] + alle_typer,
                key="perler_type_filter",
            )

        filtrerte_perler = _filtrer_perler_liste(
            sok_perle, type_perle, T["perler_alle"], radar_treff
        )

        if not sok_perle and type_perle == T["perler_alle"]:
            st.caption(tr("perler_viser_alle").format(len(filtrerte_perler)))

        perler_filter_key = f"{sok_perle}|{type_perle}"
        if st.session_state.get("_perler_filter_key") != perler_filter_key:
            st.session_state["_perler_filter_key"] = perler_filter_key
            st.session_state["perler_vis_antall"] = 12
        vis_antall = st.session_state.get("perler_vis_antall", 12)
        perler_side = filtrerte_perler[:vis_antall]

        if sok_perle:
            sok_med_koords = filtrer_data(filtrerte_perler)
            if sok_med_koords:
                sok_treff = [{"data": p, "avstand": 0} for p in sok_med_koords]
                lat_snitt = sum(float(p["latitude"]) for p in sok_med_koords) / len(
                    sok_med_koords
                )
                lon_snitt = sum(float(p["longitude"]) for p in sok_med_koords) / len(
                    sok_med_koords
                )
                st.caption(
                    tr("perler_sok_kart").format(len(sok_med_koords), sok_perle)
                )
                sok_kart = lag_radar_kart(
                    sok_treff,
                    sentrum=(lat_snitt, lon_snitt),
                    sentrum_navn=sok_perle,
                    zoom_start=10 if len(sok_med_koords) == 1 else 8,
                )
                st_folium(
                    sok_kart,
                    width="stretch",
                    height=360,
                    returned_objects=[],
                    key="perler_sok_folium_kart",
                )

        if perler_side:
            for i in range(0, len(perler_side), 3):
                cols = st.columns(3)
                for j in range(3):
                    if i + j < len(perler_side):
                        p = perler_side[i + j]
                        perle_tittel = sted_tittel_med_profil(p, _sted_emoji(p))
                        with cols[j]:
                            vis_sted_foto(p, key_suffix=f"perle_{p['id']}")
                            _vis_travel_card(p, perle_tittel)
                            view_key = (
                                "mat"
                                if p.get("source_type") == "restaurant"
                                else "overnatting"
                                if p.get("source_type") == "hotel"
                                else "perle"
                            )
                            render_reiseplan_knapp(p, view_key, index=i + j)
                            if view_key in ("mat", "overnatting"):
                                render_affiliate_lenker(p, view_key, index=i + j)
            if len(filtrerte_perler) > vis_antall:
                if st.button(
                    tr("perler_vis_flere").format(len(filtrerte_perler) - vis_antall),
                    key="perler_vis_flere_btn",
                ):
                    st.session_state["perler_vis_antall"] = vis_antall + 12
                    st.rerun()
        else:
            vis_tom_tilstand(
                "🔍",
                tr("tom_perler_tittel"),
                T["perler_ingen_treff"],
            )
            if sok_perle and any(
                _sted_matcher_sok(s, sok_perle) for s in LOKALE_SPISESTEDER_DB
            ):
                st.caption(tr("perler_sok_mat_hint").format(sok_perle))


# --- FANE: KI (OPPDAGELSER + HELGEBY) ---
with fane_ki:
    ki_modus = st.segmented_control(
        tr("ki_modus_label"),
        options=[tr("ki_modus_oppdagelser"), tr("ki_modus_helgeby")],
        default=tr("ki_modus_oppdagelser"),
        key="ki_modus_valg",
    )
    if ki_modus == tr("ki_modus_helgeby"):
        st.header(tr("helgeby_header"))
        st.caption(tr("helgeby_caption"))
    else:
        st.header(tr("sank_header"))
        st.caption(tr("sank_caption"))
        _render_sank_ki_panel(
            tr=tr,
            min_unikhetsgrad=MIN_UNIKHETSGRAD,
            sanke_perler_for_omrade=sanke_perler_for_omrade,
            lagre_agent_perle_i_db=lagre_agent_perle_i_db,
            legg_lagret_sted_i_lokale_lister=_legg_lagret_sted_i_lokale_lister,
            kandidat_lagringsstatus=_kandidat_lagringsstatus,
            render_reiseplan_knapp_agent=render_reiseplan_knapp_agent,
            chat_lagre_tekster=_chat_lagre_tekster,
            vis_sted_foto=vis_sted_foto,
            berik_kandidat_fra_db=_berik_kandidat_fra_db,
            vis_travel_card=_vis_travel_card,
            sted_tittel_fn=sted_tittel_med_profil,
            sted_emoji_fn=_sted_emoji,
        )

    if ki_modus == tr("ki_modus_helgeby"):
        with st.form("helgeby_form"):
            helgeby_omrade = st.text_input(
                tr("helgeby_omrade"),
                placeholder=tr("helgeby_omrade_ph"),
                key="helgeby_omrade_input",
            )
            helgeby_antall = st.slider(
                tr("helgeby_antall"),
                min_value=2,
                max_value=8,
                value=4,
                key="helgeby_antall_input",
            )
            helgeby_streng = st.checkbox(
                tr("sank_strict_mode"),
                value=False,
                key="helgeby_strict_input",
            )
            start_helgeby = st.form_submit_button(
                tr("helgeby_knapp"), type="primary", use_container_width=True
            )

        helgeby_kandidater = list(st.session_state.get("helgeby_kandidater") or [])
        helgeby_rapport = st.session_state.get("helgeby_rapport")

        if start_helgeby:
            omrade = (helgeby_omrade or "").strip()
            if not omrade:
                st.error(tr("helgeby_feil_omrade"))
            else:
                st.session_state.pop("helgeby_kandidater", None)
                st.session_state.pop("helgeby_rapport", None)
                try:
                    with st.spinner(tr("helgeby_spinner").format(omrade)):
                        kandidater, rapport = sanke_helgebyer_for_omrade(
                            omrade,
                            helgeby_antall,
                            strict_mode=helgeby_streng,
                        )
                    st.session_state["helgeby_kandidater"] = kandidater
                    st.session_state["helgeby_rapport"] = rapport
                    helgeby_kandidater = list(kandidater or [])
                    helgeby_rapport = rapport
                except Exception as e:
                    st.error(tr("helgeby_feil_generell").format(str(e)))

        if helgeby_rapport:
            st.caption(
                tr("helgeby_rapport").format(
                    helgeby_rapport.get("foreslaatt", 0),
                    helgeby_rapport.get("godkjent", 0),
                    helgeby_rapport.get("forkastet_mainstream", 0),
                    helgeby_rapport.get("forkastet_storby", 0),
                    helgeby_rapport.get("forkastet_hotell", 0),
                    helgeby_rapport.get("forkastet_score", 0),
                )
            )

        if helgeby_kandidater and not any(
            isinstance(k.get("hoteller"), list) and k["hoteller"] for k in helgeby_kandidater
        ):
            st.warning(tr("helgeby_hotell_stale"))

        if helgeby_kandidater:
            for idx, kandidat in enumerate(helgeby_kandidater):
                with st.container(border=True):
                    vis_sted = {
                        **kandidat,
                        "navn": kandidat.get("by", ""),
                        "id": kandidat.get("agent_id", f"helgeby_{idx}"),
                        "type": "kultur",
                    }
                    vis_sted_foto(
                        vis_sted,
                        key_suffix=f"helgeby_{idx}_{vis_sted['id']}",
                        autoload=True,
                    )
                    helge_tittel = f"🌆 {kandidat['by']}"
                    _vis_travel_card(vis_sted, helge_tittel)
                    if kandidat.get("hvorfor_helg"):
                        st.markdown(
                            f"**{tr('helgeby_hvorfor')}** {kandidat['hvorfor_helg']}"
                        )
                    if kandidat.get("highlights"):
                        st.markdown(
                            f"**{tr('helgeby_highlights')}:** "
                            + ", ".join(kandidat["highlights"])
                        )
                    if kandidat.get("beste_tid"):
                        st.caption(tr("helgeby_beste_tid") + ": " + kandidat["beste_tid"])
                    hoteller = kandidat.get("hoteller") or []
                    st.subheader(tr("helgeby_hoteller"))
                    if not hoteller:
                        st.caption(tr("helgeby_hotell_mangler"))
                    for h_idx, hotell in enumerate(hoteller):
                        with st.container(border=True):
                            pris = (hotell.get("pris") or "").strip()
                            pris_del = f" · {pris}" if pris else ""
                            st.markdown(
                                f"**{hotell.get('navn', '')}** · "
                                f"{tr('helgeby_hotell_unikhet').format(hotell.get('saerhetsscore', 0))}"
                                f"{pris_del}"
                            )
                            if hotell.get("beskrivelse"):
                                st.write(hotell["beskrivelse"])
                            if st.button(
                                tr("helgeby_lagre_hotell"),
                                key=f"helgeby_hotel_{idx}_{h_idx}_{hotell['agent_id']}",
                                use_container_width=True,
                            ):
                                lagret = lagre_agent_perle_i_db(hotell)
                                _legg_lagret_sted_i_lokale_lister(lagret)
                                st.toast(tr("chat_lagre_toast"))
                                st.rerun()
                    col_plan, col_db = st.columns([1, 1])
                    with col_plan:
                        render_reiseplan_knapp_agent(
                            kandidat, f"helgeby_{idx}_{kandidat['agent_id']}"
                        )
                    with col_db:
                        if st.button(
                            tr("chat_lagre_db"),
                            key=f"helgeby_save_{idx}_{kandidat['agent_id']}",
                            use_container_width=True,
                        ):
                            lagret = lagre_agent_perle_i_db(kandidat)
                            _legg_lagret_sted_i_lokale_lister(lagret)
                            st.toast(tr("chat_lagre_toast"))
                            st.rerun()
        elif helgeby_rapport and helgeby_rapport.get("godkjent", 0) == 0:
            vis_tom_tilstand("🌆", tr("sank_ingen_tittel"), tr("helgeby_ingen"))


# --- FANE 1: MAT ---
with fane1:
    st.header(T["mat_header"])
    st.caption(tr("mat_i_db").format(len(LOKALE_SPISESTEDER_DB)))

    mat_med_koordinater = [
        s for s in LOKALE_SPISESTEDER_DB if "latitude" in s and "longitude" in s
    ]
    with st.expander(tr("mat_kart_expander"), expanded=False):
        if mat_med_koordinater:
            mat_kart = lag_stedskart(mat_med_koordinater)
            st_folium(
                mat_kart,
                width=700,
                height=500,
                returned_objects=[],
                key="mat_folium_kart",
            )
            st.caption(tr("mat_pa_kart").format(len(mat_med_koordinater)))
        else:
            st.info(T["perler_ingen_koordinater"])

    col_m1, col_m2 = st.columns(2)
    with col_m1:
        sok_mat = st.text_input(T["mat_sok"], "", key="mat_sok_input").strip()
    with col_m2:
        type_mat = st.selectbox(
            T["mat_sorter_type"],
            [T["perler_alle"]]
            + sorted(list(set(m["type"] for m in LOKALE_SPISESTEDER_DB))),
            key="mat_type_filter",
        )

    st.write("---")

    filtrert_mat = []
    for sted in LOKALE_SPISESTEDER_DB:
        if type_mat != T["perler_alle"] and sted["type"] != type_mat:
            continue
        if not _sted_matcher_sok(sted, sok_mat):
            continue
        filtrert_mat.append(sted)


    if filtrert_mat:
        for i in range(0, len(filtrert_mat), 3):
            cols = st.columns(3)
            for j in range(3):
                if i + j < len(filtrert_mat):
                    s = filtrert_mat[i + j]
                    mat_tittel = sted_tittel_med_profil(s, "🍽️")
                    with cols[j]:
                        vis_sted_foto(s, key_suffix=f"mat_{s['id']}")
                        _vis_travel_card(
                            s,
                            mat_tittel,
                            pris_tekst=f"{T['mat_pris']} {s['pris']}",
                        )
                        render_reiseplan_knapp(s, "mat", index=i + j)
                        render_affiliate_lenker(s, "mat", index=i + j)
    else:
        vis_tom_tilstand("🍽️", tr("tom_perler_tittel"), T["mat_ingen_treff"])


# --- FANE 2: OVERNATTING ---
with fane2:
    st.header(tr("hotell_header"))
    st.caption(tr("hotell_i_db").format(len(LOKALE_HOTELLER_DB)))

    if not LOKALE_HOTELLER_DB:
        st.warning(
            "Ingen overnatting lastet inn. Lagre database.py (med UNIKE_OVERNATTING) "
            "og start appen på nytt (Ctrl+C, deretter streamlit run app.py)."
        )

    hotell_med_koordinater = [
        s for s in LOKALE_HOTELLER_DB if "latitude" in s and "longitude" in s
    ]
    with st.expander(tr("hotell_kart_expander"), expanded=False):
        if hotell_med_koordinater:
            hotell_kart = lag_stedskart(hotell_med_koordinater)
            st_folium(
                hotell_kart,
                width=700,
                height=500,
                returned_objects=[],
                key="hotell_folium_kart",
            )
            st.caption(tr("hotell_pa_kart").format(len(hotell_med_koordinater)))
        else:
            st.info(T["perler_ingen_koordinater"])

    col_h1, col_h2 = st.columns(2)
    with col_h1:
        sok_hotell = st.text_input(tr("hotell_sok"), "", key="hotell_sok_input").strip()
    with col_h2:
        type_valg = [T["perler_alle"]] + sorted(
            {m["type"] for m in LOKALE_HOTELLER_DB if m.get("type")}
        )
        type_hotell = st.selectbox(
            tr("hotell_sorter_type"),
            type_valg,
            key="overnatting_type_filter_v2",
        )

    st.write("---")

    filtrert_hotell = []
    for sted in LOKALE_HOTELLER_DB:
        if not _matcher_overnatting_type(type_hotell, sted.get("type")):
            continue
        if not _sted_matcher_sok(sted, sok_hotell):
            continue
        filtrert_hotell.append(sted)


    if filtrert_hotell:
        for i in range(0, len(filtrert_hotell), 3):
            cols = st.columns(3)
            for j in range(3):
                if i + j < len(filtrert_hotell):
                    s = filtrert_hotell[i + j]
                    hotell_tittel = sted_tittel_med_profil(s, "🛏️")
                    with cols[j]:
                        vis_sted_foto(s, key_suffix=f"hotell_{s['id']}")
                        _vis_travel_card(
                            s,
                            hotell_tittel,
                            pris_tekst=f"{tr('hotell_pris')} {s.get('pris', '€€')}",
                        )
                        render_reiseplan_knapp(s, "hotell", index=i + j)
                        render_affiliate_lenker(s, "hotell", index=i + j)
    else:
        vis_tom_tilstand("🛏️", tr("tom_perler_tittel"), tr("hotell_ingen_treff"))


# --- FANE 3: REISE-CHAT ---
with fane3:
    st.header(T["chat_header"])
    st.caption(T["chat_caption"])

    if not st.session_state.reise_chat:
        st.info(tr("chat_ingen_meldinger"))

    if st.session_state.reise_chat:
        html_innhold = """
        <html>
        <head>
            <style>
                body { font-family: 'Inter', 'Segoe UI', sans-serif; padding: 30px; color: #1F1F1F; background: #F8FAFD; line-height: 1.6; }
                .header { text-align: center; border-bottom: 2px solid #1A73E8; padding-bottom: 10px; margin-bottom: 30px; }
                .message-box { margin-bottom: 20px; padding: 15px; border-radius: 10px; }
                .user { background-color: #FFFFFF; border-left: 4px solid #DADCE0; color: #3C4043; }
                .assistant { background-color: #F0F4F9; border-left: 4px solid #1A73E8; color: #3C4043; }
                .sender-name { font-weight: 700; font-size: 0.9em; color: #1A73E8; margin-bottom: 5px; }
                .map-hint { font-size: 0.85em; color: #5F6368; font-style: italic; margin-top: 10px; }
            </style>
        </head>
        <body>
            <div class="header">
                <h1>🇪🇺 Min Reiseplan</h1>
                <p>Generert av Hemmelige Europa</p>
            </div>
        """

        for m in st.session_state.reise_chat:
            if m["role"] == "user":
                html_innhold += f"""
                <div class="message-box user">
                    <div class="sender-name">👤 REISENDE</div>
                    <div>{m["content"]}</div>
                </div>
                """
            else:
                html_innhold += f"""
                <div class="message-box assistant">
                    <div class="sender-name">🇪🇺 REISEEKSPERT</div>
                    <div>{m["content"]}</div>
                """
                if m.get("lat"):
                    html_innhold += (
                        f'<div class="map-hint">📍 Kartkoordinater lagret for '
                        f'{m.get("sted", "destinasjonen")}: {m["lat"]}, {m["lon"]}</div>'
                    )
                html_innhold += "</div>"

        html_innhold += """
        </body>
        </html>
        """

        st.download_button(
            label=T["chat_last_ned"],
            data=html_innhold,
            file_name="min_reiseplan.html",
            mime="text/html",
            use_container_width=True,
            key="chat_download",
        )
        st.write("")

    for loop_index, melding in enumerate(st.session_state.reise_chat):
        with st.chat_message(melding["role"]):
            st.markdown(synlig_ai_svar(melding["content"]))
            if melding.get("lat") and melding.get("lon"):
                st.write("")
                st.markdown(
                    f"{T['chat_kart_over']} {(melding.get('sted') or 'destinasjonen').capitalize()}:"
                )
                chat_kart = lag_chat_oppdag_kart(
                    melding["lat"],
                    melding["lon"],
                    _alle_steder_i_databasen(),
                    melding.get("sted") or "",
                )
                sted_key = (melding.get("sted") or "destinasjon").replace(" ", "_")
                st_folium(
                    chat_kart,
                    width=700,
                    height=420,
                    returned_objects=[],
                    key=f"chat_folium_map_{sted_key}_{loop_index}",
                )
            kandidat = melding.get("agent_perle")
            if (
                melding["role"] == "assistant"
                and kandidat
                and kandidat.get("saerhetsscore", 0) >= MIN_UNIKHETSGRAD
            ):
                render_chat_agent_perle_handlinger(
                    kandidat,
                    f"hist_{kandidat['agent_id']}_{loop_index}",
                    melding.get("content", ""),
                )

    with st.form("reise_chat_skjema", clear_on_submit=True):
        sporsmal = st.text_area(
            T["chat_input"],
            height=100,
            placeholder=tr("chat_placeholder"),
            key="reise_chat_sporsmal",
        )
        sendt = st.form_submit_button(
            tr("chat_send"),
            type="primary",
            use_container_width=True,
        )

    if sendt and sporsmal and sporsmal.strip():
        sporsmal = sporsmal.strip()
        st.session_state.reise_chat.append({"role": "user", "content": sporsmal})
        with st.chat_message("user"):
            st.markdown(sporsmal)

        with st.chat_message("assistant"):
            wiki_kontekst = ""
            sted_for_kart = ""
            lat, lon = None, None
            fullt_svar = ""
            synlig_svar = ""
            agent_perle = None

            sted_for_kart = _parse_wiki_stedsnavn(sporsmal)
            if sted_for_kart:
                wiki_geo_start = time.perf_counter()
                try:
                    with st.spinner(T["chat_wiki_spinner"].format(sted_for_kart)):
                        wiki_info, lat, lon, wiki_feil = _run_async(
                            _hent_wikivoyage_for_chat_async(sted_for_kart)
                        )
                    if wiki_info:
                        st.markdown(f"{T['chat_wiki_hentet']}\n> *{wiki_info}*")
                        wiki_kontekst = (
                            f"Kontekstinformasjon fra Wikivoyage om {sted_for_kart}: {wiki_info}"
                        )
                    elif wiki_feil:
                        if wiki_feil.startswith("Ingen Wikivoyage"):
                            st.warning(tr("chat_wiki_ingen").format(sted_for_kart))
                        else:
                            st.warning(tr("chat_wiki_feil").format(wiki_feil))
                    if st.session_state.get("vis_perf_debug"):
                        st.caption(
                            tr("perf_wiki_geo").format(
                                time.perf_counter() - wiki_geo_start
                            )
                        )
                except Exception as e:
                    st.warning(tr("chat_wiki_feil").format(str(e)))

            try:
                fullt_svar = st.write_stream(
                    generer_reiseekspert_stream(sporsmal, wiki_kontekst)
                )
            except Exception as e:
                fullt_svar = tr("chat_feil_stream").format(str(e))
                st.error(fullt_svar)
            synlig_svar = synlig_ai_svar(fullt_svar or "")
            agent_perle = detekter_perle_fra_ai_svar(fullt_svar or "")

            if lat and lon:
                st.write("")
                st.markdown(f"{T['chat_kart_over']} {sted_for_kart.capitalize()}:")
                ny_chat_kart = lag_chat_oppdag_kart(
                    lat, lon, _alle_steder_i_databasen(), sted_for_kart
                )
                sted_key = (sted_for_kart or "destinasjon").replace(" ", "_")
                st_folium(
                    ny_chat_kart,
                    width=700,
                    height=420,
                    returned_objects=[],
                    key=f"chat_folium_map_{sted_key}_{len(st.session_state.reise_chat)}",
                )
            if agent_perle and agent_perle.get("saerhetsscore", 0) >= MIN_UNIKHETSGRAD:
                render_chat_agent_perle_handlinger(
                    agent_perle,
                    f"live_{agent_perle['agent_id']}",
                    fullt_svar or "",
                )

        st.session_state.reise_chat.append(
            {
                "role": "assistant",
                "content": synlig_svar or fullt_svar or "",
                "lat": lat,
                "lon": lon,
                "sted": sted_for_kart,
                "agent_perle": agent_perle,
            }
        )
        lagre_data(
            st.session_state.reise_chat,
            st.session_state.profil,
            st.session_state.get("reiseplan"),
        )
        st.rerun()


with fane4:
    st.header(T["reiseplan_header"])
    itinerary_items = get_itinerary_items()
    st.caption(tr("reiseplan_antall").format(len(itinerary_items)))

    with st.expander(tr("reiseplan_egne_expander"), expanded=False):
        with st.form("eget_sted_form"):
            custom_navn = st.text_input(
                tr("reiseplan_sted_navn"),
                placeholder=tr("reiseplan_sted_navn_ph"),
                key="add_custom_navn",
            )
            custom_by = st.text_input(
                tr("reiseplan_by"), placeholder=tr("reiseplan_by_ph"), key="add_custom_by"
            )
            custom_land = st.text_input(
                tr("reiseplan_land"), placeholder=tr("reiseplan_land_ph"), key="add_custom_land"
            )
            custom_beskrivelse = st.text_area(
                tr("reiseplan_beskrivelse"),
                placeholder=tr("reiseplan_beskrivelse_ph"),
                key="add_custom_beskrivelse",
            )
            if st.form_submit_button(tr("reiseplan_lagre"), use_container_width=True):
                navn = custom_navn.strip()
                by = custom_by.strip()
                land = custom_land.strip()
                if not navn or not by or not land:
                    st.error(tr("reiseplan_fyll_ut"))
                else:
                    latitude, longitude = hent_koordinater_for_sok(f"{by}, {land}")
                    eget_sted = {
                        "id": f"custom_{int(time.time() * 1000)}",
                        "navn": navn,
                        "by": by,
                        "land": land,
                        "beskrivelse": custom_beskrivelse.strip(),
                        "latitude": latitude,
                        "longitude": longitude,
                        "type": "egendefinert",
                    }
                    add_itinerary_item(eget_sted)
                    persist_reiseplan()
                    st.success(tr("reiseplan_lagret_ok"))
                    st.rerun()

    if not itinerary_items:
        vis_tom_tilstand(
            "✨",
            tr("tom_reiseplan_tittel"),
            T["reiseplan_tom"],
        )
    else:
        st.download_button(
            T["reiseplan_last_ned"],
            data=lag_reiseplan_html(itinerary_items),
            file_name="hemmelige-europa-reiseplan.html",
            mime="text/html",
            use_container_width=True,
            key="reiseplan_download",
        )

        if any(item.get("latitude") and item.get("longitude") for item in itinerary_items):
            reiseplan_kart = lag_reiseplan_rute_kart(itinerary_items)
            st_folium(
                reiseplan_kart,
                width=900,
                height=420,
                returned_objects=[],
                key="reiseplan_kart",
            )

        for item in itinerary_items:
            with st.container(border=True):
                vis_sted_foto(item, key_suffix=f"plan_{item['id']}")
                _vis_travel_card(item, item.get("navn", ""))
                if st.button(
                    tr("reiseplan_fjern"),
                    key=f"reiseplan_remove_{item['id']}",
                    use_container_width=True,
                ):
                    remove_itinerary_item(item["id"])
                    persist_reiseplan()
                    st.rerun()

        dager = st.slider(
            T["reiseplan_ai_prompt"],
            min_value=1,
            max_value=14,
            value=5,
            key="reiseplan_dager",
        )
        if st.button(
            T["reiseplan_ai"],
            type="primary",
            use_container_width=True,
            key="reiseplan_ai_btn",
        ):
            optimalisert_reiseplan = optimaliser_reiserute_naermeste_nabo(itinerary_items)
            st.caption(tr("reiseplan_optimalisert"))
            st.write_stream(generer_ai_reiserute(optimalisert_reiseplan, dager))

# --- FANE 5: TRANSPORT ---
with fane5:
    stedvalg = bygg_stedvalg_fra_database(_alle_steder_i_databasen())
    alle_labels = sorted(stedvalg.keys())
    plan_items = get_itinerary_items()

    def _label_for_sted(sted):
        for label, kandidat in stedvalg.items():
            if kandidat.get("id") == sted.get("id"):
                return label
        return None

    if len(plan_items) >= 2:
        st.subheader(tr("transport_fra_plan"))
        for i in range(len(plan_items) - 1):
            fra_item, til_item = plan_items[i], plan_items[i + 1]
            if st.button(
                tr("transport_leg_knapp").format(fra_item["navn"], til_item["navn"]),
                key=f"tp_plan_leg_{i}",
                use_container_width=True,
            ):
                fra_l = _label_for_sted(fra_item)
                til_l = _label_for_sted(til_item)
                if fra_l:
                    st.session_state["tp_fra_select"] = fra_l
                if til_l:
                    st.session_state["tp_til_select"] = til_l
                st.rerun()

    if alle_labels:
        if "tp_fra_select" not in st.session_state:
            st.session_state["tp_fra_select"] = alle_labels[0]
        if "tp_til_select" not in st.session_state:
            st.session_state["tp_til_select"] = alle_labels[min(1, len(alle_labels) - 1)]
        c_fra, c_til = st.columns(2)
        with c_fra:
            fra_label = st.selectbox(
                T["transport_fra"], alle_labels, key="tp_fra_select"
            )
        with c_til:
            til_label = st.selectbox(
                T["transport_til"], alle_labels, key="tp_til_select"
            )

        fra_sted = stedvalg.get(fra_label)
        til_sted = stedvalg.get(til_label)

        if fra_sted and til_sted and fra_sted.get("id") != til_sted.get("id"):
            eksterne = bygg_eksterne_planleggere(
                fra_sted["by"],
                fra_sted["land"],
                til_sted["by"],
                til_sted["land"],
                spraak,
            )
            lenker = [
                ("google", T["transport_lenke_google"]),
                ("omio", T["transport_lenke_omio"]),
                ("rome2rio", T["transport_lenke_rome2rio"]),
                ("trainline", T["transport_lenke_trainline"]),
            ]
            if "cp_atlas" in eksterne:
                lenker.append(("cp_atlas", tr("transport_lenke_cp")))
            kolonner = st.columns(len(lenker))
            for col, (slug, tekst) in zip(kolonner, lenker):
                with col:
                    st.link_button(
                        tekst,
                        eksterne[slug],
                        use_container_width=True,
                        key=f"tp_link_{slug}",
                    )



# ========================================
# FOOTER
# ========================================
st.markdown("---")
