from pathlib import Path


def main() -> None:
    src = Path("vurdering_perler_med_ai.txt")
    dst = Path("vurdering_perler_ai_vurder_sletting.txt")

    if not src.exists():
        raise FileNotFoundError(f"Fant ikke {src}")

    lines = src.read_text(encoding="utf-8").splitlines()

    flagged_blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "Status: vurder sletting" in line:
            # Finn start av blokken (nærmeste linje oppover som ser ut som "001. ...")
            start = i
            while start > 0 and not (
                len(lines[start]) >= 5
                and lines[start][:3].isdigit()
                and lines[start][3] == "."
            ):
                start -= 1

            # Finn slutten av blokken (blanklinje)
            end = i
            while end + 1 < len(lines) and lines[end + 1].strip():
                end += 1

            flagged_blocks.append("\n".join(lines[start : end + 1]))
            i = end + 1
            continue
        i += 1

    body = []
    body.append("PERLEVURDERING MED AI — KUN 'VURDER SLETTING'")
    body.append(f"Kildefil: {src.name}")
    body.append(f"Antall flaggede steder: {len(flagged_blocks)}")
    body.append("-" * 80)
    body.append("")
    body.extend(flagged_blocks)

    dst.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"Lagde fil: {dst} ({len(flagged_blocks)} steder)")


if __name__ == "__main__":
    main()
