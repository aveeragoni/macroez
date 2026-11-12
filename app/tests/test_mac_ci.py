"""Runs on a real Mac in GitHub Actions: the Mac parts load, the key tables are complete, apps and windows can be
read, and the window opens. It never clicks or presses anything (the build Macs don't allow that anyway)."""
import os, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
os.environ["MACROEZ_NO_WELCOME"] = "1"
import macroez as gc
import macroez_mac as mac

tmp = Path(tempfile.mkdtemp())
gc.CONFIG, gc.IMAGES, gc.CRASH_LOG = tmp / "config.json", tmp / "images", tmp / "crash.log"
assert gc.IS_MAC and gc.Recorder is mac.Recorder and gc.send_scan is mac.send_scan

# every key MacroEZ can press or use as a hotkey has a Mac key
assert all(n in mac.MAC_KEYS for n in gc.SCAN_CODES)
assert all(vk in mac.VK_TO_MAC or vk in mac.MOUSE_VKS for vk in gc.HOTKEY_KEYS.values() if vk)

# reading the system works
print("cursor", gc.cursor_position())
print("front app", repr(gc.foreground_exe()))
print("apps", list(gc.open_apps())[:8])
print("key F6 down?", gc.key_is_down(gc.HOTKEY_KEYS["F6"]))
print("our window at 0,0?", gc.point_is_ours(0, 0))
print("permissions still needed", mac.missing_permissions())
print("screen capture blocked?", gc.screen_capture_blocked())
with gc.mss.MSS() as sct:
    monitor = gc.pick_monitor(sct, 1)
    frame = gc.grab_bgr(sct, monitor)
    print("screen", monitor, "grab", frame.shape)
    assert frame.shape[:2] == (monitor["height"], monitor["width"]), "grabs come back at point size"
recorder = gc.Recorder()
try:
    recorder.start()
    print("recorder started")
    time.sleep(0.2)
    recorder.stop()
except OSError as problem:
    print("recorder needs permission (expected on a build Mac):", problem)

# the window opens, every page builds, and it closes cleanly
app = gc.App()
r = app.root
for page in ("image", "classic", "hybrid", "reaction", "autoclick", "presets", "profiles", "settings"):
    app.select_page(page)
    r.update()
assert len(app.pages) == 8
app.close()
assert not gc.CRASH_LOG.exists(), gc.CRASH_LOG.read_text()
print("ALL OK")
