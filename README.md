# Video Annotator — point and bounding box annotations

A small, portable GUI to watch a video, pause it, and annotate on the paused
frame the **apparitions** of animals (a box on one frame) and their **actions**
(a time interval with a box where it starts and another where it ends).
Everything is exported to two plain CSV files.

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
3. Rewind a few seconds with **« 5s** (`←`), then land on the exact frame you
   want with **−1f** / **+1f** (`Shift`+`←`/`→`). Clicking anywhere on the
   progress bar jumps straight there.
4. Annotate (the tool pauses playback):
   * **Apparition** — click two opposite corners of a box around the animal,
     then pick its species (or type a new one). The tool stays armed, so you can
     box several animals on that frame; press it again or `Esc` to disarm.
   * **Action** — see below.
5. **Save CSVs** when done (or `Ctrl+S`). No dialog: the app creates its own
   output folder and tells you where it wrote the files.

### Actions

An action is an interval of one of these types, each with its colour:

| Type | Individuals |
|---|---|
| Interaction | two or more, of any species — the same one too (two crabs fighting) |
| Passing | one |
| Shelter use | one |
| Foraging | one |
| Presence | one |
| Approach | one (moving in on the bait / prey) |
| Consume | one (eating it) |

1. On the frame where it **starts**, press **Action** and click two corners
   around where it happens.
2. Pick the **type** and the **species** doing it. For an interaction, set how
   many **individuals** take part and the species of each one. Every species
   box lists the species used so far; typing a new name adds it.
3. The action is now **in progress**: it appears in the **In progress** panel
   (bottom right), selected, and the button under the panel reads **End …** in
   its type's colour (e.g. a red **End Interaction**). Play or skip to the frame
   where the action **ends** and press it: the action ends on that frame.
4. The app then asks for the **end box**: click two corners around where the
   action ends — the animals may well have moved. To leave it without an end
   box, press `Esc` (or the Action button, now **Skip end box**).

Between the two ends a dashed guide box slides from the start box to the end
box, and a banner at the top of the frame lists the actions in progress.

**Intermediate boxes.** A straight slide from start to end is a poor guess for a
long action or a winding path. Go to any frame inside the action where the guide
box drifts off the animal, select the action (or leave nothing selected if it is
the only one on the frame) and press `K` — or right-click it → **Add
intermediate box on this frame** — then click two corners around the animal.
Add as many as needed: the guide box then slides from each drawn box to the
next.

Selecting an action in the list opens the **Boxes** panel above **In progress**:
it lists the action's start box, its intermediate boxes and its end box in time
order, with their frame, time and coordinates, and reminds you that `K` adds
one. Clicking a row goes to that frame; the row of the frame on screen is
highlighted. Its buttons add (or redraw) a box on the current frame and remove
the intermediate box of the current frame. On a frame that already has one, `K` redraws it and right-click offers
**Remove intermediate box**. On the action's first / last frame, `K` redraws its
start / end box instead. `Ctrl+Z` undoes any of these. Intermediate boxes are
drawn solid, captioned `BOX`; the list row counts them (`+3 boxes`) and its
tooltip gives their frames.

**Several actions at once.** **Action** always starts a new action, whatever is
already in progress — say, a crab foraging while two others fight. The **In
progress** panel lists them all, newest first, with their colour, species and
start time. Click the one to end and press **End …** (it takes that action's
colour), or double-click it; the others keep running. Selecting an action in
either list selects it in the other too.

`E` does the same as **End …** for the action selected. Right-click → **End it
on this frame** works on any action, including old points converted to
actions, which have no type and are not listed as in progress.

To correct an action later, select it in the list and press `Enter` to jump to
it, then press `S` on its real first frame (and draw the start box again) or
`E` on its real last frame. `Ctrl+Z` undoes either. Right-clicking an action
offers the same, plus editing its type / species and jumping to its start or end
(`Enter` / `Shift`+`Enter` on the list row do the same). Old points converted to
actions (no type yet) are drawn past their start only while selected, so they do
not clutter every later frame.

**Individuals joining or leaving an interaction.** Go to the frame where it
happens, right-click the interaction (in the list, or its box on the frame) and
choose **Individual joins on this frame…** (then its species) or **Individual
leaves on this frame ▸** (then which one). The banner on the frame lists only the
individuals present at that moment, and hovering the action in the list shows
each individual with the frames it was in.

### The list on the right

Every annotation of the video, in time order: its type first (with its colour),
then the species, then when it happens — `hh:mm:ss (seconds)`, and for actions
the end and duration. Hovering a row shows its frame numbers. Move with `↑`/`↓`
and press `Enter` (or double-click) to jump there. The box above it filters to
apparitions, actions, or one action type.

### Old points files

Files made by earlier versions may have a `<video>_points.csv` (a species and a
frame per row). **Load CSVs** turns each point into an action with **no type
yet** (`?` in the list) and a small box around the point. Right-click → **Edit
action** to give it a type, and `S` / `E` to set its real start and end. If you
then overwrite that folder, the points file is renamed
`<video>_points_converted.csv` so it is not loaded twice.

## Controls

| Button / key | Action |
|---|---|
| **Open Video** | Choose the video file to annotate |
| **Load CSVs** | Re-import CSVs (opens in the video's folder) to continue or correct them |
| **Save CSVs** | Write the CSVs (`Ctrl+S`) |
| **▶ Play / ⏸ Pause** | Start / stop playback (`Space`) |
| **« 5s** / **5s »** | Jump 5 seconds back / forward (`←` / `→`) |
| **−1f** / **+1f** | Step one frame back / forward (`Shift`+`←`/`→`, or `,` / `.`) |
| Progress bar | Click anywhere to jump there, or drag to scrub — like a web player. Playback carries on if it was running |
| **🔊 / 🔇** | Mute or unmute the video sound (`M`) |
| Speed box | 0.25× to 4× playback speed (sound follows) |
| **Apparition** | Box an animal on this frame (two clicks) |
| **Action** | Start an action: box where it starts, then its type and species |
| **In progress** panel | Actions not ended yet; click one, then **End …** (its colour) — or double-click it — to end it on the current frame |
| `E` | End an action on the current frame: the selected one, the only one in progress, or pick from a menu |
| `S` | Redraw the start box of the selected action on the current frame |
| `K` | Add (or redraw) an intermediate box of the selected action on the current frame |
| `Esc` | Disarm the tool / cancel a half-drawn box (an action waiting for its end stays open) |
| `Ctrl+Z` | Undo the last annotation or redrawn start / end |
| `Del` | Delete the box clicked in the boxes panel, else the annotation selected in the list |
| Right-click an annotation | Edit its species / type, redraw start / end, jump, or delete it |
| `Enter` or double-click a list row | Jump to that annotation (`Shift`+`Enter`: an action's end) |
| `Ctrl` + mouse wheel | Zoom in/out on the frame (does not affect coordinates) |

Keyboard shortcuts work whatever you clicked last — buttons and the progress bar
never keep the keyboard focus, so the arrow keys always move the video instead
of jumping between buttons.

## Output

Saving creates its own folder — you are never asked where to put the files, and
**nothing is overwritten unless you ask for it** (see *Load CSVs* below). Each
annotation session gets a folder named
after the video and the moment it was first saved:

```
VideoLabeler/
└── annotations/
    ├── dive01_20260729_114100/      ← Monday's session
    │   ├── dive01_bboxes.csv
    │   ├── dive01_actions.csv
    │   ├── dive01_participants.csv
    │   └── dive01_keyframes.csv
    └── dive01_20260730_092512/      ← Tuesday's session on the same video
        ├── dive01_bboxes.csv
        ├── dive01_actions.csv
        ├── dive01_participants.csv
        └── dive01_keyframes.csv
```

Saving repeatedly during one session updates the files in that session's folder.
A new folder is created when you restart the app or open another video.

After **Load CSVs**, the first save asks what to do: **Overwrite loaded CSVs**
writes back into the folder you loaded from (e.g. to correct someone else's
annotations next to the video), while **Save to new folder** (the default)
starts a new session folder and leaves the loaded files untouched. Later saves
go to the same place without asking again. If the loaded files also held rows of
other videos, the dialog warns that overwriting drops them.

`annotations/` sits next to `app.py` (it falls back to the video's own folder if
the app folder is read-only). `<video>` below is the video file name without its
extension.

**`<video>_bboxes.csv`** — apparitions

```
video_name,frame,time_sec,class_name,x,y,width,height
dive01.mp4,410,13.667,Turtle,120,64,255,190
```

**`<video>_actions.csv`**

```
action_id,video_name,action,classes,individuals,start_frame,start_time_sec,end_frame,end_time_sec,duration_sec,start_x,start_y,start_width,start_height,end_x,end_y,end_width,end_height
1,dive01.mp4,Interaction,Crab;Crab;Whelk,3,500,16.667,620,20.667,4.000,300,120,180,140,420,160,170,150
```

**`<video>_participants.csv`** — one row per individual of each action

```
action_id,video_name,action,individual,class_name,join_frame,join_time_sec,leave_frame,leave_time_sec
1,dive01.mp4,Interaction,1,Crab,500,16.667,620,20.667
1,dive01.mp4,Interaction,2,Crab,500,16.667,580,19.333
1,dive01.mp4,Interaction,3,Whelk,540,18.000,620,20.667
```

**`<video>_keyframes.csv`** — one row per intermediate box of each action

```
action_id,video_name,action,frame,time_sec,x,y,width,height
1,dive01.mp4,Interaction,560,18.667,350,100,175,145
1,dive01.mp4,Interaction,590,19.667,400,140,170,150
```

Column meaning:

* `video_name` — file name of the annotated video.
* `frame` — 0-based frame index; `time_sec` — `frame / fps`, in seconds.
* `class_name` — the species of the apparition.
* `x`, `y`, `width`, `height` — a box: **`x` is the column** and **`y` the row**
  of its **top-left corner** (pixels from the left / top edge), in the video's
  original resolution. Zooming in the GUI never changes them.
* `action_id` — numbers the actions in time order; links an action to its rows
  in the participants file.
* `action` — the action type (empty for an old point not typed yet);
  `classes` — the species of every individual, joined by `;` (a species appears
  once per individual); `individuals` — how many took part.
* `individual`, `join_frame`, `leave_frame` (participants) — the individual's
  number within the action and the frames it entered and left it; they equal the
  action's start / end unless it joined late or left early.
* `start_frame` / `end_frame` and their `_time_sec` — first and last frame of
  the action; `duration_sec` — the interval in seconds.
* `start_*` / `end_*` — the box on the first / last frame.
* `frame`, `x` … (keyframes) — an intermediate box of the action and the frame
  it was drawn on, strictly between its start and end. Between any two drawn
  boxes the animal is assumed to move linearly.

An open action (its end never marked) leaves every `end_*` column,
`duration_sec` and the `leave_*` columns of individuals still in it empty. All
files are always written, even if some have no rows beyond the header.

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
* **Load CSVs** opens on the folder of the video you have open, since the
  annotations to review usually sit next to the video. Pick the folder holding
  the CSVs — or a folder of sessions such as `annotations/`, and the app loads
  the most recent session for the video you have open. Rows whose `video_name` belongs to another video are skipped
  and reported.
