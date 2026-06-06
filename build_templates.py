import sys
import json
from pathlib import Path

import numpy as np
import cv2

DATA_DIR = Path(__file__).parent / "data"
PORTRAITS_DIR = DATA_DIR / "portraits"
HEROES_JSON = DATA_DIR / "heroes.json"
OUT_PATH = DATA_DIR / "templates.npz"

GRAY_SIZE = 32          
H_BINS, S_BINS = 32, 32 


def center_square(img):
    h, w = img.shape[:2]
    side = min(h, w)
    x = (w - side) // 2
    y = (h - side) // 2
    return img[y:y + side, x:x + side]


def fingerprint(bgr):
    sq = center_square(bgr)
    sq = cv2.resize(sq, (96, 96), interpolation=cv2.INTER_AREA)

    hsv = cv2.cvtColor(sq, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [H_BINS, S_BINS], [0, 180, 0, 256])
    hist = cv2.normalize(hist, hist).flatten().astype(np.float32)

    gray = cv2.cvtColor(sq, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (GRAY_SIZE, GRAY_SIZE), interpolation=cv2.INTER_AREA)
    g = gray.flatten().astype(np.float32)
    g = (g - g.mean())                      
    n = np.linalg.norm(g) or 1.0
    g = g / n
    return hist, g


def main():
    if not HEROES_JSON.exists():
        print("[!] no heroes.json — run update_data.py first"); sys.exit(1)
    if not PORTRAITS_DIR.exists():
        print("[!] no portraits/ — run download_portraits.py first"); sys.exit(1)

    with open(HEROES_JSON, encoding="utf-8") as f:
        heroes = json.load(f)

    ids, names, hists, grays = [], [], [], []
    missing = []

    for hid, h in heroes.items():
        short = h["name"].replace("npc_dota_hero_", "")
        p = PORTRAITS_DIR / f"{hid}_{short}.png"
        if not p.exists():
            p = PORTRAITS_DIR / f"{hid}_{short}_lg.png"
        if not p.exists():
            missing.append(h["localized_name"]); continue

        img = cv2.imread(str(p))
        if img is None:
            missing.append(h["localized_name"]); continue

        hist, g = fingerprint(img)
        ids.append(int(hid)); names.append(h["localized_name"])
        hists.append(hist); grays.append(g)

    if not ids:
        print("[!] no templates collected"); sys.exit(1)

    np.savez_compressed(
        OUT_PATH,
        ids=np.array(ids, dtype=np.int32),
        names=np.array(names, dtype=object),
        hists=np.stack(hists),
        grays=np.stack(grays),
        meta=np.array([H_BINS, S_BINS, GRAY_SIZE], dtype=np.int32),
    )
    print(f"Templates collected: {len(ids)} -> {OUT_PATH.name}")
    if missing:
        print(f"Without a portrait ({len(missing)}): {', '.join(missing)}")


if __name__ == "__main__":
    main()
