# ui/main_window.py

import os
import sys
import shutil
import csv
import traceback
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

# --- PySide6 Core & GUI Imports ---
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QModelIndex, QDate, QItemSelection, QUrl
from PySide6.QtGui import QFont, QPixmap, QAction, QPainter, QIcon, QDragEnterEvent, QDropEvent, QShortcut, QKeySequence, QTextCharFormat, QColor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QToolBar, QFileDialog, QMessageBox,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QListWidget, QListWidgetItem,
    QPushButton, QInputDialog, QProgressDialog, QSplitter,
    QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QTextEdit, QLineEdit, QComboBox,
    QTableView, QHeaderView, QMenu, QAbstractItemView, QStatusBar,
    QTableWidget, QTableWidgetItem, QStyle, QGridLayout, QDateEdit,
    QSizePolicy, QCheckBox, QDialog, QFormLayout, QScrollArea,
    QCalendarWidget, QTextBrowser, QStackedWidget, QDialogButtonBox
)

# --- Icon Provider Handling ---
try:
    from PySide6.QtWidgets import QFileIconProvider
    HAS_ICON_PROVIDER = True
except ImportError:
    try:
        from PySide6.QtGui import QAbstractFileIconProvider as QFileIconProvider
        HAS_ICON_PROVIDER = True
    except ImportError:
        HAS_ICON_PROVIDER = False

# --- Data & Charting Imports ---
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except Exception:
    PANDAS_AVAILABLE = False

try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    MATPLOTLIB_AVAILABLE = True
except Exception:
    MATPLOTLIB_AVAILABLE = False

# --- Your Custom Modules ---
from config import *
from utils import *
from database.db_manager import CatalogDB
from workers.threads import CopyThread, SearchThread, ScanThread, ImportThread, ChartWorker, CompareThread
from ui.tables import FastTableModel, ActionTableView, SandboxTableView, SmartTableItem
from ui.dialogs import ConflictDialog
from ui.viewers import InternalViewer, ScaledImageLabel, ImageLoader
from workers.threads import DeleteDriveThread 

class ImportDetailsDialog(QDialog):
    def __init__(self, default_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Drive Data")
        self.setMinimumWidth(400)
        layout = QFormLayout(self)
        
        self.name_edit = QLineEdit(default_name)
        self.date_edit = QLineEdit()
        self.date_edit.setPlaceholderText("MM/YYYY or YYYY-MM-DD (Optional)")
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("Add a comment... (Optional)")
        
        layout.addRow("Drive Name:", self.name_edit)
        layout.addRow("Purchase Date:", self.date_edit)
        layout.addRow("Comment:", self.comment_edit)
        
        from PySide6.QtWidgets import QDialogButtonBox
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        
    def get_data(self):
        return self.name_edit.text().strip(), self.date_edit.text().strip(), self.comment_edit.text().strip()


class ExtFilterDialog(QDialog):
    def __init__(self, extensions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filter Extensions")
        self.setMinimumWidth(300)
        self.layout = QVBoxLayout(self)
        self.layout.addWidget(QLabel("Uncheck extensions you don't want to see:"))
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        w = QWidget()
        self.vbox = QVBoxLayout(w)
        self.checkboxes = {}
        for ext in sorted(extensions):
            if not ext: ext = "[No Extension]"
            chk = QCheckBox(ext)
            chk.setChecked(True)
            self.checkboxes[ext] = chk
            self.vbox.addWidget(chk)
        self.vbox.addStretch()
        scroll.setWidget(w)
        self.layout.addWidget(scroll)
        
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        self.layout.addWidget(btns)
        
    def get_excluded(self):
        return [ext for ext, chk in self.checkboxes.items() if not chk.isChecked()]

# ---------------- Main window ----------------
class DriveExplorerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_dirs()
        self.setWindowTitle(APP_TITLE)
        self.resize(1600, 1000)
        self.setFont(QFont("Segoe UI", 10))
        self.setWindowIcon(QIcon("icons/WhereFiles.png"))

        self.db = CatalogDB(DB_FILE)
        self._workers: List[QThread] = []
        self.search_thread = None
        self.search_dlg = None
        self.last_compare_result = None
        self.chart_thread = None
        self.is_dark_mode = False
        self.global_drive_filter = []
        
        self.current_explorer_prefix = ""
        self.current_myspace_prefix = "/"
        self.current_report_path = ""
        
        self.sb_clip_mode = ""
        self.sb_clip_ids = set()
        self.sb_clip_items = []
        
        self._icon_cache = {}
        self.folder_icon = self.style().standardIcon(QStyle.SP_DirIcon)
        self.file_icon = self.style().standardIcon(QStyle.SP_FileIcon)
        self._load_custom_icons()

        if HAS_ICON_PROVIDER: 
            self.icon_provider = QFileIconProvider()
        else: 
            self.icon_provider = None

        self._build_ui()
        self.toggle_theme() 
        self._setup_shortcuts()
        
        self.tabs.currentChanged.connect(self.on_tab_changed)
        QTimer.singleShot(150, self.refresh_all)
        


# ---------- Fast Explorer Logic ----------
    def fast_navigate_up(self):
        curr = self.current_fast_prefix.strip("/")
        if not curr: return
        parts = curr.split("/")
        new_prefix = "/".join(parts[:-1]) + "/" if len(parts) > 1 else ""
        self.load_fast_directory(new_prefix)

    def clear_fast_search(self):
        self.fast_search.clear()
        self.load_fast_directory(self.current_fast_prefix)

    def fast_filter_changed(self, text):
        model = self.fast_table.model()
        if not model: return
        model.set_filter(text)

    def load_fast_directory(self, prefix: str):
        self.current_fast_prefix = prefix
        self.fast_address_bar.setText(prefix if prefix else "/")
        
        self.fast_table.setUpdatesEnabled(False)
        self.fast_table.setSortingEnabled(False)
        
        cur = self.db.conn.cursor()
        target_drive = self.fast_drive.currentText()
        drive_filter_sql = "" if target_drive == "Any Drive" else f"AND drive = '{target_drive}'"
        
        # Super fast distinct name grab - completely bypasses SUM/COUNT math
        if prefix:
            plen = len(prefix) + 1
            query = f"SELECT DISTINCT SUBSTR(relpath, {plen}, INSTR(SUBSTR(relpath, {plen}), '/') - 1) FROM files WHERE relpath LIKE ? AND INSTR(SUBSTR(relpath, {plen}), '/') > 0 {drive_filter_sql}"
            cur.execute(query, (f"{prefix}%",))
        else:
            query = f"SELECT DISTINCT SUBSTR(relpath, 1, INSTR(relpath, '/') - 1) FROM files WHERE INSTR(relpath, '/') > 0 {drive_filter_sql}"
            cur.execute(query)
            
        folders = [r[0] for r in cur.fetchall() if r[0]]
        
        if not prefix: 
            cur.execute(f"SELECT relpath, name, size, extension, modified, drive, fullpath FROM files WHERE INSTR(relpath, '/') = 0 AND is_folder = 0 {drive_filter_sql} LIMIT {MAX_RENDER_ROWS}")
        else:
            plen = len(prefix) + 1
            cur.execute(f"SELECT relpath, name, size, extension, modified, drive, fullpath FROM files WHERE relpath LIKE ? AND INSTR(SUBSTR(relpath, {plen}), '/') = 0 AND is_folder = 0 {drive_filter_sql} LIMIT {MAX_RENDER_ROWS}", (f"{prefix}%",))
        files = cur.fetchall()

        rows = []
        row_idx = 1
        
        for f_name in sorted(folders):
            folder_rel = f"{prefix}{f_name}/"
            rows.append({
                "display": [str(row_idx), f_name, "-", "-", "File folder", "", "-"],
                "sort_keys": [row_idx, (0, f_name.lower()), (0, 0), (0, 0), (0, "file folder"), (0, ""), (0, 0)],
                "user_data": ("folder", folder_rel), "user_data_1": "", "user_data_2": True, "ext_meta": "", "is_folder_meta": True
            })
            row_idx += 1
            
        for rp, name, size, ext, mod, drive, fp in files:
            ext_str = ext or "file"
            rows.append({
                "display": [str(row_idx), name, "-", human_size(size), ext_str, mod, "-"],
                "sort_keys": [row_idx, (1, name.lower()), (1, -1), (1, size), (1, ext_str.lower()), (1, mod), (1, 0)],
                "user_data": ("file", rp), "user_data_1": fp, "user_data_2": False, "ext_meta": ext_str, "is_folder_meta": False
            })
            row_idx += 1
            
        headers = ["S.No", "Name", "Total Items", "Total Size", "Type", "Modified", "Global Copies"]
        model = FastTableModel(headers, rows, self._get_icon)
        self.fast_table.setModel(model)
        
        cw = self.fast_table.setColumnWidth
        cw(0, 50); cw(1, 400); cw(2, 90); cw(3, 90)
        cw(4, 90); cw(5, 140); cw(6, 100)
        self.fast_table.setSortingEnabled(True)
        self.fast_table.setUpdatesEnabled(True)

    def on_fast_table_double_click(self, index: QModelIndex):
        model = self.fast_table.model()
        if not model: return
        data = model.data(model.index(index.row(), 1), Qt.UserRole)
        if not data: return
        typ, path = data
        if typ == "folder": 
            self.load_fast_directory(path)
        else: 
            self.open_local_file("", self.fast_table, index.row())

    def on_tab_changed(self, index):
            if self.tabs.tabText(index) == "⭐ MySpace Sandbox":
                self.load_myspace_directory(self.current_myspace_prefix)
                self.refresh_myspace_tree()
            elif self.tabs.tabText(index) == "⚡ Fast Explorer":
                self.load_fast_directory(self.current_fast_prefix)            
            elif self.tabs.tabText(index) == "📅 Timeline Diary":
                self.refresh_diary_data()

    def _load_custom_icons(self):
        if ICONS_DIR.exists():
            for f in ICONS_DIR.iterdir():
                if f.is_file() and f.suffix.lower() in ('.png', '.ico', '.jpg', '.jpeg'):
                    ext_name = f.stem.lower()
                    self._icon_cache[ext_name] = QIcon(str(f))

    def _get_icon(self, ext: str, is_folder: bool) -> QIcon:
        if is_folder: 
            return self.folder_icon
        if not ext: 
            return self.file_icon
        ext = ext.lower().strip('.')
        if ext not in self._icon_cache:
            self._icon_cache[ext] = self.file_icon 
        return self._icon_cache[ext]

    def _format_preview_html(self, model_data: list, headers: list, title: str, path: str, rows: list) -> str:
        html = f"""
        <div style='font-family: Segoe UI, sans-serif;'>
            <h3 style='color: #4da6ff; margin-top: 0;'>{title}</h3>
            <table style='width: 100%; border-collapse: collapse; margin-bottom: 15px;'>
                <tr><td style='padding: 3px; font-weight: bold; width: 110px; color: #aaa;'>Relative Path:</td><td style='padding: 3px;'>{path}</td></tr>
        """
        
        # Map headers dynamically (skipping S.No and Name, which are index 0 and 1)
        for i, val in enumerate(model_data[2:]):
            header_idx = i + 2
            header_name = headers[header_idx] if header_idx < len(headers) else f"Detail {i+1}"
            
            # Hide empty data rows to keep the UI clean
            if val and str(val).strip() != "-":
                html += f"<tr><td style='padding: 3px; font-weight: bold; color: #aaa;'>{header_name}:</td><td style='padding: 3px;'>{val}</td></tr>"
                
        html += f"""
            </table>
            <h4 style='color: #e67e22; border-bottom: 1px solid #555; padding-bottom: 5px; margin-bottom: 5px;'>🌍 Global Presence ({len(rows)} Locations)</h4>
            <ul style='margin-top: 5px; padding-left: 20px; list-style-type: square;'>
        """
        
        if not rows:
            html += "<li><span style='color: #aaa;'>No other locations found.</span></li>"
        else:
            for d, full, rel, size, sha in rows:
                # Prioritize showing the physical full path if connected, otherwise show the relative path
                display_path = full if full else rel
                html += f"<li style='margin-bottom: 6px;'><b>[{d}]</b> {display_path}<br><span style='color: #888; font-size: 11px;'>Size: {human_size(size)} | SHA: {sha[:12] if sha else 'None'}</span></li>"
                
        html += "</ul></div>"
        return html

    # ---------- Core UI Setup ----------
    def _build_ui(self):
        tb = QToolBar("Main Toolbar")
        self.addToolBar(tb)
        
        act_scan = QAction("Scan New Folder", self)
        act_scan.triggered.connect(self.scan_folder)
        act_import = QAction("Import CSV", self)
        act_import.triggered.connect(self.import_csvs)
        act_compare = QAction("Compare Drives", self)
        act_compare.triggered.connect(self.compare_selected)
        act_refresh = QAction("Refresh All Data", self)
        act_refresh.triggered.connect(self.refresh_all)
        act_theme = QAction("🌙 Theme", self)
        act_theme.triggered.connect(self.toggle_theme)
        act_help = QAction("❓ Help", self)
        act_help.triggered.connect(self.show_help)
        
        tb.addAction(act_scan)
        tb.addAction(act_import)
        tb.addAction(act_compare)
        tb.addAction(act_refresh)
        
        empty = QWidget()
        empty.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(empty)
        tb.addAction(act_help)
        tb.addAction(act_theme)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # 1. Drives Dashboard
        drives_tab = QWidget()
        dv = QVBoxLayout(drives_tab)
        dv.addWidget(QLabel("<b>Drives Dashboard</b>"))
        
        self.drives_table = QTableWidget()
        self.drives_table.setColumnCount(7) 
        self.drives_table.setHorizontalHeaderLabels(["Use", "Drive Name", "Files", "Total Size", "Age", "Scanned Date", "Comment"])
        self.drives_table.cellChanged.connect(self.on_drive_comment_changed) # Connect for editable comments
        self.drives_table.verticalHeader().setVisible(False)
        self.drives_table.horizontalHeader().setStretchLastSection(True)
        self.drives_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.drives_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        # Allow editing ONLY by double clicking or pressing F2/Enter
        self.drives_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        
        self.drives_table.setAlternatingRowColors(True)
        self.drives_table.setSortingEnabled(True)
        self.drives_table.setStyleSheet("QCheckBox::indicator:checked { background-color: #0e639c; }")
        dv.addWidget(self.drives_table)
        
        btns = QHBoxLayout()
        self.btn_check_all = QPushButton("Check All")
        self.btn_check_all.clicked.connect(self.check_all_drives)
        self.btn_uncheck_all = QPushButton("Uncheck All")
        self.btn_uncheck_all.clicked.connect(self.uncheck_all_drives)
        
        self.btn_apply_global = QPushButton("🌐 Apply as Global Filter to All Tabs")
        self.btn_apply_global.setStyleSheet("background-color: #0e639c;")
        self.btn_apply_global.clicked.connect(self.apply_global_drive_filter)
        
        self.btn_update_drive = QPushButton("🔄 Update Selected Drive")
        self.btn_update_drive.setStyleSheet("background-color: #0e639c;")
        self.btn_update_drive.clicked.connect(self.update_selected_drive)
        
        self.btn_delete_drive = QPushButton("Delete Selected Drive Data")
        self.btn_delete_drive.clicked.connect(self.delete_selected_drive)
        self.btn_run_compare = QPushButton("Compare Selected Drives")
        self.btn_run_compare.clicked.connect(self.compare_selected)
        
        btns.addWidget(self.btn_check_all)
        btns.addWidget(self.btn_uncheck_all)
        btns.addWidget(self.btn_apply_global)
        btns.addWidget(self.btn_update_drive)
        btns.addWidget(self.btn_delete_drive)
        btns.addWidget(self.btn_run_compare)
        btns.addStretch()
        dv.addLayout(btns)
        
        self.tabs.addTab(drives_tab, "Drives Dashboard")

        # 2. Global Explorer Tab
        explorer_tab = QWidget()
        ev = QVBoxLayout(explorer_tab)
        top_row = QHBoxLayout()
        
        self.btn_up = QPushButton("⬆ Up")
        self.btn_up.clicked.connect(self.navigate_up)
        self.address_bar = QLineEdit()
        self.address_bar.setReadOnly(True)
        self.ex_drive = QComboBox()
        self.ex_drive.addItem("Any Drive")
        self.ex_drive.currentIndexChanged.connect(self.clear_explorer_search)
        
        self.ex_search = QLineEdit()
        self.ex_search.setPlaceholderText("Filter current folder...")
        self.ex_search.textChanged.connect(self.ex_filter_changed)
        
        self.btn_clear_search = QPushButton("Clear")
        self.btn_clear_search.clicked.connect(self.ex_search.clear)
        
        top_row.addWidget(self.btn_up)
        top_row.addWidget(self.address_bar, stretch=2)
        top_row.addWidget(QLabel("Drive:"))
        top_row.addWidget(self.ex_drive)
        top_row.addWidget(QLabel("Filter:"))
        top_row.addWidget(self.ex_search, stretch=1)
        top_row.addWidget(self.btn_clear_search)
        ev.addLayout(top_row)

        split = QSplitter(Qt.Horizontal)
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderLabel("Folders")
        self.folder_tree.itemExpanded.connect(self.on_folder_expand)
        self.folder_tree.itemClicked.connect(self.on_folder_click)
        split.addWidget(self.folder_tree)

        self.file_table = ActionTableView()
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.file_table.horizontalHeader().setStretchLastSection(True)
        self.file_table.setSortingEnabled(True)
        self.file_table.setAlternatingRowColors(True)
        
        self.file_table.itemSelectionChanged.connect(self.on_file_click)
        self.file_table.multiSelectionChanged.connect(self.on_multi_select_update)
        self.file_table.doubleClicked.connect(self.on_table_double_click)
        self.file_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_table.customContextMenuRequested.connect(lambda pos: self.file_context_menu(pos, self.file_table))
        split.addWidget(self.file_table)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0,0,0,0)
        rv.addWidget(QLabel("<b>Details Preview</b>"))
        
        self.preview_image = ScaledImageLabel()
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        
        rv.addWidget(self.preview_image, stretch=1)
        rv.addWidget(self.preview_text)
        
        right_container = QWidget()
        right_container.setLayout(rv)
        split.addWidget(right_container)
        split.setSizes([250, 800, 350])
        ev.addWidget(split)
        
        self.tabs.addTab(explorer_tab, "Global Explorer")
        
        # 2.5. Fast Explorer Tab
        fast_tab = QWidget()
        fv = QVBoxLayout(fast_tab)
        fast_top_row = QHBoxLayout()
        
        self.btn_fast_up = QPushButton("⬆ Up")
        self.btn_fast_up.clicked.connect(self.fast_navigate_up)
        self.fast_address_bar = QLineEdit()
        self.fast_address_bar.setReadOnly(True)
        self.fast_drive = QComboBox()
        self.fast_drive.addItem("Any Drive")
        self.fast_drive.currentIndexChanged.connect(self.clear_fast_search)
        
        self.fast_search = QLineEdit()
        self.fast_search.setPlaceholderText("Filter current folder...")
        self.fast_search.textChanged.connect(self.fast_filter_changed)
        
        self.btn_fast_clear = QPushButton("Clear")
        self.btn_fast_clear.clicked.connect(self.fast_search.clear)
        
        fast_top_row.addWidget(self.btn_fast_up)
        fast_top_row.addWidget(self.fast_address_bar, stretch=2)
        fast_top_row.addWidget(QLabel("Drive:"))
        fast_top_row.addWidget(self.fast_drive)
        fast_top_row.addWidget(QLabel("Filter:"))
        fast_top_row.addWidget(self.fast_search, stretch=1)
        fast_top_row.addWidget(self.btn_fast_clear)
        fv.addLayout(fast_top_row)

        self.fast_table = ActionTableView()
        self.fast_table.verticalHeader().setVisible(False)
        self.fast_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.fast_table.horizontalHeader().setStretchLastSection(True)
        self.fast_table.setSortingEnabled(True)
        self.fast_table.setAlternatingRowColors(True)
        
        self.fast_table.doubleClicked.connect(self.on_fast_table_double_click)
        self.fast_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.fast_table.customContextMenuRequested.connect(lambda pos: self.file_context_menu(pos, self.fast_table))
        fv.addWidget(self.fast_table)
        
        self.tabs.addTab(fast_tab, "⚡ Fast Explorer")
        self.current_fast_prefix = ""
        
        # Move it to index 2 (Right beside Global Explorer)
        self.tabs.tabBar().moveTab(self.tabs.indexOf(fast_tab), 2)        
        

        # 3. MySpace Sandbox Tab 
        myspace_tab = QWidget()
        ms_vbox = QVBoxLayout(myspace_tab)
        ms_top_row = QHBoxLayout()
        
        self.btn_ms_up = QPushButton("⬆ Up")
        self.btn_ms_up.clicked.connect(self.ms_navigate_up)
        self.ms_address_bar = QLineEdit()
        self.ms_address_bar.setReadOnly(True)
        self.ms_search = QLineEdit()
        self.ms_search.setPlaceholderText("Filter Sandbox Content...")
        self.ms_search.textChanged.connect(self.ms_search_changed)
        
        ms_top_row.addWidget(self.btn_ms_up)
        ms_top_row.addWidget(self.ms_address_bar, stretch=2)
        ms_top_row.addWidget(QLabel("Search:"))
        ms_top_row.addWidget(self.ms_search, stretch=1)
        ms_vbox.addLayout(ms_top_row)

        ms_split = QSplitter(Qt.Horizontal)
        self.ms_folder_tree = QTreeWidget()
        self.ms_folder_tree.setHeaderLabel("Virtual Folders")
        self.ms_folder_tree.itemExpanded.connect(self.on_ms_folder_expand)
        self.ms_folder_tree.itemClicked.connect(self.on_ms_folder_click)
        ms_split.addWidget(self.ms_folder_tree)

        self.ms_file_table = SandboxTableView()
        self.ms_file_table.filesDropped.connect(self.on_sandbox_files_dropped)
        self.ms_file_table.verticalHeader().setVisible(False)
        self.ms_file_table.horizontalHeader().setStretchLastSection(True)
        self.ms_file_table.setSortingEnabled(True)
        self.ms_file_table.setAlternatingRowColors(True)
        
        self.ms_file_table.itemSelectionChanged.connect(self.on_ms_file_click)
        self.ms_file_table.multiSelectionChanged.connect(self.on_ms_multi_select_update)
        self.ms_file_table.doubleClicked.connect(self.on_ms_table_double_click)
        self.ms_file_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ms_file_table.customContextMenuRequested.connect(self.ms_context_menu)
        ms_split.addWidget(self.ms_file_table)

        ms_right = QWidget()
        ms_rv = QVBoxLayout(ms_right)
        ms_rv.setContentsMargins(0,0,0,0)
        ms_rv.addWidget(QLabel("<b>Sandbox Details</b>"))
        
        self.ms_preview_image = ScaledImageLabel()
        self.ms_preview_text = QTextEdit()
        self.ms_preview_text.setReadOnly(True)
        
        ms_rv.addWidget(self.ms_preview_image, stretch=1)
        ms_rv.addWidget(self.ms_preview_text)
        
        ms_right_container = QWidget()
        ms_right_container.setLayout(ms_rv)
        ms_split.addWidget(ms_right_container)
        ms_split.setSizes([250, 800, 350])
        ms_vbox.addWidget(ms_split)
        
        self.tabs.addTab(myspace_tab, "⭐ MySpace Sandbox")

        # 4. Advanced Search Tab
        adv_search_tab = QWidget()
        adv_layout = QVBoxLayout(adv_search_tab)
        form_layout = QGridLayout()
        form_layout.setSpacing(10)
        
        self.as_name = QLineEdit()
        self.as_name.returnPressed.connect(self.run_advanced_search) # <--- ADDED
        self.as_folder = QLineEdit()
        self.as_folder.returnPressed.connect(self.run_advanced_search) # <--- ADDED
        self.as_match_type = QComboBox()
        self.as_match_type.addItems(["Contains", "Exact Match", "Starts With", "Ends With"])
        self.as_type = QComboBox()
        self.as_type.addItems(["Files & Folders", "Files Only", "Folders Only"])
        self.as_drive = QComboBox()
        self.as_drive.addItem("Any Drive")
        self.as_ext = QLineEdit()
        self.as_ext.returnPressed.connect(self.run_advanced_search) # <--- ADDED
        
        size_layout = QHBoxLayout()
        self.as_min_size = QLineEdit()
        self.as_min_size.returnPressed.connect(self.run_advanced_search) # <--- ADDED
        self.as_max_size = QLineEdit()
        self.as_max_size.returnPressed.connect(self.run_advanced_search) # <--- ADDED
        size_layout.addWidget(self.as_min_size)
        size_layout.addWidget(QLabel(" to "))
        size_layout.addWidget(self.as_max_size)
        
        date_layout = QHBoxLayout()
        self.as_date_from = QDateEdit()
        self.as_date_from.setCalendarPopup(True)
        self.as_date_from.setDate(QDate(1970, 1, 1))
        
        self.as_date_to = QDateEdit()
        self.as_date_to.setCalendarPopup(True)
        self.as_date_to.setDate(QDate.currentDate().addDays(1))
        
        date_layout.addWidget(self.as_date_from)
        date_layout.addWidget(QLabel(" to "))
        date_layout.addWidget(self.as_date_to)
        
        form_layout.addWidget(QLabel("Item Name:"), 0, 0)
        form_layout.addWidget(self.as_name, 0, 1)
        form_layout.addWidget(QLabel("Folder Path:"), 0, 2)
        form_layout.addWidget(self.as_folder, 0, 3)
        
        form_layout.addWidget(QLabel("Match Mode:"), 1, 0)
        form_layout.addWidget(self.as_match_type, 1, 1)
        form_layout.addWidget(QLabel("Look For:"), 1, 2)
        form_layout.addWidget(self.as_type, 1, 3)
        
        form_layout.addWidget(QLabel("Drive:"), 2, 0)
        form_layout.addWidget(self.as_drive, 2, 1)
        form_layout.addWidget(QLabel("Extension:"), 2, 2)
        form_layout.addWidget(self.as_ext, 2, 3)
        
        form_layout.addWidget(QLabel("Size (MB):"), 3, 0)
        form_layout.addLayout(size_layout, 3, 1)
        form_layout.addWidget(QLabel("Modified:"), 3, 2)
        form_layout.addLayout(date_layout, 3, 3)
        
        # Add Exclusions layout right above btn_layout in Advanced Search Tab
        excl_layout = QHBoxLayout()
        excl_layout.addWidget(QLabel("Exclude Names (comma separated):"))
        self.as_exclude_text = QLineEdit()
        excl_layout.addWidget(self.as_exclude_text)
        excl_layout.addWidget(QLabel("Exclude Exts:"))
        self.as_exclude_ext = QLineEdit()
        excl_layout.addWidget(self.as_exclude_ext)
        adv_layout.addLayout(excl_layout)
        
        btn_layout = QHBoxLayout()
        self.btn_adv_search = QPushButton("Run Search")
        self.btn_adv_search.clicked.connect(self.run_advanced_search)
        self.btn_adv_clear = QPushButton("Clear Criteria")
        self.btn_adv_clear.clicked.connect(self.clear_advanced_search)
        self.btn_adv_export = QPushButton("Export Results")
        self.btn_adv_export.clicked.connect(self.export_advanced_search)
        
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_adv_clear)
        btn_layout.addWidget(self.btn_adv_export)
        btn_layout.addWidget(self.btn_adv_search)
        
        adv_layout.addLayout(form_layout)
        adv_layout.addLayout(btn_layout)
        
        self.as_table = ActionTableView()
        self.as_table.verticalHeader().setVisible(False)
        self.as_table.horizontalHeader().setStretchLastSection(True)
        self.as_table.setSortingEnabled(True)
        self.as_table.setAlternatingRowColors(True)
        
        self.as_table.itemSelectionChanged.connect(self.on_as_click)
        self.as_table.doubleClicked.connect(self.on_as_double_click)
        self.as_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.as_table.customContextMenuRequested.connect(lambda pos: self.file_context_menu(pos, self.as_table))
        adv_layout.addWidget(self.as_table)
        
        self.tabs.addTab(adv_search_tab, "🔍 Advanced Search")

        # 5. Comparisons tab
        comp_tab = QWidget()
        cv = QVBoxLayout(comp_tab)
        row = QHBoxLayout()
        
        buttons = [
            ("Exact duplicates (SHA)", "dup_by_sha"), 
            ("Same content, diff path", "same_content_diff_path"), 
            ("Same filename diff locations", "same_name_diff_location"), 
            ("Name conflicts (size)", "name_conflicts"), 
            ("Missing (By RelPath)", "missing_relpath"), 
            ("Missing (By File Name)", "missing_name")
        ]
        for t, m in buttons:
            btn = QPushButton(t)
            btn.clicked.connect(lambda _, x=m: self.run_compare_mode(x))
            row.addWidget(btn)
        cv.addLayout(row)
        
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter Name:"))
        self.comp_search_name = QLineEdit()
        self.comp_search_name.textChanged.connect(self.apply_comp_filter)
        filter_layout.addWidget(self.comp_search_name)
        
        filter_layout.addWidget(QLabel("Filter Ext:"))
        self.comp_search_ext = QLineEdit()
        self.comp_search_ext.textChanged.connect(self.apply_comp_filter)
        filter_layout.addWidget(self.comp_search_ext)
        
        filter_layout.addWidget(QLabel("General Search:"))
        self.comp_search = QLineEdit()
        self.comp_search.setPlaceholderText("Search in displayed results...")
        self.comp_search.textChanged.connect(self.apply_comp_filter)
        filter_layout.addWidget(self.comp_search)
        cv.addLayout(filter_layout)
        
        self.comp_table = ActionTableView()
        self.comp_table.verticalHeader().setVisible(False)
        self.comp_table.horizontalHeader().setStretchLastSection(True)
        self.comp_table.setAlternatingRowColors(True)
        self.comp_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.comp_table.customContextMenuRequested.connect(self.comp_context_menu)
        self.comp_table.doubleClicked.connect(self.on_as_double_click)
        cv.addWidget(self.comp_table)
        
        exp_row = QHBoxLayout()
        self.btn_export_comp = QPushButton("Export last compare")
        self.btn_export_comp.clicked.connect(self.export_last_compare)
        exp_row.addWidget(self.btn_export_comp)
        exp_row.addStretch()
        cv.addLayout(exp_row)
        self.tabs.addTab(comp_tab, "Comparisons")

        # 6. Advanced Reports Dashboard
        reports_tab = QWidget()
        rv2 = QVBoxLayout(reports_tab)
        rep_split = QSplitter(Qt.Horizontal)
        
        left_rep = QWidget()
        left_vbox = QVBoxLayout(left_rep)
        left_vbox.addWidget(QLabel("<b>Saved Reports</b>"))
        self.reports_list = QListWidget()
        self.reports_list.itemClicked.connect(self.load_report_to_table)
        left_vbox.addWidget(self.reports_list)
        rep_split.addWidget(left_rep)
        
        right_rep = QWidget()
        right_vbox = QVBoxLayout(right_rep)
        rep_controls = QHBoxLayout()
        
        rep_controls.addWidget(QLabel("Filter:"))
        self.rep_filter = QLineEdit()
        self.rep_filter.textChanged.connect(self.apply_report_filter)
        rep_controls.addWidget(self.rep_filter)
        
        rep_controls.addWidget(QLabel("Max Rows:"))
        self.rep_limit = QComboBox()
        self.rep_limit.addItems(["1000", "5000", "10000", "All"])
        self.rep_limit.currentIndexChanged.connect(self.apply_report_filter)
        rep_controls.addWidget(self.rep_limit)
        right_vbox.addLayout(rep_controls)
        
        self.rep_table = QTableView()
        self.rep_table.verticalHeader().setVisible(False)
        self.rep_table.horizontalHeader().setStretchLastSection(True)
        self.rep_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rep_table.setAlternatingRowColors(True)
        self.rep_table.setSortingEnabled(True)
        right_vbox.addWidget(self.rep_table)
        
        rep_split.addWidget(right_rep)
        rep_split.setSizes([300, 1100])
        rv2.addWidget(rep_split)
        self.tabs.addTab(reports_tab, "📑 Advanced Reports")

        # 7. Statistics tab
        charts_tab = QWidget()
        cv2 = QVBoxLayout(charts_tab)
        chart_controls = QHBoxLayout()
        
        chart_controls.addWidget(QLabel("Target Drive:"))
        self.stat_drive = QComboBox()
        self.stat_drive.addItem("Any Drive")
        self.stat_drive.currentIndexChanged.connect(self.update_charts)
        chart_controls.addWidget(self.stat_drive)
        
        chart_controls.addWidget(QLabel("  Date:"))
        self.stat_date_from = QDateEdit()
        self.stat_date_from.setCalendarPopup(True)
        self.stat_date_from.setDate(QDate(1970, 1, 1))
        
        self.stat_date_to = QDateEdit()
        self.stat_date_to.setCalendarPopup(True)
        self.stat_date_to.setDate(QDate.currentDate().addDays(1))
        
        self.stat_date_from.dateChanged.connect(self.update_charts)
        self.stat_date_to.dateChanged.connect(self.update_charts)
        
        chart_controls.addWidget(self.stat_date_from)
        chart_controls.addWidget(QLabel("to"))
        chart_controls.addWidget(self.stat_date_to)
        
        chart_controls.addWidget(QLabel("   Statistic:"))
        self.chart_combo = QComboBox()
        self.chart_combo.addItems([
            "Drives by Total Size (GB)", "Drives by File Count", "Top 15 Formats by Count", 
            "Top 15 Formats by Size", "File Size Distribution", "Files by Mod Year", 
            "Top 20 Largest Files", "Storage Usage by Year", "File Age Distribution",
            "Drive Overlap (Shared Files)"
        ])
        self.chart_combo.currentIndexChanged.connect(self.update_charts)
        chart_controls.addWidget(self.chart_combo)
        
        self.stat_chart_type = QComboBox()
        self.stat_chart_type.addItems(["Bar Chart", "Line Chart", "Horizontal Bar"])
        self.stat_chart_type.currentIndexChanged.connect(self.update_charts)
        chart_controls.addWidget(self.stat_chart_type)
        chart_controls.addStretch()
        cv2.addLayout(chart_controls)

        if MATPLOTLIB_AVAILABLE:
            self.figure = Figure(figsize=(8,6))
            self.canvas = FigureCanvas(self.figure)
            cv2.addWidget(self.canvas)
        else:
            cv2.addWidget(QLabel("Matplotlib not installed; charts disabled"))
            self.figure = None
            self.canvas = None
            
        self.tabs.addTab(charts_tab, "📊 Statistics & Charts")

        # 8. Activity Timeline Diary Tab (PRO UPGRADE)
        diary_tab = QWidget()
        dv_main = QVBoxLayout(diary_tab)

        # Top Control Bar - Added Category & Extension Filters
        diary_controls = QHBoxLayout()
        
        diary_controls.addWidget(QLabel("Drive:"))
        self.diary_drive = QComboBox()
        self.diary_drive.addItem("Any Drive")
        self.diary_drive.currentIndexChanged.connect(self.refresh_diary_data)
        diary_controls.addWidget(self.diary_drive)

        diary_controls.addWidget(QLabel("Category:"))
        self.diary_category = QComboBox()
        self.diary_category.addItems(["All Files", "Images", "Videos", "Documents", "Audio", "Archives", "Code/Scripts"])
        self.diary_category.currentIndexChanged.connect(self.refresh_diary_data)
        diary_controls.addWidget(self.diary_category)

        diary_controls.addWidget(QLabel("Ext:"))
        self.diary_ext = QLineEdit()
        self.diary_ext.setPlaceholderText("e.g. .jpg")
        self.diary_ext.setMaximumWidth(80)
        self.diary_ext.textChanged.connect(self.refresh_diary_data)
        diary_controls.addWidget(self.diary_ext)

        diary_controls.addWidget(QLabel("Year:"))
        self.diary_year = QComboBox()
        self.diary_year.currentIndexChanged.connect(self.on_diary_year_changed)
        diary_controls.addWidget(self.diary_year)

        self.diary_search = QLineEdit()
        self.diary_search.setPlaceholderText("Filter current view (Name, Path)...")
        self.diary_search.textChanged.connect(self.apply_diary_filter)
        diary_controls.addWidget(self.diary_search, stretch=1)

        self.btn_refresh_diary = QPushButton("↻ Refresh")
        self.btn_refresh_diary.clicked.connect(self.refresh_diary_data)
        diary_controls.addWidget(self.btn_refresh_diary)
        dv_main.addLayout(diary_controls)

        diary_split = QSplitter(Qt.Horizontal)

        # Left Side: Calendar, Quick Controls & Monthly Chart
        cal_container = QWidget()
        cal_layout = QVBoxLayout(cal_container)
        cal_layout.addWidget(QLabel("<b>Activity Calendar</b>"))
        
        self.activity_cal = QCalendarWidget()
        self.activity_cal.setGridVisible(True)
        # -> Mouse click updates table
        self.activity_cal.clicked.connect(self.on_diary_date_clicked)
        # -> Enter/Return key updates table natively 
        self.activity_cal.activated.connect(self.on_diary_date_clicked) 
        # -> Arrow keys changing months updates the chart
        self.activity_cal.currentPageChanged.connect(self.update_diary_chart) 
        cal_layout.addWidget(self.activity_cal)

        self.btn_view_timeline = QPushButton("🌟 Load Current Month's Timeline")
        self.btn_view_timeline.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold; padding: 8px;")
        self.btn_view_timeline.clicked.connect(self.load_full_timeline)
        cal_layout.addWidget(self.btn_view_timeline)

        # Colorful Chart Widget & Controls
        chart_ctrl_layout = QHBoxLayout()
        chart_ctrl_layout.addWidget(QLabel("<b>Chart Mode:</b>"))
        self.diary_chart_mode = QComboBox()
        self.diary_chart_mode.addItems([
            "Daily Activity (Count)", 
            "Daily Activity (Size)", 
            "By Category (Count)", 
            "By Category (Size)",
            "By Extension (Count)",
            "By Extension (Size)"
        ])
        # Update chart immediately when option is changed
        self.diary_chart_mode.currentIndexChanged.connect(lambda: self.update_diary_chart(self.activity_cal.selectedDate().year(), self.activity_cal.selectedDate().month()))
        chart_ctrl_layout.addWidget(self.diary_chart_mode, stretch=1)
        cal_layout.addLayout(chart_ctrl_layout)

        if MATPLOTLIB_AVAILABLE:
            self.diary_figure = Figure(figsize=(4, 3))
            self.diary_canvas = FigureCanvas(self.diary_figure)
            cal_layout.addWidget(self.diary_canvas)
        else:
            cal_layout.addWidget(QLabel("Matplotlib not installed. Chart disabled."))

        diary_split.addWidget(cal_container)

        # Right Side: Interactive Table View
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        
        self.diary_lbl = QLabel("<b>Select a date or load the full timeline to view events. (Tip: Use Arrow Keys, Enter to open, Backspace to clear date)</b>")
        right_layout.addWidget(self.diary_lbl)

        self.diary_table = ActionTableView()
        self.diary_table.verticalHeader().setVisible(False)
        self.diary_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.diary_table.horizontalHeader().setStretchLastSection(True)
        self.diary_table.setSortingEnabled(True)
        self.diary_table.setAlternatingRowColors(True)
        
        self.diary_table.doubleClicked.connect(self.on_diary_table_double_click)
        self.diary_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.diary_table.customContextMenuRequested.connect(lambda pos: self.file_context_menu(pos, self.diary_table))
        
        # KEYBOARD CONTROLS INJECTION
        original_key_press = self.diary_table.keyPressEvent
        def custom_diary_key_press(event):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                idx = self.diary_table.currentIndex()
                if idx.isValid(): self.on_diary_table_double_click(idx)
            elif event.key() == Qt.Key_Backspace:
                self.load_full_timeline() # Backspace clears date filter and loads full filtered timeline
            else:
                original_key_press(event) # Preserves Arrow keys for navigation
        self.diary_table.keyPressEvent = custom_diary_key_press

        right_layout.addWidget(self.diary_table)
        diary_split.addWidget(right_container)
        diary_split.setSizes([450, 1150])
        
        dv_main.addWidget(diary_split)
        self.tabs.addTab(diary_tab, "📅 Timeline Diary")

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.selected_label = QLabel("0 selected")
        self.status.addPermanentWidget(self.selected_label)

    def _setup_shortcuts(self):
            # --- Global App Navigation ---
            QShortcut(QKeySequence("Ctrl+M"), self, activated=self.restore_background_players)


            # Next/Prev Tab (Ctrl+Tab / Ctrl+Shift+Tab)
            QShortcut(QKeySequence("Ctrl+Tab"), self, activated=self.next_tab)
            QShortcut(QKeySequence("Ctrl+Shift+Tab"), self, activated=self.prev_tab)
            
            # Quick Tab Jumps (Ctrl+1, Ctrl+2, etc.)
            for i in range(self.tabs.count()):
                QShortcut(QKeySequence(f"Ctrl+{i+1}"), self, activated=lambda idx=i: self.tabs.setCurrentIndex(idx))

            # --- Global App Actions ---
            QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close) # Quit
            QShortcut(QKeySequence("F5"), self, activated=self.refresh_all) # Global Refresh
            QShortcut(QKeySequence("Ctrl+T"), self, activated=self.toggle_theme) # Toggle Theme
            
            # Smart Search Focus (Ctrl+F routes to the active tab's search bar)
            QShortcut(QKeySequence("Ctrl+F"), self, activated=self.smart_focus_search)
            
            # --- Sandbox Clipboard Shortcuts ---
            QShortcut(QKeySequence.Copy, self.ms_file_table, activated=self.ms_copy)
            QShortcut(QKeySequence.Cut, self.ms_file_table, activated=self.ms_cut)
            QShortcut(QKeySequence.Paste, self.ms_file_table, activated=self.ms_paste)
            QShortcut(QKeySequence.Delete, self.ms_file_table, activated=self.ms_delete_selected_shortcut)

    # ---------- Navigation Helpers ----------
    def restore_background_players(self):
        if hasattr(self, 'active_viewers'):
            for viewer in self.active_viewers:
                if viewer.isHidden():
                    viewer.showNormal()
                    viewer.activateWindow()    
    
    
    def next_tab(self):
            next_idx = (self.tabs.currentIndex() + 1) % self.tabs.count()
            self.tabs.setCurrentIndex(next_idx)

    def prev_tab(self):
            prev_idx = (self.tabs.currentIndex() - 1) % self.tabs.count()
            self.tabs.setCurrentIndex(prev_idx)
            
    def smart_focus_search(self):
            """Focuses the relevant search bar depending on the active tab."""
            current_tab = self.tabs.tabText(self.tabs.currentIndex())
            if current_tab == "Global Explorer":
                self.ex_search.setFocus()
                self.ex_search.selectAll()
            elif current_tab == "⚡ Fast Explorer":
                self.fast_search.setFocus()
                self.fast_search.selectAll()
            elif current_tab == "⭐ MySpace Sandbox":
                self.ms_search.setFocus()
                self.ms_search.selectAll()
            elif current_tab == "📅 Timeline Diary":
                self.diary_search.setFocus()
                self.diary_search.selectAll()
            elif current_tab == "Comparisons":
                self.comp_search.setFocus()
                self.comp_search.selectAll()
            elif current_tab == "🔍 Advanced Search":
                self.as_name.setFocus()
                self.as_name.selectAll()
            elif current_tab == "📑 Advanced Reports":
                self.rep_filter.setFocus()
                self.rep_filter.selectAll()

    def show_help(self):
        help_text = (
            "<h3>General & Navigation</h3>"
            "<b>Ctrl + F</b>: Focus Filter box.<br>"
            "<b>Up/Down Arrows</b>: Navigate tables and auto-update Image/Details Preview.<br>"
            "<b>Shift/Ctrl + Click (or Drag)</b>: Multi-select items. Status bar shows total size.<br>"
            "<b>Enter</b>: Open selected folder or real file externally.<br><br>"

            "<h3>Drives Dashboard</h3>"
            "<b>Smart Update</b>: Updates existing drives instantly by skipping hashes for untouched files.<br>"
            "<b>Global Filter</b>: Locks the entire app (Search, Stats, Timeline) to your selected drives.<br>"
            "<b>Comments</b>: Double-click the 'Comment' cell to add custom notes to any drive.<br><br>"

            "<h3>Advanced Search</h3>"
            "<b>Exclusions</b>: Use the 'Exclude' fields to ignore specific words or extensions.<br>"
            "<b>Smart Post-Filter</b>: After searching, a popup lets you quickly uncheck extensions you want to hide.<br><br>"

            "<h3>Sandbox Shortcuts</h3>"
            "<b>Ctrl + C / X / V</b>: Copy, Cut, Paste items (Supports folders recursively, features Conflict Resolution).<br>"
            "<b>Delete</b>: Remove selected from Sandbox.<br><br>"

            "<h3>Advanced Internal Viewer (General)</h3>"
            "<b>Left / Right</b> or <b>PgUp / PgDn</b>: Previous / Next file.<br>"
            "<b>F11</b>: True borderless Fullscreen.<br>"
            "<b>Esc</b>: Exit Fullscreen / Close viewer.<br>"
            "<b>H</b>: Hide/Show the bottom tool panel.<br>"
            "<b>B</b>: Send to background (Hide viewer but keep media playing). Restore with Ctrl+M on main app.<br>"
            "<b>S</b>: Toggle Slideshow / Vertical Auto-Scroll.<br><br>"

            "<h3>Internal Viewer (Images & Documents)</h3>"
            "<b>Ctrl + Scroll Wheel</b> or <b>+ / -</b>: Mouse-anchored Zoom In/Out.<br>"
            "<b>0</b>: Reset View to Original Size.<br>"
            "<b>W</b>: Fit to Width (Perfect for vertical reading/webtoon scrolling).<br>"
            "<b>R</b>: Rotate 90°.<br>"
            "<b>F</b> / <b>Shift+F</b>: Flip Horizontally / Vertically.<br>"
            "<i>Tip: Left-click and drag to pan smoothly around zoomed images.</i><br><br>"

            "<h3>Internal Viewer (Media Player)</h3>"
            "<b>Space</b>: Play / Pause.<br>"
            "<b>Shift + Left / Right</b>: Seek backward/forward 5 seconds.<br>"
            "<b>Up / Down</b>: Volume Up / Down.<br>"
            "<b>M</b>: Mute / Unmute.<br><br>"

            "<h3>Notes</h3>"
            "• <b>Global Copies</b> counts instances of a file's hash globally across all drives.<br>"
            "• <b>Custom Icons</b>: Place .png or .ico files in the 'icons' folder named by extension (e.g., 'jpg.png')."
        )
        
        # Create a custom scrollable dialog
        help_dialog = QDialog(self)
        help_dialog.setWindowTitle("Help, Features & Shortcuts")
        help_dialog.resize(600, 500)  # Keeps the window at a fixed, reasonable size
        
        layout = QVBoxLayout(help_dialog)
        
        # Use QTextBrowser to support rich HTML text and automatic scrollbars
        text_browser = QTextBrowser()
        text_browser.setHtml(help_text)
        text_browser.setStyleSheet("font-size: 13px; background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #333; padding: 10px;")
        
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet("QPushButton { background-color: #0e639c; color: white; padding: 6px; border-radius: 4px; font-weight: bold; } QPushButton:hover { background-color: #1177bb; }")
        close_btn.clicked.connect(help_dialog.accept)
        
        layout.addWidget(text_browser)
        layout.addWidget(close_btn)
        
        help_dialog.exec()

    # ---------- Global Data Methods ----------
    def scan_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder to scan")
        if not folder: 
            return
        
        drive_name, ok = QInputDialog.getText(self, "Drive name", "Short name for this drive:")
        if not ok or not drive_name.strip(): 
            return
            
        # Safety Check: Prevent accidental overwrite of an existing drive
        cur = self.db.conn.cursor()
        cur.execute("SELECT drive_name FROM drives WHERE drive_name = ?", (drive_name.strip(),))
        if cur.fetchone():
            if QMessageBox.question(self, "Drive Exists", f"A drive named '{drive_name.strip()}' already exists. Overwrite completely?\n\n(Tip: Use 'Update Selected Drive' instead to preserve hashes and comments!)", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
        
        purchase, _ = QInputDialog.getText(self, "Purchase date", "Purchase date (MM/YYYY or YYYY-MM-DD) — optional:")
        csha = QMessageBox.question(self, "SHA256", "Compute SHA-256 for files? (slower)", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
        
        # New Feature: Ask if user wants a CSV backup
        save_csv = QMessageBox.question(self, "Save CSV Backup", "Do you want to save a CSV backup for this scan?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
        
        # Wipe old drive records to prevent duplicate SQL rows
        self.db.delete_drive(drive_name.strip())
        
        dlg = QProgressDialog("Scanning...", "Cancel", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.show()
        
        t = ScanThread(folder, drive_name.strip(), purchase.strip(), csha, workers=max(1, min(32, (os.cpu_count() or 2) * 2)), write_csv=save_csv, parent=self)
        t.progress.connect(dlg.setValue)
        t.error.connect(lambda msg: (dlg.close(), QMessageBox.critical(self, "Scan error", str(msg))))
        t.finished.connect(lambda cp, dn, pd: (dlg.close(), self.db.insert_drive(dn, pd, cp), self.refresh_all(), QMessageBox.information(self, "Done", f"Scanned drive '{dn}'")))
        self._register_worker(t)
        t.start()

    def import_csvs(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select CSVs", str(CSV_DIR), "CSV files (*.csv)")
        for f in files:
            default_dname = Path(f).stem
            dlg = ImportDetailsDialog(default_dname, self)
            if dlg.exec():
                dname, pdate, comment = dlg.get_data()
                if not dname: continue
                
                pdlg = QProgressDialog(f"Importing data to '{dname}'...", "Cancel", 0, 0, self)
                pdlg.show()
                t = ImportThread(f, dname, pdate, comment, parent=self)
                t.progress.connect(lambda n: pdlg.setLabelText(f"Inserted: {n} files"))
                t.error.connect(lambda msg: (pdlg.close(), QMessageBox.critical(self, "Error", str(msg))))
                t.finished.connect(lambda cnt, dn: (pdlg.close(), self.refresh_all(), QMessageBox.information(self, "Done", f"Successfully imported {cnt} records into {dn}.")))
                self._register_worker(t)
                t.start()

    def toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        if self.is_dark_mode:
            dark_ss = """
                QMainWindow, QWidget { background-color: #1e1e1e; color: #d4d4d4; }
                QTableWidget, QTreeWidget, QListWidget, QTableView { background-color: #252526; color: #d4d4d4; border: 1px solid #3e3e42; alternate-background-color: #2d2d30; }
                QTextEdit, QTextBrowser, QPlainTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #3e3e42; }
                QTableView::indicator, QTableWidget::indicator { width: 18px; height: 18px; }
                QHeaderView::section { background-color: #333333; color: #ffffff; padding: 4px; border: 1px solid #3e3e42; }
                QLineEdit, QComboBox, QDateEdit, QSpinBox { background-color: #333333; color: #d4d4d4; border: 1px solid #555; padding: 3px; border-radius: 3px; }
                
                /* --- THE FIX: Slimmed scrollbars and forced dropdown width --- */
                QScrollBar:vertical { border: none; background: #1e1e1e; width: 12px; margin: 0px; }
                QScrollBar::handle:vertical { background: #555; border-radius: 6px; min-height: 20px; }
                QScrollBar::handle:vertical:hover { background: #777; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
                QComboBox QAbstractItemView { min-width: 100px; background-color: #252526; selection-background-color: #0e639c; }
                /* ------------------------------------------------------------- */
                
                QPushButton { background-color: #0e639c; color: #ffffff; border: none; padding: 6px 12px; border-radius: 3px; font-weight: bold; }
                QPushButton:hover { background-color: #1177bb; }
                QTabBar::tab { background-color: #2d2d30; color: #d4d4d4; padding: 8px 16px; border: 1px solid #3e3e42; }
                QTabBar::tab:selected { background-color: #1e1e1e; font-weight: bold; border-bottom: 2px solid #0e639c; }
                QToolBar { border: none; background-color: #2d2d30; }
                QMenu { background-color: #252526; color: #d4d4d4; border: 1px solid #3e3e42; }
                QMenu::item:selected { background-color: #0e639c; }
                QCalendarWidget QWidget { alternate-background-color: #252526; }
                QCalendarWidget QAbstractItemView:enabled { color: #d4d4d4; background-color: #1e1e1e; selection-background-color: #0e639c; selection-color: white; }
                QCalendarWidget QAbstractItemView:disabled { color: #555; }
            """
            self.setStyleSheet(dark_ss)
            self.address_bar.setStyleSheet("background-color: #333333; color: #ffffff; padding: 4px; font-weight: bold; border: 1px solid #555;")
            self.ms_address_bar.setStyleSheet("background-color: #333333; color: #ffffff; padding: 4px; font-weight: bold; border: 1px solid #555;")
        else:
            self.setStyleSheet("")
            self.address_bar.setStyleSheet("background-color: #ffffff; color: #000000; padding: 4px; font-weight: bold; border: 1px solid #ccc;")
            self.ms_address_bar.setStyleSheet("background-color: #e8f4f8; color: #000000; padding: 4px; font-weight: bold; border: 1px solid #b8daff;")
        self.update_charts() 
        self.refresh_diary_data()

    def _register_worker(self, worker: QThread):
        self._workers.append(worker)
        worker.finished.connect(lambda: self._cleanup_worker(worker))

    def _cleanup_worker(self, worker: QThread):
        try:
            if worker in self._workers: 
                self._workers.remove(worker)
            worker.deleteLater()
        except Exception: 
            pass

    def refresh_all(self):
        dlg = QProgressDialog("Loading Database & Indexing... Please wait.", None, 0, 7, self)
        dlg.setWindowTitle("Refreshing Data")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            QApplication.processEvents()
            self.refresh_drives()
            dlg.setValue(1)
            QApplication.processEvents()
            self.refresh_reports()
            dlg.setValue(2)
            QApplication.processEvents()
            self.refresh_folder_tree()
            dlg.setValue(3)
            QApplication.processEvents()
            self.load_directory("")
            dlg.setValue(4)
            QApplication.processEvents()
            self.refresh_myspace_tree()
            self.load_myspace_directory("/")
            dlg.setValue(5)
            QApplication.processEvents()
            self.update_charts()
            dlg.setValue(6)
            QApplication.processEvents()
            self.refresh_diary_data()
        finally:
            QApplication.restoreOverrideCursor()
            dlg.close()

    def refresh_drives(self):
        self.drives_table.blockSignals(True)
        self.drives_table.setSortingEnabled(False)
        self.drives_table.setRowCount(0)
        
        self.ex_drive.blockSignals(True); self.ex_drive.clear(); self.ex_drive.addItem("Any Drive")
        self.as_drive.blockSignals(True); self.as_drive.clear(); self.as_drive.addItem("Any Drive")
        self.stat_drive.blockSignals(True); self.stat_drive.clear(); self.stat_drive.addItem("Any Drive")
        self.fast_drive.blockSignals(True); self.fast_drive.clear(); self.fast_drive.addItem("Any Drive")
        self.diary_drive.blockSignals(True); self.diary_drive.clear(); self.diary_drive.addItem("Any Drive")
        
        summary = self.db.drives_summary()
        self.drives_table.setRowCount(len(summary))
        self.load_fast_directory("")
        
        def _make_readonly_item(text, sort_val):
            it = SmartTableItem(text, sort_val)
            it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled) # Locks the cell from editing
            return it
            
        for row, (drive_name, purchase_date, scanned_at, csv_path, comment, file_count, total_size) in enumerate(summary):
            age_str, age_days = age_from_date(parse_purchase_date(purchase_date))
            
            # FIX: Restore the combo-box population
            self.ex_drive.addItem(drive_name)
            self.as_drive.addItem(drive_name)
            self.stat_drive.addItem(drive_name)
            self.fast_drive.addItem(drive_name)
            self.diary_drive.addItem(drive_name)
            
            # User Friendly Date Format
            try:
                dt = datetime.fromisoformat(scanned_at)
                scan_str = dt.strftime("%b %d, %Y - %I:%M %p") # e.g. July 23, 2026 - 08:33 PM
            except:
                scan_str = str(scanned_at)
            
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.setContentsMargins(0,0,0,0)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk_box = QCheckBox()
            chk_box.setProperty("drive_name", drive_name)
            chk_box.setChecked(drive_name in self.global_drive_filter)
            chk_box.toggled.connect(self.update_selected_label)
            chk_layout.addWidget(chk_box)
            
            self.drives_table.setCellWidget(row, 0, chk_widget)
            self.drives_table.setItem(row, 1, _make_readonly_item(str(drive_name), str(drive_name).lower()))
            self.drives_table.setItem(row, 2, _make_readonly_item(str(file_count), file_count))
            self.drives_table.setItem(row, 3, _make_readonly_item(human_size(total_size), total_size))
            self.drives_table.setItem(row, 4, _make_readonly_item(age_str, age_days))
            self.drives_table.setItem(row, 5, _make_readonly_item(scan_str, scanned_at))
            
            # Editable Comment Column
            comment_item = QTableWidgetItem(str(comment) if comment else "")
            comment_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsEditable) # Allows editing
            comment_item.setData(Qt.UserRole, drive_name) # Safe retrieval identifier
            self.drives_table.setItem(row, 6, comment_item)
            
        self.drives_table.setColumnWidth(0, 50); self.drives_table.setColumnWidth(1, 150)
        self.drives_table.setColumnWidth(6, 300) # Give comments more room
        
        self.ex_drive.blockSignals(False)
        self.as_drive.blockSignals(False)
        self.stat_drive.blockSignals(False)
        self.fast_drive.blockSignals(False)
        self.diary_drive.blockSignals(False)
        
        self.drives_table.setSortingEnabled(True)
        self.drives_table.blockSignals(False)
        self.update_selected_label()
        
    def on_drive_comment_changed(self, row, col):
        if col == 6: # Comment column
            item = self.drives_table.item(row, col)
            drive_name = item.data(Qt.UserRole)
            self.db.update_drive_comment(drive_name, item.text())

    def apply_global_drive_filter(self):
        sel_items = self.selected_drives()
        self.global_drive_filter = list(sel_items)
        msg = f"Applied Global Filter. Now showing data strictly for {len(sel_items)} drives." if sel_items else "Cleared Global Filter. Showing all drives."
        QMessageBox.information(self, "Global Filter", msg)
        self.refresh_all()
        
    def update_selected_drive(self):
        sel_items = self.selected_drives()
        if len(sel_items) != 1: 
            return QMessageBox.warning(self, "Update", "Please select exactly ONE drive to update.")
        
        drive_name = sel_items[0]
        folder = QFileDialog.getExistingDirectory(self, f"Select the physical folder for pendrive '{drive_name}' to sync against")
        if not folder: return
        
        # New Feature: Ask if user wants a CSV backup
        save_csv = QMessageBox.question(self, "Export CSV Backup", "Do you want to save a CSV backup of this update?", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes

        dlg = QProgressDialog("Analyzing differences (Reading Fast Hashes)...", "Cancel", 0, 0, self)
        dlg.show()
        
        cur = self.db.conn.cursor()
        
        # 1. Fetch current known files for smart hashing
        cur.execute("SELECT relpath, size, modified, sha FROM files WHERE drive = ? AND is_folder=0", (drive_name,))
        existing_files = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
        
        # 2. Fetch existing metadata to preserve Age and Comments
        cur.execute("SELECT purchase_date, comment FROM drives WHERE drive_name = ?", (drive_name,))
        meta_row = cur.fetchone()
        old_purchase_date = meta_row[0] if meta_row and meta_row[0] else ""
        old_comment = meta_row[1] if meta_row and meta_row[1] else ""
        
        # 3. CRITICAL: Delete old drive data before scan starts to prevent duplicating rows in SQL.
        # We no longer rely on the CSV file to re-import data, the Thread pushes directly to SQL.
        self.db.delete_drive(drive_name)
        
        dlg.close()
        
        scan_dlg = QProgressDialog(f"Updating '{drive_name}' (Skipping unmodified files)...", "Cancel", 0, 100, self)
        scan_dlg.show()
        
        # Pass the write_csv preference to the thread
        t = ScanThread(folder, drive_name, old_purchase_date, True, workers=max(1, min(32, (os.cpu_count() or 2) * 2)), write_csv=save_csv, update_map=existing_files, parent=self)
        t.progress.connect(scan_dlg.setValue)
        
        def finish_update(cp, dn, pd):
            scan_dlg.close()
            
            # Manually restore the drive record with BOTH the preserved purchase date AND comment
            cur = self.db.conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO drives (drive_name, purchase_date, scanned_at, csv_path, comment) VALUES (?, ?, ?, ?, ?);",
                (dn, pd, datetime.now().isoformat(), cp, old_comment)
            )
            self.db.conn.commit()
            
            self.refresh_all()
            QMessageBox.information(self, "Done", f"Smart Update for '{dn}' completed successfully.\nReused hashes for unmodified files. (Age and Comments preserved).")
            
        t.finished.connect(finish_update)
        t.error.connect(lambda msg: (scan_dlg.close(), QMessageBox.critical(self, "Scan error", str(msg))))
        self._register_worker(t)
        t.start()

    def update_selected_label(self):
        self.selected_label.setText(f"{len(self.selected_drives())} selected")

    def selected_drives(self) -> List[str]:
        drives = []
        for r in range(self.drives_table.rowCount()):
            widget = self.drives_table.cellWidget(r, 0)
            if widget:
                chk = widget.findChild(QCheckBox)
                if chk and chk.isChecked(): 
                    drives.append(chk.property("drive_name"))
        return drives

    def check_all_drives(self):
        for r in range(self.drives_table.rowCount()):
            widget = self.drives_table.cellWidget(r, 0)
            if widget: 
                widget.findChild(QCheckBox).setChecked(True)
        self.update_selected_label()

    def uncheck_all_drives(self):
        for r in range(self.drives_table.rowCount()):
            widget = self.drives_table.cellWidget(r, 0)
            if widget: 
                widget.findChild(QCheckBox).setChecked(False)
        self.update_selected_label()

    def delete_selected_drive(self):
        sel_items = self.selected_drives()
        if not sel_items: 
            return QMessageBox.information(self, "Delete drive", "Check a drive in the table to delete.")
        
        names = ", ".join(sel_items)
        if QMessageBox.question(self, "Delete drives", f"Delete all database records for these drives?\n{names}", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes: 
            return
        
        # Move associated physical CSVs first
        cur = self.db.conn.cursor()
        for dname in sel_items:
            cur.execute("SELECT csv_path FROM drives WHERE drive_name = ?", (dname,))
            res = cur.fetchone()
            if res and res[0] and os.path.exists(res[0]):
                try: shutil.move(res[0], OLD_DATA_DIR / Path(res[0]).name)
                except Exception: pass
                
        self.del_dlg = QProgressDialog("Initializing deletion...", "Cancel", 0, 100, self)
        self.del_dlg.setWindowModality(Qt.WindowModal)
        self.del_dlg.show()
        
        self.del_thread = DeleteDriveThread(sel_items, DB_FILE, self)
        self.del_thread.progress.connect(lambda val, txt: (self.del_dlg.setValue(val), self.del_dlg.setLabelText(txt)))
        self.del_thread.finished.connect(lambda: (self.del_dlg.close(), self.refresh_all(), QMessageBox.information(self, "Deleted", "Drive data successfully removed.")))
        self.del_thread.error.connect(lambda e: (self.del_dlg.close(), QMessageBox.critical(self, "Error", f"Deletion failed: {e}")))
        
        self._register_worker(self.del_thread)
        self.del_thread.start()

    def update_charts(self):
        if not MATPLOTLIB_AVAILABLE or not PANDAS_AVAILABLE or not self.figure: 
            return
            
        mode = self.chart_combo.currentIndex()
        c_type = self.stat_chart_type.currentText()
        target_drive = self.stat_drive.currentText()
        d_from = self.stat_date_from.date().toString("yyyy-MM-dd")
        d_to = self.stat_date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
        
        self.figure.clear()
        bg_color = '#1e1e1e' if self.is_dark_mode else '#ffffff'
        text_color = 'white' if self.is_dark_mode else 'black'
        self.figure.patch.set_facecolor(bg_color)
        ax = self.figure.add_subplot(111)
        ax.set_facecolor(bg_color)
        ax.text(0.5, 0.5, "Generating Chart Data...\nPlease wait.", ha='center', va='center', color=text_color, fontsize=14)
        ax.set_axis_off()
        self.canvas.draw()
        
        if self.chart_thread and self.chart_thread.isRunning():
            self.chart_thread.cancel()
            self.chart_thread.wait()
            
        self.chart_thread = ChartWorker(DB_FILE, mode, c_type, target_drive, d_from, d_to, self)
        self.chart_thread.finished_data.connect(self._render_chart_data)
        self.chart_thread.progress.connect(self._update_chart_progress) 
        self.chart_thread.error.connect(lambda e: print(f"Chart Error: {e}"))
        self.chart_thread.start()

    def _update_chart_progress(self, pct):
        if not self.figure: return
        self.figure.clear()
        bg_color = '#1e1e1e' if self.is_dark_mode else '#ffffff'
        text_color = 'white' if self.is_dark_mode else 'black'
        self.figure.patch.set_facecolor(bg_color)
        ax = self.figure.add_subplot(111)
        ax.set_facecolor(bg_color)
        ax.text(0.5, 0.5, f"Calculating Overlap... {pct}%\nPlease wait.", ha='center', va='center', color=text_color, fontsize=14)
        ax.set_axis_off()
        self.canvas.draw()        
        
    def _render_chart_data(self, mode, c_type, target_drive, data):
        if not self.figure: 
            return
        self.figure.clear()
        bg_color = '#1e1e1e' if self.is_dark_mode else '#ffffff'
        text_color = 'white' if self.is_dark_mode else 'black'
        spine_color = '#555' if self.is_dark_mode else '#ccc'
        
        self.figure.patch.set_facecolor(bg_color)
        ax = self.figure.add_subplot(111)
        ax.set_facecolor(bg_color)
        
        try:
            if mode == 0 or mode == 1:
                if not data: return
                df = pd.DataFrame(data, columns=["drive", "files", "size"])
                if mode == 0:
                    df = df.sort_values(by="size", ascending=True)
                    if c_type == "Bar Chart": 
                        ax.bar(df["drive"], df["size"] / (1024**3), color='#3498db')
                        ax.set_ylabel("Size (GB)")
                    elif c_type == "Line Chart": 
                        ax.plot(df["drive"], df["size"] / (1024**3), marker='o', color='#3498db')
                        ax.set_ylabel("Size (GB)")
                    else: 
                        ax.barh(df["drive"], df["size"] / (1024**3), color='#3498db')
                        ax.set_xlabel("Size (GB)")
                    ax.set_title("Total Storage Space by Drive")
                else:
                    df = df.sort_values(by="files", ascending=True)
                    if c_type == "Bar Chart": 
                        ax.bar(df["drive"], df["files"], color='#e67e22')
                        ax.set_ylabel("File Count")
                    elif c_type == "Line Chart": 
                        ax.plot(df["drive"], df["files"], marker='o', color='#e67e22')
                        ax.set_ylabel("File Count")
                    else: 
                        ax.barh(df["drive"], df["files"], color='#e67e22')
                        ax.set_xlabel("File Count")
                    ax.set_title("Total Number of Files by Drive")
                if c_type != "Horizontal Bar": 
                    ax.tick_params(axis='x', rotation=25)
                
            elif mode == 2 or mode == 3:
                if not data: return
                df = pd.DataFrame(data, columns=["ext", "count", "size"])
                if mode == 2:
                    df = df.sort_values(by="count", ascending=True).tail(15)
                    if c_type == "Bar Chart": ax.bar(df["ext"], df["count"], color='#2ecc71')
                    elif c_type == "Line Chart": ax.plot(df["ext"], df["count"], marker='s', color='#2ecc71')
                    else: ax.barh(df["ext"], df["count"], color='#2ecc71')
                    ax.set_title(f"Top 15 File Formats by Frequency ({target_drive})")
                else:
                    df = df.sort_values(by="size", ascending=True).tail(15)
                    if c_type == "Bar Chart": ax.bar(df["ext"], df["size"] / (1024**3), color='#9b59b6')
                    elif c_type == "Line Chart": ax.plot(df["ext"], df["size"] / (1024**3), marker='s', color='#9b59b6')
                    else: ax.barh(df["ext"], df["size"] / (1024**3), color='#9b59b6')
                    ax.set_title(f"Top 15 Formats by Storage Space ({target_drive})")
                if c_type != "Horizontal Bar": 
                    ax.tick_params(axis='x', rotation=45)
                
            elif mode == 4:
                bins = {'<1 MB': 0, '1-10 MB': 0, '10-100 MB': 0, '100MB-1GB': 0, '>1 GB': 0}
                for (sz,) in data:
                    if sz is None: continue
                    mb = sz / (1024 * 1024)
                    if mb < 1: bins['<1 MB'] += 1
                    elif mb < 10: bins['1-10 MB'] += 1
                    elif mb < 100: bins['10-100 MB'] += 1
                    elif mb < 1024: bins['100MB-1GB'] += 1
                    else: bins['>1 GB'] += 1
                if c_type == "Horizontal Bar": ax.barh(list(bins.keys()), list(bins.values()), color='#e74c3c')
                elif c_type == "Line Chart": ax.plot(list(bins.keys()), list(bins.values()), marker='o', color='#e74c3c')
                else: ax.bar(list(bins.keys()), list(bins.values()), color='#e74c3c')
                ax.set_title(f"File Size Distribution ({target_drive})")
                
            elif mode == 5:
                if not data: return
                valid_data = [(int(y), c) for y, c in data if y and str(y).isdigit() and 1980 < int(y) <= datetime.now().year + 1]
                if not valid_data: return
                df = pd.DataFrame(valid_data, columns=["year", "count"]).sort_values(by="year")
                if c_type == "Bar Chart": ax.bar(df["year"], df["count"], color='#f1c40f')
                else: 
                    ax.plot(df["year"], df["count"], marker='o', color='#f1c40f', linestyle='-', linewidth=2)
                    ax.fill_between(df["year"], df["count"], color='#f1c40f', alpha=0.2)
                ax.set_title(f"File Modification Timeline ({target_drive})")
                ax.grid(True, linestyle='--', alpha=0.3, color=spine_color)
                
            elif mode == 6:
                if not data: return
                df = pd.DataFrame(data, columns=["name", "size"]).sort_values(by="size", ascending=True)
                ax.barh(df["name"], df["size"] / (1024**3), color='#e84393')
                ax.set_xlabel("Size (GB)")
                ax.set_title(f"Top 20 Largest Files ({target_drive})")
                
            elif mode == 7:
                if not data: return
                valid_data = [(int(y), s / (1024**3)) for y, s in data if y and str(y).isdigit() and 1980 < int(y) <= datetime.now().year + 1]
                if not valid_data: return
                df = pd.DataFrame(valid_data, columns=["year", "size"]).sort_values(by="year")
                if c_type == "Bar Chart": ax.bar(df["year"], df["size"], color='#00cec9')
                else:
                    ax.plot(df["year"], df["size"], marker='s', color='#00cec9', linestyle='-', linewidth=2)
                    ax.fill_between(df["year"], df["size"], color='#00cec9', alpha=0.2)
                ax.set_title(f"Storage Usage by Year ({target_drive})")
                ax.grid(True, linestyle='--', alpha=0.3, color=spine_color)
                
            elif mode == 8:
                bins = {'<1 Month': 0, '1-6 Months': 0, '6m-1 Year': 0, '1-3 Years': 0, '>3 Years': 0}
                now = datetime.now()
                for (m,) in data:
                    try:
                        d = datetime.fromisoformat(m[:19])
                        days = (now - d).days
                        if days < 30: bins['<1 Month'] += 1
                        elif days < 180: bins['1-6 Months'] += 1
                        elif days < 365: bins['6m-1 Year'] += 1
                        elif days < 1095: bins['1-3 Years'] += 1
                        else: bins['>3 Years'] += 1
                    except: pass
                if c_type == "Horizontal Bar": ax.barh(list(bins.keys()), list(bins.values()), color='#8e44ad')
                elif c_type == "Line Chart": ax.plot(list(bins.keys()), list(bins.values()), marker='o', color='#8e44ad')
                else: ax.bar(list(bins.keys()), list(bins.values()), color='#8e44ad')
                ax.set_title(f"File Age Distribution ({target_drive})")
                
            elif mode == 9:
                if data == "NEED_DRIVE":
                    ax.text(0.5, 0.5, "Please select a specific Target Drive\nto calculate Overlap.", ha='center', va='center', color=text_color, fontsize=12)
                    ax.set_axis_off()
                elif not data:
                    ax.text(0.5, 0.5, f"No shared files found between\n'{target_drive}' and other drives.", ha='center', va='center', color=text_color, fontsize=12)
                    ax.set_axis_off()
                else:
                    df = pd.DataFrame(data, columns=["drive", "shared_count", "shared_size"]).sort_values(by="shared_size", ascending=True)
                    # Convert to Megabytes (MB) so smaller overlaps are clearly visible
                    df["shared_mb"] = df["shared_size"] / (1024 * 1024)
                    
                    colors = ['#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4', '#46f0f0', '#f032e6', '#bcf60c', '#fabebe']
                    c_list = [colors[i % len(colors)] for i in range(len(df))]
                    
                    if c_type == "Bar Chart":
                        ax.bar(df["drive"], df["shared_mb"], color=c_list)
                        ax.set_ylabel("Shared Size (MB)")
                    elif c_type == "Line Chart":
                        ax.plot(df["drive"], df["shared_mb"], marker='s', color='#3498db', linewidth=2)
                        ax.set_ylabel("Shared Size (MB)")
                    else:
                        ax.barh(df["drive"], df["shared_mb"], color=c_list)
                        ax.set_xlabel("Shared Size (MB)")
                        
                    ax.set_title(f"Drive Overlap: Data inside '{target_drive}' shared with others")
                    if c_type != "Horizontal Bar": 
                        ax.tick_params(axis='x', rotation=45)

            ax.tick_params(colors=text_color)
            ax.xaxis.label.set_color(text_color)
            ax.yaxis.label.set_color(text_color)
            ax.title.set_color(text_color)
            for spine in ax.spines.values(): 
                spine.set_edgecolor(spine_color)
            self.figure.tight_layout()
            self.canvas.draw()
        except Exception as e: 
            print(f"Chart error: {e}")

    # ---------- Timeline Diary Logic ----------
    def _get_diary_filters_sql(self) -> Tuple[str, list]:
        """Helper to build dynamic SQL based on UI filters (Drive, Category, Extension)"""
        drive = self.diary_drive.currentText()
        cat = self.diary_category.currentText()
        ext = self.diary_ext.text().strip().lower()

        sql = " modified != '' "
        params = []

        if drive != "Any Drive":
            sql += " AND drive = ?"
            params.append(drive)

        if cat != "All Files":
            cat_map = {
                "Images": "('.jpg','.jpeg','.png','.gif','.bmp','.webp','.ico','.tif','.tiff')",
                "Videos": "('.mp4','.avi','.mkv','.mov','.wmv','.flv','.webm')",
                "Documents": "('.txt','.doc','.docx','.pdf','.xls','.xlsx','.ppt','.pptx','.csv')",
                "Audio": "('.mp3','.wav','.aac','.ogg','.flac')",
                "Archives": "('.zip','.rar','.7z','.tar','.gz')",
                "Code/Scripts": "('.py','.js','.html','.css','.json','.cpp','.c','.java')"
            }
            if cat in cat_map:
                sql += f" AND extension IN {cat_map[cat]}"

        if ext:
            if not ext.startswith('.'): ext = '.' + ext
            sql += " AND extension = ?"
            params.append(ext)

        return sql, params

    def refresh_diary_data(self):
        cur = self.db.conn.cursor()
        base_sql, params = self._get_diary_filters_sql()

        # Update Active Years
        cur.execute(f"SELECT DISTINCT SUBSTR(modified, 1, 4) FROM files WHERE {base_sql} ORDER BY 1 DESC", params)
        years = [r[0] for r in cur.fetchall() if r[0] and r[0].isdigit()]
        
        self.diary_year.blockSignals(True)
        self.diary_year.clear()
        if years:
            self.diary_year.addItems(years)
        else:
            self.diary_year.addItem("No Data")
        self.diary_year.blockSignals(False)

        # Highlight Active Dates on Calendar
        cur.execute(f"SELECT DISTINCT SUBSTR(modified, 1, 10) FROM files WHERE {base_sql}", params)
        dates = [r[0] for r in cur.fetchall() if r[0]]

        self.activity_cal.setDateTextFormat(QDate(), QTextCharFormat())
        fmt = QTextCharFormat()
        bg_col = QColor(39, 174, 96, 150) if self.is_dark_mode else QColor(46, 204, 113, 100)
        fmt.setBackground(bg_col)
        fmt.setFontWeight(QFont.Bold)

        for d in dates:
            qdate = QDate.fromString(d, "yyyy-MM-dd")
            if qdate.isValid():
                self.activity_cal.setDateTextFormat(qdate, fmt)

        # Refresh the Chart 
        self.update_diary_chart(self.activity_cal.selectedDate().year(), self.activity_cal.selectedDate().month())
        
        # Clear the table so it starts empty and waits for user choice
        self._populate_diary_table([])
        self.diary_lbl.setText("<b>Select a date or click 'Load Full Timeline' to view events.</b>")

    def update_diary_chart(self, year=None, month=None):
        if not MATPLOTLIB_AVAILABLE or not hasattr(self, 'diary_figure'): return
        
        if year is None or month is None:
            year = self.activity_cal.selectedDate().year()
            month = self.activity_cal.selectedDate().month()

        month_str = f"{year}-{month:02d}"
        base_sql, params = self._get_diary_filters_sql()
        mode = self.diary_chart_mode.currentText()
        
        cur = self.db.conn.cursor()
        
        labels = []
        values = []
        title = f"Activity for {month_str}"
        ylabel = "Count"
        
        # 1. Daily Activity Logic
        if "Daily Activity" in mode:
            query = f"SELECT SUBSTR(modified, 9, 2) as day, COUNT(id), SUM(size) FROM files WHERE {base_sql} AND modified LIKE ? GROUP BY day"
            cur.execute(query, params + [f"{month_str}%"])
            data = cur.fetchall()
            labels = [str(int(r[0])) for r in data]
            values = [r[1] if "Count" in mode else (r[2] or 0)/(1024*1024) for r in data]
            ylabel = "Count" if "Count" in mode else "Size (MB)"
            title = f"Daily {ylabel} for {month_str}"
        
        # 2. Top Extensions Logic
        elif "By Extension" in mode:
            query = f"SELECT COALESCE(NULLIF(extension, ''), 'unknown') as ext, COUNT(id), SUM(size) FROM files WHERE {base_sql} AND modified LIKE ? GROUP BY ext ORDER BY COUNT(id) DESC LIMIT 10"
            cur.execute(query, params + [f"{month_str}%"])
            data = cur.fetchall()
            labels = [r[0] for r in data]
            values = [r[1] if "Count" in mode else (r[2] or 0)/(1024*1024) for r in data]
            ylabel = "Count" if "Count" in mode else "Size (MB)"
            title = f"Top Formats for {month_str}"
            
        # 3. Category Grouping Logic
        elif "By Category" in mode:
            query = f"SELECT extension, COUNT(id), SUM(size) FROM files WHERE {base_sql} AND modified LIKE ? GROUP BY extension"
            cur.execute(query, params + [f"{month_str}%"])
            data = cur.fetchall()
            
            cat_map = {
                "Images": {'.jpg','.jpeg','.png','.gif','.bmp','.webp','.ico','.tif','.tiff'},
                "Videos": {'.mp4','.avi','.mkv','.mov','.wmv','.flv','.webm'},
                "Docs": {'.txt','.doc','.docx','.pdf','.xls','.xlsx','.ppt','.pptx','.csv'},
                "Audio": {'.mp3','.wav','.aac','.ogg','.flac'},
                "Zips": {'.zip','.rar','.7z','.tar','.gz'},
                "Code": {'.py','.js','.html','.css','.json','.cpp','.c','.java'}
            }
            buckets = {k: 0 for k in cat_map.keys()}
            buckets["Others"] = 0
            
            for ext, cnt, sz in data:
                val = cnt if "Count" in mode else (sz or 0)/(1024*1024)
                placed = False
                for cat, exts in cat_map.items():
                    if ext and ext.lower() in exts:
                        buckets[cat] += val
                        placed = True
                        break
                if not placed:
                    buckets["Others"] += val
                    
            labels = [k for k, v in buckets.items() if v > 0]
            values = [v for v in buckets.values() if v > 0]
            ylabel = "Count" if "Count" in mode else "Size (MB)"
            title = f"Category {ylabel} for {month_str}"

        self.diary_figure.clear()
        bg_color = '#1e1e1e' if self.is_dark_mode else '#ffffff'
        text_color = 'white' if self.is_dark_mode else 'black'
        self.diary_figure.patch.set_facecolor(bg_color)
        
        ax = self.diary_figure.add_subplot(111)
        ax.set_facecolor(bg_color)

        if not values:
            ax.text(0.5, 0.5, f"No activity in {month_str}", ha='center', va='center', color=text_color)
            ax.set_axis_off()
        else:
            # Render vibrant bars based on mode
            colors = ['#ff9999','#66b3ff','#99ff99','#ffcc99','#c2c2f0','#ffb3e6', '#c4e17f', '#76D7C4', '#F7DC6F', '#85C1E9']
            c_list = [colors[i % len(colors)] for i in range(len(labels))]

            ax.bar(labels, values, color=c_list)
            ax.set_title(title, color=text_color, fontsize=10)
            ax.set_ylabel(ylabel, color=text_color, fontsize=8)
            ax.tick_params(colors=text_color, labelsize=8)
            
            if "Extension" in mode or "Category" in mode:
                ax.tick_params(axis='x', rotation=45)
                
            ax.spines['bottom'].set_color('#555' if self.is_dark_mode else '#ccc')
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['left'].set_color('#555' if self.is_dark_mode else '#ccc')

        self.diary_figure.tight_layout()
        self.diary_canvas.draw()

    def on_diary_year_changed(self):
        year = self.diary_year.currentText()
        if year and year.isdigit():
            self.activity_cal.setSelectedDate(QDate(int(year), 1, 1))

    def apply_diary_filter(self, text):
        model = self.diary_table.model()
        if model:
            model.set_filter(text)

    def on_diary_table_double_click(self, index: QModelIndex):
        model = self.diary_table.model()
        if not model: return
        data = model.data(model.index(index.row(), 1), Qt.UserRole)
        if not data: return
        typ, path = data
        if typ == "folder":
            self.tabs.setCurrentIndex(1) # Go to explorer
            self.load_directory(path)
            self._sync_tree_to_path(path)
        else:
            self.open_local_file("", self.diary_table, index.row())

    def on_diary_date_clicked(self, date: QDate):
        d_str = date.toString("yyyy-MM-dd")
        self.diary_lbl.setText(f"<b>Activity for {d_str}</b>")
        
        cur = self.db.conn.cursor()
        base_sql, params = self._get_diary_filters_sql()

        cur.execute(f"SELECT relpath, name, size, extension, modified, drive, fullpath, is_folder FROM files WHERE {base_sql} AND modified LIKE ? ORDER BY modified DESC", params + [f"{d_str}%"])
        self._populate_diary_table(cur.fetchall())

    def load_full_timeline(self):
        # Fetch the exact year and month currently visible on the calendar
        year = self.activity_cal.yearShown()
        month = self.activity_cal.monthShown()
        month_str = f"{year}-{month:02d}"

        self.diary_lbl.setText(f"<b>Activity Timeline for {month_str} (Filtered, Limited to {MAX_RENDER_ROWS})</b>")
        
        cur = self.db.conn.cursor()
        base_sql, params = self._get_diary_filters_sql()

        # Add the month filter (LIKE 'YYYY-MM%') directly to the SQL query
        query = f"""
            SELECT relpath, name, size, extension, modified, drive, fullpath, is_folder 
            FROM files 
            WHERE {base_sql} AND modified LIKE ? 
            ORDER BY modified DESC 
            LIMIT {MAX_RENDER_ROWS}
        """
        
        cur.execute(query, params + [f"{month_str}%"])
        self._populate_diary_table(cur.fetchall())

    def _populate_diary_table(self, rows):
        self.diary_table.setUpdatesEnabled(False)
        self.diary_table.setSortingEnabled(False)
        
        table_rows = []
        for r_idx, (rp, n, s, e, m, d, fp, is_f) in enumerate(rows):
            n_str = str(n) if n else ""
            if is_f and not n_str:
                clean_rp = str(rp).rstrip('/')
                n_str = clean_rp.split('/')[-1] if clean_rp else "/"
                
            m_str = str(m)
            dt_part = m_str[:10] if len(m_str) >= 10 else m_str
            tm_part = m_str[11:19] if len(m_str) >= 19 else ""

            table_rows.append({
                "display": [str(r_idx+1), dt_part, tm_part, n_str, str(d), human_size(s or 0), str(e) if e else "Folder", str(rp)],
                "sort_keys": [r_idx+1, (1, dt_part), (1, tm_part), (0 if is_f else 1, n_str.lower()), (0 if is_f else 1, str(d).lower()), (0 if is_f else 1, s or 0), (0 if is_f else 1, str(e).lower() if e else "folder"), (0 if is_f else 1, str(rp).lower())],
                "user_data": ("folder" if is_f else "file", str(rp)), 
                "user_data_1": str(fp) if fp else str(rp), 
                "user_data_2": bool(is_f), 
                "ext_meta": e, 
                "is_folder_meta": bool(is_f)
            })
            
        headers = ["S.No", "Date", "Time", "Name", "Drive", "Size", "Type", "RelPath"]
        model = FastTableModel(headers, table_rows, self._get_icon)
        self.diary_table.setModel(model)
        
        cw = self.diary_table.setColumnWidth
        cw(0, 50); cw(1, 90); cw(2, 80); cw(3, 250); cw(4, 100); cw(5, 80); cw(6, 80)
        self.diary_table.setSortingEnabled(True)
        self.diary_table.setUpdatesEnabled(True)

    # ---------- Global Explorer ----------
    def refresh_folder_tree(self):
        self.folder_tree.clear()
        root = QTreeWidgetItem(self.folder_tree, ["/ (Home)"])
        root.setData(0, Qt.UserRole, "")
        root.setIcon(0, self.folder_icon)
        root.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        root.setExpanded(True)

    def _get_subfolders(self, prefix: str) -> List[str]:
        cur = self.db.conn.cursor()
        target_drive = self.ex_drive.currentText()
        drive_filter_sql = "" if target_drive == "Any Drive" else f"AND drive = '{target_drive}'"
        
        if not prefix: 
            cur.execute(f"SELECT DISTINCT SUBSTR(relpath, 1, INSTR(relpath, '/') - 1) FROM files WHERE INSTR(relpath, '/') > 0 {drive_filter_sql}")
        else:
            plen = len(prefix) + 1
            cur.execute(f"SELECT DISTINCT SUBSTR(SUBSTR(relpath, {plen}), 1, INSTR(SUBSTR(relpath, {plen}), '/') - 1) FROM files WHERE relpath LIKE ? AND INSTR(SUBSTR(relpath, {plen}), '/') > 0 {drive_filter_sql}", (f"{prefix}%",))
        return sorted([str(r[0]) for r in cur.fetchall() if r[0]])

    def on_folder_expand(self, item: QTreeWidgetItem):
        if item.data(0, Qt.UserRole + 1): 
            return
        prefix = item.data(0, Qt.UserRole)
        for sf in self._get_subfolders(prefix):
            child = QTreeWidgetItem(item, [sf])
            child.setData(0, Qt.UserRole, f"{prefix}{sf}/")
            child.setIcon(0, self.folder_icon)
            child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        item.setData(0, Qt.UserRole + 1, True)

    def on_folder_click(self, item: QTreeWidgetItem, col: int):
        self.load_directory(item.data(0, Qt.UserRole))

    def load_directory(self, prefix: str):
        self.current_explorer_prefix = prefix
        self.address_bar.setText(prefix if prefix else "/")
        
        cur = self.db.conn.cursor()
        target_drive = self.ex_drive.currentText()
        drive_filter_sql = "" if target_drive == "Any Drive" else f"AND drive = '{target_drive}'"
        
        folder_stats = {}
        if prefix:
            plen = len(prefix) + 1
            query = f"""SELECT SUBSTR(relpath, {plen}, INSTR(SUBSTR(relpath, {plen}), '/') - 1) AS subfolder, COUNT(id) AS total_items, SUM(size) AS total_size FROM files WHERE relpath LIKE ? AND INSTR(SUBSTR(relpath, {plen}), '/') > 0 {drive_filter_sql} GROUP BY subfolder"""
            cur.execute(query, (f"{prefix}%",))
        else:
            query = f"""SELECT SUBSTR(relpath, 1, INSTR(relpath, '/') - 1) AS subfolder, COUNT(id) AS total_items, SUM(size) AS total_size FROM files WHERE INSTR(relpath, '/') > 0 {drive_filter_sql} GROUP BY subfolder"""
            cur.execute(query)
            
        for sf, t_items, t_size in cur.fetchall():
            folder_stats[sf] = {"items": t_items, "size": t_size or 0}

        if not prefix: 
            cur.execute(f"SELECT relpath, name, size, extension, modified, drive, sha, fullpath FROM files WHERE INSTR(relpath, '/') = 0 AND is_folder = 0 {drive_filter_sql} LIMIT {MAX_RENDER_ROWS}")
        else:
            plen = len(prefix) + 1
            cur.execute(f"SELECT relpath, name, size, extension, modified, drive, sha, fullpath FROM files WHERE relpath LIKE ? AND INSTR(SUBSTR(relpath, {plen}), '/') = 0 AND is_folder = 0 {drive_filter_sql} LIMIT {MAX_RENDER_ROWS}", (f"{prefix}%",))
            
        self._populate_file_table(folder_stats, cur.fetchall(), prefix)

    def navigate_up(self):
        curr = self.current_explorer_prefix.strip("/")
        if not curr: return
        parts = curr.split("/")
        new_prefix = "/".join(parts[:-1]) + "/" if len(parts) > 1 else ""
        self.load_directory(new_prefix)
        self._sync_tree_to_path(new_prefix)

    def _sync_tree_to_path(self, path: str):
        if not path:
            self.folder_tree.clearSelection()
            root = self.folder_tree.topLevelItem(0)
            if root: root.setSelected(True)
            return
            
        parts = path.strip("/").split("/")
        current_item = self.folder_tree.topLevelItem(0)
        current_prefix = ""
        for part in parts:
            if not current_item.isExpanded(): 
                current_item.setExpanded(True)
                self.on_folder_expand(current_item)
            current_prefix += part + "/"
            found = False
            for i in range(current_item.childCount()):
                child = current_item.child(i)
                if child.data(0, Qt.UserRole) == current_prefix: 
                    current_item = child
                    found = True
                    break
            if not found: break
                
        if current_item: 
            self.folder_tree.clearSelection()
            current_item.setSelected(True)
            self.folder_tree.scrollToItem(current_item)

    def ex_filter_changed(self, text):
        model = self.file_table.model()
        if not model: return
        model.set_filter(text)

    def explorer_global_search(self):
        txt = self.ex_search.text().strip()
        if not txt: 
            return self.clear_explorer_search()
            
        self.folder_tree.clearSelection()
        target_drive = self.ex_drive.currentText()
        drive_filter_sql = "" if target_drive == "Any Drive" else f"AND drive = '{target_drive}'"
        
        self.search_dlg = QProgressDialog("Quick Searching... Please wait.", "Cancel", 0, 0, self)
        self.search_dlg.setWindowModality(Qt.WindowModal)
        self.search_dlg.show()
        
        query = f"SELECT relpath, name, size, extension, modified, drive, sha, fullpath FROM files WHERE (name LIKE ? OR relpath LIKE ?) AND is_folder = 0 {drive_filter_sql} LIMIT {MAX_RENDER_ROWS}"
        self.search_thread = SearchThread(DB_FILE, query, (f"%{txt}%", f"%{txt}%"), self)
        self.search_thread.finished.connect(lambda rows, t=txt: self._on_quick_search_done(rows, t))
        self.search_thread.error.connect(self._on_search_error)
        self.search_dlg.canceled.connect(self.search_thread.cancel)
        self.search_thread.start()

    def _on_quick_search_done(self, rows, txt):
        if self.search_dlg: 
            self.search_dlg.close()
            
        if len(rows) == MAX_RENDER_ROWS: 
            self.status.showMessage(f"Showing Top {MAX_RENDER_ROWS} Search Results.", 5000)
        else: 
            self.status.showMessage(f"Search Complete: Found {len(rows)} files.", 5000)
            
        self.address_bar.setText(f"Search Results for: '{txt}'")
        self._populate_file_table({}, rows, "", is_search=True)
        self.preview_image.clear()

    def _on_search_error(self, err_msg):
        if self.search_dlg: 
            self.search_dlg.close()
        QMessageBox.critical(self, "Search Error", f"An error occurred during search:\n{err_msg}")

    def clear_explorer_search(self):
        self.ex_search.clear()
        self.load_directory(self.current_explorer_prefix)

    def _populate_file_table(self, folder_stats_dict, files, current_prefix, is_search=False):
        self.file_table.setUpdatesEnabled(False)
        self.file_table.setSortingEnabled(False)
        cur = self.db.conn.cursor()
        
        shas = tuple(set(r[6] for r in files if r[6]))
        names_sizes = tuple(set((r[1], r[2]) for r in files if not r[6]))
        
        sha_counts = {}
        if shas:
            for i in range(0, len(shas), 900):
                chunk = shas[i:i+900]
                pl = ",".join("?" * len(chunk))
                cur.execute(f"SELECT sha, COUNT(DISTINCT drive) FROM files WHERE sha IN ({pl}) GROUP BY sha", chunk)
                sha_counts.update(dict(cur.fetchall()))
                
        ns_counts = {}
        if names_sizes:
            names_chunk = tuple(set(n for n, s in names_sizes))
            for i in range(0, len(names_chunk), 900):
                chunk = names_chunk[i:i+900]
                pl = ",".join("?" * len(chunk))
                cur.execute(f"SELECT name, size, COUNT(DISTINCT drive) FROM files WHERE is_folder=0 AND name IN ({pl}) GROUP BY name, size", chunk)
                for n, s, c in cur.fetchall(): 
                    ns_counts[(n, s)] = c

        rows = []
        row_idx = 1
        
        for f_name, stats in sorted(folder_stats_dict.items()):
            folder_rel = f"{current_prefix}{f_name}/"
            
            cur.execute("SELECT fullpath FROM files WHERE relpath LIKE ? AND fullpath != '' LIMIT 1", (f"{folder_rel}%",))
            r = cur.fetchone()
            sample_real = os.path.dirname(r[0]) if r and r[0] else ""
            
            rows.append({
                "display": [str(row_idx), f_name, str(stats["items"]), human_size(stats["size"]), "File folder", "", ""],
                "sort_keys": [row_idx, (0, f_name.lower()), (0, stats["items"]), (0, stats["size"]), (0, "file folder"), (0, ""), (0, 0)],
                "user_data": ("folder", folder_rel), "user_data_1": sample_real, "user_data_2": True, "ext_meta": "", "is_folder_meta": True
            })
            row_idx += 1
            
        for rp, name, size, ext, mod, drive, sha, fp in files:
            name_disp = rp if is_search else name
            ext_str = ext or "file"
            if sha: 
                global_c = sha_counts.get(sha, 1)
            else: 
                global_c = ns_counts.get((name, size), 1)
            
            rows.append({
                "display": [str(row_idx), name_disp, "-", human_size(size), ext_str, mod, str(global_c)],
                "sort_keys": [row_idx, (1, name_disp.lower()), (1, -1), (1, size), (1, ext_str.lower()), (1, mod), (1, global_c)],
                "user_data": ("file", rp), "user_data_1": fp, "user_data_2": False, "ext_meta": ext_str, "is_folder_meta": False
            })
            row_idx += 1
            
        headers = ["S.No", "Name", "Total Items", "Total Size", "Type", "Modified", "Global Copies"]
        model = FastTableModel(headers, rows, self._get_icon)
        self.file_table.setModel(model)
        
        cw = self.file_table.setColumnWidth
        cw(0, 50); cw(1, 400); cw(2, 90); cw(3, 90)
        cw(4, 90); cw(5, 140); cw(6, 100)
        self.file_table.setSortingEnabled(True)
        self.file_table.setUpdatesEnabled(True)

    def on_multi_select_update(self, indexes):
        if not indexes:
            self.selected_label.setText("0 items selected")
            return
            
        model = self.file_table.model()
        total_size = 0
        items_count = len(indexes)
        
        for idx in indexes:
            try:
                row_data = model.filtered_rows[idx.row()]
                actual_size = row_data["sort_keys"][3][1]
                if isinstance(actual_size, (int, float)) and actual_size > 0:
                    total_size += actual_size
            except Exception: pass
            
        self.selected_label.setText(f"{items_count} items selected ({human_size(total_size)})")

    def on_ms_multi_select_update(self, indexes):
        if not indexes:
            self.selected_label.setText("0 items selected")
            return
            
        model = self.ms_file_table.model()
        total_size = 0
        items_count = len(indexes)
        
        for idx in indexes:
            try:
                row_data = model.filtered_rows[idx.row()]
                actual_size = row_data["sort_keys"][2][1]
                if isinstance(actual_size, (int, float)) and actual_size > 0:
                    total_size += actual_size
            except Exception: pass
            
        self.selected_label.setText(f"{items_count} items selected ({human_size(total_size)})")

    def on_table_double_click(self, index: QModelIndex):
        model = self.file_table.model()
        if not model: return
        data = model.data(model.index(index.row(), 1), Qt.UserRole)
        if not data: return
        typ, path = data
        if typ == "folder": 
            self.load_directory(path)
            self._sync_tree_to_path(path)
        else: 
            self.open_local_file("", self.file_table, index.row())

    def _get_global_file_info(self, name, size, sha=None):
        cur = self.db.conn.cursor()
        if sha: 
            cur.execute("SELECT drive, fullpath, relpath, size, sha FROM files WHERE sha = ?;", (sha,))
        else: 
            cur.execute("SELECT drive, fullpath, relpath, size, sha FROM files WHERE name = ? AND size = ?;", (name, size))
        return cur.fetchall()

    def on_file_click(self, index: QModelIndex):
        model = self.file_table.model()
        if not model: return
        data = model.data(model.index(index.row(), 1), Qt.UserRole)
        if not data: return
        typ, payload = data
        row_data = model.filtered_rows[index.row()]
        
        self.preview_image.clear()
        if typ == "folder": 
            self.preview_text.setHtml(self._format_preview_html(row_data["display"], model.headers, f"Folder: {row_data['display'][1]}", payload, []))
            return
            
        cur = self.db.conn.cursor()
        cur.execute("SELECT name, size, sha FROM files WHERE relpath = ? LIMIT 1;", (payload,))
        target_file = cur.fetchone()
        
        rows = []
        if target_file:
            n, s, sha = target_file
            rows = self._get_global_file_info(n, s, sha)

        if not rows: return
        
        html = self._format_preview_html(row_data["display"], model.headers, f"File: {row_data['display'][1]}", payload, rows)
        self.preview_text.setHtml(html)
        
        sample = None
        for d, full, rel, size, sha in rows:
            if not sample and full and os.path.exists(full) and os.path.splitext(full)[1].lower() in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".ico"): 
                sample = full
                
        if sample: 
            loader = ImageLoader(sample, parent=self)
            loader.finished.connect(self.on_image_loaded)
            self._register_worker(loader)
            loader.start()

    def show_properties(self, relpath: str, is_folder: bool):
        cur = self.db.conn.cursor()
        dlg = QDialog(self)
        dlg.setWindowTitle("Properties")
        dlg.setMinimumWidth(400)
        layout = QFormLayout(dlg)
        
        if is_folder:
            plen = len(relpath)
            cur.execute(f"SELECT COUNT(id), SUM(size) FROM files WHERE relpath LIKE ? AND INSTR(SUBSTR(relpath, {plen}), '/') > 0", (f"{relpath}%",))
            cnt, sz = cur.fetchone()
            layout.addRow("Type:", QLabel("File Folder"))
            layout.addRow("Location:", QLabel(relpath))
            layout.addRow("Total Items:", QLabel(str(cnt or 0)))
            layout.addRow("Total Size:", QLabel(human_size(sz or 0)))
        else:
            cur.execute("SELECT name, size, extension, modified, fullpath, sha FROM files WHERE relpath = ? LIMIT 1", (relpath,))
            r = cur.fetchone()
            if not r: return
            n, s, e, m, fp, sha = r
            layout.addRow("File Name:", QLabel(n))
            layout.addRow("Path:", QLabel(relpath))
            layout.addRow("Local Target:", QLabel(fp if fp else "Disconnected"))
            layout.addRow("Size:", QLabel(human_size(s)))
            layout.addRow("Extension:", QLabel(e))
            layout.addRow("Modified:", QLabel(m))
            if sha: 
                layout.addRow("SHA-256:", QLineEdit(sha)) 
        dlg.exec()

    def copy_to_real_location(self, relpath: str, is_folder: bool):
        cur = self.db.conn.cursor()
        if is_folder: 
            cur.execute("SELECT fullpath FROM files WHERE relpath = ? AND is_folder = 1 AND fullpath != ''", (relpath,))
        else: 
            cur.execute("SELECT fullpath FROM files WHERE relpath = ? AND is_folder = 0 AND fullpath != ''", (relpath,))
            
        paths = [r[0] for r in cur.fetchall() if r[0] and os.path.exists(r[0])]
        
        if not paths and is_folder:
            cur.execute("SELECT fullpath FROM files WHERE relpath LIKE ? AND fullpath != '' LIMIT 1", (f"{relpath}%",))
            row = cur.fetchone()
            if row and row[0]:
                test_dir = os.path.dirname(row[0])
                if os.path.exists(test_dir): 
                    paths.append(test_dir)
                
        if not paths: 
            return QMessageBox.warning(self, "Not Found", "Item does not exist locally to copy.")
            
        src = paths[0]
        dst = QFileDialog.getExistingDirectory(self, "Select Destination Directory to Copy To")
        if not dst: return
        
        t = CopyThread(src, dst, is_folder, parent=self)
        t.progress.connect(self.status.showMessage)
        t.finished.connect(lambda: QMessageBox.information(self, "Done", "Copy operation completed."))
        t.error.connect(lambda e: QMessageBox.critical(self, "Error", f"Failed: {e}"))
        self._register_worker(t)
        t.start()


    def file_context_menu(self, pos, table_source):
            idx = table_source.indexAt(pos)
            if not idx.isValid(): return
            
            model = table_source.model()
            sel_rows = table_source.selectionModel().selectedRows()
            menu = QMenu(self)
            
            if len(sel_rows) == 1:
                data = model.data(model.index(sel_rows[0].row(), 1), Qt.UserRole)
                if data:
                    typ, relpath = data
                    
                    # FIX: Extract the actual physical path from user_data_1
                    fullpath = model.data(model.index(sel_rows[0].row(), 1), Qt.UserRole + 1)
                    
                    if typ == "file":
                        act_open = QAction("Open Local File (System Default)", self)
                        # FIX: Pass the 'fullpath' variable via lambda
                        act_open.triggered.connect(lambda checked=False, fp=fullpath: self.open_local_file_system(fp))
                        menu.addAction(act_open)
                        
                        act_open_int = QAction("Open in Built-in Viewer", self)
                        act_open_int.triggered.connect(lambda: self.open_local_file(relpath, table_source, sel_rows[0].row()))
                        menu.addAction(act_open_int)
                        
                        act_open_loc = QAction("Open Physical Location", self)
                        # FIX: Pass the 'fullpath' variable via lambda
                        act_open_loc.triggered.connect(lambda checked=False, fp=fullpath: self.open_file_location(fp))
                        menu.addAction(act_open_loc)
                    
                    act_copy_real = QAction("Copy to Real Location...", self)
                    act_copy_real.triggered.connect(lambda: self.copy_to_real_location(relpath, typ=="folder"))
                    menu.addAction(act_copy_real)
                    
                    act_copy_path = QAction("Copy Relative Path", self)
                    act_copy_path.triggered.connect(lambda: QApplication.clipboard().setText(relpath))
                    menu.addAction(act_copy_path)
                    
                    act_prop = QAction("Properties", self)
                    act_prop.triggered.connect(lambda: self.show_properties(relpath, typ=="folder"))
                    menu.addAction(act_prop)
                    
                    menu.addSeparator()
                    
            act_add_ms = QAction(f"⭐ Add {len(sel_rows)} Selected to MySpace Sandbox", self)
            act_add_ms.triggered.connect(lambda: self.add_selected_to_myspace(table_source=table_source))
            menu.addAction(act_add_ms)
            menu.exec(table_source.viewport().mapToGlobal(pos))




    def on_image_loaded(self, path, pix):
        if isinstance(pix, QPixmap) and not pix.isNull(): 
            self.preview_image.setPixmap(pix)

    def on_ms_image_loaded(self, path, pix):
        if isinstance(pix, QPixmap) and not pix.isNull(): 
            self.ms_preview_image.setPixmap(pix)

    def open_local_file_system(self, file_path: str):
        if os.path.exists(file_path):
            try: 
                os.startfile(file_path) if sys.platform=="win32" else os.system(f"open '{file_path}'" if sys.platform=="darwin" else f"xdg-open '{file_path}'")
            except Exception as e: 
                QMessageBox.warning(self, "Open", str(e))
            return
            
        QMessageBox.information(self, "Open", "No accessible path for this file on the local machine.")

    def open_local_file(self, file_path: str, table_source, row_idx: int):
        # Initialize an array to safely hold active viewers if it doesn't exist
        if not hasattr(self, 'active_viewers'):
            self.active_viewers = []
            
        viewer = InternalViewer(table_source, row_idx, self)
        self.active_viewers.append(viewer)
        
        # CRITICAL: show() instead of exec() allows the player to run in the background
        viewer.show()

    def open_file_location(self, file_path: str):
        if os.path.exists(file_path):
            try:
                norm_path = os.path.normpath(file_path)
                if sys.platform == "win32":
                    import subprocess
                    subprocess.Popen(['explorer', '/select,', norm_path])
                elif sys.platform == "darwin":
                    import subprocess
                    subprocess.Popen(["open", "-R", norm_path])
                else: 
                    os.system(f"xdg-open '{os.path.dirname(norm_path)}'")
            except Exception as e: 
                QMessageBox.warning(self, "Open Location", str(e))
            return
            
        QMessageBox.warning(self, "Not Found", "Item does not exist locally.")


    # ---------- MySpace Sandbox ----------
    def _ensure_ms_folder(self, base_path, folder_name) -> bool:
        cur = self.db.conn.cursor()
        cur.execute("SELECT id FROM myspace WHERE parent_path=? AND name=? AND is_folder=1", (base_path, folder_name))
        if not cur.fetchone():
            cur.execute("INSERT INTO myspace (parent_path, name, is_folder) VALUES (?,?,1)", (base_path, folder_name))
            return True
        return False

    def refresh_myspace_tree(self):
        self.ms_folder_tree.clear()
        root = QTreeWidgetItem(self.ms_folder_tree, ["/ (Sandbox Root)"])
        root.setData(0, Qt.UserRole, "/")
        root.setIcon(0, self.folder_icon)
        root.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        root.setExpanded(True)

    def _get_ms_subfolders(self, parent_path: str) -> List[Tuple[str, int]]:
        cur = self.db.conn.cursor()
        cur.execute("SELECT name, id FROM myspace WHERE parent_path = ? AND is_folder = 1 ORDER BY name", (parent_path,))
        return [(str(r[0]), r[1]) for r in cur.fetchall() if r[0]]

    def on_ms_folder_expand(self, item: QTreeWidgetItem):
        if item.data(0, Qt.UserRole + 1): return
        parent_path = item.data(0, Qt.UserRole)
        for sf, _ in self._get_ms_subfolders(parent_path):
            child = QTreeWidgetItem(item, [sf])
            child.setData(0, Qt.UserRole, f"{parent_path}{sf}/")
            child.setIcon(0, self.folder_icon)
            child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        item.setData(0, Qt.UserRole + 1, True)

    def on_ms_folder_click(self, item: QTreeWidgetItem, col: int):
        self.load_myspace_directory(item.data(0, Qt.UserRole))

    def get_sandbox_folder_size(self, folder_path: str) -> int:
        cur = self.db.conn.cursor()
        cur.execute("SELECT SUM(size) FROM myspace WHERE parent_path LIKE ? AND is_folder = 0", (f"{folder_path}%",))
        res = cur.fetchone()
        return res[0] if res and res[0] else 0

    def load_myspace_directory(self, parent_path: str):
        self.ms_file_table.setUpdatesEnabled(False)
        self.ms_file_table.setSortingEnabled(False)
        self.current_myspace_prefix = parent_path
        self.ms_address_bar.setText(parent_path)
        self.ms_search.clear()
        
        folders = self._get_ms_subfolders(parent_path)
        cur = self.db.conn.cursor()
        cur.execute("SELECT id, name, size, extension, real_path, modified FROM myspace WHERE parent_path = ? AND is_folder = 0", (parent_path,))
        files = cur.fetchall()
        
        shas = []
        names_sizes = []
        for r in files:
            names_sizes.append((r[1], r[2]))
            
        ns_counts = {}
        if names_sizes:
            names_chunk = tuple(set(n for n, s in names_sizes))
            for i in range(0, len(names_chunk), 900):
                chunk = names_chunk[i:i+900]
                pl = ",".join("?" * len(chunk))
                cur.execute(f"SELECT name, size, COUNT(DISTINCT drive) FROM files WHERE is_folder=0 AND name IN ({pl}) GROUP BY name, size", chunk)
                for n, s, c in cur.fetchall(): 
                    ns_counts[(n, s)] = c

        rows = []
        row_idx = 1
        for f, f_id in folders:
            sz = self.get_sandbox_folder_size(f"{parent_path}{f}/")
            name_str = f
            if f_id in self.sb_clip_ids:
                name_str = f"[CUT] {f}" if self.sb_clip_mode == "cut" else f"[COPIED] {f}"

            rows.append({
                "display": [str(row_idx), name_str, human_size(sz), "Virtual Folder", "Inside Sandbox", ""],
                "sort_keys": [row_idx, (0, f.lower()), (0, sz), (0, "virtual folder"), (0, ""), (0, 0)],
                "user_data": ("folder", f"{parent_path}{f}/", f_id), "user_data_1": "", "user_data_2": True, "ext_meta": "", "is_folder_meta": True
            })
            row_idx += 1
            
        for db_id, n, s, ext, rp, mod in files:
            s_val = s if s else 0
            global_c = ns_counts.get((n, s_val), 1)
            name_str = n
            if db_id in self.sb_clip_ids:
                name_str = f"[CUT] {n}" if self.sb_clip_mode == "cut" else f"[COPIED] {n}"
                
            rows.append({
                "display": [str(row_idx), name_str, human_size(s_val), str(ext) if ext else "file", str(rp) if rp else "", str(global_c)],
                "sort_keys": [row_idx, (1, str(n).lower()), (1, s_val), (1, str(ext).lower() if ext else ""), (1, str(rp).lower() if rp else ""), (1, global_c)],
                "user_data": ("file", str(rp), db_id), "user_data_1": str(rp), "user_data_2": False, "ext_meta": ext, "is_folder_meta": False
            })
            row_idx += 1
            
        model = FastTableModel(["S.No", "Name", "Size", "Type", "Real Target Source", "Global Copies"], rows, self._get_icon)
        self.ms_file_table.setModel(model)
        self.ms_file_table.setColumnWidth(0, 60)
        self.ms_file_table.setColumnWidth(1, 350)
        self.ms_file_table.setColumnWidth(2, 100)
        self.ms_file_table.setColumnWidth(3, 100)
        self.ms_file_table.setSortingEnabled(True)
        self.ms_file_table.setUpdatesEnabled(True)

    def ms_search_changed(self, text):
        model = self.ms_file_table.model()
        if not model: return
        model.set_filter(text)

    def on_sandbox_files_dropped(self, paths):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            cur = self.db.conn.cursor()
            base_dest = self.current_myspace_prefix
            added_files = 0
            added_folders = 0

            for p in paths:
                if os.path.isdir(p):
                    folder_name = os.path.basename(p)
                    self._ensure_ms_folder(base_dest, folder_name)
                    added_folders += 1
                    for root, dirs, files in os.walk(p):
                        rel_root = os.path.relpath(root, p).replace("\\", "/")
                        if rel_root == ".": 
                            curr_parent = base_dest + folder_name + "/"
                        else: 
                            curr_parent = base_dest + folder_name + "/" + rel_root + "/"
                        for d in dirs: 
                            self._ensure_ms_folder(curr_parent, d)
                            added_folders += 1
                        for f in files:
                            fp = os.path.join(root, f)
                            sz = os.path.getsize(fp)
                            mod = datetime.fromtimestamp(os.path.getmtime(fp)).isoformat()
                            ext = os.path.splitext(f)[1].lower()
                            cur.execute("INSERT INTO myspace (parent_path, name, is_folder, real_path, size, extension, modified) VALUES (?, ?, 0, ?, ?, ?, ?)", (curr_parent, f, fp, sz, ext, mod))
                            added_files += 1
                else:
                    f = os.path.basename(p)
                    sz = os.path.getsize(p)
                    mod = datetime.fromtimestamp(os.path.getmtime(p)).isoformat()
                    ext = os.path.splitext(f)[1].lower()
                    cur.execute("INSERT INTO myspace (parent_path, name, is_folder, real_path, size, extension, modified) VALUES (?, ?, 0, ?, ?, ?, ?)", (base_dest, f, p, sz, ext, mod))
                    added_files += 1
            self.db.conn.commit()
        finally: 
            QApplication.restoreOverrideCursor()
        self.load_myspace_directory(self.current_myspace_prefix)
        self.refresh_myspace_tree()
        self.status.showMessage(f"Dropped: {added_files} files, {added_folders} folders.", 5000)

    def ms_navigate_up(self):
        curr = self.current_myspace_prefix
        if curr == "/": return
        parts = curr.strip("/").split("/")
        if len(parts) <= 1: 
            new_prefix = "/"
        else: 
            new_prefix = "/" + "/".join(parts[:-1]) + "/"
        
        self.load_myspace_directory(new_prefix)
        self.ms_folder_tree.clearSelection()
        if new_prefix == "/":
            root = self.ms_folder_tree.topLevelItem(0)
            if root: root.setSelected(True)
            return

        parts = new_prefix.strip("/").split("/")
        current_item = self.ms_folder_tree.topLevelItem(0)
        current_prefix = "/"
        for part in parts:
            if not current_item.isExpanded(): 
                current_item.setExpanded(True)
                self.on_ms_folder_expand(current_item)
            current_prefix += part + "/"
            for i in range(current_item.childCount()):
                child = current_item.child(i)
                if child.data(0, Qt.UserRole) == current_prefix: 
                    current_item = child
                    break
        if current_item: 
            current_item.setSelected(True)
            self.ms_folder_tree.scrollToItem(current_item)

    def on_ms_file_click(self, index: QModelIndex):
        model = self.ms_file_table.model()
        if not model: return
        data = model.data(model.index(index.row(), 1), Qt.UserRole)
        if not data: return
        typ, path, db_id = data
        row_data = model.filtered_rows[index.row()]
        
        self.ms_preview_image.clear()
        if typ == "folder": 
            self.ms_preview_text.setHtml(self._format_preview_html(row_data["display"], model.headers, f"Virtual Folder: {row_data['display'][1]}", path, []))
            return
            
        cur = self.db.conn.cursor()
        cur.execute("SELECT real_path, name, size, extension, modified FROM myspace WHERE id = ?", (db_id,))
        row = cur.fetchone()
        if not row: return
        real_path, n, size, ext, mod = row
        
        rows = self._get_global_file_info(n, size)
        
        html = self._format_preview_html(row_data["display"], model.headers, f"Sandbox File: {row_data['display'][1]}", self.current_myspace_prefix, rows)
        self.ms_preview_text.setHtml(html)
        
        if real_path and os.path.exists(real_path) and str(ext).lower() in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".ico"):
            loader = ImageLoader(real_path, parent=self)
            loader.finished.connect(self.on_ms_image_loaded)
            self._register_worker(loader)
            loader.start()

    def on_ms_table_double_click(self, index: QModelIndex):
        model = self.ms_file_table.model()
        if not model: return
        data = model.data(model.index(index.row(), 1), Qt.UserRole)
        if not data: return
        typ, path, db_id = data
        if typ == "folder":
            self.load_myspace_directory(path)
            parts = path.strip("/").split("/")
            current_item = self.ms_folder_tree.topLevelItem(0)
            current_prefix = "/"
            for part in parts:
                if not current_item.isExpanded(): 
                    current_item.setExpanded(True)
                    self.on_ms_folder_expand(current_item)
                current_prefix += part + "/"
                for i in range(current_item.childCount()):
                    child = current_item.child(i)
                    if child.data(0, Qt.UserRole) == current_prefix: 
                        current_item = child
                        break
            if current_item: 
                self.ms_folder_tree.clearSelection()
                current_item.setSelected(True)
        else:
            self.open_local_file("", self.ms_file_table, index.row())

    def ms_context_menu(self, pos):
        idx = self.ms_file_table.indexAt(pos)
        menu = QMenu(self)
        
        # 1. Global Actions (Always available, even when clicking empty space)
        act_new_folder = QAction("Create New Virtual Folder", self)
        act_new_folder.triggered.connect(self.ms_create_folder)
        menu.addAction(act_new_folder)
        menu.addSeparator()
        
        act_paste = QAction("Paste here", self)
        act_paste.triggered.connect(self.ms_paste)
        if not self.sb_clip_ids: act_paste.setEnabled(False)
        menu.addAction(act_paste)
        
        # 2. Item-Specific Actions (Only appear if you actually clicked a file/folder)
        if idx.isValid():
            sel_rows = self.ms_file_table.selectionModel().selectedRows()
            if sel_rows:
                menu.addSeparator()
                act_copy = QAction("Copy", self)
                act_copy.triggered.connect(self.ms_copy)
                act_cut = QAction("Cut", self)
                act_cut.triggered.connect(self.ms_cut)
                act_del = QAction("Remove from Sandbox", self)
                act_del.triggered.connect(self.ms_delete_selected_shortcut)
                
                menu.addAction(act_copy)
                menu.addAction(act_cut)
                menu.addAction(act_del)
                
                if len(sel_rows) == 1:
                    model = self.ms_file_table.model()
                    typ, path, db_id = model.data(model.index(sel_rows[0].row(), 1), Qt.UserRole)
                    
                    act_rename = QAction("Rename in Sandbox", self)
                    act_rename.triggered.connect(lambda: self.ms_rename_item(typ, path, db_id, sel_rows[0].row()))
                    menu.addAction(act_rename)
                    
                    act_move = QAction("Move to Virtual Folder...", self)
                    act_move.triggered.connect(lambda: self.ms_move_item(typ, path, db_id, sel_rows[0].row()))
                    menu.addAction(act_move)
                    
                    menu.addSeparator()
                    
                    if typ == "file":
                        act_open = QAction("Open Local File (System Default)", self)
                        act_open.triggered.connect(lambda: self.ms_open_local_file_system(db_id))
                        menu.addAction(act_open)
                        
                        act_open_int = QAction("Open in Built-in Viewer", self)
                        act_open_int.triggered.connect(lambda: self.on_ms_table_double_click(sel_rows[0]))
                        menu.addAction(act_open_int)
                        
                        act_open_loc = QAction("Open Physical Location", self)
                        act_open_loc.triggered.connect(lambda: self.ms_open_physical_location(db_id))
                        menu.addAction(act_open_loc)
                    
                    act_real = QAction("Copy to Real Path on Disk...", self)
                    act_real.triggered.connect(lambda: self.ms_copy_to_real_location(db_id, typ, path))
                    menu.addAction(act_real)
                    
        menu.exec(self.ms_file_table.viewport().mapToGlobal(pos))

    def ms_open_local_file_system(self, db_id):
        cur = self.db.conn.cursor()
        cur.execute("SELECT real_path FROM myspace WHERE id = ?", (db_id,))
        row = cur.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            try: 
                os.startfile(row[0]) if sys.platform=="win32" else os.system(f"open '{row[0]}'" if sys.platform=="darwin" else f"xdg-open '{row[0]}'")
            except Exception as e: 
                QMessageBox.warning(self, "Open", str(e))
        else: 
            QMessageBox.information(self, "Open", "No accessible path for this file on the local machine.")

    def ms_open_physical_location(self, db_id):
        cur = self.db.conn.cursor()
        cur.execute("SELECT real_path FROM myspace WHERE id = ?", (db_id,))
        row = cur.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            try:
                norm_path = os.path.normpath(row[0])
                if sys.platform == "win32":
                    import subprocess
                    subprocess.Popen(['explorer', '/select,', norm_path])
                elif sys.platform == "darwin":
                    import subprocess
                    subprocess.Popen(["open", "-R", norm_path])
                else:
                    os.system(f"xdg-open '{os.path.dirname(norm_path)}'")
            except Exception as e: 
                QMessageBox.warning(self, "Open Location", str(e))
        else:
            QMessageBox.warning(self, "Not Found", "Item does not exist locally.")

    def ms_create_folder(self):
        name, ok = QInputDialog.getText(self, "New Virtual Folder", "Folder Name:")
        if not ok or not name.strip(): 
            return
        name = name.strip()
        cur = self.db.conn.cursor()
        cur.execute("SELECT id FROM myspace WHERE parent_path = ? AND name = ? AND is_folder = 1", (self.current_myspace_prefix, name))
        if cur.fetchone(): 
            return QMessageBox.warning(self, "Error", "Folder already exists here.")
            
        cur.execute("INSERT INTO myspace (parent_path, name, is_folder) VALUES (?, ?, 1)", (self.current_myspace_prefix, name))
        self.db.conn.commit()
        self.refresh_myspace_tree()
        self.load_myspace_directory(self.current_myspace_prefix)

    def ms_delete_item(self, typ, path, db_id):
        if QMessageBox.question(self, "Remove", "Remove this item from MySpace Sandbox? (Real files are NOT deleted)", QMessageBox.Yes|QMessageBox.No) != QMessageBox.Yes: 
            return
        cur = self.db.conn.cursor()
        if typ == "file": 
            cur.execute("DELETE FROM myspace WHERE id = ?", (db_id,))
        else: 
            cur.execute("DELETE FROM myspace WHERE parent_path LIKE ? OR (parent_path = ? AND name = ? AND is_folder = 1)", (f"{path}%", self.current_myspace_prefix, path.strip("/").split("/")[-1]))
        self.db.conn.commit()
        self.refresh_myspace_tree()
        self.load_myspace_directory(self.current_myspace_prefix)

    def ms_rename_item(self, typ, path, db_id, row_idx):
        model = self.ms_file_table.model()
        old_name = model.data(model.index(row_idx, 1), Qt.DisplayRole)
        if old_name.startswith("[CUT] "): old_name = old_name[6:]
        if old_name.startswith("[COPIED] "): old_name = old_name[9:]
        
        new_name, ok = QInputDialog.getText(self, "Rename", "New Name:", QLineEdit.Normal, str(old_name))
        if not ok or not new_name.strip() or new_name == old_name: 
            return
        new_name = new_name.strip()
        cur = self.db.conn.cursor()
        
        cur.execute("SELECT id FROM myspace WHERE parent_path = ? AND name = ? AND is_folder = ?", (self.current_myspace_prefix, new_name, 1 if typ == "folder" else 0))
        if cur.fetchone(): 
            return QMessageBox.warning(self, "Error", "An item with this name already exists.")

        if typ == "file": 
            cur.execute("UPDATE myspace SET name = ? WHERE id = ?", (new_name, db_id))
        else:
            cur.execute("UPDATE myspace SET name = ? WHERE parent_path = ? AND name = ? AND is_folder = 1", (new_name, self.current_myspace_prefix, old_name))
            old_path, new_path = path, f"{self.current_myspace_prefix}{new_name}/"
            cur.execute("UPDATE myspace SET parent_path = ? || SUBSTR(parent_path, LENGTH(?) + 1) WHERE parent_path LIKE ?", (new_path, old_path, f"{old_path}%"))
        self.db.conn.commit()
        self.refresh_myspace_tree()
        self.load_myspace_directory(self.current_myspace_prefix)

    def ms_move_item(self, typ, path, db_id, row_idx):
        cur = self.db.conn.cursor()
        cur.execute("SELECT DISTINCT parent_path || name || '/' FROM myspace WHERE is_folder = 1")
        folders = ["/"] + sorted([r[0] for r in cur.fetchall()])
        if typ == "folder" and path in folders: 
            folders.remove(path) 
            
        dest, ok = QInputDialog.getItem(self, "Move To...", "Select Virtual Destination:", folders, 0, False)
        if not ok or dest == self.current_myspace_prefix: 
            return
        
        model = self.ms_file_table.model()
        old_name = model.data(model.index(row_idx, 1), Qt.DisplayRole)
        if old_name.startswith("[CUT] "): old_name = old_name[6:]
        if old_name.startswith("[COPIED] "): old_name = old_name[9:]
        
        cur.execute("SELECT id FROM myspace WHERE parent_path = ? AND name = ? AND is_folder = ?", (dest, old_name, 1 if typ == "folder" else 0))
        if cur.fetchone():
            dlg = ConflictDialog(old_name, self)
            if not dlg.exec() or dlg.choice == "skip": 
                return
            if dlg.choice == "keep": 
                old_name = f"{old_name} - Copy"
            elif dlg.choice == "replace":
                if typ == "file": 
                    cur.execute("DELETE FROM myspace WHERE parent_path = ? AND name = ? AND is_folder = 0", (dest, old_name))
                else: 
                    cur.execute("DELETE FROM myspace WHERE parent_path LIKE ? OR (parent_path = ? AND name = ? AND is_folder = 1)", (f"{dest}{old_name}/%", dest, old_name))
            
        if typ == "file": 
            cur.execute("UPDATE myspace SET parent_path = ?, name = ? WHERE id = ?", (dest, old_name, db_id))
        else:
            cur.execute("UPDATE myspace SET parent_path = ?, name = ? WHERE id = ?", (dest, old_name, db_id))
            old_full_path, new_full_path = path, f"{dest}{old_name}/"
            cur.execute("UPDATE myspace SET parent_path = ? || SUBSTR(parent_path, LENGTH(?) + 1) WHERE parent_path LIKE ?", (new_full_path, old_full_path, f"{old_full_path}%"))
            
        self.db.conn.commit()
        self.refresh_myspace_tree()
        self.load_myspace_directory(self.current_myspace_prefix)

    def ms_copy(self):
        sel = self.ms_file_table.selectionModel().selectedRows()
        if not sel: 
            return
        model = self.ms_file_table.model()
        self.sb_clip_mode = "copy"
        self.sb_clip_items = [model.data(model.index(idx.row(), 1), Qt.UserRole) for idx in sel]
        self.sb_clip_ids = {db_id for _, _, db_id in self.sb_clip_items}
        self.status.showMessage(f"Copied {len(sel)} items in Sandbox.", 3000)

    def ms_cut(self):
        sel = self.ms_file_table.selectionModel().selectedRows()
        if not sel: 
            return
        model = self.ms_file_table.model()
        self.sb_clip_mode = "cut"
        self.sb_clip_items = [model.data(model.index(idx.row(), 1), Qt.UserRole) for idx in sel]
        self.sb_clip_ids = {db_id for _, _, db_id in self.sb_clip_items}
        self.status.showMessage(f"Cut {len(sel)} items in Sandbox.", 3000)

    def _resolve_conflict_name(self, dest: str, name: str, is_folder: bool, skip_all: bool) -> Tuple[str, bool]:
        if skip_all: 
            return "", True
        cur = self.db.conn.cursor()
        cur.execute("SELECT id FROM myspace WHERE parent_path = ? AND name = ? AND is_folder = ?", (dest, name, 1 if is_folder else 0))
        if not cur.fetchone(): 
            return name, False
            
        dlg = ConflictDialog(name, self)
        if not dlg.exec() or dlg.choice == "skip": 
            return "", False
        if dlg.choice == "skip_all": 
            return "", True
        if dlg.choice == "keep": 
            return f"{name} - Copy", False
        if dlg.choice == "replace":
            if not is_folder: 
                cur.execute("DELETE FROM myspace WHERE parent_path = ? AND name = ? AND is_folder = 0", (dest, name))
            else: 
                cur.execute("DELETE FROM myspace WHERE parent_path LIKE ? OR (parent_path = ? AND name = ? AND is_folder = 1)", (f"{dest}{name}/%", dest, name))
        return name, False

    def ms_paste(self):
        if not self.sb_clip_items: 
            return
        cur = self.db.conn.cursor()
        dest = self.current_myspace_prefix
        skip_all = False
        
        for typ, path, db_id in self.sb_clip_items:
            if typ == "file":
                cur.execute("SELECT name, real_path, size, extension, modified FROM myspace WHERE id = ?", (db_id,))
                row = cur.fetchone()
                if not row: continue
                orig_name, real_path, sz, ext, mod = row
            else:
                orig_name = path.strip("/").split("/")[-1]
            
            final_name, skip_all_flag = self._resolve_conflict_name(dest, orig_name, typ == "folder", skip_all)
            if skip_all_flag: 
                skip_all = True
            if not final_name: 
                continue

            if typ == "file":
                if self.sb_clip_mode == "cut":
                    cur.execute("UPDATE myspace SET parent_path = ?, name = ? WHERE id = ?", (dest, final_name, db_id))
                else:
                    cur.execute("INSERT INTO myspace (parent_path, name, is_folder, real_path, size, extension, modified) VALUES (?, ?, 0, ?, ?, ?, ?)", (dest, final_name, real_path, sz, ext, mod))
            else:
                if self.sb_clip_mode == "cut":
                    cur.execute("UPDATE myspace SET parent_path = ?, name = ? WHERE id = ?", (dest, final_name, db_id))
                    old_full_path, new_full_path = path, f"{dest}{final_name}/"
                    cur.execute("UPDATE myspace SET parent_path = ? || SUBSTR(parent_path, LENGTH(?) + 1) WHERE parent_path LIKE ?", (new_full_path, old_full_path, f"{old_full_path}%"))
                else:
                    cur.execute("INSERT INTO myspace (parent_path, name, is_folder) VALUES (?, ?, 1)", (dest, final_name))
                    old_full_path, new_full_path = path, f"{dest}{final_name}/"
                    cur.execute("SELECT name, is_folder, real_path, size, extension, modified, parent_path FROM myspace WHERE parent_path LIKE ?", (f"{old_full_path}%",))
                    children = cur.fetchall()
                    for c_name, c_isf, c_rp, c_sz, c_ext, c_mod, c_parent in children:
                        new_child_parent = c_parent.replace(old_full_path, new_full_path, 1)
                        cur.execute("INSERT INTO myspace (parent_path, name, is_folder, real_path, size, extension, modified) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                    (new_child_parent, c_name, c_isf, c_rp, c_sz, c_ext, c_mod))
        
        if self.sb_clip_mode == "cut": 
            self.sb_clip_items = []
            self.sb_clip_ids = set()
            self.sb_clip_mode = ""
            
        self.db.conn.commit()
        self.refresh_myspace_tree()
        self.load_myspace_directory(self.current_myspace_prefix)

    def ms_delete_selected_shortcut(self):
        sel = self.ms_file_table.selectionModel().selectedRows()
        if not sel: 
            return
        if QMessageBox.question(self, "Remove", f"Remove {len(sel)} items from Sandbox? (Real files are NOT deleted)", QMessageBox.Yes|QMessageBox.No) != QMessageBox.Yes: 
            return
        
        cur = self.db.conn.cursor()
        model = self.ms_file_table.model()
        for idx in sel:
            typ, path, db_id = model.data(model.index(idx.row(), 1), Qt.UserRole)
            if typ == "file": 
                cur.execute("DELETE FROM myspace WHERE id = ?", (db_id,))
            else: 
                cur.execute("DELETE FROM myspace WHERE parent_path LIKE ? OR (parent_path = ? AND name = ? AND is_folder = 1)", (f"{path}%", self.current_myspace_prefix, path.strip("/").split("/")[-1]))
        self.db.conn.commit()
        self.refresh_myspace_tree()
        self.load_myspace_directory(self.current_myspace_prefix)

    def ms_copy_to_real_location(self, db_id, typ, path):
        cur = self.db.conn.cursor()
        if typ == "folder":
            paths = []
            cur.execute("SELECT real_path FROM myspace WHERE parent_path LIKE ? AND is_folder = 0", (f"{path}%",))
            for r in cur.fetchall():
                if r[0] and os.path.exists(r[0]): 
                    paths.append(r[0])
            if not paths: 
                return QMessageBox.warning(self, "Not Found", "No valid local files found in this virtual folder.")
            
            dst = QFileDialog.getExistingDirectory(self, "Select Destination Directory for Sandbox Export")
            if not dst: 
                return
            
            self.status.showMessage(f"Exporting {len(paths)} files...")
            for p in paths:
                try: 
                    shutil.copy2(p, dst)
                except Exception: 
                    pass
            QMessageBox.information(self, "Done", "Export completed.")
        else:
            cur.execute("SELECT real_path FROM myspace WHERE id = ?", (db_id,))
            row = cur.fetchone()
            if not row or not row[0] or not os.path.exists(row[0]): 
                return QMessageBox.warning(self, "Not Found", "Item does not exist locally.")
            
            src = row[0]
            dst = QFileDialog.getExistingDirectory(self, "Select Destination Directory to Copy To")
            if not dst: 
                return
            
            t = CopyThread(src, dst, False, parent=self)
            t.progress.connect(self.status.showMessage)
            t.finished.connect(lambda: QMessageBox.information(self, "Done", "Copy operation completed."))
            t.error.connect(lambda e: QMessageBox.critical(self, "Error", f"Failed: {e}"))
            self._register_worker(t)
            t.start()

    def add_selected_to_myspace(self, table_source=None):
        if table_source is None: 
            table_source = self.file_table
        sel_rows = table_source.selectionModel().selectedRows()
        if not sel_rows: 
            return
        model = table_source.model()
        if not model: 
            return
        
        QApplication.setOverrideCursor(Qt.WaitCursor)
        cur = self.db.conn.cursor()
        base_dest = self.current_myspace_prefix
        added_files = 0
        added_folders = 0
        
        try:
            for idx in sel_rows:
                data = model.data(model.index(idx.row(), 1), Qt.UserRole)
                if not data: continue
                typ, relpath = data
                
                if typ == "file":
                    cur.execute("SELECT name, size, extension, modified, fullpath FROM files WHERE relpath = ?", (relpath,))
                    row = cur.fetchone()
                    if row:
                        cur.execute("INSERT INTO myspace (parent_path, name, is_folder, real_path, size, extension, modified) VALUES (?, ?, 0, ?, ?, ?, ?)",
                                    (base_dest, row[0], row[4] or relpath, row[1], row[2], row[3]))
                        added_files += 1
                else:
                    clean_path = relpath.rstrip('/')
                    folder_name = clean_path.split('/')[-1]
                    if self._ensure_ms_folder(base_dest, folder_name): 
                        added_folders += 1
                    
                    cur.execute("SELECT relpath, name, size, extension, modified, fullpath, is_folder FROM files WHERE relpath LIKE ?", (f"{clean_path}/%",))
                    for c_rel, c_name, c_size, c_ext, c_mod, c_full, c_isf in cur.fetchall():
                        sub_path = c_rel[len(clean_path):].strip('/')
                        if not sub_path: continue 
                        
                        parts = sub_path.split('/')
                        curr_parent = base_dest + folder_name + "/"
                        for p in parts[:-1]:
                            if self._ensure_ms_folder(curr_parent, p): 
                                added_folders += 1
                            curr_parent += p + "/"
                            
                        if c_isf:
                            if self._ensure_ms_folder(curr_parent, parts[-1]): 
                                added_folders += 1
                        else:
                            cur.execute("INSERT INTO myspace (parent_path, name, is_folder, real_path, size, extension, modified) VALUES (?, ?, 0, ?, ?, ?, ?)",
                                        (curr_parent, parts[-1], c_full or c_rel, c_size, c_ext, c_mod))
                            added_files += 1
            self.db.conn.commit()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Sandbox Error", str(e) + "\n" + traceback.format_exc())
            return
        finally: 
            QApplication.restoreOverrideCursor()
            
        if self.tabs.tabText(self.tabs.currentIndex()) == "⭐ MySpace Sandbox": 
            self.load_myspace_directory(self.current_myspace_prefix)
        QMessageBox.information(self, "Success", f"Recursively mapped {added_files} files and {added_folders} folders into Sandbox:\n{base_dest}")

    def apply_comp_filter(self):
        model = self.comp_table.model()
        if not model: 
            return
        g_txt = self.comp_search.text()
        n_txt = self.comp_search_name.text()
        e_txt = self.comp_search_ext.text()
        
        if hasattr(model, 'set_advanced_filter'):
            model.set_advanced_filter(g_txt, n_txt, e_txt)

    # ---------- Precision Advanced Search Logic ----------
    def run_advanced_search(self):
        name = self.as_name.text().strip()
        match_type = self.as_match_type.currentText()
        item_type = self.as_type.currentText()
        folder = self.as_folder.text().strip()
        ext = self.as_ext.text().strip()
        
        exclude_text = self.as_exclude_text.text().strip()
        exclude_ext = self.as_exclude_ext.text().strip()
        
        min_sz = self.as_min_size.text().strip()
        max_sz = self.as_max_size.text().strip()
        d_from = self.as_date_from.date().toString("yyyy-MM-dd")
        d_to = self.as_date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
        
        drive_sql, params = self._get_drive_sql(self.as_drive)
        
        if item_type == "Folders Only":
            # FIXED folder search: no longer groups by MAX(modified) which breaks the WHERE filter on child files.
            query = f"""
            SELECT DISTINCT 
                CASE WHEN is_folder = 1 THEN relpath ELSE SUBSTR(relpath, 1, LENGTH(relpath) - LENGTH(COALESCE(name, ''))) END AS rp,
                '' AS n, 0 AS s, 'Folder' AS e, '' AS m, drive AS d, 
                CASE WHEN is_folder = 1 THEN fullpath ELSE SUBSTR(fullpath, 1, LENGTH(fullpath) - LENGTH(COALESCE(name, ''))) END AS fp,
                1 AS is_f
            FROM files WHERE 1=1 {drive_sql}
            """
            # Date filter explicitly applied to folders themselves if they exist
            query += " AND (is_folder = 0 OR (modified >= ? AND modified <= ?))"
            params.extend([d_from, d_to])
            
            if folder:
                folder_clean = folder.replace("\\", "/")
                query += " AND (CASE WHEN is_folder = 1 THEN relpath ELSE SUBSTR(relpath, 1, LENGTH(relpath) - LENGTH(COALESCE(name, ''))) END) LIKE ?"
                params.append(f"%{folder_clean}%")
            if name:
                query += " AND (CASE WHEN is_folder = 1 THEN relpath ELSE SUBSTR(relpath, 1, LENGTH(relpath) - LENGTH(COALESCE(name, ''))) END) LIKE ?"
                params.append(f"%{name}%")
        else:
            query = f"SELECT relpath, name, size, extension, modified, drive, fullpath, is_folder FROM files WHERE 1=1 {drive_sql}"
            if item_type == "Files Only": query += " AND is_folder = 0"
            
            if name:
                if match_type == "Contains": query += " AND name LIKE ?"; params.append(f"%{name}%")
                elif match_type == "Exact Match": query += " AND name = ?"; params.append(name)
                elif match_type == "Starts With": query += " AND name LIKE ?"; params.append(f"{name}%")
                elif match_type == "Ends With": query += " AND name LIKE ?"; params.append(f"%{name}")
            
            if exclude_text:
                for word in exclude_text.split(','):
                    query += " AND name NOT LIKE ?"
                    params.append(f"%{word.strip()}%")
                    
            if folder: 
                folder_clean = folder.replace("\\", "/")
                query += " AND SUBSTR(relpath, 1, LENGTH(relpath) - LENGTH(COALESCE(name, ''))) LIKE ?"
                params.append(f"%{folder_clean}%")
                
            if ext: 
                if not ext.startswith("."): ext = "." + ext
                query += " AND extension = ?"
                params.append(ext.lower())
                
            if exclude_ext:
                for ex in exclude_ext.split(','):
                    e = ex.strip().lower()
                    if not e.startswith('.'): e = '.' + e
                    query += " AND extension != ?"
                    params.append(e)
                
            if min_sz:
                try: query += " AND size >= ?"; params.append(int(float(min_sz) * 1024 * 1024))
                except: pass
            if max_sz:
                try: query += " AND size <= ?"; params.append(int(float(max_sz) * 1024 * 1024))
                except: pass
                
            query += " AND modified >= ? AND modified <= ?"
            params.extend([d_from, d_to])
            
        self.search_dlg = QProgressDialog("Advanced Searching... Please wait.", "Cancel", 0, 0, self)
        self.search_dlg.show()
        
        self.search_thread = SearchThread(DB_FILE, query, params, self)
        if item_type == "Folders Only": 
            self.search_thread.finished.connect(lambda rows: self._on_advanced_folder_search_done(rows, folder, name, match_type))
        else: 
            self.search_thread.finished.connect(self._on_advanced_search_done)
        self.search_thread.start()



    def _on_advanced_folder_search_done(self, rows, target_folder, target_name, match_type):
        if self.search_dlg: 
            self.search_dlg.close()
        folder_set = set()
        processed_rows = []
        target_folder = target_folder.replace("\\", "/").lower()
        target_name = target_name.lower()
        
        for rp, n, s, e, m, drv, fp, is_f in rows:
            rp_low = rp.lower()
            if target_folder:
                idx = rp_low.find(target_folder)
                if idx == -1: continue
                slash_idx = rp.find('/', idx)
                if slash_idx != -1: 
                    folder_rp = rp[:slash_idx+1]
                    folder_fp = fp[:fp.find('/', idx + len(fp) - len(rp))] if fp else folder_rp
                else: 
                    folder_rp = rp
                    folder_fp = fp
            else:
                slash_idx = rp.rfind('/')
                if slash_idx != -1: 
                    folder_rp = rp[:slash_idx+1]
                    folder_fp = fp[:fp.rfind('/')] if fp else folder_rp
                else: 
                    continue
                
            sig = (folder_rp, drv)
            if sig not in folder_set:
                folder_set.add(sig)
                folder_name = folder_rp.strip('/').split('/')[-1]
                if target_name:
                    fn_low = folder_name.lower()
                    if match_type == "Contains" and target_name not in fn_low: continue
                    if match_type == "Exact Match" and target_name != fn_low: continue
                    if match_type == "Starts With" and not fn_low.startswith(target_name): continue
                    if match_type == "Ends With" and not fn_low.endswith(target_name): continue
                processed_rows.append((folder_rp, folder_name, 0, "Folder", m, drv, folder_fp, 1))

        self.status.showMessage(f"Search Complete: Dynamically extracted {len(processed_rows)} Folders.", 5000)
        self._render_search_results(processed_rows)

    def _on_advanced_search_done(self, rows):
        if self.search_dlg: self.search_dlg.close()
        
        # Extract unique extensions for the popup
        found_exts = set(r[3].lower() if r[3] else "[No Extension]" for r in rows if r[7] == 0)
        
        if found_exts and len(found_exts) > 1:
            dlg = ExtFilterDialog(found_exts, self)
            if dlg.exec():
                excluded = dlg.get_excluded()
                rows = [r for r in rows if r[7] == 1 or (r[3].lower() if r[3] else "[No Extension]") not in excluded]

        self.status.showMessage(f"Search Complete: Found {len(rows)} items.", 5000)
        self._render_search_results(rows)

    def _render_search_results(self, rows):
        self.as_table.setUpdatesEnabled(False)
        self.as_table.setSortingEnabled(False)
        table_rows = []
        for r_idx, (rp, n, s, e, m, d, fp, is_f) in enumerate(rows):
            n_str = str(n) if n else ""
            if is_f and not n_str:
                clean_rp = str(rp).rstrip('/')
                n_str = clean_rp.split('/')[-1] if clean_rp else "/"
            table_rows.append({
                "display": [str(r_idx+1), n_str, str(rp), str(d), human_size(s or 0), str(e) if e else "Folder", str(m)],
                "sort_keys": [r_idx+1, (0 if is_f else 1, n_str.lower()), (0 if is_f else 1, str(rp).lower()), (0 if is_f else 1, str(d).lower()), (0 if is_f else 1, s or 0), (0 if is_f else 1, str(e).lower() if e else "folder"), (0 if is_f else 1, str(m).lower())],
                "user_data": ("folder" if is_f else "file", str(rp)), "user_data_1": str(fp) if fp else str(rp), "user_data_2": bool(is_f), "ext_meta": e, "is_folder_meta": bool(is_f)
            })
            
        model = FastTableModel(["S.No", "Name", "RelPath", "Drive", "Size", "Type", "Modified"], table_rows, self._get_icon)
        self.as_table.setModel(model)
        self.as_table.setColumnWidth(0, 60)
        self.as_table.setColumnWidth(1, 200)
        self.as_table.setColumnWidth(2, 350)
        self.as_table.setColumnWidth(4, 100)
        self.as_table.setSortingEnabled(True)
        self.as_table.setUpdatesEnabled(True)

    def clear_advanced_search(self):
        self.as_name.clear()
        self.as_folder.clear()
        self.as_ext.clear()
        self.as_min_size.clear()
        self.as_max_size.clear()
        self.as_drive.setCurrentIndex(0)
        self.as_match_type.setCurrentIndex(0)
        self.as_type.setCurrentIndex(0)
        self.as_date_from.setDate(QDate(1970, 1, 1))
        self.as_date_to.setDate(QDate.currentDate().addDays(1))
        if self.as_table.model(): 
            self.as_table.model().all_rows = []
            self.as_table.model().set_filter("")

    def export_advanced_search(self):
        model = self.as_table.model()
        if not model or len(model.filtered_rows) == 0: 
            return QMessageBox.warning(self, "Empty", "No results to export.")
        path, _ = QFileDialog.getSaveFileName(self, "Save search results", str(DATA_DIR/f"search_{now_ts()}.csv"), "CSV (*.csv)")
        if not path: return
        try:
            rows = [["S.No", "Name", "RelPath", "Drive", "Size", "Type", "Modified"]]
            for r in model.filtered_rows: 
                rows.append(r["display"])
            with open(path, "w", encoding="utf-8", newline="") as fh: 
                csv.writer(fh).writerows(rows)
            QMessageBox.information(self, "Export", f"Saved {len(model.filtered_rows)} results to {path}")
        except Exception as e: 
            QMessageBox.critical(self, "Error", f"Failed to export: {e}")

    def on_as_click(self, index: QModelIndex):
        # We must figure out which table called this to get the right model
        table = self.sender() if isinstance(self.sender(), QTableView) else self.as_table
        self.on_as_double_click(index, preview_only=True, table_source=table)
        
    def on_as_double_click(self, index: QModelIndex, preview_only=False, table_source=None):
        table = table_source or self.sender() if isinstance(self.sender(), QTableView) else self.as_table
        model = table.model()
        if not model: return
        
        real_path = model.filtered_rows[index.row()].get("user_data_1", "")
        is_f = model.filtered_rows[index.row()].get("user_data_2", False)
        
        if preview_only: return
            
        if is_f: 
            QMessageBox.information(self, "Folder Record", f"Database Path:\n{real_path}")
        else:
            self.open_local_file("", table, index.row())

    # ---------- Comparisons ----------
    def compare_selected(self):
            sel = self.selected_drives()
            if len(sel) < 2: 
                return QMessageBox.warning(self, "Compare", "Select at least two drives in the Drives Dashboard.")
            dlg = QProgressDialog("Comparing selected drives...", "Cancel", 0, 100, self)
            dlg.show()
            t = CompareThread(sel, parent=self)
            t.progress.connect(lambda p, m: (dlg.setValue(p), dlg.setLabelText(m)))
            t.error.connect(lambda msg: (dlg.close(), QMessageBox.critical(self, "Compare error", str(msg))))
            
            # FIX: Changed setCurrentIndex(4) to setCurrentIndex(5) to account for the new Fast Explorer tab
            t.finished.connect(lambda res: (dlg.close(), setattr(self, 'last_compare_result', res), self.tabs.setCurrentIndex(5), self.display_compare_category("dup_by_sha")))
            
            self._register_worker(t)
            t.start()

    def run_compare_mode(self, category: str):
        if not self.last_compare_result or set(self.last_compare_result.get("selected_drives", [])) != set(self.selected_drives()): 
            return self.compare_selected()
        self.display_compare_category(category)

    def display_compare_category(self, category: str):
        if not self.last_compare_result: 
            return QMessageBox.information(self, "No data", "Run Compare first.")
        
        self.comp_search.blockSignals(True)
        self.comp_search_name.blockSignals(True)
        self.comp_search_ext.blockSignals(True)
        self.comp_search.clear()
        self.comp_search_name.clear()
        self.comp_search_ext.clear()
        self.comp_search.blockSignals(False)
        self.comp_search_name.blockSignals(False)
        self.comp_search_ext.blockSignals(False)

        res = self.last_compare_result
        rows = []
        headers = []
        if category in ("dup_by_sha", "same_content_diff_path"): 
            headers = ["sha", "relpath", "drive", "size", "fullpath"]
            rows = [(d["sha"], d["relpath"], d["drive"], human_size(d["size"]), d["fullpath"]) for d in res[category]]
        elif category in ("same_name_diff_location", "name_conflicts"): 
            headers = ["name", "relpath", "drive", "size", "sha", "fullpath"]
            rows = [(d["name"], d["relpath"], d["drive"], human_size(d["size"]), d.get("sha", ""), d.get("fullpath", "")) for d in res[category]]
        elif category == "missing_relpath": 
            headers = ["relpath", "Missing In Drive(s)", "Present In Drive(s)", "fullpath"]
            rows = [(d["relpath"], d["missing_in"], d["present_in"], d.get("fullpath", "")) for d in res["missing_relpath"]]
        elif category == "missing_name": 
            headers = ["name", "Missing In Drive(s)", "Present In Drive(s)", "fullpath"]
            rows = [(d["name"], d["missing_in"], d["present_in"], d.get("fullpath", "")) for d in res["missing_name"]]
        
        self.comp_table.setUpdatesEnabled(False)
        self.comp_table.setSortingEnabled(False)
        full_headers = ["S.No"] + headers
        table_rows = []
        for r_idx, row_data in enumerate(rows):
            disp = [str(r_idx+1)] + [str(x) for x in row_data]
            sorts = [r_idx+1] + [(1, str(x).lower()) for x in row_data]
            
            fp = row_data[-1] if "fullpath" in headers else ""
            
            ext_val = ""
            rp_val = ""
            if "name" in headers:
                name_val = str(row_data[headers.index("name")])
                ext_val = os.path.splitext(name_val)[1].lower()
            if "relpath" in headers:
                rp_val = str(row_data[headers.index("relpath")])
                if not ext_val:
                    ext_val = os.path.splitext(rp_val)[1].lower()
            
            table_rows.append({
                "display": disp, 
                "sort_keys": sorts, 
                "user_data": ("file", rp_val), 
                "user_data_1": fp,
                "user_data_2": False,
                "ext_meta": ext_val, 
                "is_folder_meta": False
            })
            
        model = FastTableModel(full_headers, table_rows, self._get_icon)
        self.comp_table.setModel(model)
        self.comp_table.setColumnWidth(0, 60)
        self.comp_table.setSortingEnabled(True)
        self.comp_table.setUpdatesEnabled(True)

    def comp_context_menu(self, pos):
        idx = self.comp_table.indexAt(pos)
        if not idx.isValid(): 
            return
        menu = QMenu(self)
        
        sel_rows = self.comp_table.selectionModel().selectedRows()
        
        if len(sel_rows) == 1:
            model = self.comp_table.model()
            data = model.data(model.index(sel_rows[0].row(), 1), Qt.UserRole)
            if data:
                typ, relpath = data
                row_data = model.filtered_rows[sel_rows[0].row()]
                fp = row_data.get("user_data_1", "")
                
                if fp:
                    act_open = QAction("Open Present File (System Default)", self)
                    act_open.triggered.connect(lambda: self.open_local_file_system(fp))
                    menu.addAction(act_open)
                    
                    act_open_int = QAction("Open Present File (Built-in Viewer)", self)
                    act_open_int.triggered.connect(lambda: self.open_local_file(fp, self.comp_table, sel_rows[0].row()))
                    menu.addAction(act_open_int)
                    
                    act_open_loc = QAction("Open Present File Location", self)
                    act_open_loc.triggered.connect(lambda: self.open_file_location(fp))
                    menu.addAction(act_open_loc)
                
                act_copy_real = QAction("Sync / Copy Present File to...", self)
                act_copy_real.triggered.connect(lambda: self.copy_to_real_location_explicit(fp, typ=="folder"))
                menu.addAction(act_copy_real)
                
                if relpath:
                    act_copy_path = QAction("Copy Relative Path", self)
                    act_copy_path.triggered.connect(lambda: QApplication.clipboard().setText(relpath))
                    menu.addAction(act_copy_path)
                    
                    act_prop = QAction("Properties", self)
                    act_prop.triggered.connect(lambda: self.show_properties(relpath, typ=="folder"))
                    menu.addAction(act_prop)
                
                menu.addSeparator()
                
        act_add_ms = QAction(f"⭐ Add {len(sel_rows)} Selected to Sandbox (From Present Drives)", self)
        act_add_ms.triggered.connect(lambda: self.add_selected_to_myspace_compare(self.comp_table))
        menu.addAction(act_add_ms)
        
        menu.addSeparator()
        act_export = QAction("Export selected comparison rows", self)
        act_export.triggered.connect(self.export_selected_comp_rows)
        menu.addAction(act_export)
            
        menu.exec(self.comp_table.viewport().mapToGlobal(pos))

    def add_selected_to_myspace_compare(self, table_source):
        sel_rows = table_source.selectionModel().selectedRows()
        if not sel_rows: 
            return
        model = table_source.model()
        if not model: 
            return
        
        QApplication.setOverrideCursor(Qt.WaitCursor)
        cur = self.db.conn.cursor()
        base_dest = self.current_myspace_prefix
        added_files = 0
        added_folders = 0
        
        try:
            for idx in sel_rows:
                data = model.data(model.index(idx.row(), 1), Qt.UserRole)
                row_data = model.filtered_rows[idx.row()]
                fp = row_data.get("user_data_1", "")
                
                if not data: continue
                typ, relpath = data
                
                if typ == "file":
                    cur.execute("SELECT name, size, extension, modified, fullpath FROM files WHERE relpath = ? OR fullpath = ? LIMIT 1", (relpath, fp))
                    row = cur.fetchone()
                    if row:
                        cur.execute("INSERT INTO myspace (parent_path, name, is_folder, real_path, size, extension, modified) VALUES (?, ?, 0, ?, ?, ?, ?)",
                                    (base_dest, row[0], row[4] or relpath, row[1], row[2], row[3]))
                        added_files += 1
                else:
                    if not relpath: continue
                    clean_path = relpath.rstrip('/')
                    folder_name = clean_path.split('/')[-1]
                    if self._ensure_ms_folder(base_dest, folder_name): 
                        added_folders += 1
                    
                    cur.execute("SELECT relpath, name, size, extension, modified, fullpath, is_folder FROM files WHERE relpath LIKE ?", (f"{clean_path}/%",))
                    for c_rel, c_name, c_size, c_ext, c_mod, c_full, c_isf in cur.fetchall():
                        sub_path = c_rel[len(clean_path):].strip('/')
                        if not sub_path: continue 
                        
                        parts = sub_path.split('/')
                        curr_parent = base_dest + folder_name + "/"
                        for p in parts[:-1]:
                            if self._ensure_ms_folder(curr_parent, p): 
                                added_folders += 1
                            curr_parent += p + "/"
                            
                        if c_isf:
                            if self._ensure_ms_folder(curr_parent, parts[-1]): 
                                added_folders += 1
                        else:
                            cur.execute("INSERT INTO myspace (parent_path, name, is_folder, real_path, size, extension, modified) VALUES (?, ?, 0, ?, ?, ?, ?)",
                                        (curr_parent, parts[-1], c_full or c_rel, c_size, c_ext, c_mod))
                            added_files += 1
            self.db.conn.commit()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Sandbox Error", str(e) + "\n" + traceback.format_exc())
            return
        finally: 
            QApplication.restoreOverrideCursor()
            
        if self.tabs.tabText(self.tabs.currentIndex()) == "⭐ MySpace Sandbox": 
            self.load_myspace_directory(self.current_myspace_prefix)
        QMessageBox.information(self, "Success", f"Recursively mapped {added_files} files and {added_folders} folders into Sandbox:\n{base_dest}")


    def export_selected_comp_rows(self):
        sel = self.comp_table.selectionModel().selectedRows()
        if not sel: 
            return
        model = self.comp_table.model()
        if not model: 
            return
        rows = [model.filtered_rows[idx.row()]["display"] for idx in sel]
        path, _ = QFileDialog.getSaveFileName(self, "Save rows", str(DATA_DIR/f"selected_{now_ts()}.csv"), "CSV (*.csv)")
        if not path: 
            return
        with open(path, "w", encoding="utf-8", newline="") as fh: 
            csv.writer(fh).writerows(rows)
        QMessageBox.information(self, "Export", f"Saved {len(rows)} rows.")

    def export_last_compare(self):
        if not self.last_compare_result: 
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save report", str(DATA_DIR/f"report_{now_ts()}.csv"), "CSV (*.csv)")
        if not path: 
            return
        try:
            tmp = str(Path(path).with_suffix(".tmp"))
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                res = self.last_compare_result
                w.writerows([["meta", "selected_drives", ",".join(res["selected_drives"])], ["meta", "total_relpaths", res["total_relpaths"]]])
                for cat, label in [("dup_by_sha", "duplicate"), ("same_content_diff_path", "same_content_diff_path")]:
                    for d in res[cat]: 
                        w.writerow([label, d["sha"], d["relpath"], d["drive"], d["size"], d["fullpath"]])
                for cat, label in [("same_name_diff_location", "same_name_diff_location"), ("name_conflicts", "name_conflict")]:
                    for d in res[cat]: 
                        w.writerow([label, d.get("name",""), d.get("relpath",""), d.get("drive",""), d.get("size",""), d.get("sha",""), d.get("fullpath","")])
                for d in res["missing_relpath"]: 
                    w.writerow(["missing_relpath", d.get("relpath",""), d.get("missing_in", ""), d.get("present_in", "")])
                for d in res["missing_name"]: 
                    w.writerow(["missing_name", d.get("name",""), d.get("missing_in", ""), d.get("present_in", "")])
            os.replace(tmp, path)
            shutil.copy2(path, DATA_DIR/Path(path).name)
            self.refresh_reports()
        except Exception as e: 
            QMessageBox.critical(self, "Error", f"Failed: {e}")

    # ---------- Advanced Reports Tab Logic ----------
    def refresh_reports(self):
        self.reports_list.clear()
        for p in sorted(DATA_DIR.glob("*.csv"), reverse=True):
            it = QListWidgetItem(p.name)
            it.setData(Qt.UserRole, str(p))
            self.reports_list.addItem(it)

    def load_report_to_table(self, item: QListWidgetItem):
        self.current_report_path = item.data(Qt.UserRole)
        self.apply_report_filter()

    def apply_report_filter(self):
        if not hasattr(self, 'current_report_path') or not self.current_report_path: 
            return
        path = self.current_report_path
        limit_text = self.rep_limit.currentText()
        limit = float('inf') if limit_text == "All" else int(limit_text)
        filter_text = self.rep_filter.text().lower()
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                reader = csv.reader(fh)
                try: 
                    headers = next(reader)
                except StopIteration: 
                    return
                rows = []
                count = 0
                for row in reader:
                    if filter_text:
                        if not any(filter_text in str(cell).lower() for cell in row): 
                            continue
                    disp = [str(count+1)] + row
                    sorts = [count+1] + [(1, str(x).lower()) for x in row]
                    rows.append({"display": disp, "sort_keys": sorts, "user_data": None, "ext_meta": "", "is_folder_meta": False})
                    count += 1
                    if count >= limit: 
                        break
            full_headers = ["S.No"] + headers
            model = FastTableModel(full_headers, rows, self._get_icon)
            self.rep_table.setModel(model)
            self.rep_table.setColumnWidth(0, 60)
        except Exception as e: 
            QMessageBox.warning(self, "Load Error", str(e))

    def closeEvent(self, ev):
        for w in list(self._workers):
            try: 
                if hasattr(w, "cancel"): 
                    w.cancel()
                if w.isRunning(): 
                    w.wait(2000)
            except Exception: 
                pass
        try: 
            self.db.close()
        except Exception: 
            pass
        super().closeEvent(ev)
        
    def _get_drive_sql(self, combo_box, table_prefix="") -> Tuple[str, list]:
        """Returns the SQL clause and params for the given combobox, honoring Global Filters."""
        target = combo_box.currentText()
        prefix = f"{table_prefix}." if table_prefix else ""
        if target == "Any Drive":
            if self.global_drive_filter:
                placeholders = ",".join("?" for _ in self.global_drive_filter)
                return f"AND {prefix}drive IN ({placeholders})", list(self.global_drive_filter)
            return "", []
        return f"AND {prefix}drive = ?", [target]        
        
