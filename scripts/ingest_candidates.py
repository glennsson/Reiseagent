import argparse
import json
import math
import re
import time
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_store import get_connection, init_db, normalize_place

USER_AGENT = "HemmeligeEuropaIngest/1.0"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]


def _request_with_retries(method: str, url: str, *, retries: int = 3, timeout: int = 60, **kwargs):
    last_err = None
    for attempt in range(retries):
        try:
            return requests.request(
                method,
                url,
                timeout=timeout,
                headers={**{"User-Agent": USER_AGENT}, **(kwargs.pop("headers", {}) or {})},
                **kwargs,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            time.sleep(1.5 * (2**attempt))
    raise last_err  # type: ignore[misc]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return r * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _score_uniqueness(text: str) -> int:
    text_l = (text or "").lower()
    score = 5
    plus = (
        "hidden",
        "secret",
        "off the beaten",
        "local",
        "independent",
        "historic",
        "quirky",
        "viewpoint",
        "cave",
        "ruin",
    )
    minus = ("eiffel", "main square", "mainstream", "resort", "all inclusive")
    for token in plus:
        if token in text_l:
            score += 1
    for token in minus:
        if token in text_l:
            score -= 2
    return max(1, min(10, score))


def _fetch_wikidata_candidates(area: str, limit: int) -> List[Dict]:
    query = f"""
SELECT ?item ?itemLabel ?coord ?countryLabel ?desc WHERE {{
  ?item wdt:P31/wdt:P279* ?type ;
        wdt:P625 ?coord ;
        wdt:P17 ?country .
  FILTER(?type IN (wd:Q570116, wd:Q839954, wd:Q23413, wd:Q16970, wd:Q24354, wd:Q4989906))
  OPTIONAL {{ ?item schema:description ?desc FILTER(LANG(?desc) = "en") }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
  FILTER(CONTAINS(LCASE(?itemLabel), LCASE("{area}")) || CONTAINS(LCASE(COALESCE(?desc, "")), LCASE("{area}")))
}}
LIMIT {limit}
"""
    resp = _request_with_retries(
        "GET",
        WIKIDATA_SPARQL_URL,
        params={"format": "json", "query": query},
        retries=3,
        timeout=75,
    )
    resp.raise_for_status()
    bindings = resp.json().get("results", {}).get("bindings", [])
    out = []
    for row in bindings:
        name = row.get("itemLabel", {}).get("value", "").strip()
        country = row.get("countryLabel", {}).get("value", "").strip() or "Unknown"
        desc = row.get("desc", {}).get("value", "").strip()
        coord = row.get("coord", {}).get("value", "")
        match = re.search(r"Point\(([-0-9.]+)\s+([-0-9.]+)\)", coord)
        if not name or not match:
            continue
        lon = float(match.group(1))
        lat = float(match.group(2))
        out.append(
            {
                "navn": name,
                "by": area.title(),
                "land": country,
                "beskrivelse": desc,
                "latitude": lat,
                "longitude": lon,
                "source_type": "hidden_gem",
                "type": "kultur",
                "source_url": row.get("item", {}).get("value", ""),
            }
        )
    return out


def _coords_from_osm_element(element: Dict) -> Optional[Tuple[float, float]]:
    """Henter lat/lon fra node eller center på way/relation."""
    lat = element.get("lat")
    lon = element.get("lon")
    if lat is not None and lon is not None:
        return float(lat), float(lon)
    center = element.get("center") or {}
    c_lat = center.get("lat")
    c_lon = center.get("lon")
    if c_lat is not None and c_lon is not None:
        return float(c_lat), float(c_lon)
    return None


def _overpass_query(
    lat: float,
    lon: float,
    *,
    kinds: Tuple[str, ...] = ("node", "way", "relation"),
    radius_m: int = 25000,
) -> str:
    """Overpass-spørring for valgte OSM-typer med navn."""
    around = f"around:{radius_m},{lat},{lon}"
    tag_filters = (
        '["tourism"~"attraction|viewpoint|museum"]["name"]',
        '["historic"]["name"]',
        '["amenity"~"arts_centre|theatre|marketplace"]["name"]',
    )
    lines = []
    for tag_filter in tag_filters:
        for kind in kinds:
            lines.append(f"  {kind}{tag_filter}({around});")
    body = "\n".join(lines)
    return f"""
[out:json][timeout:25];
(
{body}
);
out tags center;
"""


def _run_overpass_query(query: str) -> List[Dict]:
    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            resp = _request_with_retries(
                "POST",
                endpoint,
                data=query.encode("utf-8"),
                headers={"Content-Type": "text/plain"},
                retries=2,
                timeout=90,
            )
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return []


def _fetch_overpass_candidates(area: str, limit: int) -> List[Dict]:
    geo = _request_with_retries(
        "GET",
        "https://nominatim.openstreetmap.org/search",
        params={"q": area, "format": "json", "limit": 1, "addressdetails": 1},
        retries=3,
        timeout=25,
    )
    geo.raise_for_status()
    g = geo.json()
    if not g:
        return []
    hit = g[0]
    lat = float(hit["lat"])
    lon = float(hit["lon"])
    default_city = (hit.get("address") or {}).get("city") or (hit.get("address") or {}).get(
        "town"
    ) or (hit.get("address") or {}).get("village") or area.title()
    default_country = (hit.get("address") or {}).get("country") or area.title()

    # Fase 1: noder (rask og stabil). Fase 2: way/relation ved behov.
    elements = _run_overpass_query(_overpass_query(lat, lon, kinds=("node",), radius_m=25000))
    if len(elements) < limit:
        try:
            extra = _run_overpass_query(
                _overpass_query(lat, lon, kinds=("way", "relation"), radius_m=15000)
            )
            elements.extend(extra)
        except Exception:
            pass

    out = []
    seen_names = set()
    for e in elements:
        if len(out) >= limit:
            break
        tags = e.get("tags") or {}
        name = (tags.get("name") or tags.get("name:en") or "").strip()
        if not name:
            continue
        name_key = name.lower()
        if name_key in seen_names:
            continue
        coords = _coords_from_osm_element(e)
        if not coords:
            continue
        e_lat, e_lon = coords
        seen_names.add(name_key)
        desc_parts = [
            tags.get("tourism", ""),
            tags.get("historic", ""),
            tags.get("amenity", ""),
        ]
        desc = ", ".join([p for p in desc_parts if p])
        land = tags.get("addr:country") or default_country
        by = tags.get("addr:city") or default_city
        osm_type = e.get("type", "node")
        out.append(
            {
                "navn": name,
                "by": by,
                "land": land,
                "beskrivelse": f"OpenStreetMap: {desc}" if desc else "OpenStreetMap-kandidat.",
                "latitude": e_lat,
                "longitude": e_lon,
                "source_type": "hidden_gem",
                "type": "kultur",
                "source_url": f"https://www.openstreetmap.org/{osm_type}/{e.get('id')}",
            }
        )
    return out


def _load_existing_places() -> List[Dict]:
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT name, city, country, latitude, longitude, source_type
            FROM places
            """
        ).fetchall()
    return [
        {
            "navn": r[0] or "",
            "by": r[1] or "",
            "land": r[2] or "",
            "latitude": r[3],
            "longitude": r[4],
            "source_type": r[5] or "hidden_gem",
        }
        for r in rows
    ]


def _is_duplicate(candidate: Dict, existing: List[Dict]) -> bool:
    c_name = (candidate.get("navn") or "").strip().lower()
    c_city = (candidate.get("by") or "").strip().lower()
    c_country = (candidate.get("land") or "").strip().lower()
    c_lat = candidate.get("latitude")
    c_lon = candidate.get("longitude")
    for p in existing:
        if (
            c_name
            and c_city
            and c_country
            and c_name == (p.get("navn") or "").strip().lower()
            and c_city == (p.get("by") or "").strip().lower()
            and c_country == (p.get("land") or "").strip().lower()
        ):
            return True
        p_lat = p.get("latitude")
        p_lon = p.get("longitude")
        if (
            c_lat is not None
            and c_lon is not None
            and p_lat is not None
            and p_lon is not None
            and _haversine_km(float(c_lat), float(c_lon), float(p_lat), float(p_lon)) < 0.25
        ):
            return True
    return False


def _normalize_candidate(raw: Dict, area: str, min_score: int = 6) -> Optional[Dict]:
    navn = (raw.get("navn") or "").strip()
    if not navn:
        return None
    by = (raw.get("by") or area).strip() or area
    land = (raw.get("land") or "").strip() or "Unknown"
    beskrivelse = (raw.get("beskrivelse") or "").strip()
    lat = raw.get("latitude")
    lon = raw.get("longitude")
    if lat is None or lon is None:
        return None
    score = _score_uniqueness(f"{navn} {beskrivelse}")
    source_url = raw.get("source_url") or ""
    if "openstreetmap.org" in source_url and any(
        token in beskrivelse.lower()
        for token in ("historic", "viewpoint", "attraction", "museum", "ruins", "monument")
    ):
        score = min(10, score + 1)
    if score < min_score:
        return None
    source_type = raw.get("source_type") or "hidden_gem"
    sted = {
        "id": f"{source_type}-ingest-{_slug(navn)}-{_slug(by)}-{_slug(land)}",
        "navn": navn,
        "by": by,
        "land": land,
        "type": raw.get("type") or "kultur",
        "profil_kategori": "Kultur & Historie",
        "beskrivelse": beskrivelse or f"Automatisk funnet kandidat i {area}.",
        "tips": "Automatisk innhentet. Verifiser lokalt.",
        "beste_tid": "",
        "pris": "€€",
        "latitude": float(lat),
        "longitude": float(lon),
        "image_url": "",
        "country_code": "",
        "source_type": source_type,
        "uniqueness_score": score,
        "source_url": raw.get("source_url", ""),
    }
    return sted


def _persist_places(places: List[Dict]) -> int:
    init_db()
    inserted = 0
    with get_connection() as conn:
        for p in places:
            normalized = normalize_place(p, p["source_type"])
            raw_json = dict(normalized)
            raw_json["uniqueness_score"] = p.get("uniqueness_score")
            raw_json["source_url"] = p.get("source_url")
            conn.execute(
                """
                INSERT OR REPLACE INTO places (
                    id, name, city, country, country_code, category, description,
                    tips, best_time, price, latitude, longitude, source_type,
                    search_key, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["id"],
                    normalized["navn"],
                    normalized["by"],
                    normalized["land"],
                    normalized["country_code"],
                    normalized["type"],
                    normalized["beskrivelse"],
                    normalized["tips"],
                    normalized["beste_tid"],
                    normalized["pris"],
                    normalized["latitude"],
                    normalized["longitude"],
                    normalized["source_type"],
                    normalized["search_key"],
                    json.dumps(raw_json, ensure_ascii=False),
                ),
            )
            inserted += 1
        conn.commit()
    return inserted


def run_ingest(
    area: str,
    limit: int,
    commit: bool,
    *,
    skip_wikidata: bool = False,
    min_score: int = 6,
) -> Tuple[List[Dict], Dict]:
    existing = _load_existing_places()
    candidates = []
    source_errors: List[str] = []
    overpass_count = 0
    if not skip_wikidata:
        try:
            candidates.extend(_fetch_wikidata_candidates(area, limit))
        except Exception as e:
            source_errors.append(f"wikidata: {e}")
    try:
        overpass_hits = _fetch_overpass_candidates(area, limit)
        overpass_count = len(overpass_hits)
        candidates.extend(overpass_hits)
    except Exception as e:
        source_errors.append(f"overpass: {e}")

    approved: List[Dict] = []
    stats = {
        "fetched": len(candidates),
        "overpass_raw": overpass_count,
        "approved": 0,
        "duplicates": 0,
        "low_quality_or_invalid": 0,
        "inserted": 0,
        "source_errors": source_errors,
    }
    seen_local = []
    for raw in candidates:
        normalized = _normalize_candidate(raw, area, min_score=min_score)
        if not normalized:
            stats["low_quality_or_invalid"] += 1
            continue
        if _is_duplicate(normalized, existing + seen_local):
            stats["duplicates"] += 1
            continue
        approved.append(normalized)
        seen_local.append(normalized)

    stats["approved"] = len(approved)
    if commit and approved:
        stats["inserted"] = _persist_places(approved)
    return approved, stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest hidden-gem candidates from Wikidata and Overpass."
    )
    parser.add_argument("--area", required=True, help="Area, city, region, or theme.")
    parser.add_argument("--limit", type=int, default=50, help="Max candidates per source.")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write approved candidates to database. Without this, dry-run only.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output json path for approved candidates preview.",
    )
    parser.add_argument(
        "--skip-wikidata",
        action="store_true",
        help="Skip Wikidata (useful when SPARQL is slow or down).",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=6,
        help="Minimum uniqueness score (1-10) for ingest. Default 6 for bulk growth.",
    )
    args = parser.parse_args()

    approved, stats = run_ingest(
        args.area,
        args.limit,
        args.commit,
        skip_wikidata=args.skip_wikidata,
        min_score=max(1, min(10, args.min_score)),
    )
    print(
        json.dumps(
            {
                "area": args.area,
                "stats": stats,
                "sample": approved[:10],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(approved, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
