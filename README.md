# Hemmelige Europa

Streamlit-app for å oppdage skjulte perler, lokale spisesteder, radar-søk og reiseplan i Europa.

## Krav

- **Python 3.12** (anbefalt; unngå 3.14 på Windows — mange pakker mangler ferdigbygde hjul)
- Internett (Wikivoyage, Nominatim, valgfritt OpenRouter for KI)

## Kom i gang (Windows)

```powershell
cd "c:\Mine dokumenter\KI-App-prosjekter\Hemmelige Europa"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Kopier `.streamlit/secrets.example.toml` til `.streamlit/secrets.toml` og legg inn `OPENROUTER_API_KEY` hvis du vil bruke reiseekspert og KI-søk. Valgfritt: `BOOKING_AID` for affiliate-lenker.

Start appen:

```powershell
python -m streamlit run app.py
```

Åpne **http://localhost:8501** i nettleseren.

## Streamlit Cloud

- Pek appen mot `app.py`, `requirements.txt` og `runtime.txt` (Python 3.12)
- Sett secrets i Streamlit-dashboard (samme nøkler som i `secrets.example.toml`)
- Cron-ping: `?ping_cron` i URL vekker SQLite uten å laste hele UI-en

## Prosjektstruktur (kort)

| Fil | Innhold |
|-----|---------|
| `app.py` | Hoved-UI og faner |
| `persistence.py` | JSON-lagring av profil, chat og reiseplan |
| `ui_cards.py` | Stedskort, bilder og affiliate-lenker |
| `ui_panels.py` | KI-paneler (oppdagelses-søk) |
| `kart_utils.py` | Folium-kart og avstandsberegning |
| `database.py` | Kuraterte perler, mat og overnatting |
| `data_store.py` | SQLite og reiseplan |
| `place_images.py` | Bilder fra Wikimedia |
| `affiliate_links.py` | Booking, leiebil og matlevering-URL-er |
| `translations.py` | Norsk / engelsk |

## Forhåndslaste bilder (anbefalt)

For raskere visning uten Wikipedia-oppslag ved første besøk:

```powershell
python scripts/precache_images.py
```

Dette oppdaterer `data/preloaded_images.json`, som appen leser automatisk før live-oppslag.

## Tips

- Ikke committ `.venv/`, `venv/`, `reiseprofil.json` eller `.streamlit/secrets.toml` (se `.gitignore`).
- Databasefilen `hemmelige_europa.sqlite3` genereres lokalt ved første kjøring.
- Bilder lastes **lazy** som standard — slå på «Hent alle bilder» i sidemenyen for autoload.
