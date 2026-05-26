"""Henter stedsbilder fra Wikipedia/Wikimedia (gratis, med attribusjon på kilde)."""

import requests

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "HemmeligeEuropa/1.0 (educational travel app)"

# Fallback per profilkategori når ingen treff (Wikimedia Commons)
KATEGORI_FALLBACK_BILDE = {
    "gastronomi": "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/Taberna_en_Sevilla.jpg/640px-Taberna_en_Sevilla.jpg",
    "kultur": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/Europeana_138_-_Voyage_sur_le_Rhin.jpg/640px-Europeana_138_-_Voyage_sur_le_Rhin.jpg",
    "natur": "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3f/Forest_-_European_landscape.jpg/640px-Forest_-_European_landscape.jpg",
    "golf": "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8f/Golf_course_in_Finland.jpg/640px-Golf_course_in_Finland.jpg",
    "historie": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/Europeana_138_-_Voyage_sur_le_Rhin.jpg/640px-Europeana_138_-_Voyage_sur_le_Rhin.jpg",
    "museum": "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4e/Europeana_138_-_Voyage_sur_le_Rhin.jpg/640px-Europeana_138_-_Voyage_sur_le_Rhin.jpg",
}


def _wiki_get(params):
    try:
        response = requests.get(
            WIKI_API,
            params={**params, "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=6,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def _thumbnail_fra_sider(page_ids):
    if not page_ids:
        return None
    data = _wiki_get(
        {
            "action": "query",
            "pageids": "|".join(str(pid) for pid in page_ids),
            "prop": "pageimages",
            "piprop": "thumbnail",
            "pithumbsize": 640,
        }
    )
    if not data:
        return None
    for page in data.get("query", {}).get("pages", {}).values():
        thumb = page.get("thumbnail", {}).get("source")
        if thumb:
            return thumb
    return None


def _sok_wikipedia_bilde(soketerm):
    if not soketerm or not soketerm.strip():
        return None
    data = _wiki_get(
        {
            "action": "query",
            "generator": "search",
            "gsrsearch": soketerm.strip(),
            "gsrlimit": 3,
            "prop": "pageimages",
            "piprop": "thumbnail",
            "pithumbsize": 640,
        }
    )
    if not data:
        return None
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        thumb = page.get("thumbnail", {}).get("source")
        if thumb:
            return thumb
    return None


def _geo_wikipedia_bilde(latitude, longitude):
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None

    data = _wiki_get(
        {
            "action": "query",
            "list": "geosearch",
            "gscoord": f"{lat}|{lon}",
            "gsradius": 8000,
            "gslimit": 5,
        }
    )
    if not data:
        return None
    page_ids = [item["pageid"] for item in data.get("query", {}).get("geosearch", [])]
    return _thumbnail_fra_sider(page_ids)


def hent_sted_bilde_url(sted):
    """
    Returnerer URL til stedsbilde.
    Prioritet: image_url i data → Wikipedia-søk → geosøk → kategori-fallback.
    """
    if not sted:
        return None

    eksplisitt = sted.get("image_url")
    if eksplisitt:
        return eksplisitt

    navn = (sted.get("navn") or "").strip()
    by = (sted.get("by") or "").strip()
    land = (sted.get("land") or "").strip()
    sted_type = (sted.get("type") or "").lower()

    sokerekker = []
    if navn and by and land:
        sokerekker.append(f"{navn} {by} {land}")
    if navn and land:
        sokerekker.append(f"{navn} {land}")
    if navn and by:
        sokerekker.append(f"{navn} {by}")
    if navn:
        sokerekker.append(navn)

    for sok in sokerekker:
        bilde = _sok_wikipedia_bilde(sok)
        if bilde:
            return bilde

    lat = sted.get("latitude")
    lon = sted.get("longitude")
    if lat is not None and lon is not None:
        bilde = _geo_wikipedia_bilde(lat, lon)
        if bilde:
            return bilde

    profil_kat = sted.get("profil_kategori", "")
    profil_fallback = {
        "Mat & Vin": "gastronomi",
        "Kultur & Historie": "kultur",
        "Natur & Aktivitet": "natur",
        "Golf": "golf",
    }
    kategori = profil_fallback.get(profil_kat, sted_type)
    return KATEGORI_FALLBACK_BILDE.get(kategori) or KATEGORI_FALLBACK_BILDE.get("kultur")
