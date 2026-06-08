"""Persistent lagring av profil, chat og reiseplan (JSON)."""

import json
import os
import streamlit as st

PROFIL_FIL = "reiseprofil.json"

STANDARD_PROFIL = {
    "reise_folge": "Par",
    "budsjett": "Medium",
}

PROFIL_REISE_FOLGE = ["Singel", "Par", "Familie med barn"]
PROFIL_BUDSJETT = ["Budsjett", "Medium", "Luksus"]


def normaliser_profil(profil):
    """Sikrer at profil-dict har gyldige felt."""
    if not isinstance(profil, dict):
        return dict(STANDARD_PROFIL)

    def _sikker(verdi, alternativer, standard):
        return verdi if verdi in alternativer else standard

    return {
        "reise_folge": _sikker(
            profil.get("reise_folge"), PROFIL_REISE_FOLGE, STANDARD_PROFIL["reise_folge"]
        ),
        "budsjett": _sikker(
            profil.get("budsjett"), PROFIL_BUDSJETT, STANDARD_PROFIL["budsjett"]
        ),
    }


def last_inn_data():
    """Henter lagret data fra fil hvis den eksisterer."""
    default_data = {
        "reise_chat": [],
        "profil": dict(STANDARD_PROFIL),
        "reiseplan": [],
    }
    if os.path.exists(PROFIL_FIL):
        try:
            with open(PROFIL_FIL, "r", encoding="utf-8") as f:
                innhold = f.read().strip()
                if not innhold:
                    return default_data
                lagret = json.loads(innhold)
                if not isinstance(lagret, dict):
                    return default_data
                if "reise_chat" not in lagret or not isinstance(lagret["reise_chat"], list):
                    lagret["reise_chat"] = []
                if "reiseplan" not in lagret or not isinstance(lagret["reiseplan"], list):
                    lagret["reiseplan"] = []
                lagret["profil"] = normaliser_profil(lagret.get("profil"))
                return lagret
        except Exception:
            return default_data
    return default_data


def lagre_data(chat, profil=None, reiseplan=None):
    """Lagrer chatlogg, reiseprofil og reiseplan til JSON."""
    try:
        profil_a_lagre = normaliser_profil(
            profil if profil is not None else st.session_state.get("profil", STANDARD_PROFIL)
        )
        if reiseplan is None:
            reiseplan = st.session_state.get("reiseplan", [])
        data_til_lagring = {
            "reise_chat": chat if isinstance(chat, list) else [],
            "profil": profil_a_lagre,
            "reiseplan": reiseplan if isinstance(reiseplan, list) else [],
        }
        with open(PROFIL_FIL, "w", encoding="utf-8") as f:
            json.dump(data_til_lagring, f, ensure_ascii=False, indent=4)
    except Exception as e:
        st.error(f"Kunne ikke lagre data: {e}")


def sync_reiseplan_to_sqlite(items):
    """Gjenoppretter SQLite-reiseplan fra JSON ved oppstart."""
    from data_store import get_connection, init_db

    if not items:
        return
    init_db()
    with get_connection() as conn:
        eksisterende = {
            row[0]
            for row in conn.execute("SELECT id FROM itinerary_items").fetchall()
        }
        for place in items:
            if not isinstance(place, dict) or not place.get("id"):
                continue
            if place["id"] in eksisterende:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO itinerary_items (id, place_id, added_at, snapshot_json)
                VALUES (?, ?, datetime('now'), ?)
                """,
                (
                    place["id"],
                    place["id"],
                    json.dumps(place, ensure_ascii=False),
                ),
            )
        conn.commit()


def export_reiseplan_from_sqlite():
    """Leser reiseplan fra SQLite for JSON-lagring."""
    from data_store import get_itinerary_items

    return get_itinerary_items()


def persist_reiseplan():
    """Lagrer gjeldende reiseplan til JSON."""
    items = export_reiseplan_from_sqlite()
    st.session_state.reiseplan = items
    lagre_data(
        st.session_state.get("reise_chat", []),
        st.session_state.get("profil"),
        items,
    )
