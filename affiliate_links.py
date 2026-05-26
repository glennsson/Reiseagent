"""Bygger affiliate-URL-er for hotell, leiebil og matlevering."""

from urllib.parse import quote, urlencode

LAND_TIL_CC = {
    "spania": "ES",
    "norge": "NO",
    "sverige": "SE",
    "danmark": "DK",
    "portugal": "PT",
    "italia": "IT",
    "frankrike": "FR",
    "tyskland": "DE",
    "storbritannia": "GB",
    "irland": "IE",
}


def _ren_by(by):
    """Fjerner region-deler som 'Ronda/Cádiz' → 'Ronda'."""
    return (by or "").split("/")[0].split(",")[0].strip()


def _country_code(land, country_code=""):
    if country_code:
        return country_code.upper()
    return LAND_TIL_CC.get((land or "").lower(), "")


def bygg_booking_url(by, land, booking_aid="888888"):
    query = urlencode({"ss": f"{_ren_by(by)}, {land}", "aid": booking_aid})
    return f"https://www.booking.com/searchresults.html?{query}"


def bygg_leiebil_url(by, land, booking_aid="888888", country_code=""):
    """Leiebil via Booking.com Cars (samme AID som hotell)."""
    sted = f"{_ren_by(by)}, {land}"
    query = urlencode(
        {
            "aid": booking_aid,
            "adplat": "website",
            "label": "hemmelige-europa",
            "location": sted,
        }
    )
    return f"https://www.booking.com/cars/index.html?{query}"


def bygg_matlevering_url(
    by,
    land,
    country_code="",
    spraak="NO",
    glovo_affiliate_url="",
    wolt_affiliate_url="",
    ubereats_affiliate_url="",
):
    """
    Matlevering tilpasset marked: Glovo (Spania), Wolt (Norden), Uber Eats som fallback.
    Valgfrie secrets overstyrer standard-landingssider med egne affiliate-lenker.
    """
    city = quote(_ren_by(by))
    cc = _country_code(land, country_code)
    land_l = (land or "").lower()

    if glovo_affiliate_url and (cc == "ES" or land_l == "spania"):
        return glovo_affiliate_url.replace("{by}", _ren_by(by)).replace("{city}", _ren_by(by))

    if wolt_affiliate_url and (cc in {"NO", "SE", "DK", "FI"} or land_l in {"norge", "sverige", "danmark", "finland"}):
        return wolt_affiliate_url.replace("{by}", _ren_by(by)).replace("{city}", _ren_by(by))

    if ubereats_affiliate_url:
        return ubereats_affiliate_url.replace("{by}", _ren_by(by)).replace("{city}", _ren_by(by))

    if cc == "ES" or land_l == "spania":
        return f"https://glovoapp.com/en/search/?query={city}"

    if cc == "NO" or land_l == "norge":
        return f"https://wolt.com/en/search?q={city}"

    if cc in {"SE", "DK"} or land_l in {"sverige", "danmark"}:
        return f"https://wolt.com/en/search?q={city}"

    locale = "no" if spraak == "NO" else "en"
    region = cc.lower() if cc else "es"
    return f"https://www.ubereats.com/{locale}-{region}/search?q={city}"
