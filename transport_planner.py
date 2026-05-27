"""Eksterne reiseplanleggere og stedvalg for transportfanen."""

from urllib.parse import quote


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
