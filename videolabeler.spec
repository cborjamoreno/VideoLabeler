# PyInstaller build recipe — run it with:  pyinstaller videolabeler.spec
#
# One spec for the three platforms. PyInstaller cannot cross-compile: run it
# ON Windows to get the .exe and ON macOS to get the .app.
#
#   Windows -> dist/VideoLabeler.exe   (single file, just send it over)
#   macOS   -> dist/VideoLabeler.app   (bundle, double-clickable)
#   Linux   -> dist/VideoLabeler       (single file)

import sys

APP_NAME = "VideoLabeler"

# Everything the app reads at runtime but does not import.
datas = [("app_modules/button_styles.qss", "app_modules")]

# QtMultimedia is imported inside a try/except (audio is optional), so name it
# explicitly to be sure the module and its FFmpeg backend get collected.
hiddenimports = ["PyQt6.QtMultimedia"]

# Nothing here is used by the app; excluding it keeps the build a few hundred
# MB smaller and the startup faster.
excludes = [
    "torch", "torchvision", "tensorflow", "matplotlib", "scipy", "pandas",
    "sklearn", "skimage", "PIL", "tkinter", "IPython", "jupyter", "notebook",
    "pytest", "setuptools", "pip", "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtQuick", "PyQt6.QtQml", "PyQt6.Qt3DCore", "PyQt6.QtCharts",
    "PyQt6.QtDesigner", "PyQt6.QtBluetooth", "PyQt6.QtWebSockets",
]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

if sys.platform == "darwin":
    # macOS: a .app bundle (folder-based, but a single icon to the user).
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        argv_emulation=True,      # lets users drop a video onto the app icon
        target_arch=None,         # set to "universal2" for an Intel+ARM build
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=False, name=APP_NAME,
    )
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier="org.videolabeler.app",
        info_plist={
            "CFBundleName": "Video Annotator",
            "CFBundleDisplayName": "Video Annotator",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
            # Needed on macOS 13+ if the video sits in Desktop/Documents/Downloads
            "NSDesktopFolderUsageDescription":
                "Video Annotator needs access to open the videos you annotate.",
            "NSDocumentsFolderUsageDescription":
                "Video Annotator reads your videos and writes the annotation CSVs.",
            "NSDownloadsFolderUsageDescription":
                "Video Annotator needs access to open the videos you annotate.",
        },
    )
else:
    # Windows and Linux: one self-contained executable file.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,            # no console window behind the GUI
        disable_windowed_traceback=False,
        icon=None,
    )
