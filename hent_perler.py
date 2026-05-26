import requests
import json
import time
import os
from datetime import datetime

# --- INTELLIGENT SJEKK MOT BÅDE DATABASE OG GAMMEL TXT-FIL ---
EKSISTERENDE_NAVN = []

try:
    from database import SKJULTE_PERLER, LOKALE_SPISESTEDER
    for p in (SKJULTE_PERLER + LOKALE_SPISESTEDER):
        EKSISTERENDE_NAVN.append(p["navn"].lower().strip())
    print(f"🔄 Sjekker database.py... Fant {len(SKJULTE_PERLER) + len(LOKALE_SPISESTEDER)} steder.")
except Exception:
    print("⚠️ Kunne ikke lese database.py.")

arkiv_fil = "nye_globale_perler.txt"
txt_teller = 0
if os.path.exists(arkiv_fil):
    try:
        with open(arkiv_fil, "r", encoding="utf-8") as f:
            linjer = f.readlines()
            for linje in linjer:
                if '"navn":' in linje:
                    start = linje.find('"navn": "') + 9
                    slutt = linje.find('"', start)
                    if start > 8 and slutt > -1:
                        navn = linje[start:slutt].lower().strip()
                        if navn not in EKSISTERENDE_NAVN:
                            EKSISTERENDE_NAVN.append(navn)
                            txt_teller += 1
        print(f"📦 Sjekker {arkiv_fil}... Fant {txt_teller} globale steder du har lagret der fra før.")
    except Exception as e:
        print(f"⚠️ Kunne ikke lese eksisterende {arkiv_fil}: {e}")

print(f"🛡️ Total-skjold aktivert for {len(EKSISTERENDE_NAVN)} unike steder.\n")


def hent_wikivoyage_kategori(kategori_navn):
    """Henter alle hovedartikler fra en kategori på Wikivoyage"""
    url = "https://en.wikivoyage.org/w/api.php"
    perler = []
    
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{kategori_navn}",
        "cmlimit": "max",
        "format": "json"
    }
    
    headers = {
        "User-Agent": "HemmeligeEuropaReiseApp/1.0 (kontakt: glenn@example.com)"
    }
    
    response = requests.get(url, params=params, headers=headers).json()
    
    if "query" in response and "categorymembers" in response["query"]:
        for item in response["query"]["categorymembers"]:
            if ":" not in item["title"]:
                perler.append(item["title"])
                
    return perler


def hent_detaljer_og_koordinater(sted_navn):
    """Henter koordinater, land og sammendrag globalt med full tysk/engelsk/svensk/norsk Europa-ordbok"""
    url = "https://en.wikivoyage.org/w/api.php"
    
    params = {
        "action": "query",
        "prop": "coordinates|extracts",
        "titles": sted_navn,
        "exintro": True,
        "explaintext": True,
        "exchars": 300,
        "format": "json"
    }
    
    headers = {
        "User-Agent": "HemmeligeEuropaReiseApp/1.0 (kontakt: glenn@example.com)"
    }
    
    try:
        res = requests.get(url, params=params, headers=headers).json()
        pages = res["query"]["pages"]
        page_id = list(pages.keys())[0]
        page_data = pages[page_id]
        
        # Sjekker og henter koordinatene live fra API-et
        if "coordinates" in page_data:
            lat = page_data["coordinates"][0]["lat"]
            lon = page_data["coordinates"][0]["lon"]
            
            # Henter og renser beskrivelsen
            beskrivelse = page_data.get("extracts", "En fantastisk destinasjon oppdaget via Wikivoyage.")
            beskrivelse = beskrivelse.replace("\n", " ").strip()
            
            land = "Utlandet"
            
            # Fire-språklig geografisk ordbok for Europa
            land_dict = {
                "Germany": "Tyskland", "Deutschland": "Tyskland",
                "France": "Frankrike", "Frankreich": "Frankrike",
                "Italy": "Italia", "Italien": "Italia",
                "Spain": "Spania", "Spanien": "Spania",
                "United Kingdom": "Storbritannia", "Großbritannien": "Storbritannia", "UK": "Storbritannia", "England": "Storbritannia", "Scotland": "Storbritannia", "Wales": "Storbritannia",
                "Netherlands": "Nederland", "Niederlande": "Nederland", "Holland": "Nederland",
                "Belgium": "Belgia", "Belgien": "Belgia",
                "Switzerland": "Sveits", "Schweiz": "Sveits",
                "Austria": "Østerrike", "Österreich": "Østerrike",
                "Luxembourg": "Luxembourg", "Luxemburg": "Luxembourg",
                "Ireland": "Irland",
                "Norway": "Norge", "Norwegen": "Norge",
                "Sweden": "Sverige", "Schweden": "Sverige",
                "Denmark": "Danmark", "Dänemark": "Danmark",
                "Finland": "Finland", "Finnland": "Finland",
                "Iceland": "Island",
                "Poland": "Polen",
                "Czech Republic": "Tsjekkia", "Czechia": "Tsjekkia", "Tschechien": "Tsjekkia", "Tjeckien": "Tsjekkia",
                "Slovakia": "Slovakia", "Slowakei": "Slovakia",
                "Hungary": "Ungarn", "Ungern": "Ungarn",
                "Romania": "Romania", "Rumänien": "Romania",
                "Bulgaria": "Bulgaria", "Bulgarien": "Bulgaria",
                "Ukraine": "Ukraina",
                "Belarus": "Belarus", "Weißrussland": "Belarus", "Hvitrussland": "Belarus",
                "Moldova": "Moldova", "Moldawien": "Moldova",
                "Estonia": "Estland",
                "Latvia": "Latvia", "Lettland": "Latvia",
                "Lithuania": "Litauen", "Litauen": "Litauen",
                "Portugal": "Portugal",
                "Greece": "Hellas", "Griechenland": "Hellas", "Grekland": "Hellas",
                "Croatia": "Kroatia", "Kroatien": "Kroatia", "Croatia": "Kroatia",
                "Slovenia": "Slovenia", "Slowenien": "Slovenia",
                "Serbia": "Serbia", "Serbien": "Serbia",
                "Montenegro": "Montenegro",
                "Bosnia": "Bosnia og Hercegovina", "Bosnien": "Bosnia og Hercegovina", "Bosnia and Herzegovina": "Bosnia og Hercegovina",
                "Albania": "Albania", "Albanien": "Albania",
                "North Macedonia": "Nord-Makedonia", "Nordmazedonien": "Nord-Makedonia", "North Macedonia": "Nord-Makedonia", "Nordmakedonien": "Nord-Makedonia",
                "Kosovo": "Kosovo",
                "Malta": "Malta",
                "Cyprus": "Kypros", "Zypern": "Kypros", "Cyprus": "Kypros", "Cypern": "Kypros",
                "Monaco": "Monaco", "Andorra": "Andorra", "San Marino": "San Marino", "Liechtenstein": "Liechtenstein",
                "Vatican City": "Vatikanstaten", "Vatikanstadt": "Vatikanstaten",
                "Turkey": "Tyrkia", "Türkei": "Tyrkia", "Turkiet": "Tyrkia",
                "Georgia": "Georgia", "Georgien": "Georgia",
                "Armenia": "Armenia", "Armenien": "Armenia",
                "Azerbaijan": "Aserbajdsjan", "Aserbaidschan": "Aserbajdsjan", "Azerbaijan": "Aserbajdsjan"
            }
            
            for fremmed_land, norsk_land in land_dict.items():
                if fremmed_land in beskrivelse or fremmed_land in sted_navn:
                    land = norsk_land
                    break

            return {
                "navn": sted_navn,
                "by": sted_navn,
                "land": land,
                "type": "eventyr" if "nature" in beskrivelse.lower() or "park" in beskrivelse.lower() else "kultur",
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "beskrivelse": beskrivelse if len(beskrivelse) > 10 else f"Skjult perle: {sted_navn}.",
                "tips": "Sjekk Wikivoyage eller lokale medier for detaljer.",
                "pris": "€"
            }
    except Exception:
        return None
    return None


# --- HOVEDPROSESSERING ---
if __name__ == "__main__":
    stedsliste = hent_wikivoyage_kategori("Previously Off the beaten path")
    totalt_antall = len(stedsliste)
    print(f"Fant totalt {totalt_antall} steder på Wikivoyage-listen globalt.")
    
    nye_globale_perler = []
    hoppet_over_teller = 0
    
    print("\nStarter skanning med dobbel duplikatsjekk (DB + TXT)...")
    for i, sted in enumerate(stedsliste):
        wiki_navn_lav = sted.lower().strip()
        
        allerede_finnes = False
        for eksisterende in EKSISTERENDE_NAVN:
            if wiki_navn_lav in eksisterende or eksisterende in wiki_navn_lav:
                allerede_finnes = True
                break
                
        if allerede_finnes:
            hoppet_over_teller += 1
            continue
            
        data = hent_detaljer_og_koordinater(sted)
        if data:
            nye_globale_perler.append(data)
            print(f"[{i+1}/{totalt_antall}] 🟢 Hentet NYTT sted: {data['navn']} ({data['land']})")
        
        time.sleep(0.1)
        
    # --- SMART LAGRING ---
    if nye_globale_perler:
        naa = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        eksisterende_linjer_innhold = []
        if os.path.exists(arkiv_fil):
            with open(arkiv_fil, "r", encoding="utf-8") as f:
                for linje in f.readlines():
                    if "{" in linje and '"navn"' in linje:
                        eksisterende_linjer_innhold.append(linje.rstrip())

        with open(arkiv_fil, "w", encoding="utf-8") as f:
            f.write(f"# ========================================================\n")
            f.write(f"# 📅 SIST OPPDATERT: {naa}\n")
            f.write(f"# Inneholder gamle globale steder + {len(nye_globale_perler)} nye funn.\n")
            f.write(f"# ========================================================\n\n")
            f.write("NYE_PERLER = [\n")
            
            for gammel_linje in eksisterende_linjer_innhold:
                f.write(f"{gammel_linje}\n")
                
            for p in nye_globale_perler:
                f.write(f"    {json.dumps(p, ensure_ascii=False)},\n")
                
            f.write("]\n")
            
        print(f"\n🎉 Kjøring fullført ({naa})!")
        print(f"Skippet {hoppet_over_teller} steder fordi de fantes i DB eller TXT.")
        print(f"La til {len(nye_globale_perler)} helt nye funn inn i '{arkiv_fil}'.")
    else:
        print(f"\n✅ Alt er helt ajour! Ingen nye publiseringer funnet på Wikivoyage siden sist.")