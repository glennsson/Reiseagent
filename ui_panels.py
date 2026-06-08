"""UI-paneler utskilt fra app.py for enklere vedlikehold."""

import streamlit as st

from ui_cards import vis_tom_tilstand


def _render_ki_kandidat_kort(
    kandidat,
    idx,
    *,
    tr,
    vis_sted_foto,
    vis_travel_card,
    sted_tittel_fn,
    sted_emoji_fn,
    berik_kandidat_fra_db,
    kandidat_lagringsstatus,
    render_reiseplan_knapp_agent,
    chat_lagre_tekster,
    lagre_agent_perle_i_db,
    legg_lagret_sted_i_lokale_lister,
):
    visning = dict(kandidat)
    if berik_kandidat_fra_db:
        visning = berik_kandidat_fra_db(visning)
    if not visning.get("id"):
        visning["id"] = visning.get("agent_id", f"sank_{idx}")
    vis_sted_foto(
        visning,
        key_suffix=f"sank_{idx}_{visning.get('agent_id', idx)}",
        autoload=True,
    )
    tittel = sted_tittel_fn(visning, sted_emoji_fn(visning))
    vis_travel_card(visning, tittel)
    if kandidat.get("latitude") is None or kandidat.get("longitude") is None:
        st.caption(tr("sank_uten_koordinater"))
    lagringsstatus = kandidat.get("lagringsstatus") or kandidat_lagringsstatus(
        kandidat
    )
    if lagringsstatus.get("melding_nokkel") and (
        lagringsstatus.get("allerede_synlig") or lagringsstatus.get("erstatter")
    ):
        st.caption(tr(lagringsstatus["melding_nokkel"]))
    col_plan, col_db = st.columns([1, 1])
    with col_plan:
        render_reiseplan_knapp_agent(
            kandidat, f"sank_{idx}_{kandidat['agent_id']}"
        )
    with col_db:
        if lagringsstatus.get("allerede_synlig"):
            pass
        elif st.button(
            chat_lagre_tekster(kandidat)[0],
            key=f"sank_save_{idx}_{kandidat['agent_id']}",
            use_container_width=True,
        ):
            lagret = lagre_agent_perle_i_db(kandidat)
            legg_lagret_sted_i_lokale_lister(lagret)
            rest = st.session_state.get("sank_kandidater", [])
            st.session_state["sank_kandidater"] = [
                k for k in rest if k.get("agent_id") != kandidat.get("agent_id")
            ]
            st.toast(chat_lagre_tekster(kandidat)[1])
            st.rerun()


def render_sank_ki_panel(
    *,
    tr,
    min_unikhetsgrad,
    sanke_perler_for_omrade,
    lagre_agent_perle_i_db,
    legg_lagret_sted_i_lokale_lister,
    kandidat_lagringsstatus,
    render_reiseplan_knapp_agent,
    chat_lagre_tekster,
    vis_sted_foto,
    berik_kandidat_fra_db=None,
    vis_travel_card,
    sted_tittel_fn,
    sted_emoji_fn,
):
    """KI-søk etter nye oppdagelser — egen fane, ikke inne i reiseekspert."""
    with st.form("sank_perler_form"):
        sank_omrade = st.text_input(
            tr("sank_omrade"),
            placeholder=tr("sank_omrade_ph"),
            key="sank_omrade_input",
        )
        sank_antall = st.slider(
            tr("sank_antall"),
            min_value=3,
            max_value=20,
            value=8,
            key="sank_antall_input",
        )
        st.caption(
            tr("sank_min_score_fast").format(min_unikhetsgrad)
            + " "
            + tr("hotell_min_unikhet").format(min_unikhetsgrad)
            + " "
            + tr("mat_min_unikhet").format(min_unikhetsgrad)
        )
        sank_streng = st.checkbox(
            tr("sank_strict_mode"),
            value=False,
            key="sank_strict_mode_input",
        )
        start_sank = st.form_submit_button(
            tr("sank_knapp"), type="primary", use_container_width=True
        )

    sank_kandidater = list(st.session_state.get("sank_kandidater") or [])
    sank_rapport = st.session_state.get("sank_rapport")

    if start_sank:
        omrade = (sank_omrade or "").strip()
        if not omrade:
            st.error(tr("sank_feil_omrade"))
        else:
            st.session_state.pop("sank_kandidater", None)
            st.session_state.pop("sank_rapport", None)
            try:
                with st.spinner(tr("sank_spinner").format(omrade)):
                    kandidater, rapport = sanke_perler_for_omrade(
                        omrade,
                        sank_antall,
                        strict_mode=sank_streng,
                    )
                st.session_state["sank_kandidater"] = kandidater
                st.session_state["sank_rapport"] = rapport
                st.session_state["sank_omrade"] = omrade
                sank_kandidater = list(kandidater or [])
                sank_rapport = rapport
            except Exception as e:
                st.error(tr("sank_feil_generell").format(str(e)))
    if sank_rapport:
        st.caption(
            tr("sank_rapport").format(
                sank_rapport.get("foreslaatt", 0),
                sank_rapport.get("godkjent", 0),
                sank_rapport.get("forkastet_duplikat", 0),
                sank_rapport.get("forkastet_score", 0),
                sank_rapport.get("forkastet_geo", 0),
                sank_rapport.get("forkastet_normalisering", 0),
            )
        )
        if st.session_state.get("vis_perf_debug"):
            st.caption(
                tr("perf_sank").format(
                    sank_rapport.get("tid_geo_s", 0),
                    sank_rapport.get("tid_total_s", 0),
                )
            )

    if sank_kandidater:
        sortering = st.selectbox(
            tr("sank_sorter"),
            [
                tr("sank_sorter_score_desc"),
                tr("sank_sorter_score_asc"),
                tr("sank_sorter_navn"),
            ],
            key="sank_sortering",
        )
        if sortering == tr("sank_sorter_score_desc"):
            visningsliste = sorted(
                sank_kandidater,
                key=lambda k: k.get("saerhetsscore", 0),
                reverse=True,
            )
        elif sortering == tr("sank_sorter_score_asc"):
            visningsliste = sorted(
                sank_kandidater,
                key=lambda k: k.get("saerhetsscore", 0),
            )
        else:
            visningsliste = sorted(
                sank_kandidater,
                key=lambda k: (k.get("navn") or "").lower(),
            )

        if st.button(tr("sank_lagre_alle"), use_container_width=True, key="sank_save_all"):
            lagret_antall = 0
            for kandidat in sank_kandidater:
                if kandidat.get("allerede_i_db"):
                    continue
                lagret = lagre_agent_perle_i_db(kandidat)
                legg_lagret_sted_i_lokale_lister(lagret)
                lagret_antall += 1
            st.session_state["sank_kandidater"] = []
            if lagret_antall:
                st.session_state["sank_rapport"] = None
            st.success(tr("sank_lagret_alle").format(lagret_antall))
            st.rerun()

        for i in range(0, len(visningsliste), 2):
            cols = st.columns(2)
            for j in range(2):
                if i + j >= len(visningsliste):
                    continue
                kandidat = visningsliste[i + j]
                idx = i + j
                with cols[j]:
                    _render_ki_kandidat_kort(
                        kandidat,
                        idx,
                        tr=tr,
                        vis_sted_foto=vis_sted_foto,
                        vis_travel_card=vis_travel_card,
                        sted_tittel_fn=sted_tittel_fn,
                        sted_emoji_fn=sted_emoji_fn,
                        berik_kandidat_fra_db=berik_kandidat_fra_db,
                        kandidat_lagringsstatus=kandidat_lagringsstatus,
                        render_reiseplan_knapp_agent=render_reiseplan_knapp_agent,
                        chat_lagre_tekster=chat_lagre_tekster,
                        lagre_agent_perle_i_db=lagre_agent_perle_i_db,
                        legg_lagret_sted_i_lokale_lister=legg_lagret_sted_i_lokale_lister,
                    )
    elif sank_rapport and sank_rapport.get("godkjent", 0) == 0:
        if sank_rapport.get("foreslaatt", 0) == 0:
            vis_tom_tilstand("✨", tr("sank_ingen_tom_tittel"), tr("sank_ingen_tom"))
        else:
            vis_tom_tilstand(
                "🔍",
                tr("sank_ingen_tittel"),
                tr("sank_ingen")
                + " "
                + tr("sank_ingen_detalj").format(
                    sank_rapport.get("forkastet_score", 0),
                    sank_rapport.get("forkastet_duplikat", 0),
                    sank_rapport.get("forkastet_normalisering", 0),
                ),
            )
