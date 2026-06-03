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

Kopier `.streamlit/secrets.example.toml` til `.streamlit/secrets.toml` og legg inn `OPENROUTER_API_KEY` hvis du vil bruke reiseekspert og KI-søk.

Start appen:

```powershell
python -m streamlit run app.py
```

Åpne **http://localhost:8501** i nettleseren.

## Streamlit Cloud

- Pek appen mot `app.py` og `requirements.txt`
- Sett secrets i Streamlit-dashboard (samme nøkler som i `secrets.example.toml`)
- Bruk Python **3.12** i cloud-innstillinger hvis det kan velges

## Prosjektstruktur (kort)

| Fil | Innhold |
|-----|---------|
| `app.py` | Hoved-UI |
| `database.py` | Kuraterte perler og restauranter |
| `data_store.py` | SQLite og reiseplan |
| `place_images.py` | Bilder fra Wikimedia |
| `translations.py` | Norsk / engelsk |

## Tips

- Ikke committ `.venv/`, `venv/` eller `.streamlit/secrets.toml` (se `.gitignore`).
- Databasefilen `hemmelige_europa.sqlite3` genereres lokalt ved første kjøring.
