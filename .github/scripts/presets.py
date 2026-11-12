"""Checks preset submissions (GitHub issues) and publishes approved ones to presets/ on the website.

  python presets.py check    -> writes comment.md with what the preset does and anything worth a second look
  python presets.py publish  -> adds the preset to presets/<id>/preset.zip and presets/index.json

Reads the issue from the environment: ISSUE_NUMBER, ISSUE_TITLE, ISSUE_BODY, ISSUE_AUTHOR.
The same rules as the app's read_preset(), so a preset that passes here opens in the app.
"""
import datetime, io, json, os, re, sys, urllib.request, zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PRESETS = ROOT / "presets"
LIMITS = {"bytes": 15_000_000, "images": 200, "steps": 5000, "pixels": 4000}
MODES = {"image": "Image", "classic": "Classic", "hybrid": "Hybrid", "reaction": "Reaction",
         "autoclick": "Autoclicker"}
LISTS = ("targets", "reconnect", "reactions", "classic", "hybrid", "watches")
PRIVATE = {"run_in", "only_process", "monitor", "profile_key", "mode_keys", "safe_zones"}
ATTACHMENT = re.compile(r"https://github\.com/(?:user-attachments/files|[\w.-]+/[\w.-]+/files)/\d+/[^\s)\]]+?\.zip")


class Refused(Exception):
    pass


def download(body):
    match = ATTACHMENT.search(body or "")
    if not match:
        raise Refused("I couldn't find a .zip file in the **Preset file** box. Drag the file from MacroEZ into it.")
    request = urllib.request.Request(match.group(0), headers={"User-Agent": "MacroEZ preset check"})
    with urllib.request.urlopen(request, timeout=60) as response:
        blob = response.read(LIMITS["bytes"] + 1)
    if len(blob) > LIMITS["bytes"]:
        raise Refused("The file is too big (15 MB at most).")
    return blob


def read(blob):
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
        entries = archive.infolist()
        if sum(e.file_size for e in entries) > LIMITS["bytes"] * 3:
            raise Refused("The file is too big once unpacked.")
        data = json.loads(archive.read("preset.json").decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, ValueError):
        raise Refused("That isn't a MacroEZ preset file. Make it in MacroEZ with **Share this profile**.") from None
    if not isinstance(data, dict) or data.get("format") != "macroez-preset" or data.get("version") != 1:
        raise Refused("That isn't a MacroEZ preset file, or it's from a newer version.")
    if data.get("mode") not in MODES:
        raise Refused("The preset's mode isn't one MacroEZ has.")
    for key in LISTS:
        items = data.get(key, [])
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise Refused("The preset file is damaged.")
    if sum(len(data.get(k, [])) for k in LISTS) > LIMITS["steps"]:
        raise Refused("The preset has too many steps.")
    images = 0
    for entry in entries:
        if entry.filename == "preset.json" or entry.is_dir():
            continue
        name = entry.filename[len("images/"):] if entry.filename.startswith("images/") else ""
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}\.png", name) or ".." in name:
            raise Refused(f"The file contains something that isn't a preset image: `{entry.filename[:80]}`.")
        try:
            with Image.open(io.BytesIO(archive.read(entry))) as picture:
                if picture.format != "PNG" or max(picture.size) > LIMITS["pixels"]:
                    raise Refused("One of the images is too big or not a PNG.")
                picture.load()
        except OSError:
            raise Refused("One of the images is damaged.") from None
        images += 1
    if images > LIMITS["images"]:
        raise Refused("The preset has too many images.")
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    if PRIVATE & set(settings):
        raise Refused("The preset contains personal settings. Make it again with **Share this profile**.")
    return data, images


def warnings(data):
    mode = data["mode"]
    settings = data.get("settings", {})
    keys = []
    if mode in ("classic", "hybrid"):
        keys = [(str(a.get("key", "")), a.get("scan", 0), bool(a.get("extended")), a.get("kind"))
                for a in data.get(mode, []) if a.get("kind") in ("key", "key_down", "key_up")]
    elif mode == "reaction":
        keys = [(str(t.get("key", "")), 0, False, "key") for t in data.get("reactions", [])]
    elif mode == "image" and settings.get("spam_enabled"):
        keys = [(str(settings.get("spam_key", "")), 0, False, "key")]
    names = [k[0].lower() for k in keys]
    found = []
    if any(n in ("left win", "right win") or (scan, ext) in ((0x5B, True), (0x5C, True)) for n, scan, ext, _ in keys):
        found.append("Presses the Windows key")
    if any(kind == "key_down" and ("ctrl" in n.lower() or "alt" in n.lower() or scan in (0x1D, 0x38))
           for n, scan, ext, kind in keys):
        found.append("Uses keyboard shortcuts (holds Ctrl or Alt)")
    if "menu" in names:
        found.append("Presses the Menu key")
    typed = sum(1 for n in names if len(n) == 1 and n.isalnum())
    if typed >= 12:
        found.append(f"Types text ({typed} key presses)")
    return found


def describe_steps(data, limit=60):
    mode = data["mode"]
    lines = []
    if mode in ("classic", "hybrid"):
        for n, a in enumerate(data.get(mode, []), start=1):
            kind = a.get("kind")
            what = {"key": f"press {a.get('key')}", "key_down": f"hold {a.get('key')} down",
                    "key_up": f"let go of {a.get('key')}", "click": f"{a.get('button', 'left')} click at {a.get('x')}, {a.get('y')}",
                    "mouse_down": f"hold {a.get('button')} button", "mouse_up": f"let go of {a.get('button')} button",
                    "wait": f"wait {a.get('ms')}-{a.get('ms_max')} ms", "scroll": f"scroll {a.get('direction')}",
                    "click_image": f"click image {a.get('name')}", "wait_image": f"wait for image {a.get('name')}"}
            lines.append(f"{n}. {what.get(kind, kind)}")
    elif mode == "image":
        lines = [f"click {t.get('name')}" for t in data.get("targets", [])]
        lines += [f"recovery: click {t.get('name')}" for t in data.get("reconnect", [])]
    elif mode == "reaction":
        lines = [f"when {t.get('name')} shows up, press {t.get('key')}" for t in data.get("reactions", [])]
    else:
        s = data.get("settings", {})
        lines = [f"click {s.get('ac_button', 'left')} at {s.get('ac_cps', 10)} clicks per second"]
        lines += [f"smart stop: {t.get('name')} ({t.get('watch')})" for t in data.get("watches", [])]
    if len(lines) > limit:
        lines = lines[:limit] + [f"… and {len(lines) - limit} more"]
    return lines


def count(data):
    mode = data["mode"]
    return len(data.get({"image": "targets", "reaction": "reactions", "autoclick": "watches"}.get(mode, mode), []))


def slug(name, number):
    text = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "preset"
    return f"{text}-{number}"


def clean(text, limit):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def field(body, label):
    """A value from the issue form, which GitHub writes as '### Label' then the answer."""
    match = re.search(rf"###\s*{re.escape(label)}\s*\n+(.*?)(?=\n###|\Z)", body or "", re.S)
    value = match.group(1).strip() if match else ""
    return "" if value == "_No response_" else value


def main(action):
    number = int(os.environ["ISSUE_NUMBER"])
    body = os.environ.get("ISSUE_BODY", "")
    author = clean(os.environ.get("ISSUE_AUTHOR", ""), 40)
    out = Path(os.environ.get("RUNNER_TEMP", ".")) / "comment.md"
    try:
        data, images = read(download(body))
    except Refused as problem:
        out.write_text(f"❌ **This preset can't be listed yet.** {problem}\n\nFix it, then edit this issue "
                       "and it'll be checked again.", encoding="utf-8")
        print("refused:", problem)
        return 1
    name = clean(field(body, "Preset name") or data.get("name") or "Preset", 60)
    description = clean(field(body, "What does it do?") or data.get("description"), 300)
    found = warnings(data)
    steps = describe_steps(data)
    if action == "check":
        flags = "\n".join(f"- ⚠️ {w}" for w in found) or "- Nothing unusual."
        lines = "\n".join(steps) or "(no steps)"
        out.write_text(
            f"✅ **The file checks out.** {MODES[data['mode']]} mode, {count(data)} item(s), {images} image(s).\n\n"
            f"**Worth a second look:**\n{flags}\n\n<details><summary>Every step</summary>\n\n```\n{lines}\n```\n"
            "</details>\n\nA maintainer will look it over. Adding the **approved** label publishes it.",
            encoding="utf-8")
        return 0
    preset_id = slug(name, number)
    folder = PRESETS / preset_id
    folder.mkdir(parents=True, exist_ok=True)
    blob = download(body)
    (folder / "preset.zip").write_bytes(blob)
    index_path = PRESETS / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"presets": []}
    entry = {"id": preset_id, "name": name, "description": description, "mode": data["mode"],
             "author": author, "items": count(data), "images": images, "warnings": found,
             "size": len(blob), "added": datetime.date.today().isoformat(), "issue": number}
    index["presets"] = [p for p in index["presets"] if p.get("id") != preset_id] + [entry]
    index["presets"].sort(key=lambda p: p.get("added", ""), reverse=True)
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    (Path(os.environ.get("RUNNER_TEMP", ".")) / "preset_id.txt").write_text(preset_id, encoding="utf-8")
    out.write_text(f"🎉 **Published!** It's live at https://macroez.com/presets/#{preset_id} in a minute or two, "
                   "and in the app under **Presets**. Thanks for sharing!", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
