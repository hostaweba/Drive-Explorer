# ui/viewers.py

import random
import os
import sys

# --- PySide6 Imports ---
from PySide6.QtCore import Qt, QTimer, QUrl, QThread, Signal
from PySide6.QtGui import QWheelEvent, QPainter, QPixmap, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QPlainTextEdit, QDialog, QGraphicsView, QGraphicsScene, 
    QGraphicsPixmapItem, QSlider, QProgressBar
)

# --- PySide6 Multimedia ---
try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PySide6.QtMultimediaWidgets import QVideoWidget
    MULTIMEDIA_AVAILABLE = True
except ImportError:
    MULTIMEDIA_AVAILABLE = False

# --- Custom Modules ---
from utils import human_size  # Required if you keep _format_preview_html here

# -------------- Advanced Internal Viewer  ---------------

# Smart Category Definitions
IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".ico"}
VID_EXTS = {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm"}
AUD_EXTS = {".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a", ".wma"}
TXT_EXTS = {".txt", ".log", ".csv", ".py", ".json", ".xml", ".ini", ".md", ".html", ".css", ".js", ".c", ".cpp"}


class ImageLoader(QThread):
    finished = Signal(str, object)
    def __init__(self, path: str, max_size=(1920, 1080), parent=None): 
        super().__init__(parent)
        self.path = path
        self.max_size = max_size
        
    def run(self):
        try:
            pm = QPixmap(self.path)
            if not pm.isNull() and (pm.width() > self.max_size[0] or pm.height() > self.max_size[1]): 
                pm = pm.scaled(*self.max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.finished.emit(self.path, pm)
        except Exception: 
            self.finished.emit(self.path, QPixmap())

class ScaledImageLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background-color: #1e1e1e;")
        self._pixmap = None
        
    def setPixmap(self, pm): 
        self._pixmap = pm
        self.update()
        
    def clear(self): 
        self._pixmap = None
        self.update()
        
    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap and not self._pixmap.isNull():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            
            if self._pixmap.width() <= self.width() and self._pixmap.height() <= self.height():
                scaled = self._pixmap
            else:
                scaled = self._pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                
            painter.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)




class DedicatedImageViewer(QGraphicsView):
    """Pro Image Viewer with Perfect Fit, Zoom, Pan, Rotation, and Flip."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)
        
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setContentsMargins(0, 0, 0, 0)
        self.setStyleSheet("background-color: #121212; border: none;")
        self.zoom_factor = 1.0

    def load_file(self, filepath):
        pixmap = QPixmap(filepath)
        self.pixmap_item.setPixmap(pixmap)
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        QTimer.singleShot(10, self.reset_view)

    def reset_view(self):
        self.zoom_factor = 1.0
        self.resetTransform()
        self.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def zoom(self, factor):
        self.zoom_factor *= factor
        self.scale(factor, factor)

    def rotate_image(self):
        self.rotate(90)

    def flip_image(self, horizontal=True):
        if horizontal: self.scale(-1, 1)
        else: self.scale(1, -1)

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() == Qt.ControlModifier:
            if event.angleDelta().y() > 0: self.zoom(1.15)
            else: self.zoom(0.85)
        else:
            super().wheelEvent(event)

class DedicatedMediaViewer(QWidget):
    """Pro Media Player with Timeline, Seek, Volume, Mute, Loop, and Shuffle."""
    def __init__(self, is_video=True, parent_viewer=None, parent=None):
        super().__init__(parent)
        self.is_video = is_video
        self.parent_viewer = parent_viewer 
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        
        self.is_repeat = False
        self.is_shuffle = False
        self.is_muted = False
        
        if self.is_video:
            self.video_widget = QVideoWidget()
            self.player.setVideoOutput(self.video_widget)
            self.layout.addWidget(self.video_widget, stretch=1)
        else:
            self.audio_lbl = QLabel("🎵\nLoading Audio...")
            self.audio_lbl.setAlignment(Qt.AlignCenter)
            self.audio_lbl.setStyleSheet("background-color: #1a1a1a; color: #4da6ff; font-size: 28px; font-weight: bold; border-radius: 10px;")
            self.layout.addWidget(self.audio_lbl, stretch=1)

        self.controls_container = QWidget()
        self.controls_layout = QVBoxLayout(self.controls_container)
        self.controls_layout.setContentsMargins(5,5,5,5)

        time_layout = QHBoxLayout()
        self.lbl_current = QLabel("00:00")
        self.lbl_current.setStyleSheet("color: #d4d4d4; font-weight: bold;")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setStyleSheet("QSlider::handle:horizontal { background: #4da6ff; width: 14px; margin: -4px 0; border-radius: 7px; } QSlider::groove:horizontal { background: #444; height: 6px; border-radius: 3px; }")
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.set_position)
        self.lbl_total = QLabel("00:00")
        self.lbl_total.setStyleSheet("color: #d4d4d4; font-weight: bold;")
        
        time_layout.addWidget(self.lbl_current)
        time_layout.addWidget(self.slider)
        time_layout.addWidget(self.lbl_total)
        self.controls_layout.addLayout(time_layout)

        ctrl_layout = QHBoxLayout()
        self.btn_shuffle = QPushButton("🔀")
        self.btn_shuffle.setCheckable(True)
        self.btn_shuffle.setToolTip("Shuffle")
        self.btn_shuffle.clicked.connect(self.toggle_shuffle)
        
        self.btn_play = QPushButton("▶ Play")
        self.btn_play.setMinimumWidth(100)
        self.btn_play.clicked.connect(self.toggle_playback)
        
        self.btn_repeat = QPushButton("🔁")
        self.btn_repeat.setCheckable(True)
        self.btn_repeat.setToolTip("Repeat")
        self.btn_repeat.clicked.connect(self.toggle_repeat)

        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setToolTip("Mute")
        self.btn_mute.clicked.connect(self.toggle_mute)
        
        ctrl_layout.addWidget(self.btn_shuffle)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.btn_play)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.btn_mute)
        ctrl_layout.addWidget(self.btn_repeat)
        self.controls_layout.addLayout(ctrl_layout)
        self.layout.addWidget(self.controls_container)

        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(self.state_changed)
        self.player.mediaStatusChanged.connect(self.status_changed)

    def load_file(self, filepath, filename=""):
        if not self.is_video:
            self.audio_lbl.setText(f"🎵\n{filename}")
        self.player.setSource(QUrl.fromLocalFile(filepath))
        self.player.play()

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState: self.player.pause()
        else: self.player.play()

    def toggle_shuffle(self):
        self.is_shuffle = self.btn_shuffle.isChecked()
        self.btn_shuffle.setStyleSheet("background-color: #0e639c;" if self.is_shuffle else "")

    def toggle_repeat(self):
        self.is_repeat = self.btn_repeat.isChecked()
        self.btn_repeat.setStyleSheet("background-color: #0e639c;" if self.is_repeat else "")

    def toggle_mute(self):
        self.is_muted = not self.is_muted
        self.audio_output.setMuted(self.is_muted)
        self.btn_mute.setText("🔇" if self.is_muted else "🔊")
        self.btn_mute.setStyleSheet("background-color: #c0392b;" if self.is_muted else "")

    def seek(self, seconds):
        new_pos = max(0, min(self.player.position() + (seconds * 1000), self.player.duration()))
        self.player.setPosition(new_pos)

    def change_volume(self, delta):
        new_vol = max(0.0, min(1.0, self.audio_output.volume() + delta))
        self.audio_output.setVolume(new_vol)
        if self.is_muted: self.toggle_mute() # Unmute on volume change

    def set_position(self, position):
        self.player.setPosition(position)

    def position_changed(self, position):
        self.slider.setValue(position)
        self.lbl_current.setText(self.format_time(position))

    def duration_changed(self, duration):
        self.slider.setRange(0, duration)
        self.lbl_total.setText(self.format_time(duration))

    def state_changed(self, state):
        if state == QMediaPlayer.PlayingState: self.btn_play.setText("⏸ Pause")
        else: self.btn_play.setText("▶ Play")

    def status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.is_repeat:
                self.player.setPosition(0)
                self.player.play()
            elif self.parent_viewer:
                self.parent_viewer.next_file()

    def format_time(self, ms):
        s = ms // 1000
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h > 0: return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def clean_up(self):
        self.player.stop()

class DedicatedTextViewer(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 11))
        self.setStyleSheet("background-color: #1e1e1e; color: #dcdcaa; border: none; padding: 10px; selection-background-color: #264f78;")

    def load_file(self, filepath):
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(2 * 1024 * 1024) 
                if f.read(1): content += "\n\n... [FILE TRUNCATED] ..."
                self.setPlainText(content)
        except Exception as e:
            self.setPlainText(f"Failed to read file: {e}")

class InternalViewer(QDialog):
    def __init__(self, table_view, start_row: int, parent=None):
        super().__init__(parent)
        self.table_view = table_view
        self.model = table_view.model()
        
        self.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        
        self.setWindowTitle("Smart Dedicated Viewer")
        self.resize(1100, 850)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0,0,0,0)
        self.layout.setSpacing(0)
        self.setStyleSheet("QDialog { background-color: #121212; }")
        
        # Stylish Autoplay Progress Bar (Hidden by default)
        self.slideshow_bar = QProgressBar()
        self.slideshow_bar.setFixedHeight(4)
        self.slideshow_bar.setTextVisible(False)
        self.slideshow_bar.setStyleSheet("QProgressBar { background-color: #121212; border: none; } QProgressBar::chunk { background-color: #8e44ad; border-radius: 2px; }")
        self.slideshow_bar.setVisible(False)
        self.layout.addWidget(self.slideshow_bar)

        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0,0,0,0)
        self.layout.addWidget(self.content_widget, stretch=1)
        
        # Universal Navigation Bar
        self.nav_container = QWidget()
        self.nav_container.setStyleSheet("background-color: #252526; padding: 8px;")
        nav_layout = QHBoxLayout(self.nav_container)
        nav_layout.setContentsMargins(5,5,5,5)
        
        self.btn_close = QPushButton("✖ Close")
        self.btn_close.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold; border-radius: 4px; padding: 5px 15px;")
        self.btn_close.clicked.connect(self.close)
        
        self.btn_prev = QPushButton("◀ Prev")
        self.btn_next = QPushButton("Next ▶")
        
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet("color: #ecf0f1; font-weight: bold; font-size: 13px;")
        self.lbl_info.setAlignment(Qt.AlignCenter)
        
        self.btn_open_ext = QPushButton("↗ Open Externally")
        self.btn_open_ext.setStyleSheet("background-color: #2980b9; color: white; border-radius: 4px; padding: 5px 10px;")
        self.btn_open_ext.clicked.connect(self.open_current_externally)

        self.btn_prev.clicked.connect(self.prev_file)
        self.btn_next.clicked.connect(self.next_file)
        
        nav_layout.addWidget(self.btn_close)
        nav_layout.addWidget(self.btn_prev)
        nav_layout.addWidget(self.lbl_info, stretch=1)
        nav_layout.addWidget(self.btn_next)
        nav_layout.addWidget(self.btn_open_ext)
        self.layout.addWidget(self.nav_container)
        
        self.current_player = None
        self.active_category = None
        self.valid_rows = []
        self.current_valid_index = 0
        self.ui_hidden = False
        
        # Smooth Animated Slideshow Logic
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.setInterval(50) # 50ms smooth animation tick
        self.slideshow_timer.timeout.connect(self._animate_slideshow)
        self.is_slideshow = False
        self.slideshow_progress = 0
        self.slideshow_max = 70 # 70 ticks * 50ms = 3.5 seconds per slide
        self.slideshow_bar.setMaximum(self.slideshow_max)
        
        self._dynamic_shortcuts = []
        self.current_filepath = ""
        
        self._setup_global_shortcuts()
        self._setup_smart_playlist(start_row)
        self.load_file()

    def _setup_global_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key_F11), self, self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.handle_escape)
        QShortcut(QKeySequence(Qt.Key_H), self, self.toggle_ui)
        QShortcut(QKeySequence(Qt.Key_B), self, self.hide) 
        QShortcut(QKeySequence(Qt.Key_PageDown), self, self.next_file)
        QShortcut(QKeySequence(Qt.Key_PageUp), self, self.prev_file)

    def _apply_dynamic_shortcuts(self):
        for sc in self._dynamic_shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._dynamic_shortcuts.clear()

        if isinstance(self.current_player, DedicatedImageViewer):
            self._dynamic_shortcuts.extend([
                QShortcut(QKeySequence(Qt.Key_Right), self, self.next_file),
                QShortcut(QKeySequence(Qt.Key_Left), self, self.prev_file),
                QShortcut(QKeySequence(Qt.Key_Plus), self, lambda: self.current_player.zoom(1.15)),
                QShortcut(QKeySequence(Qt.Key_Equal), self, lambda: self.current_player.zoom(1.15)),
                QShortcut(QKeySequence(Qt.Key_Minus), self, lambda: self.current_player.zoom(0.85)),
                QShortcut(QKeySequence(Qt.Key_0), self, self.current_player.reset_view),
                QShortcut(QKeySequence(Qt.Key_R), self, self.current_player.rotate_image),
                QShortcut(QKeySequence(Qt.Key_F), self, lambda: self.current_player.flip_image(True)),
                QShortcut(QKeySequence("Shift+F"), self, lambda: self.current_player.flip_image(False)),
                QShortcut(QKeySequence(Qt.Key_S), self, self.toggle_slideshow)
            ])
        elif isinstance(self.current_player, DedicatedMediaViewer):
            self._dynamic_shortcuts.extend([
                QShortcut(QKeySequence(Qt.Key_Right), self, self.next_file),
                QShortcut(QKeySequence(Qt.Key_Left), self, self.prev_file),
                QShortcut(QKeySequence("Shift+Right"), self, lambda: self.current_player.seek(5)), 
                QShortcut(QKeySequence("Shift+Left"), self, lambda: self.current_player.seek(-5)),
                QShortcut(QKeySequence(Qt.Key_Up), self, lambda: self.current_player.change_volume(0.1)),
                QShortcut(QKeySequence(Qt.Key_Down), self, lambda: self.current_player.change_volume(-0.1)),
                QShortcut(QKeySequence(Qt.Key_M), self, self.current_player.toggle_mute),
                QShortcut(QKeySequence(Qt.Key_Space), self, self.current_player.toggle_playback)
            ])

    def toggle_slideshow(self):
        self.is_slideshow = not self.is_slideshow
        self.slideshow_bar.setVisible(self.is_slideshow)
        if self.is_slideshow:
            self.slideshow_progress = 0
            self.slideshow_timer.start()
        else:
            self.slideshow_timer.stop()

    def _animate_slideshow(self):
        """Smoothly animates the progress bar and changes image when full"""
        self.slideshow_progress += 1
        self.slideshow_bar.setValue(self.slideshow_progress)
        if self.slideshow_progress >= self.slideshow_max:
            self.slideshow_progress = 0
            self.next_file()

    def open_current_externally(self):
        if self.current_filepath and os.path.exists(self.current_filepath):
            try:
                if sys.platform=="win32": os.startfile(self.current_filepath)
                elif sys.platform=="darwin": os.system(f"open '{self.current_filepath}'")
                else: os.system(f"xdg-open '{self.current_filepath}'")
            except Exception: pass

    def _setup_smart_playlist(self, start_row):
        start_data = self.model.filtered_rows[start_row]
        start_ext = start_data.get("ext_meta", "").lower()
        if not start_ext and start_data.get("display"):
            start_ext = os.path.splitext(str(start_data["display"][1]))[1].lower()

        if start_ext in IMG_EXTS: self.active_category = IMG_EXTS
        elif start_ext in VID_EXTS: self.active_category = VID_EXTS
        elif start_ext in AUD_EXTS: self.active_category = AUD_EXTS
        elif start_ext in TXT_EXTS: self.active_category = TXT_EXTS
        else: self.active_category = None

        for idx, row in enumerate(self.model.filtered_rows):
            if row.get("is_folder_meta", False): continue
            ext = row.get("ext_meta", "").lower()
            if self.active_category is None or ext in self.active_category:
                self.valid_rows.append(idx)

        try: self.current_valid_index = self.valid_rows.index(start_row)
        except ValueError: self.current_valid_index = 0

    def next_file(self):
        self.slideshow_progress = 0 # Reset animation on manual next
        if isinstance(self.current_player, DedicatedMediaViewer) and self.current_player.is_shuffle:
            if len(self.valid_rows) > 1:
                new_idx = self.current_valid_index
                while new_idx == self.current_valid_index:
                    new_idx = random.randint(0, len(self.valid_rows) - 1)
                self.current_valid_index = new_idx
                self.load_file()
            return
            
        if self.current_valid_index < len(self.valid_rows) - 1: self.current_valid_index += 1
        else: self.current_valid_index = 0
        self.load_file()

    def prev_file(self):
        self.slideshow_progress = 0 # Reset animation on manual prev
        if self.current_valid_index > 0: self.current_valid_index -= 1
        else: self.current_valid_index = len(self.valid_rows) - 1
        self.load_file()

    def toggle_fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    def handle_escape(self):
        if self.isFullScreen(): self.showNormal()
        else: self.close()

    def toggle_ui(self):
        self.ui_hidden = not self.ui_hidden
        self.nav_container.setVisible(not self.ui_hidden)
        if isinstance(self.current_player, DedicatedMediaViewer):
            self.current_player.controls_container.setVisible(not self.ui_hidden)

    def load_file(self):
        if isinstance(self.current_player, DedicatedMediaViewer):
            self.current_player.clean_up()
            
        while self.content_layout.count():
            child = self.content_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()
            
        self.current_player = None
            
        table_row = self.valid_rows[self.current_valid_index]
        self.table_view.selectRow(table_row)
        row_data = self.model.filtered_rows[table_row]
        
        self.current_filepath = row_data.get("user_data_1", "")
        filename = str(row_data.get("display", [])[1]) if len(row_data.get("display", [])) > 1 else "Unknown"
        ext = row_data.get("ext_meta", "").lower()
        
        # Smart Compact Resizing for Audio
        if self.active_category == AUD_EXTS and not self.isFullScreen():
            self.setMinimumSize(450, 250)
            self.resize(450, 250)
        elif not self.isFullScreen():
            self.setMinimumSize(800, 600)
            
        cat_name = "Files"
        if self.active_category == IMG_EXTS: cat_name = "Images"
        elif self.active_category == VID_EXTS: cat_name = "Videos"
        elif self.active_category == AUD_EXTS: cat_name = "Audio"
        elif self.active_category == TXT_EXTS: cat_name = "Documents"
        
        info_text = f"{cat_name}: {self.current_valid_index + 1} / {len(self.valid_rows)} | {filename}"
        self.lbl_info.setText(info_text)
        self.setWindowTitle(f"{cat_name} Viewer - {filename}")
        
        if not self.current_filepath or not os.path.exists(self.current_filepath):
            lbl = QLabel(f"Cannot preview '{filename}'.\nItem is missing from physical disk.")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color: #d4d4d4; font-size: 16px;")
            self.content_layout.addWidget(lbl)
            return

        if ext in IMG_EXTS:
            self.current_player = DedicatedImageViewer()
            self.content_layout.addWidget(self.current_player)
            self.current_player.load_file(self.current_filepath)
        elif ext in TXT_EXTS:
            self.current_player = DedicatedTextViewer()
            self.content_layout.addWidget(self.current_player)
            self.current_player.load_file(self.current_filepath)
            # CRITICAL FIX: Give focus back to Text Editor so Arrow Keys work immediately
            self.current_player.setFocus()
        elif ext in VID_EXTS and MULTIMEDIA_AVAILABLE:
            self.current_player = DedicatedMediaViewer(is_video=True, parent_viewer=self)
            self.content_layout.addWidget(self.current_player)
            self.current_player.load_file(self.current_filepath, filename)
        elif ext in AUD_EXTS and MULTIMEDIA_AVAILABLE:
            self.current_player = DedicatedMediaViewer(is_video=False, parent_viewer=self)
            self.content_layout.addWidget(self.current_player)
            self.current_player.load_file(self.current_filepath, filename)
        else:
            lbl = QLabel(f"No dedicated player for {ext} files.\nPress 'Esc' and click 'Open Externally'.")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet("color: #d4d4d4; font-size: 14px;")
            self.content_layout.addWidget(lbl)
            return
            
        if self.ui_hidden and isinstance(self.current_player, DedicatedMediaViewer):
            self.current_player.controls_container.setVisible(False)

        self._apply_dynamic_shortcuts()

    def closeEvent(self, event):
        if isinstance(self.current_player, DedicatedMediaViewer):
            self.current_player.clean_up()
        if self.slideshow_timer.isActive():
            self.slideshow_timer.stop()
        if self in self.parent().active_viewers:
            self.parent().active_viewers.remove(self)
        super().closeEvent(event)





