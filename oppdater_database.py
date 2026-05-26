import json
import os
import re

# --- FIRE-SPRÅKLIG TOTAL-LISTE (NORSK, SVENSK, ENGELSK, TYSK) ---
EUROPEISKE_LAND = [
    # Vest-, Sentral- og Sør-Europa
    "Tyskland", "Deutschland", "Germany", "Tyskland",
    "Frankrike", "Frankreich", "France", "Frankrike",
    "Italia", "Italien", "Italy", "Italien",
    "Spania", "Spanien", "Spain", "Spanien",
    "Storbritannia", "Großbritannien", "United Kingdom", "UK", "England", "Skottland", "Wales",
    "Nederland", "Niederlande", "Netherlands", "Holland",
    "Belgia", "Belgien", "Belgium", "Belgien",
    "Sveits", "Schweiz", "Switzerland", "Sveits",
    "Østerrike", "Österreich", "Austria", "Österrike",
    "Luxembourg", "Luxemburg",
    "Irland", "Ireland",
    "Portugal",

    # Norden
    "Norge", "Norwegen", "Norway", "Norge",
    "Sverige", "Schweden", "Sweden", "Sverige",
    "Danmark", "Dänemark", "Denmark", "Danmark",
    "Finland", "Finnland", "Finland",
    "Island", "Iceland",

    # Baltikum
    "Estland", "Estonia",
    "Latvia", "Lettland",
    "Litauen", "Lithuania",

    # Øst- og Sentral-Europa
    "Polen", "Poland",
    "Tsjekkia", "Tschechien", "Czechia", "Czech Republic", "Tjeckien",
    "Slovakia", "Slowakei",
    "Ungarn", "Hungary", "Ungern",
    "Romania", "Rumänien",
    "Bulgaria", "Bulgarien",
    "Ukraina", "Ukraine",
    "Belarus", "Weißrussland", "Hvitrussland",
    "Moldova", "Moldawien",

    # 📍 BALKAN & HELLAS
    "Kroatia", "Kroatien", "Croatia", "Kroatien",
    "Hellas", "Griechenland", "Greece", "Grekland",
    "Slovenia", "Slowenien",
    "Serbia", "Serbien",
    "Montenegro",
    "Bosnia og Hercegovina", "Bosnien", "Bosnia", "Bosnia and Herzegovina",
    "Albania", "Albanien",
    "Nord-Makedonia", "Nordmazedonien", "North Macedonia", "Nordmakedonien",
    "Kosovo",

    # Mikrostater og øyer
    "Malta",
    "Kypros", "Zypern", "Cyprus", "Cypern",
    "Monaco",
    "Andorra",
    "San Marino",
    "Liechtenstein",
    "Vatikanstaten", "Vatikanstadt", "Vatican City",

    # Kaukasus og grenseland
    "Tyrkia", "Türkei", "Turkey", "Turkiet",
    "Georgia", "Georgien",
    "Armenia", "Armenien",
    "Aserbajdsjan", "Aserbaidschan", "Azerbaijan"
]

txt_fil = "nye_globale_perler.txt"
db_fil = "database.py"

if not os.path.exists(txt_fil):
    print(f"❌ Fant ikke filen '{txt_fil}'. Kjør 'hent_perler.py' først!")
    exit()

# --- 1. LES OG PARS STEDENE FRA TXT-FILEN ---
europeiske_funn = []
behold_globale_steder = []

print("📂 Analyserer nye_globale_perler.txt etter europeiske skatter...")

try:
    with open(txt_fil, "r", encoding="utf-8") as f:
        innhold = f.read()
        
    # Siden txt-filen inneholder JSON-aktige linjer, bruker vi en regex-sjekk for å lese hver ordbok { ... }
    sted_blokker = re.findall(r'(\{.*?\})', innhold)
    
    for blokk in sted_blokker:
        try:
            # Gjør om tekst-blokken til et ekte Python-objekt (dictionary)
            p = json.loads(blokk)
            
            # Sjekk om stedet tilhører et av dine land
            if p.get("land") in EUROPEISKE_LAND:
                europeiske_funn.append(p)
            else:
                behold_globale_steder.append(p)
        except Exception:
            continue
except Exception as e:
    print(f"❌ Kunne ikke lese tekstfilen: {e}")
    exit()

if not europeiske_funn:
    print("✅ Ingen nye europeiske steder ble funnet i tekstfilen denne gangen. Alt er i orden!")
    exit()

print(f"✨ Fant {len(europeiske_funn)} nye europeiske steder som skal inn i databasen!")


# --- 2. INNESKETING I DATABASE.PY ---
print(f"📝 Oppdaterer {db_fil}...")

try:
    with open(db_fil, "r", encoding="utf-8") as f:
        db_innhold = f.read()
        
    # Vi finner posisjonen til den siste lukkede firkantparentesen for SKJULTE_PERLER
    # Siden det kan være flere lister (f.eks. LOKALE_SPISESTEDER), må vi finne akkurat slutten av SKJULTE_PERLER
    # Vi søker etter mønsteret der SKJULTE_PERLER avsluttes rett før LOKALE_SPISESTEDER eller filslutt.
    
    # En trygg måte i ditt oppsett: Vi finner hvor SKJULTE_PERLER slutter ved å lete etter
    # den siste parantesen før LOKALE_SPISESTEDER starter
    if "LOKALE_SPISESTEDER" in db_innhold:
        dele_punkt = db_innhold.find("LOKALE_SPISESTEDER")
        forste_del = db_innhold[:dele_punkt]
        siste_del = db_innhold[dele_punkt:]
        
        # Finn den siste ] i første del (som lukker SKJULTE_PERLER)
        siste_bracket = forste_del.rfind("]")
    else:
        # Hvis LOKALE_SPISESTEDER ikke er der, lukkes den helt i bunnen av filen
        first_del = db_innhold
        siste_del = ""
        siste_bracket = db_innhold.rfind("]")

    if siste_bracket == -1:
        print("❌ Kunne ikke navigere i strukturen til database.py. Gjør det manuelt i stedet.")
        exit()
        
    # Bygg koden for de nye linjene som skal limes inn
    nye_linjer_kode = ""
    for p in europeiske_funn:
        # Formaterer det pent med innrykk på 4 mellomrom
        nye_linjer_kode += "    " + json.dumps(p, ensure_ascii=False) + ",\n"
        
    # Sett sammen den nye database-filen
    # Vi legger til et komma foran de nye linjene for å sikre syntaksen, og lukker med ] igjen
    ny_forste_del = forste_del[:siste_bracket].rstrip()
    
    # Sjekk om forrige sted sluttet med et komma. Hvis ikke, legg det til.
    if not ny_forste_del.endswith(","):
        ny_forste_del += ","
        
    oppdatert_db_tekst = f"{ny_forste_del}\n{nye_linjer_kode}]\n{siste_del}"
    
    # Lagre den oppdaterte database.py
    with open(db_fil, "w", encoding="utf-8") as f:
        f.write(oppdatert_db_tekst)
        
    print(f"🎉 Suksess! Følgende steder ble lagt til i {db_fil}:")
    for p in europeiske_funn:
        print(f"  - {p['navn']} ({p['land']})")

except Exception as e:
    print(f"❌ En feil oppstod under skriving til database.py: {e}")
    exit()


# --- 3. RENS NYE_GLOBALE_PERLER.TXT (BEHOLD KUN UTENLANDSKE SKJOLD) ---
try:
    from datetime import datetime
    naa = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(txt_fil, "w", encoding="utf-8") as f:
        f.write(f"# ========================================================\n")
        f.write(f"# 📅 OPPDATERT AV AUTOMATISK SKRIPT: {naa}\n")
        f.write(f"# Europeiske steder er flyttet til database.py.\n")
        f.write(f"# {len(behold_globale_steder)} steder står igjen som 'skjold'.\n")
        f.write(f"# ========================================================\n\n")
        f.write("NYE_PERLER = [\n")
        for p in behold_globale_steder:
            f.write(f"    {json.dumps(p, ensure_ascii=False)},\n")
        f.write("]\n")
    print(f"🧹 Renset '{txt_fil}'. De ikke-europeiske stedene står igjen for å blokkere fremtidige nedlastinger.")

except Exception as e:
    print(f"⚠️ Kunne ikke rense tekstfilen ordentlig: {e}")

