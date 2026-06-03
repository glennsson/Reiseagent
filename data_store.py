import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from database import LOKALE_SPISESTEDER, SKJULTE_PERLER, SPANIA_MARKEDSDATA

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


def seed_places():
    init_db()
    skjulte_kilde = list(SKJULTE_PERLER) + _spania_skjulte_perler()
    restaurant_kilde = list(LOKALE_SPISESTEDER) + _spania_restauranter()
    places = [normalize_place(p, "hidden_gem") for p in skjulte_kilde]
    places += [normalize_place(p, "restaurant") for p in restaurant_kilde]
    with get_connection() as conn:
        # Fjern utdaterte duplikater fra tidligere «Rostiga Roadtrips»-navn (ny ID ved rename).
        conn.execute(
            "DELETE FROM places WHERE id LIKE '%rostiga-roadtrips%'"
        )
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
        except (json.JSONDecodeError, TypeError):
            pass
    return place


def get_places(source_type=None):
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



def get_affiliate_stats(limit=5):
    init_db()
    with get_connection() as conn:
        total_clicks = conn.execute("SELECT COUNT(*) FROM affiliate_clicks").fetchone()[0]
        top_places = conn.execute(
            """
            SELECT place_name, city, country, COUNT(*) as clicks
            FROM affiliate_clicks
            GROUP BY place_id, place_name, city, country
            ORDER BY clicks DESC, place_name
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        top_sources = conn.execute(
            """
            SELECT source_view, COUNT(*) as clicks
            FROM affiliate_clicks
            GROUP BY source_view
            ORDER BY clicks DESC, source_view
            """
        ).fetchall()
        top_partners = conn.execute(
            """
            SELECT
                CASE
                    WHEN instr(source_view, ':') > 0
                    THEN substr(source_view, instr(source_view, ':') + 1)
                    ELSE 'booking'
                END AS partner,
                COUNT(*) AS clicks
            FROM affiliate_clicks
            GROUP BY partner
            ORDER BY clicks DESC, partner
            """
        ).fetchall()
        latest_click = conn.execute(
            "SELECT clicked_at FROM affiliate_clicks ORDER BY clicked_at DESC LIMIT 1"
        ).fetchone()

    return {
        "total_clicks": total_clicks,
        "top_places": [
            {
                "place_name": row[0],
                "city": row[1],
                "country": row[2],
                "clicks": row[3],
            }
            for row in top_places
        ],
        "top_sources": [
            {
                "source_view": row[0],
                "clicks": row[1],
            }
            for row in top_sources
        ],
        "top_partners": [
            {"partner": row[0], "clicks": row[1]}
            for row in top_partners
        ],
        "latest_click": latest_click[0] if latest_click else None,
    }
