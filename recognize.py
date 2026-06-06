r"""
recognize.py — recognizing heroes in slots (Phase 2, step 3).

Reads the zones (data/regions.json) and templates (data/templates.npz), crops each
slot, computes a fingerprint and finds the nearest hero (HSV histogram + gray shape).

TWO MODES:
  python recognize.py path\to\screenshot.jpg   — test on a screenshot (prints what it recognized)
  python recognize.py                           — capture the screen live once

As a module: Recognizer().read_enemies(bgr_or_None) -> [(hero_id, name, confidence), ...]
This is what the main app uses for auto-reading.
"""

import sys
import json
from pathlib import Path

import numpy as np
import cv2

DATA_DIR = Path(__file__).parent / "data"
REGIONS_PATH = DATA_DIR / "regions.json"
TEMPLATES_PATH = DATA_DIR / "templates.npz"

# confidence threshold: below this we treat the slot as empty/unrecognized
MIN_CONFIDENCE = 0.45
# weight of histogram vs gray shape in the final similarity
W_HIST, W_GRAY = 0.6, 0.4


def center_square(img):
    h, w = img.shape[:2]
    side = min(h, w)
    x = (w - side) // 2; y = (h - side) // 2
    return img[y:y + side, x:x + side]


class Recognizer:
    def __init__(self):
        if not REGIONS_PATH.exists():
            raise FileNotFoundError("no regions.json — run calibrate.py first")
        if not TEMPLATES_PATH.exists():
            raise FileNotFoundError("no templates.npz — run build_templates.py first")
        with open(REGIONS_PATH, encoding="utf-8") as f:
            self.regions = json.load(f)
        z = np.load(TEMPLATES_PATH, allow_pickle=True)
        self.ids = z["ids"]
        self.names = z["names"]
        self.hists = z["hists"]
        self.grays = z["grays"]
        self.H_BINS, self.S_BINS, self.GRAY_SIZE = [int(x) for x in z["meta"]]
        # sides: if swap=True, logical "enemy" is read from the physical "ally" zone
        self.swap = bool(self.regions.get("swap", False))

    def set_swap(self, value):
        """Swap which zone is considered the enemy; persist the choice to regions.json."""
        self.swap = bool(value)
        self.regions["swap"] = self.swap
        try:
            with open(REGIONS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.regions, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _physical_side(self, logical):
        """Logical side -> physical zone key, accounting for swap."""
        if not self.swap:
            return logical
        return {"enemy": "ally", "ally": "enemy"}.get(logical, logical)

    # ── fingerprint of one slot (same recipe as in build_templates) ──
    def _fingerprint(self, bgr):
        sq = center_square(bgr)
        sq = cv2.resize(sq, (96, 96), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(sq, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [self.H_BINS, self.S_BINS],
                            [0, 180, 0, 256])
        hist = cv2.normalize(hist, hist).flatten().astype(np.float32)
        gray = cv2.cvtColor(sq, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (self.GRAY_SIZE, self.GRAY_SIZE),
                          interpolation=cv2.INTER_AREA)
        g = gray.flatten().astype(np.float32)
        g = g - g.mean()
        g = g / (np.linalg.norm(g) or 1.0)
        return hist, g

    def _match(self, bgr):
        """Find the best hero for a slot image. -> (id, name, confidence)."""
        hist, g = self._fingerprint(bgr)
        # histogram similarity (correlation, 0..1) for all templates
        hist_sims = np.array([
            cv2.compareHist(hist, self.hists[i], cv2.HISTCMP_CORREL)
            for i in range(len(self.ids))
        ], dtype=np.float32)
        hist_sims = np.clip(hist_sims, 0.0, 1.0)
        # gray fingerprint similarity = cosine (already normalized) -> 0..1
        gray_sims = (self.grays @ g + 1.0) / 2.0
        score = W_HIST * hist_sims + W_GRAY * gray_sims
        best = int(np.argmax(score))
        return int(self.ids[best]), str(self.names[best]), float(score[best])

    def _grab(self):
        import mss
        with mss.mss() as sct:
            shot = np.array(sct.grab(sct.monitors[1]))
            return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

    def read_enemies(self, bgr=None, side="enemy"):
        """
        Recognize heroes in the slots of a side (enemy/ally).
        bgr=None -> capture the screen. Returns [(id, name, confidence), ...]
        only for slots where confidence >= MIN_CONFIDENCE (the rest are skipped).
        """
        if bgr is None:
            bgr = self._grab()
        phys = self._physical_side(side)
        if phys not in self.regions:
            return []
        out = []
        for (x, y, w, h) in self.regions[phys]["slots"]:
            crop = bgr[y:y + h, x:x + w]
            if crop.size == 0:
                continue
            hid, name, conf = self._match(crop)
            if conf >= MIN_CONFIDENCE:
                out.append((hid, name, round(conf, 3)))
        return out


def main():
    rec = Recognizer()
    if len(sys.argv) > 1:
        bgr = cv2.imread(sys.argv[1])
        if bgr is None:
            print("[!] cannot open", sys.argv[1]); sys.exit(1)
        print(f"Test on screenshot: {sys.argv[1]}")
    else:
        print("Capturing the screen...")
        bgr = None

    img = bgr if bgr is not None else rec._grab()
    print("\n=== ENEMIES ===")
    for hid, name, conf in rec.read_enemies(img, "enemy"):
        bar = "█" * int(conf * 20)
        print(f"  {name:20} conf={conf:.2f} {bar}")
    if "ally" in rec.regions:
        print("\n=== ALLIES ===")
        for hid, name, conf in rec.read_enemies(img, "ally"):
            print(f"  {name:20} conf={conf:.2f}")


if __name__ == "__main__":
    main()
