"""Video annotation tool — apparitions and actions of animals in a video.

Two kinds of annotation, both drawn as boxes on a paused frame:

* Apparition — one box on one frame, with the species (class) seen.
* Action — an interval of one of a fixed set of types (Interaction, Passing,
  Shelter use, Foraging, Presence, Approach, Consume), performed by one
  individual (two or more for an interaction, possibly of the same species, and
  able to join or leave while it lasts). It has a box on its first frame and
  another on its last frame, since the animals may have moved in between, plus
  as many intermediate boxes as needed where the motion is not a straight line.

Everything is exported to CSV:

    <video>_bboxes.csv   video_name,frame,time_sec,class_name,x,y,width,height
    <video>_actions.csv  action_id,video_name,action,classes,individuals,
                         start_frame,start_time_sec,end_frame,end_time_sec,
                         duration_sec,start_x,start_y,start_width,start_height,
                         end_x,end_y,end_width,end_height
    <video>_participants.csv
                         action_id,video_name,action,individual,class_name,
                         join_frame,join_time_sec,leave_frame,leave_time_sec
    <video>_keyframes.csv
                         action_id,video_name,action,frame,time_sec,
                         x,y,width,height

`classes` joins the species of every individual with ';' (repeats included);
participants.csv has one row per individual with when it joined and left;
keyframes.csv one row per intermediate box of an action.
An action whose end was never marked leaves every end_* column, duration_sec
and the leave columns of individuals still in it empty.

Older <video>_points.csv files (a species and a frame) are still read: each
point becomes an action with no type yet and a small box around the point.

x is the column (pixels from the left edge) and y is the row (pixels from the
top edge), both in ORIGINAL video resolution — zooming never changes them.

Dependencies: PyQt6, opencv-python, numpy. Nothing else.
"""

import copy
import csv
import os
import sys
from datetime import datetime

import cv2
import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QFileDialog, QVBoxLayout,
    QHBoxLayout, QMessageBox, QSlider, QComboBox, QListWidget, QListWidgetItem, QLineEdit,
    QSizePolicy, QScrollArea, QFrame, QGridLayout, QMenu, QDialog, QStyle,
    QStyleOptionSlider
)
from PyQt6.QtGui import (QPixmap, QImage, QGuiApplication, QShortcut, QKeySequence,
                         QRegularExpressionValidator, QCursor)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl, QRegularExpression

from app_modules import LabelDialog, ActionDialog, color_icon

# Audio is optional: OpenCV decodes no sound at all, so it is played by Qt's
# multimedia module alongside the frames. A plain `pip install PyQt6` ships it
# (with the bundled FFmpeg backend), but some Qt builds leave it out — the app
# then runs exactly as before, just silently.
try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    AUDIO_SUPPORTED = True
except ImportError:
    AUDIO_SUPPORTED = False

# Video containers OpenCV can usually open. "All files" stays available as a
# fallback because codec support depends on the local FFmpeg build.
VIDEO_FILTER = (
    "Videos (*.mp4 *.avi *.mov *.mkv *.mpg *.mpeg *.m4v *.wmv *.flv *.webm);;"
    "All files (*)"
)

# Playback rates offered in the speed combo box.
SPEEDS = [0.25, 0.5, 1.0, 2.0, 4.0]

# Per-class display colours (RGB). A class gets a colour by its position in
# self.classes, so the same class keeps its colour for the whole session and the
# biologist never has to pick one.
#
# No pure red or green: they read as "wrong" / "right" rather than as a species.
PALETTE = [
    (0, 160, 255),    # blue
    (255, 255, 0),    # yellow
    (255, 0, 255),    # magenta
    (0, 255, 255),    # cyan
    (255, 140, 0),    # orange
    (160, 80, 255),   # purple
    (255, 20, 147),   # deep pink
    (255, 255, 255),  # white
    (140, 200, 255),  # light blue
    (210, 170, 110),  # tan
]

FALLBACK_FPS = 25.0

# Seconds the « / » buttons (and the arrow keys) jump, YouTube-style.
SKIP_SECONDS = 5.0

# Separator between the classes involved in an action, in the CSV.
CLASS_SEPARATOR = ";"

# Action types and their display colours (RGB). Change the colours here.
ACTION_TYPES = [
    ("Interaction", (230, 57, 70)),     # red
    ("Passing", (69, 123, 255)),        # blue
    ("Shelter use", (155, 89, 182)),    # purple
    ("Foraging", (255, 200, 0)),        # yellow
    ("Presence", (46, 204, 113)),       # green
    # Predation, split: moving in on the bait / prey, then eating it
    ("Approach", (255, 127, 0)),        # orange
    ("Consume", (255, 64, 160)),        # pink
]
ACTION_COLORS = dict(ACTION_TYPES)
# Types performed by two or more species; the rest take exactly one.
MULTI_SPECIES_TYPES = {"Interaction"}
# Actions with no type yet (converted from old points) or an unknown one.
UNKNOWN_ACTION_COLOR = (190, 190, 190)

# Side length of the box drawn around an old point converted to an action,
# as a fraction of the frame's shorter side.
CONVERTED_POINT_BOX = 0.05

# Annotation-list filter: (label, (kinds shown, action type or None)).
LIST_FILTERS = (
    [("All", (("bbox", "action"), None)),
     ("Apparitions", (("bbox",), None)),
     ("Actions (all types)", (("action",), None))]
    + [(f"Actions: {name}", (("action",), name)) for name, _color in ACTION_TYPES]
)

# When the audio clock runs ahead of the decoder by more than this many frames,
# jump straight there instead of decoding every frame in between.
MAX_CATCHUP_FRAMES = 30

def resource_path(*parts):
    """Locate a read-only file that ships with the app.

    Running from source that is next to app.py; inside a PyInstaller build it
    is the temporary folder the bundle unpacks into (``sys._MEIPASS``).
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def app_data_dir():
    """Folder the app may WRITE into — never the bundle's own contents.

    Frozen apps must not use ``__file__``: it points inside a temporary
    extraction folder that is deleted on exit, which would silently throw away
    every saved CSV. Next to the executable is where users expect their output,
    except on macOS, where the executable lives inside the .app bundle and the
    output belongs beside the bundle instead.
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if sys.platform == "darwin" and exe_dir.endswith(os.path.join("Contents", "MacOS")):
            # .../VideoLabeler.app/Contents/MacOS -> folder holding the .app
            return os.path.dirname(os.path.dirname(os.path.dirname(exe_dir)))
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))


# Saved annotations go to <app folder>/annotations/<video>_<YYYYMMDD_HHMMSS>/.
# One folder per session, so a later session never overwrites an earlier one.
APP_DIR = app_data_dir()
ANNOTATIONS_DIR_NAME = "annotations"


def load_stylesheet(file_path):
    """Load stylesheet from a file"""
    try:
        with open(file_path, 'r') as f:
            return f.read()
    except Exception as e:
        print(f"Error loading stylesheet: {e}")
        return ""


def format_time(seconds, millis=True):
    """Format a duration in seconds as mm:ss.mmm (hh:mm:ss.mmm past an hour);
    with millis=False, whole seconds only (mm:ss)."""
    if seconds is None or seconds < 0 or not np.isfinite(seconds):
        return "--:--.---" if millis else "--:--"
    if not millis:
        seconds = int(seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    sec_text = f"{secs:06.3f}" if millis else f"{int(secs):02d}"
    if hours:
        return f"{hours:d}:{minutes:02d}:{sec_text}"
    return f"{minutes:02d}:{sec_text}"


# Subclass QLabel to capture mouse clicks on the frame
class ClickableLabel(QLabel):
    clicked = pyqtSignal(object)
    right_clicked = pyqtSignal(object)
    mouse_moved = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.interactions_enabled = False

    def mousePressEvent(self, event):
        # Clicking the video hands keyboard focus back to the main window, so
        # the transport keys work again after using a button or the list.
        window = self.window()
        if window is not None:
            window.setFocus(Qt.FocusReason.MouseFocusReason)
        if self.interactions_enabled:
            if event.button() == Qt.MouseButton.LeftButton:
                self.clicked.emit(event.pos())
            elif event.button() == Qt.MouseButton.RightButton:
                self.right_clicked.emit(event.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.interactions_enabled:
            self.mouse_moved.emit(event.pos())
        super().mouseMoveEvent(event)


# Scroll area that hosts the frame label and provides zoom-on-Ctrl+wheel.
# A plain wheel scrolls the view (when the zoomed frame overflows); Ctrl+wheel
# is forwarded to the viewer so it can zoom centered on the cursor.
class ZoomScrollArea(QScrollArea):
    ctrl_wheel = pyqtSignal(object)  # forwards the QWheelEvent

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.ctrl_wheel.emit(event)
            event.accept()
        else:
            super().wheelEvent(event)


# Progress bar that jumps to wherever you click, like a web video player. The
# stock QSlider only pages towards the click, which feels broken on a timeline.
class SeekSlider(QSlider):
    def _value_at(self, x):
        """Slider value under an x position on the groove."""
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self)
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self)
        span = groove.width() - handle.width()
        pos = x - groove.x() - handle.width() / 2
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(pos), max(1, span))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            value = self._value_at(event.position().x())
            self.setSliderDown(True)
            self.setValue(value)
            self.sliderMoved.emit(value)   # same handler as a drag
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Keep scrubbing while the button stays down after a click anywhere.
        if self.isSliderDown() and self.maximum() > self.minimum():
            value = self._value_at(event.position().x())
            self.setValue(value)
            self.sliderMoved.emit(value)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.isSliderDown():
            self.setSliderDown(False)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class VideoAnnotator(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Video Annotator — apparitions & actions")
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            init_w = max(1000, min(int(avail.width() * 0.85), 1900))
            init_h = max(700, min(int(avail.height() * 0.9), 1400))
        else:
            init_w, init_h = 1400, 900
        self.resize(init_w, init_h)
        self.setMinimumSize(950, 650)

        # ---------------- video state ----------------
        self.cap = None
        self.video_path = None
        self.video_name = None
        self.fps = FALLBACK_FPS
        self.total_frames = 0          # 0 => unknown (some containers)
        self.frame_idx = -1
        self.current_frame = None      # RGB uint8 ndarray of the shown frame
        self.playing = False
        self.speed = 1.0

        # ---------------- annotation state ----------------
        self.tool = None               # None | "bbox" | "action"
        self.bbox_first_corner = None  # (x, y) in image coords
        self.bboxes = []               # dicts: frame, time_sec, class_name, x, y, width, height
        # dicts: action (type, "" if not set yet), participants, start_frame,
        # start_box, end_frame, end_box — boxes are (x, y, width, height); the
        # end ones are None until the end is marked. participants: one dict per
        # individual — species, join, leave — where join / leave None mean the
        # action's own start / end, so they follow when those are redrawn.
        # keyframes: {frame: box} of the intermediate boxes; ones left outside
        # the interval by a redrawn start / end are ignored, not deleted, so
        # undoing that redraw brings them back.
        self.actions = []
        self.classes = []              # ordered class names seen so far
        # (action index, "endbox" | "start" | "key"): what the armed Action tool
        # is drawing for an existing action — the end box of one just ended, a
        # redrawn start box (S) or an intermediate box (K).
        self.target = None
        # For Ctrl+Z: ("add", kind, index) for an added annotation and
        # ("endpoint", "action", index, which, previous_frame, previous_box)
        # for a redrawn start / end, ("snapshot", "action", index, old_copy)
        # for an individual joining / leaving or an intermediate box.
        self.history = []
        self.session_dir = None        # created on the first save of a session
        # Folder the current annotations were loaded from (None if not loaded)
        # and how many of its rows belonged to other videos.
        self.loaded_dir = None
        self.loaded_skipped = 0
        self.dirty = False

        # ---------------- display state ----------------
        self.displayed_pixmap = None
        self.zoom_factor = 1.0
        self.min_zoom = 1.0
        self.max_zoom = 8.0

        # ---------------- widgets ----------------
        self.frame_label = ClickableLabel(self)
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_label.clicked.connect(self.on_frame_clicked)
        self.frame_label.right_clicked.connect(self.on_frame_right_clicked)
        self.frame_label.mouse_moved.connect(self.on_mouse_moved)
        self.frame_label.interactions_enabled = False

        self.scroll_area = ZoomScrollArea(self)
        self.scroll_area.setWidget(self.frame_label)
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.scroll_area.ctrl_wheel.connect(self.on_ctrl_wheel_zoom)

        # Top row: file actions
        self.open_button = self._make_button("Open Video", "neutral-button", self.open_video, width=130)
        self.open_button.setEnabled(True)
        self.load_button = self._make_button("Load CSVs", "neutral-button", self.load_csvs, width=130)
        self.save_button = self._make_button("Save CSVs", "save-button", self.save_csvs, width=130)
        self.video_info_label = QLabel("No video loaded")

        # Playback row
        self.play_button = self._make_button("▶  Play", "primary-button", self.toggle_play, width=110)

        skip = int(SKIP_SECONDS)
        self.skip_back_button = self._make_button(
            f"«  {skip}s", "step-button", lambda: self.skip_seconds(-SKIP_SECONDS), width=64)
        self.skip_back_button.setToolTip(f"Back {skip} seconds (Left arrow)")
        self.skip_fwd_button = self._make_button(
            f"{skip}s  »", "step-button", lambda: self.skip_seconds(SKIP_SECONDS), width=64)
        self.skip_fwd_button.setToolTip(f"Forward {skip} seconds (Right arrow)")

        # Frame-exact stepping still matters: the first apparition of an animal
        # is a specific frame, not a 5-second neighbourhood.
        self.step_back_button = self._make_button("−1f", "step-button", lambda: self.step_frame(-1), width=52)
        self.step_back_button.setToolTip("Back one frame (Shift+Left, or ',')")
        self.step_fwd_button = self._make_button("+1f", "step-button", lambda: self.step_frame(1), width=52)
        self.step_fwd_button.setToolTip("Forward one frame (Shift+Right, or '.')")

        self.mute_button = self._make_button("🔊", "neutral-button", self.toggle_mute, width=48)
        self.mute_button.setToolTip("Mute / unmute the video sound (M)")

        self.position_slider = SeekSlider(Qt.Orientation.Horizontal, self)
        self.position_slider.setEnabled(False)
        self.position_slider.setMinimum(0)
        self.position_slider.setMaximum(0)
        self.position_slider.sliderMoved.connect(self.on_slider_moved)
        self.position_slider.sliderReleased.connect(self.on_slider_released)
        self.position_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Same reason as the buttons: a focused slider eats the arrow keys.
        self.position_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.speed_combo = QComboBox(self)
        for s in SPEEDS:
            self.speed_combo.addItem(f"{s:g}×", s)
        self.speed_combo.setCurrentIndex(SPEEDS.index(1.0))
        self.speed_combo.currentIndexChanged.connect(self.on_speed_changed)
        self.speed_combo.setEnabled(False)
        self.speed_combo.setFixedWidth(80)
        # Clicking it still opens the popup; it just never keeps the focus
        # afterwards (where arrow keys would change the speed).
        self.speed_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Type a frame number and press Enter to go there
        self.frame_edit = QLineEdit(self)
        self.frame_edit.setFixedWidth(80)
        self.frame_edit.setAlignment(Qt.AlignmentFlag.AlignRight)
        # Digits only (QIntValidator would also let a locale's "." through)
        self.frame_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"\d{0,9}"), self))
        # Only a click gives it the keyboard: as the one focusable widget it
        # would otherwise grab the focus and swallow the transport keys.
        self.frame_edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.frame_edit.setToolTip("Type a frame number and press Enter to go there")
        self.frame_edit.returnPressed.connect(self.on_frame_entered)
        self.frame_edit.setEnabled(False)
        self.status_label = QLabel("/ -  —  --:-- / --:--")
        self.status_label.setMinimumWidth(240)

        # Annotation tools
        self.bbox_button = self._make_button("Apparition", "bbox-button-idle", self.toggle_bbox_tool, width=120)
        self.bbox_button.setToolTip("Box an animal on the frame where it appears")
        self.action_button = self._make_button("Action", "action-button-idle", self.toggle_action_tool, width=120)
        self.action_button.setToolTip(
            "Box where an action starts, then box where it ends on its last frame.\n"
            "K adds an intermediate box on the current frame for long or winding motions")
        self.hint_label = QLabel("Open a video to start")

        # Annotation list panel
        self.annotation_list = QListWidget(self)
        self.annotation_list.setMinimumWidth(280)
        self.annotation_list.setMaximumWidth(460)
        self.annotation_list.itemDoubleClicked.connect(self.on_annotation_activated)
        self.annotation_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.annotation_list.customContextMenuRequested.connect(self.on_list_context_menu)
        # An open action is drawn as in progress only while selected; an action
        # in progress picked here is also picked in the "In progress" panel
        self.annotation_list.currentItemChanged.connect(self.on_annotation_selected)
        self.list_filter_combo = QComboBox(self)
        for label, kinds in LIST_FILTERS:
            self.list_filter_combo.addItem(label, kinds)
        self.list_filter_combo.currentIndexChanged.connect(self.refresh_annotation_list)
        # Keeps the Up/Down keys for the list itself
        self.list_filter_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.delete_button = self._make_button("Delete selected", "delete-button", self.delete_selected_annotation, width=150)

        # Boxes of the selected action: its start, intermediate and end boxes.
        # Shown only while an action is selected in the list above.
        self.boxes_label = QLabel()
        self.boxes_label.setWordWrap(True)
        self.boxes_list = QListWidget(self)
        self.boxes_list.setMinimumWidth(280)
        self.boxes_list.setMaximumWidth(460)
        # Mouse only, like the "In progress" panel
        self.boxes_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.boxes_list.itemClicked.connect(self.on_box_clicked)
        self.boxes_list.setToolTip("Click a box to go to its frame.\n"
                                   "The box of the frame on screen is highlighted.")
        self.add_box_button = self._make_button("Add box here  (K)", "neutral-button",
                                                self.keyframe_with_key)
        self.add_box_button.setToolTip("Draw a box of this action on the current frame")
        self.remove_box_button = self._make_button("Remove box", "delete-button",
                                                   self.remove_current_keyframe)
        self.remove_box_button.setToolTip("Remove the intermediate box of the current frame")
        self.boxes_panel = QWidget(self)
        boxes_layout = QVBoxLayout(self.boxes_panel)
        boxes_layout.setContentsMargins(0, 0, 0, 0)
        boxes_layout.addWidget(self.boxes_label)
        boxes_layout.addWidget(self.boxes_list, 1)
        boxes_buttons = QHBoxLayout()
        boxes_buttons.addWidget(self.add_box_button)
        boxes_buttons.addWidget(self.remove_box_button)
        boxes_layout.addLayout(boxes_buttons)
        self.boxes_panel.setVisible(False)

        # Actions in progress: pick one and end it on the current frame
        self.open_label = QLabel("In progress")
        self.open_list = QListWidget(self)
        self.open_list.setMinimumWidth(280)
        self.open_list.setMaximumWidth(460)
        # Mouse only: the arrow keys stay with the video / the list above
        self.open_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.open_list.currentItemChanged.connect(self.on_open_action_selected)
        self.open_list.itemDoubleClicked.connect(
            lambda item: self.end_action_here(item.data(Qt.ItemDataRole.UserRole)))
        self.open_list.setToolTip("Select an action, then press End (or E) on its last frame.\n"
                                  "Double-click: end it on the current frame.")
        self.end_open_button = QPushButton("End action", self)
        self.end_open_button.setFixedHeight(40)
        self.end_open_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.end_open_button.clicked.connect(self.end_selected_open_action)
        self.end_open_button.setEnabled(False)

        # ---------------- layout ----------------
        top_layout = QHBoxLayout()
        top_layout.addWidget(self.open_button)
        top_layout.addWidget(self.load_button)
        top_layout.addWidget(self.save_button)
        top_layout.addSpacing(15)
        top_layout.addWidget(self.video_info_label)
        top_layout.addStretch()

        playback_layout = QHBoxLayout()
        playback_layout.addWidget(self.play_button)
        playback_layout.addWidget(self.skip_back_button)
        playback_layout.addWidget(self.step_back_button)
        playback_layout.addWidget(self.step_fwd_button)
        playback_layout.addWidget(self.skip_fwd_button)
        playback_layout.addWidget(self.position_slider, 1)
        playback_layout.addWidget(self.mute_button)
        playback_layout.addWidget(self.speed_combo)
        playback_layout.addWidget(QLabel("frame"))
        playback_layout.addWidget(self.frame_edit)
        playback_layout.addWidget(self.status_label)

        tools_layout = QHBoxLayout()
        tools_layout.addStretch()
        tools_layout.addWidget(self.bbox_button)
        tools_layout.addWidget(self.action_button)
        tools_layout.addSpacing(20)
        tools_layout.addWidget(self.hint_label)
        tools_layout.addStretch()

        frame_container = QWidget()
        frame_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        frame_grid = QGridLayout(frame_container)
        frame_grid.setContentsMargins(0, 0, 0, 0)
        frame_grid.setSpacing(0)
        frame_grid.addWidget(self.scroll_area, 0, 0)

        side_layout = QVBoxLayout()
        side_layout.addWidget(QLabel("Annotations (Enter / double-click to jump)"))
        side_layout.addWidget(self.list_filter_combo)
        side_layout.addWidget(self.annotation_list, 3)
        side_layout.addWidget(self.delete_button)
        side_layout.addSpacing(10)
        side_layout.addWidget(self.boxes_panel, 2)
        side_layout.addWidget(self.open_label)
        side_layout.addWidget(self.open_list, 1)
        side_layout.addWidget(self.end_open_button)

        center_layout = QHBoxLayout()
        center_layout.addWidget(frame_container, 1)
        center_layout.addLayout(side_layout)

        main_layout = QVBoxLayout()
        main_layout.addLayout(top_layout)
        main_layout.addLayout(center_layout, 1)
        main_layout.addLayout(playback_layout)
        main_layout.addLayout(tools_layout)
        self.setLayout(main_layout)

        # Playback timer
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance_frame)

        # Audio companion. OpenCV gives us frames only, so the sound comes from
        # a QMediaPlayer pointed at the same file with no video sink attached.
        # While it plays, its position is the master clock the frames follow.
        self.player = None
        self.audio_output = None
        self.audio_ready = False
        if AUDIO_SUPPORTED:
            self.audio_output = QAudioOutput(self)
            self.player = QMediaPlayer(self)
            self.player.setAudioOutput(self.audio_output)
            self.player.mediaStatusChanged.connect(self.on_media_status_changed)
            self.player.errorOccurred.connect(self.on_media_error)

        self._set_controls_enabled(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._install_shortcuts()

    # ------------------------------------------------------------------
    # widget helpers
    # ------------------------------------------------------------------
    def _make_button(self, text, qss_class, slot, width=None):
        button = QPushButton(text, self)
        button.clicked.connect(slot)
        button.setProperty("class", qss_class)
        button.setFixedHeight(40)
        if width:
            button.setFixedWidth(width)
        button.setEnabled(False)
        # Buttons never take keyboard focus: otherwise clicking one leaves the
        # arrow keys navigating between buttons instead of moving the video.
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _install_shortcuts(self):
        """Transport keys as window-level shortcuts.

        Bound to the window rather than handled in keyPressEvent so they fire
        no matter which widget holds the focus — a focused slider, combo box or
        list would otherwise swallow the arrow keys for its own navigation.
        Modal dialogs are their own window, so typing a class name containing
        ',' or 'm' is unaffected.
        """
        bindings = [
            ("Left", lambda: self.skip_seconds(-SKIP_SECONDS)),
            ("Right", lambda: self.skip_seconds(SKIP_SECONDS)),
            ("Shift+Left", lambda: self.step_frame(-1)),
            ("Shift+Right", lambda: self.step_frame(1)),
            (",", lambda: self.step_frame(-1)),
            (".", lambda: self.step_frame(1)),
            ("Space", self.toggle_play),
            ("M", self.toggle_mute),
            ("Ctrl+Z", self.undo_last),
            ("Ctrl+S", self.save_csvs),
            ("Delete", self.delete_selected_annotation),
            ("Return", self._on_return),
            ("Enter", self._on_return),
            ("Shift+Return", lambda: self.jump_to_selected_annotation(to_end=True)),
            ("Shift+Enter", lambda: self.jump_to_selected_annotation(to_end=True)),
            ("E", self.end_with_key),
            ("S", self.start_with_key),
            ("K", self.keyframe_with_key),
            ("Escape", self._on_escape),
        ]
        self._shortcuts = []
        for sequence, slot in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    def _on_return(self):
        """Enter: go to the typed frame while the frame field has the focus,
        otherwise to the annotation selected in the list."""
        if self.frame_edit.hasFocus():
            self.on_frame_entered()
        else:
            self.jump_to_selected_annotation()

    def _set_button_class(self, button, qss_class):
        button.setProperty("class", qss_class)
        button.style().unpolish(button)
        button.style().polish(button)

    def _set_controls_enabled(self, enabled):
        for widget in (self.play_button, self.step_back_button, self.step_fwd_button,
                       self.skip_back_button, self.skip_fwd_button,
                       self.bbox_button, self.action_button,
                       self.save_button,
                       self.load_button, self.delete_button):
            widget.setEnabled(enabled)
        self.speed_combo.setEnabled(enabled)
        self.frame_edit.setEnabled(enabled)
        self.position_slider.setEnabled(enabled and self.total_frames > 0)
        self.frame_label.interactions_enabled = enabled
        self._refresh_mute_button()

    # ------------------------------------------------------------------
    # video loading / playback
    # ------------------------------------------------------------------
    def open_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", VIDEO_FILTER)
        if not path:
            return

        if self.dirty and not self._confirm_discard("Opening another video"):
            return

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            QMessageBox.critical(self, "Error", f"Could not open video:\n{path}")
            cap.release()
            return

        if self.cap is not None:
            self.cap.release()

        self.pause()
        self.cap = cap
        self.video_path = path
        self.video_name = os.path.basename(path)

        fps = cap.get(cv2.CAP_PROP_FPS)
        self.fps = float(fps) if fps and np.isfinite(fps) and fps > 0 else FALLBACK_FPS

        total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        self.total_frames = int(total) if total and np.isfinite(total) and total > 0 else 0

        # Reset annotation + display state for the new video
        self.bboxes = []
        self.actions = []
        self.classes = []
        self.history = []
        self.dirty = False
        self.session_dir = None        # a new video starts a new session folder
        self.loaded_dir = None
        self.loaded_skipped = 0
        self.tool = None
        self.target = None
        self.bbox_first_corner = None
        self.frame_idx = -1
        self.current_frame = None
        self.zoom_factor = 1.0
        self.refresh_annotation_list()
        self._refresh_tool_buttons()

        # Hand the same file to the audio player; hasAudio() only becomes
        # meaningful once Qt reports the media as loaded (see
        # on_media_status_changed), so assume silence until then.
        if self.player is not None:
            self.audio_ready = False
            self.player.stop()
            self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
            self.player.setPlaybackRate(self.speed)

        self.position_slider.setMaximum(max(0, self.total_frames - 1))
        self.position_slider.setValue(0)
        self._set_controls_enabled(True)

        duration = self.total_frames / self.fps if self.total_frames else None
        length_txt = f"{self.total_frames} frames" if self.total_frames else "length unknown"
        self.video_info_label.setText(
            f"{self.video_name}  —  {self.fps:g} fps, {length_txt}"
            + (f", {format_time(duration)}" if duration else "")
        )
        self.hint_label.setText("Pause, then press Apparition or Action to annotate")

        if not self.seek_to(0):
            QMessageBox.critical(self, "Error", "Could not read the first frame of the video.")
            return
        self.setWindowTitle(f"Video Annotator — {self.video_name}")

    # ---- audio ----
    def on_media_status_changed(self, status):
        """Qt finished loading (or ran out of) the audio track."""
        if self.player is None:
            return
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,
                      QMediaPlayer.MediaStatus.BufferedMedia):
            self.audio_ready = self.player.hasAudio()
            self._refresh_mute_button()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.pause()

    def on_media_error(self, error, error_string=""):
        """No usable audio (missing codec, no backend, no track): stay silent."""
        self.audio_ready = False
        self._refresh_mute_button()
        if error_string:
            print(f"Audio unavailable: {error_string}")

    def _refresh_mute_button(self):
        if not AUDIO_SUPPORTED:
            self.mute_button.setEnabled(False)
            self.mute_button.setText("🔇")
            self.mute_button.setToolTip("Audio unavailable: PyQt6 has no QtMultimedia module")
            return
        self.mute_button.setEnabled(self.audio_ready)
        muted = bool(self.audio_output is not None and self.audio_output.isMuted())
        self.mute_button.setText("🔇" if muted else "🔊")
        if not self.audio_ready:
            self.mute_button.setToolTip("This video has no audio track")
        else:
            self.mute_button.setToolTip(
                ("Unmute the video sound (M)" if muted else "Mute the video sound (M)"))

    def toggle_mute(self):
        if self.audio_output is None:
            return
        self.audio_output.setMuted(not self.audio_output.isMuted())
        self._refresh_mute_button()
        self.hint_label.setText("Sound muted" if self.audio_output.isMuted() else "Sound on")

    def _sync_audio_position(self):
        """Point the audio at the frame currently on screen."""
        if self.audio_ready and self.player is not None:
            self.player.setPosition(int(round(1000.0 * self.frame_idx / self.fps)))

    def _audio_is_playing(self):
        return (self.audio_ready and self.player is not None
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)

    # ---- transport ----
    def toggle_play(self):
        if self.cap is None:
            return
        if self.playing:
            self.pause()
        else:
            self.play()

    def play(self):
        if self.cap is None or self.playing:
            return
        # Arming a tool pauses playback, so playing disarms the tools.
        self.tool = None
        self.bbox_first_corner = None
        self._refresh_tool_buttons()
        self.playing = True
        self.play_button.setText("⏸  Pause")
        if self.audio_ready and self.player is not None:
            self.player.setPlaybackRate(self.speed)
            self._sync_audio_position()
            self.player.play()
        self.timer.start(self._timer_interval())
        self.show_frame()

    def pause(self):
        if self.player is not None:
            self.player.pause()
        if not self.playing:
            self.play_button.setText("▶  Play")
            return
        self.playing = False
        self.timer.stop()
        self.play_button.setText("▶  Play")
        self.show_frame()

    def _timer_interval(self):
        interval = 1000.0 / (self.fps * self.speed)
        return max(1, int(round(interval)))

    def on_speed_changed(self, _index):
        self.speed = self.speed_combo.currentData()
        if self.player is not None:
            self.player.setPlaybackRate(self.speed)
        if self.playing:
            self.timer.start(self._timer_interval())

    def _grab_frame(self):
        """Read the next frame from the capture and store it as RGB."""
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return False
        self.current_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return True

    def advance_frame(self):
        """Timer tick: show the frame that is due now.

        With sound, the audio clock decides which frame is due — that keeps
        picture and sound together even when decoding cannot keep up, and
        drops frames instead of drifting. Without sound, the timer simply
        walks one frame per tick."""
        if self.cap is None:
            return

        step = 1
        if self._audio_is_playing():
            target = int(round(self.player.position() / 1000.0 * self.fps))
            if target <= self.frame_idx:
                return                      # the sound has not reached the next frame yet
            step = target - self.frame_idx
            if step > MAX_CATCHUP_FRAMES:
                # Way behind (a stall, or the user seeked the audio): jump
                # there without tugging the audio back.
                if not self.seek_to(target, sync_audio=False):
                    self.pause()
                    self.hint_label.setText("End of video")
                return

        # Decode only the frame we are going to show; skip over the rest.
        for _ in range(step - 1):
            if not self.cap.grab():
                self.pause()
                self.hint_label.setText("End of video")
                return
        if not self._grab_frame():
            self.pause()
            self.hint_label.setText("End of video")
            return
        self.frame_idx += step
        self.show_frame()
        self.update_status()

    def seek_to(self, index, sync_audio=True):
        """Jump to an absolute frame index and display it."""
        if self.cap is None:
            return False
        index = max(0, index)
        if self.total_frames:
            index = min(index, self.total_frames - 1)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        if not self._grab_frame():
            # Past the end (or a container that lies about its length): clamp by
            # rewinding one frame and trying again.
            if index > 0:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, index - 1)
                if self._grab_frame():
                    self.frame_idx = index - 1
                    self.show_frame()
                    self.update_status()
                    if sync_audio:
                        self._sync_audio_position()
            return False
        self.frame_idx = index
        self.show_frame()
        self.update_status()
        if sync_audio:
            self._sync_audio_position()
        return True

    def step_frame(self, delta):
        """Move exactly `delta` frames — always pauses, for frame-exact work."""
        if self.cap is None:
            return
        self.pause()
        self.seek_to(self.frame_idx + delta)

    def skip_seconds(self, seconds):
        """Jump `seconds` back or forward, keeping playback running if it was.

        This is the YouTube-style coarse move used to find an animal; the
        ±1 frame buttons are the fine move used to pin down its first frame.
        """
        if self.cap is None:
            return
        target = int(round(self.frame_idx + seconds * self.fps))
        if not self.seek_to(target) and seconds > 0:
            self.pause()
            self.hint_label.setText("End of video")
            return
        self.hint_label.setText(
            f"{'Forward' if seconds > 0 else 'Back'} {abs(seconds):g}s → frame {self.frame_idx}")

    def on_slider_moved(self, value):
        """Click or drag anywhere on the bar: the playhead moves there.

        Playback keeps whatever state it had, like a web video player — click
        while playing and it plays on from the new spot."""
        if self.cap is None:
            return
        self.seek_to(value)

    def on_slider_released(self):
        if self.cap is not None:
            self.seek_to(self.position_slider.value())

    def update_status(self):
        if self.cap is None:
            self.frame_edit.clear()
            self.status_label.setText("/ -  —  --:-- / --:--")
            return
        total_txt = str(self.total_frames) if self.total_frames else "?"
        current_time = self.frame_idx / self.fps if self.frame_idx >= 0 else 0.0
        total_time = self.total_frames / self.fps if self.total_frames else None
        total_time_txt = format_time(total_time, millis=False) if total_time else "--:--"
        # Leave the field alone while the user is typing a frame into it
        if not self.frame_edit.hasFocus():
            self.frame_edit.setText(str(self.frame_idx))
        self.status_label.setText(
            f"/ {total_txt}  —  {format_time(current_time, millis=False)} / {total_time_txt}")
        if self.total_frames:
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(min(self.frame_idx, self.total_frames - 1))
            self.position_slider.blockSignals(False)
        self._highlight_current_box()

    def on_frame_entered(self):
        text = self.frame_edit.text().strip()
        # Hand the keyboard back to the video so the transport keys work again
        self.setFocus()
        if self.cap is None or not text:
            self.update_status()
            return
        self.pause()
        self.seek_to(int(text))
        self.update_status()     # shows the clamped frame if it was past the end

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------
    def class_color(self, class_name):
        """Deterministic colour for a class: its position in self.classes."""
        if class_name in self.classes:
            return PALETTE[self.classes.index(class_name) % len(PALETTE)]
        return PALETTE[len(self.classes) % len(PALETTE)]

    def _draw_scale(self):
        """Overlay marks are drawn on the full-resolution frame, so their size
        has to follow the video resolution — a 5 px dot is invisible on 4K
        footage shrunk to fit the window."""
        if self.current_frame is None:
            return 1.0
        return max(1.0, self.current_frame.shape[1] / 960.0)

    def _draw_caption(self, image, text, anchor, color):
        """Draw a small class caption with a dark backdrop for legibility."""
        x, y = anchor
        s = self._draw_scale()
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.5 * s
        thickness = max(1, int(round(s)))
        (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
        h, w = image.shape[:2]
        tx = int(min(max(0, x), max(0, w - tw - 2)))
        ty = int(y)
        if ty - th - 4 < 0:
            ty = th + 6
        cv2.rectangle(image, (tx, ty - th - baseline - 2), (tx + tw + 4, ty + 2), (0, 0, 0), -1)
        cv2.putText(image, text, (tx + 2, ty - baseline + 1), font, scale, color, thickness, cv2.LINE_AA)

    def action_color(self, action_type):
        return ACTION_COLORS.get(action_type, UNKNOWN_ACTION_COLOR)

    def build_overlay(self, cursor_point=None):
        """Current frame plus the annotations visible on this frame."""
        overlay = self.current_frame.copy()
        s = self._draw_scale()
        thickness = max(1, int(round(2 * s)))
        arm = max(6, int(round(8 * s)))

        for ann in self.bboxes:
            if ann["frame"] != self.frame_idx:
                continue
            color = self.class_color(ann["class_name"])
            x, y, w, h = ann["x"], ann["y"], ann["width"], ann["height"]
            cv2.rectangle(overlay, (x, y), (x + w, y + h), color, thickness)
            self._draw_caption(overlay, ann["class_name"], (x, y - thickness), color)

        banner = self._draw_actions(overlay, thickness)
        line_h = int(round(22 * s))
        for i, (text, color) in enumerate(banner):
            self._draw_caption(overlay, text, (4, line_h * (i + 1)), color)

        # Rubber band between the first and second corner click
        if self.tool is not None and self.bbox_first_corner is not None:
            cx, cy = self.bbox_first_corner
            if cursor_point is not None:
                x1, x2 = sorted((cx, cursor_point[0]))
                y1, y2 = sorted((cy, cursor_point[1]))
                cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 255), thickness)
            cv2.line(overlay, (cx - arm, cy), (cx + arm, cy), (0, 255, 255), thickness)
            cv2.line(overlay, (cx, cy - arm), (cx, cy + arm), (0, 255, 255), thickness)

        return overlay

    @staticmethod
    def _interval_active(start, end, frame):
        """True when `frame` lies inside [start, end] (open ones never end)."""
        if frame < start:
            return False
        return end is None or frame <= end

    def _shown_on_frame(self, index, ann):
        """Is this action drawn on the current frame?

        A closed one shows over its whole interval, an open one (in progress)
        from its start onwards. Except open actions with no type — old points
        converted on load, possibly dozens — which would clutter every later
        frame: those show past their start only while selected in the list.
        """
        start, end = ann["start_frame"], ann["end_frame"]
        worked_on = (self.target is not None and self.target[0] == index) or \
            self._selected_annotation() == ("action", index)
        if end is None and not ann["action"] and not worked_on:
            end = start
        return self._interval_active(start, end, self.frame_idx)

    @staticmethod
    def _action_keyframes(ann):
        """Intermediate boxes inside the action's interval: [(frame, box)] in
        time order."""
        start, end = ann["start_frame"], ann["end_frame"]
        return [(f, box) for f, box in sorted(ann["keyframes"].items())
                if f > start and (end is None or f < end)]

    @classmethod
    def _action_track(cls, ann):
        """Every box drawn for the action, [(frame, box)] in time order: start,
        intermediate ones, end (if its box was drawn)."""
        track = [(ann["start_frame"], ann["start_box"])] + cls._action_keyframes(ann)
        if ann["end_frame"] is not None and ann["end_box"] is not None:
            track.append((ann["end_frame"], ann["end_box"]))
        return track

    @classmethod
    def _action_box_at(cls, ann, frame):
        """The action's box on `frame`: sliding linearly from each drawn box
        (start, intermediate, end) to the next one — a guide only between
        them. Past the last drawn box it stays there."""
        track = cls._action_track(ann)
        if frame <= track[0][0]:
            return track[0][1]
        for (f0, box0), (f1, box1) in zip(track, track[1:]):
            if frame <= f1 and f1 > f0:
                t = (frame - f0) / (f1 - f0)
                return tuple(int(round(a + (b - a) * t)) for a, b in zip(box0, box1))
        return track[-1][1]

    @staticmethod
    def _span_text(start, end):
        return f"f{start}-" + ("open" if end is None else f"f{end}")

    @staticmethod
    def _participant_span(ann, participant):
        """(join, leave) frames of an individual; leave None while open."""
        join = participant["join"] if participant["join"] is not None else ann["start_frame"]
        leave = participant["leave"] if participant["leave"] is not None else ann["end_frame"]
        return join, leave

    def _active_participants(self, ann, frame):
        return [p for p in ann["participants"]
                if self._interval_active(*self._participant_span(ann, p), frame)]

    @staticmethod
    def _species_text(species):
        """'Carcinus maenas ×2 + Libinia emarginata' — counts, first-seen order."""
        counts = {}
        for name in species:
            counts[name] = counts.get(name, 0) + 1
        return " + ".join(name if n == 1 else f"{name} ×{n}" for name, n in counts.items())

    def _action_label(self, ann, frame=None):
        """Type and individuals; with `frame`, only those in it on that frame."""
        people = (ann["participants"] if frame is None
                  else self._active_participants(ann, frame))
        return f"{ann['action'] or '?'}: {self._species_text([p['species'] for p in people])}"

    def _draw_dashed_rect(self, image, p1, p2, color, thickness):
        (x1, y1), (x2, y2) = p1, p2
        dash = max(6, thickness * 4)
        for a, b, fixed, horizontal in ((x1, x2, y1, True), (x1, x2, y2, True),
                                        (y1, y2, x1, False), (y1, y2, x2, False)):
            for start in range(a, b, dash * 2):
                end = min(start + dash, b)
                if horizontal:
                    cv2.line(image, (start, fixed), (end, fixed), color, thickness)
                else:
                    cv2.line(image, (fixed, start), (fixed, end), color, thickness)

    def _draw_actions(self, overlay, thickness):
        """Solid boxes on an action's first and last frame and on its
        intermediate boxes, a dashed guide box in between. Returns the lines
        of the in-progress banner."""
        banner = []
        for i, ann in enumerate(self.actions):
            if not self._shown_on_frame(i, ann):
                continue
            color = self.action_color(ann["action"])
            label = self._action_label(ann, self.frame_idx)
            x, y, w, h = self._action_box_at(ann, self.frame_idx)
            if self.frame_idx == ann["start_frame"]:
                cv2.rectangle(overlay, (x, y), (x + w, y + h), color, thickness * 2)
                self._draw_caption(overlay, f"START {label}", (x, y - thickness), color)
            elif self.frame_idx == ann["end_frame"]:
                cv2.rectangle(overlay, (x, y), (x + w, y + h), color, thickness * 2)
                self._draw_caption(overlay, f"END {label}", (x, y - thickness), color)
            elif self.frame_idx in dict(self._action_keyframes(ann)):
                cv2.rectangle(overlay, (x, y), (x + w, y + h), color, thickness * 2)
                self._draw_caption(overlay, f"BOX {label}", (x, y - thickness), color)
            else:
                self._draw_dashed_rect(overlay, (x, y), (x + w, y + h), color,
                                       max(1, thickness // 2))
            banner.append((f"{label}  {self._span_text(ann['start_frame'], ann['end_frame'])}",
                           color))
        return banner

    def show_frame(self, cursor_point=None):
        if self.current_frame is None:
            return
        self._render_pixmap_to_label(self.build_overlay(cursor_point))

    def _base_fit_scale(self, width, height):
        """Scale that fits a (width x height) frame inside the scroll-area
        viewport while preserving aspect ratio. This is the zoom == 1.0
        ("100%", fit-to-window) reference; actual display scale multiplies
        this by self.zoom_factor."""
        vp = self.scroll_area.viewport().size()
        vw, vh = vp.width(), vp.height()
        if vw <= 0 or vh <= 0 or width <= 0 or height <= 0:
            return 1.0
        return min(vw / width, vh / height)

    def _render_pixmap_to_label(self, overlay_image):
        """Render a full-resolution RGB overlay into the frame label at the
        current zoom level. The label is sized exactly to the scaled pixmap so
        that (a) get_image_coordinates keeps a zero centering offset and (b) the
        scroll area shows scrollbars when the zoomed frame overflows."""
        overlay_image = np.ascontiguousarray(overlay_image)
        height, width, _ = overlay_image.shape
        bytes_per_line = 3 * width
        qimage = QImage(overlay_image.data, width, height, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimage)

        scale = self._base_fit_scale(width, height) * self.zoom_factor
        disp_w = max(1, int(round(width * scale)))
        disp_h = max(1, int(round(height * scale)))
        scaled_pixmap = pixmap.scaled(
            disp_w, disp_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.displayed_pixmap = scaled_pixmap
        self.frame_label.setPixmap(scaled_pixmap)
        self.frame_label.setFixedSize(scaled_pixmap.size())

    def on_ctrl_wheel_zoom(self, event):
        """Zoom in/out on Ctrl+wheel, centered on the cursor.

        Wheel forward zooms in; wheel backward zooms out, clamped so it never
        goes below 100% (fit-to-window). Annotation coordinates are unaffected —
        only the on-screen display scale changes."""
        if self.current_frame is None or self.displayed_pixmap is None:
            return

        delta = event.angleDelta().y()
        if delta == 0:
            return

        old_zoom = self.zoom_factor
        step = 1.25 if delta > 0 else 1.0 / 1.25
        new_zoom = max(self.min_zoom, min(old_zoom * step, self.max_zoom))
        if abs(new_zoom - old_zoom) < 1e-6:
            return

        # Frame fraction currently under the cursor (in label/content coords),
        # clamped in case the cursor sits in the centered margin at zoom 1.0.
        gpos = event.globalPosition().toPoint()
        cursor_lbl = self.frame_label.mapFromGlobal(gpos)
        old_w = max(1, self.frame_label.width())
        old_h = max(1, self.frame_label.height())
        fx = min(1.0, max(0.0, cursor_lbl.x() / old_w))
        fy = min(1.0, max(0.0, cursor_lbl.y() / old_h))

        # Where the cursor sits inside the viewport (target it stays fixed at).
        cursor_vp = self.scroll_area.viewport().mapFromGlobal(gpos)

        self.zoom_factor = new_zoom
        self.show_frame()

        # Re-anchor: keep the same frame fraction under the cursor.
        new_x = fx * self.frame_label.width()
        new_y = fy * self.frame_label.height()
        self.scroll_area.horizontalScrollBar().setValue(int(round(new_x - cursor_vp.x())))
        self.scroll_area.verticalScrollBar().setValue(int(round(new_y - cursor_vp.y())))

    def get_image_coordinates(self, pos):
        """Convert a click position on the label into original-resolution
        (x, y) frame coordinates. Returns None when the click misses the frame."""
        if self.displayed_pixmap is None or self.current_frame is None:
            return None

        label_width = self.frame_label.width()
        label_height = self.frame_label.height()
        pixmap_width = self.displayed_pixmap.width()
        pixmap_height = self.displayed_pixmap.height()

        offset_x = (label_width - pixmap_width) / 2
        offset_y = (label_height - pixmap_height) / 2

        if not (offset_x <= pos.x() <= offset_x + pixmap_width and
                offset_y <= pos.y() <= offset_y + pixmap_height):
            return None

        original_h, original_w, _ = self.current_frame.shape
        ratio_x = original_w / pixmap_width
        ratio_y = original_h / pixmap_height

        orig_x = int((pos.x() - offset_x) * ratio_x)
        orig_y = int((pos.y() - offset_y) * ratio_y)
        # Guard against rounding landing exactly on the far edge
        orig_x = min(max(0, orig_x), original_w - 1)
        orig_y = min(max(0, orig_y), original_h - 1)
        return (orig_x, orig_y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.current_frame is not None:
            self.show_frame()

    # ------------------------------------------------------------------
    # annotation tools
    # ------------------------------------------------------------------
    def _refresh_tool_buttons(self):
        which = self.target[1] if self.target else None
        self.action_button.setText(
            {"endbox": "Skip end box", "start": "Cancel", "key": "Cancel"}.get(which, "Action"))
        self._set_button_class(
            self.bbox_button,
            "bbox-button-active" if self.tool == "bbox" else "bbox-button-idle")
        self._set_button_class(
            self.action_button,
            "action-button-active" if self.tool == "action" else "action-button-idle")
        if self.tool is None:
            self.frame_label.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.frame_label.setCursor(Qt.CursorShape.CrossCursor)

    def _on_escape(self):
        """Esc disarms the tool / cancels a half-drawn box, and nothing else.
        An action waiting for its end box stays in the list, open."""
        if self.frame_edit.hasFocus():
            # Abandon the typed frame number
            self.setFocus()
            self.update_status()
            return
        if self.tool is None and self.bbox_first_corner is None:
            return
        target = self.target
        self.set_tool(None)
        if target is None or target[0] >= len(self.actions):
            return
        ann = self.actions[target[0]]
        if target[1] == "endbox":
            self.hint_label.setText(
                f"{self._action_label(ann)} ended at frame {ann['end_frame']} (no end box drawn)")
        elif target[1] == "start":
            self.hint_label.setText("Start box unchanged")
        elif target[1] == "key":
            self.hint_label.setText("No intermediate box added")

    def toggle_bbox_tool(self):
        self.set_tool(None if self.tool == "bbox" else "bbox")

    def toggle_action_tool(self):
        """Starts a new action — always, even with others in progress; those
        are ended with their own "End …" buttons. While the tool is drawing
        an end / start box, pressing it again skips / cancels that box."""
        if self.tool == "action":
            self._on_escape()
        else:
            self.set_tool("action")

    def set_tool(self, tool):
        if self.cap is None:
            return
        if tool is not None:
            self.pause()          # annotation always happens on a paused frame
        self.tool = tool
        self.target = None
        self.bbox_first_corner = None
        self._refresh_tool_buttons()
        if tool == "bbox":
            self.hint_label.setText("Click two opposite corners of the box")
        elif tool == "action":
            self.hint_label.setText(
                "On the frame where the action starts, click two corners around it")
        else:
            self.hint_label.setText("Tool disarmed")
        self.show_frame()

    def _arm_endpoint(self, index, which):
        """Arm the Action tool to draw the start, end or an intermediate box
        of an action."""
        self.set_tool("action")
        self.target = (index, which)
        self._refresh_tool_buttons()
        ann = self.actions[index]
        if which == "endbox":
            self.hint_label.setText(
                f"{self._action_label(ann)} ended at frame {ann['end_frame']} — click two "
                f"corners around where it ends (Esc: no end box)")
        elif which == "key":
            self.hint_label.setText(
                f"{self._action_label(ann)} — click two corners around where it is on "
                f"frame {self.frame_idx} (Esc: cancel)")
        else:
            self.hint_label.setText(
                f"{self._action_label(ann)} — on its FIRST frame, click two corners "
                f"around where it starts (Esc: cancel)")
        self.show_frame()

    def on_frame_clicked(self, pos):
        if self.current_frame is None or self.tool is None:
            return
        point = self.get_image_coordinates(pos)
        if point is None:
            return

        # Both tools draw a box: click 1 = first corner, click 2 = opposite corner
        if self.bbox_first_corner is None:
            self.bbox_first_corner = point
            self.hint_label.setText("Click the opposite corner")
            self.show_frame(point)
            return

        x1, x2 = sorted((self.bbox_first_corner[0], point[0]))
        y1, y2 = sorted((self.bbox_first_corner[1], point[1]))
        if x2 - x1 < 2 or y2 - y1 < 2:
            self.hint_label.setText("Box too small — click a wider opposite corner")
            return
        box = (x1, y1, x2 - x1, y2 - y1)
        self.bbox_first_corner = None

        if self.tool == "action":
            if self.target is not None:
                if self.target[1] == "key":
                    self.set_keyframe(self.target[0], box)
                else:
                    self.set_endpoint(*self.target, box)
                return
            details = self.ask_action_details()
            if details is None:
                self.hint_label.setText("Action discarded")
                self.show_frame()
                return
            self.add_action(box, *details)
            self.set_tool(None)
            self.hint_label.setText(
                f"{self._action_label(self.actions[-1])} in progress — on its last frame "
                f"press End under 'In progress' (or E)")
            return

        class_name = self.ask_class_name()
        if class_name is None:
            self.hint_label.setText("Box discarded")
            self.show_frame()
            return
        self.add_bbox(*box, class_name)
        self.hint_label.setText("Click two opposite corners of the box")

    def on_mouse_moved(self, pos):
        if self.tool is None or self.bbox_first_corner is None:
            return
        point = self.get_image_coordinates(pos)
        if point is None:
            return
        self.show_frame(point)

    def ask_class_name(self):
        """Ask for the class of the annotation just placed. None => cancelled."""
        preselect = self.classes[-1] if self.classes else None
        dialog = LabelDialog(self.classes, self, preselect=preselect)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        name = (dialog.selected_label or "").strip()
        return name or None

    def ask_action_details(self, current=None):
        """Ask for the action type and the species performing it.

        Returns (type, [classes]) or None when cancelled. `current` is the
        action being edited; a new action starts from the previous one's
        answers, since the same action tends to be annotated in a row.
        """
        template = current or (self.actions[-1] if self.actions else None)
        dialog = ActionDialog(
            ACTION_TYPES, self.classes, self,
            preselect_action=template["action"] if template else None,
            preselect_classes=[p["species"] for p in template["participants"]] if template else None,
            multi_species_types=MULTI_SPECIES_TYPES,
            unknown_color=UNKNOWN_ACTION_COLOR)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_action, dialog.selected_classes

    def _register_class(self, class_name):
        if class_name not in self.classes:
            self.classes.append(class_name)

    def _store(self, kind):
        return {"bbox": self.bboxes, "action": self.actions}[kind]

    @staticmethod
    def _start_frame(kind, ann):
        return ann["start_frame"] if kind == "action" else ann["frame"]

    def _name(self, kind, ann):
        return self._action_label(ann) if kind == "action" else ann["class_name"]

    def add_bbox(self, x, y, width, height, class_name):
        self._register_class(class_name)
        self.bboxes.append({
            "frame": self.frame_idx,
            "time_sec": self.frame_idx / self.fps,
            "class_name": class_name,
            "x": int(x),
            "y": int(y),
            "width": int(width),
            "height": int(height),
        })
        self.history.append(("add", "bbox", len(self.bboxes) - 1))
        self.dirty = True
        self.refresh_annotation_list()
        self.show_frame()

    @staticmethod
    def _new_participants(species):
        return [{"species": name, "join": None, "leave": None} for name in species]

    def add_action(self, box, action, species):
        for name in species:
            self._register_class(name)
        self.actions.append({
            "action": action,
            "participants": self._new_participants(species),
            "start_frame": self.frame_idx,
            "start_box": tuple(int(v) for v in box),
            "end_frame": None,
            "end_box": None,
            "keyframes": {},
        })
        self.history.append(("add", "action", len(self.actions) - 1))
        self.dirty = True
        self.refresh_annotation_list(select=("action", len(self.actions) - 1))
        self.show_frame()

    def set_endpoint(self, index, which, box):
        """Put the start or end of an action on the current frame, with `box`."""
        if index >= len(self.actions):
            return False
        if which == "endbox":
            which = "end"
        ann = self.actions[index]
        frame = self.frame_idx
        if which == "end" and frame < ann["start_frame"]:
            self.hint_label.setText(
                f"It starts at frame {ann['start_frame']} — draw its end box on a later frame")
            self.show_frame()
            return False
        if which == "start" and ann["end_frame"] is not None and frame > ann["end_frame"]:
            self.hint_label.setText(
                f"It ends at frame {ann['end_frame']} — draw its start box on an earlier frame")
            self.show_frame()
            return False
        self.history.append(("endpoint", "action", index, which,
                             ann[f"{which}_frame"], ann[f"{which}_box"]))
        ann[f"{which}_frame"] = frame
        ann[f"{which}_box"] = tuple(int(v) for v in box)
        self.dirty = True
        self.set_tool(None)
        self.refresh_annotation_list(select=("action", index))
        self.show_frame()
        text = f"{self._action_label(ann)}  {self._span_text(ann['start_frame'], ann['end_frame'])}"
        if ann["end_frame"] is not None:
            text += f" ({(ann['end_frame'] - ann['start_frame']) / self.fps:.2f}s)"
        self.hint_label.setText(text)
        return True

    def _keyframe_allowed(self, ann, frame):
        """Hint text if an intermediate box cannot go on `frame`, else None."""
        start, end = ann["start_frame"], ann["end_frame"]
        if frame < start or (end is not None and frame > end):
            return f"Go to a frame inside the action ({self._span_text(start, end)}) first"
        return None

    def arm_keyframe(self, index):
        """Arm the Action tool to draw an intermediate box of an action on
        the current frame — on its first / last frame, that redraws the
        start / end box instead."""
        ann = self.actions[index]
        problem = self._keyframe_allowed(ann, self.frame_idx)
        if problem:
            self.hint_label.setText(problem)
            return
        if self.frame_idx == ann["start_frame"]:
            self._arm_endpoint(index, "start")
        elif self.frame_idx == ann["end_frame"]:
            self._arm_endpoint(index, "endbox")
        else:
            self._arm_endpoint(index, "key")

    def set_keyframe(self, index, box):
        """Put an intermediate box of an action on the current frame (it
        replaces the one already there, if any)."""
        if index >= len(self.actions):
            return False
        ann = self.actions[index]
        problem = self._keyframe_allowed(ann, self.frame_idx)
        if problem:
            self.hint_label.setText(problem)
            self.show_frame()
            return False
        self.history.append(("snapshot", "action", index, copy.deepcopy(ann)))
        ann["keyframes"][self.frame_idx] = tuple(int(v) for v in box)
        self.dirty = True
        self.set_tool(None)
        self.refresh_annotation_list(select=("action", index))
        self.show_frame()
        self.hint_label.setText(
            f"{self._action_label(ann)} — box on frame {self.frame_idx} "
            f"({len(self._action_keyframes(ann))} intermediate)")
        return True

    def remove_keyframe(self, index, frame):
        ann = self.actions[index]
        if frame not in ann["keyframes"]:
            return
        self.history.append(("snapshot", "action", index, copy.deepcopy(ann)))
        del ann["keyframes"][frame]
        self.dirty = True
        self.refresh_annotation_list(select=("action", index))
        self.show_frame()
        self.hint_label.setText(f"{self._action_label(ann)} — box on frame {frame} removed")

    def participant_joins(self, index):
        """A new individual joins the action on the current frame."""
        ann = self.actions[index]
        frame = self.frame_idx
        if not self._interval_active(ann["start_frame"], ann["end_frame"], frame):
            self.hint_label.setText("Go to a frame inside the action first")
            return
        species = self.ask_class_name()
        if species is None:
            return
        self._register_class(species)
        self.history.append(("snapshot", "action", index, copy.deepcopy(ann)))
        ann["participants"].append(
            {"species": species, "join": None if frame == ann["start_frame"] else frame,
             "leave": None})
        self.dirty = True
        self.refresh_annotation_list(select=("action", index))
        self.show_frame()
        self.hint_label.setText(f"{species} joins at frame {frame} — {self._action_label(ann, frame)}")

    def participant_leaves(self, index, individual):
        """Individual number `individual` leaves the action on the current frame."""
        ann = self.actions[index]
        participant = ann["participants"][individual]
        join, _leave = self._participant_span(ann, participant)
        frame = self.frame_idx
        if frame < join:
            self.hint_label.setText(f"It joined at frame {join} — it must leave after that")
            return
        self.history.append(("snapshot", "action", index, copy.deepcopy(ann)))
        participant["leave"] = None if frame == ann["end_frame"] else frame
        self.dirty = True
        self.refresh_annotation_list(select=("action", index))
        self.show_frame()
        self.hint_label.setText(
            f"{participant['species']} leaves at frame {frame} — {self._action_label(ann, frame)}")

    def end_action_here(self, index):
        """End an action on the current frame, then ask for its end box."""
        ann = self.actions[index]
        if self.frame_idx < ann["start_frame"]:
            self.hint_label.setText(
                f"{self._action_label(ann)} starts at frame {ann['start_frame']} — "
                f"go to a later frame to end it")
            return
        self.pause()
        self.history.append(("endpoint", "action", index, "end",
                             ann["end_frame"], ann["end_box"]))
        ann["end_frame"] = self.frame_idx
        self.dirty = True
        self.refresh_annotation_list(select=("action", index))
        self._arm_endpoint(index, "endbox")

    def _pick_action_to_end(self):
        """The action E should end: the one selected in the list; otherwise
        the only one in progress; with several in progress, ask which."""
        selected = self._selected_annotation()
        if selected is not None and selected[0] == "action":
            return selected[1]
        in_progress = [i for i, a in enumerate(self.actions)
                       if a["end_frame"] is None and a["start_frame"] <= self.frame_idx]
        if not in_progress:
            self.hint_label.setText("No action in progress here — select one in the list")
            return None
        if len(in_progress) == 1:
            return in_progress[0]
        menu = QMenu(self)
        menu.addSection("End which action?")
        for i in sorted(in_progress, key=lambda i: -self.actions[i]["start_frame"]):
            ann = self.actions[i]
            since = format_time(ann["start_frame"] / self.fps, millis=False)
            item = menu.addAction(color_icon(self.action_color(ann["action"])),
                                  f"{self._action_label(ann)}   (since {since})")
            item.setData(i)
        chosen = menu.exec(QCursor.pos())
        return None if chosen is None else chosen.data()

    def start_with_key(self):
        """S: redraw the start box of the selected action on the current frame."""
        if self.cap is None:
            return
        if self.target is not None:
            index = self.target[0]
        else:
            selected = self._selected_annotation()
            if selected is None or selected[0] != "action":
                self.hint_label.setText("Select an action in the list first")
                return
            index = selected[1]
        self._arm_endpoint(index, "start")

    def keyframe_with_key(self):
        """K: draw an intermediate box on the current frame for the action the
        tool is working on, else the selected one, else the only one shown."""
        if self.cap is None:
            return
        if self.target is not None:
            index = self.target[0]
        else:
            selected = self._selected_annotation()
            if selected is not None and selected[0] == "action":
                index = selected[1]
            else:
                shown = [i for i, a in enumerate(self.actions) if self._shown_on_frame(i, a)]
                if len(shown) != 1:
                    self.hint_label.setText("Select an action in the list first")
                    return
                index = shown[0]
        self.arm_keyframe(index)

    def end_with_key(self):
        """E: end an action on the current frame — the one the tool is working
        on, else the selected one, else the one (or a chosen one) in progress."""
        if self.cap is None:
            return
        index = self.target[0] if self.target is not None else self._pick_action_to_end()
        if index is not None:
            self.end_action_here(index)

    def undo_last(self):
        """Remove the most recently added annotation (or redrawn start / end)."""
        while self.history:
            entry = self.history.pop()
            op, kind, index = entry[:3]
            store = self._store(kind)
            if index >= len(store):
                continue
            if op == "snapshot":
                store[index] = entry[3]
                self.dirty = True
                self.refresh_annotation_list()
                self.show_frame()
                self.hint_label.setText(f"Undid the change to {self._name(kind, entry[3])}")
                return
            if op == "endpoint":
                which, prev_frame, prev_box = entry[3:]
                ann = store[index]
                ann[f"{which}_frame"] = prev_frame
                ann[f"{which}_box"] = prev_box
                self.dirty = True
                self.refresh_annotation_list()
                self.show_frame()
                self.hint_label.setText(f"Undid the {which} of {self._name(kind, ann)}")
                return
            removed = store.pop(index)
            self._reindex_history(kind, index)
            self.dirty = True
            self.refresh_annotation_list()
            self.show_frame()
            self.hint_label.setText(
                f"Undid {self.KIND_LABELS[kind].lower()} '{self._name(kind, removed)}' "
                f"at frame {self._start_frame(kind, removed)}")
            return
        self.hint_label.setText("Nothing to undo")

    def _reindex_history(self, kind, removed_index):
        """Keep undo indices (and the tool's target) valid after an annotation
        is removed."""
        updated = []
        for entry in self.history:
            op, h_kind, h_index = entry[:3]
            if h_kind == kind:
                if h_index == removed_index:
                    continue
                if h_index > removed_index:
                    entry = (op, h_kind, h_index - 1) + tuple(entry[3:])
            updated.append(entry)
        self.history = updated

        if kind == "action" and self.target is not None:
            if self.target[0] == removed_index:
                # What the tool was waiting for is gone: back to a new action
                self.target = None
                self._refresh_tool_buttons()
            elif self.target[0] > removed_index:
                self.target = (self.target[0] - 1, self.target[1])

    # ------------------------------------------------------------------
    # annotation list / editing
    # ------------------------------------------------------------------
    KIND_LABELS = {"bbox": "Apparition", "action": "Action"}

    @staticmethod
    def _list_time_text(seconds):
        """h:m:s plus whole seconds, truncated so they agree with the h:m:s."""
        return f"{format_time(seconds)} ({int(seconds)}s)"

    def _list_entry_text(self, kind, ann):
        """Type first, then the species, then when — frame numbers are left to
        the tooltip (see _list_entry_tooltip)."""
        start = self._start_frame(kind, ann)
        when = self._list_time_text(start / self.fps)
        if kind == "bbox":
            return (f"Apparition  ·  {ann['class_name']}  ·  {when}  "
                    f"({ann['x']}, {ann['y']}) {ann['width']}×{ann['height']}")
        if ann["end_frame"] is None:
            when += " → OPEN"
        else:
            when += (f" → {self._list_time_text(ann['end_frame'] / self.fps)}  "
                     f"[{(ann['end_frame'] - start) / self.fps:.2f}s]")
        species = self._species_text([p["species"] for p in ann["participants"]])
        keys = len(self._action_keyframes(ann))
        if keys:
            when += f"  +{keys} box{'es' if keys > 1 else ''}"
        return f"{ann['action'] or '?'}  ·  {species}  ·  {when}"

    def _list_entry_tooltip(self, kind, ann):
        start = self._start_frame(kind, ann)
        if kind == "bbox":
            return f"Frame {start}"
        tip = f"Frames {self._span_text(start, ann['end_frame'])}"
        if len(ann["participants"]) > 1:
            for i, p in enumerate(ann["participants"]):
                tip += f"\n  #{i + 1} {p['species']}  {self._span_text(*self._participant_span(ann, p))}"
        keys = self._action_keyframes(ann)
        if keys:
            tip += "\nIntermediate boxes on frames " + ", ".join(str(f) for f, _box in keys)
        if not ann["action"]:
            tip += "\nNo action type yet: right-click → Edit action"
        if ann["end_frame"] is None:
            tip += "\nOpen: go to its last frame and press E to draw its end box"
        return tip

    def _list_entry_icon(self, kind, ann):
        if kind == "bbox":
            return color_icon(self.class_color(ann["class_name"]))
        return color_icon(self.action_color(ann["action"]))

    def refresh_annotation_list(self, *_args, select=None):
        """Rebuild the list, keeping the selection (or selecting `select`)."""
        if select is None:
            select = self._selected_annotation()
        kinds, action_type = self.list_filter_combo.currentData() or LIST_FILTERS[0][1]
        entries = []
        for kind in kinds:
            for i, ann in enumerate(self._store(kind)):
                if action_type is not None and ann["action"] != action_type:
                    continue
                entries.append((self._start_frame(kind, ann), kind, i, ann))
        entries.sort(key=lambda e: e[:3])

        self.annotation_list.blockSignals(True)
        self.annotation_list.clear()
        for _frame, kind, index, ann in entries:
            item = QListWidgetItem(self._list_entry_icon(kind, ann),
                                   self._list_entry_text(kind, ann))
            item.setData(Qt.ItemDataRole.UserRole, (kind, index))
            item.setToolTip(self._list_entry_tooltip(kind, ann))
            self.annotation_list.addItem(item)
            if (kind, index) == select:
                self.annotation_list.setCurrentItem(item)
        self.annotation_list.blockSignals(False)
        self._refresh_in_progress()
        self._refresh_boxes_panel()

    # ------------------------------------------------------------------
    # actions in progress panel
    # ------------------------------------------------------------------
    def _refresh_in_progress(self):
        """Fill the "In progress" panel: every open action, newest first.

        Old points converted to actions (no type yet) are open too but are not
        in progress in any real sense, and may be dozens — they are left out
        (E or right-click still ends them). Selects the action selected in the
        list above if it is in progress (a new action is), else keeps the row
        selected before, else the newest."""
        selected = self._selected_annotation()
        previous = (selected[1] if selected is not None and selected[0] == "action"
                    and selected[1] < len(self.actions)
                    and self.actions[selected[1]]["end_frame"] is None
                    else self._selected_open_action())
        in_progress = sorted(
            ((i, a) for i, a in enumerate(self.actions) if a["end_frame"] is None and a["action"]),
            key=lambda e: -e[1]["start_frame"])

        self.open_list.blockSignals(True)
        self.open_list.clear()
        for index, ann in in_progress:
            species = self._species_text([p["species"] for p in ann["participants"]])
            since = format_time(ann["start_frame"] / self.fps, millis=False)
            item = QListWidgetItem(color_icon(self.action_color(ann["action"])),
                                   f"{ann['action']}  ·  {species}  ·  since {since}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(f"Started at frame {ann['start_frame']}")
            self.open_list.addItem(item)
            if index == previous:
                self.open_list.setCurrentItem(item)
        if self.open_list.currentItem() is None and self.open_list.count():
            self.open_list.setCurrentRow(0)
        self.open_list.blockSignals(False)

        self.open_label.setText(f"In progress ({len(in_progress)})" if in_progress
                                else "In progress — none")
        self._style_end_open_button()

    # ------------------------------------------------------------------
    # boxes panel (the selected action's boxes)
    # ------------------------------------------------------------------
    def _boxes_action(self):
        """Index of the action the boxes panel shows, or None."""
        selected = self._selected_annotation()
        if selected is None or selected[0] != "action" or selected[1] >= len(self.actions):
            return None
        return selected[1]

    def _refresh_boxes_panel(self):
        """List the selected action's boxes in time order; hide the panel
        when no action is selected."""
        index = self._boxes_action()
        self.boxes_panel.setVisible(index is not None)
        if index is None:
            return
        ann = self.actions[index]
        keys = self._action_keyframes(ann)
        self.boxes_label.setText(
            f"<b>Boxes of {self._action_label(ann)}</b> ({len(keys)} intermediate)<br>"
            f"Press <b>K</b> on a frame inside the action to add a box there "
            f"(or redraw the one already on it).")
        rows = [("START", ann["start_frame"], ann["start_box"])]
        rows += [("BOX", frame, box) for frame, box in keys]
        if ann["end_frame"] is not None:
            rows.append(("END", ann["end_frame"], ann["end_box"]))
        color = color_icon(self.action_color(ann["action"]))
        self.boxes_list.blockSignals(True)
        self.boxes_list.clear()
        for number, (which, frame, box) in enumerate(rows):
            name = f"#{number}" if which == "BOX" else which
            where = ("no box drawn" if box is None
                     else f"({box[0]}, {box[1]}) {box[2]}×{box[3]}")
            item = QListWidgetItem(
                color, f"{name:<6}  f{frame}  ·  {format_time(frame / self.fps, millis=False)}  ·  {where}")
            item.setData(Qt.ItemDataRole.UserRole, (which, frame))
            self.boxes_list.addItem(item)
        if ann["end_frame"] is None:
            item = QListWidgetItem("END     not marked yet (E on its last frame)")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.boxes_list.addItem(item)
        self.boxes_list.blockSignals(False)
        self._highlight_current_box()

    def _highlight_current_box(self):
        """Select the box drawn on the frame on screen (none between boxes)
        and enable the buttons that make sense here."""
        index = self._boxes_action()
        if index is None or not self.boxes_panel.isVisible():
            return
        ann = self.actions[index]
        current = None
        self.boxes_list.blockSignals(True)
        self.boxes_list.clearSelection()
        self.boxes_list.setCurrentItem(None)
        for row in range(self.boxes_list.count()):
            item = self.boxes_list.item(row)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data is not None and data[1] == self.frame_idx:
                self.boxes_list.setCurrentItem(item)
                self.boxes_list.scrollToItem(item)
                current = data[0]
        self.boxes_list.blockSignals(False)
        inside = self._keyframe_allowed(ann, self.frame_idx) is None
        self.add_box_button.setEnabled(self.cap is not None and inside)
        self.add_box_button.setText("Redraw box here  (K)" if current else "Add box here  (K)")
        self.remove_box_button.setEnabled(current == "BOX")

    def on_box_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None or self.cap is None:
            return
        self.pause()
        self.seek_to(data[1])

    def remove_current_keyframe(self):
        index = self._boxes_action()
        if index is not None:
            self.remove_keyframe(index, self.frame_idx)

    def _selected_open_action(self):
        item = self.open_list.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _style_end_open_button(self):
        """The End button takes the colour of the action it would end."""
        index = self._selected_open_action()
        if index is None or index >= len(self.actions):
            self.end_open_button.setText("End action")
            self.end_open_button.setEnabled(False)
            self.end_open_button.setStyleSheet("")
            return
        ann = self.actions[index]
        r, g, b = self.action_color(ann["action"])
        text_color = "black" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "white"
        self.end_open_button.setText(f"End {ann['action']}")
        self.end_open_button.setEnabled(self.cap is not None)
        self.end_open_button.setStyleSheet(
            f"QPushButton {{ background-color: rgb({r}, {g}, {b}); color: {text_color};"
            f" border: 2px solid rgb({r // 2}, {g // 2}, {b // 2}); border-radius: 4px;"
            f" padding: 4px 12px; font-weight: bold; }}"
            f"QPushButton:hover {{ border: 2px solid black; }}")

    def on_open_action_selected(self, *_):
        """Picking an action in progress also selects it in the list above, so
        E and the frame overlay follow it."""
        self._style_end_open_button()
        index = self._selected_open_action()
        if index is None:
            return
        for row in range(self.annotation_list.count()):
            item = self.annotation_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == ("action", index):
                self.annotation_list.blockSignals(True)
                self.annotation_list.setCurrentItem(item)
                self.annotation_list.blockSignals(False)
                self.annotation_list.scrollToItem(item)
                break
        self._refresh_boxes_panel()
        self.show_frame()

    def on_annotation_selected(self, *_):
        """Keep the "In progress" panel on the same action as the list."""
        selected = self._selected_annotation()
        if selected is not None and selected[0] == "action":
            for row in range(self.open_list.count()):
                if self.open_list.item(row).data(Qt.ItemDataRole.UserRole) == selected[1]:
                    self.open_list.blockSignals(True)
                    self.open_list.setCurrentRow(row)
                    self.open_list.blockSignals(False)
                    self._style_end_open_button()
                    break
        self._refresh_boxes_panel()
        self.show_frame()

    def end_selected_open_action(self):
        index = self._selected_open_action()
        if index is not None:
            self.end_action_here(index)

    def _selected_annotation(self):
        item = self.annotation_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def on_annotation_activated(self, item):
        self.jump_to_annotation(*item.data(Qt.ItemDataRole.UserRole))

    def jump_to_selected_annotation(self, to_end=False):
        """Enter jumps to the selected annotation; Shift+Enter to its end."""
        selected = self._selected_annotation()
        if selected is None:
            return
        self.jump_to_annotation(*selected, to_end=to_end)

    def jump_to_annotation(self, kind, index, to_end=False):
        store = self._store(kind)
        if self.cap is None or index >= len(store):
            return
        ann = store[index]
        frame = self._start_frame(kind, ann)
        if to_end and ann.get("end_frame") is not None:
            frame = ann["end_frame"]
        self.pause()
        self.seek_to(frame)

    def delete_annotation(self, kind, index):
        store = self._store(kind)
        if index >= len(store):
            return
        removed = store.pop(index)
        self._reindex_history(kind, index)
        self.dirty = True
        self.refresh_annotation_list()
        self.show_frame()
        self.hint_label.setText(
            f"Deleted {self.KIND_LABELS[kind].lower()} '{self._name(kind, removed)}' "
            f"at frame {self._start_frame(kind, removed)}")

    def delete_selected_annotation(self):
        selected = self._selected_annotation()
        if selected is None:
            self.hint_label.setText("Select an annotation in the list first")
            return
        self.delete_annotation(*selected)

    def change_annotation_class(self, kind, index):
        store = self._store(kind)
        if index >= len(store):
            return
        if kind == "action":
            details = self.ask_action_details(current=store[index])
            if details is None:
                return
            ann = store[index]
            self.history.append(("snapshot", "action", index, copy.deepcopy(ann)))
            ann["action"] = details[0]
            # Individual i keeps its join / leave frames; extra ones span the
            # whole action, missing ones are dropped
            old = ann["participants"]
            ann["participants"] = [
                {"species": name,
                 "join": old[i]["join"] if i < len(old) else None,
                 "leave": old[i]["leave"] if i < len(old) else None}
                for i, name in enumerate(details[1])]
            for name in details[1]:
                self._register_class(name)
            self.dirty = True
            self.refresh_annotation_list()
            self.show_frame()
            return
        dialog = LabelDialog(self.classes, self, preselect=store[index]["class_name"])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = (dialog.selected_label or "").strip()
        if not name:
            return
        self._register_class(name)
        store[index]["class_name"] = name
        self.dirty = True
        self.refresh_annotation_list()
        self.show_frame()

    def on_list_context_menu(self, pos):
        item = self.annotation_list.itemAt(pos)
        if item is None:
            return
        self.annotation_list.setCurrentItem(item)
        kind, index = item.data(Qt.ItemDataRole.UserRole)
        self._show_annotation_menu(self.annotation_list.mapToGlobal(pos), kind, index)

    def on_frame_right_clicked(self, pos):
        """Right-click on an annotation of the current frame: edit / delete."""
        point = self.get_image_coordinates(pos)
        if point is None:
            return
        hit = self.annotation_at(point)
        if hit is None:
            return
        self._show_annotation_menu(self.frame_label.mapToGlobal(pos), *hit)

    def _show_annotation_menu(self, global_pos, kind, index):
        menu = QMenu(self)
        change_action = menu.addAction("Edit action…" if kind == "action" else "Change class")
        start_here = end_here = start_jump = end_jump = join_here = None
        key_here = key_remove = None
        leave_actions = {}
        if kind == "action" and self.actions[index]["action"] in MULTI_SPECIES_TYPES:
            ann = self.actions[index]
            join_here = menu.addAction(f"Individual joins on this frame ({self.frame_idx})…")
            leave_menu = menu.addMenu(f"Individual leaves on this frame ({self.frame_idx})")
            for i, participant in enumerate(ann["participants"]):
                if participant in self._active_participants(ann, self.frame_idx):
                    leave_actions[leave_menu.addAction(
                        f"#{i + 1}  {participant['species']}")] = i
            leave_menu.setEnabled(bool(leave_actions))
            menu.addSeparator()
        if kind == "action":
            start_here = menu.addAction(f"Redraw start box on this frame ({self.frame_idx})  S")
            end_here = menu.addAction(f"End it on this frame ({self.frame_idx})  E")
            ann = self.actions[index]
            menu.addSeparator()
            if self.frame_idx in dict(self._action_keyframes(ann)):
                key_here = menu.addAction(
                    f"Redraw intermediate box on this frame ({self.frame_idx})  K")
                key_remove = menu.addAction(
                    f"Remove intermediate box on this frame ({self.frame_idx})")
            else:
                key_here = menu.addAction(
                    f"Add intermediate box on this frame ({self.frame_idx})  K")
                key_here.setEnabled(
                    self._keyframe_allowed(ann, self.frame_idx) is None
                    and self.frame_idx not in (ann["start_frame"], ann["end_frame"]))
            menu.addSeparator()
            start_jump = menu.addAction("Jump to start")
            end_jump = menu.addAction("Jump to end")
            end_jump.setEnabled(self.actions[index]["end_frame"] is not None)
        delete_action = menu.addAction("Delete")
        action = menu.exec(global_pos)
        if action is None:
            return
        if action == delete_action:
            self.delete_annotation(kind, index)
        elif action == change_action:
            self.change_annotation_class(kind, index)
        elif action == join_here:
            self.participant_joins(index)
        elif action in leave_actions:
            self.participant_leaves(index, leave_actions[action])
        elif action == start_here:
            self._arm_endpoint(index, "start")
        elif action == end_here:
            self.end_action_here(index)
        elif action == key_here:
            self.arm_keyframe(index)
        elif action == key_remove:
            self.remove_keyframe(index, self.frame_idx)
        elif action == start_jump:
            self.jump_to_annotation(kind, index)
        elif action == end_jump:
            self.jump_to_annotation(kind, index, to_end=True)

    def annotation_at(self, point):
        """Annotation drawn on the current frame under (x, y), or None."""
        x, y = point
        for i, ann in enumerate(self.bboxes):
            if ann["frame"] != self.frame_idx:
                continue
            if (ann["x"] <= x <= ann["x"] + ann["width"]
                    and ann["y"] <= y <= ann["y"] + ann["height"]):
                return ("bbox", i)
        for i, ann in enumerate(self.actions):
            if not self._shown_on_frame(i, ann):
                continue
            bx, by, bw, bh = self._action_box_at(ann, self.frame_idx)
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return ("action", i)
        return None

    # ------------------------------------------------------------------
    # CSV import / export
    # ------------------------------------------------------------------
    def _csv_paths(self, directory):
        """points (legacy, read only), bboxes and actions CSV paths."""
        stem = os.path.splitext(self.video_name)[0]
        return (os.path.join(directory, f"{stem}_points.csv"),
                os.path.join(directory, f"{stem}_bboxes.csv"),
                os.path.join(directory, f"{stem}_actions.csv"))

    def _participants_path(self, directory):
        stem = os.path.splitext(self.video_name)[0]
        return os.path.join(directory, f"{stem}_participants.csv")

    def _annotations_root(self):
        """Parent folder holding one sub-folder per annotation session.

        Lives next to app.py so everything travels together on a USB stick;
        falls back to the video's own folder if the app folder is read-only.
        """
        root = os.path.join(APP_DIR, ANNOTATIONS_DIR_NAME)
        try:
            os.makedirs(root, exist_ok=True)
            return root
        except OSError:
            fallback = os.path.join(
                os.path.dirname(self.video_path or "") or os.getcwd(), ANNOTATIONS_DIR_NAME)
            os.makedirs(fallback, exist_ok=True)
            return fallback

    def _ensure_session_dir(self):
        """Output folder for this session: <video>_<YYYYMMDD_HHMMSS>.

        Created on the first save and reused for every later save of the same
        session, so re-saving updates the same files while a *new* session (or
        a session resumed from loaded CSVs) gets its own folder and never
        overwrites earlier work — unless the user chose to overwrite the loaded
        CSVs, in which case session_dir already points at their folder.
        """
        if self.session_dir and os.path.isdir(self.session_dir):
            return self.session_dir

        stem = os.path.splitext(self.video_name)[0]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = self._annotations_root()
        path = os.path.join(root, f"{stem}_{stamp}")
        # Two saves within the same second, or a folder left by a crashed run
        suffix = 2
        while os.path.exists(path):
            path = os.path.join(root, f"{stem}_{stamp}_{suffix}")
            suffix += 1
        os.makedirs(path)
        self.session_dir = path
        return path

    ACTION_COLUMNS = ["action_id", "video_name", "action", "classes", "individuals",
                      "start_frame", "start_time_sec", "end_frame", "end_time_sec", "duration_sec",
                      "start_x", "start_y", "start_width", "start_height",
                      "end_x", "end_y", "end_width", "end_height"]

    PARTICIPANT_COLUMNS = ["action_id", "video_name", "action", "individual", "class_name",
                           "join_frame", "join_time_sec", "leave_frame", "leave_time_sec"]

    KEYFRAME_COLUMNS = ["action_id", "video_name", "action", "frame", "time_sec",
                        "x", "y", "width", "height"]

    def _keyframes_path(self, directory):
        stem = os.path.splitext(self.video_name)[0]
        return os.path.join(directory, f"{stem}_keyframes.csv")

    def _frame_columns(self, frame):
        """frame, time_sec — both empty for a frame not marked yet."""
        return ["", ""] if frame is None else [frame, f"{frame / self.fps:.3f}"]

    def save_csvs(self):
        if self.cap is None:
            return
        if not self.bboxes and not self.actions:
            QMessageBox.information(self, "Nothing to save", "No annotations yet.")
            return

        if self.session_dir is None and self.loaded_dir is not None:
            choice = self._ask_overwrite_loaded()
            if choice is None:
                return
            if choice:
                self.session_dir = self.loaded_dir

        try:
            directory = self._ensure_session_dir()
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Could not create the output folder:\n{e}")
            return
        points_path, bboxes_path, actions_path = self._csv_paths(directory)
        participants_path = self._participants_path(directory)
        keyframes_path = self._keyframes_path(directory)

        legacy_note = ""
        try:
            with open(bboxes_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["video_name", "frame", "time_sec", "class_name",
                                 "x", "y", "width", "height"])
                for ann in sorted(self.bboxes, key=lambda a: a["frame"]):
                    writer.writerow([self.video_name, ann["frame"], f"{ann['time_sec']:.3f}",
                                     ann["class_name"], ann["x"], ann["y"],
                                     ann["width"], ann["height"]])

            # action_id numbers the actions in time order and links each one
            # to its individuals in the participants file
            ordered = sorted(self.actions, key=lambda a: a["start_frame"])
            with open(actions_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.ACTION_COLUMNS)
                for action_id, ann in enumerate(ordered, 1):
                    start, end = ann["start_frame"], ann["end_frame"]
                    duration = "" if end is None else f"{(end - start) / self.fps:.3f}"
                    end_box = list(ann["end_box"]) if ann["end_box"] else ["", "", "", ""]
                    writer.writerow([action_id, self.video_name, ann["action"],
                                     CLASS_SEPARATOR.join(p["species"] for p in ann["participants"]),
                                     len(ann["participants"]),
                                     *self._frame_columns(start), *self._frame_columns(end),
                                     duration, *ann["start_box"], *end_box])

            with open(participants_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.PARTICIPANT_COLUMNS)
                for action_id, ann in enumerate(ordered, 1):
                    for number, participant in enumerate(ann["participants"], 1):
                        join, leave = self._participant_span(ann, participant)
                        writer.writerow([action_id, self.video_name, ann["action"], number,
                                         participant["species"],
                                         *self._frame_columns(join), *self._frame_columns(leave)])

            with open(keyframes_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(self.KEYFRAME_COLUMNS)
                for action_id, ann in enumerate(ordered, 1):
                    for frame, box in self._action_keyframes(ann):
                        writer.writerow([action_id, self.video_name, ann["action"],
                                         *self._frame_columns(frame), *box])

            # Overwriting a folder that held an old points file: its points
            # are now actions in the file above, so it must not be read again.
            if os.path.exists(points_path):
                stem = os.path.splitext(self.video_name)[0]
                legacy_path = os.path.join(directory, f"{stem}_points_converted.csv")
                os.replace(points_path, legacy_path)
                legacy_note = (f"\n\nThe old {os.path.basename(points_path)} is now in the "
                               f"actions file; it was renamed {os.path.basename(legacy_path)}.")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Could not write the CSV files:\n{e}")
            return

        self.dirty = False
        open_count = sum(1 for a in self.actions if a["end_frame"] is None)
        untyped = sum(1 for a in self.actions if not a["action"])
        notes = ""
        if open_count:
            notes += f"\n\n{open_count} action(s) still have no end — saved with empty end columns."
        if untyped:
            notes += f"\n{untyped} action(s) still have no type — saved with an empty action column."
        QMessageBox.information(
            self, "Saved",
            f"{len(self.bboxes)} apparitions → {os.path.basename(bboxes_path)}\n"
            f"{len(self.actions)} actions → {os.path.basename(actions_path)}\n"
            f"their individuals → {os.path.basename(participants_path)}\n"
            f"their intermediate boxes → {os.path.basename(keyframes_path)}\n\n"
            f"Folder: {directory}{notes}{legacy_note}")
        self.hint_label.setText(f"Saved to {os.path.basename(directory)}/")

    def _ask_overwrite_loaded(self):
        """First save after Load CSVs: overwrite those files or keep them?

        True => overwrite, False => new session folder, None => cancel. Asked
        once; later saves go wherever the first one went.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Save CSVs")
        text = (f"These annotations were loaded from:\n{self.loaded_dir}\n\n"
                f"Overwrite those CSV files, or save to a new session folder "
                f"and leave them untouched?")
        if self.loaded_skipped:
            text += (f"\n\nWarning: those files also hold {self.loaded_skipped} row(s) "
                     f"of other videos, which overwriting will remove.")
        box.setText(text)
        overwrite = box.addButton("Overwrite loaded CSVs", QMessageBox.ButtonRole.DestructiveRole)
        new_folder = box.addButton("Save to new folder", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(new_folder)
        box.exec()
        clicked = box.clickedButton()
        if clicked == overwrite:
            return True
        if clicked == new_folder:
            return False
        return None

    def load_csvs(self):
        """Reload previously saved CSVs so a long video can be resumed."""
        if self.cap is None:
            return
        if self.dirty and not self._confirm_discard("Loading CSVs"):
            return

        # Start in the video's folder: the CSVs worth reviewing usually travel
        # with the video (often made by someone else), not in our own output.
        start_dir = os.path.dirname(self.video_path or "") or self._annotations_root()
        directory = QFileDialog.getExistingDirectory(
            self, "Folder containing the CSVs", start_dir)
        if not directory:
            return

        paths = self._csv_paths(directory)
        if not any(os.path.exists(path) for path in paths):
            # The user probably picked the annotations root instead of one
            # session folder — fall back to this video's most recent session.
            latest = self._latest_session_dir(directory)
            if latest is not None:
                directory = latest
                paths = self._csv_paths(directory)

        if not any(os.path.exists(path) for path in paths):
            stem = os.path.splitext(self.video_name)[0]
            QMessageBox.warning(
                self, "Not found",
                f"No {stem}_points.csv, {stem}_bboxes.csv or {stem}_actions.csv in:\n{directory}")
            return
        points_path, bboxes_path, actions_path = paths
        participants_path = self._participants_path(directory)
        keyframes_path = self._keyframes_path(directory)

        bboxes, actions, skipped, converted = [], [], 0, 0

        def rows(path):
            nonlocal skipped
            if not os.path.exists(path):
                return
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("video_name") and row["video_name"] != self.video_name:
                        skipped += 1
                        continue
                    yield row

        try:
            for row in rows(bboxes_path):
                bboxes.append({
                    "frame": int(row["frame"]),
                    "time_sec": float(row["time_sec"]),
                    "class_name": row["class_name"],
                    "x": int(float(row["x"])),
                    "y": int(float(row["y"])),
                    "width": int(float(row["width"])),
                    "height": int(float(row["height"])),
                })
            individuals = {}
            for row in rows(participants_path):
                individuals.setdefault(row["action_id"].strip(), []).append(row)
            keyframes = {}
            for row in rows(keyframes_path):
                box = self._read_box(row, "")
                if box is not None:
                    keyframes.setdefault(row["action_id"].strip(), {})[int(row["frame"])] = box
            for row in rows(actions_path):
                # Files from before end boxes existed have x,y,width,height
                start_box = (self._read_box(row, "start_")
                             or self._read_box(row, ""))
                ann = {
                    "action": row["action"].strip(),
                    "start_frame": int(row["start_frame"]),
                    "start_box": start_box,
                    "end_frame": self._read_int(row, "end_frame"),
                    "end_box": self._read_box(row, "end_"),
                    "keyframes": keyframes.get((row.get("action_id") or "").strip(), {}),
                }
                listed = individuals.get((row.get("action_id") or "").strip())
                if listed:
                    listed.sort(key=lambda r: int(r["individual"]))
                    ann["participants"] = [self._read_participant(ann, r) for r in listed]
                else:
                    # No participants file (older files): each species in
                    # `classes` is one individual present the whole time
                    ann["participants"] = self._new_participants(
                        [c.strip() for c in row["classes"].split(CLASS_SEPARATOR) if c.strip()])
                actions.append(ann)
            for row in rows(points_path):
                actions.append(self._point_to_action(row))
                converted += 1
        except (OSError, KeyError, ValueError, TypeError) as e:
            QMessageBox.critical(self, "Error", f"Could not read the CSV files:\n{e}")
            return

        self.bboxes = bboxes
        self.actions = actions
        self.history = []
        # Indices are about to change under a tool waiting for a box
        self.set_tool(None)
        self.classes = []
        for ann in self.bboxes:
            self._register_class(ann["class_name"])
        for ann in self.actions:
            for participant in ann["participants"]:
                self._register_class(participant["species"])
        # The first save asks whether to overwrite the folder we just read
        # from or to start a fresh session folder (see save_csvs).
        self.session_dir = None
        self.loaded_dir = directory
        self.loaded_skipped = skipped
        self.dirty = False
        self.refresh_annotation_list()
        self.show_frame()

        message = f"Loaded {len(bboxes)} apparitions and {len(actions)} actions."
        if converted:
            message += (f"\n{converted} of the actions come from old points: they have no "
                        f"type yet and a small box around the point. Right-click → Edit "
                        f"action to set the type; S / E to redraw the start / end box.")
        if skipped:
            message += f"\n{skipped} row(s) skipped (they belong to another video)."
        QMessageBox.information(self, "Loaded", message)
        self.hint_label.setText(message.splitlines()[0])

    def _point_to_action(self, row):
        """An old point (species + frame) as an action with no type yet and a
        small box centred on the point."""
        frame_h, frame_w = (self.current_frame.shape[:2] if self.current_frame is not None
                            else (1080, 1920))
        side = max(16, int(round(CONVERTED_POINT_BOX * min(frame_w, frame_h))))
        px, py = int(float(row["x"])), int(float(row["y"]))
        x = min(max(0, px - side // 2), max(0, frame_w - side))
        y = min(max(0, py - side // 2), max(0, frame_h - side))
        return {
            "action": "",
            "participants": self._new_participants([row["class_name"].strip()]),
            "start_frame": int(row["frame"]),
            "start_box": (x, y, side, side),
            # Points saved by the previous version may carry an end frame
            "end_frame": self._read_int(row, "end_frame"),
            "end_box": None,
            "keyframes": {},
        }

    def _read_participant(self, ann, row):
        """An individual; a join / leave equal to the action's own start / end
        is stored as None so it keeps following them."""
        join = self._read_int(row, "join_frame")
        leave = self._read_int(row, "leave_frame")
        return {
            "species": row["class_name"].strip(),
            "join": None if join in (None, ann["start_frame"]) else join,
            "leave": None if leave in (None, ann["end_frame"]) else leave,
        }

    @staticmethod
    def _read_int(row, column):
        value = (row.get(column) or "").strip()
        return int(float(value)) if value else None

    @classmethod
    def _read_box(cls, row, prefix):
        """(x, y, width, height) from `<prefix>x` ... columns, None if empty."""
        values = [cls._read_int(row, prefix + name) for name in ("x", "y", "width", "height")]
        return None if None in values else tuple(values)

    def _latest_session_dir(self, root):
        """Most recent session folder under `root` holding this video's CSVs."""
        stem = os.path.splitext(self.video_name)[0]
        candidates = []
        try:
            entries = os.listdir(root)
        except OSError:
            return None
        for name in entries:
            path = os.path.join(root, name)
            if not os.path.isdir(path) or not name.startswith(f"{stem}_"):
                continue
            if any(os.path.exists(p) for p in self._csv_paths(path)):
                candidates.append(path)
        if not candidates:
            return None
        # Folder names end in the timestamp, so sorting by name orders by time
        return sorted(candidates)[-1]

    def _confirm_discard(self, action):
        reply = QMessageBox.question(
            self, "Unsaved annotations",
            f"{action} will discard unsaved annotations. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return reply == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------
    # keyboard / lifecycle
    # ------------------------------------------------------------------
    # Every keyboard action lives in _install_shortcuts(); there is deliberately
    # no keyPressEvent here, so a key can never be handled twice (once by the
    # shortcut and once by the focused widget's handler).

    def closeEvent(self, event):
        if self.dirty:
            reply = QMessageBox.question(
                self, "Unsaved annotations",
                "You have unsaved annotations. Quit anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.timer.stop()
        if self.player is not None:
            self.player.stop()
        if self.cap is not None:
            self.cap.release()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Video Annotator")
    app.setStyleSheet(load_stylesheet(resource_path("app_modules", "button_styles.qss")))
    viewer = VideoAnnotator()
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
