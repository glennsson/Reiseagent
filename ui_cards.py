"""Stedskort, bilder og affiliate-lenker."""

import html
import os

import streamlit as st
import streamlit.components.v1 as components

from affiliate_links import bygg_booking_url, bygg_leiebil_url, bygg_matlevering_url
from data_store import log_affiliate_click, preloaded_image_for_place_id
from kart_utils import kart_markor_farge


LAND_FLAGG = {
    "Norge": "🇳🇴",
    "Sverige": "🇸🇪",
    "Danmark": "🇩🇰",
    "Finland": "🇫🇮",
    "Island": "🇮🇸",
    "Tyskland": "🇩🇪",
    "Frankrike": "🇫🇷",
    "Spania": "🇪🇸",
    "Italia": "🇮🇹",
    "Portugal": "🇵🇹",
    "Storbritannia": "🇬🇧",
    "Irland": "🇮🇪",
    "Nederland": "🇳🇱",
    "Belgia": "🇧🇪",
    "Østerrike": "🇦🇹",
    "Sveits": "🇨🇭",
    "Polen": "🇵🇱",
    "Tsjekkia": "🇨🇿",
    "Slovakia": "🇸🇰",
    "Ungarn": "🇭🇺",
    "Romania": "🇷🇴",
    "Bulgaria": "🇧🇬",
    "Hellas": "🇬🇷",
    "Kroatia": "🇭🇷",
    "Slovenia": "🇸🇮",
    "Estland": "🇪🇪",
    "Latvia": "🇱🇻",
    "Litauen": "🇱🇹",
    "Åland": "🇫🇮",
}


def hent_land_flagg(land, country_code=""):
    cc = (country_code or "").strip().upper()
    if len(cc) == 2 and cc.isalpha():
        return "".join(chr(127397 + ord(c)) for c in cc)
    return LAND_FLAGG.get((land or "").strip(), "🌍")


def berik_sted_bilde(sted):
    if (sted.get("image_url") or "").strip():
        return sted
    forhånd = preloaded_image_for_place_id(sted.get("id", ""))
    if forhånd:
        return {**sted, "image_url": forhånd}
    return sted


def hent_affiliate_konfig():
    def _secret(key, default=""):
        try:
            verdi = st.secrets.get(key, default)
            return verdi if verdi else os.environ.get(key, default)
        except Exception:
            return os.environ.get(key, default)

    return {
        "booking_aid": _secret("BOOKING_AID", "888888"),
        "glovo": _secret("GLOVO_AFFILIATE_URL", ""),
        "wolt": _secret("WOLT_AFFILIATE_URL", ""),
        "ubereats": _secret("UBEREATS_AFFILIATE_URL", ""),
    }


def behandle_affiliate_pending(spraak):
    pending = st.session_state.pop("affiliate_pending", None)
    if not pending:
        return
    sted, source_view, url = pending
    log_affiliate_click(sted, source_view, spraak, url)
    safe_url = html.escape(url, quote=True)
    components.html(
        f'<script>window.open("{safe_url}", "_blank");</script>',
        height=0,
    )


def render_affiliate_lenker(sted, source_view, index, tr, effektiv_kilde_type, spraak):
    if not sted.get("by") or not sted.get("land"):
        return

    kilde = effektiv_kilde_type(sted)
    cfg = hent_affiliate_konfig()
    by, land = sted["by"], sted["land"]
    cc = sted.get("country_code", "")
    lenker = []

    if kilde == "hotel":
        lenker.append(
            (
                "booking",
                tr("affiliate_booking"),
                bygg_booking_url(by, land, cfg["booking_aid"]),
            )
        )
        lenker.append(
            (
                "leiebil",
                tr("affiliate_leiebil"),
                bygg_leiebil_url(by, land, cfg["booking_aid"], cc),
            )
        )
    elif kilde == "restaurant":
        lenker.append(
            (
                "mat",
                tr("affiliate_mat"),
                bygg_matlevering_url(
                    by,
                    land,
                    cc,
                    spraak,
                    cfg["glovo"],
                    cfg["wolt"],
                    cfg["ubereats"],
                ),
            )
        )

    if not lenker:
        return

    cols = st.columns(len(lenker))
    idx_del = f"_{index}" if index is not None else ""
    for col, (slug, tekst, url) in zip(cols, lenker):
        with col:
            if st.button(
                tekst,
                key=f"aff_{source_view}_{sted.get('id', 'x')}{idx_del}_{slug}",
                use_container_width=True,
            ):
                st.session_state["affiliate_pending"] = (sted, source_view, url)
                st.rerun()


def _unikhet_badge_html(sted, tr):
    score = sted.get("saerhetsscore")
    if score is None or score <= 0:
        return ""
    nivå = "hoy" if score >= 8 else "middels" if score >= 6 else "lav"
    etikett = tr("unikhet_badge").format(int(score))
    return (
        f'<span class="unikhet-badge unikhet-{nivå}" '
        f'title="{html.escape(etikett)}">{int(score)}/10</span>'
    )


def render_travel_card_html(sted, tittel, tr, vis_sted_type_fn, pris_tekst=None):
    meta = f"📍 {html.escape(sted.get('by', ''))}, {html.escape(sted.get('land', ''))}"
    type_vis = vis_sted_type_fn(sted)
    if type_vis:
        meta += f" · {html.escape(type_vis)}"

    extras = []
    if pris_tekst:
        extras.append(f'<p class="travel-card-price">{html.escape(pris_tekst)}</p>')
    if sted.get("tips"):
        extras.append(f'<p class="travel-tip">💡 {html.escape(sted["tips"])}</p>')
    if sted.get("beste_tid"):
        extras.append(
            f'<p class="travel-card-time">{html.escape(tr("perler_beste_tid").format(sted["beste_tid"]))}</p>'
        )
    extra_html = "\n".join(extras)
    kant_farge = kart_markor_farge(sted)
    badge = _unikhet_badge_html(sted, tr)

    return f"""
<div class="travel-card" style="border-left: 4px solid {html.escape(kant_farge)};">
    <div class="travel-card-head">
        <h3 class="travel-card-title">{html.escape(tittel)}</h3>
        {badge}
    </div>
    <p class="travel-card-meta">{meta}</p>
    <p class="travel-card-desc">{html.escape(sted.get("beskrivelse", ""))}</p>
    {extra_html}
</div>
"""


def render_hero_html(antall_oppdagelser, antall_land, tr):
    return f"""
<div class="he-hero">
    <p class="he-hero-tagline">{html.escape(tr("hero_tagline"))}</p>
    <div class="he-hero-stats">
        <span class="he-hero-stat">
            <strong>{antall_oppdagelser}</strong>
            {html.escape(tr("hero_stat_oppdagelser"))}
        </span>
        <span class="he-hero-dot">·</span>
        <span class="he-hero-stat">
            <strong>{antall_land}</strong>
            {html.escape(tr("hero_stat_land"))}
        </span>
    </div>
</div>
"""


def render_land_kort_html(land, antall_tekst, eksempel_navn, country_code=""):
    flagg = hent_land_flagg(land, country_code)
    eksempel = html.escape(eksempel_navn or "")
    return f"""
<div class="he-land-kort">
    <span class="he-land-flagg">{flagg}</span>
    <div class="he-land-innhold">
        <strong class="he-land-navn">{html.escape(land)}</strong>
        <span class="he-land-antall">{html.escape(antall_tekst)}</span>
        {f'<span class="he-land-eksempel">{eksempel}</span>' if eksempel else ''}
    </div>
</div>
"""


def render_tom_tilstand_html(emoji, tittel, tekst):
    return f"""
<div class="he-empty">
    <span class="he-empty-emoji">{html.escape(emoji)}</span>
    <p class="he-empty-tittel">{html.escape(tittel)}</p>
    <p class="he-empty-tekst">{html.escape(tekst)}</p>
</div>
"""


def vis_tom_tilstand(emoji, tittel, tekst):
    st.markdown(render_tom_tilstand_html(emoji, tittel, tekst), unsafe_allow_html=True)


def vis_sted_foto(sted, key_suffix, tr, hent_bilde_fn, *, autoload=False):
    sted = berik_sted_bilde(sted)
    suffix = key_suffix or sted.get("id", "")
    bilde_url = (sted.get("image_url") or "").strip()

    if not bilde_url:
        last_nokkel = f"foto_last_{suffix}"
        skal_hente = autoload or st.session_state.get("bilde_autoload_wiki", False)
        if not skal_hente:
            if not st.session_state.get(last_nokkel):
                if st.button(
                    tr("bilde_vis_knapp"),
                    key=f"foto_btn_{suffix}",
                    use_container_width=True,
                ):
                    st.session_state[last_nokkel] = True
                    st.rerun()
                return
        bilde_url = hent_bilde_fn(sted)

    if bilde_url:
        st.image(bilde_url, use_container_width=True)
        st.caption(tr("bilde_kilde").format(sted.get("navn", "")))
    else:
        st.caption(tr("bilde_ingen"))


def injiser_mork_modus_css():
    st.markdown(
        """
<style>
:root {
    --gemini-bg: #0B0F19;
    --gemini-surface: #131722;
    --gemini-surface-2: #1E2430;
    --gemini-text: #E8EAED;
    --gemini-text-secondary: #DADCE0;
    --gemini-muted: #BDC1C6;
    --gemini-border: #2D3544;
    --gemini-shadow: 0 16px 36px rgba(0, 0, 0, 0.32);
}
</style>
""",
        unsafe_allow_html=True,
    )
