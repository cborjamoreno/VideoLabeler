"""Video annotation tool — mark the first apparition of each animal.

Play a video, pause it, and drop a point or a bounding box on the paused frame.
Every annotation carries a class name and is exported to CSV:

    <video>_points.csv  video_name,frame,time_sec,class_name,x,y
    <video>_bboxes.csv  video_name,frame,time_sec,class_name,x,y,width,height

x is the column (pixels from the left edge) and y is the row (pixels from the
top edge), both in ORIGINAL video resolution — zooming never changes them.

Dependencies: PyQt6, opencv-python, numpy. Nothing else.
"""

import csv
import os
import sys
from datetime import datetime

import cv2
import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QFileDialog, QVBoxLayout,
    QHBoxLayout, QMessageBox, QSlider, QComboBox, QListWidget, QListWidgetItem,
    QSizePolicy, QScrollArea, QFrame, QGridLayout, QMenu, QDialog, QStyle,
    QStyleOptionSlider
)
from PyQt6.QtGui import QPixmap, QImage, QGuiApplication, QShortcut, QKeySequence
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QUrl

from app_modules import LabelDialog

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
PALETTE = [
    (255, 0, 0),      # red
    (0, 255, 0),      # green
    (0, 160, 255),    # blue
    (255, 255, 0),    # yellow
    (255, 0, 255),    # magenta
    (0, 255, 255),    # cyan
    (255, 140, 0),    # orange
    (160, 80, 255),   # purple
    (255, 20, 147),   # deep pink
    (0, 255, 160),    # spring green
]

FALLBACK_FPS = 25.0

# Seconds the « / » buttons (and the arrow keys) jump, YouTube-style.
SKIP_SECONDS = 5.0

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


def format_time(seconds):
    """Format a duration in seconds as mm:ss.mmm (hh:mm:ss.mmm past an hour)."""
    if seconds is None or seconds < 0 or not np.isfinite(seconds):
        return "--:--.---"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:06.3f}"
    return f"{minutes:02d}:{secs:06.3f}"


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

        self.setWindowTitle("Video Annotator — points & bboxes")
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
        self.tool = None               # None | "point" | "bbox"
        self.bbox_first_corner = None  # (x, y) in image coords
        self.points = []               # dicts: frame, time_sec, class_name, x, y
        self.bboxes = []               # dicts: ... x, y, width, height
        self.classes = []              # ordered class names seen so far
        self.history = []              # ("point"|"bbox", index) for Ctrl+Z
        self.session_dir = None        # created on the first save of a session
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

        self.status_label = QLabel("frame - / -  —  --:--.--- / --:--.---")
        self.status_label.setMinimumWidth(300)

        # Annotation tools
        self.point_button = self._make_button("Point", "point-button-idle", self.toggle_point_tool, width=110)
        self.bbox_button = self._make_button("BBox", "bbox-button-idle", self.toggle_bbox_tool, width=110)
        self.hint_label = QLabel("Open a video to start")

        # Annotation list panel
        self.annotation_list = QListWidget(self)
        self.annotation_list.setMinimumWidth(280)
        self.annotation_list.setMaximumWidth(380)
        self.annotation_list.itemDoubleClicked.connect(self.on_annotation_activated)
        self.annotation_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.annotation_list.customContextMenuRequested.connect(self.on_list_context_menu)
        self.delete_button = self._make_button("Delete selected", "delete-button", self.delete_selected_annotation, width=150)

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
        playback_layout.addWidget(self.status_label)

        tools_layout = QHBoxLayout()
        tools_layout.addStretch()
        tools_layout.addWidget(self.point_button)
        tools_layout.addWidget(self.bbox_button)
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
        side_layout.addWidget(QLabel("Annotations (double-click to jump)"))
        side_layout.addWidget(self.annotation_list, 1)
        side_layout.addWidget(self.delete_button)

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
            ("Escape", self._on_escape),
        ]
        self._shortcuts = []
        for sequence, slot in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(slot)
            self._shortcuts.append(shortcut)

    def _set_button_class(self, button, qss_class):
        button.setProperty("class", qss_class)
        button.style().unpolish(button)
        button.style().polish(button)

    def _set_controls_enabled(self, enabled):
        for widget in (self.play_button, self.step_back_button, self.step_fwd_button,
                       self.skip_back_button, self.skip_fwd_button,
                       self.point_button, self.bbox_button, self.save_button,
                       self.load_button, self.delete_button):
            widget.setEnabled(enabled)
        self.speed_combo.setEnabled(enabled)
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
        self.points = []
        self.bboxes = []
        self.classes = []
        self.history = []
        self.dirty = False
        self.session_dir = None        # a new video starts a new session folder
        self.tool = None
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
        self.hint_label.setText("Pause, then arm Point or BBox to annotate")

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
            self.status_label.setText("frame - / -  —  --:--.--- / --:--.---")
            return
        total_txt = str(self.total_frames) if self.total_frames else "?"
        current_time = self.frame_idx / self.fps if self.frame_idx >= 0 else 0.0
        total_time = self.total_frames / self.fps if self.total_frames else None
        total_time_txt = format_time(total_time) if total_time else "--:--.---"
        self.status_label.setText(
            f"frame {self.frame_idx} / {total_txt}  —  "
            f"{format_time(current_time)} / {total_time_txt}"
        )
        if self.total_frames:
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(min(self.frame_idx, self.total_frames - 1))
            self.position_slider.blockSignals(False)

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

    def build_overlay(self, cursor_point=None):
        """Current frame plus the annotations that belong to this frame."""
        overlay = self.current_frame.copy()
        s = self._draw_scale()
        radius = max(3, int(round(5 * s)))
        thickness = max(1, int(round(2 * s)))
        arm = max(6, int(round(8 * s)))

        for ann in self.points:
            if ann["frame"] != self.frame_idx:
                continue
            color = self.class_color(ann["class_name"])
            cv2.circle(overlay, (ann["x"], ann["y"]), radius, color, -1)
            cv2.circle(overlay, (ann["x"], ann["y"]), radius + 2, (255, 255, 255),
                       max(1, thickness // 2))
            self._draw_caption(overlay, ann["class_name"],
                               (ann["x"] + arm, ann["y"] - arm // 2), color)

        for ann in self.bboxes:
            if ann["frame"] != self.frame_idx:
                continue
            color = self.class_color(ann["class_name"])
            x, y, w, h = ann["x"], ann["y"], ann["width"], ann["height"]
            cv2.rectangle(overlay, (x, y), (x + w, y + h), color, thickness)
            self._draw_caption(overlay, ann["class_name"], (x, y - thickness), color)

        # Rubber band between the first and second bbox click
        if self.tool == "bbox" and self.bbox_first_corner is not None:
            cx, cy = self.bbox_first_corner
            if cursor_point is not None:
                x1, x2 = sorted((cx, cursor_point[0]))
                y1, y2 = sorted((cy, cursor_point[1]))
                cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 255), thickness)
            cv2.line(overlay, (cx - arm, cy), (cx + arm, cy), (0, 255, 255), thickness)
            cv2.line(overlay, (cx, cy - arm), (cx, cy + arm), (0, 255, 255), thickness)

        return overlay

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
        self._set_button_class(
            self.point_button,
            "point-button-active" if self.tool == "point" else "point-button-idle")
        self._set_button_class(
            self.bbox_button,
            "bbox-button-active" if self.tool == "bbox" else "bbox-button-idle")
        if self.tool is None:
            self.frame_label.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.frame_label.setCursor(Qt.CursorShape.CrossCursor)

    def _on_escape(self):
        """Esc disarms the tool / cancels a half-drawn box, and nothing else."""
        if self.tool is not None or self.bbox_first_corner is not None:
            self.set_tool(None)

    def toggle_point_tool(self):
        self.set_tool(None if self.tool == "point" else "point")

    def toggle_bbox_tool(self):
        self.set_tool(None if self.tool == "bbox" else "bbox")

    def set_tool(self, tool):
        if self.cap is None:
            return
        if tool is not None:
            self.pause()          # annotation always happens on a paused frame
        self.tool = tool
        self.bbox_first_corner = None
        self._refresh_tool_buttons()
        if tool == "point":
            self.hint_label.setText("Click the animal to place a point")
        elif tool == "bbox":
            self.hint_label.setText("Click two opposite corners of the box")
        else:
            self.hint_label.setText("Tool disarmed")
        self.show_frame()

    def on_frame_clicked(self, pos):
        if self.current_frame is None or self.tool is None:
            return
        point = self.get_image_coordinates(pos)
        if point is None:
            return

        if self.tool == "point":
            class_name = self.ask_class_name()
            if class_name is None:
                return
            self.add_point(point[0], point[1], class_name)
            return

        # bbox: click 1 = first corner, click 2 = opposite corner
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

        class_name = self.ask_class_name()
        self.bbox_first_corner = None
        if class_name is None:
            self.hint_label.setText("Box discarded")
            self.show_frame()
            return
        self.add_bbox(x1, y1, x2 - x1, y2 - y1, class_name)
        self.hint_label.setText("Click two opposite corners of the box")

    def on_mouse_moved(self, pos):
        if self.tool != "bbox" or self.bbox_first_corner is None:
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

    def _register_class(self, class_name):
        if class_name not in self.classes:
            self.classes.append(class_name)

    def add_point(self, x, y, class_name):
        self._register_class(class_name)
        self.points.append({
            "frame": self.frame_idx,
            "time_sec": self.frame_idx / self.fps,
            "class_name": class_name,
            "x": int(x),
            "y": int(y),
        })
        self.history.append(("point", len(self.points) - 1))
        self.dirty = True
        self.refresh_annotation_list()
        self.show_frame()
        self.hint_label.setText(f"Point '{class_name}' at frame {self.frame_idx}")

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
        self.history.append(("bbox", len(self.bboxes) - 1))
        self.dirty = True
        self.refresh_annotation_list()
        self.show_frame()

    def undo_last(self):
        """Remove the most recently added annotation."""
        while self.history:
            kind, index = self.history.pop()
            store = self.points if kind == "point" else self.bboxes
            if index < len(store):
                removed = store.pop(index)
                self._reindex_history(kind, index)
                self.dirty = True
                self.refresh_annotation_list()
                self.show_frame()
                self.hint_label.setText(
                    f"Undid {kind} '{removed['class_name']}' at frame {removed['frame']}")
                return
        self.hint_label.setText("Nothing to undo")

    def _reindex_history(self, kind, removed_index):
        """Keep undo indices valid after an annotation is removed."""
        updated = []
        for h_kind, h_index in self.history:
            if h_kind == kind:
                if h_index == removed_index:
                    continue
                if h_index > removed_index:
                    h_index -= 1
            updated.append((h_kind, h_index))
        self.history = updated

    # ------------------------------------------------------------------
    # annotation list / editing
    # ------------------------------------------------------------------
    def refresh_annotation_list(self):
        self.annotation_list.clear()
        entries = ([("point", i, a) for i, a in enumerate(self.points)]
                   + [("bbox", i, a) for i, a in enumerate(self.bboxes)])
        entries.sort(key=lambda e: (e[2]["frame"], e[0], e[1]))
        for kind, index, ann in entries:
            if kind == "point":
                detail = f"({ann['x']}, {ann['y']})"
            else:
                detail = f"({ann['x']}, {ann['y']}) {ann['width']}×{ann['height']}"
            item = QListWidgetItem(
                f"f{ann['frame']}  {format_time(ann['time_sec'])}  "
                f"{ann['class_name']}  [{kind}] {detail}")
            item.setData(Qt.ItemDataRole.UserRole, (kind, index))
            self.annotation_list.addItem(item)

    def _selected_annotation(self):
        item = self.annotation_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def on_annotation_activated(self, item):
        kind, index = item.data(Qt.ItemDataRole.UserRole)
        store = self.points if kind == "point" else self.bboxes
        if index < len(store):
            self.pause()
            self.seek_to(store[index]["frame"])

    def delete_annotation(self, kind, index):
        store = self.points if kind == "point" else self.bboxes
        if index >= len(store):
            return
        removed = store.pop(index)
        self._reindex_history(kind, index)
        self.dirty = True
        self.refresh_annotation_list()
        self.show_frame()
        self.hint_label.setText(
            f"Deleted {kind} '{removed['class_name']}' at frame {removed['frame']}")

    def delete_selected_annotation(self):
        selected = self._selected_annotation()
        if selected is None:
            self.hint_label.setText("Select an annotation in the list first")
            return
        self.delete_annotation(*selected)

    def change_annotation_class(self, kind, index):
        store = self.points if kind == "point" else self.bboxes
        if index >= len(store):
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
        """Right-click on an annotation of the current frame: delete / relabel."""
        point = self.get_image_coordinates(pos)
        if point is None:
            return
        hit = self.annotation_at(point)
        if hit is None:
            return
        self._show_annotation_menu(self.frame_label.mapToGlobal(pos), *hit)

    def _show_annotation_menu(self, global_pos, kind, index):
        menu = QMenu(self)
        change_action = menu.addAction("Change class")
        delete_action = menu.addAction("Delete")
        action = menu.exec(global_pos)
        if action == delete_action:
            self.delete_annotation(kind, index)
        elif action == change_action:
            self.change_annotation_class(kind, index)

    def annotation_at(self, point):
        """Annotation of the current frame under (x, y), or None."""
        x, y = point
        tolerance = max(8, int(round(8 * self._draw_scale())))
        for i, ann in enumerate(self.points):
            if ann["frame"] != self.frame_idx:
                continue
            if abs(ann["x"] - x) <= tolerance and abs(ann["y"] - y) <= tolerance:
                return ("point", i)
        for i, ann in enumerate(self.bboxes):
            if ann["frame"] != self.frame_idx:
                continue
            if (ann["x"] <= x <= ann["x"] + ann["width"]
                    and ann["y"] <= y <= ann["y"] + ann["height"]):
                return ("bbox", i)
        return None

    # ------------------------------------------------------------------
    # CSV import / export
    # ------------------------------------------------------------------
    def _csv_paths(self, directory):
        stem = os.path.splitext(self.video_name)[0]
        return (os.path.join(directory, f"{stem}_points.csv"),
                os.path.join(directory, f"{stem}_bboxes.csv"))

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
        a session resumed from loaded CSVs) always gets its own folder and can
        never overwrite earlier work.
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

    def save_csvs(self):
        if self.cap is None:
            return
        if not self.points and not self.bboxes:
            QMessageBox.information(self, "Nothing to save", "No annotations yet.")
            return

        try:
            directory = self._ensure_session_dir()
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Could not create the output folder:\n{e}")
            return
        points_path, bboxes_path = self._csv_paths(directory)

        try:
            with open(points_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["video_name", "frame", "time_sec", "class_name", "x", "y"])
                for ann in sorted(self.points, key=lambda a: a["frame"]):
                    writer.writerow([self.video_name, ann["frame"], f"{ann['time_sec']:.3f}",
                                     ann["class_name"], ann["x"], ann["y"]])

            with open(bboxes_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["video_name", "frame", "time_sec", "class_name",
                                 "x", "y", "width", "height"])
                for ann in sorted(self.bboxes, key=lambda a: a["frame"]):
                    writer.writerow([self.video_name, ann["frame"], f"{ann['time_sec']:.3f}",
                                     ann["class_name"], ann["x"], ann["y"],
                                     ann["width"], ann["height"]])
        except OSError as e:
            QMessageBox.critical(self, "Error", f"Could not write the CSV files:\n{e}")
            return

        self.dirty = False
        QMessageBox.information(
            self, "Saved",
            f"{len(self.points)} points → {os.path.basename(points_path)}\n"
            f"{len(self.bboxes)} bboxes → {os.path.basename(bboxes_path)}\n\n"
            f"Folder: {directory}")
        self.hint_label.setText(f"Saved to {os.path.basename(directory)}/")

    def load_csvs(self):
        """Reload previously saved CSVs so a long video can be resumed."""
        if self.cap is None:
            return
        if self.dirty and not self._confirm_discard("Loading CSVs"):
            return

        directory = QFileDialog.getExistingDirectory(
            self, "Folder containing the CSVs", self._annotations_root())
        if not directory:
            return

        points_path, bboxes_path = self._csv_paths(directory)
        if not os.path.exists(points_path) and not os.path.exists(bboxes_path):
            # The user probably picked the annotations root instead of one
            # session folder — fall back to this video's most recent session.
            latest = self._latest_session_dir(directory)
            if latest is not None:
                directory = latest
                points_path, bboxes_path = self._csv_paths(directory)

        if not os.path.exists(points_path) and not os.path.exists(bboxes_path):
            stem = os.path.splitext(self.video_name)[0]
            QMessageBox.warning(
                self, "Not found",
                f"No {stem}_points.csv or {stem}_bboxes.csv in:\n{directory}")
            return

        points, bboxes, skipped = [], [], 0
        try:
            if os.path.exists(points_path):
                with open(points_path, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        if row.get("video_name") and row["video_name"] != self.video_name:
                            skipped += 1
                            continue
                        points.append({
                            "frame": int(row["frame"]),
                            "time_sec": float(row["time_sec"]),
                            "class_name": row["class_name"],
                            "x": int(float(row["x"])),
                            "y": int(float(row["y"])),
                        })
            if os.path.exists(bboxes_path):
                with open(bboxes_path, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        if row.get("video_name") and row["video_name"] != self.video_name:
                            skipped += 1
                            continue
                        bboxes.append({
                            "frame": int(row["frame"]),
                            "time_sec": float(row["time_sec"]),
                            "class_name": row["class_name"],
                            "x": int(float(row["x"])),
                            "y": int(float(row["y"])),
                            "width": int(float(row["width"])),
                            "height": int(float(row["height"])),
                        })
        except (OSError, KeyError, ValueError) as e:
            QMessageBox.critical(self, "Error", f"Could not read the CSV files:\n{e}")
            return

        self.points = points
        self.bboxes = bboxes
        self.history = []
        self.classes = []
        for ann in self.points + self.bboxes:
            self._register_class(ann["class_name"])
        # Resumed work saves into a fresh session folder — the one we just read
        # from stays untouched as a backup.
        self.session_dir = None
        self.dirty = False
        self.refresh_annotation_list()
        self.show_frame()

        message = f"Loaded {len(points)} points and {len(bboxes)} bboxes."
        if skipped:
            message += f"\n{skipped} row(s) skipped (they belong to another video)."
        QMessageBox.information(self, "Loaded", message)
        self.hint_label.setText(message.splitlines()[0])

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
            points_path, bboxes_path = self._csv_paths(path)
            if os.path.exists(points_path) or os.path.exists(bboxes_path):
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
