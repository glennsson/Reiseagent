"""Hent reisemål fra Wikivoyage (MediaWiki API) med koordinater og beskrivelser."""
import re
import time
from typing import Dict, Iterable, List, Optional, Tuple

import requests

WIKIVOYAGE_API = "https://en.wikivoyage.org/w/api.php"
USER_AGENT = "HemmeligeEuropa/1.0 (https://github.com/)"
DEFAULT_PAUSE_SEC = 0.12

# Nyttige kategorier på en.wikivoyage.org
ANBEFALTE_KATEGORIER = (
    "Previously Off the beaten path",
    "UNESCO World Heritage Sites",
    "National parks",
    "Archaeological sites",
)

# Byer/destinasjoner for å parse See/Do (kan utvides)
EUROPA_DESTINASJONER = (
    "Ghent",
    "Tallinn",
    "Ljubljana",
    "Brno",
    "Plovdiv",
    "Sibiu",
    "Kotor",
    "Mostar",
    "Tirana",
    "Valletta",
    "Palermo",
    "Matera",
    "Sintra",
    "Porto",
    "Bilbao",
    "San Sebastián",
    "Bergen",
    "Trondheim",
    "Turku",
    "Riga",
    "Vilnius",
    "Lviv",
    "Cluj-Napoca",
    "Sarajevo",
    "Skopje",
    "Prizren",
    "Ohrid",
    "Timișoara",
    "Debrecen",
    "Pécs",
    "Salzburg",
    "Graz",
    "Innsbruck",
    "Strasbourg",
    "Colmar",
    "Nantes",
    "Toulouse",
    "Bordeaux",
    "Leipzig",
    "Dresden",
    "Lübeck",
    "Helsingør",
    "Ålesund",
)

LISTING_TYPER = frozenset({"see", "do", "eat", "drink", "buy", "sleep"})

LAND_TIL_NORSK = {
    "Germany": "Tyskland",
    "Deutschland": "Tyskland",
    "France": "Frankrike",
    "Italy": "Italia",
    "Spain": "Spania",
    "United Kingdom": "Storbritannia",
    "England": "Storbritannia",
    "Scotland": "Storbritannia",
    "Wales": "Storbritannia",
    "Netherlands": "Nederland",
    "Belgium": "Belgia",
    "Switzerland": "Sveits",
    "Austria": "Østerrike",
    "Ireland": "Irland",
    "Norway": "Norge",
    "Sweden": "Sverige",
    "Denmark": "Danmark",
    "Finland": "Finland",
    "Iceland": "Island",
    "Poland": "Polen",
    "Czech Republic": "Tsjekkia",
    "Czechia": "Tsjekkia",
    "Slovakia": "Slovakia",
    "Hungary": "Ungarn",
    "Romania": "Romania",
    "Bulgaria": "Bulgaria",
    "Ukraine": "Ukraina",
    "Estonia": "Estland",
    "Latvia": "Latvia",
    "Lithuania": "Litauen",
    "Portugal": "Portugal",
    "Greece": "Hellas",
    "Croatia": "Kroatia",
    "Slovenia": "Slovenia",
    "Serbia": "Serbia",
    "Montenegro": "Montenegro",
    "Bosnia and Herzegovina": "Bosnia",
    "Albania": "Albania",
    "North Macedonia": "Nord-Makedonia",
    "Kosovo": "Kosovo",
    "Malta": "Malta",
    "Cyprus": "Kypros",
    "Turkey": "Tyrkia",
    "Georgia": "Georgia",
    "Armenia": "Armenia",
    "Azerbaijan": "Aserbajdsjan",
}


def _api_get(params: Dict, pause: float = DEFAULT_PAUSE_SEC) -> Dict:
    params = {**params, "format": "json"}
    resp = requests.get(
        WIKIVOYAGE_API,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    if pause:
        time.sleep(pause)
    return resp.json()


def hent_kategori_medlemmer(
    kategori: str,
    *,
    limit: int = 200,
    pause: float = DEFAULT_PAUSE_SEC,
) -> List[str]:
    """Alle artikkelnavn i en Wikivoyage-kategori (uten underkategorier)."""
    titler: List[str] = []
    cmcontinue = None
    while len(titler) < limit:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{kategori}",
            "cmlimit": min(50, limit - len(titler)),
            "cmtype": "page",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue
        data = _api_get(params, pause=pause)
        for item in data.get("query", {}).get("categorymembers", []):
            title = item.get("title", "")
            if title and ":" not in title:
                titler.append(title)
        cont = data.get("continue", {})
        cmcontinue = cont.get("cmcontinue")
        if not cmcontinue:
            break
    return titler[:limit]


def _hent_seksjonsindekser(destinasjon: str, pause: float) -> Dict[str, str]:
    data = _api_get(
        {
            "action": "parse",
            "page": destinasjon,
            "prop": "sections",
        },
        pause=pause,
    )
    parse = data.get("parse") or {}
    ut: Dict[str, str] = {}
    for sec in parse.get("sections", []):
        line = (sec.get("line") or "").strip().lower()
        if line in {"see", "do", "eat", "drink"}:
            ut[line] = str(sec.get("index", ""))
    return ut


def _hent_seksjon_wikitext(destinasjon: str, section_index: str, pause: float) -> str:
    data = _api_get(
        {
            "action": "parse",
            "page": destinasjon,
            "section": section_index,
            "prop": "wikitext",
        },
        pause=pause,
    )
    return (data.get("parse", {}).get("wikitext", {}) or {}).get("*", "")


def _parse_template_params(block: str) -> Dict[str, str]:
    """Wikivoyage-listings har ofte mange |key=value på samme linje."""
    params: Dict[str, str] = {}
    for match in re.finditer(
        r"\|\s*([a-z_]+)\s*=\s*(.*?)(?=\s*\|\s*[a-z_]+\s*=|\s*$)",
        block,
        re.IGNORECASE | re.DOTALL,
    ):
        params[match.group(1).lower()] = match.group(2).strip()
    return params


def _parse_listings_fra_wikitext(
    wikitext: str,
    *,
    destinasjon: str,
    land: str,
    listing_type: str,
) -> List[Dict]:
    kandidater: List[Dict] = []
    pattern = re.compile(
        r"\{\{(see|do|eat|drink|buy|sleep)\s*\n(.*?)\n\}\}",
        re.DOTALL | re.IGNORECASE,
    )
    # Fallback når }} ligger rett etter siste parameter uten ekstra linjeskift
    pattern_alt = re.compile(
        r"\{\{(see|do|eat|drink|buy|sleep)\s*\n(.*?)\}\}",
        re.DOTALL | re.IGNORECASE,
    )
    matches = list(pattern.finditer(wikitext))
    if not matches:
        matches = list(pattern_alt.finditer(wikitext))
    for match in matches:
        kind = match.group(1).lower()
        if kind not in LISTING_TYPER:
            continue
        params = _parse_template_params(match.group(2))
        navn = (params.get("name") or params.get("alt") or "").strip()
        if not navn:
            continue
        content = (params.get("content") or "").replace("\n", " ").strip()
        lat = params.get("lat")
        lon = params.get("long") or params.get("lon")
        latitude = longitude = None
        try:
            if lat and lon:
                latitude = round(float(lat), 5)
                longitude = round(float(lon), 5)
        except ValueError:
            pass
        sted_type = "gastronomi" if kind in ("eat", "drink") else "kultur"
        source_type = "restaurant" if kind in ("eat", "drink") else "hidden_gem"
        kandidater.append(
            {
                "navn": navn,
                "by": destinasjon,
                "land": land,
                "type": sted_type,
                "beskrivelse": content
                or f"Anbefalt på Wikivoyage ({listing_type} i {destinasjon}).",
                "tips": f"Wikivoyage: {destinasjon} — {kind}.",
                "beste_tid": "",
                "pris": "€€" if kind in ("eat", "drink", "sleep") else "€",
                "latitude": latitude,
                "longitude": longitude,
                "source_type": source_type,
                "source_url": f"https://en.wikivoyage.org/wiki/{destinasjon.replace(' ', '_')}",
                "wikivoyage_listing": kind,
            }
        )
    return kandidater


def hent_listings_for_destinasjon(
    destinasjon: str,
    *,
    seksjoner: Iterable[str] = ("see", "do"),
    land: str = "",
    pause: float = DEFAULT_PAUSE_SEC,
) -> List[Dict]:
    """Henter {{see}}/{{do}}/… fra en by-/destinasjonsartikkel."""
    seksjoner = [s.lower() for s in seksjoner]
    indekser = _hent_seksjonsindekser(destinasjon, pause)
    if not land:
        land = gjett_land_fra_destinasjon(destinasjon)
    alle: List[Dict] = []
    for sek in seksjoner:
        idx = indekser.get(sek)
        if not idx:
            continue
        wikitext = _hent_seksjon_wikitext(destinasjon, idx, pause)
        alle.extend(
            _parse_listings_fra_wikitext(
                wikitext,
                destinasjon=destinasjon,
                land=land,
                listing_type=sek,
            )
        )
    return alle


def gjett_land_fra_destinasjon(destinasjon: str) -> str:
    """Forsøker å hente land fra destinasjonsside-intro."""
    data = _api_get(
        {
            "action": "query",
            "titles": destinasjon,
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "exchars": 400,
        }
    )
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        tekst = page.get("extract", "")
        for fremmed, norsk in LAND_TIL_NORSK.items():
            if fremmed in tekst:
                return norsk
    return "Europa"


def hent_artikkel_med_koordinater(
    tittel: str,
    *,
    pause: float = DEFAULT_PAUSE_SEC,
) -> Optional[Dict]:
    """Henter intro, koordinater og land for en frittstående Wikivoyage-artikkel."""
    data = _api_get(
        {
            "action": "query",
            "titles": tittel,
            "prop": "coordinates|extracts",
            "colimit": 1,
            "exintro": 1,
            "explaintext": 1,
            "exchars": 400,
            "redirects": 1,
        },
        pause=pause,
    )
    pages = data.get("query", {}).get("pages", {})
    for page_id, page in pages.items():
        if page_id == "-1":
            return None
        coords = page.get("coordinates") or []
        if not coords:
            return None
        lat = round(float(coords[0]["lat"]), 5)
        lon = round(float(coords[0]["lon"]), 5)
        beskrivelse = (page.get("extract") or "").replace("\n", " ").strip()
        land = "Europa"
        for fremmed, norsk in LAND_TIL_NORSK.items():
            if fremmed in beskrivelse or fremmed in tittel:
                land = norsk
                break
        return {
            "navn": tittel,
            "by": tittel,
            "land": land,
            "type": "kultur",
            "beskrivelse": beskrivelse
            or f"Destinasjon fra Wikivoyage: {tittel}.",
            "tips": "Kilde: Wikivoyage (kategori eller artikkel).",
            "beste_tid": "",
            "pris": "€",
            "latitude": lat,
            "longitude": lon,
            "source_type": "hidden_gem",
            "source_url": f"https://en.wikivoyage.org/wiki/{tittel.replace(' ', '_')}",
        }
    return None


def geokod_med_nominatim(
    navn: str,
    by: str,
    land: str,
    *,
    pause: float = DEFAULT_PAUSE_SEC,
) -> Tuple[Optional[float], Optional[float]]:
    try:
        q = ", ".join(x for x in (navn, by, land) if x)
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        treff = resp.json()
        if pause:
            time.sleep(max(pause, 1.0))
        if treff:
            return round(float(treff[0]["lat"]), 5), round(float(treff[0]["lon"]), 5)
    except Exception:
        pass
    return None, None


def fyll_manglende_koordinater(
    steder: List[Dict],
    *,
    pause: float = DEFAULT_PAUSE_SEC,
) -> List[Dict]:
    ut = []
    for sted in steder:
        sted = dict(sted)
        if sted.get("latitude") is None or sted.get("longitude") is None:
            lat, lon = geokod_med_nominatim(
                sted.get("navn", ""),
                sted.get("by", ""),
                sted.get("land", ""),
                pause=pause,
            )
            sted["latitude"] = lat
            sted["longitude"] = lon
        ut.append(sted)
    return ut
