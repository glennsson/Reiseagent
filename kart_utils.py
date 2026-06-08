"""Folium-kart, avstandsberegning og geografisk sortering."""

import html
import math

import folium

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


def regn_ut_avstand_km(lat1, lon1, lat2, lon2):
    """Regner ut avstanden i kilometer mellom to GPS-koordinater."""
    R = 6371.0
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


def filtrer_data(data):
    """Filtrerer bort steder uten gyldige koordinater."""
    return [
        d
        for d in data
        if d.get("latitude") is not None and d.get("longitude") is not None
    ]


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


def lag_sted_kart_popup(sted):
    """HTML-popup med navn, sted og bilde hvis tilgjengelig."""
    navn = html.escape(sted.get("navn", ""))
    by = html.escape(sted.get("by", ""))
    land = html.escape(sted.get("land", ""))
    kategori = html.escape(
        sted.get("profil_kategori") or sted.get("type") or sted.get("source_type", "")
    )
    bilde_html = ""
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


def lag_chat_oppdag_kart(lat, lon, alle_steder, sentrum_navn="", radius_km=50):
    """Chat-kart med blått søkepunkt og nærliggende steder innen radius."""
    m = _nytt_folium_kart([lat, lon], zoom_start=9)
    navn = html.escape(sentrum_navn or "Søkepunkt")
    folium.Marker(
        location=[lat, lon],
        tooltip=sentrum_navn or "Søkepunkt",
        popup=f"<b>{navn}</b>",
        icon=_lag_modern_kart_div_icon(KART_SENTRUM_FARGE, variant="sentrum"),
    ).add_to(m)

    for sted in alle_steder:
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
