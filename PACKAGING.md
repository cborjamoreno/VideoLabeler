# Building the executable

The goal: hand a biologist one file, with no Python and no install steps.

**You must build on the system you are targeting.** PyInstaller does not
cross-compile: a Windows `.exe` can only be produced on Windows, and a macOS
`.app` only on macOS. There is no way around this from Linux. If you have no
Mac at hand, see *Building without the machine* at the end.

| Target | Build on | Command | Result |
|---|---|---|---|
| Windows | Windows 10/11 | double-click `build_windows.bat` | `dist\VideoLabeler.exe` |
| macOS | macOS 11+ | `bash build_macos.sh` | `dist/VideoLabeler.app` + `VideoLabeler-macos.zip` |
| Linux | Linux | `pyinstaller videolabeler.spec` | `dist/VideoLabeler` |

Each script creates its own `build-env` virtual environment, installs the
dependencies **with pip** (which is what brings the audio support), verifies
that `PyQt6.QtMultimedia` imported correctly, and then runs `videolabeler.spec`.

Requirements on the build machine: Python 3.9 or newer, and an internet
connection for the first run. Nothing else — no compiler, no Qt install.

## What to hand over

* **Windows** — just `VideoLabeler.exe`. It is one self-contained file
  (~160 MB, normal for Qt + OpenCV). It takes a few seconds to start the first
  time because it unpacks itself into a temporary folder.
* **macOS** — `VideoLabeler-macos.zip`, produced by the build script with
  `ditto`. Do **not** re-zip the `.app` with a plain `zip`, and do not send it
  through anything that strips extended attributes; the bundle stops working.

Both write their CSVs to an `annotations` folder **next to the executable**
(next to the `.app` on macOS), exactly like running from source. Tell users to
put the executable somewhere writable — their Desktop or a data folder, not
`Program Files` or `/Applications`.

## First launch on someone else's machine

The builds are unsigned, so both systems warn about them:

* **Windows** — SmartScreen shows "Windows protected your PC". The user clicks
  *More info* → *Run anyway*. This disappears only with a paid code-signing
  certificate (~300 €/year).
* **macOS** — Gatekeeper says the app "cannot be opened because the developer
  cannot be verified". The user right-clicks (or Control-clicks) the app →
  *Open* → *Open*, once. Double-clicking the first time will not work.
  The build script applies an *ad-hoc* signature, which is what stops Apple
  Silicon Macs from refusing the app outright, but it is not notarization —
  that needs a paid Apple Developer account.

Tell the biologists about the specific click sequence for their platform, or
they will assume the tool is broken.

## Apple Silicon vs Intel

A build made on an Apple Silicon Mac runs on Apple Silicon; on an Intel Mac it
runs on Intel and, via Rosetta, on Apple Silicon too. So:

* Building on an **Intel** Mac gives the widest coverage.
* Building on **Apple Silicon** produces an ARM-only app that will *not* run on
  older Intel Macs.
* For a single app covering both natively, set `target_arch="universal2"` in
  `videolabeler.spec` — this needs a universal2 Python and universal2 wheels for
  PyQt6, numpy and OpenCV, which is fiddly. Building twice is usually easier.

## Verifying a build before shipping it

Run the executable on a machine that has **no Python installed**, then:

1. Open a video, play it, confirm you hear sound and the 🔊 button is enabled.
2. Place a point and a box, then **Save CSVs**.
3. Confirm an `annotations/<video>_<timestamp>/` folder appeared next to the
   executable, with both CSVs in it and the rows you expect.

Step 3 matters most: it is the check that catches a frozen app writing into its
own temporary unpack folder, where every saved file would vanish on exit.

## Building without the machine

If you have no Mac (or no Windows box), the usual route is GitHub Actions:
push the project to a repository and let a workflow build on
`windows-latest` and `macos-latest` runners, which are free for public repos.
The same two commands from the table are all the workflow has to run. macOS
runners are Apple Silicon by default; use `macos-13` for an Intel build.

## Notes on the spec

`videolabeler.spec` is shared by all three platforms and branches on
`sys.platform`. Two details in it are load-bearing:

* `datas` ships `app_modules/button_styles.qss`, which the app reads at
  runtime and PyInstaller therefore cannot discover on its own.
* `hiddenimports` names `PyQt6.QtMultimedia`, because the app imports it inside
  a `try/except` (audio is optional) and that is easy for static analysis to
  miss. Without it, the packaged app would run silently.
