"""
download_portraits.py — downloads all hero portraits for recognition (Phase 2).

Takes hero machine names from data/heroes.json (already fetched),
downloads horizontal portraits from the official Valve CDN into data/portraits/.
Run once; rerunning only fetches the missing ones (skip-existing).

If a new hero ships / Valve changes icons — delete data/portraits/ and run again.

URL formats (machine name = name without the npc_dota_hero_ prefix):
  main:    .../dota_react/heroes/{short}.png        (vertical "react" portrait)
  wide:    .../apps/dota2/images/heroes/{short}_lg.png (horizontal, like in the draft top bar)
We download both types — _lg is closest to what's shown in the top row of picks.
"""

import sys
import json
import time
from pathlib import Path

import requests

DATA_DIR = Path(__file__).parent / "data"
PORTRAITS_DIR = DATA_DIR / "portraits"
HEROES_JSON = DATA_DIR / "heroes.json"

# Several CDN options. We try them in turn until one returns an image.
# {short} — machine name without the prefix; {kind} — portrait type.
URL_TEMPLATES = [
    "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/{short}.png",
    "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/heroes/{short}_lg.png",
    "https://cdn.dota2.com/apps/dota2/images/dota_react/heroes/{short}.png",
    "https://cdn.dota2.com/apps/dota2/images/heroes/{short}_lg.png",
]

# Separately download the horizontal _lg as a second file (handy for matching the top bar).
LG_TEMPLATES = [
    "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/heroes/{short}_lg.png",
    "https://cdn.dota2.com/apps/dota2/images/heroes/{short}_lg.png",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (DraftHelper portrait fetcher)"}


def short_name(machine_name: str) -> str:
    """npc_dota_hero_storm_spirit -> storm_spirit"""
    return machine_name.replace("npc_dota_hero_", "")


def fetch_first_ok(templates, short, retries=3):
    """Tries the templates in turn; returns the bytes of the first successful image or None."""
    for tmpl in templates:
        url = tmpl.format(short=short)
        for attempt in range(1, retries + 1):
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
                if r.status_code == 200 and r.content:
                    return r.content, url
                break  # 404 etc. — try the next template, don't retry this one
            except (requests.Timeout, requests.ConnectionError):
                time.sleep(attempt * 2)
    return None, None


def main():
    if not HEROES_JSON.exists():
        print(f"[!] No {HEROES_JSON}. Run update_data.py first.")
        sys.exit(1)

    PORTRAITS_DIR.mkdir(parents=True, exist_ok=True)
    with open(HEROES_JSON, encoding="utf-8") as f:
        heroes = json.load(f)

    print(f"\nDOWNLOADING PORTRAITS ({len(heroes)} heroes) n")
    ok, skipped, failed = 0, 0, []

    for hid, h in heroes.items():
        short = short_name(h["name"])
        main_path = PORTRAITS_DIR / f"{hid}_{short}.png"
        lg_path = PORTRAITS_DIR / f"{hid}_{short}_lg.png"

        # main portrait
        if main_path.exists() and main_path.stat().st_size > 0:
            skipped += 1
        else:
            data, url = fetch_first_ok(URL_TEMPLATES, short)
            if data:
                main_path.write_bytes(data)
                ok += 1
                print(f"  ✓ {h['localized_name']:22} ({short})")
            else:
                failed.append(h["localized_name"])
                print(f"  ✗ {h['localized_name']:22} — failed (check the name {short})")
            time.sleep(0.15)

        # horizontal _lg (for matching the top draft row); not critical if absent
        if not (lg_path.exists() and lg_path.stat().st_size > 0):
            data_lg, _ = fetch_first_ok(LG_TEMPLATES, short)
            if data_lg:
                lg_path.write_bytes(data_lg)
            time.sleep(0.1)

    print("\nDONE")
    print(f"downloaded: {ok} | skipped (already present): {skipped} | failed: {len(failed)}")
    if failed:
        print("failed to download:", ", ".join(failed))
        print("(Valve may use a different file name — send the list, I'll fix it)")
    print(f"portraits in: {PORTRAITS_DIR}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
