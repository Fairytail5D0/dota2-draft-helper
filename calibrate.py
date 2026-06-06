r"""
calibrate.py — setting up recognition zones (Phase 2, step 1).

You show the program once WHERE the enemy portraits are on screen — it remembers
the coordinates in data/regions.json. The recognizer then reads exactly those.

TWO MODES:
  python calibrate.py path\to\screenshot.jpg   — calibrate from a saved screenshot
  python calibrate.py                           — capture the screen live (5s countdown,
                                                   switch to the draft in time)

HOW TO SELECT:
  • a window with the screenshot opens;
  • drag a rectangle around the WHOLE ROW of enemy portraits (all 5);
  • press ENTER or SPACE — confirm; C — cancel the selection;
  • then likewise you can select your own row (for synergy) or just press
    ENTER on an empty selection to skip.
The program splits the strip into 5 equal slots, shows a preview and saves regions.json.
"""

import sys
import json
import time
from pathlib import Path

import numpy as np
import cv2

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
REGIONS_PATH = DATA_DIR / "regions.json"

SLOTS = 5
DISPLAY_MAX_W = 1600   # scale for display (a 1920 screen may not fit the window)
DISPLAY_MAX_H = 900


def grab_screen():
    """Full-screen capture via mss (works in borderless)."""
    import mss
    with mss.mss() as sct:
        mon = sct.monitors[1]            # main monitor
        shot = np.array(sct.grab(mon))   # BGRA
        return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR), (mon["width"], mon["height"])


def load_image(path):
    img = cv2.imread(path)
    if img is None:
        print(f"[!] could not open {path}")
        sys.exit(1)
    h, w = img.shape[:2]
    return img, (w, h)


def select_strip(img, title):
    """
    Shows the (scaled) image, lets you select a rectangle.
    Returns (x, y, w, h) in FULL image COORDINATES, or None if skipped.
    """
    h, w = img.shape[:2]
    scale = min(DISPLAY_MAX_W / w, DISPLAY_MAX_H / h, 1.0)
    disp = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1.0 else img.copy()

    print(f"\n>>> {title}")
    print("    Drag a box around the portrait row, then ENTER/SPACE.")
    print("    Empty selection + ENTER = skip.")
    r = cv2.selectROI(title, disp, showCrosshair=False, fromCenter=False)
    cv2.destroyWindow(title)

    if r is None or r[2] == 0 or r[3] == 0:
        return None
    x, y, bw, bh = r
    # back to full-image coordinates
    inv = 1.0 / scale
    return (int(x * inv), int(y * inv), int(bw * inv), int(bh * inv))


def split_into_slots(strip, n=SLOTS):
    """Split a horizontal strip into n equal slots."""
    x, y, w, h = strip
    cell = w / n
    return [(int(x + i * cell), int(y), int(cell), int(h)) for i in range(n)]


def draw_preview(img, regions):
    prev = img.copy()
    colors = {"enemy": (60, 60, 255), "ally": (120, 220, 80)}
    for side, data in regions.items():
        if side not in colors or "slots" not in data:
            continue
        for i, (x, y, w, h) in enumerate(data["slots"]):
            cv2.rectangle(prev, (x, y), (x + w, y + h), colors[side], 2)
            cv2.putText(prev, f"{side[0].upper()}{i+1}", (x + 3, y + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colors[side], 1)
    h, w = prev.shape[:2]
    scale = min(DISPLAY_MAX_W / w, DISPLAY_MAX_H / h, 1.0)
    if scale < 1.0:
        prev = cv2.resize(prev, (int(w * scale), int(h * scale)))
    print("\nZone preview. If everything is fine — press any key in the window (it will save).")
    print("If not — close the window and run calibration again.")
    cv2.imshow("preview (press a key = save)", prev)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def main():
    if len(sys.argv) > 1:
        img, (w, h) = load_image(sys.argv[1])
        print(f"Screenshot loaded: {w}x{h}")
    else:
        print("Screen capture in 5 seconds — switch to the draft/strategy stage...")
        for i in range(5, 0, -1):
            print(f"  {i}...", end="", flush=True); time.sleep(1)
        print()
        img, (w, h) = grab_screen()
        print(f"Screen captured: {w}x{h}")

    regions = {"screen": {"w": w, "h": h}, "slots": SLOTS}

    enemy = select_strip(img, "ENEMY portraits — select the whole row")
    if enemy:
        regions["enemy"] = {"strip": list(enemy), "slots": [list(s) for s in split_into_slots(enemy)]}
        print(f"  enemy slots: {SLOTS}, strip {enemy}")
    else:
        print("  [!] enemy zone not selected — recognition won't work without it.")

    ally = select_strip(img, "OUR portraits — select the row (or ENTER to skip)")
    if ally:
        regions["ally"] = {"strip": list(ally), "slots": [list(s) for s in split_into_slots(ally)]}
        print(f"  our slots: {SLOTS}, strip {ally}")

    draw_preview(img, regions)

    with open(REGIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(regions, f, ensure_ascii=False, indent=2)
    print(f"\nSaved -> {REGIONS_PATH}")
    print("Done. Next we build the recognizer that reads these zones.")


if __name__ == "__main__":
    main()
