"""
update_data.py — data layer for the draft helper.

Run once per patch/week. Fetches everything needed and caches it into ./data/*.json.
The app itself reads only the cache during the game and works offline.

Source strategy:
  • OpenDota (no token)        -> base: heroes, bracket win rates, matchup matrix.
  • Stratz (token required)    -> positional distributions + matchups split by bracket.
                                 If there's no token or a request fails — graceful fallback
                                 to OpenDota; the script does NOT crash, the data is just
                                 less accurate on positions.

The Stratz token is read from the STRATZ_TOKEN environment variable or a .env file.
NEVER hardcode the token in the code.
"""

import os
import sys
import json
import time
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv is optional; you can set STRATZ_TOKEN via the system environment

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

OPENDOTA_BASE = "https://api.opendota.com/api"
STRATZ_GRAPHQL = "https://api.stratz.com/graphql"
STRATZ_TOKEN = os.environ.get("STRATZ_TOKEN", "").strip()

# Stratz REQUIRES this User-Agent, otherwise it rejects requests.
STRATZ_HEADERS = {
    "Authorization": f"Bearer {STRATZ_TOKEN}",
    "User-Agent": "STRATZ_API",
    "Content-Type": "application/json",
}

# Pause between OpenDota requests (free limit ~60/min without a key).
OPENDOTA_DELAY = 1.1

# Stratz brackets. RankBracketBasicEnum is already a GROUPED value (per schema
# introspection: HERALD_GUARDIAN, CRUSADER_ARCHON, LEGEND_ANCIENT, DIVINE_IMMORTAL, plus ALL).
# So we pass a single whole value, not a pair. Group name == enum value.
STRATZ_BRACKETS = [
    "HERALD_GUARDIAN",
    "CRUSADER_ARCHON",
    "LEGEND_ANCIENT",
    "DIVINE_IMMORTAL",
]

# OpenDota heroStats returns pick/win across 8 brackets (1=Herald ... 8=Immortal).
# We group them into the same 4 buckets so data from both sources lines up.
OPENDOTA_BRACKET_GROUPS = {
    "HERALD_GUARDIAN": ["1", "2"],
    "CRUSADER_ARCHON": ["3", "4"],
    "LEGEND_ANCIENT": ["5", "6"],
    "DIVINE_IMMORTAL": ["7", "8"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Small utilities
# ─────────────────────────────────────────────────────────────────────────────

def log(msg):
    print(f"  {msg}", flush=True)


def save_json(name, obj):
    path = DATA_DIR / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    log(f"saved -> {path.name}  ({path.stat().st_size // 1024} KB)")


def get_json(url, retries=4, **kwargs):
    """GET with retries. Timeout/transient error -> pause and try again."""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=45, **kwargs)
            r.raise_for_status()
            return r.json()
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            wait = attempt * 3   # 3s, 6s, 9s...
            log(f"  ~ timeout/network ({attempt}/{retries}), waiting {wait}s and retrying...")
            time.sleep(wait)
        except requests.HTTPError as e:
            # 429 (rate limit) / 5xx — also worth retrying; other 4xx — not
            code = e.response.status_code if e.response is not None else 0
            if code == 429 or 500 <= code < 600:
                last_err = e
                wait = attempt * 5
                log(f"  ~ server {code} ({attempt}/{retries}), waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise last_err


# OpenDota

def fetch_opendota_heroes():
    log("OpenDota: hero list...")
    raw = get_json(f"{OPENDOTA_BASE}/heroes")
    heroes = {}
    for h in raw:
        heroes[str(h["id"])] = {
            "id": h["id"],
            "name": h["name"],
            "localized_name": h["localized_name"],
            "primary_attr": h.get("primary_attr"),
            "attack_type": h.get("attack_type"),
            "roles": h.get("roles", []),
        }
    log(f"OpenDota: {len(heroes)} heroes")
    return heroes


def fetch_opendota_herostats():
    """
    Baseline win rates by bracket from /heroStats
    Returns {hero_id: {bracket_group: {'pick': int, 'win': int, 'wr': float}}}.
    This is the base for baseline winrate (to compute the pure matchup advantage).
    """
    log("OpenDota: heroStats (bracket win rates)...")
    raw = get_json(f"{OPENDOTA_BASE}/heroStats")
    out = {}
    for h in raw:
        hid = str(h["id"])
        groups = {}
        for group, brackets in OPENDOTA_BRACKET_GROUPS.items():
            pick = sum(int(h.get(f"{b}_pick", 0) or 0) for b in brackets)
            win = sum(int(h.get(f"{b}_win", 0) or 0) for b in brackets)
            wr = (win / pick) if pick else 0.5
            groups[group] = {"pick": pick, "win": win, "wr": round(wr, 4)}
        # overall win rate across all brackets
        total_pick = sum(g["pick"] for g in groups.values())
        total_win = sum(g["win"] for g in groups.values())
        groups["ALL"] = {
            "pick": total_pick,
            "win": total_win,
            "wr": round((total_win / total_pick) if total_pick else 0.5, 4),
        }
        out[hid] = groups
    log(f"OpenDota: baseline for {len(out)} heroes")
    return out


def fetch_opendota_matchups(hero_ids, baseline):
    """
    Matchup matrix. For each hero A vs each B we compute the PURE advantage:
        advantage(A vs B) = winrate(A vs B) − baseline_winrate(A)
    This removes the influence of the hero's overall strength and leaves the "counter signal".
    Returns {A_id: {B_id: {'games': n, 'wr': x, 'advantage': y}}}.

    RESUME: the intermediate result is written to disk, so on a timeout/restart
    the script doesn't re-fetch what it already got.
    """
    cache_path = DATA_DIR / "matchups_opendota.json"
    matrix = {}
    if cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as f:
                matrix = json.load(f)
            done = len(matrix)
            if done:
                log(f"OpenDota: found matchup cache ({done}/{len(hero_ids)}), fetching the rest...")
        except Exception:
            matrix = {}

    todo = [h for h in hero_ids if h not in matrix]
    if not todo:
        log("OpenDota: all matchups already cached, skipping fetch.")
        return matrix

    log(f"OpenDota: matchups, {len(todo)} of {len(hero_ids)} heroes remaining...")
    for i, hid in enumerate(todo, 1):
        try:
            rows = get_json(f"{OPENDOTA_BASE}/heroes/{hid}/matchups")
        except requests.HTTPError as e:
            log(f"  ! matchups for {hid} failed ({e}); skipping")
            matrix[hid] = {}
            time.sleep(OPENDOTA_DELAY)
            continue

        base_wr = baseline.get(hid, {}).get("ALL", {}).get("wr", 0.5)
        vs = {}
        for row in rows:
            opp = str(row["hero_id"])
            games = int(row.get("games_played", 0) or 0)
            wins = int(row.get("wins", 0) or 0)
            if games < 50:        # cut noise on a small sample
                continue
            wr = wins / games
            vs[opp] = {
                "games": games,
                "wr": round(wr, 4),
                "advantage": round(wr - base_wr, 4),
            }
        matrix[hid] = vs

        # write the cache every 10 heroes and at the end — to avoid losing progress
        if i % 10 == 0:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(matrix, f, ensure_ascii=False)
            log(f"  ...{i}/{len(todo)} (cache saved)")
        time.sleep(OPENDOTA_DELAY)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(matrix, f, ensure_ascii=False)
    log("OpenDota: matchup matrix ready")
    return matrix


# ─────────────────────────────────────────────────────────────────────────────
# Stratz — positional accuracy (token required; isolated and with fallback)
# ─────────────────────────────────────────────────────────────────────────────
#
#  NOTE: field names in the Stratz GraphQL change from time to time. If something
#  fails to parse — open https://api.stratz.com/graphiql, check the schema and
#  fix ONLY the queries below. The rest of the code isn't tied to the schema.
# ─────────────────────────────────────────────────────────────────────────────

def stratz_query(query, variables=None):
    """A single POST to Stratz GraphQL. Raises on any problem."""
    resp = requests.post(
        STRATZ_GRAPHQL,
        headers=STRATZ_HEADERS,
        json={"query": query, "variables": variables or {}},
        timeout=40,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"Stratz GraphQL errors: {data['errors']}")
    return data["data"]


def fetch_stratz_positions():
    """
    Hero position distribution via stats(groupByPosition: true).
    Returns {hero_id: {
        'dist': {'POSITION_1': share, ... 'POSITION_5': share},   # sums to 1.0
        'wr':   {'POSITION_1': winrate, ...}                       # win rate on the position
    }}.
    This is the core of role inference: it lets us avoid hardcoding "Zeus = pos 2".
    Fields confirmed by introspection: heroId, position, matchCount, winCount.
    """
    query = """
    query Positions {
      heroStats {
        stats(groupByPosition: true) {
          heroId
          position
          matchCount
          winCount
        }
      }
    }
    """
    data = stratz_query(query)
    rows = data["heroStats"]["stats"]

    agg = {}  # hero_id -> {position: {'m': matchCount, 'w': winCount}}
    for row in rows:
        hid = str(row["heroId"])
        pos = row["position"]                       # "POSITION_1".."POSITION_5"
        m = int(row.get("matchCount", 0) or 0)
        w = int(row.get("winCount", 0) or 0)
        slot = agg.setdefault(hid, {}).setdefault(pos, {"m": 0, "w": 0})
        slot["m"] += m
        slot["w"] += w

    out = {}
    for hid, posmap in agg.items():
        total = sum(p["m"] for p in posmap.values())
        if total <= 0:
            continue
        dist = {pos: round(p["m"] / total, 4) for pos, p in posmap.items()}
        wr = {
            pos: round(p["w"] / p["m"], 4)
            for pos, p in posmap.items()
            if p["m"] > 0
        }
        out[hid] = {"dist": dist, "wr": wr}
    return out


def fetch_stratz_matchups_by_bracket(hero_ids):
    """
    Fetches TWO axes in one response (no extra API requests):
      • vs   — counters: how much stronger A is AGAINST B
      • with — synergy:  how well A plays TOGETHER with B
    Returns a tuple (counters, synergy), each in the format:
      {bracket_group: {A_id: {B_id: [synergy_pp, matchCount]}}}
    synergy — the Stratz field (centered around 0; + = better),
    matchCount — the number of games in the sample (for sample-size weighting in the engine).
    Fields confirmed by introspection: { heroId2, matchCount, winCount, synergy }.
    """
    query = """
    query Matchup($heroId: Short!, $brackets: [RankBracketBasicEnum]) {
      heroStats {
        matchUp(heroId: $heroId, bracketBasicIds: $brackets, take: 250) {
          heroId
          vs   { heroId2 matchCount synergy }
          with { heroId2 matchCount synergy }
        }
      }
    }
    """
    counters = {group: {} for group in STRATZ_BRACKETS}
    synergy = {group: {} for group in STRATZ_BRACKETS}

    def collect(blocks, branch):
        """Collect a block (vs/with) into {opp_id: [synergy, matchCount]}, filtering obvious noise."""
        acc = {}
        for block in blocks:
            for row in block.get(branch, []) or []:
                opp = str(row["heroId2"])
                n = int(row.get("matchCount", 0) or 0)
                if n < 50:                      # base threshold against the coarsest noise
                    continue
                acc[opp] = [round(float(row.get("synergy", 0) or 0), 4), n]
        return acc

    for group in STRATZ_BRACKETS:
        log(f"Stratz: matchups (vs+with), bracket {group}...")
        for hid in hero_ids:
            try:
                data = stratz_query(query, {"heroId": int(hid), "brackets": [group]})
                blocks = data["heroStats"]["matchUp"] or []
                vs = collect(blocks, "vs")
                wi = collect(blocks, "with")
                if vs:
                    counters[group][hid] = vs
                if wi:
                    synergy[group][hid] = wi
            except Exception as e:
                log(f"  ! Stratz matchup {hid}/{group} failed ({e}); skipping")
            time.sleep(0.2)   # gently respect the limits (Default ~7/sec)
    return counters, synergy


# ─────────────────────────────────────────────────────────────────────────────
# Main run
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n=== UPDATING DRAFT HELPER DATA ===\n")

    # --fresh: clear the OpenDota matchup cache to fetch everything from scratch
    if "--fresh" in sys.argv:
        cache = DATA_DIR / "matchups_opendota.json"
        if cache.exists():
            cache.unlink()
            log("--fresh: OpenDota matchup cache deleted, fetching from scratch.")

    # 1) OpenDota base — always.
    heroes = fetch_opendota_heroes()
    save_json("heroes.json", heroes)

    baseline = fetch_opendota_herostats()
    save_json("baseline.json", baseline)

    hero_ids = list(heroes.keys())
    matchups = fetch_opendota_matchups(hero_ids, baseline)
    save_json("matchups_opendota.json", matchups)

    # 2) Stratz — if there's a token. Any failure -> fallback, the script doesn't crash.
    meta = {"sources": ["opendota"], "stratz_ok": False}

    if not STRATZ_TOKEN:
        log("Stratz: no token (STRATZ_TOKEN empty) — running on OpenDota only.")
    else:
        log("Stratz: token found, fetching positions and bracket matchups...")
        try:
            positions = fetch_stratz_positions()
            save_json("positions_stratz.json", positions)

            strat_counters, strat_synergy = fetch_stratz_matchups_by_bracket(hero_ids)
            save_json("matchups_by_bracket_stratz.json", strat_counters)
            save_json("synergy_by_bracket_stratz.json", strat_synergy)

            meta["sources"].append("stratz")
            meta["stratz_ok"] = True
            log("Stratz: data fetched successfully.")
        except Exception as e:
            log(f"Stratz: failure ({e}). Staying on OpenDota — that's fine, just less accurate on positions.")

    # 3) Flags/meta for the engine, so it knows what it has.
    meta["hero_count"] = len(heroes)
    meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_json("meta.json", meta)

    print("\n=== DONE ===")
    print(f"Sources: {', '.join(meta['sources'])}")
    print(f"Files in: {DATA_DIR}\n")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"\n[HTTP error] {e}\nCheck your internet / API availability and try again.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by the user.")
        sys.exit(130)
