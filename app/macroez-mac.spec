# Builds MacroEZ.app for macOS:  pyinstaller --noconfirm macroez-mac.spec   (run from this folder, on a Mac)
import os

HERE = os.path.abspath(".")
VERSION = os.environ.get("MACROEZ_VERSION", "1.4")
UNUSED = ("opencv_videoio_ffmpeg", "_avif")  # video playback and an image format MacroEZ never opens

a = Analysis(["macroez.py"], pathex=[HERE], datas=[("macroez_logo.png", ".")],
             hiddenimports=["macroez_mac", "Quartz", "AppKit", "ApplicationServices"],
             excludes=["matplotlib", "scipy", "pandas", "IPython", "pytest", "pydoc", "doctest", "tkinter.test",
                       "lib2to3", "xmlrpc"])
a.binaries = [b for b in a.binaries if not any(word in b[0] for word in UNUSED)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MacroEZ", console=False, upx=False,
          argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name="MacroEZ", upx=False)
app = BUNDLE(
    coll,
    name="MacroEZ.app",
    icon="MacroEZ.icns" if os.path.exists("MacroEZ.icns") else None,
    bundle_identifier="com.macroez.app",
    version=VERSION,
    info_plist={
        "CFBundleName": "MacroEZ",
        "CFBundleDisplayName": "MacroEZ",
        "CFBundleShortVersionString": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        # "Open in MacroEZ" on macroez.com/presets
        "CFBundleURLTypes": [{"CFBundleURLName": "com.macroez.preset", "CFBundleURLSchemes": ["macroez"]}],
        "NSAppleEventsUsageDescription": "MacroEZ presses keys and clicks for you in the apps you choose.",
    },
)
