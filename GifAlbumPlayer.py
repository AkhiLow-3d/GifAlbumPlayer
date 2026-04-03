import sys
from pathlib import Path

from PIL import Image, ImageSequence
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, QSettings
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class PreloadSignals(QObject):
    finished = Signal(int, int, object, object, str)


class GifPreloadTask(QRunnable):
    def __init__(self, job_id: int, index: int, gif_path: Path) -> None:
        super().__init__()
        self.job_id = job_id
        self.index = index
        self.gif_path = gif_path
        self.signals = PreloadSignals()

    def run(self) -> None:
        images: list[QImage] = []
        durations: list[int] = []

        try:
            with Image.open(self.gif_path) as img:
                for frame in ImageSequence.Iterator(img):
                    frame_rgba = frame.convert("RGBA")
                    data = frame_rgba.tobytes("raw", "RGBA")
                    qimage = QImage(
                        data,
                        frame_rgba.width,
                        frame_rgba.height,
                        frame_rgba.width * 4,
                        QImage.Format_RGBA8888,
                    ).copy()
                    images.append(qimage)

                    duration = frame.info.get("duration", 100)
                    if not isinstance(duration, int) or duration <= 0:
                        duration = 100
                    durations.append(duration)

            self.signals.finished.emit(self.job_id, self.index, images, durations, "")
        except Exception as e:
            self.signals.finished.emit(self.job_id, self.index, [], [], str(e))


class GifAlbumPlayer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.settings = QSettings("akhiLow", "GifAlbumPlayer")

        self.setWindowTitle("GIF Album Player")
        self.resize(700, 900)

        self.gif_files: list[Path] = []
        self.current_index: int = -1

        self.images: list[QImage] = []
        self.durations: list[int] = []
        self.current_frame_index: int = 0
        self.elapsed_time_ms: int = 0

        self.preloaded_index: int | None = None
        self.preloaded_images: list[QImage] = []
        self.preloaded_durations: list[int] = []
        self.preload_job_id: int = 0
        self.thread_pool = QThreadPool.globalInstance()

        self.is_paused: bool = False
        self.is_fullscreen: bool = False

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._advance_frame)

        self._build_ui()
        self._build_actions()
        self._restore_settings()

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
        self.open_button.setAutoDefault(False)
        self.open_button.setDefault(False)
        self.open_button.setFocusPolicy(Qt.NoFocus)
        controls.addWidget(self.open_button)

        controls.addWidget(QLabel("最低再生時間(秒)"))

        self.time_spin = QSpinBox()
        self.time_spin.setMinimum(3)
        self.time_spin.setMaximum(999)
        self.time_spin.setValue(3)
        self.time_spin.setFocusPolicy(Qt.ClickFocus)
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
        self.image_label.setFocusPolicy(Qt.StrongFocus)
        root.addWidget(self.image_label, 1)

        self.help_label = QLabel("Space: 一時停止  ←/→: 前後移動  F: 全画面")
        root.addWidget(self.help_label)

        self.status_label = QLabel("待機中")
        root.addWidget(self.status_label)

        self.setFocusPolicy(Qt.StrongFocus)
        self.centralWidget().setFocusPolicy(Qt.StrongFocus)
        self.image_label.setFocus()

    def _build_actions(self) -> None:
        self.open_action = QAction("フォルダを開く", self)
        self.open_action.triggered.connect(self.choose_folder)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.setShortcutContext(Qt.WindowShortcut)
        self.addAction(self.open_action)

        self.pause_action = QAction("一時停止/再生", self)
        self.pause_action.triggered.connect(self.toggle_pause)
        self.pause_action.setShortcut(QKeySequence(Qt.Key_Space))
        self.pause_action.setShortcutContext(Qt.WindowShortcut)
        self.addAction(self.pause_action)

        self.next_action = QAction("次へ", self)
        self.next_action.triggered.connect(self.play_next)
        self.next_action.setShortcut(QKeySequence(Qt.Key_Right))
        self.next_action.setShortcutContext(Qt.WindowShortcut)
        self.addAction(self.next_action)

        self.prev_action = QAction("前へ", self)
        self.prev_action.triggered.connect(self.play_previous)
        self.prev_action.setShortcut(QKeySequence(Qt.Key_Left))
        self.prev_action.setShortcutContext(Qt.WindowShortcut)
        self.addAction(self.prev_action)

        self.fullscreen_action = QAction("全画面", self)
        self.fullscreen_action.triggered.connect(self.toggle_fullscreen)
        self.fullscreen_action.setShortcut(QKeySequence(Qt.Key_F))
        self.fullscreen_action.setShortcutContext(Qt.WindowShortcut)
        self.addAction(self.fullscreen_action)

    # ---------------- 設定保存 ----------------

    def _restore_settings(self) -> None:
        saved_seconds = self.settings.value("min_seconds", 3, type=int)
        if saved_seconds < 3:
            saved_seconds = 3
        self.time_spin.setValue(saved_seconds)

        saved_folder = self.settings.value("last_folder", "", type=str)
        if saved_folder:
            folder_path = Path(saved_folder)
            if folder_path.exists() and folder_path.is_dir():
                self.load_folder(folder_path)
                self.status_label.setText(f"前回フォルダを復元: {folder_path}")

    def _save_settings(self) -> None:
        self.settings.setValue("min_seconds", self.time_spin.value())

        if self.gif_files and 0 <= self.current_index < len(self.gif_files):
            current_folder = str(self.gif_files[self.current_index].parent)
            self.settings.setValue("last_folder", current_folder)

    # ---------------- フォルダ ----------------

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "GIFフォルダを選択")
        if not folder:
            return
        self.load_folder(Path(folder))
        self.image_label.setFocus()

    def load_folder(self, folder: Path) -> None:
        gif_files = sorted(
            [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".gif"]
        )

        self.stop_playback()
        self._reset_preload_cache()
        self.gif_files = gif_files
        self.current_index = -1
        self.count_label.setText(f"GIF数: {len(self.gif_files)}")

        if not self.gif_files:
            self.file_label.setText("再生中: なし")
            self.image_label.setText("このフォルダにはGIFがありません")
            self.status_label.setText("GIFが見つかりません")
            return

        self.settings.setValue("last_folder", str(folder))
        self.status_label.setText(f"読み込み完了: {folder}")
        self.play_index(0)

    # ---------------- 再生 ----------------

    def play_index(self, index: int) -> None:
        if not self.gif_files:
            return

        actual_index = index % len(self.gif_files)
        self.stop_playback(clear_image=False)
        self.current_index = actual_index
        gif_path = self.gif_files[self.current_index]
        self.is_paused = False

        self.file_label.setText(f"再生中: {gif_path.name}")
        self.status_label.setText(
            f"{self.current_index + 1} / {len(self.gif_files)} を再生中"
        )

        loaded_from_preload = False

        if self.preloaded_index == self.current_index and self.preloaded_images:
            self.images = self.preloaded_images
            self.durations = self.preloaded_durations
            loaded_from_preload = True
        else:
            success, images, durations = self._load_gif_images_sync(gif_path)
            if not success:
                self.status_label.setText(f"壊れているGIFをスキップ: {gif_path.name}")
                self.play_next()
                return
            self.images = images
            self.durations = durations

        self.current_frame_index = 0
        self.elapsed_time_ms = 0
        self._show_current_frame()
        self._schedule_next_frame()

        if loaded_from_preload:
            self.status_label.setText(
                f"{self.current_index + 1} / {len(self.gif_files)} を再生中（先読み済み）"
            )

        self.preloaded_index = None
        self.preloaded_images = []
        self.preloaded_durations = []

        self._start_preload_for_next()
        self._save_settings()
        self.image_label.setFocus()

    def play_next(self) -> None:
        if not self.gif_files:
            return
        next_index = (self.current_index + 1) % len(self.gif_files)
        self.play_index(next_index)

    def play_previous(self) -> None:
        if not self.gif_files:
            return
        prev_index = (self.current_index - 1) % len(self.gif_files)
        self.play_index(prev_index)

    def toggle_pause(self) -> None:
        if not self.images:
            return

        if self.is_paused:
            self.is_paused = False
            self.status_label.setText(
                f"{self.current_index + 1} / {len(self.gif_files)} を再生中"
            )
            self._schedule_next_frame()
        else:
            self.is_paused = True
            self.timer.stop()
            self.status_label.setText("一時停止")

        self.image_label.setFocus()

    def toggle_fullscreen(self) -> None:
        if self.is_fullscreen:
            self.showNormal()
            self.is_fullscreen = False
        else:
            self.showFullScreen()
            self.is_fullscreen = True

        self.image_label.setFocus()

    def stop_playback(self, clear_image: bool = True) -> None:
        self.timer.stop()
        self.images = []
        self.durations = []
        self.current_frame_index = 0
        self.elapsed_time_ms = 0
        if clear_image:
            self.image_label.clear()

    # ---------------- 読み込み ----------------

    def _load_gif_images_sync(
        self, gif_path: Path
    ) -> tuple[bool, list[QImage], list[int]]:
        images: list[QImage] = []
        durations: list[int] = []

        try:
            with Image.open(gif_path) as img:
                for frame in ImageSequence.Iterator(img):
                    frame_rgba = frame.convert("RGBA")
                    data = frame_rgba.tobytes("raw", "RGBA")
                    qimage = QImage(
                        data,
                        frame_rgba.width,
                        frame_rgba.height,
                        frame_rgba.width * 4,
                        QImage.Format_RGBA8888,
                    ).copy()
                    images.append(qimage)

                    duration = frame.info.get("duration", 100)
                    if not isinstance(duration, int) or duration <= 0:
                        duration = 100
                    durations.append(duration)

            return len(images) > 0, images, durations
        except Exception:
            return False, [], []

    def _start_preload_for_next(self) -> None:
        if not self.gif_files:
            return

        next_index = (self.current_index + 1) % len(self.gif_files)

        if self.preloaded_index == next_index and self.preloaded_images:
            return

        self.preload_job_id += 1
        job_id = self.preload_job_id

        task = GifPreloadTask(job_id, next_index, self.gif_files[next_index])
        task.signals.finished.connect(self._on_preload_finished)
        self.thread_pool.start(task)

    def _on_preload_finished(
        self,
        job_id: int,
        index: int,
        images: list[QImage],
        durations: list[int],
        error_message: str,
    ) -> None:
        if job_id != self.preload_job_id:
            return

        if error_message or not images:
            return

        self.preloaded_index = index
        self.preloaded_images = images
        self.preloaded_durations = durations

    def _reset_preload_cache(self) -> None:
        self.preload_job_id += 1
        self.preloaded_index = None
        self.preloaded_images = []
        self.preloaded_durations = []

    # ---------------- 表示 ----------------

    def _show_current_frame(self) -> None:
        if not self.images:
            return

        original = self.images[self.current_frame_index]
        fitted = original.scaled(
            self.image_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.image_label.setPixmap(QPixmap.fromImage(fitted))

    def _schedule_next_frame(self) -> None:
        if not self.durations or self.is_paused:
            return
        duration = self.durations[self.current_frame_index]
        self.timer.start(duration)

    def _advance_frame(self) -> None:
        if self.is_paused or not self.images:
            return

        self.elapsed_time_ms += self.durations[self.current_frame_index]
        self.current_frame_index += 1

        if self.current_frame_index >= len(self.images):
            min_time_ms = self.time_spin.value() * 1000

            if self.elapsed_time_ms >= min_time_ms:
                self.play_next()
                return
            else:
                self.current_frame_index = 0

        self._show_current_frame()
        self._schedule_next_frame()

    # ---------------- UIイベント ----------------

    def _on_time_changed(self) -> None:
        self.elapsed_time_ms = 0
        self.settings.setValue("min_seconds", self.time_spin.value())
        self.image_label.setFocus()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._show_current_frame()

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.image_label.setFocus()

    def closeEvent(self, event) -> None:
        self._save_settings()
        self.stop_playback()
        self._reset_preload_cache()
        self.thread_pool.waitForDone()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    window = GifAlbumPlayer()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
