import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import database as _database

LOKALE_SPISESTEDER = _database.LOKALE_SPISESTEDER
SKJULTE_PERLER = _database.SKJULTE_PERLER
SPANIA_MARKEDSDATA = _database.SPANIA_MARKEDSDATA
def _hent_overnatting_kilde():
    """Leser alltid fersk liste fra database.py (unngår tom cache etter filretting)."""
    import importlib

    importlib.reload(_database)
    return list(
        getattr(_database, "UNIKE_OVERNATTING", None)
        or getattr(_database, "UNIKE_HOTELLER", [])
    )


# Bakoverkompatibilitet for «from data_store import UNIKE_HOTELLER»
UNIKE_OVERNATTING = _hent_overnatting_kilde()
UNIKE_HOTELLER = UNIKE_OVERNATTING
from place_quality import filtrer_steder_for_app

# Profilinteresser (UI) → interne DB-kategorier
PROFIL_TIL_DB_TYPE = {
    "Mat & Vin": "gastronomi",
    "Kultur & Historie": "kultur",
    "Natur & Aktivitet": "natur",
    "Golf": "golf",
    "Sport": "sport",
}

DB_PATH = Path(__file__).with_name("hemmelige_europa.sqlite3")

LAND_KODER = {
    "Albania": "AL",
    "Belgia": "BE",
    "Bosnia": "BA",
    "Bulgaria": "BG",
    "Danmark": "DK",
    "Estland": "EE",
    "Finland": "FI",
    "Frankrike": "FR",
    "Irland": "IE",
    "Italia": "IT",
    "Kosovo": "XK",
    "Latvia": "LV",
    "Litauen": "LT",
    "Montenegro": "ME",
    "Nederland": "NL",
    "Nord-Makedonia": "MK",
    "Norge": "NO",
    "Polen": "PL",
    "Portugal": "PT",
    "Romania": "RO",
    "Slovakia": "SK",
    "Spania": "ES",
    "Storbritannia": "GB",
    "Sverige": "SE",
    "Tsjekkia": "CZ",
    "Tyskland": "DE",
    "Ungarn": "HU",
    "Østerrike": "AT",
}


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS places (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                city TEXT NOT NULL,
                country TEXT NOT NULL,
                country_code TEXT,
                category TEXT,
                description TEXT,
                tips TEXT,
                best_time TEXT,
                price TEXT,
                latitude REAL,
                longitude REAL,
                source_type TEXT NOT NULL,
                search_key TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS itinerary_items (
                id TEXT PRIMARY KEY,
                place_id TEXT NOT NULL,
                added_at TEXT NOT NULL,
                snapshot_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS affiliate_clicks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id TEXT,
                place_name TEXT,
                city TEXT,
                country TEXT,
                source_view TEXT NOT NULL,
                language TEXT NOT NULL,
                url TEXT NOT NULL,
                clicked_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_places_country ON places(country)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_places_search ON places(search_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_itinerary_added ON itinerary_items(added_at)")
        conn.commit()


def make_place_id(sted, source_type):
    raw = "|".join(
        [
            source_type,
            sted.get("navn", ""),
            sted.get("by", ""),
            sted.get("land", ""),
        ]
    )
    return raw.lower().replace(" ", "-")


def _forbered_spania_sted(sted):
    """Mapper kuraterte Spania-poster til DB-format med profil_kategori."""
    sted = dict(sted)
    profil_kategori = sted.get("profil_kategori") or sted.get("type", "")
    if profil_kategori in PROFIL_TIL_DB_TYPE:
        sted["profil_kategori"] = profil_kategori
        sted["type"] = PROFIL_TIL_DB_TYPE[profil_kategori]
    return sted


def _spania_skjulte_perler():
    return [
        _forbered_spania_sted(s)
        for s in SPANIA_MARKEDSDATA
        if str(s.get("id", "")).startswith("es_gem")
    ]


def _spania_restauranter():
    return [
        _forbered_spania_sted(s)
        for s in SPANIA_MARKEDSDATA
        if str(s.get("id", "")).startswith("es_rest")
    ]


_PRELOADED_IMAGES_CACHE = None


def _get_preloaded_images():
    global _PRELOADED_IMAGES_CACHE
    if _PRELOADED_IMAGES_CACHE is None:
        path = Path(__file__).with_name("data") / "preloaded_images.json"
        if path.is_file():
            _PRELOADED_IMAGES_CACHE = json.loads(path.read_text(encoding="utf-8"))
        else:
            _PRELOADED_IMAGES_CACHE = {}
    return _PRELOADED_IMAGES_CACHE


def preloaded_image_for_place_id(place_id):
    """Returnerer forhåndslagret bilde-URL for et sted, eller tom streng."""
    if not place_id:
        return ""
    return _get_preloaded_images().get(place_id, "") or ""


def normalize_place(sted, source_type):
    country = sted.get("land", "")
    country_code = sted.get("country_code", LAND_KODER.get(country, ""))
    place = {
        "id": sted.get("id") or make_place_id(sted, source_type),
        "navn": sted.get("navn", ""),
        "by": sted.get("by", ""),
        "land": country,
        "country_code": country_code,
        "type": sted.get("type", ""),
        "profil_kategori": sted.get("profil_kategori", ""),
        "beskrivelse": sted.get("beskrivelse", ""),
        "tips": sted.get("tips", ""),
        "beste_tid": sted.get("beste_tid", ""),
        "pris": sted.get("pris", "€"),
        "latitude": sted.get("latitude"),
        "longitude": sted.get("longitude"),
        "image_url": sted.get("image_url", ""),
        "source_type": source_type,
    }
    if sted.get("saerhetsscore") is not None:
        place["saerhetsscore"] = sted["saerhetsscore"]
    place["search_key"] = " ".join(
        [
            place["navn"],
            place["by"],
            place["land"],
            place["country_code"],
            place["type"],
            place.get("profil_kategori", ""),
            place["source_type"],
        ]
    ).lower()
    if not place["image_url"]:
        forhånd = _get_preloaded_images().get(place["id"])
        if forhånd:
            place["image_url"] = forhånd
    return place


def hent_kuratert_overnatting_db():
    """Kuratert overnatting fra database.py — skal alltid vises i appen."""
    return [normalize_place(p, "hotel") for p in _hent_overnatting_kilde()]


def _perle_lagringsnokkel(navn, by, land):
    return f"{(navn or '').strip().lower()}|{(by or '').strip().lower()}|{(land or '').strip().lower()}"


def _fjern_perle_duplikater_av_kuratert_overnatting(conn, hotell_places):
    """Fjerner feilklassifiserte perler som matcher kuratert overnatting (samme navn+sted)."""
    hotell_nokler = {
        _perle_lagringsnokkel(p["navn"], p["by"], p["land"]) for p in hotell_places
    }
    rows = conn.execute(
        "SELECT id, name, city, country, source_type FROM places WHERE source_type != 'hotel'"
    ).fetchall()
    for row in rows:
        if _perle_lagringsnokkel(row[1], row[2], row[3]) in hotell_nokler:
            conn.execute("DELETE FROM places WHERE id = ?", (row[0],))


def seed_places():
    init_db()
    skjulte_kilde = list(SKJULTE_PERLER) + _spania_skjulte_perler()
    restaurant_kilde = list(LOKALE_SPISESTEDER) + _spania_restauranter()
    hotell_places = [normalize_place(p, "hotel") for p in _hent_overnatting_kilde()]
    places = [normalize_place(p, "hidden_gem") for p in skjulte_kilde]
    places += [normalize_place(p, "restaurant") for p in restaurant_kilde]
    places = filtrer_steder_for_app(places)
    places += hotell_places
    with get_connection() as conn:
        # Fjern utdaterte duplikater fra tidligere «Rostiga Roadtrips»-navn (ny ID ved rename).
        conn.execute(
            "DELETE FROM places WHERE id LIKE '%rostiga-roadtrips%'"
        )
        _fjern_perle_duplikater_av_kuratert_overnatting(conn, hotell_places)
        for place in places:
            conn.execute(
                """
                INSERT OR REPLACE INTO places (
                    id, name, city, country, country_code, category, description,
                    tips, best_time, price, latitude, longitude, source_type,
                    search_key, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    place["id"],
                    place["navn"],
                    place["by"],
                    place["land"],
                    place["country_code"],
                    place["type"],
                    place["beskrivelse"],
                    place["tips"],
                    place["beste_tid"],
                    place["pris"],
                    place["latitude"],
                    place["longitude"],
                    place["source_type"],
                    place["search_key"],
                    json.dumps(place, ensure_ascii=False),
                ),
            )
        conn.commit()


def _row_to_place(row):
    place = {
        "id": row[0],
        "navn": row[1],
        "by": row[2],
        "land": row[3],
        "country_code": row[4],
        "type": row[5],
        "beskrivelse": row[6],
        "tips": row[7],
        "beste_tid": row[8],
        "pris": row[9],
        "latitude": row[10],
        "longitude": row[11],
        "source_type": row[12],
        "search_key": row[13],
        "profil_kategori": "",
        "image_url": "",
    }
    if len(row) > 14 and row[14]:
        try:
            raw = json.loads(row[14])
            place["profil_kategori"] = raw.get("profil_kategori", "") or ""
            place["image_url"] = raw.get("image_url", "") or ""
            if raw.get("saerhetsscore") is not None:
                place["saerhetsscore"] = raw["saerhetsscore"]
        except (json.JSONDecodeError, TypeError):
            pass
    return place


def get_places(source_type=None, *, seed=True):
    if seed:
        seed_places()
    sql = """
        SELECT id, name, city, country, country_code, category, description,
               tips, best_time, price, latitude, longitude, source_type, search_key,
               raw_json
        FROM places
    """
    params = []
    if source_type:
        sql += " WHERE source_type = ?"
        params.append(source_type)
    sql += " ORDER BY country, city, name"
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_place(row) for row in rows]


def add_itinerary_item(place):
    init_db()
    added_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO itinerary_items (id, place_id, added_at, snapshot_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                place["id"],
                place["id"],
                added_at,
                json.dumps(place, ensure_ascii=False),
            ),
        )
        conn.commit()


def get_itinerary_items():
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT snapshot_json FROM itinerary_items ORDER BY added_at"
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def remove_itinerary_item(place_id):
    init_db()
    with get_connection() as conn:
        conn.execute("DELETE FROM itinerary_items WHERE id = ?", (place_id,))
        conn.commit()


def log_affiliate_click(place, source_view, language, url):
    init_db()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO affiliate_clicks (
                place_id, place_name, city, country, source_view, language, url, clicked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                place.get("id"),
                place.get("navn"),
                place.get("by"),
                place.get("land"),
                source_view,
                language,
                url,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

