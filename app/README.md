# MacroEZ app

The code for the MacroEZ app on Windows and Mac. Download the app itself from https://macroez.com/download/.

- `macroez.py`: the app. On Windows it uses the Windows APIs directly.
- `macroez_mac.py`: the Mac versions of the low-level parts (keys, clicks, recording, hotkeys, apps), swapped in on a Mac.
- `macroez-mac.spec`: builds `MacroEZ.app`. GitHub builds it on every change (see `.github/workflows/mac.yml`).

Run it from source: `pip install -r requirements-mac.txt` (on a Mac), then `python macroez.py`.
