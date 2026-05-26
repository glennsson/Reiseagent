import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from database import LOKALE_SPISESTEDER, SKJULTE_PERLER

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


def normalize_place(sted, source_type):
    country = sted.get("land", "")
    country_code = sted.get("country_code", LAND_KODER.get(country, ""))
    place = {
        "id": make_place_id(sted, source_type),
        "navn": sted.get("navn", ""),
        "by": sted.get("by", ""),
        "land": country,
        "country_code": country_code,
        "type": sted.get("type", ""),
        "beskrivelse": sted.get("beskrivelse", ""),
        "tips": sted.get("tips", ""),
        "beste_tid": sted.get("beste_tid", ""),
        "pris": sted.get("pris", "€"),
        "latitude": sted.get("latitude"),
        "longitude": sted.get("longitude"),
        "source_type": source_type,
    }
    place["search_key"] = " ".join(
        [
            place["navn"],
            place["by"],
            place["land"],
            place["country_code"],
            place["type"],
            place["source_type"],
        ]
    ).lower()
    return place


def seed_places():
    init_db()
    places = [normalize_place(p, "hidden_gem") for p in SKJULTE_PERLER]
    places += [normalize_place(p, "restaurant") for p in LOKALE_SPISESTEDER]
    with get_connection() as conn:
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
    return {
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
    }


def get_places(source_type=None):
    seed_places()
    sql = """
        SELECT id, name, city, country, country_code, category, description,
               tips, best_time, price, latitude, longitude, source_type, search_key
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
        "latest_click": latest_click[0] if latest_click else None,
    }
