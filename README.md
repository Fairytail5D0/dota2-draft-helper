# Dota 2 Draft Helper

A desktop tool that suggests counter-picks, synergy picks, and "flexible" picks
against the enemy draft in real time. Recommendations are based on Stratz
matchup/synergy data (per rank bracket), with OpenDota as a fallback. Can read
enemy picks automatically from the screen during the draft.

## Features
- **Counters** — heroes that perform well against the already-picked enemies.
- **Synergy** — heroes that fit your own already-picked team (ignores the enemy).
- **Flexible** — counters minus a penalty for vulnerability to likely future enemy picks.
- Per-role filtering, adjustable result count, and a sample-size "trust" slider.
- Probabilistic role inference (no hardcoded hero→position mapping).
- Optional auto-reading of enemy picks via screen capture + template matching.

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Get a free Stratz API token (Steam login at https://stratz.com → API),
   then copy the env template and paste your token:
   ```
   copy .env.example .env
   ```
   Open `.env` and put your token after `STRATZ_TOKEN=`. (Optional — without it,
   the tool runs on OpenDota data only.)

## Build the data cache (run once per patch)
```
python update_data.py
```
Use `python update_data.py --fresh` to rebuild the OpenDota matchup cache from scratch.

## Run
```
python app.py
```

## Auto-reading from screen (optional, Phase 2)
1. Download hero portraits:  `python download_portraits.py`
2. Build recognition templates:  `python build_templates.py`
3. Calibrate the on-screen enemy/ally portrait zones (do this in Borderless Windowed):
   ```
   python calibrate.py path\to\draft_screenshot.jpg
   ```
4. In the app, toggle **Auto-read**. Use **Sides** if your team is on the opposite row.

## Files
- `update_data.py` — fetches & caches hero/matchup/synergy/position data.
- `engine.py` — recommendation engine (counter / synergy / flexible axes).
- `app.py` — PySide6 GUI.
- `recognize.py`, `build_templates.py`, `calibrate.py`, `download_portraits.py` — screen auto-reading.


## Notes
- The `.env` file (your token) and the generated `data/` caches are gitignored.
- Run `update_data.py` after fresh-cloning to regenerate the data caches locally.

