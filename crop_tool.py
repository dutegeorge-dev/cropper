"""Fast, keyboard-oriented batch photo cropper built with PySide6 and Pillow."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPen, QPixmap
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
RATIOS = (("16:10", 16 / 10), ("16:9", 16 / 9), ("4:3", 4 / 3), ("1:1", 1.0))


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
        rgba = image.convert("RGBA")
        data = rgba.tobytes("raw", "RGBA")
        qimage = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
        pixmap = QPixmap.fromImage(qimage)
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
        self._build_ui()
        self._add_shortcuts()
        self._update_controls()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        self.view = CropView()
        layout.addWidget(self.view, 1)

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
        hints = QLabel("Enter / Пробел — сохранить и дальше\n← / → — назад / пропустить\n1–4 — выбрать формат рамки")
        hints.setStyleSheet("color: #777")
        panel.addWidget(hints)
        panel.addStretch()

    def _add_shortcuts(self) -> None:
        bindings = (("Return", self.crop_and_next), ("Space", self.crop_and_next),
                    ("Left", self.previous_image), ("Right", self.next_image))
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
        self.source_dir = Path(selected)
        self.files = sorted(
            (path for path in self.source_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS),
            key=lambda path: path.name.casefold(),
        )
        if not self.files:
            self.index = -1
            self.current_image = None
            self.view.clear_image()
            QMessageBox.information(
                self,
                "Нет фотографий",
                "В выбранной папке нет JPG, PNG, WebP, HEIC или HEIF файлов.",
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
        while 0 <= self.index < len(self.files):
            path = self.files[self.index]
            try:
                with Image.open(path) as opened:
                    opened.load()
                    self.current_image = ImageOps.exif_transpose(opened).copy()
                self.view.set_pil_image(self.current_image)
                self.setWindowTitle(f"Быстрая обрезка фото — {path.name}")
                self._update_controls()
                return
            except Exception as error:
                QMessageBox.warning(self, "Не удалось открыть", f"{path.name}\n\n{error}\n\nФайл будет пропущен.")
                self.index += 1
        self.current_image = None
        self.view.clear_image()
        self._update_controls()
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
        base = f"{self.files[self.index].stem}_{suffix}"
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
        self.skip_button.setEnabled(active)
        self.back_button.setEnabled(active and self.index > 0)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Быстрая обрезка фото")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
