import json
import sys
import time
import os
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

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

from wikivoyage_client import hent_artikkel_med_koordinater, hent_kategori_medlemmer


def hent_wikivoyage_kategori(kategori_navn):
    """Henter alle hovedartikler fra en kategori på Wikivoyage."""
    return hent_kategori_medlemmer(kategori_navn, limit=500, pause=0.1)


def hent_detaljer_og_koordinater(sted_navn):
    """Henter koordinater, land og sammendrag fra Wikivoyage."""
    data = hent_artikkel_med_koordinater(sted_navn, pause=0.1)
    if not data:
        return None
    besk = (data.get("beskrivelse") or "").lower()
    if "nature" in besk or "park" in besk:
        data["type"] = "eventyr"
    return data


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