import requests
import json
import time
import os
from datetime import datetime

# --- DEN KOMPLETTE TOTAL-LISTEN (ALLE TV-SERIER + ATLAS OBSCURA) ---
TV_STEDER = [
    # 🇬🇧 Storbritannia / Skottland / Wales (Fra sesong 1-3 & Glemte ingeniørbragder)
    {"navn": "Maunsell Forts", "land": "Storbritannia"},
    {"navn": "Orford Ness", "land": "Storbritannia"},
    {"navn": "Imber", "land": "Storbritannia"},
    {"navn": "Bannerman's Castle", "land": "Storbritannia"},
    {"navn": "Tyneham", "land": "Storbritannia"},
    {"navn": "Stroma, Scotland", "land": "Storbritannia"},
    {"navn": "Scapa Flow", "land": "Storbritannia"},
    {"navn": "Kelvedon Hatch Secret Nuclear Bunker", "land": "Storbritannia"},
    {"navn": "Dunmore Pineapple", "land": "Storbritannia"},
    {"navn": "Pluckley", "land": "Storbritannia"},
    {"navn": "The Forbidden Corner", "land": "Storbritannia"},
    
    # 🇩🇪 Tyskland (Fra Geheime Orte, Lost Places & Kalte Krieges)
    {"navn": "Beelitz-Heilstätten", "land": "Tyskland"},
    {"navn": "Prora", "land": "Tyskland"},
    {"navn": "Teufelsberg", "land": "Tyskland"},
    {"navn": "Wünsdorf", "land": "Tyskland"},
    {"navn": "Spreepark", "land": "Tyskland"},
    {"navn": "Regierungsbunker", "land": "Tyskland"},
    {"navn": "Sankt Nikolai, Hamburg", "land": "Tyskland"},
    {"navn": "Heeresversuchsanstalt Peenemünde", "land": "Tyskland"},
    {"navn": "Vogelsang Ordensburg", "land": "Tyskland"},
    {"navn": "Waldspirale", "land": "Tyskland"},
    
    # 🇵🇱 Polen (Fra Secret Nazi Ruins & Atlas Obscura)
    {"navn": "Project Riese", "land": "Polen"},
    {"navn": "Wieliczka Salt Mine", "land": "Polen"},
    {"navn": "Crooked Forest", "land": "Polen"},
    {"navn": "Kaplica Czaszek", "land": "Polen"},
    {"navn": "Wilcze Gardło", "land": "Polen"},
    
    # 🇫🇷 Frankrike / 🇪🇸 Spania / 🇮🇹 Italia (Atlas Obscura-spesialer)
    {"navn": "Palais Idéal", "land": "Frankrike"},
    {"navn": "Setenil de las Bodegas", "land": "Spania"},
    {"navn": "Reschensee", "land": "Italia"}
]

def hent_wikipedia_data(sted_navn, land_navn):
    """Slår opp stedet på Wikipedia for å hente GPS-koordinater og brødtekst"""
    spraak = "de" if land_navn == "Tyskland" else "en"
    url = f"https://{spraak}.wikipedia.org/w/api.php"
    
    params = {
        "action": "query",
        "prop": "coordinates|extracts",
        "titles": sted_navn,
        "exintro": True,
        "explaintext": True,
        "exchars": 250,
        "format": "json"
    }
    
    headers = {"User-Agent": "HemmeligeEuropaReiseApp/1.0 (kontakt: glenn@example.com)"}
    
    try:
        res = requests.get(url, params=params, headers=headers).json()
        pages = res["query"]["pages"]
        page_id = list(pages.keys())[0]
        page_data = pages[page_id]
        
        if "coordinates" in page_data:
            lat = page_data["coordinates"][0]["lat"]
            lon = page_data["coordinates"][0]["lon"]
            
            beskrivelse = page_data.get("extracts", "Fantastisk historisk sted kjent fra TV.").replace("\n", " ").strip()
            
            return {
                "navn": sted_navn.split(",")[0],
                "by": sted_navn.split(",")[0],
                "land": land_navn,
                "type": "kultur",
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "beskrivelse": beskrivelse if len(beskrivelse) > 10 else f"Historisk perle i {land_navn}.",
                "tips": "Kjent fra TV-dokumentarer om sære og forlatte steder.",
                "pris": "€"
            }
    except Exception:
        return None
    return None

if __name__ == "__main__":
    print(f"📺 Starter skanning av {len(TV_STEDER)} utvalgte steder fra TV-serier...")
    print("-" * 60)
    
    tv_perler = []
    
    for i, sted_info in enumerate(TV_STEDER):
        navn = sted_info["navn"]
        land = sted_info["land"]
        
        print(f"[{i+1}/{len(TV_STEDER)}] Slår opp koordinater for: {navn} ({land})...")
        data = hent_wikipedia_data(navn, land)
        
        if data:
            tv_perler.append(data)
            print(f"   🟢 Suksess! Fant GPS: {data['latitude']}, {data['longitude']}")
        else:
            print(f"   ❌ Fant ikke GPS-data for dette navnet på Wikipedia.")
            
        time.sleep(0.1)

    # --- AUTOMATISK INNSETTING I DATABASE.PY ---
    db_fil = "database.py"
    
    if tv_perler and os.path.exists(db_fil):
        print("-" * 60)
        print(f"📝 Kobler til {db_fil} for automatisk overføring...")
        
        try:
            with open(db_fil, "r", encoding="utf-8") as f:
                db_innhold = f.read()
            
            # Sjekker hvilke steder som IKKE finnes i db fra før for å unngå duplikater
            nye_steder_å_legge_til = []
            for p in tv_perler:
                if p["navn"].lower() not in db_innhold.lower():
                    nye_steder_å_legge_til.append(p)
            
            if not nye_steder_å_legge_til:
                print("✅ Alle disse TV-stedene ligger allerede inne i database.py fra før!")
                exit()
                
            # Finner slutten på SKJULTE_PERLER-listen før eventuelt neste liste starter
            if "LOKALE_SPISESTEDER" in db_innhold:
                dele_punkt = db_innhold.find("LOKALE_SPISESTEDER")
                forste_del = db_innhold[:dele_punkt]
                siste_del = db_innhold[dele_punkt:]
                siste_bracket = forste_del.rfind("]")
            else:
                forste_del = db_innhold
                siste_del = ""
                siste_bracket = db_innhold.rfind("]")

            if siste_bracket != -1:
                # Bygg koden for de nye linjene
                nye_linjer_kode = ""
                for p in nye_steder_å_legge_til:
                    nye_linjer_kode += "    " + json.dumps(p, ensure_ascii=False) + ",\n"
                
                ny_forste_del = forste_del[:siste_bracket].rstrip()
                if not ny_forste_del.endswith(","):
                    ny_forste_del += ","
                
                oppdatert_db_tekst = f"{ny_forste_del}\n{nye_linjer_kode}]\n{siste_del}"
                
                # Skriv tilbake til database.py
                with open(db_fil, "w", encoding="utf-8") as f:
                    f.write(oppdatert_db_tekst)
                
                print(f"🎉 Suksess! La til {len(nye_steder_å_legge_til)} nye TV-perler rett inn i {db_fil}!")
                for p in nye_steder_å_legge_til:
                    print(f"  - {p['navn']} ({p['land']})")
            else:
                print("⚠️ Kunne ikke navigere i database.py-strukturen automatisk.")
                
        except Exception as e:
            print(f"❌ En feil oppstod under skriving til database.py: {e}")
    else:
        print(f"⚠️ Fant ikke '{db_fil}' i mappen. Ingen steder ble flyttet.")