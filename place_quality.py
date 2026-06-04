"""Felles kvalitetsregler for nye og eksisterende perler (KI, seed, visning).

Overnatting: bruk _hent_overnatting_kilde() — ikke
«from database import UNIKE_HOTELLER» (omdøpt til UNIKE_OVERNATTING).
"""
import json
import re
from typing import Dict, List, Optional, Set, Tuple

import database as _database

LOKALE_SPISESTEDER = _database.LOKALE_SPISESTEDER
SKJULTE_PERLER = _database.SKJULTE_PERLER
SPANIA_MARKEDSDATA = _database.SPANIA_MARKEDSDATA


def _hent_overnatting_kilde():
    import importlib

    importlib.reload(_database)
    return list(
        getattr(_database, "UNIKE_OVERNATTING", None)
        or getattr(_database, "UNIKE_HOTELLER", [])
    )

MIN_UNIKHETSGRAD = 9
HOTELL_MAKS_EURO_DOBLELTROM = 300

_KI_LAGRET_MARKORER = (
    "ki-agent",
    "oppdaget av ki",
    "særhetsscore:",
    "saerhetsscore:",
    "anbefalt av reiseeksperten",
)

_RESTAURANT_KJEDER = (
    "mcdonald",
    "burger king",
    "starbucks",
    "subway",
    "kfc",
    "pizza hut",
    "domino",
    "taco bell",
    "wagamama",
    "hard rock cafe",
    "planet hollywood",
    "tgi friday",
)

_RESTAURANT_STERKE_TYPER = ("gastronomi", "restaurant", "mat", "tapas", "trattoria", "osteria")

_RESTAURANT_STERKE_ORD = (
    "restaurant",
    "trattoria",
    "osteria",
    "taverna",
    "gastropub",
    "spisested",
    "matsted",
    "bistro",
    "kro",
    "sjømat",
    "steakhouse",
)

_RESTAURANT_UNIKHET_ORD = (
    "lokal",
    "familie",
    "tradisjon",
    "historisk",
    "chef",
    "kokk",
    "signatur",
    "hjemmelag",
    "marked",
    "generasjon",
    "håndlag",
    "spesial",
    "kjent for",
    "siden 1",
    "siden 18",
)

_HOTELL_KJEDER = (
    "marriott",
    "hilton",
    "radisson",
    "holiday inn",
    "best western",
    "ibis",
    "novotel",
    "premier inn",
    "hyatt",
    "sheraton",
    "intercontinental",
    "four seasons",
    "ritz-carlton",
)


def _make_place_id(sted, source_type: str) -> str:
    raw = "|".join(
        [
            source_type,
            sted.get("navn", ""),
            sted.get("by", ""),
            sted.get("land", ""),
        ]
    )
    return raw.lower().replace(" ", "-")


def _build_kuratert_ids() -> Set[str]:
    ids: Set[str] = set()
    for sted in SKJULTE_PERLER:
        ids.add(sted.get("id") or _make_place_id(sted, "hidden_gem"))
    for sted in LOKALE_SPISESTEDER:
        ids.add(sted.get("id") or _make_place_id(sted, "restaurant"))
    for sted in _hent_overnatting_kilde():
        ids.add(sted.get("id") or _make_place_id(sted, "hotel"))
    for sted in SPANIA_MARKEDSDATA:
        sid = str(sted.get("id", ""))
        if sid.startswith("es_gem"):
            ids.add(sid)
        elif sid.startswith("es_rest"):
            ids.add(sid)
    return ids


KURATERT_STED_IDS = _build_kuratert_ids()


def refresh_kuratert_sted_ids() -> Set[str]:
    """Oppfrisker kuraterte ID-er (database.py kan være endret siden første import)."""
    global KURATERT_STED_IDS
    KURATERT_STED_IDS = _build_kuratert_ids()
    return KURATERT_STED_IDS


def score_saerhetstekst(tekst: str) -> int:
    tekst_l = (tekst or "").lower()
    score = 5
    plusspoeng = {
        "skjult": 2,
        "hemmelig": 2,
        "off-the-beaten-path": 2,
        "eksentrisk": 2,
        "quirky": 2,
        "lokal": 1,
        "ukjent": 1,
        "forlatt": 1,
        "bakgård": 1,
        "uvanlig": 1,
        "unik": 1,
        "historie": 1,
        "utsikt": 1,
    }
    minuspoeng = {
        "eiffeltårnet": 4,
        "eiffel tower": 4,
        "resort": 2,
        "all inclusive": 2,
        "mainstream": 2,
        "turistfelle": 2,
    }
    for ordlyd, poeng in plusspoeng.items():
        if ordlyd in tekst_l:
            score += poeng
    for ordlyd, poeng in minuspoeng.items():
        if ordlyd in tekst_l:
            score -= poeng
    return max(1, min(10, score))


def score_saerhet_utvidet(sted: Dict) -> int:
    """Regelbasert score for eksisterende steder (datakvalitet + tekst)."""
    tekst = " ".join(
        str(sted.get(felt, "") or "")
        for felt in ("navn", "beskrivelse", "tips", "type")
    )
    score = score_saerhetstekst(tekst)
    if sted.get("latitude") is None or sted.get("longitude") is None:
        score -= 1
    if not (sted.get("beskrivelse") or "").strip():
        score -= 1
    if len((sted.get("beskrivelse") or "").split()) > 14:
        score += 1
    return max(1, min(10, score))


def normaliser_saerhetsscore(verdi, fallback_tekst: str = "") -> int:
    try:
        score = int(round(float(verdi)))
    except (TypeError, ValueError):
        score = score_saerhetstekst(fallback_tekst)
    return max(1, min(10, score))


def _fallback_tekst(sted: Dict) -> str:
    return " ".join(
        str(sted.get(felt, "") or "")
        for felt in ("navn", "beskrivelse", "tips", "type", "by", "land")
    )


def _hent_lagret_saerhetsscore(sted: Dict) -> Optional[int]:
    for felt in ("saerhetsscore", "uniqueness_score"):
        if sted.get(felt) is not None:
            return normaliser_saerhetsscore(sted[felt], _fallback_tekst(sted))
    return None


def _er_kuratert_hotel_id(sted_id: str) -> bool:
    """Sjekk mot fersk UNIKE_OVERNATTING (uavhengig av import-cache)."""
    for sted in _hent_overnatting_kilde():
        if (sted.get("id") or _make_place_id(sted, "hotel")) == sted_id:
            return True
    return False


def er_kuratert_seed(sted: Dict) -> bool:
    sid = sted.get("id") or ""
    if sid in KURATERT_STED_IDS:
        return True
    if sted.get("source_type") == "hotel" and _er_kuratert_hotel_id(sid):
        return True
    return False


def er_ki_eller_agent_lagret(sted: Dict) -> bool:
    blob = f"{sted.get('tips', '')} {sted.get('beskrivelse', '')}".lower()
    return any(markor in blob for markor in _KI_LAGRET_MARKORER)


def effektiv_saerhetsscore(sted: Dict) -> int:
    eksplisitt = _hent_lagret_saerhetsscore(sted)
    if eksplisitt is not None:
        return eksplisitt
    if er_kuratert_seed(sted):
        return MIN_UNIKHETSGRAD
    return score_saerhet_utvidet(sted)


def tekst_for_sted_sjekk(sted: Dict) -> str:
    return " ".join(
        str(sted.get(felt, "") or "")
        for felt in ("navn", "beskrivelse", "by", "land", "tips")
    ).lower()


def er_kjede_restaurant(tekst: str) -> bool:
    return any(kjede in tekst for kjede in _RESTAURANT_KJEDER)


def er_kjede_hotell(tekst: str) -> bool:
    return any(kjede in tekst for kjede in _HOTELL_KJEDER)


def hotell_pris_innen_grense(sted: Dict) -> bool:
    pris = (sted.get("pris") or "").strip()
    tekst = tekst_for_sted_sjekk(sted)
    if "€€€" in pris:
        return False
    tall: List[int] = []
    for lav in (pris.lower(), tekst):
        for start, end in re.findall(r"(\d{2,4})\s*[-–]\s*(\d{2,4})", lav):
            tall.extend(int(x) for x in (start, end))
        for enkelt in re.findall(
            r"(?:€|eur|euro)\s*(\d{2,4})|(\d{2,4})\s*(?:€|eur|euro|kr/natt|per natt)",
            lav,
        ):
            tall.extend(int(x) for x in enkelt if x)
        for n in re.findall(r"(?<![\d])(\d{2,4})(?!\d)", lav):
            verdi = int(n)
            if 40 <= verdi <= 2500:
                tall.append(verdi)
    if tall and max(tall) > HOTELL_MAKS_EURO_DOBLELTROM:
        return False
    return True


def godkjent_hotel_kandidat(kandidat: Dict, strict_mode: bool = False) -> bool:
    if kandidat.get("source_type") != "hotel":
        return True
    tekst = tekst_for_sted_sjekk(kandidat)
    if er_kjede_hotell(tekst):
        return False
    if kandidat.get("saerhetsscore", 0) < MIN_UNIKHETSGRAD:
        return False
    if not hotell_pris_innen_grense(kandidat):
        return False
    if len((kandidat.get("beskrivelse") or "").strip()) < 35:
        return False
    return True


def godkjent_restaurant_kandidat(kandidat: Dict, strict_mode: bool = False) -> bool:
    if kandidat.get("source_type") != "restaurant":
        return True
    tekst = tekst_for_sted_sjekk(kandidat)
    if er_kjede_restaurant(tekst):
        return False
    if kandidat.get("saerhetsscore", 0) < MIN_UNIKHETSGRAD:
        return False
    beskrivelse = (kandidat.get("beskrivelse") or "").strip()
    if len(beskrivelse) < 40:
        return False
    if strict_mode and not any(signal in tekst for signal in _RESTAURANT_UNIKHET_ORD):
        return False
    return True


def _med_score(sted: Dict) -> Dict:
    return {**sted, "saerhetsscore": effektiv_saerhetsscore(sted)}


def oppfyller_visning_kriterier(sted: Dict, strict_mode: bool = False) -> bool:
    """Samme regler som KI-sanking, tilpasset kuratert vs. KI-lagret innhold."""
    med = _med_score(sted)
    source = med.get("source_type", "hidden_gem")

    if source == "hotel":
        return godkjent_hotel_kandidat(med, strict_mode=strict_mode)
    if source == "restaurant":
        return godkjent_restaurant_kandidat(med, strict_mode=strict_mode)

    if er_ki_eller_agent_lagret(sted) and med["saerhetsscore"] < MIN_UNIKHETSGRAD:
        return False
    if not er_kuratert_seed(sted) and not er_ki_eller_agent_lagret(sted):
        if med["saerhetsscore"] < MIN_UNIKHETSGRAD:
            return False
    tekst = tekst_for_sted_sjekk(sted)
    if er_kjede_restaurant(tekst) or er_kjede_hotell(tekst):
        return False
    return True


def filtrer_steder_for_app(steder: List[Dict], strict_mode: bool = False) -> List[Dict]:
    refresh_kuratert_sted_ids()
    return [s for s in steder if oppfyller_visning_kriterier(s, strict_mode=strict_mode)]


def vurder_eksisterende_sted(sted: Dict, strict_mode: bool = False) -> Dict:
    """Regelbasert vurdering for rapporter (samme terskler som appen)."""
    med = _med_score(sted)
    score = med["saerhetsscore"]
    grunner: List[str] = []
    source = med.get("source_type", "hidden_gem")

    if source == "hotel":
        if er_kjede_hotell(tekst_for_sted_sjekk(sted)):
            grunner.append("Overnatting-kjede")
        if score < MIN_UNIKHETSGRAD:
            grunner.append(f"Unikhetsgrad {score} < {MIN_UNIKHETSGRAD}")
        if not hotell_pris_innen_grense(sted):
            grunner.append(f"Dobbeltrom over {HOTELL_MAKS_EURO_DOBLELTROM} €")
        if len((sted.get("beskrivelse") or "").strip()) < 35:
            grunner.append("For kort beskrivelse av overnatting")
    elif source == "restaurant":
        if er_kjede_restaurant(tekst_for_sted_sjekk(sted)):
            grunner.append("Restaurantkjede")
        if score < MIN_UNIKHETSGRAD:
            grunner.append(f"Unikhetsgrad {score} < {MIN_UNIKHETSGRAD}")
        if len((sted.get("beskrivelse") or "").strip()) < 40:
            grunner.append("For kort restaurantbeskrivelse")
        if strict_mode and not any(
            s in tekst_for_sted_sjekk(sted) for s in _RESTAURANT_UNIKHET_ORD
        ):
            grunner.append("Mangler tydelig lokal/unik mat-signatur")
    else:
        if er_ki_eller_agent_lagret(sted) and score < MIN_UNIKHETSGRAD:
            grunner.append(f"KI-lagret med unikhetsgrad {score} < {MIN_UNIKHETSGRAD}")
        elif not er_kuratert_seed(sted) and score < MIN_UNIKHETSGRAD:
            grunner.append(f"Ukjent kilde, unikhetsgrad {score} < {MIN_UNIKHETSGRAD}")
        if er_kjede_restaurant(tekst_for_sted_sjekk(sted)):
            grunner.append("Kjede (mat)")
        if er_kjede_hotell(tekst_for_sted_sjekk(sted)):
            grunner.append("Kjede (overnatting)")

    kilde = (
        "kuratert"
        if er_kuratert_seed(sted)
        else ("ki/agent" if er_ki_eller_agent_lagret(sted) else "annet")
    )
    godkjent = not grunner
    return {
        "score": score,
        "status": "behold" if godkjent else "vurder sletting",
        "begrunnelse": (
            f"Oppfyller app-regler (kilde: {kilde})."
            if godkjent
            else "; ".join(grunner)
        ),
        "kilde": kilde,
        "godkjent": godkjent,
    }


def klassifiser_restaurant_fra_perle(perle, navn, beskrivelse, type_hint, source_type):
    tekst_lc = f"{navn} {beskrivelse}".lower()
    if source_type in ("hidden_gem", "restaurant"):
        eksplisitt = source_type == "restaurant"
    else:
        eksplisitt = False

    type_er_mat = type_hint in _RESTAURANT_STERKE_TYPER
    sterkt_matord = any(ordlyd in tekst_lc for ordlyd in _RESTAURANT_STERKE_ORD)

    if eksplisitt or type_er_mat or sterkt_matord:
        if er_kjede_restaurant(tekst_lc):
            return "hidden_gem"
        return "restaurant"
    return "hidden_gem"


_HOTELL_STERKE_ORD = (
    "hotell",
    "hotel",
    "suite",
    "grottehotell",
    "overnatting",
    "fyrvokter",
    "prestegård",
    "hostel",
    "pensjonat",
    "bed and breakfast",
    "bnb",
    "resort",
    "slott",
    "schloss",
    "slot ",
    "herberge",
    "gjestgiveri",
)


def klassifiser_source_type_fra_perle(perle, navn, beskrivelse, type_hint, source_type):
    tekst_lc = f"{navn} {beskrivelse}".lower()
    eksplisitt = (perle.get("source_type") or "").strip().lower()

    er_hotell = (
        eksplisitt == "hotel"
        or type_hint in ("hotell", "hotel", "overnatting", "lodging")
        or any(ordlyd in tekst_lc for ordlyd in _HOTELL_STERKE_ORD)
    )
    if er_hotell:
        return "hidden_gem" if er_kjede_hotell(tekst_lc) else "hotel"

    return klassifiser_restaurant_fra_perle(perle, navn, beskrivelse, type_hint, source_type)
