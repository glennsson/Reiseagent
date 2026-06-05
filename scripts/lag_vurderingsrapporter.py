import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_store import get_places
from place_quality import (
    MIN_UNIKHETSGRAD,
    vurder_eksisterende_sted,
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")


def _batch(items: List[Dict], size: int) -> List[List[Dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _parse_json_payload(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _vurder_med_ai(steder: List[Dict], batch_size: int = 30, retry_unknown: bool = True) -> Dict[str, Dict]:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        return {
            sted["id"]: {
                "score": None,
                "status": "ukjent",
                "begrunnelse": "OPENROUTER_API_KEY mangler; AI-vurdering kunne ikke kjøres.",
            }
            for sted in steder
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost:8501",
        "X-Title": "Hemmelige Europa Quality Review",
    }

    resultater: Dict[str, Dict] = {}
    for chunk in _batch(steder, max(1, batch_size)):
        kandidat_liste = [
            {
                "id": s["id"],
                "navn": s.get("navn", ""),
                "by": s.get("by", ""),
                "land": s.get("land", ""),
                "type": s.get("type", ""),
                "source_type": s.get("source_type", ""),
                "beskrivelse": s.get("beskrivelse", ""),
                "tips": s.get("tips", ""),
                "pris": s.get("pris", ""),
            }
            for s in chunk
        ]

        prompt = (
            "Vurder kvaliteten på hver perle for appen Hemmelige Europa (skjulte, unike steder). "
            f"Gi score 1-10. Status 'behold' hvis stedet er unikt og oppfyller: "
            f"unikhetsgrad >= {MIN_UNIKHETSGRAD}, "
            "ingen kjeder eller mainstream-turistfeller. Ellers 'vurder sletting'. "
            "Svar KUN gyldig JSON med format: "
            '{"vurderinger":[{"id":"...","score":9,"status":"behold","begrunnelse":"..."}]}.\n\n'
            f"Kandidater:\n{json.dumps(kandidat_liste, ensure_ascii=False)}"
        )
        payload = {
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 2400,
        }

        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            content = (
                resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            ).strip()
            parsed = _parse_json_payload(content)
            if not isinstance(parsed, dict):
                raise ValueError("AI-svar kunne ikke parses som JSON.")
            vurderinger = parsed.get("vurderinger", [])
            for v in vurderinger:
                sid = v.get("id")
                if sid:
                    score = v.get("score")
                    resultater[sid] = {
                        "score": int(score) if isinstance(score, (int, float)) else None,
                        "status": v.get("status", "ukjent"),
                        "begrunnelse": v.get("begrunnelse", ""),
                    }
        except Exception as e:
            for s in chunk:
                resultater[s["id"]] = {
                    "score": None,
                    "status": "ukjent",
                    "begrunnelse": f"AI-feil: {e}",
                }

    if retry_unknown:
        ukjente = [s for s in steder if resultater.get(s["id"], {}).get("status") == "ukjent"]
        for s in ukjente:
            kandidat_liste = [
                {
                    "id": s["id"],
                    "navn": s.get("navn", ""),
                    "by": s.get("by", ""),
                    "land": s.get("land", ""),
                    "type": s.get("type", ""),
                    "source_type": s.get("source_type", ""),
                    "beskrivelse": s.get("beskrivelse", ""),
                    "tips": s.get("tips", ""),
                    "pris": s.get("pris", ""),
                }
            ]
            prompt = (
                "Vurder kvaliteten på denne perlen for Hemmelige Europa. "
                f"Status 'behold' kun ved unikhetsgrad >= {MIN_UNIKHETSGRAD}. "
                "Svar KUN gyldig JSON med format: "
                '{"vurderinger":[{"id":"...","score":9,"status":"behold","begrunnelse":"..."}]}.\n\n'
                f"Kandidat:\n{json.dumps(kandidat_liste, ensure_ascii=False)}"
            )
            payload = {
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 500,
            }
            try:
                resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=45)
                resp.raise_for_status()
                content = (
                    resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                ).strip()
                parsed = _parse_json_payload(content)
                if not isinstance(parsed, dict):
                    continue
                vurderinger = parsed.get("vurderinger", [])
                if vurderinger:
                    v = vurderinger[0]
                    score = v.get("score")
                    resultater[s["id"]] = {
                        "score": int(score) if isinstance(score, (int, float)) else None,
                        "status": v.get("status", "ukjent"),
                        "begrunnelse": v.get("begrunnelse", ""),
                    }
            except Exception:
                continue

    for sted in steder:
        if sted["id"] not in resultater:
            resultater[sted["id"]] = {
                "score": None,
                "status": "ukjent",
                "begrunnelse": "Mangler AI-respons for denne raden.",
            }
    return resultater


def _skriv_rapport(path: str, tittel: str, steder: List[Dict], vurderinger: Dict[str, Dict]) -> None:
    linjer: List[str] = []
    linjer.append(f"{tittel}")
    linjer.append(f"Generert: {datetime.now().isoformat(timespec='seconds')}")
    linjer.append(
        f"Regler: min. unikhetsgrad {MIN_UNIKHETSGRAD}"
    )
    linjer.append(f"Antall steder: {len(steder)}")
    linjer.append("")

    behold = 0
    vurder_slett = 0
    ukjent = 0
    for s in steder:
        v = vurderinger.get(s["id"], {})
        status = v.get("status", "ukjent")
        if status == "behold":
            behold += 1
        elif status == "vurder sletting":
            vurder_slett += 1
        else:
            ukjent += 1
    linjer.append(f"Oppsummering: behold={behold}, vurder_sletting={vurder_slett}, ukjent={ukjent}")
    linjer.append("-" * 80)

    sortert = sorted(
        steder,
        key=lambda x: (
            (vurderinger.get(x["id"], {}).get("score") is None),
            -(vurderinger.get(x["id"], {}).get("score") or -1),
            x.get("land", ""),
            x.get("by", ""),
            x.get("navn", ""),
        ),
    )

    for idx, s in enumerate(sortert, start=1):
        v = vurderinger.get(s["id"], {})
        score = v.get("score")
        score_tekst = str(score) if score is not None else "N/A"
        linjer.append(
            f"{idx:03d}. {s.get('navn','')} ({s.get('by','')}, {s.get('land','')}) "
            f"[{s.get('source_type','')}/{s.get('type','')}]"
        )
        linjer.append(f"     Score: {score_tekst} | Status: {v.get('status','ukjent')}")
        if v.get("kilde"):
            linjer.append(f"     Kilde: {v.get('kilde')}")
        linjer.append(f"     Begrunnelse: {v.get('begrunnelse','')}")
        if (s.get("beskrivelse") or "").strip():
            linjer.append(f"     Beskrivelse: {s.get('beskrivelse','')}")
        linjer.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(linjer))


def main() -> None:
    parser = argparse.ArgumentParser(description="Lag vurderingsrapporter med og uten AI.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=30,
        help="Antall steder per AI-kall (lavere kan gi færre parse-feil).",
    )
    parser.add_argument(
        "--no-retry-unknown",
        action="store_true",
        help="Slå av ekstra enkeltrunde for ukjente AI-vurderinger.",
    )
    parser.add_argument(
        "--kun-regler",
        action="store_true",
        help="Generer kun regelbasert rapport (ingen AI-kall).",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env", override=True)
    steder = (
        get_places("hidden_gem")
        + get_places("restaurant")
        + get_places("hotel")
    )

    uten_ai = {s["id"]: vurder_eksisterende_sted(s) for s in steder}
    _skriv_rapport(
        ROOT / "vurdering_perler_uten_ai.txt",
        "PERLEVURDERING UTEN AI (samme regler som appen)",
        steder,
        uten_ai,
    )

    if args.kun_regler:
        print("Lagde fil: vurdering_perler_uten_ai.txt")
        return

    med_ai = _vurder_med_ai(
        steder,
        batch_size=max(1, args.batch_size),
        retry_unknown=not args.no_retry_unknown,
    )
    _skriv_rapport(
        ROOT / "vurdering_perler_med_ai.txt",
        "PERLEVURDERING MED AI",
        steder,
        med_ai,
    )
    print("Lagde filer: vurdering_perler_uten_ai.txt, vurdering_perler_med_ai.txt")


if __name__ == "__main__":
    main()
