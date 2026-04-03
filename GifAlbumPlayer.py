import sys
from pathlib import Path

from PIL import Image, ImageSequence
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QSpinBox,
)


class GifAlbumPlayer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("GIF Album Player")
        self.resize(700, 900)

        self.gif_files: list[Path] = []
        self.current_index: int = -1

        self.frames: list[QPixmap] = []
        self.durations: list[int] = []
        self.current_frame_index: int = 0

        self.elapsed_time_ms: int = 0  # ← 追加（経過時間）

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._advance_frame)

        self._build_ui()
        self._build_menu()

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        controls = QHBoxLayout()
        root.addLayout(controls)

        self.open_button = QPushButton("フォルダを開く")
        self.open_button.clicked.connect(self.choose_folder)
        controls.addWidget(self.open_button)

        # 最低再生時間
        controls.addWidget(QLabel("最低再生時間(秒)"))

        self.time_spin = QSpinBox()
        self.time_spin.setMinimum(3)
        self.time_spin.setMaximum(999)
        self.time_spin.setValue(3)
        self.time_spin.valueChanged.connect(self._on_time_changed)
        controls.addWidget(self.time_spin)

        controls.addStretch()

        self.count_label = QLabel("GIF数: 0")
        controls.addWidget(self.count_label)

        self.file_label = QLabel("再生中: なし")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.file_label)

        self.image_label = QLabel("GIFフォルダを選択してください")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet(
            "background-color: black; color: white; border: 1px solid #444;"
        )
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image_label.setMinimumSize(200, 200)
        root.addWidget(self.image_label, 1)

        self.status_label = QLabel("待機中")
        root.addWidget(self.status_label)

    def _build_menu(self) -> None:
        open_action = QAction("フォルダを開く", self)
        open_action.triggered.connect(self.choose_folder)
        open_action.setShortcut("Ctrl+O")
        self.addAction(open_action)

    # ---------------- フォルダ ----------------

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "GIFフォルダを選択")
        if not folder:
            return
        self.load_folder(Path(folder))

    def load_folder(self, folder: Path) -> None:
        gif_files = sorted(
            [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".gif"]
        )

        self.stop_playback()
        self.gif_files = gif_files
        self.current_index = -1
        self.count_label.setText(f"GIF数: {len(self.gif_files)}")

        if not self.gif_files:
            self.file_label.setText("再生中: なし")
            self.image_label.setText("このフォルダにはGIFがありません")
            self.status_label.setText("GIFが見つかりません")
            return

        self.status_label.setText(f"読み込み完了: {folder}")
        self.play_index(0)

    # ---------------- 再生 ----------------

    def play_index(self, index: int) -> None:
        if not self.gif_files:
            return

        self.stop_playback()

        self.current_index = index % len(self.gif_files)
        gif_path = self.gif_files[self.current_index]

        self.file_label.setText(f"再生中: {gif_path.name}")
        self.status_label.setText(
            f"{self.current_index + 1} / {len(self.gif_files)} を再生中"
        )

        success = self._load_gif_frames(gif_path)
        if not success:
            self.status_label.setText(f"壊れているGIFをスキップ: {gif_path.name}")
            self.play_next()
            return

        self.current_frame_index = 0
        self.elapsed_time_ms = 0  # ← リセット
        self._show_current_frame()
        self._schedule_next_frame()

    def play_next(self) -> None:
        if not self.gif_files:
            return
        next_index = (self.current_index + 1) % len(self.gif_files)
        self.play_index(next_index)

    def stop_playback(self) -> None:
        self.timer.stop()
        self.frames = []
        self.durations = []
        self.current_frame_index = 0
        self.elapsed_time_ms = 0
        self.image_label.clear()

    # ---------------- GIF読み込み ----------------

    def _load_gif_frames(self, gif_path: Path) -> bool:
        self.frames = []
        self.durations = []

        try:
            with Image.open(gif_path) as img:
                for frame in ImageSequence.Iterator(img):
                    frame_rgba = frame.convert("RGBA")
                    pixmap = self._pil_to_pixmap(frame_rgba)
                    self.frames.append(pixmap)

                    duration = frame.info.get("duration", 100)
                    if not isinstance(duration, int) or duration <= 0:
                        duration = 100
                    self.durations.append(duration)

            return len(self.frames) > 0

        except Exception:
            return False

    def _pil_to_pixmap(self, image):
        data = image.tobytes("raw", "RGBA")
        qimage = QImage(
            data,
            image.width,
            image.height,
            image.width * 4,
            QImage.Format_RGBA8888,
        )
        return QPixmap.fromImage(qimage.copy())

    # ---------------- 表示 ----------------

    def _show_current_frame(self) -> None:
        if not self.frames:
            return

        original = self.frames[self.current_frame_index]
        fitted = original.scaled(
            self.image_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image_label.setPixmap(fitted)

    def _schedule_next_frame(self) -> None:
        duration = self.durations[self.current_frame_index]
        self.timer.start(duration)

    def _advance_frame(self) -> None:
        if not self.frames:
            return

        self.elapsed_time_ms += self.durations[self.current_frame_index]
        self.current_frame_index += 1

        # 最後のフレーム
        if self.current_frame_index >= len(self.frames):
            min_time_ms = self.time_spin.value() * 1000

            if self.elapsed_time_ms >= min_time_ms:
                self.play_next()
                return
            else:
                # ループ
                self.current_frame_index = 0

        self._show_current_frame()
        self._schedule_next_frame()

    # ---------------- UIイベント ----------------

    def _on_time_changed(self):
        # タイマーリセット（要求仕様）
        self.elapsed_time_ms = 0

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._show_current_frame()

    def closeEvent(self, event) -> None:
        self.stop_playback()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = GifAlbumPlayer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
