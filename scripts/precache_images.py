"""Forhåndslaster Wikimedia-URL-er til data/preloaded_images.json (kjør ved behov)."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import LOKALE_SPISESTEDER, SKJULTE_PERLER
from data_store import normalize_place
from place_images import hent_sted_bilde_url

OUT = ROOT / "data" / "preloaded_images.json"
PAUSE_SEC = 0.25


def _lagre(out):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    out = {}
    if OUT.is_file():
        out = json.loads(OUT.read_text(encoding="utf-8"))
    kilder = [(SKJULTE_PERLER, "hidden_gem"), (LOKALE_SPISESTEDER, "restaurant")]
    for liste, source_type in kilder:
        for rå in liste:
            sted = normalize_place(rå, source_type)
            pid = sted["id"]
            if pid in out:
                continue
            url = hent_sted_bilde_url(sted)
            if url:
                out[pid] = url
                print("OK", len(out))
            else:
                print("SKIP", sted["navn"].encode("ascii", "replace").decode())
            if len(out) % 10 == 0:
                _lagre(out)
            time.sleep(PAUSE_SEC)

    _lagre(out)
    print("Lagret", len(out), "bilder")


if __name__ == "__main__":
    main()
