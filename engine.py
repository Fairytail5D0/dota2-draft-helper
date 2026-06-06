import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

POSITIONS = ["POSITION_1", "POSITION_2", "POSITION_3", "POSITION_4", "POSITION_5"]
POS_SHORT = {  
    "POSITION_1": "1 Carry", "POSITION_2": "2 Mid", "POSITION_3": "3 Off",
    "POSITION_4": "4 SoftSup", "POSITION_5": "5 HardSup",
}
BRACKETS = ["HERALD_GUARDIAN", "CRUSADER_ARCHON", "LEGEND_ANCIENT", "DIVINE_IMMORTAL"]


def _load(name):
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


class DraftEngine:
    def __init__(self):
        self.heroes = _load("heroes.json")                       # id -> meta
        self.positions = _load("positions_stratz.json")          # id -> {dist, wr}
        self.counters = _load("matchups_by_bracket_stratz.json") # bracket -> A -> B -> synergy
        self.synergy = _load("synergy_by_bracket_stratz.json")   # bracket -> A -> B -> synergy
        try:
            self.od_matchups = _load("matchups_opendota.json")   # fallback: A -> B -> {advantage}
        except FileNotFoundError:
            self.od_matchups = {}
        try:
            self.baseline = _load("baseline.json")               # id -> bracket -> {pick,win,wr}
        except FileNotFoundError:
            self.baseline = {}

        # name indexes
        self.id2name = {hid: h["localized_name"] for hid, h in self.heroes.items()}
        self.name2id = {h["localized_name"].lower(): hid for hid, h in self.heroes.items()}

    # hero popularity in a bracket (0..1, where ~1 = most popular).
    # A proxy for the probability that the enemy will still pick this hero later in the draft
    def _popularity(self, bracket):
        picks = {}
        for hid, groups in self.baseline.items():
            g = groups.get(bracket) or groups.get("ALL") or {}
            picks[hid] = g.get("pick", 0)
        mx = max(picks.values()) if picks else 0
        if mx <= 0:
            return {hid: 0.5 for hid in self.heroes}  
        return {hid: picks.get(hid, 0) / mx for hid in self.heroes}

    def name(self, hid):
        return self.id2name.get(str(hid), f"#{hid}")

    def resolve(self, query):
        q = query.strip().lower()
        if q in self.name2id:
            return self.name2id[q]
        hits = [(n, hid) for n, hid in self.name2id.items() if q in n]
        if len(hits) == 1:
            return hits[0][1]
        if len(hits) > 1:
            exact = [hid for n, hid in hits if n == q]
            if exact:
                return exact[0]
            raise ValueError(f"ambiguous: {[n for n, _ in hits][:6]}")
        raise ValueError(f"hero not found: {query}")

    def suggest(self, query, limit=8):
       
        q = query.strip().lower()
        if not q:
            return []
        starts, contains = [], []
        for hid, h in self.heroes.items():
            n = h["localized_name"]
            nl = n.lower()
            if nl.startswith(q):
                starts.append((n, hid))
            elif q in nl:
                contains.append((n, hid))
        starts.sort(key=lambda x: x[0])
        contains.sort(key=lambda x: x[0])
        return (starts + contains)[:limit]

    def infer_positions(self, enemy_ids):
        """
        Returns {enemy_id: {position: probability}}.
        Start from the hero's prior distribution. Then a few iterations of "soft"
        constraint propagation: positions where the team is already confidently
        occupied become less likely for the rest (since there's roughly one lane per player)
        """
        prior = {}
        for hid in enemy_ids:
            dist = self.positions.get(str(hid), {}).get("dist", {})
            if not dist:
                dist = {p: 0.2 for p in POSITIONS}  # uniform if no data
            prior[hid] = {p: dist.get(p, 0.0) for p in POSITIONS}

        belief = {h: dict(d) for h, d in prior.items()}

        for _ in range(8):  
            # total "occupancy" of each position by the enemy team
            occupancy = {p: sum(belief[h][p] for h in enemy_ids) for p in POSITIONS}
            new = {}
            for h in enemy_ids:
                row = {}
                for p in POSITIONS:
                    # competition: other heroes already claim this position
                    competition = occupancy[p] - belief[h][p]
                    # the more it's taken by others, the lower the chance here; +1 to avoid div by 0
                    row[p] = prior[h][p] / (1.0 + max(0.0, competition))
                s = sum(row.values()) or 1.0
                new[h] = {p: v / s for p, v in row.items()}
            belief = new

        return belief

    #  reading advantage from caches (with sample-size weighting) 
    @staticmethod
    def _shrink(value, n, k):
        """
        Shrinks the advantage toward zero, more strongly for smaller samples:
            adjusted = value × n / (n + k)
        n much greater than k -> almost full advantage; small n -> pushed toward 0.
        """
        if n <= 0:
            return 0.0
        return value * (n / (n + k))

    def _counter_value(self, my_id, enemy_id, bracket, k=0):
        """
        Advantage of my hero vs enemy, weighted by sample size (k - "trust")
        Returns (adjusted_value, games).Stratz per bracket, fallback - OpenDota
        """
        cell = self.counters.get(bracket, {}).get(str(my_id), {}).get(str(enemy_id))
        if cell is not None:
            val, n = cell if isinstance(cell, list) else (cell, 999999)  
            return self._shrink(val, n, k), n
        od = self.od_matchups.get(str(my_id), {}).get(str(enemy_id))
        if od is not None:
            n = int(od.get("games", 0) or 0)
            return self._shrink(od["advantage"] * 100.0, n, k), n
        return 0.0, 0

    def _synergy_value(self, my_id, ally_id, bracket, k=0):
        cell = self.synergy.get(bracket, {}).get(str(my_id), {}).get(str(ally_id))
        if cell is None:
            return 0.0, 0
        val, n = cell if isinstance(cell, list) else (cell, 999999)
        return self._shrink(val, n, k), n

    def _pos_wr(self, my_id, position):
        return self.positions.get(str(my_id), {}).get("wr", {}).get(position, 0.5)

    def recommend(self, enemy_ids, my_ids=None, bracket="DIVINE_IMMORTAL",
                  open_positions=None, top_n=5, min_pos_share=0.10,
                  risk_weight=1.0, k=300):
        """
        Three INDEPENDENT axes per position:
          counter  - advantage against already-picked enemies (sum of adv)
          synergy  - compatibility with our picks (ignores the enemy)
          flexible - counter minus a penalty for vulnerability to NOT-yet-picked enemies:
                     a hero that is good against the picked ones and hard to punish with the
                     rest of the likely draft. The penalty is weighted by the threat hero's
                     popularity and by whether the enemy has an open lane for it
        k - "trust" for sample-size weighting: a matchup's advantage is
            multiplied by n/(n+k). Larger k = more conservative (favors
            matchups with many games). k=0 disables weighting
        Returns {position: {'counter':[...], 'synergy':[...], 'flexible':[...]}}
        Each item: {'id','name','score','pos_share','reasons':[(name, contribution, games)...]}
        """
        my_ids = my_ids or []
        open_positions = open_positions or POSITIONS
        enemy_ids = [str(h) for h in enemy_ids]
        my_ids = [str(h) for h in my_ids]
        taken = set(enemy_ids) | set(my_ids)

        pop = self._popularity(bracket)

        # open enemy lanes: occupancy from role inference, free = 1 - occupancy
        enemy_pos = self.infer_positions(enemy_ids) if enemy_ids else {}
        occupancy = {p: 0.0 for p in POSITIONS}
        for h in enemy_ids:
            for p in POSITIONS:
                occupancy[p] += enemy_pos.get(h, {}).get(p, 0.0)
        free = {p: max(0.0, 1.0 - occupancy[p]) for p in POSITIONS}
        # how many picks the enemy still has (to select the worst threats)
        remaining = max(1, 5 - len(enemy_ids))

        def fit_open(hid):
            """How much a hero claims an OPEN enemy lane (0..1)"""
            dist = self.positions.get(hid, {}).get("dist", {})
            return sum(dist.get(p, 0.0) * free[p] for p in POSITIONS)

        def risk_of(cid):
            """
            Expected vulnerability of the candidate to future enemy picks
            Take the strongest threats (as many as the enemy still picks) - sum their weight
            Returns (raw_risk, [(threat_name, weight)...])
            """
            threats = []
            for E in self.heroes:
                if E in taken or E == cid:
                    continue
                adv_against, _ = self._counter_value(E, cid, bracket, k)  
                if adv_against <= 1.0:
                    continue
                weight = adv_against * pop.get(E, 0.3) * fit_open(E)
                if weight > 0:
                    threats.append((weight, self.name(E), round(adv_against, 1)))
            threats.sort(reverse=True)
            worst = threats[:remaining]
            raw = sum(w for w, _, _ in worst)
            reasons = [(nmv, advv) for _, nmv, advv in worst]
            return raw, reasons

        result = {}
        for position in open_positions:
            counter_rows, synergy_rows, flex_rows = [], [], []

            for cid in self.heroes:
                if cid in taken:
                    continue
                share = self.positions.get(cid, {}).get("dist", {}).get(position, 0.0)
                if share < min_pos_share:        # cut positional noise (Troll on pos3 etc.)
                    continue
                pos_wr = self._pos_wr(cid, position)
                wr_mult = 1.0 + (pos_wr - 0.5)

                # COUNTER
                c_score, c_reasons = 0.0, []
                for eid in enemy_ids:
                    adv, games = self._counter_value(cid, eid, bracket, k)
                    c_score += adv
                    if abs(adv) >= 1.0:
                        c_reasons.append((self.name(eid), round(adv, 1), games))
                c_score *= wr_mult
                c_reasons.sort(key=lambda r: r[1], reverse=True)

                # SYNERGY
                s_score, s_reasons = 0.0, []
                for aid in my_ids:
                    syn, games = self._synergy_value(cid, aid, bracket, k)
                    s_score += syn
                    if abs(syn) >= 1.0:
                        s_reasons.append((self.name(aid), round(syn, 1), games))
                s_score *= wr_mult
                s_reasons.sort(key=lambda r: r[1], reverse=True)

                # FLEXIBLE = counter − penalty for vulnerability to future picks
                raw_risk, risk_reasons = risk_of(cid)
                f_score = c_score - risk_weight * raw_risk

                counter_rows.append({
                    "id": cid, "name": self.name(cid), "score": round(c_score, 1),
                    "pos_share": round(share, 2), "reasons": c_reasons[:3],
                })
                flex_rows.append({
                    "id": cid, "name": self.name(cid), "score": round(f_score, 1),
                    "pos_share": round(share, 2),
                    "reasons": c_reasons[:2],          # who it's good against
                    "risk": risk_reasons[:3],          # who it's vulnerable to (for the note)
                })
                if my_ids:
                    synergy_rows.append({
                        "id": cid, "name": self.name(cid), "score": round(s_score, 1),
                        "pos_share": round(share, 2), "reasons": s_reasons[:3],
                    })

            counter_rows.sort(key=lambda r: r["score"], reverse=True)
            synergy_rows.sort(key=lambda r: r["score"], reverse=True)
            flex_rows.sort(key=lambda r: r["score"], reverse=True)
            result[position] = {
                "counter": counter_rows[:top_n],
                "synergy": synergy_rows[:top_n],
                "flexible": flex_rows[:top_n],
            }
        return result


