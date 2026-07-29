import os
import sys

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem
)
from PyQt6.QtCore import Qt


def load_stylesheet(file_path):
    """Load stylesheet from a file"""
    try:
        with open(file_path, 'r') as f:
            return f.read()
    except Exception as e:
        print(f"Error loading stylesheet: {e}")
        return ""


class LabelDialog(QDialog):
    """Ask for the class name of the annotation that was just placed.

    Only the name matters here — annotations are exported to CSV, so there is
    no per-class colour to pick (the app derives a display colour from the
    class name itself).
    """

    def __init__(self, existing_labels, parent=None, preselect=None):
        super().__init__(parent)
        self.setWindowTitle("Class name")
        self.setModal(True)
        self.setMinimumWidth(320)

        # Inside a PyInstaller build the stylesheet is unpacked under
        # sys._MEIPASS/app_modules/, not next to this source file.
        base = getattr(sys, "_MEIPASS", None)
        qss_path = (os.path.join(base, "app_modules", "button_styles.qss") if base
                    else os.path.join(os.path.dirname(__file__), "button_styles.qss"))
        self.setStyleSheet(load_stylesheet(qss_path))

        # existing_labels: ordered list of class names already used
        self.labels = list(existing_labels)
        self.selected_label = None

        layout = QVBoxLayout()

        self.label_list = QListWidget()
        self.label_list.itemClicked.connect(self.on_label_selected)
        self.label_list.itemDoubleClicked.connect(self.on_label_double_clicked)
        self.label_list.setStyleSheet(
            """
            QListWidget::item {
                padding: 8px;
                font-size: 14px;
            }
            """
        )

        self.new_label_edit = QLineEdit()
        self.new_label_edit.setPlaceholderText("Or type a new class name")
        self.new_label_edit.textChanged.connect(self.on_text_changed)
        self.new_label_edit.returnPressed.connect(self.on_return_pressed)

        button_layout = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(self.accept)
        self.ok_button.setEnabled(False)
        self.ok_button.setProperty("class", "primary-button")
        self.ok_button.setDefault(True)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        cancel_button.setProperty("class", "neutral-button")

        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(cancel_button)

        layout.addWidget(QLabel("Select an existing class:"))
        layout.addWidget(self.label_list)
        layout.addWidget(QLabel("Or create a new one:"))
        layout.addWidget(self.new_label_edit)
        layout.addLayout(button_layout)

        self.setLayout(layout)

        self.update_label_list(preselect)

        # Typing is the common case when the class list is short; selecting is
        # the common case once it has grown.
        if not self.labels:
            self.new_label_edit.setFocus()
        else:
            self.label_list.setFocus()

    def update_label_list(self, preselect=None):
        """Fill the list with the classes used so far and preselect one."""
        self.label_list.clear()
        for label in self.labels:
            self.label_list.addItem(QListWidgetItem(label))

        if self.label_list.count() == 0:
            return

        row = 0
        if preselect in self.labels:
            row = self.labels.index(preselect)
        item = self.label_list.item(row)
        self.label_list.setCurrentItem(item)
        self.selected_label = item.text()
        self.ok_button.setEnabled(True)

    def on_label_selected(self, item):
        self.selected_label = item.text()
        self.new_label_edit.clear()
        self.ok_button.setEnabled(True)

    def on_label_double_clicked(self, item):
        self.selected_label = item.text()
        self.accept()

    def on_text_changed(self, text):
        text = text.strip()
        if text:
            self.label_list.clearSelection()
            self.selected_label = text
            self.ok_button.setEnabled(True)
        else:
            # Fall back to whatever is selected in the list, if anything
            current = self.label_list.currentItem()
            if current is not None and current.isSelected():
                self.selected_label = current.text()
                self.ok_button.setEnabled(True)
            else:
                self.selected_label = None
                self.ok_button.setEnabled(False)

    def on_return_pressed(self):
        if self.ok_button.isEnabled():
            self.accept()

    def accept(self):
        typed = self.new_label_edit.text().strip()
        if typed:
            self.selected_label = typed
        if not self.selected_label:
            return
        super().accept()
