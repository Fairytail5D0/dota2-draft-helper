"""
peek.py — downloads nothing, just reads the local data/*.json and prints samples
to sanity-check the scales and format before writing the engine.
Put it next to update_data.py and run:  python peek.py
"""

import json
from pathlib import Path

DATA = Path(__file__).parent / "data"


def load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return json.load(f)


heroes = load("heroes.json")
positions = load("positions_stratz.json")
counters = load("matchups_by_bracket_stratz.json")
synergy = load("synergy_by_bracket_stratz.json")
baseline = load("baseline.json")

# convenient name<->id access
id2name = {hid: h["localized_name"] for hid, h in heroes.items()}


def nm(hid):
    return id2name.get(str(hid), f"#{hid}")


print("\n=== SIZES ===")
print("heroes:", len(heroes), "| positions:", len(positions))
for g in counters:
    print(f"counters[{g}]: {len(counters[g])} heroes | synergy[{g}]: {len(synergy[g])}")

print("\n=== POSITIONS: example (Anti-Mage=1, Zeus=22, CM=5) ===")
for hid in ["1", "22", "5"]:
    if hid in positions:
        p = positions[hid]
        print(f"{nm(hid)}: dist={p['dist']}")
        print(f"    wr={p.get('wr')}")

print("\n=== synergy RANGE in counters (DIVINE_IMMORTAL) ===")
vals = []
for a, vsmap in counters.get("DIVINE_IMMORTAL", {}).items():
    vals.extend(vsmap.values())
if vals:
    vals.sort()
    print(f"n={len(vals)}  min={vals[0]}  max={vals[-1]}  "
          f"median={vals[len(vals)//2]}")
    # top-5 strongest counters in the bracket
    pairs = []
    for a, vsmap in counters["DIVINE_IMMORTAL"].items():
        for b, v in vsmap.items():
            pairs.append((v, a, b))
    pairs.sort(reverse=True)
    print("top-5 counters (A strong against B):")
    for v, a, b in pairs[:5]:
        print(f"    {nm(a)}  vs  {nm(b)}  = +{v}")

print("\n=== synergy RANGE in the synergy file (DIVINE_IMMORTAL) ===")
svals = []
for a, wmap in synergy.get("DIVINE_IMMORTAL", {}).items():
    svals.extend(wmap.values())
if svals:
    svals.sort()
    print(f"n={len(svals)}  min={svals[0]}  max={svals[-1]}  "
          f"median={svals[len(svals)//2]}")
    pairs = []
    for a, wmap in synergy["DIVINE_IMMORTAL"].items():
        for b, v in wmap.items():
            pairs.append((v, a, b))
    pairs.sort(reverse=True)
    print("top-5 synergies (A + B together):")
    for v, a, b in pairs[:5]:
        print(f"    {nm(a)}  +  {nm(b)}  = +{v}")

print("\n=== CHECK: are all IDs from positions in heroes ===")
missing = [h for h in positions if h not in heroes]
print("missing in heroes:", missing or "none, everything matches")

print("\n=== Anti-Mage: strongest against whom (DIVINE_IMMORTAL) ===")
am = counters.get("DIVINE_IMMORTAL", {}).get("1", {})
top = sorted(am.items(), key=lambda kv: kv[1], reverse=True)[:8]
for b, v in top:
    print(f"    AM vs {nm(b)} = +{v}")
