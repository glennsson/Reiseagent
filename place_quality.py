"""Felles kvalitetsregler for nye og eksisterende perler (KI, seed, visning).

Overnatting: bruk _hent_overnatting_kilde() — ikke
«from database import UNIKE_HOTELLER» (omdøpt til UNIKE_OVERNATTING).
"""

# Offentlige terskler — defineres før database-import for trygg del-import.
MIN_UNIKHETSGRAD = 9
MAT_MIN_UNIKHETSGRAD = 9
HOTELL_MIN_UNIKHETSGRAD = 10
SANK_MIN_UNIKHETSGRAD = 8
SANK_HOTELL_MIN_BESKRIVELSE = 25
SANK_RESTAURANT_MIN_BESKRIVELSE = 28

# Bakoverkompatibilitet: makspris er fjernet (aldri brukt til filtrering lenger).
HOTELL_MAKS_NOK_DOBBELTROM = None
HOTELL_MAKS_NOK_DOBLELTROM = None
HOTELL_MAKS_EURO_DOBLELTROM = None

__all__ = [
    "MIN_UNIKHETSGRAD",
    "MAT_MIN_UNIKHETSGRAD",
    "HOTELL_MIN_UNIKHETSGRAD",
    "SANK_MIN_UNIKHETSGRAD",
    "SANK_HOTELL_MIN_BESKRIVELSE",
    "SANK_RESTAURANT_MIN_BESKRIVELSE",
    "HOTELL_MAKS_NOK_DOBBELTROM",
    "HOTELL_MAKS_NOK_DOBLELTROM",
    "HOTELL_MAKS_EURO_DOBLELTROM",
    "LOKALE_SPISESTEDER",
    "SKJULTE_PERLER",
    "SPANIA_MARKEDSDATA",
    "KURATERT_STED_IDS",
    "_RESTAURANT_STERKE_ORD",
    "effektiv_saerhetsscore",
    "er_ki_eller_agent_lagret",
    "er_agent_forslag",
    "er_kjede_hotell",
    "er_kjede_restaurant",
    "er_kuratert_seed",
    "er_velbesokt_museum",
    "er_velbesøkt_museum",
    "er_mainstream_turistdestinasjon",
    "er_blant_landets_storste_byer",
    "filtrer_steder_for_app",
    "godkjent_hotel_kandidat",
    "godkjent_restaurant_kandidat",
    "hotell_pris_innen_grense",
    "klassifiser_restaurant_fra_perle",
    "klassifiser_source_type_fra_perle",
    "normaliser_saerhetsscore",
    "oppfyller_visning_kriterier",
    "refresh_kuratert_sted_ids",
    "score_saerhet_utvidet",
    "score_saerhetstekst",
    "tekst_for_sted_sjekk",
    "vurder_eksisterende_sted",
]

import json
import re
import unicodedata
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

_MUSEUM_TYPER = ("museum", "museer", "galleri", "gallery", "kunstmuseum", "art museum")

_MUSEUM_ORD = ("museum", "museet", "museo", "musée", "museu", "galleri", "gallery", "samling")

_KJENTE_MUSEER = (
    "louvre",
    "british museum",
    "vatikanmuseene",
    "vatican museums",
    "musei vaticani",
    "rijksmuseum",
    "uffizi",
    "museo del prado",
    "museo prado",
    "hermitage",
    "eremitasjen",
    "anne frank huis",
    "anne frank house",
    "tate modern",
    "national gallery",
    "musée d'orsay",
    "musee d'orsay",
    "centre pompidou",
    "pompidou",
    "van gogh museum",
    "acropolis museum",
    "akropolis-museet",
    "munch-museet",
    "munch museum",
    "dali museum",
    "dalí museum",
    "guggenheim museum",
    "natural history museum",
    "science museum london",
    "deutsches museum",
    "reichsmuseum",
)

_MUSEUM_BESOK_SIGNAL = (
    "million besøk",
    "million visitors",
    "million tourist",
    "mest besøkte museum",
    "most visited museum",
    "verdens mest besøkte",
    "world's most visited",
    "worlds most visited",
    "turistmagnet",
    "major museum",
    "berømt museum",
    "famous museum",
    "ikonisk museum",
    "internasjonalt museum",
    "international museum",
    "kjent museum",
    "well-known museum",
    "hovedattraksjon",
    "main attraction",
)

_MAINSTREAM_TURISTBYER = (
    "paris",
    "roma",
    "rome",
    "barcelona",
    "amsterdam",
    "london",
    "berlin",
    "praha",
    "prague",
    "wien",
    "vienna",
    "venedig",
    "venice",
    "firenze",
    "florence",
    "istanbul",
    "dubrovnik",
    "santorini",
    "mykonos",
    "nice",
    "monaco",
    "madrid",
    "lisboa",
    "lisbon",
    "budapest",
    "københavn",
    "copenhagen",
    "stockholm",
    "oslo",
    "helsinki",
    "athen",
    "athens",
    "dublin",
    "edinburgh",
    "brugge",
    "bruges",
    "salzburg",
    "interlaken",
    "zermatt",
    "canary islands",
    "kanariøyene",
    "mallorca",
    "ibiza",
)

HELGEBY_MAKS_STORBY_RANG = 5

# Rang 1–5 per land (aliaser i hvert tuplenivå). Brukes til helgeby-filter.
_LAND_TOP_BYER: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "norge": (("oslo",), ("bergen",), ("trondheim",), ("stavanger",), ("kristiansand", "drammen")),
    "sverige": (("stockholm",), ("goteborg", "gothenburg", "göteborg"), ("malmo", "malmö"), ("uppsala",), ("linkoping", "linköping")),
    "danmark": (("kobenhavn", "copenhagen", "københavn"), ("aarhus", "århus"), ("odense",), ("aalborg",), ("esbjerg",)),
    "finland": (("helsinki", "helsingfors"), ("espoo", "esbo"), ("tampere",), ("vantaa", "vanda"), ("oulu", "uleåborg")),
    "island": (("reykjavik", "reykjavík"), ("kopavogur", "kópavogur"), ("hafnarfjordur", "hafnarfjörður"), ("akureyri",), ("reykjanesbaer", "reykjanesbær")),
    "tyskland": (("berlin",), ("hamburg",), ("munchen", "munich", "münchen"), ("koln", "cologne", "köln"), ("frankfurt", "frankfurt am main")),
    "frankrike": (("paris",), ("marseille", "marseilles"), ("lyon",), ("toulouse",), ("nice",)),
    "storbritannia": (("london",), ("birmingham",), ("manchester",), ("glasgow",), ("liverpool",)),
    "irland": (("dublin",), ("cork",), ("limerick",), ("galway",), ("waterford",)),
    "nederland": (("amsterdam",), ("rotterdam",), ("den haag", "the hague", "s-gravenhage"), ("utrecht",), ("eindhoven",)),
    "belgia": (("brussel", "brussels", "bruxelles"), ("antwerpen", "antwerp", "anvers"), ("gent", "ghent"), ("charleroi",), ("liege", "liège")),
    "luxembourg": (("luxembourg", "luxembourg city", "luxembourg by"), ("esch-sur-alzette", "esch sur alzette"), ("differdange",), ("dudelange",), ("petange", "pétange")),
    "spania": (("madrid",), ("barcelona",), ("valencia", "valència"), ("sevilla", "seville"), ("zaragoza", "saragossa")),
    "portugal": (("lisboa", "lisbon"), ("porto", "oporto"), ("braga",), ("coimbra",), ("funchal",)),
    "italia": (("roma", "rome"), ("milano", "milan"), ("napoli", "naples"), ("torino", "turin"), ("palermo",)),
    "hellas": (("athen", "athens", "athina"), ("thessaloniki", "thessalonica", "salonica"), ("patras", "patra"), ("heraklion", "iraklio"), ("larissa",)),
    "polen": (("warszawa", "warsaw"), ("krakow", "kraków", "cracow"), ("lodz", "łódź"), ("wroclaw", "wrocław"), ("poznan", "poznań")),
    "tsjekkia": (("praha", "prague"), ("brno",), ("ostrava",), ("plzen", "plzeň"), ("liberec",)),
    "slovakia": (("bratislava",), ("kosice", "košice"), ("presov", "prešov"), ("zilina", "žilina"), ("nitra",)),
    "ungarn": (("budapest",), ("debrecen",), ("szeged",), ("miskolc",), ("pecs", "pécs")),
    "osterrike": (("wien", "vienna"), ("graz",), ("linz",), ("salzburg",), ("innsbruck",)),
    "sveits": (("zurich", "zürich"), ("geneve", "genève", "geneva"), ("basel", "bâle"), ("bern", "berne"), ("lausanne",)),
    "romania": (("bucuresti", "bucharest"), ("cluj-napoca", "cluj"), ("timisoara", "timișoara"), ("iasi", "iași"), ("constanta", "constanța")),
    "bulgaria": (("sofia",), ("plovdiv",), ("varna",), ("burgas",), ("ruse", "rousse")),
    "kroatia": (("zagreb",), ("split",), ("rijeka",), ("osijek",), ("zadar",)),
    "serbia": (("beograd", "belgrade"), ("novi sad",), ("nis", "niš"), ("kragujevac",), ("subotica",)),
    "slovenia": (("ljubljana",), ("maribor",), ("celje",), ("kranj",), ("koper",)),
    "bosnia": (("sarajevo",), ("banja luka",), ("tuzla",), ("zenica",), ("mostar",)),
    "nord-makedonia": (("skopje",), ("bitola",), ("kumanovo",), ("prilep",), ("tetovo",)),
    "albania": (("tirana", "tiranë"), ("durres", "durrës"), ("vlore", "vlorë"), ("shkoder", "shkodër"), ("fier",)),
    "montenegro": (("podgorica",), ("niksic", "nikšić"), ("herceg novi",), ("pljevlja",), ("bar",)),
    "estland": (("tallinn",), ("tartu",), ("narva",), ("parnu", "pärnu"), ("kohtla-jarve", "kohtla-järve")),
    "latvia": (("riga",), ("daugavpils",), ("liepaja", "liepāja"), ("jelgava",), ("jurmala", "jūrmala")),
    "litauen": (("vilnius",), ("kaunas",), ("klaipeda", "klaipėda"), ("siauliai", "šiauliai"), ("panevezys", "panevėžys")),
    "ukraina": (("kyiv", "kiev"), ("kharkiv", "kharkov"), ("odesa", "odessa"), ("dnipro", "dnipropetrovsk"), ("lviv", "lvov", "lemberg")),
    "hviterussland": (("minsk",), ("homiel", "gomel"), ("mahilyow", "mogilev"), ("vitebsk",), ("hrodna", "grodno")),
    "moldova": (("chisinau", "chișinău"), ("tiraspol",), ("balti", "bălți"), ("bender", "tighina"), ("cahul",)),
    "malta": (("birkirkara",), ("mosta",), ("qormi",), ("zabbar", "żabbar"), ("sliema",)),
    "kypros": (("nicosia", "lefkosia"), ("limassol", "lemesos"), ("larnaca", "larnaka"), ("paphos", "pafos"), ("famagusta", "gazimagusa")),
    "tyrkia": (("istanbul", "constantinople"), ("ankara",), ("izmir", "smyrna"), ("bursa",), ("antalya",)),
    "georgia": (("tbilisi", "tiflis"), ("batumi",), ("kutaisi",), ("rustavi",), ("gori",)),
}

_LAND_NAVN_ALIASES: Dict[str, str] = {
    "norge": "norge", "norway": "norge", "no": "norge",
    "sverige": "sverige", "sweden": "sverige", "se": "sverige",
    "danmark": "danmark", "denmark": "danmark", "dk": "danmark",
    "finland": "finland", "suomi": "finland", "fi": "finland",
    "island": "island", "iceland": "island", "is": "island",
    "tyskland": "tyskland", "germany": "tyskland", "deutschland": "tyskland", "de": "tyskland",
    "frankrike": "frankrike", "france": "frankrike", "fr": "frankrike",
    "storbritannia": "storbritannia", "united kingdom": "storbritannia", "uk": "storbritannia",
    "great britain": "storbritannia", "england": "storbritannia", "scotland": "storbritannia", "wales": "storbritannia",
    "irland": "irland", "ireland": "irland", "eire": "irland", "ie": "irland",
    "nederland": "nederland", "netherlands": "nederland", "holland": "nederland", "nl": "nederland",
    "belgia": "belgia", "belgium": "belgia", "be": "belgia",
    "luxembourg": "luxembourg", "luxemburg": "luxembourg", "lu": "luxembourg",
    "spania": "spania", "spain": "spania", "espana": "spania", "españa": "spania", "es": "spania",
    "portugal": "portugal", "pt": "portugal",
    "italia": "italia", "italy": "italia", "it": "italia",
    "hellas": "hellas", "greece": "hellas", "gr": "hellas",
    "polen": "polen", "poland": "polen", "polska": "polen", "pl": "polen",
    "tsjekkia": "tsjekkia", "czech republic": "tsjekkia", "czechia": "tsjekkia", "cz": "tsjekkia",
    "slovakia": "slovakia", "slovak republic": "slovakia", "slovensko": "slovakia", "sk": "slovakia",
    "ungarn": "ungarn", "hungary": "ungarn", "magyarorszag": "ungarn", "hu": "ungarn",
    "osterrike": "osterrike", "østerrike": "osterrike", "austria": "osterrike", "osterreich": "osterrike", "österreich": "osterrike", "at": "osterrike",
    "sveits": "sveits", "switzerland": "sveits", "schweiz": "sveits", "suisse": "sveits", "ch": "sveits",
    "romania": "romania", "românia": "romania", "ro": "romania",
    "bulgaria": "bulgaria", "bg": "bulgaria",
    "kroatia": "kroatia", "croatia": "kroatia", "hrvatska": "kroatia", "hr": "kroatia",
    "serbia": "serbia", "srbija": "serbia", "rs": "serbia",
    "slovenia": "slovenia", "slovenija": "slovenia", "si": "slovenia",
    "bosnia": "bosnia", "bosnia and herzegovina": "bosnia", "bosna i hercegovina": "bosnia", "ba": "bosnia",
    "nord-makedonia": "nord-makedonia", "north macedonia": "nord-makedonia", "macedonia": "nord-makedonia", "mk": "nord-makedonia",
    "albania": "albania", "shqiperia": "albania", "al": "albania",
    "montenegro": "montenegro", "crna gora": "montenegro", "me": "montenegro",
    "estland": "estland", "estonia": "estland", "eesti": "estland", "ee": "estland",
    "latvia": "latvia", "latvija": "latvia", "lv": "latvia",
    "litauen": "litauen", "lithuania": "litauen", "lietuva": "litauen", "lt": "litauen",
    "ukraina": "ukraina", "ukraine": "ukraina", "ua": "ukraina",
    "hviterussland": "hviterussland", "belarus": "hviterussland", "by": "hviterussland",
    "moldova": "moldova", "md": "moldova",
    "malta": "malta", "mt": "malta",
    "kypros": "kypros", "cyprus": "kypros", "cy": "kypros",
    "tyrkia": "tyrkia", "turkey": "tyrkia", "turkiye": "tyrkia", "türkiye": "tyrkia", "tr": "tyrkia",
    "georgia": "georgia", "sakartvelo": "georgia", "ge": "georgia",
}

_LAND_TOP_BYER_NORMALISERT: Dict[str, Tuple[Set[str], ...]] = {}


def _normaliser_sted_navn(tekst: str) -> str:
    t = (tekst or "").strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s'-]", "", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _normaliser_land_navn(land: str) -> str:
    return _LAND_NAVN_ALIASES.get(_normaliser_sted_navn(land), _normaliser_sted_navn(land))


def _hent_land_top_byer_normalisert(land_key: str) -> Tuple[Set[str], ...]:
    if land_key not in _LAND_TOP_BYER_NORMALISERT:
        rader = []
        for rang in _LAND_TOP_BYER.get(land_key, ()):
            rader.append({_normaliser_sted_navn(alias) for alias in rang})
        _LAND_TOP_BYER_NORMALISERT[land_key] = tuple(rader)
    return _LAND_TOP_BYER_NORMALISERT[land_key]


_TURISTBY_SIGNAL = (
    "hovedstad",
    "capital city",
    "million turister",
    "million tourists",
    "million besøkende",
    "million visitors",
    "typisk turistdestinasjon",
    "classic tourist destination",
    "masseturisme",
    "mass tourism",
    "cruise port",
    "cruiseturist",
    "bucket list city",
    "must-see city",
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
        "million besøk": 3,
        "million visitors": 3,
        "mest besøkte museum": 4,
        "most visited museum": 4,
        "louvre": 4,
        "british museum": 4,
        "rijksmuseum": 3,
        "uffizi": 3,
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


def effektiv_kilde_type(sted: Dict) -> str:
    """type-felt (f.eks. overnatting) veier tyngre enn feil source_type i lagret JSON."""
    if not sted:
        return "hidden_gem"
    type_hint = (sted.get("type") or "").strip().lower()
    if type_hint in ("hotell", "hotel", "overnatting", "lodging"):
        return "hotel"
    if type_hint in ("gastronomi", "restaurant", "mat"):
        return "restaurant"
    return (sted.get("source_type") or "hidden_gem").strip().lower()


def er_ki_eller_agent_lagret(sted: Dict) -> bool:
    blob = f"{sted.get('tips', '')} {sted.get('beskrivelse', '')}".lower()
    return any(markor in blob for markor in _KI_LAGRET_MARKORER)


def er_agent_forslag(kandidat: Dict) -> bool:
    """KI-forslag fra chat, sanking eller helgeby (før tips er satt ved lagring)."""
    if kandidat.get("agent_id"):
        return True
    blob = f"{kandidat.get('navn', '')} {kandidat.get('beskrivelse', '')} {kandidat.get('id', '')}".lower()
    return "agent-" in blob or "ki-agent" in blob


def effektiv_saerhetsscore(sted: Dict) -> int:
    eksplisitt = _hent_lagret_saerhetsscore(sted)
    if eksplisitt is not None:
        return eksplisitt
    if er_kuratert_seed(sted):
        type_lc = (sted.get("type") or "").lower()
        if sted.get("source_type") == "hotel" or type_lc in (
            "hotell",
            "hotel",
            "overnatting",
            "lodging",
        ):
            return HOTELL_MIN_UNIKHETSGRAD
        if sted.get("source_type") == "restaurant" or type_lc in (
            "gastronomi",
            "restaurant",
            "mat",
        ):
            return MAT_MIN_UNIKHETSGRAD
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


def _er_museum_kontekst(sted: Dict, tekst: str) -> bool:
    type_lc = (sted.get("type") or "").lower()
    if type_lc in _MUSEUM_TYPER:
        return True
    return any(ordlyd in tekst for ordlyd in _MUSEUM_ORD)


def er_velbesokt_museum(sted: Dict) -> bool:
    """Velbesøkte/blockbuster-museer er ikke skjulte perler."""
    tekst = tekst_for_sted_sjekk(sted)
    if not _er_museum_kontekst(sted, tekst):
        return False
    if any(navn in tekst for navn in _KJENTE_MUSEER):
        return True
    return any(signal in tekst for signal in _MUSEUM_BESOK_SIGNAL)


# Bakoverkompatibel alias (ø i navn ga ImportError på noen Windows-oppsett)
er_velbesøkt_museum = er_velbesokt_museum


def er_mainstream_turistdestinasjon(sted: Dict) -> bool:
    """Stor, klassisk turistby — ikke skjult helgeperle."""
    navn = (sted.get("navn") or "").strip().lower()
    by = (sted.get("by") or navn).strip().lower()
    tekst = tekst_for_sted_sjekk(sted)
    if navn in _MAINSTREAM_TURISTBYER or by in _MAINSTREAM_TURISTBYER:
        return True
    type_lc = (sted.get("type") or "").lower()
    if type_lc in ("by", "helgeby", "town", "city") and any(
        signal in tekst for signal in _TURISTBY_SIGNAL
    ):
        return True
    return False


def er_blant_landets_storste_byer(sted: Dict, *, antall: int = HELGEBY_MAKS_STORBY_RANG) -> bool:
    """True hvis byen er blant landets N største (brukes for helgeby)."""
    land_key = _normaliser_land_navn(sted.get("land") or "")
    if not land_key or land_key not in _LAND_TOP_BYER:
        return False
    by = _normaliser_sted_navn(sted.get("by") or sted.get("navn") or "")
    if not by:
        return False
    for rang_set in _hent_land_top_byer_normalisert(land_key)[: max(1, antall)]:
        if by in rang_set:
            return True
    return False


def hotell_pris_innen_grense(sted: Dict, *, strict_unknown: bool = False) -> bool:
    """Bakoverkompatibel stub — makspris for overnatting er fjernet."""
    return True


def godkjent_hotel_kandidat(
    kandidat: Dict, strict_mode: bool = False, *, for_sank: bool = False
) -> bool:
    if kandidat.get("source_type") != "hotel":
        return True
    tekst = tekst_for_sted_sjekk(kandidat)
    if er_kjede_hotell(tekst):
        return False
    score = effektiv_saerhetsscore(kandidat)
    if er_ki_eller_agent_lagret(kandidat) or er_agent_forslag(kandidat):
        min_score = MIN_UNIKHETSGRAD
        min_besk = 15
    elif for_sank:
        min_score = MIN_UNIKHETSGRAD
        min_besk = SANK_HOTELL_MIN_BESKRIVELSE
    else:
        min_score = HOTELL_MIN_UNIKHETSGRAD
        min_besk = 35
    if score < min_score:
        return False
    if len((kandidat.get("beskrivelse") or "").strip()) < min_besk:
        return False
    return True


def godkjent_restaurant_kandidat(
    kandidat: Dict, strict_mode: bool = False, *, for_sank: bool = False
) -> bool:
    if kandidat.get("source_type") != "restaurant":
        return True
    tekst = tekst_for_sted_sjekk(kandidat)
    if er_kjede_restaurant(tekst):
        return False
    if kandidat.get("saerhetsscore", 0) < MAT_MIN_UNIKHETSGRAD:
        return False
    beskrivelse = (kandidat.get("beskrivelse") or "").strip()
    min_besk = SANK_RESTAURANT_MIN_BESKRIVELSE if for_sank else 40
    if len(beskrivelse) < min_besk:
        return False
    if strict_mode and not for_sank and not any(
        signal in tekst for signal in _RESTAURANT_UNIKHET_ORD
    ):
        return False
    return True


def _med_score(sted: Dict) -> Dict:
    return {**sted, "saerhetsscore": effektiv_saerhetsscore(sted)}


def oppfyller_visning_kriterier(sted: Dict, strict_mode: bool = False) -> bool:
    """Samme regler som KI-sanking, tilpasset kuratert vs. KI-lagret innhold."""
    med = _med_score(sted)
    source = effektiv_kilde_type(med)

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
    if er_velbesokt_museum(sted):
        return False
    if er_mainstream_turistdestinasjon(sted):
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
        if score < HOTELL_MIN_UNIKHETSGRAD:
            grunner.append(f"Unikhetsgrad {score} < {HOTELL_MIN_UNIKHETSGRAD} (overnatting)")
        if len((sted.get("beskrivelse") or "").strip()) < 35:
            grunner.append("For kort beskrivelse av overnatting")
    elif source == "restaurant":
        if er_kjede_restaurant(tekst_for_sted_sjekk(sted)):
            grunner.append("Restaurantkjede")
        if score < MAT_MIN_UNIKHETSGRAD:
            grunner.append(f"Unikhetsgrad {score} < {MAT_MIN_UNIKHETSGRAD} (mat)")
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
        if er_velbesokt_museum(sted):
            grunner.append("Velbesøkt museum (ikke skjult perle)")
        if er_mainstream_turistdestinasjon(sted):
            grunner.append("Mainstream turistby (ikke skjult helgeperle)")

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
