# Video Annotator — first apparition of each animal

A small, portable GUI to watch a video, pause it, and mark animals on the paused
frame with a **point** or a **bounding box**. Each mark gets a class name, and
everything is exported to two plain CSV files.

No segmentation, no models, no GPU — it only needs PyQt6, OpenCV and NumPy.

## Install

```bash
pip install -r requirements.txt
```

Python 3.9+ is enough. If you use conda:

```bash
conda create -n videolabeler python=3.10
conda activate videolabeler
pip install -r requirements.txt
```

## Run

```bash
python app.py
```

Then press **Open Video** and pick a file (`.mp4`, `.avi`, `.mov`, `.mkv`, …).

To hand the tool to someone who has no Python — a single `.exe` on Windows or a
double-clickable `.app` on macOS — see [PACKAGING.md](PACKAGING.md).

## Workflow

1. **Open Video**.
2. Press **Play** (or `Space`) and watch — with sound, if the video has any.
   When an animal appears for the first time, pause.
3. Rewind a few seconds with **« 5s** (`←`), then land on the exact frame of
   the first apparition with **−1f** / **+1f** (`Shift`+`←`/`→`). Clicking
   anywhere on the progress bar jumps straight there.
4. Press **Point** or **BBox** — this arms the tool and pauses playback.
   * **Point**: one click on the animal.
   * **BBox**: click two opposite corners of the box.
5. A dialog asks for the **class name**: pick one already used, or type a new
   one. Cancelling discards the annotation.
6. The tool stays armed, so you can keep marking animals on that frame. Press
   the button again, or `Esc`, to disarm.
7. **Save CSVs** when done (or `Ctrl+S`). No dialog: the app creates its own
   output folder and tells you where it wrote the files.

Annotations are drawn on the frame they belong to. Scrub away and they leave
the canvas — they are still listed in the panel on the right, which shows every
annotation of the video. Double-click a row to jump back to its frame.

## Controls

| Button / key | Action |
|---|---|
| **Open Video** | Choose the video file to annotate |
| **Load CSVs** | Re-import a previous session's CSVs to continue annotating |
| **Save CSVs** | Write the CSVs into a new timestamped folder (`Ctrl+S`) |
| **▶ Play / ⏸ Pause** | Start / stop playback (`Space`) |
| **« 5s** / **5s »** | Jump 5 seconds back / forward (`←` / `→`) |
| **−1f** / **+1f** | Step one frame back / forward (`Shift`+`←`/`→`, or `,` / `.`) |
| Progress bar | Click anywhere to jump there, or drag to scrub — like a web player. Playback carries on if it was running |
| **🔊 / 🔇** | Mute or unmute the video sound (`M`) |
| Speed box | 0.25× to 4× playback speed (sound follows) |
| **Point** | Arm the point tool (one click = one annotation) |
| **BBox** | Arm the box tool (two clicks = one annotation) |
| `Esc` | Disarm the tool / cancel a half-drawn box |
| `Ctrl+Z` | Undo the last annotation |
| `Del` | Delete the annotation selected in the list |
| Right-click an annotation | Change its class or delete it |
| Double-click a list row | Jump to that annotation's frame |
| `Ctrl` + mouse wheel | Zoom in/out on the frame (does not affect coordinates) |

Keyboard shortcuts work whatever you clicked last — buttons and the progress bar
never keep the keyboard focus, so the arrow keys always move the video instead
of jumping between buttons.

## Output

Saving creates its own folder — you are never asked where to put the files, and
**nothing is ever overwritten**. Each annotation session gets a folder named
after the video and the moment it was first saved:

```
VideoLabeler/
└── annotations/
    ├── dive01_20260729_114100/      ← Monday's session
    │   ├── dive01_points.csv
    │   └── dive01_bboxes.csv
    └── dive01_20260730_092512/      ← Tuesday's session on the same video
        ├── dive01_points.csv
        └── dive01_bboxes.csv
```

Saving repeatedly during one session updates the files in that session's folder.
A new folder is created when you restart the app, open another video, or resume
from **Load CSVs** — so a resumed session leaves the folder it read as an
untouched backup.

`annotations/` sits next to `app.py` (it falls back to the video's own folder if
the app folder is read-only). `<video>` below is the video file name without its
extension.

**`<video>_points.csv`**

```
video_name,frame,time_sec,class_name,x,y
dive01.mp4,372,12.400,Fish,842,318
```

**`<video>_bboxes.csv`**

```
video_name,frame,time_sec,class_name,x,y,width,height
dive01.mp4,410,13.667,Turtle,120,64,255,190
```

Column meaning:

* `video_name` — file name of the annotated video.
* `frame` — 0-based frame index.
* `time_sec` — `frame / fps`, in seconds, 3 decimals.
* `class_name` — the class typed or selected for the annotation.
* `x`, `y` — **`x` is the column** (pixels from the left edge) and **`y` is the
  row** (pixels from the top edge), in the video's original resolution. Zooming
  in the GUI never changes them.
* `width`, `height` (bboxes only) — box size in pixels; `x, y` is its **top-left
  corner**.

Both files are always written, even if one of them has no rows beyond the
header.

## Sound

The video's own audio track plays during playback, and **🔊 / 🔇** (or `M`)
mutes it. OpenCV decodes no audio at all, so the sound comes from Qt's
multimedia module playing the same file alongside the frames; while it plays it
acts as the master clock, and the picture follows it, dropping frames rather
than drifting out of sync.

`pip install PyQt6` ships that module (`PyQt6.QtMultimedia`, with a bundled
FFmpeg backend). If a particular Qt build lacks it, or the video has no audio
track, the app runs exactly as it otherwise would — just silently, with the
mute button disabled and a tooltip saying why.

**Install PyQt6 with pip, not with conda.** conda-forge's `pyqt6` / `qt6-main`
packages ship no multimedia module at all, so `conda install pyqt6` gives a
working but permanently silent app. Inside a conda environment, still use
`pip install -r requirements.txt`. To check what you have:

```bash
python -c "from PyQt6.QtMultimedia import QMediaPlayer; print('audio OK')"
```

## Notes

* Playback decodes frames on the fly with OpenCV. Very high-resolution videos
  may not reach real-time speed on a slow machine; with sound the picture drops
  frames to stay with the audio, without sound it simply plays a little slow.
  The frame counter stays exact either way, and stepping/scrubbing is
  unaffected.
* Some containers do not report their length. The app still plays and
  annotates them; only the slider and the total-duration readout are disabled.
* **Load CSVs** opens on `annotations/`. Pick a session folder — or just pick
  `annotations/` itself and the app loads the most recent session for the video
  you have open. Rows whose `video_name` belongs to another video are skipped
  and reported.
