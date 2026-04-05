import json
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
    QListWidget,
    QListWidgetItem,
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
    def __init__(self, job_id: int, playlist_index: int, gif_path: Path) -> None:
        super().__init__()
        self.job_id = job_id
        self.playlist_index = playlist_index
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

            self.signals.finished.emit(self.job_id, self.playlist_index, images, durations, "")
        except Exception as e:
            self.signals.finished.emit(self.job_id, self.playlist_index, [], [], str(e))


class GifAlbumPlayer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.settings = QSettings("akhiLow", "GifAlbumPlayer")

        self.setWindowTitle("GIF Album Player")
        self.resize(900, 900)

        self.folder_paths: list[Path] = []
        self.playlist: list[dict[str, Path]] = []
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

        root = QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        left_panel = QVBoxLayout()
        left_panel.setSpacing(10)
        root.addLayout(left_panel, 0)

        folder_buttons = QHBoxLayout()
        left_panel.addLayout(folder_buttons)

        self.add_folder_button = QPushButton("フォルダ追加")
        self.add_folder_button.clicked.connect(self.add_folder)
        self.add_folder_button.setAutoDefault(False)
        self.add_folder_button.setDefault(False)
        self.add_folder_button.setFocusPolicy(Qt.NoFocus)
        folder_buttons.addWidget(self.add_folder_button)

        self.remove_folder_button = QPushButton("選択削除")
        self.remove_folder_button.clicked.connect(self.remove_selected_folder)
        self.remove_folder_button.setAutoDefault(False)
        self.remove_folder_button.setDefault(False)
        self.remove_folder_button.setFocusPolicy(Qt.NoFocus)
        folder_buttons.addWidget(self.remove_folder_button)

        self.folder_list = QListWidget()
        self.folder_list.setMinimumWidth(260)
        self.folder_list.setFocusPolicy(Qt.ClickFocus)
        self.folder_list.itemDoubleClicked.connect(self.jump_to_folder)
        left_panel.addWidget(self.folder_list, 1)

        self.folder_hint_label = QLabel("ダブルクリックでそのフォルダ先頭へ移動")
        left_panel.addWidget(self.folder_hint_label)

        right_panel = QVBoxLayout()
        right_panel.setSpacing(10)
        root.addLayout(right_panel, 1)

        controls = QHBoxLayout()
        right_panel.addLayout(controls)

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

        self.folder_label = QLabel("現在フォルダ: なし")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right_panel.addWidget(self.folder_label)

        self.file_label = QLabel("再生中GIF: なし")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right_panel.addWidget(self.file_label)

        self.image_label = QLabel("GIFフォルダを追加してください")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet(
            "background-color: black; color: white; border: 1px solid #444;"
        )
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image_label.setMinimumSize(200, 200)
        self.image_label.setFocusPolicy(Qt.StrongFocus)
        right_panel.addWidget(self.image_label, 1)

        self.help_label = QLabel("Space: 一時停止  ←/→: 前後移動  F: 全画面")
        right_panel.addWidget(self.help_label)

        self.status_label = QLabel("待機中")
        right_panel.addWidget(self.status_label)

        self.setFocusPolicy(Qt.StrongFocus)
        self.centralWidget().setFocusPolicy(Qt.StrongFocus)
        self.image_label.setFocus()

    def _build_actions(self) -> None:
        self.add_folder_action = QAction("フォルダ追加", self)
        self.add_folder_action.triggered.connect(self.add_folder)
        self.add_folder_action.setShortcut("Ctrl+O")
        self.add_folder_action.setShortcutContext(Qt.WindowShortcut)
        self.addAction(self.add_folder_action)

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

        saved_folders_raw = self.settings.value("folder_paths", "[]", type=str)
        try:
            saved_folders = json.loads(saved_folders_raw)
        except Exception:
            saved_folders = []

        restored_any = False
        for folder_str in saved_folders:
            folder_path = Path(folder_str)
            if folder_path.exists() and folder_path.is_dir():
                added = self._add_folder_path(folder_path, rebuild=False)
                restored_any = restored_any or added

        self._rebuild_playlist()
        self._refresh_folder_list()

        if restored_any and self.playlist:
            self.play_index(0)
            self.status_label.setText("前回フォルダ一覧を復元しました")

    def _save_settings(self) -> None:
        self.settings.setValue("min_seconds", self.time_spin.value())
        folder_strings = [str(path) for path in self.folder_paths]
        self.settings.setValue("folder_paths", json.dumps(folder_strings, ensure_ascii=False))

    # ---------------- フォルダ管理 ----------------

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "GIFフォルダを選択")
        if not folder:
            return

        folder_path = Path(folder)
        added = self._add_folder_path(folder_path, rebuild=True)
        if added:
            self.status_label.setText(f"フォルダ追加: {folder_path.name}")
        self.image_label.setFocus()

    def _add_folder_path(self, folder_path: Path, rebuild: bool = True) -> bool:
        if folder_path in self.folder_paths:
            self.status_label.setText(f"すでに登録済み: {folder_path.name}")
            return False

        gif_files = self._get_gif_files_in_folder(folder_path)
        if not gif_files:
            self.status_label.setText(f"GIFが見つからないため追加しません: {folder_path.name}")
            return False

        self.folder_paths.append(folder_path)

        if rebuild:
            self._rebuild_playlist()
            self._refresh_folder_list()
            if self.current_index == -1 and self.playlist:
                self.play_index(0)
            self._save_settings()
        return True

    def remove_selected_folder(self) -> None:
        row = self.folder_list.currentRow()
        if row < 0 or row >= len(self.folder_paths):
            self.status_label.setText("削除するフォルダを選択してください")
            return

        folder_to_remove = self.folder_paths[row]
        was_current_folder = False
        current_gif_path: Path | None = None

        if 0 <= self.current_index < len(self.playlist):
            current_entry = self.playlist[self.current_index]
            current_gif_path = current_entry["gif"]
            was_current_folder = current_entry["folder"] == folder_to_remove

        self.folder_paths.pop(row)
        self._reset_preload_cache()
        self._rebuild_playlist()
        self._refresh_folder_list()
        self._save_settings()

        if not self.playlist:
            self.stop_playback()
            self.current_index = -1
            self.folder_label.setText("現在フォルダ: なし")
            self.file_label.setText("再生中GIF: なし")
            self.count_label.setText("GIF数: 0")
            self.image_label.setText("GIFフォルダを追加してください")
            self.status_label.setText("登録フォルダがありません")
            return

        if was_current_folder:
            target_index = row % len(self.playlist)
            self.play_index(target_index)
        else:
            new_index = self._find_playlist_index_by_gif_path(current_gif_path)
            if new_index is not None:
                self.current_index = new_index
                self._update_info_labels()
                self._highlight_current_folder()
            else:
                self.play_index(0)

        self.status_label.setText(f"フォルダ削除: {folder_to_remove.name}")
        self.image_label.setFocus()

    def jump_to_folder(self, item: QListWidgetItem) -> None:
        row = self.folder_list.row(item)
        if row < 0 or row >= len(self.folder_paths):
            return

        folder_path = self.folder_paths[row]
        target_index = self._find_first_index_for_folder(folder_path)
        if target_index is not None:
            self.play_index(target_index)

    def _get_gif_files_in_folder(self, folder_path: Path) -> list[Path]:
        try:
            return sorted(
                [p for p in folder_path.iterdir() if p.is_file() and p.suffix.lower() == ".gif"]
            )
        except Exception:
            return []

    def _rebuild_playlist(self) -> None:
        old_current_gif: Path | None = None
        if 0 <= self.current_index < len(self.playlist):
            old_current_gif = self.playlist[self.current_index]["gif"]

        new_playlist: list[dict[str, Path]] = []
        valid_folders: list[Path] = []

        for folder_path in self.folder_paths:
            gif_files = self._get_gif_files_in_folder(folder_path)
            if not gif_files:
                continue

            valid_folders.append(folder_path)
            for gif_path in gif_files:
                new_playlist.append({"folder": folder_path, "gif": gif_path})

        self.folder_paths = valid_folders
        self.playlist = new_playlist
        self.count_label.setText(f"GIF数: {len(self.playlist)}")

        if not self.playlist:
            self.current_index = -1
            return

        if old_current_gif is not None:
            new_index = self._find_playlist_index_by_gif_path(old_current_gif)
            self.current_index = new_index if new_index is not None else min(self.current_index, len(self.playlist) - 1)
        elif self.current_index == -1:
            self.current_index = 0
        else:
            self.current_index = min(self.current_index, len(self.playlist) - 1)

    def _refresh_folder_list(self) -> None:
        self.folder_list.blockSignals(True)
        self.folder_list.clear()

        for folder_path in self.folder_paths:
            item = QListWidgetItem(folder_path.name)
            item.setToolTip(str(folder_path))
            self.folder_list.addItem(item)

        self.folder_list.blockSignals(False)
        self._highlight_current_folder()

    def _highlight_current_folder(self) -> None:
        if not self.playlist or not (0 <= self.current_index < len(self.playlist)):
            self.folder_list.clearSelection()
            return

        current_folder = self.playlist[self.current_index]["folder"]
        try:
            row = self.folder_paths.index(current_folder)
        except ValueError:
            self.folder_list.clearSelection()
            return

        self.folder_list.setCurrentRow(row)

    def _find_playlist_index_by_gif_path(self, gif_path: Path | None) -> int | None:
        if gif_path is None:
            return None
        for i, entry in enumerate(self.playlist):
            if entry["gif"] == gif_path:
                return i
        return None

    def _find_first_index_for_folder(self, folder_path: Path) -> int | None:
        for i, entry in enumerate(self.playlist):
            if entry["folder"] == folder_path:
                return i
        return None

    # ---------------- 再生 ----------------

    def play_index(self, index: int) -> None:
        if not self.playlist:
            return

        actual_index = index % len(self.playlist)
        self.stop_playback(clear_image=False)
        self.current_index = actual_index
        self.is_paused = False

        entry = self.playlist[self.current_index]
        folder_path = entry["folder"]
        gif_path = entry["gif"]

        self.folder_label.setText(f"現在フォルダ: {folder_path.name}")
        self.file_label.setText(f"再生中GIF: {gif_path.name}")
        self.status_label.setText(
            f"{self.current_index + 1} / {len(self.playlist)} を再生中"
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
                f"{self.current_index + 1} / {len(self.playlist)} を再生中（先読み済み）"
            )

        self.preloaded_index = None
        self.preloaded_images = []
        self.preloaded_durations = []

        self._start_preload_for_next()
        self._highlight_current_folder()
        self._save_settings()
        self.image_label.setFocus()

    def _update_info_labels(self) -> None:
        if not self.playlist or not (0 <= self.current_index < len(self.playlist)):
            self.folder_label.setText("現在フォルダ: なし")
            self.file_label.setText("再生中GIF: なし")
            return

        entry = self.playlist[self.current_index]
        self.folder_label.setText(f"現在フォルダ: {entry['folder'].name}")
        self.file_label.setText(f"再生中GIF: {entry['gif'].name}")

    def play_next(self) -> None:
        if not self.playlist:
            return
        next_index = (self.current_index + 1) % len(self.playlist)
        self.play_index(next_index)

    def play_previous(self) -> None:
        if not self.playlist:
            return
        prev_index = (self.current_index - 1) % len(self.playlist)
        self.play_index(prev_index)

    def toggle_pause(self) -> None:
        if not self.images:
            return

        if self.is_paused:
            self.is_paused = False
            self.status_label.setText(
                f"{self.current_index + 1} / {len(self.playlist)} を再生中"
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
        if not self.playlist:
            return

        next_index = (self.current_index + 1) % len(self.playlist)

        if self.preloaded_index == next_index and self.preloaded_images:
            return

        self.preload_job_id += 1
        job_id = self.preload_job_id
        gif_path = self.playlist[next_index]["gif"]

        task = GifPreloadTask(job_id, next_index, gif_path)
        task.signals.finished.connect(self._on_preload_finished)
        self.thread_pool.start(task)

    def _on_preload_finished(
        self,
        job_id: int,
        playlist_index: int,
        images: list[QImage],
        durations: list[int],
        error_message: str,
    ) -> None:
        if job_id != self.preload_job_id:
            return

        if error_message or not images:
            return

        self.preloaded_index = playlist_index
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
