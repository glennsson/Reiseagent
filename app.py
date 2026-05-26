import streamlit as st
import requests
import os
import random
import json
import pandas as pd
import folium
from streamlit_folium import st_folium
from dotenv import load_dotenv
from streamlit_js_eval import get_geolocation

import math
from urllib.parse import urlencode

from data_store import (
    add_itinerary_item,
    get_affiliate_stats,
    get_itinerary_items,
    get_places,
    log_affiliate_click,
    remove_itinerary_item,
)
from translations import TEKSTER


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


def last_inn_data():
    """Henter lagret data fra fil hvis den eksisterer"""
    default_data = {"reisehistorikk": [], "reise_chat": []}
    if os.path.exists(PROFIL_FIL):
        try:
            with open(PROFIL_FIL, "r", encoding="utf-8") as f:
                lagret = json.load(f)
                if "reisehistorikk" not in lagret:
                    lagret["reisehistorikk"] = []
                if "reise_chat" not in lagret:
                    lagret["reise_chat"] = []
                return lagret
        except Exception:
            return default_data
    return default_data


def lagre_data(historikk, chat):
    """Lagrer både reisehistorikk og chatlogg til JSON-filen"""
    try:
        data_til_lagring = {"reisehistorikk": historikk, "reise_chat": chat}
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
    if affiliate_stats["top_sources"]:
        st.write(T["affiliate_top_sources"])
        for row in affiliate_stats["top_sources"]:
            st.caption(f"{row['source_view']}: {row['clicks']}")



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

    # RAG: Sjekk om spørsmålet nevner land eller byer fra vår eksterne databasefil
    relevante_perler = []
    sporsmal_lav = sporsmal.lower()
    for p in SKJULTE_PERLER_DB:
        if p["land"].lower() in sporsmal_lav or p["by"].lower() in sporsmal_lav:
            relevante_perler.append(
                f"- {p['navn']} ({p['by']}, {p['land']}): {p['beskrivelse']} (Tips: {p['tips']})"
            )

    for s in LOKALE_SPISESTEDER_DB:
        if s["land"].lower() in sporsmal_lav or s["by"].lower() in sporsmal_lav:
            relevante_perler.append(
                f"- Spisested: {s['navn']} ({s['by']}, {s['land']}): {s['beskrivelse']} (Pris: {s['pris']})"
            )

    intern_kontekst = ""
    if relevante_perler:
        intern_kontekst = (
            "\n\nDu SKAL prioritere å anbefale disse spesifikke skjulte perlene fra vår interne database om de passer til spørsmålet:\n"
            + "\n".join(relevante_perler[:4])
        )

    system_melding = (
        "Du er en europeisk reiseekspert som elsker skjulte perler og autentisk kultur. "
        "Svar kort, engasjerende, spesifikt og entusiastisk. Maks 5 setninger."
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


def bygg_booking_url(by, land):
    partner_aid = st.secrets.get("BOOKING_AID", "888888")
    query = urlencode({"ss": f"{by}, {land}", "aid": partner_aid})
    return f"https://www.booking.com/searchresults.html?{query}"


def render_place_actions(place, source_view, key_prefix):
    booking_url = bygg_booking_url(place["by"], place["land"])
    col_add, col_booking = st.columns(2)
    with col_add:
        if st.button(T["favoritt_knapp"], key=f"{key_prefix}_add_{place['id']}", use_container_width=True):
            add_itinerary_item(place)
            st.success(T["favoritt_lagt_til"])
    with col_booking:
        if st.button(T["booking_register_click"], key=f"{key_prefix}_booking_{place['id']}", use_container_width=True):
            log_affiliate_click(place, source_view, spraak, booking_url)
            st.session_state[f"booking_url_{key_prefix}_{place['id']}"] = booking_url
            st.success(T["affiliate_logget"])

    if st.session_state.get(f"booking_url_{key_prefix}_{place['id']}"):
        st.caption(T["affiliate_disclosure"])
        st.link_button(
            f"{T['booking_open_link']} {place['by']}",
            st.session_state[f"booking_url_{key_prefix}_{place['id']}"] ,
            use_container_width=True,
        )


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
        T["fane_chat"],
    ]
)


def filtrer_data(data):
    """Filtrerer bort steder uten koordinater (latitude/longitude)."""
    return [d for d in data if "latitude" in d and "longitude" in d]


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
            perler_i_naerheten = sorted(perler_i_naerheten, key=lambda x: x["avstand"])

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

                    expander_tittel = (
                        f"{p['navn']} — {km} km {T['radar_unna']} ({p['by']})"
                        if km > 0
                        else f"{p['navn']} ({p['by']})"
                    )

                    with st.expander(expander_tittel):
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
                    land_df = kart_df[kart_df["land"] == land].sort_values(by="by")
                    antall_perler = len(land_df)

                    with st.expander(f"🌍 {land.upper()} ({antall_perler} reisemål)"):
                        for _, row in land_df.iterrows():
                            by_navn = row.get("by", "Ukjent by")
                            sted_navn = row.get("navn", "Hemmelig sted")

                            with st.container(border=True):
                                st.markdown(f"#### 🏛️ {sted_navn}")
                                st.caption(f"📍 {by_navn}, {land}")

                                beskrivelse = row.get(
                                    "beskrivelse",
                                    "Ingen beskrivelse tilgjengelig ennå for dette unike stedet.",
                                )
                                st.write(beskrivelse)

                                st.write("")

                                booking_url = bygg_booking_url(by_navn, land)

                                st.link_button(
                                    T["perler_booking"],
                                    booking_url,
                                    use_container_width=True,
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

    if filtrerte_perler:
        for i in range(0, len(filtrerte_perler), 3):
            cols = st.columns(3)
            for j in range(3):
                if i + j < len(filtrerte_perler):
                    p = filtrerte_perler[i + j]
                    with cols[j]:
                        st.markdown(
                            f"""
                        <div class="travel-card">
                            <h3>🏛️ {p["navn"]}</h3>
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

    if filtrert_mat:
        for i in range(0, len(filtrert_mat), 3):
            cols = st.columns(3)
            for j in range(3):
                if i + j < len(filtrert_mat):
                    s = filtrert_mat[i + j]
                    with cols[j]:
                        st.markdown(
                            f"""
                        <div class="travel-card">
                            <h3>🍽️ {s["navn"]}</h3>
                            <p><b>📍 {s["by"]}, {s["land"]}</b> • <i>{s["type"].capitalize()}</i></p>
                            <p>{s["beskrivelse"]}</p>
                        </div>
                        """,
                            unsafe_allow_html=True,
                        )
                        st.success(f"{T['mat_pris']} {s['pris']}")
                        render_place_actions(s, "food", "mat")
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


# --- FANE 5: REISE-CHAT ---
with fane[5]:
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
