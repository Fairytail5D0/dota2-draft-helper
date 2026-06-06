"""
gsi_probe.py — diagnostic Game State Integration server.

Goal: find out WHETHER Dota exposes enemy picks in a pub draft (and at which stage).
Changes nothing in the game. Just listens on localhost and prints what Dota sends.

No third-party libraries — standard library only.

HOW TO USE (step-by-step in the chat instructions):
  1. Put gamestate_integration_drafthelper.cfg into Dota's cfg folder.
  2. Add the launch option -gamestateintegration in Steam.
  3. Run this script:  python gsi_probe.py
  4. Enter a normal pub (a bot match All Pick works) and watch the console.

The script highlights the 'draft' block and any mention of heroes, so it's
immediately clear whether the enemy lineup arrives.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 53100   # must match the uri in the .cfg

# what exactly we look for in the payload — keys that may contain enemy picks
DRAFT_KEYS = ("draft", "hero", "player", "allplayers", "map")

_seen = {"draft": False, "any_hero": False, "stage": set(),
         "player": False, "allplayers": False, "hero": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # silence the noisy default log

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        # must reply 200, otherwise Dota considers the service dead
        self.send_response(200)
        self.end_headers()

        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            print("  [!] not JSON:", raw[:200])
            return

        self._inspect(data)

    def _inspect(self, data):
        present = [k for k in DRAFT_KEYS if k in data]
        # draft / game stage, if present
        gs = data.get("map", {}).get("game_state") or data.get("map", {}).get("dota_gamestate")
        if gs:
            if gs not in _seen["stage"]:
                _seen["stage"].add(gs)
                print(f"\n>>> game_state: {gs}")

        # the main thing — the draft block
        if "draft" in data:
            if not _seen["draft"]:
                _seen["draft"] = True
                print("\n========== 'draft' BLOCK APPEARED ==========")
            print("---- draft ----")
            print(json.dumps(data["draft"], ensure_ascii=False, indent=2)[:2000])

        # whether hero names appear anywhere in the payload at all
        blob = json.dumps(data, ensure_ascii=False)
        if "npc_dota_hero_" in blob and not _seen["any_hero"]:
            _seen["any_hero"] = True
            print("\n[i] Hero names appeared in the payload (npc_dota_hero_*).")

        # print each meaningful block once, to find WHERE the enemies live
        for key in ("allplayers", "player", "hero"):
            if key in data and data[key] and not _seen[key]:
                _seen[key] = True
                print(f"\n========== CONTENT OF '{key}' BLOCK ==========")
                print(json.dumps(data[key], ensure_ascii=False, indent=2)[:4000])
                print(f"========== end of '{key}' ==========\n")

        # compact pulse so we can see data is flowing
        keys = ",".join(present) if present else "(empty)"
        print(f"  · packet: [{keys}]")


def main():
    print(f"\n=== GSI PROBE listening on http://127.0.0.1:{PORT}/ ===")
    print("Waiting for data from Dota. Enter a pub-match draft.")
    print("When you see the 'draft' block with enemy heroes — copy it here.\n")
    print("(Ctrl+C to stop)\n")
    try:
        HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Summary:")
        print("  'draft' block arrived:", _seen["draft"])
        print("  hero names in data:", _seen["any_hero"])
        print("  game_state stages:", _seen["stage"] or "-")


if __name__ == "__main__":
    main()
