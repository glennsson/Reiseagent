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
from data_store import (
    add_itinerary_item,
    get_connection,
    get_itinerary_items,
    get_places,
    init_db,
    normalize_place,
    remove_itinerary_item,
)
from translations import TEKSTER
from place_images import hent_sted_bilde_url
from transport_planner import (
    bygg_eksterne_planleggere,
    bygg_stedvalg_fra_database,
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


load_dotenv()

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
T = {**TEKSTER["NO"], **TEKSTER[_spraak]}


def tr(key, default=None):
    """Oversettelse: aktivt språk med norsk fallback."""
    if default is not None:
        return T.get(key, default)
    return T.get(key, TEKSTER["NO"].get(key, key))


# AI-modell (OpenRouter) — velg i sidemeny eller sett OPENROUTER_MODEL i secrets.toml
MODELL_ALTERNATIVER = [
    ("google/gemini-2.5-flash", "Gemini 2.5 Flash"),
    ("google/gemini-2.0-flash-001", "Gemini 2.0 Flash"),
    ("openai/gpt-4o-mini", "GPT-4o Mini"),
    ("anthropic/claude-3.5-sonnet", "Claude 3.5 Sonnet"),
    ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B"),
]


def _standard_modell():
    try:
        return st.secrets.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
    except Exception:
        return os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")


_modell_ids = [m[0] for m in MODELL_ALTERNATIVER]
_modell_labels = {m[0]: m[1] for m in MODELL_ALTERNATIVER}
_standard_modell_id = _standard_modell()
_modell_default_idx = _modell_ids.index(_standard_modell_id) if _standard_modell_id in _modell_ids else 0

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
    ny_hovedinteresse = st.selectbox(
        tr("profil_hovedinteresse"),
        options=PROFIL_INTERESSE,
        index=PROFIL_INTERESSE.index(_profil["hovedinteresse"]),
        key="profil_hovedinteresse",
    )
    _lagre_profil_ved_endring(
        {
            "reise_folge": ny_reise_folge,
            "budsjett": ny_budsjett,
            "hovedinteresse": ny_hovedinteresse,
        }
    )
    st.caption(tr("profil_lagres_auto"))

API_KEY = os.environ.get("OPENROUTER_API_KEY", "") or st.secrets.get("OPENROUTER_API_KEY", "")
MODEL = st.session_state.get("openrouter_model", _standard_modell_id)
URL = "https://openrouter.ai/api/v1/chat/completions"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://localhost:8501",
    "X-Title": "Hemmelige Europa",
}

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


def _score_saerhetstekst(tekst):
    tekst_l = (tekst or "").lower()
    score = 5
    plusspoeng = {
        "skjult": 2,
        "hemmelig": 2,
        "off-the-beaten-path": 2,
        "eksentrisk": 2,
        "quirky": 2,
        "lokal": 1,
        "ukjent": 1,
        "forlatt": 1,
        "bakgård": 1,
        "uvanlig": 1,
        "unik": 1,
    }
    minuspoeng = {
        "eiffeltårnet": 4,
        "eiffel tower": 4,
        "resort": 2,
        "all inclusive": 2,
        "mainstream": 2,
        "turistfelle": 2,
    }
    for ordlyd, poeng in plusspoeng.items():
        if ordlyd in tekst_l:
            score += poeng
    for ordlyd, poeng in minuspoeng.items():
        if ordlyd in tekst_l:
            score -= poeng
    return max(1, min(10, score))


def synlig_ai_svar(ai_tekst):
    """Fjerner skjult JSON-blokk fra tekst som vises i chatten."""
    if not ai_tekst:
        return ""
    if AGENT_PERLE_MARKER in ai_tekst:
        return ai_tekst.split(AGENT_PERLE_MARKER, 1)[0].rstrip()
    return ai_tekst.rstrip()


def _normaliser_saerhetsscore(verdi, fallback_tekst=""):
    try:
        score = int(round(float(verdi)))
    except (TypeError, ValueError):
        score = _score_saerhetstekst(fallback_tekst)
    return max(1, min(10, score))


def _normaliser_agent_perle(perle, fallback_tekst=""):
    if not isinstance(perle, dict):
        return None
    navn = (perle.get("navn") or perle.get("name") or "").strip()
    by = (perle.get("by") or perle.get("city") or "").strip()
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
    if source_type not in ("hidden_gem", "restaurant"):
        source_type = (
            "restaurant"
            if type_hint in ("gastronomi", "restaurant", "mat")
            or any(
                ordlyd in f"{navn} {beskrivelse}".lower()
                for ordlyd in ("restaurant", "kro", "bistro", "bar", "cafe", "café")
            )
            else "hidden_gem"
        )
    if type_hint in ("gastronomi", "restaurant", "mat"):
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


def detekter_perle_fra_ai_svar(ai_tekst):
    """Finner mulig sted i AI-svaret (JSON først, deretter regex)."""
    if not ai_tekst:
        return None

    kandidat = parse_agent_perle_fra_ai_svar(ai_tekst)
    if kandidat and kandidat.get("saerhetsscore", 0) >= 7:
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
    if score < 7:
        return None

    type_hint = "restaurant" if any(
        ordlyd in synlig.lower() for ordlyd in ("restaurant", "kro", "bistro", "bar", "cafe")
    ) else "kultur"
    funn["saerhetsscore"] = score
    funn["source_type"] = "restaurant" if type_hint == "restaurant" else "hidden_gem"
    funn["type"] = "gastronomi" if type_hint == "restaurant" else "kultur"
    funn["beskrivelse"] = ""
    funn["agent_id"] = f"agent-{_slug_tekst(funn['navn'])}-{_slug_tekst(funn['by'])}-{_slug_tekst(funn['land'])}"
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


def render_chat_agent_perle_handlinger(kandidat, key_suffix):
    """Viser lagre-i-db og legg-i-reiseplan for en oppdaget perle."""
    with st.container(border=True):
        st.info(
            tr("chat_agent_oppdaget").format(
                kandidat["navn"],
                kandidat["by"],
                kandidat["land"],
                kandidat["saerhetsscore"],
            )
        )
        col_plan, col_db = st.columns(2)
        with col_plan:
            if st.button(
                tr("chat_legg_reiseplan"),
                key=f"chat_itinerary_{key_suffix}",
                use_container_width=True,
            ):
                add_itinerary_item(agent_perle_til_reiseplan_sted(kandidat))
                st.toast(tr("favoritt_lagt_til"))
        with col_db:
            if st.button(
                tr("chat_lagre_db"),
                key=f"chat_save_db_{key_suffix}",
                use_container_width=True,
            ):
                lagret = lagre_agent_perle_i_db(kandidat)
                _legg_lagret_sted_i_lokale_lister(lagret)
                st.toast(tr("chat_lagre_toast"))
                st.rerun()


def lagre_agent_perle_i_db(kandidat):
    """Lagrer agent-forslag permanent i places-tabellen."""
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
        "profil_kategori": "Mat & Vin" if kandidat["source_type"] == "restaurant" else "Kultur & Historie",
        "beskrivelse": (kandidat.get("beskrivelse") or "").strip()
        or f"Oppdaget av KI-agent i chatten. Særhetsscore: {kandidat['saerhetsscore']}/10.",
        "tips": "KI-agentforslag: sjekk stedet lokalt.",
        "beste_tid": "",
        "pris": "€€",
        "latitude": lat,
        "longitude": lon,
        "image_url": "",
        "country_code": "",
    }
    normalisert = normalize_place(sted, kandidat["source_type"])
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
    """Oppdaterer lokale stedlister etter DB-lagring."""
    if lagret["source_type"] == "restaurant":
        if not any(p.get("id") == lagret["id"] for p in LOKALE_SPISESTEDER_DB):
            LOKALE_SPISESTEDER_DB.append(lagret)
    else:
        if not any(p.get("id") == lagret["id"] for p in SKJULTE_PERLER_DB):
            SKJULTE_PERLER_DB.append(lagret)


def _perle_nokkel(navn, by, land):
    return f"{(navn or '').strip().lower()}|{(by or '').strip().lower()}|{(land or '').strip().lower()}"


def _hent_eksisterende_perle_nokler():
    keys = set()
    for sted in SKJULTE_PERLER_DB + LOKALE_SPISESTEDER_DB:
        keys.add(_perle_nokkel(sted.get("navn"), sted.get("by"), sted.get("land")))
    return keys


def _parse_json_innhold(tekst):
    rå = (tekst or "").strip()
    if not rå:
        return None
    if rå.startswith("```"):
        rå = re.sub(r"^```(?:json)?\s*", "", rå, flags=re.IGNORECASE)
        rå = re.sub(r"\s*```$", "", rå)
    try:
        return json.loads(rå)
    except json.JSONDecodeError:
        start = rå.find("{")
        slutt = rå.rfind("}")
        if start != -1 and slutt != -1 and slutt > start:
            try:
                return json.loads(rå[start : slutt + 1])
            except json.JSONDecodeError:
                return None
    return None


def sanke_perler_for_omrade(omrade, antall=8, min_score=7, strict_mode=False):
    """Henter og kvalitetssikrer flere perlekandidater automatisk."""
    total_start = time.perf_counter()
    if not API_KEY:
        raise RuntimeError(tr("sank_mangler_api"))

    antall = max(3, min(20, int(antall)))
    min_score = max(1, min(10, int(min_score)))
    if _spraak == "EN":
        strict_hint = (
            " Strict mode is ON: prioritize lesser-known places, avoid obvious landmarks, and write at least 12 words in each description."
            if strict_mode
            else ""
        )
        system_prompt = (
            "You curate off-the-beaten-path places in Europe. "
            "Return strict JSON only with this shape: "
            '{"kandidater":[{"navn":"...","by":"...","land":"...","beskrivelse":"...",'
            '"saerhetsscore":8,"type":"kultur","source_type":"hidden_gem"}]}. '
            "Include exactly the requested number of candidates, avoid mainstream landmarks, "
            "and keep descriptions factual and concise."
            f"{strict_hint}"
        )
        user_prompt = f"Area: {omrade}. Number of candidates: {antall}."
    else:
        strict_hint = (
            " Streng modus er PÅ: prioriter mindre kjente steder, unngå åpenbare landemerker, og skriv minst 12 ord i hver beskrivelse."
            if strict_mode
            else ""
        )
        system_prompt = (
            "Du kuraterer skjulte perler i Europa. "
            "Returner kun gyldig JSON med format: "
            '{"kandidater":[{"navn":"...","by":"...","land":"...","beskrivelse":"...",'
            '"saerhetsscore":8,"type":"kultur","source_type":"hidden_gem"}]}. '
            "Gi nøyaktig antall kandidater, unngå mainstream landemerker, "
            "og hold beskrivelser korte og faktabaserte."
            f"{strict_hint}"
        )
        user_prompt = f"Område: {omrade}. Antall kandidater: {antall}."

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 1400,
    }
    response = requests.post(URL, headers=HEADERS, json=payload, timeout=30)
    response.raise_for_status()
    content = (
        response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
    )
    parsed = _parse_json_innhold(content)
    if not isinstance(parsed, dict):
        raise RuntimeError(tr("sank_parse_feil"))
    rå_liste = parsed.get("kandidater")
    if not isinstance(rå_liste, list):
        raise RuntimeError(tr("sank_parse_feil"))

    eksisterende = _hent_eksisterende_perle_nokler()
    nye_nokler = set()
    godkjente = []
    forkastet_duplikat = 0
    forkastet_score = 0
    forkastet_geo = 0
    kandidater_for_geo = []

    for rå in rå_liste:
        kandidat = _normaliser_agent_perle(rå, json.dumps(rå, ensure_ascii=False))
        if not kandidat:
            continue
        if strict_mode:
            beskrivelse = (kandidat.get("beskrivelse") or "").strip()
            if len(beskrivelse.split()) < 12:
                forkastet_score += 1
                continue

        if kandidat.get("saerhetsscore", 0) < min_score:
            forkastet_score += 1
            continue

        key = _perle_nokkel(kandidat["navn"], kandidat["by"], kandidat["land"])
        if key in eksisterende or key in nye_nokler:
            forkastet_duplikat += 1
            continue
        nye_nokler.add(key)
        kandidater_for_geo.append({"kandidat": kandidat, "key": key})

    async def _geokod_kandidat_async(kandidat):
        lat, lon = await hent_koordinater_for_sok_async(
            f"{kandidat['navn']}, {kandidat['by']}, {kandidat['land']}"
        )
        if lat is None or lon is None:
            lat, lon = await hent_koordinater_for_sok_async(f"{kandidat['by']}, {kandidat['land']}")
        return lat, lon

    async def _geokod_alle_kandidater_async(items, max_parallel=4):
        sem = asyncio.Semaphore(max_parallel)

        async def _worker(item):
            async with sem:
                lat, lon = await _geokod_kandidat_async(item["kandidat"])
                return item, lat, lon

        return await asyncio.gather(*(_worker(item) for item in items))

    geo_start = time.perf_counter()
    geokodede = _run_async(_geokod_alle_kandidater_async(kandidater_for_geo))
    geo_elapsed_s = time.perf_counter() - geo_start
    for item, lat, lon in geokodede:
        if lat is None or lon is None:
            forkastet_geo += 1
            continue
        kandidat = item["kandidat"]
        kandidat["latitude"] = lat
        kandidat["longitude"] = lon
        godkjente.append(kandidat)

    rapport = {
        "foreslaatt": len(rå_liste),
        "godkjent": len(godkjente),
        "forkastet_duplikat": forkastet_duplikat,
        "forkastet_score": forkastet_score,
        "forkastet_geo": forkastet_geo,
        "tid_geo_s": round(geo_elapsed_s, 3),
        "tid_total_s": round(time.perf_counter() - total_start, 3),
    }
    return godkjente, rapport


def generer_reiseekspert_stream(sporsmal, kontekst=""):
    """Generator-funksjon for å streame AI-svar fra OpenRouter m/ RAG-databasekobling"""
    if not API_KEY:
        yield "⚠️ **Systemmelding:** `OPENROUTER_API_KEY` mangler i miljøvariablene. Kan ikke kontakte reiseeksperten."
        return

    profil = _normaliser_profil(st.session_state.get("profil"))
    reise_folge = profil["reise_folge"]
    budsjett = profil["budsjett"]
    hovedinteresse = profil["hovedinteresse"]

    intern_kontekst = _bygg_rag_kontekst(sporsmal, hovedinteresse)

    if _spraak == "EN":
        json_instruks = (
            "Always end your answer with this exact line on its own: ||PERLE_JSON|| "
            "Then one line of valid JSON (no markdown) like: "
            '{"perle":{"navn":"...","by":"...","land":"...","beskrivelse":"...","saerhetsscore":8,'
            '"type":"kultur","source_type":"hidden_gem"}}. '
            "Use saerhetsscore below 7 for mainstream places. Pick one main recommendation."
        )
        system_melding = (
            "You are the AI agent for Hidden Europe: hidden gems and eccentric destinations. "
            f"The user travels as {reise_folge} on a {budsjett} budget, focused on {hovedinteresse}. "
            "Suggest off-the-beaten-path places with local character. "
            "Avoid mainstream tourism and iconic defaults like the Eiffel Tower. "
            "Reply briefly and enthusiastically (max 5 sentences). "
            "Mention places as: Name (City, Country). "
            f"{json_instruks}"
            f"{intern_kontekst}"
        )
    else:
        json_instruks = (
            "Avslutt ALLTID svaret med nøyaktig denne linjen alene: ||PERLE_JSON|| "
            "Deretter én linje gyldig JSON (uten markdown), f.eks.: "
            '{"perle":{"navn":"...","by":"...","land":"...","beskrivelse":"...","saerhetsscore":8,'
            '"type":"kultur","source_type":"hidden_gem"}}. '
            "Bruk saerhetsscore under 7 for mainstream-steder. Velg én hovedanbefaling."
        )
        system_melding = (
            "Du er KI-agenten for Hemmelige Europa: skjulte perler og eksentriske reisemål. "
            f"Brukeren reiser som {reise_folge} med et {budsjett}-budsjett, og har hovedfokus på {hovedinteresse}. "
            "Foreslå aktivt off-the-beaten-path-steder med lokal karakter, særegen historie eller quirky opplevelser. "
            "Unngå mainstream turisme, typiske turistfeller, store resorter og ikoniske standardvalg som Eiffeltårnet. "
            "Svar kort, engasjerende, spesifikt og entusiastisk (maks 5 setninger). "
            "Nevn gjerne konkrete steder med formatet: Sted (By, Land). "
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
        response = requests.post(
            URL, headers=HEADERS, json=payload, stream=True, timeout=10
        )

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


def render_favoritt_knapp(place, key_prefix):
    if st.button(T["favoritt_knapp"], key=f"{key_prefix}_add_{place['id']}", use_container_width=True):
        add_itinerary_item(place)
        st.success(T["favoritt_lagt_til"])


KART_FARGE_MAT = "#1B5E20"
KART_FARGE_KULTUR = "#6A1B9A"
KART_FARGE_NATUR = "#00838F"
KART_FARGE_GOLF = "#004D40"

KART_KATEGORI_FARGER = {
    "restaurant": KART_FARGE_MAT,
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

    for sted in SKJULTE_PERLER_DB + LOKALE_SPISESTEDER_DB:
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


SKJULTE_PERLER_DB = get_places("hidden_gem")
LOKALE_SPISESTEDER_DB = get_places("restaurant")


def filtrer_data(data):
    """Filtrerer bort steder uten gyldige koordinater."""
    return [
        d
        for d in data
        if d.get("latitude") is not None and d.get("longitude") is not None
    ]


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


def vis_sted_type(sted):
    """Viser pene type-etiketter i stedet for rå databaseverdier."""
    raw = (sted.get("profil_kategori") or sted.get("type") or "").strip().lower()
    mapping = {
        "kultur": "type_kultur",
        "natur": "type_natur",
        "gastronomi": "type_gastronomi",
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
    for sted in SKJULTE_PERLER_DB + LOKALE_SPISESTEDER_DB:
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
    er_spisested = sted.get("source_type") == "restaurant"
    prefiks = "Spisested" if er_spisested else "Skjult perle"
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
    for sted in SKJULTE_PERLER_DB + LOKALE_SPISESTEDER_DB:
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
    alle = SKJULTE_PERLER_DB + LOKALE_SPISESTEDER_DB
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

fane0, fane1, fane2, fane3, fane4 = st.tabs(
    [
        T["fane_hjem"],
        T["fane_mat"],
        T["fane_chat"],
        T["reiseplan_fane"],
        T["fane_transport"],
    ]
)


# --- FANE 0: HJEM & RADAR (felles startside) ---
with fane0:
    st.header(T["hjem_header"])
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(T["hjem_metric_perler"], len(SKJULTE_PERLER_DB))
    with col2:
        st.metric(T["hjem_metric_spisesteder"], len(LOKALE_SPISESTEDER_DB))
    with col3:
        st.metric(
            T["hjem_metric_land"],
            len(set(s["country_code"] or s["land"] for s in SKJULTE_PERLER_DB)),
        )

    st.divider()
    st.subheader(T["radar_tittel"])
    st.caption(T["radar_sub"])

    alle_steder_i_db = SKJULTE_PERLER_DB + LOKALE_SPISESTEDER_DB
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
            sok_perle = st.text_input(T["perler_sok"], "", key="perler_sok_input").lower()
        with col_s2:
            alle_typer = sorted({p["type"] for p in treff_steder}) if treff_steder else sorted(
                {p["type"] for p in SKJULTE_PERLER_DB}
            )
            type_perle = st.selectbox(
                T["perler_sorter_type"],
                [T["perler_alle"]] + alle_typer,
                key="perler_type_filter",
            )

        filtrerte_perler = []
        for treff in radar_treff:
            perle = treff["data"]
            if type_perle != T["perler_alle"] and perle["type"] != type_perle:
                continue
            if sok_perle and (
                sok_perle not in perle["navn"].lower()
                and sok_perle not in perle["by"].lower()
            ):
                continue
            filtrerte_perler.append(perle)

        filtrerte_perler = sorter_steder_etter_profil(filtrerte_perler)

        if filtrerte_perler:
            for i in range(0, len(filtrerte_perler), 3):
                cols = st.columns(3)
                for j in range(3):
                    if i + j < len(filtrerte_perler):
                        p = filtrerte_perler[i + j]
                        er_mat = p.get("source_type") == "restaurant"
                        emoji = "🍽️" if er_mat else "🏛️"
                        perle_tittel = sted_tittel_med_profil(p, emoji)
                        with cols[j]:
                            vis_sted_foto(p, key_suffix=f"perle_{p['id']}")
                            st.markdown(
                                f"""
                            <div class="travel-card">
                                <h3>{perle_tittel}</h3>
                                <p><b>📍 {p["by"]}, {p["land"]}</b> • <i>{vis_sted_type(p)}</i></p>
                                <p>{p["beskrivelse"]}</p>
                            </div>
                            """,
                                unsafe_allow_html=True,
                            )
                            if p.get("tips"):
                                st.info(f"💡 {p['tips']}")
                            if p.get("beste_tid"):
                                st.caption(tr("perler_beste_tid").format(p["beste_tid"]))
                            render_favoritt_knapp(
                                p,
                                "mat" if er_mat else "perle",
                            )
                            st.write("<br>", unsafe_allow_html=True)
        else:
            st.info(T["perler_ingen_treff"])


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
        sok_mat = st.text_input(T["mat_sok"], "", key="mat_sok_input").lower()
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
        if sok_mat and (
            sok_mat not in sted["navn"].lower() and sok_mat not in sted["by"].lower()
        ):
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
                            f"""
                        <div class="travel-card">
                            <h3>{mat_tittel}</h3>
                            <p><b>📍 {s["by"]}, {s["land"]}</b> • <i>{vis_sted_type(s)}</i></p>
                            <p>{s["beskrivelse"]}</p>
                        </div>
                        """,
                            unsafe_allow_html=True,
                        )
                        st.success(f"{T['mat_pris']} {s['pris']}")
                        render_favoritt_knapp(s, "mat")
                        st.write("<br>", unsafe_allow_html=True)
    else:
        st.info(T["mat_ingen_treff"])


# --- FANE 2: REISE-CHAT ---
with fane2:
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
            sank_min_score = st.slider(
                tr("sank_min_score"),
                min_value=5,
                max_value=10,
                value=7,
                key="sank_min_score_input",
            )
            sank_streng = st.checkbox(
                tr("sank_strict_mode"),
                value=False,
                key="sank_strict_mode_input",
            )
            start_sank = st.form_submit_button(
                tr("sank_knapp"), type="primary", use_container_width=True
            )

        if start_sank:
            omrade = (sank_omrade or "").strip()
            if not omrade:
                st.error(tr("sank_feil_omrade"))
            else:
                try:
                    with st.spinner(tr("sank_spinner").format(omrade)):
                        kandidater, rapport = sanke_perler_for_omrade(
                            omrade,
                            sank_antall,
                            min_score=sank_min_score,
                            strict_mode=sank_streng,
                        )
                    st.session_state["sank_kandidater"] = kandidater
                    st.session_state["sank_rapport"] = rapport
                    st.session_state["sank_omrade"] = omrade
                except Exception as e:
                    st.error(tr("sank_feil_generell").format(str(e)))

        sank_kandidater = st.session_state.get("sank_kandidater", [])
        sank_rapport = st.session_state.get("sank_rapport")
        if sank_rapport:
            st.caption(
                tr("sank_rapport").format(
                    sank_rapport.get("foreslaatt", 0),
                    sank_rapport.get("godkjent", 0),
                    sank_rapport.get("forkastet_duplikat", 0),
                    sank_rapport.get("forkastet_score", 0),
                    sank_rapport.get("forkastet_geo", 0),
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
                    lagret = lagre_agent_perle_i_db(kandidat)
                    _legg_lagret_sted_i_lokale_lister(lagret)
                    lagret_antall += 1
                st.session_state["sank_kandidater"] = []
                st.success(tr("sank_lagret_alle").format(lagret_antall))
                st.rerun()

            for idx, kandidat in enumerate(visningsliste):
                with st.container(border=True):
                    st.markdown(
                        f"**{kandidat['navn']}**  \n"
                        + tr("sank_kandidat_meta").format(
                            kandidat.get("type", "kultur").capitalize(),
                            kandidat.get("saerhetsscore", 0),
                            kandidat["by"],
                            kandidat["land"],
                        )
                    )
                    if kandidat.get("beskrivelse"):
                        st.write(kandidat["beskrivelse"])
                    col_plan, col_db = st.columns(2)
                    with col_plan:
                        if st.button(
                            tr("chat_legg_reiseplan"),
                            key=f"sank_plan_{idx}_{kandidat['agent_id']}",
                            use_container_width=True,
                        ):
                            add_itinerary_item(agent_perle_til_reiseplan_sted(kandidat))
                            st.toast(tr("favoritt_lagt_til"))
                    with col_db:
                        if st.button(
                            tr("chat_lagre_db"),
                            key=f"sank_save_{idx}_{kandidat['agent_id']}",
                            use_container_width=True,
                        ):
                            lagret = lagre_agent_perle_i_db(kandidat)
                            _legg_lagret_sted_i_lokale_lister(lagret)
                            rest = st.session_state.get("sank_kandidater", [])
                            st.session_state["sank_kandidater"] = [
                                k for k in rest if k.get("agent_id") != kandidat.get("agent_id")
                            ]
                            st.toast(tr("chat_lagre_toast"))
                            st.rerun()
        elif sank_rapport:
            st.info(tr("sank_ingen"))

    if not st.session_state.reise_chat:
        st.info(tr("chat_ingen_meldinger"))

    if st.session_state.reise_chat:
        html_innhold = """
        <html>
        <head>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 30px; color: #333; line-height: 1.6; }
                .header { text-align: center; border-bottom: 2px solid #1E3A8A; padding-bottom: 10px; margin-bottom: 30px; }
                .message-box { margin-bottom: 20px; padding: 15px; border-radius: 6px; }
                .user { background-color: #F3F4F6; border-left: 4px solid #9CA3AF; }
                .assistant { background-color: #EFF6FF; border-left: 4px solid #3B82F6; }
                .sender-name { font-weight: bold; font-size: 0.9em; color: #1E3A8A; margin-bottom: 5px; }
                .map-hint { font-size: 0.85em; color: #6B7280; font-style: italic; margin-top: 10px; }
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
            if melding["role"] == "assistant" and kandidat and kandidat.get("saerhetsscore", 0) >= 7:
                render_chat_agent_perle_handlinger(
                    kandidat, f"hist_{kandidat['agent_id']}_{loop_index}"
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

            if sporsmal.lower().startswith("wiki ") or sporsmal.lower().startswith("søk "):
                sted_for_kart = (
                    sporsmal[5:].strip()
                    if sporsmal.lower().startswith("wiki ")
                    else sporsmal[4:].strip()
                )
                wiki_geo_start = time.perf_counter()
                with st.spinner(T["chat_wiki_spinner"].format(sted_for_kart)):
                    async def _wiki_og_geo():
                        return await asyncio.gather(
                            sok_wikivoyage_async(sted_for_kart),
                            hent_koordinater_for_sok_async(sted_for_kart),
                        )

                    wiki_info, (lat, lon) = _run_async(_wiki_og_geo())
                wiki_geo_elapsed = time.perf_counter() - wiki_geo_start

                if wiki_info and "Ingen" not in wiki_info:
                    st.markdown(f"{T['chat_wiki_hentet']}\n> *{wiki_info}*")
                    wiki_kontekst = f"Kontekstinformasjon fra Wikivoyage: {wiki_info}"
                if st.session_state.get("vis_perf_debug"):
                    st.caption(tr("perf_wiki_geo").format(f"{wiki_geo_elapsed:.3f}"))

            fullt_svar = st.write_stream(generer_reiseekspert_stream(sporsmal, wiki_kontekst))
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
            if agent_perle and agent_perle.get("saerhetsscore", 0) >= 7:
                render_chat_agent_perle_handlinger(
                    agent_perle, f"live_{agent_perle['agent_id']}"
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


with fane3:
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
                st.markdown(f"### {item['navn']}")
                st.caption(f"{item['by']}, {item['land']} • {item['type']}")
                st.write(item["beskrivelse"])
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

# --- FANE 4: TRANSPORT ---
with fane4:
    stedvalg = bygg_stedvalg_fra_database(SKJULTE_PERLER_DB + LOKALE_SPISESTEDER_DB)
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
