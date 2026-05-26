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


# Henter de strukturerte datatabellene fra den nye databasefilen
from database import SKJULTE_PERLER, LOKALE_SPISESTEDER

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
        except:
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
st.set_page_config(page_title="Hemmelige Europa", layout="wide")

# ========================================
# SPRÅKSYSTEM (sidebar-selektor + ordbok)
# ========================================
spraak = st.sidebar.segmented_control(
    "Språk / Language",
    options=["NO", "EN"],
    default="NO",
    key="spraak",
    selection_mode="single",
)

TEKSTER = {
    "NO": {
        "app_tittel": "🇪🇺 Hemmelige Europa",
        "app_caption": "Unike reiseanbefalinger",
        "fane_hjem": "🏠 Hjem",
        "fane_perler": "🏛️ Skjulte perler",
        "fane_mat": "🍽️ Mat",
        "fane_radar": "🧠 For deg",
        "fane_chat": "💬 Reise-chat",
        "hjem_header": "Oppdag det ekte Europa",
        "hjem_metric_perler": "🏛️ Registrerte perler",
        "hjem_metric_spisesteder": "🍽️ Unike spisesteder",
        "hjem_metric_land": "🇪🇺 Unike land",
        "hjem_filter_land": "Filtrer lykkehjul på land",
        "hjem_knapp_tilfeldig": "🎲 Gi meg en tilfeldig perle!",
        "hjem_sted": "📍 Sted:",
        "hjem_kategori": "📂 Kategori:",
        "hjem_tips": "💡 Tips:",
        "hjem_beste_tid": "🕐 Beste tidspunkt:",
        "hjem_kart": "🗺️ Kartplassering:",
        "hjem_ingen_kart": "🗺️ Kartkoordinater for dette stedet er ikke lagt inn ennå.",
        "perler_header": "🏛️ Utforsk skjulte skatter",
        "perler_kart_expander": "🗺️ Vis alle perler på kart",
        "perler_kart_caption": "Viser {0} av {1} perler med kartkoordinater. 🖱️ Hold musepekeren over et punkt for detaljer.",
        "perler_reisemal_header": "🗺️ Reisemål og overnatting fordelt på land",
        "perler_booking": "🏨 Sjekk priser på Booking.com",
        "perler_ingen_reisemal": "Ingen reisemål å vise.",
        "perler_ingen_koordinater": "Ingen perler har kartkoordinater ennå.",
        "perler_sok": "🔍 Søk etter navn eller by (perler)",
        "perler_sorter_type": "Sorter etter type",
        "perler_alle": "Alle",
        "perler_ingen_treff": "Ingen perler matchet søket ditt.",
        "mat_header": "🍽️ Autentiske spisesteder",
        "mat_sok": "🔍 Søk etter navn eller by (restauranter)",
        "mat_sorter_type": "Sorter etter kjøkkentype",
        "mat_pris": "💰 Prisnivå:",
        "mat_ingen_treff": "Ingen spisesteder matchet søket ditt.",
        "radar_tittel": "📍 Radaren – finn skjulte perler nær deg",
        "radar_sub": "Oppdag reisemål og spisesteder i nærheten av deg eller i et valgt land.",
        "radar_metode": "Velg søkemetode",
        "radar_gps": "Bruk min GPS-posisjon",
        "radar_land_sok": "Søk på land",
        "radar_radius": "Maks avstand (km)",
        "radar_spinner": "Henter GPS-posisjonen din...",
        "radar_sentrum_gps": "din GPS-posisjon",
        "radar_warning": "Kunne ikke hente GPS-posisjon. Sjekk tillatelser.",
        "radar_velg_land": "Velg et land",
        "radar_knapp_skann": "🔍 Skann området",
        "radar_fant": "Fant",
        "radar_destinasjoner": "destinasjoner i nærheten av",
        "radar_unna": "unna",
        "radar_kategori": "Kategori",
        "radar_pris": "Pris",
        "radar_tips": "Tips",
        "radar_booking": "🏨 Bestill hotell i",
        "radar_ingen_treff": "Ingen destinasjoner funnet innenfor valgt avstand eller område.",
        "radar_ingen_data": "Ingen steder tilgjengelig for denne regionen.",
        "chat_header": "💬 Spør reiseeksperten",
        "chat_caption": "Tips: Start meldingen med `wiki <stedsnavn>` (f.eks. `wiki Berlin`) for å hente rådata før AI-en svarer!",
        "chat_last_ned": "📄 Last ned reiseplanen (Klar for PDF / Utskrift)",
        "chat_kart_over": "🗺️ Interaktivt kart over",
        "chat_input": "Hvor vil du reise, eller hva lurer du på?",
        "chat_wiki_spinner": "Henter bakgrunnsinfo om {0}...",
        "chat_wiki_hentet": "📂 **Hentet fra Wikivoyage:**",
    },
    "EN": {
        "app_tittel": "🇪🇺 Hidden Europe",
        "app_caption": "Unique travel recommendations",
        "fane_hjem": "🏠 Home",
        "fane_perler": "🏛️ Hidden Gems",
        "fane_mat": "🍽️ Food",
        "fane_radar": "🧠 For You",
        "fane_chat": "💬 Travel Chat",
        "hjem_header": "Discover the real Europe",
        "hjem_metric_perler": "🏛️ Registered gems",
        "hjem_metric_spisesteder": "🍽️ Unique restaurants",
        "hjem_metric_land": "🇪🇺 Unique countries",
        "hjem_filter_land": "Filter wheel on country",
        "hjem_knapp_tilfeldig": "🎲 Give me a random gem!",
        "hjem_sted": "📍 Location:",
        "hjem_kategori": "📂 Category:",
        "hjem_tips": "💡 Tip:",
        "hjem_beste_tid": "🕐 Best time:",
        "hjem_kart": "🗺️ Map location:",
        "hjem_ingen_kart": "🗺️ Map coordinates for this place have not been added yet.",
        "perler_header": "🏛️ Explore hidden treasures",
        "perler_kart_expander": "🗺️ Show all gems on map",
        "perler_kart_caption": "Showing {0} of {1} gems with map coordinates. 🖱️ Hover over a point for details.",
        "perler_reisemal_header": "🗺️ Destinations and accommodation by country",
        "perler_booking": "🏨 Check prices on Booking.com",
        "perler_ingen_reisemal": "No destinations to show.",
        "perler_ingen_koordinater": "No gems have map coordinates yet.",
        "perler_sok": "🔍 Search by name or city (gems)",
        "perler_sorter_type": "Filter by type",
        "perler_alle": "All",
        "perler_ingen_treff": "No gems matched your search.",
        "mat_header": "🍽️ Authentic restaurants",
        "mat_sok": "🔍 Search by name or city (restaurants)",
        "mat_sorter_type": "Filter by cuisine type",
        "mat_pris": "💰 Price level:",
        "mat_ingen_treff": "No restaurants matched your search.",
        "radar_tittel": "📍 Radar – find hidden gems near you",
        "radar_sub": "Discover travel destinations and restaurants near you or in a selected country.",
        "radar_metode": "Select search method",
        "radar_gps": "Use my GPS location",
        "radar_land_sok": "Search by country",
        "radar_radius": "Max distance (km)",
        "radar_spinner": "Retrieving your GPS location...",
        "radar_sentrum_gps": "your GPS location",
        "radar_warning": "Could not retrieve GPS location. Please check browser permissions.",
        "radar_velg_land": "Select a country",
        "radar_knapp_skann": "🔍 Scan area",
        "radar_fant": "Found",
        "radar_destinasjoner": "destinations near",
        "radar_unna": "away",
        "radar_kategori": "Category",
        "radar_pris": "Price",
        "radar_tips": "Tips",
        "radar_booking": "🏨 Book hotel in",
        "radar_ingen_treff": "No destinations found within the selected distance or area.",
        "radar_ingen_data": "No coordinates available for the selected region.",
        "chat_header": "💬 Ask the travel expert",
        "chat_caption": "Tip: Start your message with `wiki <place>` (e.g. `wiki Berlin`) to fetch raw data before the AI responds!",
        "chat_last_ned": "📄 Download travel plan (Ready for PDF / Print)",
        "chat_kart_over": "🗺️ Interactive map of",
        "chat_input": "Where do you want to travel, or what would you like to know?",
        "chat_wiki_spinner": "Fetching background info about {0}...",
        "chat_wiki_hentet": "📂 **Retrieved from Wikivoyage:**",
    },
}

T = TEKSTER[spraak]


# --- PROFF STYLING (CSS-INJEKSJON) ---
st.markdown(
    """
    <style>
        /* ── Importer Inter Font ── */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

        html, body, [class*="css"], .stMarkdown {
            font-family: 'Inter', sans-serif !important;
        }

        /* ── STANDARD (LYST TEMA - GEMINI STYLE) ── */
        h1 {
            font-weight: 700 !important;
            color: #1F1F1F !important;
            letter-spacing: -0.04em !important;
        }
        h2, h3, .stMarkdown, p, span, label {
            color: #1F1F1F !important;
        }
        .streamlit-expanderHeader {
            background-color: #F0F4F9 !important; /* Gemini lys gråblå */
            border: 1px solid #E1E3E1 !important;
            color: #1F1F1F !important;
            border-radius: 8px !important;
        }

        /* ── MØRKT TEMA (DARK MODE - GEMINI STYLE) ── */
        @media (prefers-color-scheme: dark) {
            h1 {
                color: #F0F4F9 !important;
            }
            h2, h3, .stMarkdown, p, span, label {
                color: #E3E3E3 !important; /* Den myke Gemini-hvite */
            }
            .streamlit-expanderHeader {
                background-color: #1E1E24 !important; /* Dyp matt tone */
                border: 1px solid #2D2F36 !important;
                color: #E3E3E3 !important;
            }
        }

        /* ── PREMIUM KNAPPER (FELLES) ── */
        .stLinkButton > a {
            background-color: #0F172A !important;
            color: #FFFFFF !important;
            border-radius: 6px !important;
            border: none !important;
            padding: 0.6rem 1.5rem !important;
            font-weight: 500 !important;
            text-decoration: none !important;
        }
        .stLinkButton > a:hover {
            background-color: #1E293B !important;
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

if "reisehistorikk" not in st.session_state:
    st.session_state.reisehistorikk = []
if "reise_chat" not in st.session_state:
    st.session_state.reise_chat = []

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
    except:
        pass
    return None, None


def generer_reiseekspert_stream(sporsmal, kontekst=""):
    """Generator-funksjon for å streame AI-svar fra OpenRouter m/ RAG-databasekobling"""
    if not API_KEY:
        yield "⚠️ **Systemmelding:** `OPENROUTER_API_KEY` mangler i miljøvariablene. Kan ikke kontakte reiseeksperten."
        return

    # RAG: Sjekk om spørsmålet nevner land eller byer fra vår eksterne databasefil
    relevante_perler = []
    for p in SKJULTE_PERLER:
        if p["land"].lower() in sporsmal.lower() or p["by"].lower() in sporsmal.lower():
            relevante_perler.append(
                f"- {p['navn']} ({p['by']}, {p['land']}): {p['beskrivelse']} (Tips: {p['tips']})"
            )

    for s in LOKALE_SPISESTEDER:
        if s["land"].lower() in sporsmal.lower() or s["by"].lower() in sporsmal.lower():
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
                except:
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


# ========================================
# APPLIKASJONSSTRUKTUR (UI)
# ========================================
st.title(T["app_tittel"])
st.caption(T["app_caption"])

fane = st.tabs(
    [T["fane_hjem"], T["fane_perler"], T["fane_mat"], T["fane_radar"], T["fane_chat"]]
)


def filtrer_data(data):
    """Filtrerer bort steder uten koordinater (latitude/longitude)."""
    return [d for d in data if "latitude" in d and "longitude" in d]


# --- FANE 0: HJEM ---
with fane[0]:
    st.header(T["hjem_header"])
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(T["hjem_metric_perler"], len(SKJULTE_PERLER))
    with col2:
        st.metric(T["hjem_metric_spisesteder"], len(LOKALE_SPISESTEDER))
    with col3:
        st.metric(T["hjem_metric_land"], len(set(s["land"] for s in SKJULTE_PERLER)))

    st.divider()

    col_velg, col_knapp = st.columns([3, 1])
    with col_velg:
        alle_tekst = T["perler_alle"]
        land_valg = st.selectbox(
            T["hjem_filter_land"],
            [alle_tekst] + sorted(list(set(s["land"] for s in SKJULTE_PERLER))),
        )
    with col_knapp:
        st.write("<br>", unsafe_allow_html=True)
        trykk_tips = st.button(
            T["hjem_knapp_tilfeldig"], use_container_width=True, type="primary"
        )

    if trykk_tips:
        tips = (
            [t for t in SKJULTE_PERLER if t["land"] == land_valg]
            if land_valg != alle_tekst
            else SKJULTE_PERLER
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

# --- FANE 1: SKJULTE PERLER ---
with fane[1]:
    st.header(T["perler_header"])

    with st.expander(T["perler_kart_expander"], expanded=False):
        perler_med_koordinater = [
            p for p in SKJULTE_PERLER if "latitude" in p and "longitude" in p
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
                    len(perler_med_koordinater), len(SKJULTE_PERLER)
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

                                by_kodet = by_navn.replace(" ", "+")
                                land_kodet = land.replace(" ", "+")
                                booking_url = f"https://www.booking.com/searchresults.html?ss={by_kodet},+{land_kodet}"

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
            [T["perler_alle"]] + sorted(list(set(p["type"] for p in SKJULTE_PERLER))),
        )

    st.write("---")

    filtrerte_perler = []
    for perle in SKJULTE_PERLER:
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
                            st.write("<br>", unsafe_allow_html=True)
    else:
        st.info(T["perler_ingen_treff"])

# --- FANE 2: MAT ---
with fane[2]:
    st.header(T["mat_header"])

    col_m1, col_m2 = st.columns(2)
    with col_m1:
        sok_mat = st.text_input(T["mat_sok"], "").lower()
    with col_m2:
        type_mat = st.selectbox(
            T["mat_sorter_type"],
            [T["perler_alle"]]
            + sorted(list(set(m["type"] for m in LOKALE_SPISESTEDER))),
        )

    st.write("---")

    filtrert_mat = []
    for sted in LOKALE_SPISESTEDER:
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
                        st.write("<br>", unsafe_allow_html=True)
    else:
        st.info(T["mat_ingen_treff"])

# ========================================================
# FANE 3: RADAREN (MED GPS- OG LANDSØK)
# ========================================================
with fane[3]:
    st.subheader(T["radar_tittel"])
    st.write(T["radar_sub"])

    alle_steder_i_db = SKJULTE_PERLER + LOKALE_SPISESTEDER
    filtrert_for_radar = filtrer_data(alle_steder_i_db)

    if filtrert_for_radar:
        # 1. Velg søkemetode (Posisjon eller Land)
        soke_metode = st.radio(
            T["radar_metode"],
            options=[T["radar_gps"], T["radar_land_sok"]],
            horizontal=True,
        )

        maks_avstand = st.slider(
            T["radar_radius"], min_value=10, max_value=500, value=150, step=10
        )
        perler_i_naerheten = []
        soke_sentrum_navn = ""

        # 2. LOGIKK FOR Å HENTE SØKESENTRUM
        if soke_metode == T["radar_gps"]:
            with st.spinner(T["radar_spinner"]):
                geo = get_geolocation()

            if geo and "coords" in geo:
                min_lat = geo["coords"]["latitude"]
                min_lon = geo["coords"]["longitude"]
                soke_sentrum_navn = T["radar_sentrum_gps"]

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

                        # 🔒 Sikker kommersiell affiliate-lenke
                        try:
                            partner_aid = st.secrets["BOOKING_AID"]
                        except Exception:
                            partner_aid = "888888"

                        by_kodet = p["by"].replace(" ", "+")
                        land_kodet = p["land"].replace(" ", "+")
                        booking_url = f"https://www.booking.com/searchresults.html?ss={by_kodet},+{land_kodet}&aid={partner_aid}"

                        st.link_button(
                            f"{T['radar_booking']} {p['by']}",
                            booking_url,
                            use_container_width=True,
                        )
            else:
                st.info(T["radar_ingen_treff"])
    else:
        st.write(T["radar_ingen_data"])


# --- FANE 4: REISE-CHAT ---
with fane[4]:
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
        st.rerun()

# ========================================
# FOOTER
# ========================================
st.markdown("---")
