"""One-shot fetcher for trainer sprites generated via PixelLab.

Reads ``data/trainer_sprites.json`` (title → character_id manifest)
and downloads each character's south-facing rotation into
``data/trainer_sprites/<slug>.png``. Idempotent — skips files that
already exist locally with non-zero size.

Run once after generating new sprites:

    uv run python scripts/download_trainer_sprites.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "data" / "trainer_sprites.json"
OUT_DIR = REPO_ROOT / "data" / "trainer_sprites"
URL_TMPL = (
    "https://backblaze.pixellab.ai/file/pixellab-characters/"
    "43cc5986-530e-4316-9ca9-81a7cc4f1c2d/{character_id}/rotations/south.png"
)

# Title → on-disk slug. Mirrors trainers_remote._TITLE_SLUGS so the
# fetched files line up with what the loader expects.
TITLE_SLUGS: dict[str, str] = {
    "Bug Catcher": "bug-catcher",
    "Lass": "lass",
    "Youngster": "youngster",
    "Hiker": "hiker",
    "Fisherman": "fisherman",
    "Picnicker": "picnicker",
    "Camper": "camper",
    "Bird Keeper": "bird-keeper",
    "Sailor": "sailor",
    "Engineer": "engineer",
    "Beauty": "beauty",
    "Gentleman": "gentleman",
    "Schoolkid": "schoolkid",
    "Black Belt": "black-belt",
    "PokéManiac": "pokemaniac",
    "Psychic": "psychic",
    "Channeler": "channeler",
    "Tamer": "tamer",
    "Burglar": "burglar",
    "Rocker": "rocker",
}


def main() -> int:
    if not MANIFEST.exists():
        print(f"manifest missing: {MANIFEST}", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    payload = json.loads(MANIFEST.read_text())
    chars: dict[str, str] = payload.get("characters", {})

    failed: list[str] = []
    for title, char_id in chars.items():
        slug = TITLE_SLUGS.get(title)
        if slug is None:
            print(f"!! no slug for {title!r}; skipping", file=sys.stderr)
            failed.append(title)
            continue
        target = OUT_DIR / f"{slug}.png"
        if target.exists() and target.stat().st_size > 0:
            print(f"== {slug}.png already present")
            continue
        url = URL_TMPL.format(character_id=char_id)
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "tokenmon-sprite-fetcher/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                target.write_bytes(resp.read())
            print(f"++ {slug}.png ({target.stat().st_size} bytes)")
        except Exception as exc:
            print(f"!! {slug}.png failed: {exc}", file=sys.stderr)
            failed.append(title)

    if failed:
        print(f"\n{len(failed)} failed: {failed}", file=sys.stderr)
        return 2
    print(f"\nAll {len(chars)} sprites downloaded to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
