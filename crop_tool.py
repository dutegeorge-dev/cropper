"""Fast, keyboard-oriented batch photo cropper built with PySide6 and Pillow."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import av
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


# Register HEIF/HEIC with Pillow before the first Image.open() call.  pillow-heif
# also bundles the native decoder when the application is built by PyInstaller.
register_heif_opener()

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
SUPPORTED_VIDEO_EXTENSIONS = {".mov", ".mp4"}
RATIOS = (("16:10", 16 / 10), ("16:9", 16 / 9), ("4:3", 4 / 3), ("1:1", 1.0))
THUMBNAIL_LOADED_ROLE = 257  # Qt.UserRole + 1


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    """Create an owning Qt pixmap from a Pillow image."""
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimage = QImage(
        data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888
    ).copy()
    return QPixmap.fromImage(qimage)


class CropView(QGraphicsView):
    """Image view whose foreground implements a bounded, fixed-ratio crop box."""

    crop_changed = Signal()
    HANDLE = 12.0

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#202124"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setMouseTracking(True)
        self._pixmap_item = None
        self.image_rect = QRectF()
        self.crop_rect = QRectF()
        self.ratio = RATIOS[1][1]
        self._mode: str | None = None
        self._corner: str | None = None
        self._press_scene = QPointF()
        self._start_rect = QRectF()

    def set_pil_image(self, image: Image.Image) -> None:
        pixmap = pil_to_pixmap(image)
        self.scene().clear()
        self._pixmap_item = self.scene().addPixmap(pixmap)
        self.image_rect = QRectF(0, 0, image.width, image.height)
        self.scene().setSceneRect(self.image_rect)
        self.reset_crop()
        self.fit_image()

    def clear_image(self) -> None:
        self.scene().clear()
        self._pixmap_item = None
        self.image_rect = QRectF()
        self.crop_rect = QRectF()
        self.viewport().update()

    def set_ratio(self, ratio: float) -> None:
        self.ratio = ratio
        if not self.image_rect.isEmpty():
            self.reset_crop()

    def reset_crop(self) -> None:
        if self.image_rect.isEmpty():
            return
        width = self.image_rect.width()
        height = width / self.ratio
        if height > self.image_rect.height():
            height = self.image_rect.height()
            width = height * self.ratio
        self.crop_rect = QRectF(
            (self.image_rect.width() - width) / 2,
            (self.image_rect.height() - height) / 2,
            width,
            height,
        )
        self.crop_changed.emit()
        self.viewport().update()

    def fit_image(self) -> None:
        if not self.image_rect.isEmpty():
            self.fitInView(self.image_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self.fit_image()

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        super().drawForeground(painter, rect)
        if self.crop_rect.isEmpty():
            return
        painter.save()
        outside = QColor(0, 0, 0, 155)
        for area in (
            QRectF(self.image_rect.left(), self.image_rect.top(), self.image_rect.width(), self.crop_rect.top()),
            QRectF(self.image_rect.left(), self.crop_rect.bottom(), self.image_rect.width(), self.image_rect.bottom() - self.crop_rect.bottom()),
            QRectF(self.image_rect.left(), self.crop_rect.top(), self.crop_rect.left(), self.crop_rect.height()),
            QRectF(self.crop_rect.right(), self.crop_rect.top(), self.image_rect.right() - self.crop_rect.right(), self.crop_rect.height()),
        ):
            painter.fillRect(area, outside)
        scale = max(self.transform().m11(), 0.001)
        painter.setPen(QPen(QColor("#ffffff"), 2 / scale))
        painter.drawRect(self.crop_rect)
        handle = self.HANDLE / scale
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(Qt.PenStyle.NoPen)
        for point in self._corners().values():
            painter.drawRect(QRectF(point.x() - handle / 2, point.y() - handle / 2, handle, handle))
        painter.restore()

    def _corners(self) -> dict[str, QPointF]:
        return {
            "tl": self.crop_rect.topLeft(),
            "tr": self.crop_rect.topRight(),
            "bl": self.crop_rect.bottomLeft(),
            "br": self.crop_rect.bottomRight(),
        }

    def _corner_at(self, point: QPointF) -> str | None:
        tolerance = self.HANDLE / max(self.transform().m11(), 0.001)
        for name, corner in self._corners().items():
            if abs(point.x() - corner.x()) <= tolerance and abs(point.y() - corner.y()) <= tolerance:
                return name
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        point = self.mapToScene(event.position().toPoint())
        corner = self._corner_at(point)
        if event.button() == Qt.MouseButton.LeftButton and (corner or self.crop_rect.contains(point)):
            self._mode = "resize" if corner else "move"
            self._corner = corner
            self._press_scene = point
            self._start_rect = QRectF(self.crop_rect)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        point = self.mapToScene(event.position().toPoint())
        if self._mode == "move":
            rect = self._start_rect.translated(point - self._press_scene)
            rect.moveLeft(min(max(rect.left(), self.image_rect.left()), self.image_rect.right() - rect.width()))
            rect.moveTop(min(max(rect.top(), self.image_rect.top()), self.image_rect.bottom() - rect.height()))
            self.crop_rect = rect
        elif self._mode == "resize" and self._corner:
            self._resize_from_corner(point)
        else:
            self.viewport().setCursor(
                Qt.CursorShape.SizeAllCursor if self.crop_rect.contains(point) else Qt.CursorShape.ArrowCursor
            )
            super().mouseMoveEvent(event)
            return
        self.crop_changed.emit()
        self.viewport().update()
        event.accept()

    def _resize_from_corner(self, point: QPointF) -> None:
        opposite_name = {"tl": "br", "tr": "bl", "bl": "tr", "br": "tl"}[self._corner]
        anchor = self._corners()[opposite_name]
        sx = -1 if "l" in self._corner else 1
        sy = -1 if "t" in self._corner else 1
        max_width = (anchor.x() - self.image_rect.left()) if sx < 0 else (self.image_rect.right() - anchor.x())
        max_height = (anchor.y() - self.image_rect.top()) if sy < 0 else (self.image_rect.bottom() - anchor.y())
        width = max(20.0, abs(point.x() - anchor.x()))
        height = max(20.0, abs(point.y() - anchor.y()))
        # Choose the dimension closest to the pointer, then constrain it to the image.
        if width / height > self.ratio:
            height = width / self.ratio
        else:
            width = height * self.ratio
        width = min(width, max_width, max_height * self.ratio)
        height = width / self.ratio
        left = anchor.x() - width if sx < 0 else anchor.x()
        top = anchor.y() - height if sy < 0 else anchor.y()
        self.crop_rect = QRectF(left, top, width, height).normalized()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._mode:
            self._mode = None
            self._corner = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SeekSlider(QSlider):
    """Slider that seeks to the exact mouse position, not by a small page step."""

    def _value_at(self, x: float) -> int:
        usable_width = max(1, self.width() - 1)
        fraction = min(1.0, max(0.0, x / usable_width))
        return round(self.minimum() + fraction * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setValue(self._value_at(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Быстрая обрезка фото")
        self.resize(1200, 760)
        self.files: list[Path] = []
        self.index = -1
        self.source_dir: Path | None = None
        self.output_dir: Path | None = None
        self.current_image: Image.Image | None = None
        self.collage_files: set[Path] = set()
        self.rotations: dict[Path, int] = {}
        self.video_positions: dict[Path, float] = {}
        self.video_container = None
        self.video_stream = None
        self.video_duration = 0.0
        self.video_fps = 30.0
        self.current_is_video = False
        self._thumbnail_load_scheduled = False
        self._build_ui()
        self._add_shortcuts()
        self._update_controls()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        self.browser = QListWidget()
        self.browser.setViewMode(QListView.ViewMode.IconMode)
        self.browser.setResizeMode(QListView.ResizeMode.Adjust)
        self.browser.setMovement(QListView.Movement.Static)
        self.browser.setIconSize(QSize(116, 82))
        self.browser.setGridSize(QSize(138, 118))
        self.browser.setWordWrap(True)
        self.browser.setFixedWidth(302)
        self.browser.setToolTip("Все фото и видео из выбранной папки")
        self.browser.itemClicked.connect(self._open_browser_item)
        self.browser.verticalScrollBar().valueChanged.connect(
            lambda: self._schedule_visible_thumbnails()
        )
        layout.addWidget(self.browser)
        media = QVBoxLayout()
        self.view = CropView()
        media.addWidget(self.view, 1)
        layout.addLayout(media, 1)

        panel = QVBoxLayout()
        panel.setSpacing(12)
        layout.addLayout(panel)
        source_button = QPushButton("Открыть папку…")
        source_button.clicked.connect(self.open_source)
        output_button = QPushButton("Папка назначения…")
        output_button.clicked.connect(self.choose_output)
        panel.addWidget(source_button)
        panel.addWidget(output_button)
        self.output_label = QLabel("Назначение: —")
        self.output_label.setWordWrap(True)
        panel.addWidget(self.output_label)

        ratio_box = QGroupBox("Соотношение сторон")
        ratio_layout = QVBoxLayout(ratio_box)
        self.ratio_group = QButtonGroup(self)
        for number, (label, ratio) in enumerate(RATIOS, 1):
            button = QRadioButton(f"{number} — {label}")
            button.setProperty("ratio", ratio)
            button.setProperty("suffix", label.replace(":", "x"))
            numerator, denominator = (int(part) for part in label.split(":"))
            button.setProperty("ratioWidth", numerator)
            button.setProperty("ratioHeight", denominator)
            self.ratio_group.addButton(button)
            ratio_layout.addWidget(button)
            if label == "16:9":
                button.setChecked(True)
        self.ratio_group.buttonClicked.connect(self.ratio_selected)
        panel.addWidget(ratio_box)

        settings = QGroupBox("Сохранение")
        form = QFormLayout(settings)
        self.format_combo = QComboBox()
        self.format_combo.addItems(("JPG", "PNG"))
        form.addRow("Формат:", self.format_combo)
        quality_row = QHBoxLayout()
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(1, 100)
        self.quality_slider.setValue(95)
        self.quality_value = QLabel("95")
        self.quality_slider.valueChanged.connect(lambda value: self.quality_value.setText(str(value)))
        quality_row.addWidget(self.quality_slider)
        quality_row.addWidget(self.quality_value)
        form.addRow("Качество:", quality_row)
        self.limit_checkbox = QCheckBox("Ограничить длинную сторону")
        form.addRow(self.limit_checkbox)
        self.max_size = QSpinBox()
        self.max_size.setRange(100, 20000)
        self.max_size.setValue(2000)
        self.max_size.setSuffix(" px")
        self.max_size.setEnabled(False)
        self.limit_checkbox.toggled.connect(self.max_size.setEnabled)
        form.addRow("Максимум:", self.max_size)
        panel.addWidget(settings)

        self.video_box = QGroupBox("Кадр из видео")
        video_layout = QVBoxLayout(self.video_box)
        self.video_time_label = QLabel("00:00.000 / 00:00.000")
        self.video_time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.video_slider.setRange(0, 10000)
        self.video_slider.setTracking(False)
        self.video_slider.sliderReleased.connect(self.seek_video_from_slider)
        self.video_slider.valueChanged.connect(self._update_video_time_preview)
        frame_buttons = QHBoxLayout()
        self.previous_frame_button = QPushButton("← Кадр")
        self.next_frame_button = QPushButton("Кадр →")
        self.previous_frame_button.clicked.connect(lambda: self.step_video_frame(-1))
        self.next_frame_button.clicked.connect(lambda: self.step_video_frame(1))
        frame_buttons.addWidget(self.previous_frame_button)
        frame_buttons.addWidget(self.next_frame_button)
        video_layout.addWidget(self.video_time_label)
        video_layout.addWidget(self.video_slider)
        video_layout.addLayout(frame_buttons)
        self.video_box.setVisible(False)
        panel.addWidget(self.video_box)

        rotate = QHBoxLayout()
        self.rotate_left_button = QPushButton("↶ 90° влево")
        self.rotate_right_button = QPushButton("90° вправо ↷")
        self.rotate_left_button.setToolTip("Повернуть против часовой стрелки (Ctrl+←)")
        self.rotate_right_button.setToolTip("Повернуть по часовой стрелке (Ctrl+→)")
        self.rotate_left_button.clicked.connect(self.rotate_left)
        self.rotate_right_button.clicked.connect(self.rotate_right)
        rotate.addWidget(self.rotate_left_button)
        rotate.addWidget(self.rotate_right_button)
        panel.addLayout(rotate)

        collage_box = QGroupBox("Коллаж")
        collage_layout = QVBoxLayout(collage_box)
        self.collage_count = QLabel("Выбрано фото: 0")
        self.collage_toggle_button = QPushButton("Добавить текущее фото")
        self.collage_create_button = QPushButton("Создать коллаж")
        self.collage_clear_button = QPushButton("Очистить выбор")
        self.collage_toggle_button.clicked.connect(self.toggle_collage_photo)
        self.collage_create_button.clicked.connect(self.create_collage)
        self.collage_clear_button.clicked.connect(self.clear_collage)
        collage_layout.addWidget(self.collage_count)
        collage_layout.addWidget(self.collage_toggle_button)
        collage_actions = QHBoxLayout()
        collage_actions.addWidget(self.collage_create_button)
        collage_actions.addWidget(self.collage_clear_button)
        collage_layout.addLayout(collage_actions)
        panel.addWidget(collage_box)

        self.counter = QLabel("Фото 0 из 0")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.addWidget(self.counter)
        nav = QHBoxLayout()
        self.back_button = QPushButton("← Назад")
        self.skip_button = QPushButton("Пропустить →")
        self.back_button.clicked.connect(self.previous_image)
        self.skip_button.clicked.connect(self.next_image)
        nav.addWidget(self.back_button)
        nav.addWidget(self.skip_button)
        panel.addLayout(nav)
        self.crop_button = QPushButton("Обрезать и следующее")
        self.crop_button.setMinimumHeight(48)
        self.crop_button.clicked.connect(self.crop_and_next)
        panel.addWidget(self.crop_button)
        hints = QLabel(
            "Enter / Пробел — сохранить и дальше\n"
            "← / → — назад / пропустить\n"
            "Ctrl+← / Ctrl+→ — повернуть на 90°\n"
            "1–4 — выбрать формат рамки\n"
            "Клик по миниатюре — открыть файл"
        )
        hints.setStyleSheet("color: #777")
        panel.addWidget(hints)
        panel.addStretch()

    def _add_shortcuts(self) -> None:
        bindings = (
            ("Return", self.crop_and_next),
            ("Space", self.crop_and_next),
            ("Left", self.previous_image),
            ("Right", self.next_image),
            ("Ctrl+Left", self.rotate_left),
            ("Ctrl+Right", self.rotate_right),
        )
        for key, handler in bindings:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            action.triggered.connect(handler)
            self.addAction(action)
        for number, button in enumerate(self.ratio_group.buttons(), 1):
            action = QAction(self)
            action.setShortcut(QKeySequence(str(number)))
            action.triggered.connect(button.click)
            self.addAction(action)

    def open_source(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Выберите папку с фотографиями")
        if not selected:
            return
        self._close_video()
        self.source_dir = Path(selected)
        self.collage_files.clear()
        self.rotations.clear()
        self.video_positions.clear()
        self.files = sorted(
            (
                path
                for path in self.source_dir.iterdir()
                if path.is_file()
                and path.suffix.lower() in SUPPORTED_EXTENSIONS | SUPPORTED_VIDEO_EXTENSIONS
            ),
            key=lambda path: path.name.casefold(),
        )
        self._populate_browser()
        if not self.files:
            self.index = -1
            self.current_image = None
            self.current_is_video = False
            self.video_box.setVisible(False)
            self.view.clear_image()
            QMessageBox.information(
                self,
                "Нет фотографий",
                "В выбранной папке нет JPG, PNG, WebP, HEIC, HEIF, MOV или MP4 файлов.",
            )
        else:
            self.output_dir = self.source_dir / "cropped"
            self.output_label.setText(f"Назначение: {self.output_dir}")
            self.index = 0
            self.load_current()
        self._update_controls()

    def choose_output(self) -> None:
        start = str(self.output_dir or self.source_dir or Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Выберите папку назначения", start)
        if selected:
            self.output_dir = Path(selected)
            self.output_label.setText(f"Назначение: {self.output_dir}")

    def load_current(self) -> None:
        self._close_video()
        while 0 <= self.index < len(self.files):
            path = self.files[self.index]
            try:
                if path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
                    self._open_video(path)
                    position = self.video_positions.get(path, 0.0)
                    self.current_image = self._decode_video_frame(position)
                    self.current_is_video = True
                    self.video_box.setVisible(True)
                    self._set_video_slider(position)
                else:
                    with Image.open(path) as opened:
                        opened.load()
                        self.current_image = ImageOps.exif_transpose(opened).copy()
                    self.current_is_video = False
                    self.video_box.setVisible(False)
                self._apply_saved_rotation(path)
                self.view.set_pil_image(self.current_image)
                self.setWindowTitle(f"Быстрая обрезка фото — {path.name}")
                self._update_controls()
                self._sync_browser()
                return
            except Exception as error:
                self._close_video()
                QMessageBox.warning(self, "Не удалось открыть", f"{path.name}\n\n{error}\n\nФайл будет пропущен.")
                self.index += 1
        self.current_image = None
        self.current_is_video = False
        self.video_box.setVisible(False)
        self.view.clear_image()
        self._update_controls()
        self._sync_browser()
        if self.files and self.index >= len(self.files):
            QMessageBox.information(self, "Готово", "Все фотографии обработаны или пропущены.")

    def ratio_selected(self, button) -> None:
        self.view.set_ratio(float(button.property("ratio")))

    def previous_image(self) -> None:
        if self.files and self.index > 0:
            self.index -= 1
            self.load_current()

    def next_image(self) -> None:
        if self.files and self.index < len(self.files):
            self.index += 1
            self.load_current()

    def rotate_left(self) -> None:
        """Rotate the working image 90 degrees counter-clockwise."""
        self._rotate(Image.Transpose.ROTATE_90, -1)

    def rotate_right(self) -> None:
        """Rotate the working image 90 degrees clockwise."""
        self._rotate(Image.Transpose.ROTATE_270, 1)

    def _rotate(self, operation: Image.Transpose, quarter_turns: int) -> None:
        if self.current_image is None:
            return
        self.current_image = self.current_image.transpose(operation)
        path = self.files[self.index]
        self.rotations[path] = (self.rotations.get(path, 0) + quarter_turns) % 4
        # The maximum centered crop is easier to position after orientation changes.
        self.view.set_pil_image(self.current_image)
        self._invalidate_browser_thumbnail(self.index)
        self._sync_browser()

    def _apply_saved_rotation(self, path: Path) -> None:
        turns = self.rotations.get(path, 0)
        if turns:
            operation = (
                Image.Transpose.ROTATE_270,
                Image.Transpose.ROTATE_180,
                Image.Transpose.ROTATE_90,
            )[turns - 1]
            self.current_image = self.current_image.transpose(operation)

    def _populate_browser(self) -> None:
        """Create lightweight placeholders; thumbnails are decoded only when visible."""
        self.browser.clear()
        for target_index, path in enumerate(self.files):
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, target_index)
            item.setData(THUMBNAIL_LOADED_ROLE, False)
            item.setToolTip(f"{target_index + 1} из {len(self.files)} — {path.name}")
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.browser.addItem(item)
        self._schedule_visible_thumbnails()

    def _open_browser_item(self, item: QListWidgetItem) -> None:
        self.index = int(item.data(Qt.ItemDataRole.UserRole))
        self.load_current()

    def _schedule_visible_thumbnails(self) -> None:
        if self._thumbnail_load_scheduled:
            return
        self._thumbnail_load_scheduled = True
        QTimer.singleShot(0, self._load_visible_thumbnails)

    def _load_visible_thumbnails(self) -> None:
        self._thumbnail_load_scheduled = False
        visible = self.browser.viewport().rect().adjusted(0, -120, 0, 120)
        for row in range(self.browser.count()):
            item = self.browser.item(row)
            if item.data(THUMBNAIL_LOADED_ROLE):
                continue
            if not self.browser.visualItemRect(item).intersects(visible):
                continue
            path = self.files[row]
            try:
                thumbnail = self._load_preview(path)
                item.setIcon(QIcon(pil_to_pixmap(thumbnail)))
            except Exception:
                item.setText(f"⚠ {path.name}")
            item.setData(THUMBNAIL_LOADED_ROLE, True)

    def _invalidate_browser_thumbnail(self, row: int) -> None:
        if 0 <= row < self.browser.count():
            self.browser.item(row).setData(THUMBNAIL_LOADED_ROLE, False)
            self._schedule_visible_thumbnails()

    def _sync_browser(self) -> None:
        """Synchronize selection and collage highlighting in the file browser."""
        if not self.files or not (0 <= self.index < self.browser.count()):
            return
        self.browser.setCurrentRow(self.index)
        self.browser.scrollToItem(
            self.browser.item(self.index), QListView.ScrollHint.EnsureVisible
        )
        for row in range(self.browser.count()):
            path = self.files[row]
            color = QColor("#dff5df") if path in self.collage_files else QColor("transparent")
            self.browser.item(row).setBackground(QBrush(color))
        self._schedule_visible_thumbnails()

    def toggle_collage_photo(self) -> None:
        if self.current_image is None:
            return
        path = self.files[self.index]
        if path in self.collage_files:
            self.collage_files.remove(path)
        else:
            self.collage_files.add(path)
        self._update_controls()
        self._sync_browser()

    def clear_collage(self) -> None:
        self.collage_files.clear()
        self._update_controls()
        self._sync_browser()

    def create_collage(self) -> None:
        if len(self.collage_files) < 2 or self.output_dir is None:
            QMessageBox.information(self, "Коллаж", "Выберите не менее двух фотографий.")
            return
        ordered_paths = [path for path in self.files if path in self.collage_files]
        try:
            images = [self._load_for_collage(path) for path in ordered_paths]
            collage = self._compose_collage(images)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            suffix = str(self.ratio_group.checkedButton().property("suffix"))
            extension = ".jpg" if self.format_combo.currentText() == "JPG" else ".png"
            output_path = self._unique_named_path(f"collage_{suffix}", extension)
            if extension == ".jpg":
                collage.save(
                    output_path,
                    "JPEG",
                    quality=self.quality_slider.value(),
                    subsampling=0,
                )
            else:
                collage.save(output_path, "PNG", compress_level=6)
        except Exception as error:
            QMessageBox.critical(self, "Ошибка коллажа", f"Не удалось создать коллаж:\n{error}")
            return
        QMessageBox.information(self, "Коллаж сохранён", str(output_path))

    def _load_for_collage(self, path: Path) -> Image.Image:
        if path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
            image = self._read_video_frame(path, self.video_positions.get(path, 0.0))
        else:
            with Image.open(path) as opened:
                opened.load()
                image = ImageOps.exif_transpose(opened).convert("RGB")
        turns = self.rotations.get(path, 0)
        if turns:
            operation = (
                Image.Transpose.ROTATE_270,
                Image.Transpose.ROTATE_180,
                Image.Transpose.ROTATE_90,
            )[turns - 1]
            image = image.transpose(operation)
        return image

    def _load_preview(self, path: Path) -> Image.Image:
        """Load a photo or the remembered frame of a video for a thumbnail."""
        if path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
            image = self._read_video_frame(path, self.video_positions.get(path, 0.0))
        else:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).copy()
        turns = self.rotations.get(path, 0)
        if turns:
            operation = (
                Image.Transpose.ROTATE_270,
                Image.Transpose.ROTATE_180,
                Image.Transpose.ROTATE_90,
            )[turns - 1]
            image = image.transpose(operation)
        image.thumbnail((106, 76), Image.Resampling.LANCZOS)
        return image

    def _open_video(self, path: Path) -> None:
        self.video_container = av.open(str(path))
        if not self.video_container.streams.video:
            raise ValueError("В файле нет видеодорожки")
        self.video_stream = self.video_container.streams.video[0]
        if self.video_container.duration is not None:
            self.video_duration = float(self.video_container.duration / av.time_base)
        elif self.video_stream.duration is not None and self.video_stream.time_base is not None:
            self.video_duration = float(self.video_stream.duration * self.video_stream.time_base)
        else:
            self.video_duration = 0.0
        if self.video_stream.average_rate:
            self.video_fps = max(1.0, float(self.video_stream.average_rate))
        else:
            self.video_fps = 30.0

    def _close_video(self) -> None:
        if self.video_container is not None:
            self.video_container.close()
        self.video_container = None
        self.video_stream = None

    def _decode_video_frame(self, seconds: float) -> Image.Image:
        if self.video_container is None or self.video_stream is None:
            raise ValueError("Видео не открыто")
        last_frame_time = max(0.0, self.video_duration - 1 / self.video_fps)
        seconds = max(0.0, min(seconds, last_frame_time))
        self.video_container.seek(
            max(0, int(seconds * av.time_base)),
            backward=True,
            any_frame=False,
        )
        selected = None
        for frame in self.video_container.decode(self.video_stream):
            selected = frame
            if frame.time is None or float(frame.time) >= seconds:
                break
        if selected is None:
            raise ValueError("Не удалось декодировать кадр видео")
        actual_time = float(selected.time) if selected.time is not None else seconds
        path = self.files[self.index]
        self.video_positions[path] = actual_time
        return selected.to_image().convert("RGB")

    @staticmethod
    def _read_video_frame(path: Path, seconds: float) -> Image.Image:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError(f"В {path.name} нет видеодорожки")
            stream = container.streams.video[0]
            container.seek(
                max(0, int(seconds * av.time_base)),
                backward=True,
                any_frame=False,
            )
            selected = None
            for frame in container.decode(stream):
                selected = frame
                if frame.time is None or float(frame.time) >= seconds:
                    break
            if selected is None:
                raise ValueError(f"Не удалось прочитать кадр из {path.name}")
            return selected.to_image().convert("RGB")

    def seek_video_from_slider(self) -> None:
        if not self.current_is_video:
            return
        seconds = self.video_duration * self.video_slider.value() / 10000
        self._show_video_frame(seconds)

    def step_video_frame(self, direction: int) -> None:
        if not self.current_is_video:
            return
        path = self.files[self.index]
        seconds = self.video_positions.get(path, 0.0) + direction / self.video_fps
        self._show_video_frame(seconds)

    def _show_video_frame(self, seconds: float) -> None:
        try:
            self.current_image = self._decode_video_frame(seconds)
            self._apply_saved_rotation(self.files[self.index])
            self.view.set_pil_image(self.current_image)
            actual = self.video_positions.get(self.files[self.index], seconds)
            self._set_video_slider(actual)
        except Exception as error:
            QMessageBox.warning(self, "Видео", f"Не удалось открыть кадр:\n{error}")

    def _set_video_slider(self, seconds: float) -> None:
        value = round(10000 * seconds / self.video_duration) if self.video_duration else 0
        self.video_slider.blockSignals(True)
        self.video_slider.setValue(max(0, min(10000, value)))
        self.video_slider.blockSignals(False)
        self.video_time_label.setText(
            f"{self._format_video_time(seconds)} / {self._format_video_time(self.video_duration)}"
        )

    def _update_video_time_preview(self, value: int) -> None:
        seconds = self.video_duration * value / 10000
        self.video_time_label.setText(
            f"{self._format_video_time(seconds)} / {self._format_video_time(self.video_duration)}"
        )

    @staticmethod
    def _format_video_time(seconds: float) -> str:
        milliseconds = max(0, round(seconds * 1000))
        minutes, remainder = divmod(milliseconds, 60_000)
        whole_seconds, milliseconds = divmod(remainder, 1000)
        return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"

    def _compose_collage(self, images: list[Image.Image]) -> Image.Image:
        """Place center-cropped photos in an automatic white grid."""
        button = self.ratio_group.checkedButton()
        ratio_width = int(button.property("ratioWidth"))
        ratio_height = int(button.property("ratioHeight"))
        target_long = self.max_size.value() if self.limit_checkbox.isChecked() else max(
            max(image.size) for image in images
        )
        factor = max(1, target_long // ratio_width)
        canvas_width = ratio_width * factor
        canvas_height = ratio_height * factor
        columns = min(len(images), max(1, math.ceil(math.sqrt(len(images) * ratio_width / ratio_height))))
        rows = math.ceil(len(images) / columns)
        gap = max(4, round(min(canvas_width, canvas_height) * 0.015))
        cell_width = (canvas_width - gap * (columns + 1)) // columns
        cell_height = (canvas_height - gap * (rows + 1)) // rows
        if cell_width < 1 or cell_height < 1:
            raise ValueError("Выбранный максимальный размер слишком мал для такого количества фото")
        canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
        for position, image in enumerate(images):
            row, column = divmod(position, columns)
            scale = max(cell_width / image.width, cell_height / image.height)
            resized = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS,
            )
            left = (resized.width - cell_width) // 2
            top = (resized.height - cell_height) // 2
            tile = resized.crop((left, top, left + cell_width, top + cell_height))
            x = gap + column * (cell_width + gap)
            y = gap + row * (cell_height + gap)
            canvas.paste(tile, (x, y))
        return canvas

    def crop_and_next(self) -> None:
        if self.current_image is None or self.output_dir is None or self.view.crop_rect.isEmpty():
            return
        rect = self.view.crop_rect
        left, top, right, bottom = self._pixel_crop_box(rect)
        try:
            result = self.current_image.crop((left, top, right, bottom))
            if self.limit_checkbox.isChecked():
                maximum = self.max_size.value()
                if max(result.size) > maximum:
                    result.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            output_path = self._unique_output_path()
            if self.format_combo.currentText() == "JPG":
                if result.mode not in ("RGB", "L"):
                    background = Image.new("RGB", result.size, "white")
                    if "A" in result.getbands():
                        background.paste(result, mask=result.getchannel("A"))
                    else:
                        background.paste(result)
                    result = background
                result.save(output_path, "JPEG", quality=self.quality_slider.value(), subsampling=0)
            else:
                result.save(output_path, "PNG", compress_level=6)
        except Exception as error:
            QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось сохранить файл:\n{error}")
            return
        if self.current_is_video:
            self.statusBar().showMessage(f"Кадр сохранён: {output_path}", 5000)
            return
        self.index += 1
        self.load_current()

    def _pixel_crop_box(self, rect: QRectF) -> tuple[int, int, int, int]:
        """Convert the visual box to pixels while retaining the exact preset ratio."""
        button = self.ratio_group.checkedButton()
        ratio_width = int(button.property("ratioWidth"))
        ratio_height = int(button.property("ratioHeight"))
        # Use whole multiples of the ratio (16x9, 8x5, etc.) so Pillow's
        # integer crop coordinates cannot introduce a one-pixel ratio error.
        factor = max(1, min(round(rect.width() / ratio_width), round(rect.height() / ratio_height)))
        width = min(ratio_width * factor, self.current_image.width)
        height = min(ratio_height * factor, self.current_image.height)
        center_x = rect.center().x()
        center_y = rect.center().y()
        left = min(max(0, round(center_x - width / 2)), self.current_image.width - width)
        top = min(max(0, round(center_y - height / 2)), self.current_image.height - height)
        return left, top, left + width, top + height

    def _unique_output_path(self) -> Path:
        suffix = str(self.ratio_group.checkedButton().property("suffix"))
        extension = ".jpg" if self.format_combo.currentText() == "JPG" else ".png"
        source = self.files[self.index]
        if self.current_is_video:
            milliseconds = round(self.video_positions.get(source, 0.0) * 1000)
            base = f"{source.stem}_{milliseconds:09d}ms_{suffix}"
        else:
            base = f"{source.stem}_{suffix}"
        return self._unique_named_path(base, extension)

    def _unique_named_path(self, base: str, extension: str) -> Path:
        candidate = self.output_dir / f"{base}{extension}"
        number = 2
        while candidate.exists():
            candidate = self.output_dir / f"{base}_{number}{extension}"
            number += 1
        return candidate

    def _update_controls(self) -> None:
        total = len(self.files)
        shown = min(self.index + 1, total) if self.index >= 0 else 0
        self.counter.setText(f"Фото {shown} из {total}")
        active = self.current_image is not None
        self.crop_button.setEnabled(active)
        self.crop_button.setText(
            "Сохранить кадр" if self.current_is_video else "Обрезать и следующее"
        )
        self.skip_button.setEnabled(active)
        self.back_button.setEnabled(active and self.index > 0)
        self.rotate_left_button.setEnabled(active)
        self.rotate_right_button.setEnabled(active)
        selected = len(self.collage_files)
        self.collage_count.setText(f"Выбрано фото: {selected}")
        current_selected = active and self.files[self.index] in self.collage_files
        self.collage_toggle_button.setText(
            "Убрать текущее фото" if current_selected else "Добавить текущее фото"
        )
        self.collage_toggle_button.setEnabled(active)
        self.collage_create_button.setEnabled(selected >= 2 and self.output_dir is not None)
        self.collage_clear_button.setEnabled(selected > 0)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._close_video()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Быстрая обрезка фото")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
