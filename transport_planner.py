"""Europeisk kollektivtransport-planlegger (inspirert av cp.atlas.sk)."""

from datetime import datetime
from urllib.parse import quote

import requests

NAVITIA_BASE = "https://api.navitia.io/v1"
USER_AGENT = "HemmeligeEuropa/1.0"


def _navitia_headers(api_key):
    return {"Authorization": api_key, "User-Agent": USER_AGENT}


def hent_navitia_dekning(api_key):
    """Returnerer liste over regioner Navitia dekker."""
    try:
        r = requests.get(
            f"{NAVITIA_BASE}/coverage",
            headers=_navitia_headers(api_key),
            timeout=10,
        )
        r.raise_for_status()
        regions = r.json().get("regions", [])
        return [reg.get("id", "") for reg in regions if reg.get("id")]
    except Exception:
        return []


def _finn_dekning_for_punkt(api_key, lat, lon):
    """Finner Navitia coverage-region nærmest et koordinatpunkt."""
    try:
        r = requests.get(
            f"{NAVITIA_BASE}/coord/{lon};{lat}/coverage",
            headers=_navitia_headers(api_key),
            params={"distance": 50000},
            timeout=10,
        )
        if r.ok:
            for reg in r.json().get("regions", []):
                region_id = reg.get("id")
                if region_id:
                    return region_id
    except Exception:
        pass
    return None


def _hent_journeys_for_dekning(api_key, coverage_id, params):
    """Kaller Navitia journeys for en spesifikk coverage-region."""
    r = requests.get(
        f"{NAVITIA_BASE}/coverage/{coverage_id}/journeys",
        headers=_navitia_headers(api_key),
        params=params,
        timeout=25,
    )
    if r.status_code == 401:
        return None, "Ugyldig Navitia API-nøkkel."
    if r.status_code in {404, 400}:
        return None, None
    r.raise_for_status()
    journeys = r.json().get("journeys", [])
    if not journeys:
        return None, None
    return [_parse_journey(j) for j in journeys], None


def planlegg_kollektivreise(
    from_lat,
    from_lon,
    to_lat,
    to_lon,
    api_key,
    departure_dt=None,
    antall=5,
):
    """
    Søker multi-modale reiser via Navitia (tog, buss, metro der data finnes).
    Returnerer (reiser_liste, feilmelding).
    """
    if not api_key:
        return [], "NAVITIA_API_KEY mangler"

    dt = departure_dt or datetime.now()
    dt_str = dt.strftime("%Y%m%dT%H%M%S")

    params = {
        "from": f"{from_lon};{from_lat}",
        "to": f"{to_lon};{to_lat}",
        "datetime": dt_str,
        "datetime_represents": "departure",
        "count": antall,
        "max_nb_transfers": 4,
        "min_nb_transfers": 0,
    }

    dekning_fra = _finn_dekning_for_punkt(api_key, from_lat, from_lon)
    dekning_til = _finn_dekning_for_punkt(api_key, to_lat, to_lon)
    dekning_kandidater = []
    for dekning in (dekning_fra, dekning_til):
        if dekning and dekning not in dekning_kandidater:
            dekning_kandidater.append(dekning)

    if not dekning_kandidater:
        dekning_kandidater = hent_navitia_dekning(api_key)[:8]

    siste_feil = None
    try:
        for coverage_id in dekning_kandidater:
            reiser, feil = _hent_journeys_for_dekning(api_key, coverage_id, params)
            if feil and feil != siste_feil:
                siste_feil = feil
            if reiser:
                return reiser, None

        if siste_feil:
            return [], siste_feil
        return [], "Ingen kollektivforbindelser funnet for valgt tidspunkt."
    except requests.Timeout:
        return [], "Navitia tok for lang tid — prøv igjen eller bruk eksterne lenker."
    except Exception as e:
        return [], f"Kunne ikke hente reiser: {e}"


def _parse_journey(journey):
    """Gjør Navitia-journey om til et enkelt dict for UI."""
    varighet_s = journey.get("duration", 0)
    timer = varighet_s // 3600
    minutter = (varighet_s % 3600) // 60

    etapper = []
    kart_punkter = []

    for section in journey.get("sections", []):
        etapp = _parse_section(section)
        if etapp:
            etapper.append(etapp)
        coords = _section_coords(section)
        if coords:
            kart_punkter.extend(coords)

    if not kart_punkter:
        kart_punkter = _journey_endepunkter(journey)

    return {
        "varighet_tekst": f"{timer}t {minutter:02d}m" if timer else f"{minutter} min",
        "varighet_sek": varighet_s,
        "antall_bytter": journey.get("nb_transfers", 0),
        "avgang": _format_navitia_tid(journey.get("departure_date_time")),
        "ankomst": _format_navitia_tid(journey.get("arrival_date_time")),
        "etapper": etapper,
        "kart_punkter": kart_punkter,
    }


def _journey_endepunkter(journey):
    """Fallback-linje på kart når Navitia ikke returnerer geojson."""
    punkter = []
    for key in ("from", "to"):
        sted = journey.get(key, {}).get("coord", {})
        lat = sted.get("lat")
        lon = sted.get("lon")
        if lat is not None and lon is not None:
            punkter.append([lat, lon])
    return punkter


def _format_navitia_tid(tid_str):
    if not tid_str or len(tid_str) < 14:
        return "—"
    try:
        dt = datetime.strptime(tid_str[:14], "%Y%m%dT%H%M%S")
        return dt.strftime("%d.%m %H:%M")
    except ValueError:
        return tid_str


def _parse_section(section):
    type_ = section.get("type", "")
    if type_ == "public_transport":
        info = section.get("display_informations", {})
        return {
            "ikon": "🚆",
            "linje": info.get("commercial_mode", "") or info.get("network", ""),
            "navn": info.get("headsign", "") or info.get("label", "Kollektiv"),
            "fra": info.get("departure_stop_point", {}).get("name", ""),
            "til": info.get("arrival_stop_point", {}).get("name", ""),
            "avgang": _format_navitia_tid(section.get("departure_date_time")),
            "ankomst": _format_navitia_tid(section.get("arrival_date_time")),
        }
    if type_ == "street_network":
        mode = section.get("mode", "walking")
        ikon = "🚶" if mode == "walking" else "🚴"
        varighet = section.get("duration", 0) // 60
        return {
            "ikon": ikon,
            "linje": "Gange" if mode == "walking" else mode.capitalize(),
            "navn": f"{varighet} min",
            "fra": "",
            "til": "",
            "avgang": "",
            "ankomst": "",
        }
    if type_ == "waiting":
        varighet = section.get("duration", 0) // 60
        return {
            "ikon": "⏳",
            "linje": "Ventetid",
            "navn": f"{varighet} min",
            "fra": "",
            "til": "",
            "avgang": "",
            "ankomst": "",
        }
    return None


def _section_coords(section):
    geo = section.get("geojson", {})
    if geo.get("type") == "LineString":
        return [[c[1], c[0]] for c in geo.get("coordinates", []) if len(c) >= 2]
    return []


def bygg_eksterne_planleggere(fra_by, fra_land, til_by, til_land, spraak="NO"):
    """Dype lenker til europeiske planleggere (fungerer uten API)."""
    fra_q = quote(f"{fra_by}, {fra_land}")
    til_q = quote(f"{til_by}, {til_land}")
    lenker = {
        "google": (
            f"https://www.google.com/maps/dir/?api=1&origin={fra_q}&destination={til_q}"
            "&travelmode=transit"
        ),
        "omio": f"https://www.omio.com/search/{quote(fra_by)}/{quote(til_by)}",
        "rome2rio": (
            f"https://www.rome2rio.com/map/{quote(fra_by)}/{quote(til_by)}"
        ),
        "trainline": (
            f"https://www.thetrainline.com/search/{quote(fra_by)}/{quote(til_by)}"
        ),
    }
    land_l = (fra_land or "").lower()
    til_land_l = (til_land or "").lower()
    if land_l in {"slovakia", "slovakiet", "slovensko"} or til_land_l in {
        "slovakia",
        "slovakiet",
        "slovensko",
    }:
        lenker["cp_atlas"] = (
            "https://cp.sk/vlakbus/spojenie/"
            f"?f={quote(fra_by)}&t={quote(til_by)}"
        )
    return lenker


def bygg_stedvalg_fra_database(alle_steder, kun_med_koordinater=True):
    """Dict label -> sted for selectbox."""
    valg = {}
    for sted in alle_steder:
        if kun_med_koordinater and (
            sted.get("latitude") is None or sted.get("longitude") is None
        ):
            continue
        label = f"{sted['navn']} — {sted['by']}, {sted['land']}"
        valg[label] = sted
    return valg
