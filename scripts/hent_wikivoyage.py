"""
Hent flere perler fra Wikivoyage og lagre som kandidater eller i SQLite.

Eksempler:
  python scripts/hent_wikivoyage.py --kategori "Previously Off the beaten path" --limit 40
  python scripts/hent_wikivoyage.py --destinasjon Ghent --seksjoner see,do --limit 25
  python scripts/hent_wikivoyage.py --europa-byer --limit-per-by 15 --commit
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_store import get_connection, init_db, normalize_place
from place_quality import filtrer_steder_for_app, vurder_eksisterende_sted
from wikivoyage_client import (
    ANBEFALTE_KATEGORIER,
    EUROPA_DESTINASJONER,
    fyll_manglende_koordinater,
    hent_artikkel_med_koordinater,
    hent_kategori_medlemmer,
    hent_listings_for_destinasjon,
)


def _load_existing_keys():
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT name, city, country FROM places"
        ).fetchall()
    keys = set()
    for navn, by, land in rows:
        keys.add(
            f"{(navn or '').strip().lower()}|{(by or '').strip().lower()}|{(land or '').strip().lower()}"
        )
    return keys


def _er_duplikat(sted, keys):
    nokkel = (
        f"{(sted.get('navn') or '').strip().lower()}|"
        f"{(sted.get('by') or '').strip().lower()}|"
        f"{(sted.get('land') or '').strip().lower()}"
    )
    return nokkel in keys


def _hent_fra_kategori(kategori: str, limit: int, pause: float):
    titler = hent_kategori_medlemmer(kategori, limit=limit, pause=pause)
    steder = []
    for tittel in titler:
        data = hent_artikkel_med_koordinater(tittel, pause=pause)
        if data:
            steder.append(data)
    return steder


def _hent_fra_destinasjoner(destinasjoner, seksjoner, limit_per_by: int, pause: float):
    steder = []
    for dest in destinasjoner:
        listings = hent_listings_for_destinasjon(
            dest, seksjoner=seksjoner, pause=pause
        )
        steder.extend(listings[:limit_per_by])
    return steder


def _persist(steder):
    init_db()
    lagret = 0
    with get_connection() as conn:
        for sted in steder:
            norm = normalize_place(sted, sted["source_type"])
            raw = dict(norm)
            raw["source_url"] = sted.get("source_url", "")
            raw["wikivoyage_listing"] = sted.get("wikivoyage_listing", "")
            conn.execute(
                """
                INSERT OR REPLACE INTO places (
                    id, name, city, country, country_code, category, description,
                    tips, best_time, price, latitude, longitude, source_type,
                    search_key, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    norm["id"],
                    norm["navn"],
                    norm["by"],
                    norm["land"],
                    norm["country_code"],
                    norm["type"],
                    norm["beskrivelse"],
                    norm["tips"],
                    norm["beste_tid"],
                    norm["pris"],
                    norm["latitude"],
                    norm["longitude"],
                    norm["source_type"],
                    norm["search_key"],
                    json.dumps(raw, ensure_ascii=False),
                ),
            )
            lagret += 1
        conn.commit()
    return lagret


def main():
    parser = argparse.ArgumentParser(description="Hent steder fra Wikivoyage.")
    parser.add_argument("--kategori", help="Wikivoyage-kategorinavn (uten 'Category:').")
    parser.add_argument("--destinasjon", help="Én by/destinasjon (f.eks. Ghent).")
    parser.add_argument(
        "--europa-byer",
        action="store_true",
        help=f"Parse See/Do for {len(EUROPA_DESTINASJONER)} forhåndsvalgte europeiske byer.",
    )
    parser.add_argument(
        "--seksjoner",
        default="see,do",
        help="Kommaseparert: see, do, eat, drink.",
    )
    parser.add_argument("--limit", type=int, default=50, help="Maks artikler fra kategori.")
    parser.add_argument(
        "--limit-per-by",
        type=int,
        default=20,
        help="Maks listings per destinasjon ved --destinasjon/--europa-byer.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.12,
        help="Pause mellom API-kall (sekunder).",
    )
    parser.add_argument(
        "--geokod",
        action="store_true",
        help="Fyll manglende koordinater via Nominatim (tregere, respekter rate limit).",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Lagre kandidater i hemmelige_europa.sqlite3 (kun de som passer kvalitetsfilter).",
    )
    parser.add_argument(
        "--commit-alle",
        action="store_true",
        help="Lagre alle unike kandidater med koordinater, uten kvalitetsfilter.",
    )
    parser.add_argument(
        "--ingen-kvalitetsfilter",
        action="store_true",
        help="Skriv alle unike treff til JSON, også de som ikke passer app-reglene ennå.",
    )
    parser.add_argument(
        "--output",
        default="wikivoyage_kandidater.json",
        help="JSON-fil for alle kandidater (etter filtrering).",
    )
    parser.add_argument(
        "--list-kategorier",
        action="store_true",
        help="Vis anbefalte Wikivoyage-kategorier og avslutt.",
    )
    args = parser.parse_args()

    if args.list_kategorier:
        print("Anbefalte kategorier på en.wikivoyage.org:")
        for k in ANBEFALTE_KATEGORIER:
            print(f"  - {k}")
        return

    if not args.kategori and not args.destinasjon and not args.europa_byer:
        parser.error("Velg --kategori, --destinasjon eller --europa-byer (eller --list-kategorier).")

    kandidater = []
    if args.kategori:
        print(f"Henter kategori: {args.kategori} …")
        kandidater.extend(_hent_fra_kategori(args.kategori, args.limit, args.pause))

    seksjoner = [s.strip() for s in args.seksjoner.split(",") if s.strip()]
    if args.destinasjon:
        print(f"Henter listings fra {args.destinasjon} ({', '.join(seksjoner)}) …")
        kandidater.extend(
            _hent_fra_destinasjoner(
                [args.destinasjon], seksjoner, args.limit_per_by, args.pause
            )
        )
    if args.europa_byer:
        print(f"Henter listings fra {len(EUROPA_DESTINASJONER)} europeiske byer …")
        kandidater.extend(
            _hent_fra_destinasjoner(
                EUROPA_DESTINASJONER, seksjoner, args.limit_per_by, args.pause
            )
        )

    print(f"Råtreff: {len(kandidater)}")
    if args.geokod:
        print("Geokoder manglende koordinater …")
        kandidater = fyll_manglende_koordinater(kandidater, pause=args.pause)

    eksisterende = _load_existing_keys()
    unike = []
    for sted in kandidater:
        if sted.get("latitude") is None or sted.get("longitude") is None:
            continue
        if _er_duplikat(sted, eksisterende):
            continue
        unike.append(sted)
        eksisterende.add(
            f"{sted['navn'].strip().lower()}|{sted['by'].strip().lower()}|{sted['land'].strip().lower()}"
        )

    print(f"Med koordinater og uten duplikat: {len(unike)}")
    for sted in unike:
        sted["kvalitet"] = vurder_eksisterende_sted(sted)

    godkjente = filtrer_steder_for_app(unike)
    print(f"Oppfyller app-kvalitet: {len(godkjente)}")
    avvist = len(unike) - len(godkjente)
    if avvist:
        print(f"Filtrert bort av app-regler: {avvist} (bruk --ingen-kvalitetsfilter for full liste)")

    til_json = unike if args.ingen_kvalitetsfilter else godkjente
    out_path = ROOT / args.output
    out_path.write_text(
        json.dumps(til_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Skrev {len(til_json)} kandidater til {out_path}")

    if args.commit_alle and unike:
        n = _persist(unike)
        print(f"Lagret {n} steder i SQLite (alle unike, uten kvalitetsfilter).")
    elif args.commit and godkjente:
        n = _persist(godkjente)
        print(f"Lagret {n} steder i SQLite (kun kvalitetsgodkjente).")
    elif args.commit or args.commit_alle:
        print("Ingen steder å lagre.")


if __name__ == "__main__":
    main()
