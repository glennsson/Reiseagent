import streamlit as st
import requests
import os
import random
import json
from datetime import datetime, time
import pandas as pd
import folium
from streamlit_folium import st_folium
from dotenv import load_dotenv
from streamlit_js_eval import get_geolocation

import math
from data_store import (
    add_itinerary_item,
    get_affiliate_stats,
    get_itinerary_items,
    get_places,
    log_affiliate_click,
    remove_itinerary_item,
)
from translations import TEKSTER
from place_images import hent_sted_bilde_url
from affiliate_links import (
    bygg_booking_url as _bygg_booking_url,
    bygg_leiebil_url as _bygg_leiebil_url,
    bygg_matlevering_url as _bygg_matlevering_url,
)
from transport_planner import (
    bygg_eksterne_planleggere,
    bygg_stedvalg_fra_database,
    hent_navitia_dekning,
    planlegg_kollektivreise,
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

T = TEKSTER.get(spraak, TEKSTER["NO"])

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


with st.sidebar.expander("👤 Din Reiseprofil", expanded=False):
    _profil = _normaliser_profil(st.session_state.profil)
    ny_reise_folge = st.selectbox(
        "Reisefølge",
        options=PROFIL_REISE_FOLGE,
        index=PROFIL_REISE_FOLGE.index(_profil["reise_folge"]),
        key="profil_reise_folge",
    )
    ny_budsjett = st.selectbox(
        "Budsjettnivå",
        options=PROFIL_BUDSJETT,
        index=PROFIL_BUDSJETT.index(_profil["budsjett"]),
        key="profil_budsjett",
    )
    ny_hovedinteresse = st.selectbox(
        "Hovedinteresse",
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
    st.caption("Profilen lagres automatisk.")

st.sidebar.caption(T["affiliate_disclosure"])
affiliate_stats = get_affiliate_stats()
with st.sidebar.expander(T["affiliate_stats_header"], expanded=False):
    st.metric(T["affiliate_total_clicks"], affiliate_stats["total_clicks"])
    if affiliate_stats["latest_click"]:
        st.caption(f"{T['affiliate_latest_click']}: {affiliate_stats['latest_click']}")
    if affiliate_stats["top_places"]:
        st.write(T["affiliate_top_places"])
        for row in affiliate_stats["top_places"]:
            st.caption(
                f"{row['place_name']} ({row['city']}, {row['country']}): {row['clicks']}"
            )
    if affiliate_stats.get("top_partners"):
        st.write(T["affiliate_top_partners"])
        partner_navn = {
            "booking": "🏨 Hotell",
            "car": "🚗 Leiebil",
            "food": "🍔 Matlevering",
        }
        for row in affiliate_stats["top_partners"]:
            st.caption(f"{partner_navn.get(row['partner'], row['partner'])}: {row['clicks']}")
    if affiliate_stats["top_sources"]:
        st.write(T["affiliate_top_sources"])
        for row in affiliate_stats["top_sources"]:
            st.caption(f"{row['source_view']}: {row['clicks']}")

with st.sidebar.expander(T["affiliate_tjenester"], expanded=False):
    st.caption(T["affiliate_tjenester_hint"])
    _aid = st.secrets.get("BOOKING_AID", "888888")
    _glovo = st.secrets.get("GLOVO_AFFILIATE_URL", "")
    _wolt = st.secrets.get("WOLT_AFFILIATE_URL", "")
    _uber = st.secrets.get("UBEREATS_AFFILIATE_URL", "")

    sb1, sb2 = st.columns(2)
    with sb1:
        st.link_button(
            f"{T['affiliate_leiebil_btn']} · {T['affiliate_sidebar_spania']}",
            _bygg_leiebil_url("Alicante", "Spania", _aid, "ES"),
            use_container_width=True,
        )
        st.link_button(
            f"{T['affiliate_mat_btn']} · {T['affiliate_sidebar_spania']}",
            _bygg_matlevering_url("Torrevieja", "Spania", "ES", spraak, _glovo, _wolt, _uber),
            use_container_width=True,
        )
    with sb2:
        st.link_button(
            f"{T['affiliate_leiebil_btn']} · {T['affiliate_sidebar_norge']}",
            _bygg_leiebil_url("Oslo", "Norge", _aid, "NO"),
            use_container_width=True,
        )
        st.link_button(
            f"{T['affiliate_mat_btn']} · {T['affiliate_sidebar_norge']}",
            _bygg_matlevering_url("Oslo", "Norge", "NO", spraak, _glovo, _wolt, _uber),
            use_container_width=True,
        )



# --- PROFF STYLING (CSS-INJEKSJON) ---
st.markdown(
    """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

        :root {
            --gemini-bg: #F8FAFD;
            --gemini-surface: #FFFFFF;
            --gemini-surface-2: #F0F4F9;
            --gemini-text: #1F1F1F;
            --gemini-muted: #5F6368;
            --gemini-border: #DADCE0;
            --gemini-blue: #1A73E8;
            --gemini-purple: #A142F4;
            --gemini-radius: 18px;
            --gemini-shadow: 0 12px 34px rgba(60, 64, 67, 0.12);
        }

        html, body, [class*="css"], .stMarkdown, [data-testid="stAppViewContainer"] {
            font-family: 'Inter', sans-serif !important;
        }

        .stApp {
            background:
                radial-gradient(circle at top left, rgba(26, 115, 232, 0.16), transparent 28rem),
                radial-gradient(circle at top right, rgba(161, 66, 244, 0.14), transparent 30rem),
                var(--gemini-bg);
            color: var(--gemini-text);
        }

        section.main > div {
            max-width: 1180px;
            padding-top: 1.25rem;
        }

        h1 {
            font-weight: 700 !important;
            color: var(--gemini-text) !important;
            letter-spacing: -0.04em !important;
            line-height: 1.04 !important;
        }

        h2, h3, .stMarkdown, p, span, label {
            color: var(--gemini-text) !important;
        }

        [data-testid="stSidebar"] {
            background: color-mix(in srgb, var(--gemini-surface) 88%, transparent) !important;
            border-right: 1px solid var(--gemini-border);
        }

        [data-testid="stMetric"], .travel-card, [data-testid="stExpander"] {
            background: color-mix(in srgb, var(--gemini-surface) 94%, transparent) !important;
            border: 1px solid var(--gemini-border) !important;
            border-radius: var(--gemini-radius) !important;
            box-shadow: var(--gemini-shadow);
        }

        [data-testid="stMetric"] {
            padding: 1rem;
        }

        .streamlit-expanderHeader {
            background-color: var(--gemini-surface-2) !important;
            color: var(--gemini-text) !important;
            border-radius: var(--gemini-radius) !important;
        }

        .travel-card {
            min-height: 205px;
            padding: 1.05rem;
            margin-bottom: 0.75rem;
        }

        .travel-card h3 {
            margin-top: 0;
            font-size: 1.05rem;
        }

        [data-testid="stImage"] img {
            border-radius: 14px;
            object-fit: cover;
            max-height: 220px;
            width: 100%;
            box-shadow: 0 6px 18px rgba(60, 64, 67, 0.15);
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 0.35rem;
            overflow-x: auto;
        }

        .stTabs [data-baseweb="tab"] {
            background: var(--gemini-surface-2);
            border-radius: 999px;
            padding: 0.45rem 1rem;
        }

        .stTabs [aria-selected="true"] {
            background: linear-gradient(135deg, rgba(26,115,232,0.16), rgba(161,66,244,0.16));
            color: var(--gemini-blue) !important;
        }

        @media (prefers-color-scheme: dark) {
            :root {
                --gemini-bg: #0B0F19;
                --gemini-surface: #131722;
                --gemini-surface-2: #1E2430;
                --gemini-text: #E8EAED;
                --gemini-muted: #BDC1C6;
                --gemini-border: #2D3544;
                --gemini-shadow: 0 16px 36px rgba(0, 0, 0, 0.32);
            }

            h1, h2, h3, .stMarkdown, p, span, label {
                color: var(--gemini-text) !important;
            }
        }

        .stLinkButton > a {
            background: linear-gradient(135deg, var(--gemini-blue), var(--gemini-purple)) !important;
            color: #FFFFFF !important;
            border-radius: 999px !important;
            border: none !important;
            padding: 0.65rem 1.25rem !important;
            font-weight: 650 !important;
            text-decoration: none !important;
            box-shadow: 0 8px 24px rgba(26, 115, 232, 0.25);
        }

        .stLinkButton > a:hover {
            filter: brightness(1.08);
            transform: translateY(-1px);
        }

        button[kind="primary"] {
            border-radius: 999px !important;
            background: linear-gradient(135deg, var(--gemini-blue), var(--gemini-purple)) !important;
        }

        @media (max-width: 768px) {
            section.main > div {
                padding-left: 0.85rem;
                padding-right: 0.85rem;
            }

            h1 {
                font-size: 2rem !important;
            }

            .travel-card {
                min-height: auto;
            }
        }

        header {background: rgba(0,0,0,0) !important;}
        footer {visibility: hidden;}
    </style>
""",
    unsafe_allow_html=True,
)

API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "google/gemini-2.5-flash"
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

    system_melding = (
        f"Du er en europeisk reiseekspert. Brukeren reiser som {reise_folge} med et {budsjett}-budsjett, "
        f"og har hovedfokus på {hovedinteresse}. Svar kort, engasjerende, spesifikt og entusiastisk. "
        "Maks 5 setninger. Tilpass alltid dine anbefalinger og Booking.com-overnattingsforslag til denne profilen."
        f"{intern_kontekst}"
    )

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_melding},
            {"role": "user", "content": f"{kontekst}\n\n{sporsmal}"},
        ],
        "max_tokens": 500,
        "stream": True,
    }

    try:
        response = requests.post(
            URL, headers=HEADERS, json=payload, stream=True, timeout=10
        )

        for line in response.iter_lines():
            if line:
                cleaned_line = line.decode("utf-8").replace("data: ", "")
                if cleaned_line.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(cleaned_line)
                    content = chunk["choices"][0]["delta"].get("content", "")
                    if content:
                        yield content
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
    """Viser stedsfoto over treffkortet hvis bilde finnes."""
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
        st.caption(f"📷 {sted.get('navn', '')} · Wikimedia Commons")


def _affiliate_secrets():
    return {
        "aid": st.secrets.get("BOOKING_AID", "888888"),
        "glovo": st.secrets.get("GLOVO_AFFILIATE_URL", ""),
        "wolt": st.secrets.get("WOLT_AFFILIATE_URL", ""),
        "uber": st.secrets.get("UBEREATS_AFFILIATE_URL", ""),
    }


def bygg_booking_url(by, land):
    return _bygg_booking_url(by, land, _affiliate_secrets()["aid"])


def bygg_leiebil_url(by, land, country_code=""):
    return _bygg_leiebil_url(by, land, _affiliate_secrets()["aid"], country_code)


def bygg_matlevering_url(by, land, country_code=""):
    s = _affiliate_secrets()
    return _bygg_matlevering_url(
        by, land, country_code, spraak, s["glovo"], s["wolt"], s["uber"]
    )


def _affiliate_url_key(key_prefix, place_id, partner):
    return f"affiliate_url_{partner}_{key_prefix}_{place_id}"


def _affiliate_partners_for_place(place, fremhev_mat=False):
    by = place.get("by", "")
    land = place.get("land", "")
    cc = place.get("country_code", "")
    partners = [
        ("booking", T["affiliate_hotell_btn"], T["affiliate_hotell_open"], lambda: bygg_booking_url(by, land)),
        ("car", T["affiliate_leiebil_btn"], T["affiliate_leiebil_open"], lambda: bygg_leiebil_url(by, land, cc)),
        ("food", T["affiliate_mat_btn"], T["affiliate_mat_open"], lambda: bygg_matlevering_url(by, land, cc)),
    ]
    if fremhev_mat:
        partners = [partners[2], partners[0], partners[1]]
    return partners


def render_place_actions(place, source_view, key_prefix, fremhev_mat=False):
    st.caption(T["affiliate_praktisk"])
    col_fav, col_h, col_b, col_m = st.columns(4)
    with col_fav:
        if st.button(T["favoritt_knapp"], key=f"{key_prefix}_add_{place['id']}", use_container_width=True):
            add_itinerary_item(place)
            st.success(T["favoritt_lagt_til"])

    for col, (partner, btn_label, open_label, url_builder) in zip(
        (col_h, col_b, col_m),
        _affiliate_partners_for_place(place, fremhev_mat=fremhev_mat),
    ):
        url_key = _affiliate_url_key(key_prefix, place["id"], partner)
        with col:
            if st.button(btn_label, key=f"{key_prefix}_{partner}_{place['id']}", use_container_width=True):
                url = url_builder()
                log_affiliate_click(place, f"{source_view}:{partner}", spraak, url)
                st.session_state[url_key] = url
                st.success(T["affiliate_logget"])

    aktive_lenker = []
    for partner, _, open_label, _ in _affiliate_partners_for_place(place, fremhev_mat=fremhev_mat):
        url_key = _affiliate_url_key(key_prefix, place["id"], partner)
        if st.session_state.get(url_key):
            aktive_lenker.append((open_label.format(place["by"]), st.session_state[url_key]))

    if aktive_lenker:
        st.caption(T["affiliate_disclosure"])
        for label, url in aktive_lenker:
            st.link_button(label, url, use_container_width=True)


def lag_radar_kart(treff_liste, sentrum=None, sentrum_navn=""):
    if sentrum:
        m = folium.Map(location=[sentrum[0], sentrum[1]], zoom_start=7, tiles="OpenStreetMap")
        folium.Marker(
            location=[sentrum[0], sentrum[1]],
            tooltip=sentrum_navn,
            icon=folium.Icon(color="blue", icon="search"),
        ).add_to(m)
    else:
        m = folium.Map(location=[54.0, 14.0], zoom_start=4, tiles="OpenStreetMap")

    for treff in treff_liste:
        place = treff["data"]
        color = "green" if place.get("source_type") == "restaurant" else "red"
        icon = "cutlery" if place.get("source_type") == "restaurant" else "star"
        popup = f"<b>{place['navn']}</b><br>{place['by']}, {place['land']}<br>{treff['avstand']} km"
        folium.Marker(
            location=[place["latitude"], place["longitude"]],
            tooltip=f"{place['navn']} ({place['by']})",
            popup=popup,
            icon=folium.Icon(color=color, icon=icon),
        ).add_to(m)
    return m


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
        "Prioriter korte transportetapper, skjulte perler, lokale spisesteder og naturlige Booking.com-overnattingsforslag. "
        f"Svar på {'norsk' if spraak == 'NO' else 'English'}.\n\n{steder}"
    )
    yield from generer_reiseekspert_stream(prompt)


SKJULTE_PERLER_DB = get_places("hidden_gem")
LOKALE_SPISESTEDER_DB = get_places("restaurant")


# ========================================
# APPLIKASJONSSTRUKTUR (UI)
# ========================================
st.title(T["app_tittel"])
st.caption(T["app_caption"])

fane = st.tabs(
    [
        T["fane_radar"],
        T["fane_hjem"],
        T["fane_perler"],
        T["fane_mat"],
        T["reiseplan_fane"],
        T["fane_transport"],
        T["fane_chat"],
    ]
)


def filtrer_data(data):
    """Filtrerer bort steder uten koordinater (latitude/longitude)."""
    return [d for d in data if "latitude" in d and "longitude" in d]


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

    for navn in søkekandidater:
        lat, lon = hent_koordinater_for_sok(navn)
        if lat is not None and lon is not None:
            return lat, lon, navn
    return None, None, None


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


# ========================================================
# FANE 0: RADAREN (MED GPS- OG LANDSØK) — vises først
# ========================================================
with fane[0]:
    st.subheader(T["radar_tittel"])
    st.write(T["radar_sub"])

    alle_steder_i_db = SKJULTE_PERLER_DB + LOKALE_SPISESTEDER_DB
    filtrert_for_radar = filtrer_data(alle_steder_i_db)

    if filtrert_for_radar:
        # 1. Velg søkemetode (Plassering, Posisjon eller Land)
        soke_metode = st.radio(
            T["radar_metode"],
            options=[T["radar_sted_sok"], T["radar_gps"], T["radar_land_sok"]],
            horizontal=True,
        )

        maks_avstand = st.slider(
            T["radar_radius"], min_value=10, max_value=500, value=150, step=10
        )
        perler_i_naerheten = []
        soke_sentrum_navn = ""
        soke_sentrum = None

        # 2. LOGIKK FOR Å HENTE SØKESENTRUM
        if soke_metode == T["radar_sted_sok"]:
            sted_sok = st.text_input(
                T["radar_sted_input"],
                placeholder=T["radar_sted_placeholder"],
                key="radar_sted_sok_input",
            )
            if sted_sok:
                min_lat, min_lon = hent_koordinater_for_sok(sted_sok)
                soke_sentrum_navn = sted_sok
                if min_lat and min_lon:
                    soke_sentrum = (min_lat, min_lon)
                    for perle in filtrert_for_radar:
                        avstand = regn_ut_avstand_km(
                            min_lat, min_lon, perle["latitude"], perle["longitude"]
                        )
                        if avstand <= maks_avstand:
                            perler_i_naerheten.append(
                                {"data": perle, "avstand": round(avstand, 1)}
                            )
                else:
                    st.warning(T["radar_sted_warning"])
        elif soke_metode == T["radar_gps"]:
            with st.spinner(T["radar_spinner"]):
                geo = get_geolocation()

            if geo and "coords" in geo:
                min_lat = geo["coords"]["latitude"]
                min_lon = geo["coords"]["longitude"]
                soke_sentrum_navn = T["radar_sentrum_gps"]
                soke_sentrum = (min_lat, min_lon)

                # Beregn avstand fra GPS-punktet til alle perler i regionen
                for perle in filtrert_for_radar:
                    avstand = regn_ut_avstand_km(
                        min_lat, min_lon, perle["latitude"], perle["longitude"]
                    )
                    if avstand <= maks_avstand:
                        perler_i_naerheten.append(
                            {"data": perle, "avstand": round(avstand, 1)}
                        )
            else:
                st.warning(T["radar_warning"])
        else:
            # Landsøk-logikk
            unike_land = sorted(list(set([p["land"] for p in filtrert_for_radar])))
            valgt_land = st.selectbox(
                T["radar_velg_land"], unike_land, key="radar_land"
            )
            soke_sentrum_navn = valgt_land

            for perle in filtrert_for_radar:
                if perle["land"] == valgt_land:
                    perler_i_naerheten.append(
                        {
                            "data": perle,
                            "avstand": 0,  # Ingen avstand ved rent landsøk
                        }
                    )

        # 3. KNAPP FOR Å KJØRE SKANNINGEN
        if st.button(T["radar_knapp_skann"], use_container_width=True):
            perler_i_naerheten = sorted(
                perler_i_naerheten,
                key=lambda x: (_profil_sorteringsnøkkel(x["data"]), x["avstand"]),
            )

            if perler_i_naerheten:
                st.markdown(
                    f"### {T['radar_fant']} {len(perler_i_naerheten)} {T['radar_destinasjoner']} {soke_sentrum_navn}"
                )
                st_folium(
                    lag_radar_kart(perler_i_naerheten, soke_sentrum, soke_sentrum_navn),
                    width=1100,
                    height=520,
                    returned_objects=[],
                )

                for treff in perler_i_naerheten:
                    p = treff["data"]
                    km = treff["avstand"]
                    visningsnavn = sted_tittel_med_profil(p, "")

                    expander_tittel = (
                        f"{visningsnavn} — {km} km {T['radar_unna']} ({p['by']})"
                        if km > 0
                        else f"{visningsnavn} ({p['by']})"
                    )

                    with st.expander(expander_tittel):
                        vis_sted_foto(p, key_suffix=f"radar_{p['id']}")
                        st.markdown(
                            f"**{T['radar_kategori']}:** {p['type'].capitalize()}  |  **{T['radar_pris']}:** {p.get('pris', '€')}"
                        )
                        st.write(p["beskrivelse"])

                        if "tips" in p:
                            st.markdown(f"*📌 **{T['radar_tips']}:** {p['tips']}*")

                        render_place_actions(p, "radar", "radar")
            else:
                st.info(T["radar_ingen_treff"])
    else:
        st.write(T["radar_ingen_data"])


# --- FANE 1: HJEM ---
with fane[1]:
    st.header(T["hjem_header"])
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(T["hjem_metric_perler"], len(SKJULTE_PERLER_DB))
    with col2:
        st.metric(T["hjem_metric_spisesteder"], len(LOKALE_SPISESTEDER_DB))
    with col3:
        st.metric(T["hjem_metric_land"], len(set(s["country_code"] or s["land"] for s in SKJULTE_PERLER_DB)))

    st.divider()

    col_velg, col_knapp = st.columns([3, 1])
    with col_velg:
        alle_tekst = T["perler_alle"]
        land_valg = st.selectbox(
            T["hjem_filter_land"],
            [alle_tekst] + sorted(list(set(s["land"] for s in SKJULTE_PERLER_DB))),
        )
    with col_knapp:
        st.write("<br>", unsafe_allow_html=True)
        trykk_tips = st.button(
            T["hjem_knapp_tilfeldig"], use_container_width=True, type="primary"
        )

    if trykk_tips:
        tips = (
            [t for t in SKJULTE_PERLER_DB if t["land"] == land_valg]
            if land_valg != alle_tekst
            else SKJULTE_PERLER_DB
        )
        if tips:
            t = random.choice(tips)
            st.success(f"### 🌟 {t['navn']}")

            vis_sted_foto(t, key_suffix="hjem_tips")
            col_b1, col_b2 = st.columns(2)
            with col_b1:
                st.markdown(
                    f"{T['hjem_sted']} {t['by']}, {t['land']}  \n{T['hjem_kategori']} {t['type'].capitalize()}"
                )
                st.write(t["beskrivelse"])
                st.info(f"{T['hjem_tips']} {t['tips']}")
                beste_tidspunkt = t.get("beste_tid", "mai-september (eller hele året)")
                st.caption(f"{T['hjem_beste_tid']} {beste_tidspunkt}")

            with col_b2:
                if "latitude" in t and "longitude" in t:
                    st.markdown(f"**{T['hjem_kart']}**")
                    kart_data = {"lat": [t["latitude"]], "lon": [t["longitude"]]}
                    st.map(kart_data, zoom=6)
                else:
                    st.info(T["hjem_ingen_kart"])


# --- FANE 2: SKJULTE PERLER ---
with fane[2]:
    st.header(T["perler_header"])

    with st.expander(T["perler_kart_expander"], expanded=False):
        perler_med_koordinater = [
            p for p in SKJULTE_PERLER_DB if "latitude" in p and "longitude" in p
        ]
        if perler_med_koordinater:
            kart_df = pd.DataFrame(perler_med_koordinater)

            m = folium.Map(location=[54.0, 14.0], zoom_start=4, tiles="OpenStreetMap")

            for _, row in kart_df.iterrows():
                folium.CircleMarker(
                    location=[row["latitude"], row["longitude"]],
                    radius=3.5,
                    weight=1,
                    color="#E63232",
                    fill=True,
                    fill_color="#E63232",
                    fill_opacity=0.8,
                    tooltip=f"<b>🏛️ {row['navn']}</b><br>📍 {row['by']}, {row['land']}",
                ).add_to(m)

            st_folium(m, width=700, height=500, returned_objects=[])
            st.caption(
                T["perler_kart_caption"].format(
                    len(perler_med_koordinater), len(SKJULTE_PERLER_DB)
                )
            )

            st.write(f"### {T['perler_reisemal_header']}")

            if not kart_df.empty:
                unike_land = sorted(kart_df["land"].unique())

                for land in unike_land:
                    land_rader = kart_df[kart_df["land"] == land].to_dict("records")
                    land_rader = sorter_steder_etter_profil(land_rader)
                    antall_perler = len(land_rader)

                    with st.expander(f"🌍 {land.upper()} ({antall_perler} reisemål)"):
                        for row in land_rader:
                            by_navn = row.get("by", "Ukjent by")
                            sted_navn = sted_tittel_med_profil(row, "🏛️")

                            with st.container(border=True):
                                vis_sted_foto(row, key_suffix=f"kart_{row.get('id', '')}")
                                st.markdown(f"#### {sted_navn}")
                                st.caption(f"📍 {by_navn}, {land}")

                                beskrivelse = row.get(
                                    "beskrivelse",
                                    "Ingen beskrivelse tilgjengelig ennå for dette unike stedet.",
                                )
                                st.write(beskrivelse)

                                st.write("")

                                render_place_actions(
                                    row,
                                    "perler_kart",
                                    f"kart_{row.get('id', by_navn)}",
                                )
            else:
                st.info(T["perler_ingen_reisemal"])
        else:
            st.info(T["perler_ingen_koordinater"])

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        sok_perle = st.text_input(T["perler_sok"], "").lower()
    with col_s2:
        type_perle = st.selectbox(
            T["perler_sorter_type"],
            [T["perler_alle"]] + sorted(list(set(p["type"] for p in SKJULTE_PERLER_DB))),
        )

    st.write("---")

    filtrerte_perler = []
    for perle in SKJULTE_PERLER_DB:
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
                    perle_tittel = sted_tittel_med_profil(p, "🏛️")
                    with cols[j]:
                        vis_sted_foto(p, key_suffix=f"perle_{p['id']}")
                        st.markdown(
                            f"""
                        <div class="travel-card">
                            <h3>{perle_tittel}</h3>
                            <p><b>📍 {p["by"]}, {p["land"]}</b> • <i>{p["type"].capitalize()}</i></p>
                            <p>{p["beskrivelse"]}</p>
                        </div>
                        """,
                            unsafe_allow_html=True,
                        )
                        st.info(f"💡 {p['tips']}")
                        if "beste_tid" in p and p["beste_tid"]:
                            st.caption(f"🕐 Beste tid: {p['beste_tid']}")
                        render_place_actions(p, "hidden_gems", "perle")
                        st.write("<br>", unsafe_allow_html=True)
    else:
        st.info(T["perler_ingen_treff"])


# --- FANE 3: MAT ---
with fane[3]:
    st.header(T["mat_header"])

    col_m1, col_m2 = st.columns(2)
    with col_m1:
        sok_mat = st.text_input(T["mat_sok"], "").lower()
    with col_m2:
        type_mat = st.selectbox(
            T["mat_sorter_type"],
            [T["perler_alle"]]
            + sorted(list(set(m["type"] for m in LOKALE_SPISESTEDER_DB))),
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
                            <p><b>📍 {s["by"]}, {s["land"]}</b> • <i>{s["type"].capitalize()}</i></p>
                            <p>{s["beskrivelse"]}</p>
                        </div>
                        """,
                            unsafe_allow_html=True,
                        )
                        st.success(f"{T['mat_pris']} {s['pris']}")
                        render_place_actions(s, "food", "mat", fremhev_mat=True)
                        st.write("<br>", unsafe_allow_html=True)
    else:
        st.info(T["mat_ingen_treff"])


# --- FANE 4: REISEPLAN ---
with fane[4]:
    st.header(T["reiseplan_header"])
    itinerary_items = get_itinerary_items()

    if not itinerary_items:
        st.info(T["reiseplan_tom"])
    else:
        st.download_button(
            T["reiseplan_last_ned"],
            data=lag_reiseplan_html(itinerary_items),
            file_name="hemmelige-europa-reiseplan.html",
            mime="text/html",
            use_container_width=True,
        )

        if any("latitude" in item and "longitude" in item for item in itinerary_items):
            m = folium.Map(location=[54.0, 14.0], zoom_start=4, tiles="OpenStreetMap")
            for item in itinerary_items:
                if item.get("latitude") and item.get("longitude"):
                    folium.Marker(
                        location=[item["latitude"], item["longitude"]],
                        tooltip=f"{item['navn']} ({item['by']})",
                        popup=f"<b>{item['navn']}</b><br>{item['by']}, {item['land']}",
                    ).add_to(m)
            st_folium(m, width=1100, height=460, returned_objects=[])

        for item in itinerary_items:
            with st.container(border=True):
                vis_sted_foto(item, key_suffix=f"plan_{item['id']}")
                st.markdown(f"### {item['navn']}")
                st.caption(f"{item['by']}, {item['land']} • {item['type']}")
                st.write(item["beskrivelse"])
                col_link, col_remove = st.columns(2)
                with col_link:
                    render_place_actions(item, "itinerary", "reiseplan")
                with col_remove:
                    if st.button("🗑️ Fjern", key=f"remove_{item['id']}", use_container_width=True):
                        remove_itinerary_item(item["id"])
                        st.rerun()

        dager = st.slider(T["reiseplan_ai_prompt"], min_value=1, max_value=14, value=5)
        if st.button(T["reiseplan_ai"], type="primary", use_container_width=True):
            st.write_stream(generer_ai_reiserute(itinerary_items, dager))


# --- FANE 5: TRANSPORT ATLAS (cp.atlas.sk-inspirert) ---
with fane[5]:
    st.header(T["transport_header"])
    st.caption(T["transport_sub"])

    navitia_key = os.environ.get("NAVITIA_API_KEY") or st.secrets.get("NAVITIA_API_KEY", "")
    if navitia_key:
        regioner = hent_navitia_dekning(navitia_key)
        if regioner:
            st.caption(T["transport_navitia_dekning"].format(len(regioner)))
    else:
        st.info(T["transport_navitia_mangler"])

    alle_transport = SKJULTE_PERLER_DB + LOKALE_SPISESTEDER_DB
    stedvalg = bygg_stedvalg_fra_database(alle_transport)
    alle_labels = sorted(stedvalg.keys())

    itinerary_transport = get_itinerary_items()
    if len(itinerary_transport) >= 2:
        st.markdown(f"**{T['transport_reiseplan_hopp']}**")
        hopp_kolonner = st.columns(min(len(itinerary_transport) - 1, 4))
        for idx in range(min(len(itinerary_transport) - 1, 4)):
            a = itinerary_transport[idx]
            b = itinerary_transport[idx + 1]
            with hopp_kolonner[idx]:
                if st.button(
                    f"{a['navn'][:18]}… → {b['navn'][:18]}…",
                    key=f"tp_hopp_{idx}",
                    use_container_width=True,
                ):
                    st.session_state.tp_fra_label = f"{a['navn']} — {a['by']}, {a['land']}"
                    st.session_state.tp_til_label = f"{b['navn']} — {b['by']}, {b['land']}"
                    st.rerun()
    else:
        st.caption(T["transport_ingen_reiseplan"])

    st.divider()

    c_fra, c_til = st.columns(2)
    with c_fra:
        filter_fra = st.text_input(T["transport_sok_fra"], key="tp_filter_fra")
        fra_kandidater = [
            lbl
            for lbl in alle_labels
            if not filter_fra or filter_fra.lower() in lbl.lower()
        ][:100]
        default_fra = st.session_state.get("tp_fra_label")
        fra_index = (
            fra_kandidater.index(default_fra)
            if default_fra in fra_kandidater
            else 0
        )
        fra_label = st.selectbox(
            T["transport_fra"],
            fra_kandidater or alle_labels[:1],
            index=min(fra_index, max(len(fra_kandidater or alle_labels[:1]) - 1, 0)),
            key="tp_fra_select",
        )
    with c_til:
        filter_til = st.text_input(T["transport_sok_til"], key="tp_filter_til")
        til_kandidater = [
            lbl
            for lbl in alle_labels
            if not filter_til or filter_til.lower() in lbl.lower()
        ][:100]
        default_til = st.session_state.get("tp_til_label")
        til_index = (
            til_kandidater.index(default_til)
            if default_til in til_kandidater
            else 0
        )
        til_label = st.selectbox(
            T["transport_til"],
            til_kandidater or alle_labels[:1],
            index=min(til_index, max(len(til_kandidater or alle_labels[:1]) - 1, 0)),
            key="tp_til_select",
        )

    c_dato, c_tid = st.columns(2)
    with c_dato:
        avreise_dato = st.date_input(T["transport_dato"], value=datetime.now().date())
    with c_tid:
        avreise_kl = st.time_input(T["transport_tid"], value=time(9, 0))

    fra_sted = stedvalg.get(fra_label)
    til_sted = stedvalg.get(til_label)

    if st.button(T["transport_sok"], type="primary", use_container_width=True):
        if not fra_sted or not til_sted:
            st.warning(T["transport_velg_begge"])
        elif fra_sted.get("id") == til_sted.get("id"):
            st.warning(T["transport_samme_sted"])
        else:
            avreise_dt = datetime.combine(avreise_dato, avreise_kl)
            reiser, feil = planlegg_kollektivreise(
                float(fra_sted["latitude"]),
                float(fra_sted["longitude"]),
                float(til_sted["latitude"]),
                float(til_sted["longitude"]),
                navitia_key,
                departure_dt=avreise_dt,
            )
            if feil:
                st.warning(feil)
            elif reiser:
                st.session_state.tp_siste_reiser = reiser
                st.session_state.tp_siste_rute = (fra_sted, til_sted)

    if st.session_state.get("tp_siste_reiser"):
        fra_vis, til_vis = st.session_state.get("tp_siste_rute", (fra_sted, til_sted))
        st.subheader(T["transport_resultat"])
        st.caption(f"{fra_vis.get('navn')} → {til_vis.get('navn')}")

        for idx, reise in enumerate(st.session_state.tp_siste_reiser):
            with st.expander(
                f"🕐 {reise['avgang']} → {reise['ankomst']} · {reise['varighet_tekst']} · "
                f"{reise['antall_bytter']} {T['transport_bytter']}",
                expanded=(idx == 0),
            ):
                for etapp in reise["etapper"]:
                    detalj = f"**{etapp['ikon']} {etapp['linje']}**"
                    if etapp["navn"]:
                        detalj += f" — {etapp['navn']}"
                    if etapp["fra"] and etapp["til"]:
                        detalj += f"  \n{etapp['fra']} → {etapp['til']}"
                    if etapp["avgang"]:
                        detalj += f"  \n🕐 {etapp['avgang']}"
                    st.markdown(detalj)

                if reise.get("kart_punkter") and idx == 0:
                    st.markdown(f"**{T['transport_kart']}**")
                    punkter = reise["kart_punkter"]
                    m = folium.Map(location=punkter[0], zoom_start=7)
                    folium.PolyLine(punkter, color="#1A73E8", weight=4, opacity=0.8).add_to(m)
                    folium.Marker(
                        punkter[0],
                        tooltip=fra_vis.get("navn", "Fra"),
                        icon=folium.Icon(color="green"),
                    ).add_to(m)
                    folium.Marker(
                        punkter[-1],
                        tooltip=til_vis.get("navn", "Til"),
                        icon=folium.Icon(color="red"),
                    ).add_to(m)
                    st_folium(m, width=900, height=380, returned_objects=[])

    st.divider()
    st.subheader(T["transport_eksterne"])
    if fra_sted and til_sted:
        eksterne = bygg_eksterne_planleggere(
            fra_sted["by"],
            fra_sted["land"],
            til_sted["by"],
            til_sted["land"],
            spraak,
        )
        e1, e2 = st.columns(2)
        with e1:
            st.link_button(T["transport_lenke_google"], eksterne["google"], use_container_width=True)
            st.link_button(T["transport_lenke_omio"], eksterne["omio"], use_container_width=True)
        with e2:
            st.link_button(T["transport_lenke_rome2rio"], eksterne["rome2rio"], use_container_width=True)
            st.link_button(T["transport_lenke_trainline"], eksterne["trainline"], use_container_width=True)
        if "cp_atlas" in eksterne:
            st.link_button(T["transport_lenke_cp"], eksterne["cp_atlas"], use_container_width=True)


# --- FANE 6: REISE-CHAT ---
with fane[6]:
    st.header(T["chat_header"])
    st.caption(T["chat_caption"])

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
                if "lat" in m and m["lat"]:
                    html_innhold += f'<div class="map-hint">📍 Kartkoordinater lagret for {m.get("sted", "destinasjonen")}: {m["lat"]}, {m["lon"]}</div>'
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
        )
        st.write("")

    for melding in st.session_state.reise_chat:
        with st.chat_message(melding["role"]):
            st.markdown(melding["content"])

            if "lat" in melding and melding["lat"] and melding["lon"]:
                st.write("")
                st.markdown(f"{T['chat_kart_over']} {melding['sted'].capitalize()}:")
                kart_data = {"lat": [melding["lat"]], "lon": [melding["lon"]]}
                st.map(kart_data, zoom=9)

    sporsmal = st.chat_input(T["chat_input"])

    if sporsmal:
        with st.chat_message("user"):
            st.markdown(sporsmal)
        st.session_state.reise_chat.append({"role": "user", "content": sporsmal})

        with st.chat_message("assistant"):
            wiki_kontekst = ""
            sted_for_kart = ""
            lat, lon = None, None

            if sporsmal.lower().startswith("wiki ") or sporsmal.lower().startswith(
                "søk "
            ):
                sted_for_kart = (
                    sporsmal[5:].strip()
                    if sporsmal.lower().startswith("wiki ")
                    else sporsmal[4:].strip()
                )
                with st.spinner(T["chat_wiki_spinner"].format(sted_for_kart)):
                    wiki_info = sok_wikivoyage(sted_for_kart)

                if wiki_info and "Ingen" not in wiki_info:
                    st.markdown(f"{T['chat_wiki_hentet']}\n> *{wiki_info}*")
                    wiki_kontekst = f"Kontekstinformasjon fra Wikivoyage: {wiki_info}"

                lat, lon = hent_koordinater_for_sok(sted_for_kart)

            svar_generator = generer_reiseekspert_stream(sporsmal, wiki_kontekst)
            fullt_svar = st.write_stream(svar_generator)

            if lat and lon:
                st.write("")
                st.markdown(f"{T['chat_kart_over']} {sted_for_kart.capitalize()}:")
                kart_data = {"lat": [lat], "lon": [lon]}
                st.map(kart_data, zoom=9)

        st.session_state.reise_chat.append(
            {
                "role": "assistant",
                "content": fullt_svar,
                "lat": lat,
                "lon": lon,
                "sted": sted_for_kart,
            }
        )

# ========================================
# FOOTER
# ========================================
st.markdown("---")
