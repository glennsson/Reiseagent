import streamlit as st
import requests
import os
import time
import json
import re
import asyncio
from pathlib import Path
import folium
from streamlit_folium import st_folium
from dotenv import load_dotenv
from streamlit_js_eval import get_geolocation

import html
import math
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


def _last_lokale_stedlister():
    """Oppfrisker lister fra SQLite ved hver Streamlit-kjøring."""
    seed_places()
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


def regn_ut_avstand_km(lat1, lon1, lat2, lon2):
    """Regner ut avstanden i kilometer mellom to GPS-koordinater"""
    R = 6371.0  # Jordens radius i km

    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


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
# PERSISTENT LAGRING (HISTORIKK OG CHAT)
# ========================================
PROFIL_FIL = "reiseprofil.json"

STANDARD_PROFIL = {
    "reise_folge": "Par",
    "budsjett": "Medium",
    "hovedinteresse": "Kultur & Historie",
}

PROFIL_REISE_FOLGE = ["Singel", "Par", "Familie med barn"]
PROFIL_BUDSJETT = ["Budsjett", "Medium", "Luksus"]
PROFIL_INTERESSE = [
    "Mat & Vin",
    "Kultur & Historie",
    "Natur & Aktivitet",
    "Golf",
]


def _normaliser_profil(profil):
    """Sikrer at profil-dict har gyldige felt, også ved tom eller ufullstendig JSON."""
    if not isinstance(profil, dict):
        return dict(STANDARD_PROFIL)

    def _sikker(verdi, alternativer, standard):
        return verdi if verdi in alternativer else standard

    # Eldre profiler med «Sport» mappes til nærmeste interesse
    legacy = profil.get("hovedinteresse")
    if legacy == "Sport":
        legacy = "Natur & Aktivitet"

    return {
        "reise_folge": _sikker(
            profil.get("reise_folge"), PROFIL_REISE_FOLGE, STANDARD_PROFIL["reise_folge"]
        ),
        "budsjett": _sikker(
            profil.get("budsjett"), PROFIL_BUDSJETT, STANDARD_PROFIL["budsjett"]
        ),
        "hovedinteresse": _sikker(
            legacy,
            PROFIL_INTERESSE,
            STANDARD_PROFIL["hovedinteresse"],
        ),
    }


def last_inn_data():
    """Henter lagret data fra fil hvis den eksisterer"""
    default_data = {
        "reisehistorikk": [],
        "reise_chat": [],
        "profil": dict(STANDARD_PROFIL),
    }
    if os.path.exists(PROFIL_FIL):
        try:
            with open(PROFIL_FIL, "r", encoding="utf-8") as f:
                innhold = f.read().strip()
                if not innhold:
                    return default_data
                lagret = json.loads(innhold)
                if not isinstance(lagret, dict):
                    return default_data
                if "reisehistorikk" not in lagret or not isinstance(
                    lagret["reisehistorikk"], list
                ):
                    lagret["reisehistorikk"] = []
                if "reise_chat" not in lagret or not isinstance(lagret["reise_chat"], list):
                    lagret["reise_chat"] = []
                lagret["profil"] = _normaliser_profil(lagret.get("profil"))
                return lagret
        except Exception:
            return default_data
    return default_data


def lagre_data(historikk, chat, profil=None):
    """Lagrer reisehistorikk, chatlogg og reiseprofil til JSON-filen"""
    try:
        profil_a_lagre = _normaliser_profil(
            profil if profil is not None else st.session_state.get("profil", STANDARD_PROFIL)
        )
        data_til_lagring = {
            "reisehistorikk": historikk if isinstance(historikk, list) else [],
            "reise_chat": chat if isinstance(chat, list) else [],
            "profil": profil_a_lagre,
        }
        with open(PROFIL_FIL, "w", encoding="utf-8") as f:
            json.dump(data_til_lagring, f, ensure_ascii=False, indent=4)
    except Exception as e:
        st.error(f"Kunne ikke lagre data: {e}")


# Last inn data ved oppstart og klargjør session_state
lagrede_data = last_inn_data()
if "reisehistorikk" not in st.session_state:
    st.session_state.reisehistorikk = lagrede_data["reisehistorikk"]
if "reise_chat" not in st.session_state:
    st.session_state.reise_chat = lagrede_data["reise_chat"]
if "profil" not in st.session_state:
    st.session_state.profil = _normaliser_profil(lagrede_data.get("profil"))
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

# Sikrer overnatting-tekster selv om eldre translations.py mangler nøkler
_HOTELL_TEKST_FALLBACK = {
    "NO": {
        "fane_hotell": "🛏️ Overnatting",
        "hjem_metric_hoteller": "🛏️ Unik overnatting",
        "hotell_header": "🛏️ Unik overnatting",
        "hotell_sok": "🔍 Søk etter navn eller by (overnatting)",
        "hotell_sorter_type": "Sorter etter type",
        "hotell_pris": "💰 Prisnivå:",
        "hotell_ingen_treff": "Ingen overnatting matchet søket ditt.",
        "hotell_i_db": "{0} overnattingsteder i databasen",
        "hotell_kart_expander": "🗺️ Kart over overnatting",
        "hotell_pa_kart": "{0} overnattingsteder på kartet",
        "type_hotell": "Overnatting",
        "sank_min_score_fast": "Perler: minimum {0}/10 unikhet.",
        "hotell_min_unikhet": "Overnatting: minimum {0}/10 unikhet.",
        "mat_min_unikhet": "Mat: minimum {0}/10 unikhet.",
        "sank_rapport": "Foreslått: {0} · Vises: {1} · Allerede i appen: {2} · Lav score: {3} · Uten kart: {4} · Ugyldig felt: {5}",
        "sank_ingen_detalj": "(lav score: {0}, allerede i appen: {1}, ugyldig felt: {2})",
        "sank_allerede_i_db": "✓ Finnes allerede i appen — lagring hoppes over.",
        "sank_uten_koordinater": "⚠️ Kartposisjon ikke funnet ennå — kan lagres og oppdateres senere.",
        "sank_ingen_tom": "KI returnerte ingen forslag for dette området.",
    },
    "EN": {
        "fane_hotell": "🛏️ Stays",
        "hjem_metric_hoteller": "🛏️ Unique stays",
        "hotell_header": "🛏️ Unique places to stay",
        "hotell_sok": "🔍 Search by name or city (stays)",
        "hotell_sorter_type": "Filter by type",
        "hotell_pris": "💰 Price level:",
        "hotell_ingen_treff": "No stays matched your search.",
        "hotell_i_db": "{0} stays in the database",
        "hotell_kart_expander": "🗺️ Map of stays",
        "hotell_pa_kart": "{0} stays on the map",
        "type_hotell": "Stay",
        "sank_min_score_fast": "Gems: minimum {0}/10 uniqueness.",
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
            st.session_state.reisehistorikk,
            st.session_state.reise_chat,
            st.session_state.profil,
        )
        st.session_state._profil_lagret_snapshot = snapshot


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
            "hovedinteresse": _profil["hovedinteresse"],
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


def detekter_perle_fra_ai_svar(ai_tekst):
    """Finner mulig sted i AI-svaret (JSON først, deretter regex)."""
    if not ai_tekst:
        return None

    kandidat = parse_agent_perle_fra_ai_svar(ai_tekst)
    if kandidat:
        kandidat = _juster_agent_perle_fra_chat_tekst(kandidat, ai_tekst)
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
    with st.container(border=True):
        st.info(_chat_agent_oppdaget_tekst(kandidat))
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
    return tr("type_perle", "Perle")


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
    profil = _normaliser_profil(st.session_state.get("profil"))
    interesse = profil.get("hovedinteresse", "Kultur & Historie")
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
            "NEVER suggest any of a country's 5 largest cities (e.g. in Italy: Rome, Milan, Naples, Turin, Palermo). "
            f"Tailor slightly to traveller interest: {interesse}."
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
            "ALDRI foreslå noen av landets 5 største byer (f.eks. i Italia: Roma, Milano, Napoli, Torino, Palermo). "
            f"Tilpass litt til reiseinteresse: {interesse}."
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
    hovedinteresse = profil["hovedinteresse"]

    intern_kontekst = _bygg_rag_kontekst(sporsmal, hovedinteresse)

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
            f"The user travels as {reise_folge} on a {budsjett} budget, focused on {hovedinteresse}. "
            "Suggest off-the-beaten-path places with local character. "
            "Avoid mainstream tourism, iconic defaults like the Eiffel Tower, and well-visited blockbuster museums. "
            "Reply briefly and enthusiastically (max 5 sentences). "
            "Mention places as: Name (City, Country). "
            f"{_restaurant_ki_hint(hovedinteresse == 'Mat & Vin')}"
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
            f"Brukeren reiser som {reise_folge} med et {budsjett}-budsjett, og har hovedfokus på {hovedinteresse}. "
            "Foreslå aktivt off-the-beaten-path-steder med lokal karakter, særegen historie eller quirky opplevelser. "
            "Unngå mainstream turisme, typiske turistfeller, store resorter, velbesøkte museer og ikoniske standardvalg som Eiffeltårnet. "
            "Svar kort, engasjerende, spesifikt og entusiastisk (maks 5 setninger). "
            "Nevn gjerne konkrete steder med formatet: Sted (By, Land). "
            f"{_restaurant_ki_hint(hovedinteresse == 'Mat & Vin')}"
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


def vis_sted_foto(sted, key_suffix=""):
    """Viser forhåndslagrede bilder med én gang; ellers lazy Wikipedia-oppslag."""
    suffix = key_suffix or sted.get("id", "")
    forhånd = (sted.get("image_url") or "").strip()
    if forhånd:
        st.image(forhånd, use_container_width=True)
        st.caption(tr("bilde_kilde").format(sted.get("navn", "")))
        return

    last_nokkel = f"foto_last_{suffix}"
    if not st.session_state.get("bilde_autoload_wiki"):
        if not st.session_state.get(last_nokkel):
            if st.button(
                tr("bilde_vis_knapp"),
                key=f"foto_btn_{suffix}",
                use_container_width=True,
            ):
                st.session_state[last_nokkel] = True
                st.rerun()
            return

    bilde_url = _hent_sted_bilde_url_cached(
        sted.get("id", ""),
        sted.get("navn", ""),
        sted.get("by", ""),
        sted.get("land", ""),
        sted.get("latitude"),
        sted.get("longitude"),
        sted.get("type", ""),
        sted.get("profil_kategori", ""),
        sted.get("image_url", ""),
    )
    if bilde_url:
        st.image(bilde_url, use_container_width=True)
        st.caption(tr("bilde_kilde").format(sted.get("navn", "")))


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
        st.toast(tr("favoritt_lagt_til"))


def render_reiseplan_knapp_agent(kandidat, key_suffix):
    if st.button(
        tr("favoritt_knapp"),
        key=f"plan_{key_suffix}",
        type="secondary",
    ):
        add_itinerary_item(agent_perle_til_reiseplan_sted(kandidat))
        st.toast(tr("favoritt_lagt_til"))


KART_FARGE_MAT = "#1B5E20"
KART_FARGE_HOTELL = "#6D4C41"
KART_FARGE_KULTUR = "#6A1B9A"
KART_FARGE_NATUR = "#00838F"
KART_FARGE_GOLF = "#004D40"

KART_KATEGORI_FARGER = {
    "restaurant": KART_FARGE_MAT,
    "hotel": KART_FARGE_HOTELL,
    "hotell": KART_FARGE_HOTELL,
    "Mat & Vin": KART_FARGE_MAT,
    "Kultur & Historie": KART_FARGE_KULTUR,
    "Natur & Aktivitet": KART_FARGE_NATUR,
    "Golf": KART_FARGE_GOLF,
    "gastronomi": KART_FARGE_MAT,
    "kultur": KART_FARGE_KULTUR,
    "kafé": KART_FARGE_MAT,
    "kafe": KART_FARGE_MAT,
    "natur": KART_FARGE_NATUR,
    "historie": KART_FARGE_KULTUR,
    "museum": KART_FARGE_KULTUR,
    "arkitektur": KART_FARGE_KULTUR,
    "urbanexploring": KART_FARGE_KULTUR,
    "overnaturlig": KART_FARGE_KULTUR,
    "spøkstad": KART_FARGE_KULTUR,
    "eventyr": KART_FARGE_NATUR,
    "golf": KART_FARGE_GOLF,
    "friluft": KART_FARGE_NATUR,
    "sport": KART_FARGE_NATUR,
    "aktivitet": KART_FARGE_NATUR,
}
DEFAULT_KART_FARGE = "#B388FF"
KART_SENTRUM_FARGE = "#2563EB"
KART_TILES = "CartoDB Voyager"


def _morkn_farge(hex_farge, factor=0.82):
    h = hex_farge.lstrip("#")
    if len(h) != 6:
        return hex_farge
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"


def _nytt_folium_kart(location, zoom_start=4):
    return folium.Map(location=location, zoom_start=zoom_start, tiles=KART_TILES)


def _lag_modern_kart_div_icon(farge, *, variant="sted", label=None):
    """SVG-kartnål med gradient, hvit kant og skygge (Material/Maps-stil)."""
    uid = abs(hash((farge, variant, label))) % 1_000_000
    farge_mork = _morkn_farge(farge)
    label_safe = html.escape(str(label)) if label is not None else ""

    if variant == "sentrum":
        storrelse = 40
        html_str = (
            f'<div style="width:{storrelse}px;height:{storrelse}px;position:relative;">'
            f'<div style="position:absolute;inset:0;border-radius:50%;'
            f'background:rgba(37,99,235,0.18);"></div>'
            f'<div style="position:absolute;inset:6px;border-radius:50%;'
            f'background:linear-gradient(145deg,#60a5fa,{KART_SENTRUM_FARGE});'
            f'border:3px solid #fff;box-shadow:0 4px 16px rgba(29,78,216,0.45);'
            f'display:flex;align-items:center;justify-content:center;">'
            f'<div style="width:10px;height:10px;border-radius:50%;background:#fff;'
            f'box-shadow:0 0 0 2px rgba(255,255,255,0.5);"></div></div></div>'
        )
        return folium.DivIcon(
            html=html_str,
            icon_size=(storrelse, storrelse),
            icon_anchor=(storrelse // 2, storrelse // 2),
            class_name="he-kart-sentrum",
        )

    pin_w, pin_h = 34, 44
    if label:
        inner_symbol = (
            f'<circle cx="17" cy="12" r="8.5" fill="#ffffff"/>'
            f'<text x="17" y="12.5" text-anchor="middle" dominant-baseline="middle" '
            f'font-size="10" font-weight="700" fill="{farge}" '
            f'font-family="Segoe UI,Arial,sans-serif">{label_safe}</text>'
        )
    else:
        inner_symbol = '<circle cx="17" cy="12" r="5" fill="#ffffff" opacity="0.96"/>'

    html_str = (
        f'<div style="width:{pin_w}px;height:{pin_h}px;margin:0;padding:0;">'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{pin_w}" height="{pin_h}" '
        f'viewBox="0 0 34 44" role="img" aria-hidden="true">'
        f"<defs>"
        f'<filter id="he-sh-{uid}" x="-25%" y="-15%" width="150%" height="140%">'
        f'<feDropShadow dx="0" dy="2.5" stdDeviation="2.2" flood-color="#0f172a" '
        f'flood-opacity="0.32"/></filter>'
        f'<linearGradient id="he-g-{uid}" x1="0%" y1="0%" x2="0%" y2="100%">'
        f'<stop offset="0%" stop-color="{farge}"/><stop offset="100%" stop-color="{farge_mork}"/>'
        f"</linearGradient></defs>"
        f'<path filter="url(#he-sh-{uid})" fill="url(#he-g-{uid})" stroke="#ffffff" '
        f'stroke-width="2.5" stroke-linejoin="round" '
        f'd="M17 2C10.1 2 5 7.1 5 13.2c0 7.5 12 28.8 12 28.8s12-21.3 12-28.8C29 7.1 23.9 2 17 2z"/>'
        f"{inner_symbol}</svg></div>"
    )
    return folium.DivIcon(
        html=html_str,
        icon_size=(pin_w, pin_h),
        icon_anchor=(pin_w // 2, pin_h - 1),
        class_name="he-modern-marker",
    )


def kart_markor_farge(sted):
    """Returnerer hex-farge for kartmarkør basert på profil_kategori, type eller source_type."""
    if sted.get("source_type") == "restaurant":
        return KART_KATEGORI_FARGER["restaurant"]
    if sted.get("source_type") == "hotel":
        return KART_KATEGORI_FARGER["hotel"]

    for key in (sted.get("profil_kategori"), sted.get("type")):
        if not key:
            continue
        if key in KART_KATEGORI_FARGER:
            return KART_KATEGORI_FARGER[key]
        low = str(key).lower()
        if low in KART_KATEGORI_FARGER:
            return KART_KATEGORI_FARGER[low]
    return DEFAULT_KART_FARGE


def _hent_sted_bilde_for_kart(sted):
    if sted.get("image_url"):
        return sted["image_url"]
    return _hent_sted_bilde_url_cached(
        sted.get("id", ""),
        sted.get("navn", ""),
        sted.get("by", ""),
        sted.get("land", ""),
        sted.get("latitude"),
        sted.get("longitude"),
        sted.get("type", ""),
        sted.get("profil_kategori", ""),
        sted.get("image_url", ""),
    )


def lag_sted_kart_popup(sted):
    """HTML-popup med navn, sted og bilde hvis tilgjengelig."""
    navn = html.escape(sted.get("navn", ""))
    by = html.escape(sted.get("by", ""))
    land = html.escape(sted.get("land", ""))
    kategori = html.escape(
        sted.get("profil_kategori") or sted.get("type") or sted.get("source_type", "")
    )
    bilde_html = ""
    # Kun forhåndslagret bilde-URL i kart-popup (unngår trege API-kall ved hver rerun).
    bilde_url = (sted.get("image_url") or "").strip()
    if bilde_url:
        safe_url = html.escape(bilde_url, quote=True)
        bilde_html = (
            f'<img src="{safe_url}" width="150" alt="{navn}" '
            'style="border-radius:8px;display:block;margin-top:5px;">'
        )
    return (
        f'<div style="font-family:Segoe UI,sans-serif;max-width:240px;">'
        f"<strong>{navn}</strong><br>"
        f"📍 {by}, {land}<br>"
        f'<span style="color:#6B7280;font-size:0.85em;">{kategori}</span>'
        f"{bilde_html}"
        f"</div>"
    )


def legg_til_sted_markor(kart, sted):
    """Legger til én moderne fargekodet kartnål med popup."""
    farge = kart_markor_farge(sted)
    folium.Marker(
        location=[sted["latitude"], sted["longitude"]],
        tooltip=f"{sted.get('navn', '')} ({sted.get('by', '')})",
        popup=folium.Popup(lag_sted_kart_popup(sted), max_width=280),
        icon=_lag_modern_kart_div_icon(farge),
    ).add_to(kart)


def lag_stedskart(steder, sentrum=None, zoom_start=4):
    """Folium-kart med kategorifarger og bilde-popup for en liste steder."""
    if sentrum:
        m = _nytt_folium_kart([sentrum[0], sentrum[1]], zoom_start=zoom_start)
    else:
        m = _nytt_folium_kart([54.0, 14.0], zoom_start=zoom_start)
    for sted in steder:
        if sted.get("latitude") is None or sted.get("longitude") is None:
            continue
        legg_til_sted_markor(m, sted)
    return m


def lag_perler_kart(steder):
    """Folium-kart for skjulte perler (bakoverkompatibel wrapper)."""
    return lag_stedskart(steder)


def optimaliser_reiserute_naermeste_nabo(items):
    """Sorterer reiseplan geografisk med nearest-neighbor fra første sted."""
    if len(items) <= 1:
        return list(items)

    med_koord = [
        i for i in items if i.get("latitude") is not None and i.get("longitude") is not None
    ]
    uten_koord = [
        i for i in items if i.get("latitude") is None or i.get("longitude") is None
    ]
    if len(med_koord) <= 1:
        return list(items)

    gjenstaende = list(med_koord[1:])
    rekkefolge = [med_koord[0]]
    while gjenstaende:
        sist = rekkefolge[-1]
        lat1, lon1 = float(sist["latitude"]), float(sist["longitude"])
        nærmeste_idx = 0
        kortest = float("inf")
        for idx, kandidat in enumerate(gjenstaende):
            avstand = regn_ut_avstand_km(
                lat1, lon1, float(kandidat["latitude"]), float(kandidat["longitude"])
            )
            if avstand < kortest:
                kortest = avstand
                nærmeste_idx = idx
        rekkefolge.append(gjenstaende.pop(nærmeste_idx))
    return rekkefolge + uten_koord


def lag_chat_oppdag_kart(lat, lon, sentrum_navn="", radius_km=50):
    """Chat-kart med blått søkepunkt og nærliggende perler/spisesteder innen radius."""
    m = _nytt_folium_kart([lat, lon], zoom_start=9)
    navn = html.escape(sentrum_navn or "Søkepunkt")
    folium.Marker(
        location=[lat, lon],
        tooltip=sentrum_navn or "Søkepunkt",
        popup=f"<b>{navn}</b>",
        icon=_lag_modern_kart_div_icon(KART_SENTRUM_FARGE, variant="sentrum"),
    ).add_to(m)

    for sted in _alle_steder_i_databasen():
        slat = sted.get("latitude")
        slon = sted.get("longitude")
        if slat is None or slon is None:
            continue
        if regn_ut_avstand_km(lat, lon, float(slat), float(slon)) > radius_km:
            continue
        legg_til_sted_markor(m, sted)
    return m


def lag_radar_kart(treff_liste, sentrum=None, sentrum_navn="", zoom_start=7):
    if sentrum:
        m = _nytt_folium_kart([sentrum[0], sentrum[1]], zoom_start=zoom_start)
        folium.Marker(
            location=[sentrum[0], sentrum[1]],
            tooltip=sentrum_navn,
            popup=folium.Popup(
                f'<div style="font-family:Segoe UI,sans-serif;"><b>{html.escape(sentrum_navn or "")}</b></div>',
                max_width=220,
            ),
            icon=_lag_modern_kart_div_icon(KART_SENTRUM_FARGE, variant="sentrum"),
        ).add_to(m)
    else:
        m = _nytt_folium_kart([54.0, 14.0], zoom_start=zoom_start)

    for treff in treff_liste:
        place = treff["data"]
        popup = f"<b>{place['navn']}</b><br>{place['by']}, {place['land']}<br>{treff['avstand']} km"
        folium.Marker(
            location=[place["latitude"], place["longitude"]],
            tooltip=f"{place['navn']} ({place['by']})",
            popup=folium.Popup(popup, max_width=260),
            icon=_lag_modern_kart_div_icon(kart_markor_farge(place)),
        ).add_to(m)
    return m


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

    treff.sort(key=lambda x: (_profil_sorteringsnøkkel(x["data"]), x["avstand"]))
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


def filtrer_data(data):
    """Filtrerer bort steder uten gyldige koordinater."""
    return [
        d
        for d in data
        if d.get("latitude") is not None and d.get("longitude") is not None
    ]


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


def _filtrer_perler_liste(soketekst, type_filter, alle_type_label, radar_treff):
    """Perlesøk i hele databasen; uten søk vises kun radartreff (skjulte perler)."""
    if soketekst or type_filter != alle_type_label:
        kandidater = SKJULTE_PERLER_DB
    else:
        kandidater = [
            treff["data"]
            for treff in radar_treff
            if _effektiv_kilde_type(treff["data"]) == "hidden_gem"
        ]
    filtrert = []
    for perle in kandidater:
        if type_filter != alle_type_label and perle.get("type") != type_filter:
            continue
        if not _sted_matcher_sok(perle, soketekst):
            continue
        filtrert.append(perle)
    return filtrert


def _aktiv_hovedinteresse():
    return _normaliser_profil(st.session_state.get("profil"))["hovedinteresse"]


def _samlet_sted_tekst(sted):
    return " ".join(
        str(sted.get(felt, "") or "") for felt in ("navn", "beskrivelse", "tips", "type")
    ).lower()


def _type_matcher_profil(sted_type, hovedinteresse):
    sted_type = (sted_type or "").lower()
    if hovedinteresse == "Mat & Vin":
        return sted_type in {
            "gastronomi",
            "kafé",
            "kafe",
            "restaurant",
            "mat",
            "vin",
            "spisested",
        }
    if hovedinteresse == "Kultur & Historie":
        return sted_type in {
            "kultur",
            "historie",
            "museum",
            "arkitektur",
            "kafé",
            "kafe",
            "spøkstad",
            "urbanexploring",
            "overnaturlig",
        }
    if hovedinteresse == "Natur & Aktivitet":
        return sted_type in {"natur", "eventyr", "friluft"}
    if hovedinteresse == "Sport":
        return sted_type in {"sport", "aktivitet", "eventyr", "ski", "sykkel"}
    if hovedinteresse == "Golf":
        return sted_type == "golf"
    return False


def matcher_profil_interesse(sted, hovedinteresse=None):
    """True hvis stedet matcher brukerens valgte hovedinteresse."""
    hovedinteresse = hovedinteresse or _aktiv_hovedinteresse()
    if sted.get("profil_kategori") == hovedinteresse:
        return True

    sted_type = (sted.get("type") or "").lower()
    tekst = _samlet_sted_tekst(sted)

    if hovedinteresse == "Golf":
        return sted_type == "golf" or "golf" in tekst

    if _type_matcher_profil(sted_type, hovedinteresse):
        return True

    if hovedinteresse == "Mat & Vin":
        return any(
            nøkkel in tekst
            for nøkkel in (
                "mat",
                "vin",
                "restaurant",
                "spis",
                "gastronomi",
                "kafé",
                "kafe",
                "bryggeri",
                "øl",
            )
        )
    if hovedinteresse == "Kultur & Historie":
        return any(
            nøkkel in tekst
            for nøkkel in ("kultur", "historie", "museum", "unesco", "kirke", "borg", "festning")
        )
    if hovedinteresse == "Natur & Aktivitet":
        return any(
            nøkkel in tekst
            for nøkkel in ("natur", "fjell", "vandring", "nasjonalpark", "eventyr", "aktiv", "tur")
        )
    if hovedinteresse == "Sport":
        return any(
            nøkkel in tekst
            for nøkkel in (
                "sport",
                "ski",
                "sykkel",
                "løp",
                "fotball",
                "tennis",
                "padel",
                "klatring",
                "dykking",
                "surf",
                "rafting",
                "arena",
                "stadion",
            )
        )
    return False


def _profil_sorteringsnøkkel(sted, hovedinteresse=None):
    """Lavere verdi = vises først. Type-treff prioriteres over tekst-treff."""
    hovedinteresse = hovedinteresse or _aktiv_hovedinteresse()
    if not matcher_profil_interesse(sted, hovedinteresse):
        return (1, 2, (sted.get("navn") or "").lower())

    sted_type = (sted.get("type") or "").lower()
    if hovedinteresse == "Golf":
        prioritet = 0 if sted_type == "golf" else 1
        return (0, prioritet, (sted.get("navn") or "").lower())

    if hovedinteresse == "Sport":
        prioritet = 0 if _type_matcher_profil(sted_type, hovedinteresse) else 1
        return (0, prioritet, (sted.get("navn") or "").lower())

    if _type_matcher_profil(sted_type, hovedinteresse):
        return (0, 0, (sted.get("navn") or "").lower())
    return (0, 1, (sted.get("navn") or "").lower())


def sorter_steder_etter_profil(steder, hovedinteresse=None):
    hovedinteresse = hovedinteresse or _aktiv_hovedinteresse()
    return sorted(steder, key=lambda s: _profil_sorteringsnøkkel(s, hovedinteresse))


def _profil_tekst_naturlig(hovedinteresse):
    """Gjør profilinteresse lesbar i setninger (f.eks. «kultur og historie»)."""
    return (hovedinteresse or "").replace(" & ", " og ").lower()


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


def bygg_radar_ki_innsikt(valgt_land, radar_treff, hovedinteresse):
    """Lager dynamisk innsikt for radar ved landvalg."""
    if not valgt_land:
        return ""
    profil_tekst = _profil_tekst_naturlig(hovedinteresse)
    antall = len(radar_treff or [])
    if antall == 0:
        return tr("radar_innsikt_tom").format(valgt_land, profil_tekst)

    naermeste = min(radar_treff, key=lambda t: t.get("avstand", 999999))
    by_navn = naermeste.get("data", {}).get("by") or valgt_land
    return tr("radar_innsikt_treff").format(valgt_land, antall, by_navn, profil_tekst)


def sted_tittel_med_profil(sted, standard_emoji):
    """Tittel med 🌟 når stedet matcher aktiv hovedinteresse."""
    navn = sted.get("navn", "")
    emoji_del = f"{standard_emoji} " if standard_emoji else ""
    if matcher_profil_interesse(sted):
        return f"🌟 {emoji_del}{navn}".strip()
    return f"{emoji_del}{navn}".strip()


def _render_travel_card_html(sted, tittel, *, pris_tekst=None):
    """Kompakt HTML-boks for stedsbeskrivelse i perle/mat/overnatting-lister."""
    meta = f"📍 {html.escape(sted.get('by', ''))}, {html.escape(sted.get('land', ''))}"
    type_vis = vis_sted_type(sted)
    if type_vis:
        meta += f" · {html.escape(type_vis)}"

    extras = []
    if pris_tekst:
        extras.append(f'<p class="travel-card-price">{html.escape(pris_tekst)}</p>')
    if sted.get("tips"):
        extras.append(f'<p class="travel-tip">💡 {html.escape(sted["tips"])}</p>')
    if sted.get("beste_tid"):
        extras.append(
            f'<p class="travel-card-time">{html.escape(tr("perler_beste_tid").format(sted["beste_tid"]))}</p>'
        )
    extra_html = "\n".join(extras)

    return f"""
<div class="travel-card">
    <h3 class="travel-card-title">{html.escape(tittel)}</h3>
    <p class="travel-card-meta">{meta}</p>
    <p class="travel-card-desc">{html.escape(sted.get("beskrivelse") or "")}</p>
    {extra_html}
</div>
"""


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


def _hent_geo_rag_treff(sentrum_lat, sentrum_lon, hovedinteresse):
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

    treff.sort(
        key=lambda t: (_profil_sorteringsnøkkel(t["sted"], hovedinteresse), t["avstand"])
    )
    return _unike_rag_treff(treff)[:RAG_MAX_TREFF]


def _hent_fallback_rag_treff(hovedinteresse):
    alle = _alle_steder_i_databasen()
    matchende = [s for s in alle if matcher_profil_interesse(s, hovedinteresse)]
    sortert = sorter_steder_etter_profil(matchende, hovedinteresse)
    if len(sortert) >= RAG_MAX_TREFF:
        return sortert[:RAG_MAX_TREFF]

    rest = [s for s in alle if s not in matchende]
    return (sortert + sorter_steder_etter_profil(rest, hovedinteresse))[:RAG_MAX_TREFF]


def _bygg_rag_kontekst(sporsmal, hovedinteresse):
    """Bygger RAG-kontekst: geo+nærhet ved lokasjon, ellers profil-fallback."""
    lat, lon, stedsnavn = _hent_lokasjon_fra_sporsmal(sporsmal)
    linjer = []
    geo_modus = False

    if lat is not None and lon is not None:
        geo_treff = _hent_geo_rag_treff(lat, lon, hovedinteresse)
        if geo_treff:
            geo_modus = True
            for t in geo_treff:
                linjer.append(_format_rag_linje(t["sted"], round(t["avstand"], 1)))

    if not linjer:
        for sted in _hent_fallback_rag_treff(hovedinteresse):
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
            f"som skreddersydde innsidertips som matcher brukerens interesse for {hovedinteresse}."
        )

    return (
        f"\n\nDu har tilgang til følgende eksklusive perler fra vår interne database "
        f"som matcher brukerens reiseprofil ({hovedinteresse}):\n{body}\n\n"
        f"Du SKAL flette disse spesifikke anbefalingene naturlig inn i svaret ditt, og fremheve dem "
        f"som skreddersydde innsidertips som matcher brukerens interesse for {hovedinteresse}."
    )



# ========================================
# APPLIKASJONSSTRUKTUR (UI) — faner nederst i skriptet
# ========================================
st.title(T["app_tittel"])
st.caption(T["app_caption"])

fane0, fane_helgeby, fane1, fane2, fane3, fane4, fane5 = st.tabs(
    [
        tr("fane_hjem"),
        tr("fane_helgeby"),
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
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(T["hjem_metric_perler"], len(SKJULTE_PERLER_DB))
    with col2:
        st.metric(T["hjem_metric_spisesteder"], len(LOKALE_SPISESTEDER_DB))
    with col3:
        st.metric(tr("hjem_metric_hoteller"), len(LOKALE_HOTELLER_DB))
    with col4:
        st.metric(
            T["hjem_metric_land"],
            len(set(s["country_code"] or s["land"] for s in SKJULTE_PERLER_DB)),
        )

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
            st.success(
                bygg_radar_ki_innsikt(
                    valgt_land,
                    radar_treff,
                    _aktiv_hovedinteresse(),
                )
            )

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
                st.markdown(
                    f"**{valgt_perle['navn']}** — {valgt_perle['by']}, {valgt_perle['land']}  \n"
                    f"*{vis_sted_type(valgt_perle)}*"
                )
                if valgt_treff["avstand"] > 0:
                    st.caption(f"{valgt_treff['avstand']} km {T['radar_unna']}")
                st.write(valgt_perle.get("beskrivelse", ""))
        elif soke_metode == T["radar_sted_sok"] and not (sted_sok or "").strip():
            st.caption(tr("radar_skriv_sted_hint"))
        elif soke_metode != T["radar_land_sok"] or valgt_land:
            st.info(T["radar_ingen_treff"])

        st.write("---")
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
        filtrerte_perler = sorter_steder_etter_profil(filtrerte_perler)

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

        if filtrerte_perler:
            for i in range(0, len(filtrerte_perler), 3):
                cols = st.columns(3)
                for j in range(3):
                    if i + j < len(filtrerte_perler):
                        p = filtrerte_perler[i + j]
                        perle_tittel = sted_tittel_med_profil(p, _sted_emoji(p))
                        with cols[j]:
                            vis_sted_foto(p, key_suffix=f"perle_{p['id']}")
                            st.markdown(
                                _render_travel_card_html(p, perle_tittel),
                                unsafe_allow_html=True,
                            )
                            view_key = (
                                "mat"
                                if p.get("source_type") == "restaurant"
                                else "overnatting"
                                if p.get("source_type") == "hotel"
                                else "perle"
                            )
                            render_reiseplan_knapp(p, view_key, index=i + j)
        else:
            st.info(T["perler_ingen_treff"])
            if sok_perle and any(
                _sted_matcher_sok(s, sok_perle) for s in LOKALE_SPISESTEDER_DB
            ):
                st.caption(tr("perler_sok_mat_hint").format(sok_perle))


# --- FANE: HELGEBY ---
with fane_helgeby:
    st.header(tr("helgeby_header"))
    st.caption(tr("helgeby_caption"))
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
                st.markdown(
                    f"**{kandidat['by']}**  \n"
                    + tr("helgeby_kandidat_meta").format(
                        kandidat["by"],
                        kandidat["land"],
                        kandidat.get("saerhetsscore", 0),
                    )
                )
                if kandidat.get("hvorfor_helg"):
                    st.markdown(f"**{tr('helgeby_hvorfor')}** {kandidat['hvorfor_helg']}")
                elif kandidat.get("beskrivelse"):
                    st.write(kandidat["beskrivelse"])
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
        st.info(tr("helgeby_ingen"))


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

    filtrert_mat = sorter_steder_etter_profil(filtrert_mat)

    if filtrert_mat:
        for i in range(0, len(filtrert_mat), 3):
            cols = st.columns(3)
            for j in range(3):
                if i + j < len(filtrert_mat):
                    s = filtrert_mat[i + j]
                    mat_tittel = sted_tittel_med_profil(s, "🍽️")
                    with cols[j]:
                        vis_sted_foto(s, key_suffix=f"mat_{s['id']}")
                        st.markdown(
                            _render_travel_card_html(
                                s,
                                mat_tittel,
                                pris_tekst=f"{T['mat_pris']} {s['pris']}",
                            ),
                            unsafe_allow_html=True,
                        )
                        render_reiseplan_knapp(s, "mat", index=i + j)
    else:
        st.info(T["mat_ingen_treff"])


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

    filtrert_hotell = sorter_steder_etter_profil(filtrert_hotell)

    if filtrert_hotell:
        for i in range(0, len(filtrert_hotell), 3):
            cols = st.columns(3)
            for j in range(3):
                if i + j < len(filtrert_hotell):
                    s = filtrert_hotell[i + j]
                    hotell_tittel = sted_tittel_med_profil(s, "🛏️")
                    with cols[j]:
                        vis_sted_foto(s, key_suffix=f"hotell_{s['id']}")
                        st.markdown(
                            _render_travel_card_html(
                                s,
                                hotell_tittel,
                                pris_tekst=f"{tr('hotell_pris')} {s.get('pris', '€€')}",
                            ),
                            unsafe_allow_html=True,
                        )
                        render_reiseplan_knapp(s, "hotell", index=i + j)
    else:
        st.info(tr("hotell_ingen_treff"))


# --- FANE 3: REISE-CHAT ---
with fane3:
    st.header(T["chat_header"])
    st.caption(T["chat_caption"])

    with st.expander(tr("sank_expander"), expanded=False):
        with st.form("sank_perler_form"):
            sank_omrade = st.text_input(
                tr("sank_omrade"),
                placeholder=tr("sank_omrade_ph"),
                key="sank_omrade_input",
            )
            sank_antall = st.slider(
                tr("sank_antall"),
                min_value=3,
                max_value=20,
                value=8,
                key="sank_antall_input",
            )
            st.caption(
                tr("sank_min_score_fast").format(MIN_UNIKHETSGRAD)
                + " "
                + tr("hotell_min_unikhet").format(MIN_UNIKHETSGRAD)
                + " "
                + tr("mat_min_unikhet").format(MIN_UNIKHETSGRAD)
            )
            sank_streng = st.checkbox(
                tr("sank_strict_mode"),
                value=False,
                key="sank_strict_mode_input",
            )
            start_sank = st.form_submit_button(
                tr("sank_knapp"), type="primary", use_container_width=True
            )

        sank_kandidater = list(st.session_state.get("sank_kandidater") or [])
        sank_rapport = st.session_state.get("sank_rapport")

        if start_sank:
            omrade = (sank_omrade or "").strip()
            if not omrade:
                st.error(tr("sank_feil_omrade"))
            else:
                st.session_state.pop("sank_kandidater", None)
                st.session_state.pop("sank_rapport", None)
                try:
                    with st.spinner(tr("sank_spinner").format(omrade)):
                        kandidater, rapport = sanke_perler_for_omrade(
                            omrade,
                            sank_antall,
                            strict_mode=sank_streng,
                        )
                    st.session_state["sank_kandidater"] = kandidater
                    st.session_state["sank_rapport"] = rapport
                    st.session_state["sank_omrade"] = omrade
                    sank_kandidater = list(kandidater or [])
                    sank_rapport = rapport
                except Exception as e:
                    st.error(tr("sank_feil_generell").format(str(e)))
        if sank_rapport:
            st.caption(
                tr("sank_rapport").format(
                    sank_rapport.get("foreslaatt", 0),
                    sank_rapport.get("godkjent", 0),
                    sank_rapport.get("forkastet_duplikat", 0),
                    sank_rapport.get("forkastet_score", 0),
                    sank_rapport.get("forkastet_geo", 0),
                    sank_rapport.get("forkastet_normalisering", 0),
                )
            )
            if st.session_state.get("vis_perf_debug"):
                st.caption(
                    tr("perf_sank").format(
                        sank_rapport.get("tid_geo_s", 0),
                        sank_rapport.get("tid_total_s", 0),
                    )
                )

        if sank_kandidater:
            sortering = st.selectbox(
                tr("sank_sorter"),
                [
                    tr("sank_sorter_score_desc"),
                    tr("sank_sorter_score_asc"),
                    tr("sank_sorter_navn"),
                ],
                key="sank_sortering",
            )
            if sortering == tr("sank_sorter_score_desc"):
                visningsliste = sorted(
                    sank_kandidater,
                    key=lambda k: k.get("saerhetsscore", 0),
                    reverse=True,
                )
            elif sortering == tr("sank_sorter_score_asc"):
                visningsliste = sorted(
                    sank_kandidater,
                    key=lambda k: k.get("saerhetsscore", 0),
                )
            else:
                visningsliste = sorted(
                    sank_kandidater,
                    key=lambda k: (k.get("navn") or "").lower(),
                )

            if st.button(tr("sank_lagre_alle"), use_container_width=True, key="sank_save_all"):
                lagret_antall = 0
                for kandidat in sank_kandidater:
                    if kandidat.get("allerede_i_db"):
                        continue
                    lagret = lagre_agent_perle_i_db(kandidat)
                    _legg_lagret_sted_i_lokale_lister(lagret)
                    lagret_antall += 1
                st.session_state["sank_kandidater"] = []
                if lagret_antall:
                    st.session_state["sank_rapport"] = None
                st.success(tr("sank_lagret_alle").format(lagret_antall))
                st.rerun()

            for idx, kandidat in enumerate(visningsliste):
                with st.container(border=True):
                    st.markdown(
                        f"**{kandidat['navn']}**  \n"
                        + tr("sank_kandidat_meta").format(
                            _kilde_type_visning(kandidat),
                            kandidat.get("saerhetsscore", 0),
                            kandidat["by"],
                            kandidat["land"],
                        )
                    )
                    if kandidat.get("beskrivelse"):
                        st.write(kandidat["beskrivelse"])
                    if kandidat.get("latitude") is None or kandidat.get("longitude") is None:
                        st.caption(tr("sank_uten_koordinater"))
                    lagringsstatus = kandidat.get("lagringsstatus") or _kandidat_lagringsstatus(
                        kandidat
                    )
                    if lagringsstatus.get("melding_nokkel") and (
                        lagringsstatus.get("allerede_synlig") or lagringsstatus.get("erstatter")
                    ):
                        st.caption(tr(lagringsstatus["melding_nokkel"]))
                    col_plan, col_db = st.columns([1, 1])
                    with col_plan:
                        render_reiseplan_knapp_agent(
                            kandidat, f"sank_{idx}_{kandidat['agent_id']}"
                        )
                    with col_db:
                        if lagringsstatus.get("allerede_synlig"):
                            pass
                        elif st.button(
                            _chat_lagre_tekster(kandidat)[0],
                            key=f"sank_save_{idx}_{kandidat['agent_id']}",
                            use_container_width=True,
                        ):
                            lagret = lagre_agent_perle_i_db(kandidat)
                            _legg_lagret_sted_i_lokale_lister(lagret)
                            rest = st.session_state.get("sank_kandidater", [])
                            st.session_state["sank_kandidater"] = [
                                k for k in rest if k.get("agent_id") != kandidat.get("agent_id")
                            ]
                            st.toast(_chat_lagre_tekster(kandidat)[1])
                            st.rerun()
        elif sank_rapport and sank_rapport.get("godkjent", 0) == 0:
            if sank_rapport.get("foreslaatt", 0) == 0:
                st.info(tr("sank_ingen_tom"))
            else:
                st.info(
                    tr("sank_ingen")
                    + " "
                    + tr("sank_ingen_detalj").format(
                        sank_rapport.get("forkastet_score", 0),
                        sank_rapport.get("forkastet_duplikat", 0),
                        sank_rapport.get("forkastet_normalisering", 0),
                    )
                )

    if not st.session_state.reise_chat:
        st.info(tr("chat_ingen_meldinger"))

    if st.session_state.reise_chat:
        html_innhold = """
        <html>
        <head>
            <style>
                body { font-family: 'Inter', 'Segoe UI', sans-serif; padding: 30px; color: #2A2622; background: #F5F2EE; line-height: 1.6; }
                .header { text-align: center; border-bottom: 2px solid #9A8B7C; padding-bottom: 10px; margin-bottom: 30px; }
                .message-box { margin-bottom: 20px; padding: 15px; border-radius: 10px; }
                .user { background-color: #FFFFFF; border-left: 4px solid #C9BDB0; color: #45403A; }
                .assistant { background-color: #FCFAF7; border-left: 4px solid #9A8B7C; color: #45403A; }
                .sender-name { font-weight: 700; font-size: 0.9em; color: #564E47; margin-bottom: 5px; }
                .map-hint { font-size: 0.85em; color: #5E5852; font-style: italic; margin-top: 10px; }
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
                ny_chat_kart = lag_chat_oppdag_kart(lat, lon, sted_for_kart)
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
            st.session_state.reisehistorikk,
            st.session_state.reise_chat,
            st.session_state.profil,
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
                    st.success(tr("reiseplan_lagret_ok"))
                    st.rerun()

    if not itinerary_items:
        st.info(T["reiseplan_tom"])
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
            m = _nytt_folium_kart([54.0, 14.0], zoom_start=4)
            rekke_nr = 0
            for item in itinerary_items:
                if item.get("latitude") and item.get("longitude"):
                    rekke_nr += 1
                    folium.Marker(
                        location=[item["latitude"], item["longitude"]],
                        tooltip=f"{rekke_nr}. {item['navn']} ({item['by']})",
                        popup=folium.Popup(
                            f"<b>{rekke_nr}. {html.escape(item['navn'])}</b><br>"
                            f"{html.escape(item['by'])}, {html.escape(item['land'])}",
                            max_width=260,
                        ),
                        icon=_lag_modern_kart_div_icon(
                            kart_markor_farge(item),
                            label=str(rekke_nr),
                        ),
                    ).add_to(m)
            st_folium(m, width=900, height=420, returned_objects=[], key="reiseplan_kart")

        for item in itinerary_items:
            with st.container(border=True):
                vis_sted_foto(item, key_suffix=f"plan_{item['id']}")
                st.markdown(
                    _render_travel_card_html(item, item.get("navn", "")),
                    unsafe_allow_html=True,
                )
                if st.button(
                    tr("reiseplan_fjern"),
                    key=f"reiseplan_remove_{item['id']}",
                    use_container_width=True,
                ):
                    remove_itinerary_item(item["id"])
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

    if alle_labels:
        c_fra, c_til = st.columns(2)
        with c_fra:
            fra_label = st.selectbox(T["transport_fra"], alle_labels, key="tp_fra_select")
        with c_til:
            til_label = st.selectbox(T["transport_til"], alle_labels, key="tp_til_select")

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
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                st.link_button(
                    T["transport_lenke_google"],
                    eksterne["google"],
                    use_container_width=True,
                    key="tp_link_google",
                )
            with c2:
                st.link_button(
                    T["transport_lenke_omio"],
                    eksterne["omio"],
                    use_container_width=True,
                    key="tp_link_omio",
                )
            with c3:
                st.link_button(
                    T["transport_lenke_rome2rio"],
                    eksterne["rome2rio"],
                    use_container_width=True,
                    key="tp_link_rome2rio",
                )
            with c4:
                st.link_button(
                    T["transport_lenke_trainline"],
                    eksterne["trainline"],
                    use_container_width=True,
                    key="tp_link_trainline",
                )



# ========================================
# FOOTER
# ========================================
st.markdown("---")
