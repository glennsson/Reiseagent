"""Verifiser at alle place_quality-imports i prosjektet matcher eksporterte navn."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCAN_FILES = [
    ROOT / "app.py",
    ROOT / "data_store.py",
    ROOT / "scripts" / "lag_vurderingsrapporter.py",
    ROOT / "scripts" / "hent_wikivoyage.py",
]


def _hent_importnavn(fil: Path) -> list[tuple[str, str]]:
    treff: list[tuple[str, str]] = []
    tree = ast.parse(fil.read_text(encoding="utf-8"), filename=str(fil))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "place_quality":
            for alias in node.names:
                treff.append((str(fil.relative_to(ROOT)), alias.name))
    return treff


def main() -> int:
    import place_quality as pq

    eksportert = set(getattr(pq, "__all__", []))
    eksportert.update(
        n
        for n in dir(pq)
        if not n.startswith("__") and not n.startswith("_") or n == "_RESTAURANT_STERKE_ORD"
    )

    onskede = []
    for fil in SCAN_FILES:
        if fil.exists():
            onskede.extend(_hent_importnavn(fil))

    mangler = []
    for kilde, navn in onskede:
        if not hasattr(pq, navn):
            mangler.append((kilde, navn))

    print("Eksporterte konstanter i place_quality:")
    for n in sorted(
        n
        for n in dir(pq)
        if n.isupper() and not n.startswith("_") or n.startswith("HOTELL_") or n.startswith("SANK_") or n.startswith("MAT_") or n.startswith("MIN_")
    ):
        if hasattr(pq, n) and isinstance(getattr(pq, n), (int, float, str, type(None))):
            print(f"  {n} = {getattr(pq, n)!r}")

    print(f"\nSjekker {len(onskede)} import(er) fra {len(SCAN_FILES)} filer...")
    if mangler:
        print("FEIL — manglende navn:")
        for kilde, navn in mangler:
            print(f"  {kilde}: {navn}")
        return 1

    print("OK — alle place_quality-imports finnes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
