import os
import sys

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton, QSpinBox,
    QGridLayout, QWidget
)
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtCore import Qt

from .label_dialog import load_stylesheet

# Upper bound of the individuals spin box — a crowd, not a limit anyone hits
MAX_INDIVIDUALS = 30


def color_icon(rgb, size=14):
    """Small filled square used to show an action type's colour."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(*rgb))
    return QIcon(pixmap)


class ActionDialog(QDialog):
    """Ask for an action's type and the individuals that perform it.

    The types are a fixed list, each with its display colour. Most are done by
    one individual; the types in `multi_species_types` — an interaction — by
    two or more, asked one by one so that two individuals of the same species
    (two crabs fighting) are two entries.

    Result: `selected_action` and `selected_classes`, the species of each
    individual in order (repeats allowed).
    """

    def __init__(self, action_types, classes, parent=None, preselect_action=None,
                 preselect_classes=None, multi_species_types=(), unknown_color=(190, 190, 190)):
        super().__init__(parent)
        self.setWindowTitle("Action")
        self.setModal(True)
        self.setMinimumWidth(360)

        base = getattr(sys, "_MEIPASS", None)
        qss_path = (os.path.join(base, "app_modules", "button_styles.qss") if base
                    else os.path.join(os.path.dirname(__file__), "button_styles.qss"))
        self.setStyleSheet(load_stylesheet(qss_path))

        self.classes = list(classes)
        self.multi_species_types = set(multi_species_types)
        self.preselect_classes = list(preselect_classes or [])
        self.selected_action = None
        self.selected_classes = []
        self.species_combos = []

        layout = QVBoxLayout()

        self.type_combo = QComboBox()
        names = [name for name, _color in action_types]
        if not preselect_action:
            # Actions converted from old points have no type yet: make the
            # user pick one rather than silently defaulting to the first.
            self.type_combo.addItem("— choose a type —", None)
        for name, color in action_types:
            self.type_combo.addItem(color_icon(color), name, name)
        if preselect_action and preselect_action not in names:
            # A type from an older file: keep it selectable so editing the
            # species does not force a new type.
            self.type_combo.addItem(color_icon(unknown_color), preselect_action, preselect_action)
        if preselect_action:
            self.type_combo.setCurrentIndex(self.type_combo.findData(preselect_action))
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)

        self.count_row = QWidget()
        count_layout = QHBoxLayout(self.count_row)
        count_layout.setContentsMargins(0, 0, 0, 0)
        count_layout.addWidget(QLabel("Individuals involved:"))
        self.count_spin = QSpinBox()
        self.count_spin.setRange(2, MAX_INDIVIDUALS)
        self.count_spin.setValue(max(2, len(self.preselect_classes)))
        self.count_spin.valueChanged.connect(self._rebuild_rows)
        count_layout.addWidget(self.count_spin)
        count_layout.addStretch()

        self.rows_widget = QWidget()
        self.rows_layout = QGridLayout(self.rows_widget)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)

        button_layout = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)
        self.ok_button.setProperty("class", "primary-button")
        self.ok_button.setDefault(True)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        cancel_button.setProperty("class", "neutral-button")
        cancel_button.setAutoDefault(False)
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(cancel_button)

        layout.addWidget(QLabel("Action type:"))
        layout.addWidget(self.type_combo)
        layout.addWidget(self.count_row)
        layout.addWidget(self.rows_widget)
        hint = QLabel("Pick a species, or type a new one.")
        hint.setStyleSheet("color: #757575;")
        layout.addWidget(hint)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        self._on_type_changed()
        self.type_combo.setFocus()

    # ------------------------------------------------------------------
    def _current_type(self):
        return self.type_combo.currentData()

    def _is_multi(self):
        return self._current_type() in self.multi_species_types

    def _individuals(self):
        return self.count_spin.value() if self._is_multi() else 1

    def _on_type_changed(self, *_):
        self.count_row.setVisible(self._is_multi())
        self._rebuild_rows()

    def _rebuild_rows(self, *_):
        """One species box per individual, keeping what was already chosen."""
        current = [combo.currentText().strip() for combo in self.species_combos]
        wanted = self._individuals()
        while self.rows_layout.count():
            widget = self.rows_layout.takeAt(0).widget()
            if widget is not None:
                # Off screen now — deleteLater alone leaves it drawn under the
                # new rows until the event loop gets round to it
                widget.hide()
                widget.deleteLater()
        self.species_combos = []
        defaults = current + self.preselect_classes[len(current):]
        for i in range(wanted):
            label = f"Individual {i + 1}:" if wanted > 1 else "Species:"
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.addItems(self.classes)
            combo.lineEdit().setPlaceholderText("species")
            # Same species as the individual above by default: two of a kind
            # fighting is the common case
            default = defaults[i] if i < len(defaults) else (defaults[-1] if defaults else "")
            combo.setCurrentText(default)
            combo.currentTextChanged.connect(self._update_ok)
            self.rows_layout.addWidget(QLabel(label), i, 0)
            self.rows_layout.addWidget(combo, i, 1)
            self.species_combos.append(combo)
        self.rows_layout.setColumnStretch(1, 1)
        self._update_ok()
        self.adjustSize()

    def _species(self):
        return [combo.currentText().strip() for combo in self.species_combos]

    def _valid(self):
        return self._current_type() is not None and all(self._species())

    def _update_ok(self, *_):
        self.ok_button.setEnabled(self._valid())

    def accept(self):
        if not self._valid():
            return
        self.selected_action = self._current_type()
        self.selected_classes = self._species()
        super().accept()
