#!/usr/bin/env python3
"""
Drive Explorer
Unrestricted Search, Icon Caching, Recursive Sandbox, Precision Statistics
"""
from __future__ import annotations
import csv
import hashlib
import os
import shutil
import sqlite3
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QModelIndex, QAbstractTableModel, QDate, QFileInfo, QUrl
from PySide6.QtGui import QFont, QPixmap, QAction, QPainter, QIcon, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QToolBar, QFileDialog, QMessageBox,
    QWidget, QVBoxLayout, QLabel, QTabWidget, QListWidget, QListWidgetItem,
    QPushButton, QHBoxLayout, QInputDialog, QProgressDialog, QSplitter,
    QTreeWidget, QTreeWidgetItem, QPlainTextEdit, QLineEdit, QComboBox,
    QTableView, QHeaderView, QMenu, QAbstractItemView, QStatusBar,
    QTableWidget, QTableWidgetItem, QStyle, QGridLayout, QDateEdit,
    QSizePolicy, QCheckBox, QDialog, QFormLayout
)

try:
    from PySide6.QtWidgets import QFileIconProvider
    HAS_ICON_PROVIDER = True
except ImportError:
    try:
        from PySide6.QtGui import QAbstractFileIconProvider as QFileIconProvider
        HAS_ICON_PROVIDER = True
    except ImportError:
        HAS_ICON_PROVIDER = False

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

# ---------------- constants & utils ----------------
APP_TITLE = "Drive Explorer"
DATA_DIR = Path("data")
DB_FILE = DATA_DIR / "catalog.db"
CSV_DIR = DATA_DIR / "csvs"
OLD_DATA_DIR = DATA_DIR / "old_drives"
MAX_RENDER_ROWS = 20000  

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    OLD_DATA_DIR.mkdir(parents=True, exist_ok=True)

def now_ts(): 
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def human_size(num_bytes: int) -> str:
    try: n = int(num_bytes)
    except Exception: return "0 B"
    if n < 1024: return f"{n} B"
    n_kb = n / 1024.0
    if n_kb < 1024: return f"{n_kb:.1f} KB"
    n_mb = n_kb / 1024.0
    if n_mb < 1024: return f"{n_mb:.2f} MB" if n_mb < 10 else f"{n_mb:.1f} MB"
    n_gb = n_mb / 1024.0
    return f"{n_gb:.2f} GB" if n_gb < 10 else f"{n_gb:.1f} GB"

def sha256_file(path: str, chunk=1024*1024):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk)
                if not b: break
                h.update(b)
        return h.hexdigest()
    except Exception: 
        return ""

def parse_purchase_date(s: str) -> date | None:
    if not s: return None
    s = s.strip()
    try:
        if "/" in s:
            parts = s.split("/")
            if len(parts) == 2 and len(parts[1]) == 4: return date(int(parts[1]), int(parts[0]), 1)
            if len(parts) == 3: return date(int(parts[0]), int(parts[1]), int(parts[2]))
        if "-" in s:
            parts = s.split("-")
            if len(parts) == 3: return date(int(parts[0]), int(parts[1]), int(parts[2]))
            if len(parts) == 2: return date(int(parts[0]), int(parts[1]), 1)
        if len(s) == 4 and s.isdigit(): return date(int(s), 1, 1)
    except Exception: 
        pass
    return None

def age_from_date(d: date) -> Tuple[str, int]:
    if not d: return "unknown", -1
    today = date.today()
    days_total = (today - d).days
    years, months, days = today.year - d.year, today.month - d.month, today.day - d.day
    if days < 0:
        months -= 1
        from calendar import monthrange
        prev_month = (today.month - 1) or 12
        prev_year = today.year if today.month != 1 else today.year - 1
        days += monthrange(prev_year, prev_month)[1]
    if months < 0:
        years -= 1
        months += 12
    parts = []
    if years: parts.append(f"{years}y")
    if months: parts.append(f"{months}m")
    if days: parts.append(f"{days}d")
    return (" ".join(parts) if parts else "0d", days_total)

# ---------------- Database ----------------
class CatalogDB:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA mmap_size=268435456;") 
        self.conn.execute("PRAGMA cache_size=-20000;")   
        self.conn.execute("PRAGMA temp_store=MEMORY;")
        self._ensure_schema()

    def _ensure_schema(self):
        c = self.conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS drives (id INTEGER PRIMARY KEY, drive_name TEXT UNIQUE, purchase_date TEXT, scanned_at TEXT, csv_path TEXT);")
        c.execute("CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, relpath TEXT, name TEXT, size INTEGER, extension TEXT, modified TEXT, sha TEXT, drive TEXT, fullpath TEXT, is_folder INTEGER DEFAULT 0);")
        c.execute("CREATE TABLE IF NOT EXISTS myspace (id INTEGER PRIMARY KEY, parent_path TEXT, name TEXT, is_folder INTEGER, real_path TEXT, size INTEGER, extension TEXT, modified TEXT);")
        
        try: 
            c.execute("ALTER TABLE files ADD COLUMN is_folder INTEGER DEFAULT 0;")
        except sqlite3.OperationalError: 
            pass
            
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_relpath ON files(relpath);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_drive ON files(drive);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_isfolder ON files(is_folder);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_ext ON files(extension);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_myspace_parent ON myspace(parent_path);")
        self.conn.commit()

    def insert_drive(self, drive_name: str, purchase_date: str, csv_path: str = ""):
        self.conn.cursor().execute("INSERT OR REPLACE INTO drives (drive_name,purchase_date,scanned_at,csv_path) VALUES (?,?,?,?);",
                  (drive_name, purchase_date or "", datetime.now().isoformat(), csv_path or ""))
        self.conn.commit()

    def import_csv(self, csv_path: Path, drive_name: str, progress_callback=None) -> int:
        cur = self.conn.cursor()
        batch = []
        BATCH = 2000
        inserted = 0
        with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            rdr = csv.reader(fh)
            next(rdr, None)
            for row in rdr:
                if not row: continue
                batch.append((row[0], row[1] if len(row)>1 and row[1] else os.path.basename(row[0]), int(row[2]) if len(row)>2 and row[2] else 0, row[3] if len(row)>3 else os.path.splitext(row[0] if len(row)<=1 or not row[1] else row[1])[1].lower(), row[4] if len(row)>4 else "", row[5] if len(row)>5 else "", drive_name, row[6] if len(row)>6 else "", 0))
                if len(batch) >= BATCH:
                    cur.executemany("INSERT INTO files (relpath,name,size,extension,modified,sha,drive,fullpath,is_folder) VALUES (?,?,?,?,?,?,?,?,?);", batch)
                    self.conn.commit()
                    inserted += len(batch)
                    batch.clear()
                    if progress_callback: progress_callback(inserted)
            if batch:
                cur.executemany("INSERT INTO files (relpath,name,size,extension,modified,sha,drive,fullpath,is_folder) VALUES (?,?,?,?,?,?,?,?,?);", batch)
                self.conn.commit()
                inserted += len(batch)
        return inserted

    def drives_summary(self) -> List[Tuple]:
        cur = self.conn.cursor()
        cur.execute("SELECT d.drive_name, d.purchase_date, d.scanned_at, d.csv_path, COUNT(f.id) as file_count, COALESCE(SUM(f.size),0) as total_size FROM drives d LEFT JOIN files f ON f.drive = d.drive_name WHERE f.is_folder = 0 OR f.is_folder IS NULL GROUP BY d.drive_name ORDER BY d.scanned_at DESC;")
        return cur.fetchall()

    def delete_drive(self, drive_name: str):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM files WHERE drive = ?;", (drive_name,))
        cur.execute("DELETE FROM drives WHERE drive_name = ?;", (drive_name,))
        self.conn.commit()
        
    def close(self):
        try: self.conn.close()
        except Exception: pass

# ---------------- Worker Threads ----------------
class WorkerBase(QThread):
    error = Signal(str)
    def __init__(self, parent=None): 
        super().__init__(parent)
        self._cancel_requested = False
    def cancel(self): 
        self._cancel_requested = True

class SearchThread(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, db_path, query, params, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.query = query
        self.params = params
        self.conn = None
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        if self.conn:
            try: self.conn.interrupt()
            except Exception: pass

    def run(self):
        try:
            self.conn = sqlite3.connect(str(self.db_path))
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self.conn.execute("PRAGMA mmap_size=268435456;") 
            
            cur = self.conn.cursor()
            cur.execute(self.query, self.params)
            rows = cur.fetchall()
            self.conn.close()
            self.conn = None
            
            if not self._is_cancelled:
                self.finished.emit(rows)
        except sqlite3.OperationalError as e:
            if "interrupted" not in str(e).lower():
                self.error.emit(str(e))
        except Exception as e:
            self.error.emit(str(e))

class ScanThread(WorkerBase):
    progress = Signal(int)
    finished = Signal(str, str, str)
    
    def __init__(self, folder: str, drive_name: str, purchase_date: str, compute_sha: bool, workers: int = 6, batch_size: int = 2000, write_csv: bool = True, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.drive_name = drive_name
        self.purchase_date = purchase_date
        self.compute_sha = compute_sha
        self.workers = max(1, workers)
        self.batch_size = max(128, batch_size)
        self.write_csv = write_csv
        
    def run(self):
        try:
            all_paths = []
            for root, dirs, files in os.walk(self.folder):
                for d in dirs: 
                    all_paths.append((os.path.join(root, d), True))
                for f in files: 
                    all_paths.append((os.path.join(root, f), False))
                
            if not all_paths: 
                self.error.emit("No files or folders found to scan.")
                return
            
            csv_path = CSV_DIR / f"{self.drive_name}_{now_ts()}.csv" if self.write_csv else None
            csv_fh = None
            csv_w = None
            if csv_path:
                csv_fh = csv_path.open("w", encoding="utf-8", newline="")
                csv_w = csv.writer(csv_fh)
                csv_w.writerow(["relpath", "name", "size", "extension", "modified", "sha", "fullpath", "is_folder"])
                
            conn = sqlite3.connect(str(DB_FILE))
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            cur = conn.cursor()
            batch = []
            processed = 0
            
            def worker(item):
                p, is_folder = item
                if self._cancel_requested: return None
                try:
                    st = os.stat(p)
                    rel = os.path.relpath(p, self.folder).replace("\\", "/")
                    name = os.path.basename(rel)
                    modified = datetime.fromtimestamp(st.st_mtime).isoformat()
                    if is_folder: 
                        return (rel, name, 0, "Folder", modified, "", self.drive_name, p, 1)
                    else:
                        size = int(st.st_size)
                        return (rel, name, size, os.path.splitext(name)[1].lower(), modified, sha256_file(p) if self.compute_sha and size > 0 else "", self.drive_name, p, 0)
                except Exception: 
                    return None
                
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futures = {ex.submit(worker, p): p for p in all_paths}
                for fut in as_completed(futures):
                    if self._cancel_requested:
                        conn.commit()
                        conn.close()
                        if csv_fh: csv_fh.close()
                        return
                    res = fut.result()
                    if res:
                        batch.append(res)
                        if csv_w: 
                            csv_w.writerow([res[0], res[1], res[2], res[3], res[4], res[5], res[7], res[8]])
                    processed += 1
                    if len(batch) >= self.batch_size:
                        cur.executemany("INSERT INTO files (relpath,name,size,extension,modified,sha,drive,fullpath,is_folder) VALUES (?,?,?,?,?,?,?,?,?);", batch)
                        conn.commit()
                        batch.clear()
                        self.progress.emit(int(processed * 100 / len(all_paths)))
                if batch: 
                    cur.executemany("INSERT INTO files (relpath,name,size,extension,modified,sha,drive,fullpath,is_folder) VALUES (?,?,?,?,?,?,?,?,?);", batch)
                    conn.commit()
                    self.progress.emit(100)
            conn.close()
            if csv_fh: 
                csv_fh.close()
            
            main_conn = sqlite3.connect(str(DB_FILE))
            main_conn.execute("INSERT OR REPLACE INTO drives (drive_name,purchase_date,scanned_at,csv_path) VALUES (?,?,?,?);", (self.drive_name, self.purchase_date or "", datetime.now().isoformat(), str(csv_path) if csv_path else ""))
            main_conn.commit()
            main_conn.close()
            self.finished.emit(str(csv_path) if csv_path else "", self.drive_name, self.purchase_date or "")
        except Exception as e: 
            self.error.emit(f"{e}\n{traceback.format_exc()}")

class ImportThread(WorkerBase):
    progress = Signal(int)
    finished = Signal(int, str)
    def __init__(self, csv_path: str, drive_name: str, parent=None): 
        super().__init__(parent)
        self.csv_path = csv_path
        self.drive_name = drive_name
    def run(self):
        try: 
            db = CatalogDB(DB_FILE)
            inserted = db.import_csv(Path(self.csv_path), self.drive_name, progress_callback=lambda n: self.progress.emit(n))
            db.insert_drive(self.drive_name, "", self.csv_path)
            db.close()
            self.finished.emit(inserted, self.drive_name)
        except Exception as e: 
            self.error.emit(f"{e}\n{traceback.format_exc()}")

class CompareThread(WorkerBase):
    progress = Signal(int, str)
    finished = Signal(dict)
    def __init__(self, selected_drives: List[str], parent=None): 
        super().__init__(parent)
        self.selected_drives = list(selected_drives)
    def run(self):
        try:
            if not self.selected_drives or len(self.selected_drives) < 2: 
                self.error.emit("Select at least two drives to compare.")
                return
            conn = sqlite3.connect(str(DB_FILE))
            cur = conn.cursor()
            placeholders = ','.join('?' for _ in self.selected_drives)
            cur.execute(f"SELECT relpath, name, size, sha, drive, fullpath FROM files WHERE is_folder = 0 AND drive IN ({placeholders});", tuple(self.selected_drives))
            self.progress.emit(20, "Indexing Files")
            
            per_rel = {}
            per_sha = {}
            per_name = {}
            for rel, name, size, sha, drive, fullpath in cur.fetchall():
                per_rel.setdefault(rel, {})[drive] = (name, size, sha or "", fullpath)
                if sha: 
                    per_sha.setdefault(sha, []).append((rel, drive, size, fullpath))
                per_name.setdefault(name, []).append((rel, drive, size, sha or "", fullpath))
                
            self.progress.emit(50, "Analyzing duplicates")
            dup_by_sha = [{"sha": sha, "relpath": r, "drive": d, "size": s, "fullpath": f} for sha, items in per_sha.items() if len(set(d for _, d, _, _ in items)) > 1 for r, d, s, f in items]
            same_content = [{"sha": sha, "relpath": r, "drive": d, "size": s, "fullpath": f} for sha, items in per_sha.items() if len(set(r for r, _, _, _ in items)) > 1 for r, d, s, f in items]
            same_name = [{"name": n, "relpath": r, "drive": d, "size": s, "sha": sh, "fullpath": f} for n, e in per_name.items() if len(set(i[0] for i in e)) > 1 and len(set(i[1] for i in e)) > 1 for r, d, s, sh, f in e]
            conflicts = [{"name": n, "relpath": r, "drive": d, "size": s, "sha": sh, "fullpath": f} for n, e in per_name.items() if len(set(i[2] for i in e)) > 1 and len(e) > 1 for r, d, s, sh, f in e]
            missing = [{"relpath": rel, "present_drive": d} for rel, info in per_rel.items() for d in info.keys() if set(self.selected_drives) - set(info.keys())]
            
            conn.close()
            self.progress.emit(100, "Done")
            self.finished.emit({
                "selected_drives": self.selected_drives, "dup_by_sha": dup_by_sha, 
                "same_content_diff_path": same_content, "same_name_diff_location": same_name, 
                "name_conflicts": conflicts, "missing": missing, "total_relpaths": len(per_rel)
            })
        except Exception as e: 
            self.error.emit(f"{e}\n{traceback.format_exc()}")

# ---------------- UI Models & Custom Widgets ----------------
class FastTableModel(QAbstractTableModel):
    def __init__(self, headers: List[str], rows: List[Dict], parent=None):
        super().__init__(parent)
        self.headers = headers
        self.rows = rows

    def rowCount(self, parent=QModelIndex()): 
        return len(self.rows)
    def columnCount(self, parent=QModelIndex()): 
        return len(self.headers)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid(): 
            return None
        row = self.rows[index.row()]
        col = index.column()
        
        if role == Qt.DisplayRole: 
            return row["display"][col]
        elif role == Qt.UserRole: 
            return row["user_data"]
        elif role == Qt.UserRole + 1: 
            return row.get("user_data_1", None)
        elif role == Qt.UserRole + 2: 
            return row.get("user_data_2", None)
        elif role == Qt.DecorationRole and col == 1: 
            return row.get("icon", None)
        elif role == Qt.TextAlignmentRole:
            if col == 0 or (self.headers[col] == "Size"): 
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        return None

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal: 
            return self.headers[section]
        return None

    def sort(self, column: int, order=Qt.AscendingOrder):
        self.layoutAboutToBeChanged.emit()
        reverse = (order == Qt.DescendingOrder)
        self.rows.sort(key=lambda x: x["sort_keys"][column], reverse=reverse)
        self.layoutChanged.emit()

class SmartTableItem(QTableWidgetItem):
    def __init__(self, display_str, sort_value, is_folder=False):
        super().__init__(str(display_str))
        self.sort_value = sort_value
        self.is_folder = is_folder
    def __lt__(self, other):
        if isinstance(other, SmartTableItem):
            if self.is_folder != other.is_folder: 
                return self.is_folder 
            try: 
                return self.sort_value < other.sort_value
            except TypeError: 
                return str(self.sort_value) < str(other.sort_value)
        return super().__lt__(other)

class ScaledImageLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.setMinimumHeight(250)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background-color: #1e1e1e; border: 1px solid #444; border-radius: 4px;")
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
            scaled = self._pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)

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

class SandboxTableView(QTableView):
    """Custom TableView to intercept OS Drag & Drop into the MySpace Sandbox"""
    filesDropped = Signal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            urls = [url.toLocalFile() for url in event.mimeData().urls()]
            self.filesDropped.emit(urls)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

# ---------------- Main window ----------------
class DriveExplorerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_dirs()
        self.setWindowTitle(APP_TITLE)
        self.resize(1600, 1000)
        self.setFont(QFont("Segoe UI", 10))

        self.db = CatalogDB(DB_FILE)
        self._workers: List[QThread] = []
        self.search_thread = None
        self.search_dlg = None
        self.last_compare_result = None
        self.is_dark_mode = False
        
        self.current_explorer_prefix = ""
        self.current_myspace_prefix = "/"
        self.current_report_path = ""
        
        self._icon_cache = {}
        if HAS_ICON_PROVIDER:
            self.icon_provider = QFileIconProvider()
        else:
            self.icon_provider = None

        self._build_ui()
        self.toggle_theme() 
        QTimer.singleShot(150, self.refresh_all)

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
        
        act_theme = QAction("🌙 / ☀️ Toggle Theme", self)
        act_theme.triggered.connect(self.toggle_theme)
        
        tb.addAction(act_scan)
        tb.addAction(act_import)
        tb.addAction(act_compare)
        tb.addAction(act_refresh)
        
        empty = QWidget()
        empty.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(empty)
        tb.addAction(act_theme)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # ---------- Drives Dashboard tab ----------
        drives_tab = QWidget()
        dv = QVBoxLayout(drives_tab)
        dv.addWidget(QLabel("<b>Drives Dashboard</b> (Check boxes to include in Comparisons. Click column headers to sort.)"))
        
        self.drives_table = QTableWidget()
        self.drives_table.setColumnCount(6)
        self.drives_table.setHorizontalHeaderLabels(["Use", "Drive Name", "Files", "Total Size", "Age", "Scanned Date"])
        self.drives_table.verticalHeader().setVisible(False)
        self.drives_table.horizontalHeader().setStretchLastSection(True)
        self.drives_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.drives_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.drives_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.drives_table.setAlternatingRowColors(True)
        self.drives_table.setSortingEnabled(True) 
        dv.addWidget(self.drives_table)
        
        btns = QHBoxLayout()
        self.btn_check_all = QPushButton("Check All")
        self.btn_check_all.clicked.connect(self.check_all_drives)
        
        self.btn_uncheck_all = QPushButton("Uncheck All")
        self.btn_uncheck_all.clicked.connect(self.uncheck_all_drives)
        
        self.btn_delete_drive = QPushButton("Delete Selected Drive Data")
        self.btn_delete_drive.clicked.connect(self.delete_selected_drive)
        
        self.btn_run_compare = QPushButton("Compare Selected Drives")
        self.btn_run_compare.clicked.connect(self.compare_selected)
        
        btns.addWidget(self.btn_check_all)
        btns.addWidget(self.btn_uncheck_all)
        btns.addWidget(self.btn_delete_drive)
        btns.addWidget(self.btn_run_compare)
        btns.addStretch()
        dv.addLayout(btns)
        
        self.tabs.addTab(drives_tab, "Drives Dashboard")

        # ---------- Global Explorer Tab ----------
        explorer_tab = QWidget()
        ev = QVBoxLayout(explorer_tab)
        
        top_row = QHBoxLayout()
        self.btn_up = QPushButton("⬆ Up")
        self.btn_up.clicked.connect(self.navigate_up)
        
        self.address_bar = QLineEdit()
        self.address_bar.setPlaceholderText("Drive Root /")
        self.address_bar.setReadOnly(True)
        
        self.ex_drive = QComboBox() 
        self.ex_drive.addItem("Any Drive")
        self.ex_drive.currentIndexChanged.connect(self.clear_explorer_search)
        
        self.ex_search = QLineEdit()
        self.ex_search.setPlaceholderText("Quick Search (Enter)...")
        self.ex_search.returnPressed.connect(self.explorer_global_search)
        
        self.btn_clear_search = QPushButton("Clear")
        self.btn_clear_search.clicked.connect(self.clear_explorer_search)
        
        top_row.addWidget(self.btn_up)
        top_row.addWidget(self.address_bar, stretch=2)
        top_row.addWidget(QLabel("Target Drive:"))
        top_row.addWidget(self.ex_drive)
        top_row.addWidget(QLabel("Search:"))
        top_row.addWidget(self.ex_search, stretch=1)
        top_row.addWidget(self.btn_clear_search)
        ev.addLayout(top_row)

        split = QSplitter(Qt.Horizontal)
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderLabel("Folders")
        self.folder_tree.itemExpanded.connect(self.on_folder_expand)
        self.folder_tree.itemClicked.connect(self.on_folder_click)
        split.addWidget(self.folder_tree)

        self.file_table = QTableView()
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.file_table.horizontalHeader().setStretchLastSection(True)
        self.file_table.setSortingEnabled(True)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.file_table.setAlternatingRowColors(True)
        
        self.file_table.clicked.connect(self.on_file_click)
        self.file_table.doubleClicked.connect(self.on_table_double_click)
        self.file_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_table.customContextMenuRequested.connect(lambda pos: self.file_context_menu(pos, self.file_table))
        split.addWidget(self.file_table)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0,0,0,0)
        rv.addWidget(QLabel("<b>Details Preview</b>"))
        
        self.preview_image = ScaledImageLabel()
        self.preview_text = QPlainTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(150)
        
        rv.addWidget(self.preview_image, stretch=1)
        rv.addWidget(self.preview_text)
        
        right_container = QWidget()
        right_container.setLayout(rv)
        split.addWidget(right_container)
        split.setSizes([250, 800, 350])
        ev.addWidget(split)
        
        self.tabs.addTab(explorer_tab, "Global Explorer")

        # ---------- MySpace Virtual Sandbox Tab ----------
        myspace_tab = QWidget()
        ms_vbox = QVBoxLayout(myspace_tab)
        
        ms_top_row = QHBoxLayout()
        self.btn_ms_up = QPushButton("⬆ Up")
        self.btn_ms_up.clicked.connect(self.ms_navigate_up)
        
        self.ms_address_bar = QLineEdit()
        self.ms_address_bar.setPlaceholderText("Virtual Sandbox Root /")
        self.ms_address_bar.setReadOnly(True)

        self.ms_search = QLineEdit()
        self.ms_search.setPlaceholderText("Filter Sandbox Content...")
        self.ms_search.textChanged.connect(self.ms_search_changed)
        
        ms_top_row.addWidget(self.btn_ms_up)
        ms_top_row.addWidget(self.ms_address_bar, stretch=2)
        ms_top_row.addWidget(QLabel("Local Search:"))
        ms_top_row.addWidget(self.ms_search, stretch=1)
        ms_vbox.addLayout(ms_top_row)

        ms_split = QSplitter(Qt.Horizontal)
        self.ms_folder_tree = QTreeWidget()
        self.ms_folder_tree.setHeaderLabel("MySpace Virtual Folders")
        self.ms_folder_tree.itemExpanded.connect(self.on_ms_folder_expand)
        self.ms_folder_tree.itemClicked.connect(self.on_ms_folder_click)
        ms_split.addWidget(self.ms_folder_tree)

        self.ms_file_table = SandboxTableView()
        self.ms_file_table.filesDropped.connect(self.on_sandbox_files_dropped)
        self.ms_file_table.verticalHeader().setVisible(False)
        self.ms_file_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.ms_file_table.horizontalHeader().setStretchLastSection(True)
        self.ms_file_table.setSortingEnabled(True)
        self.ms_file_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ms_file_table.setAlternatingRowColors(True)
        
        self.ms_file_table.clicked.connect(self.on_ms_file_click)
        self.ms_file_table.doubleClicked.connect(self.on_ms_table_double_click)
        self.ms_file_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ms_file_table.customContextMenuRequested.connect(self.ms_context_menu)
        ms_split.addWidget(self.ms_file_table)

        ms_right = QWidget()
        ms_rv = QVBoxLayout(ms_right)
        ms_rv.setContentsMargins(0,0,0,0)
        ms_rv.addWidget(QLabel("<b>Sandbox Details Preview</b>"))
        
        self.ms_preview_image = ScaledImageLabel()
        self.ms_preview_text = QPlainTextEdit()
        self.ms_preview_text.setReadOnly(True)
        self.ms_preview_text.setMaximumHeight(150)
        
        ms_rv.addWidget(self.ms_preview_image, stretch=1)
        ms_rv.addWidget(self.ms_preview_text)
        
        ms_right_container = QWidget()
        ms_right_container.setLayout(ms_rv)
        ms_split.addWidget(ms_right_container)

        ms_split.setSizes([250, 800, 350])
        ms_vbox.addWidget(ms_split)
        
        self.tabs.addTab(myspace_tab, "⭐ MySpace Sandbox")

        # ---------- Ultimate Advanced Search Tab ----------
        adv_search_tab = QWidget()
        adv_layout = QVBoxLayout(adv_search_tab)
        
        form_layout = QGridLayout()
        form_layout.setSpacing(10)
        
        self.as_name = QLineEdit()
        self.as_name.setPlaceholderText("e.g., rus.srt")
        
        self.as_folder = QLineEdit()
        self.as_folder.setPlaceholderText("e.g., Movies2") 
        
        self.as_match_type = QComboBox()
        self.as_match_type.addItems(["Contains", "Exact Match", "Starts With", "Ends With"])
        
        self.as_type = QComboBox()
        self.as_type.addItems(["Files & Folders", "Files Only", "Folders Only"])
        
        self.as_drive = QComboBox()
        self.as_drive.addItem("Any Drive")
        
        self.as_ext = QLineEdit()
        self.as_ext.setPlaceholderText("e.g., .jpg")
        
        size_layout = QHBoxLayout()
        self.as_min_size = QLineEdit()
        self.as_min_size.setPlaceholderText("Min MB")
        self.as_max_size = QLineEdit()
        self.as_max_size.setPlaceholderText("Max MB")
        size_layout.addWidget(self.as_min_size)
        size_layout.addWidget(QLabel(" to "))
        size_layout.addWidget(self.as_max_size)
        
        date_layout = QHBoxLayout()
        self.as_date_from = QDateEdit()
        self.as_date_from.setCalendarPopup(True)
        self.as_date_from.setDate(QDate(1990, 1, 1))
        
        self.as_date_to = QDateEdit()
        self.as_date_to.setCalendarPopup(True)
        self.as_date_to.setDate(QDate.currentDate().addDays(1))
        
        date_layout.addWidget(self.as_date_from)
        date_layout.addWidget(QLabel(" to "))
        date_layout.addWidget(self.as_date_to)
        
        form_layout.addWidget(QLabel("Item Name:"), 0, 0)
        form_layout.addWidget(self.as_name, 0, 1)
        form_layout.addWidget(QLabel("Folder Path (Dynamic):"), 0, 2)
        form_layout.addWidget(self.as_folder, 0, 3)
        
        form_layout.addWidget(QLabel("Match Mode:"), 1, 0)
        form_layout.addWidget(self.as_match_type, 1, 1)
        form_layout.addWidget(QLabel("Look For:"), 1, 2)
        form_layout.addWidget(self.as_type, 1, 3)
        
        form_layout.addWidget(QLabel("Target Drive:"), 2, 0)
        form_layout.addWidget(self.as_drive, 2, 1)
        form_layout.addWidget(QLabel("Extension:"), 2, 2)
        form_layout.addWidget(self.as_ext, 2, 3)
        
        form_layout.addWidget(QLabel("Size Range (MB):"), 3, 0)
        form_layout.addLayout(size_layout, 3, 1)
        form_layout.addWidget(QLabel("Modified Date:"), 3, 2)
        form_layout.addLayout(date_layout, 3, 3)
        
        btn_layout = QHBoxLayout()
        self.btn_adv_search = QPushButton("Run Advanced Search")
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
        
        self.as_table = QTableView()
        self.as_table.verticalHeader().setVisible(False)
        self.as_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.as_table.horizontalHeader().setStretchLastSection(True)
        self.as_table.setSortingEnabled(True)
        self.as_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.as_table.setAlternatingRowColors(True)
        
        self.as_table.doubleClicked.connect(self.on_as_double_click)
        self.as_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.as_table.customContextMenuRequested.connect(lambda pos: self.file_context_menu(pos, self.as_table))
        adv_layout.addWidget(self.as_table)
        
        self.tabs.addTab(adv_search_tab, "🔍 Advanced Search")

        # ---------- Comparisons tab ----------
        comp_tab = QWidget()
        cv = QVBoxLayout(comp_tab)
        row = QHBoxLayout()
        
        for t, m in [("Exact duplicates (SHA)", "dup_by_sha"), ("Same content, diff path", "same_content_diff_path"), ("Same filename diff locations", "same_name_diff_location"), ("Name conflicts (size)", "name_conflicts"), ("Missing in selected drives", "missing")]:
            btn = QPushButton(t)
            btn.clicked.connect(lambda _, x=m: self.run_compare_mode(x))
            row.addWidget(btn)
        cv.addLayout(row)
        
        self.comp_table = QTableView()
        self.comp_table.verticalHeader().setVisible(False)
        self.comp_table.horizontalHeader().setStretchLastSection(True)
        self.comp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.comp_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.comp_table.setAlternatingRowColors(True)
        self.comp_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.comp_table.customContextMenuRequested.connect(self.comp_context_menu)
        cv.addWidget(self.comp_table)
        
        exp_row = QHBoxLayout()
        self.btn_export_comp = QPushButton("Export last compare (CSV/Excel)")
        self.btn_export_comp.clicked.connect(self.export_last_compare)
        exp_row.addWidget(self.btn_export_comp)
        exp_row.addStretch()
        cv.addLayout(exp_row)
        
        self.tabs.addTab(comp_tab, "Comparisons")

        # ---------- Advanced Reports Dashboard ----------
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
        
        rep_controls.addWidget(QLabel("Filter Report:"))
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

        # ---------- Advanced Charts & Statistics tab ----------
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
        self.stat_date_from.setDate(QDate(1990, 1, 1))
        
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
            "Drives by Total Size (GB)", 
            "Drives by File Count", 
            "Top 15 File Extensions by Count", 
            "Top 15 File Extensions by Storage Size",
            "File Size Distribution",
            "Files by Modification Year",
            "Top 20 Largest Individual Files",
            "Storage Usage by Year (GB)",
            "File Age Distribution"
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

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.selected_label = QLabel("0 selected")
        self.status.addPermanentWidget(self.selected_label)

    # ---------- Global Methods & Event Bindings ----------
    def scan_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder to scan")
        if not folder: 
            return
        drive_name, ok = QInputDialog.getText(self, "Drive name", "Short name for this drive:")
        if not ok or not drive_name.strip(): 
            return
        purchase, _ = QInputDialog.getText(self, "Purchase date", "Purchase date (MM/YYYY or YYYY-MM-DD) — optional:")
        csha = QMessageBox.question(self, "SHA256", "Compute SHA-256 for files? (slower)", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes
        
        dlg = QProgressDialog("Scanning...", "Cancel", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.show()
        
        t = ScanThread(folder, drive_name.strip(), purchase.strip(), csha, workers=max(1, min(32, (os.cpu_count() or 2) * 2)), parent=self)
        t.progress.connect(dlg.setValue)
        t.error.connect(lambda msg: (dlg.close(), QMessageBox.critical(self, "Scan error", str(msg))))
        t.finished.connect(lambda cp, dn, pd: (dlg.close(), self.db.insert_drive(dn, pd, cp), self.refresh_all(), QMessageBox.information(self, "Done", f"Scanned drive '{dn}'")))
        self._register_worker(t)
        t.start()

    def import_csvs(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select CSVs", str(CSV_DIR), "CSV files (*.csv)")
        for f in files:
            dname = Path(f).stem
            if QMessageBox.question(self, "Import", f"Import '{f}' as '{dname}'?", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
                dlg = QProgressDialog(f"Importing {dname}...", "Cancel", 0, 0, self)
                dlg.show()
                t = ImportThread(f, dname, parent=self)
                t.progress.connect(lambda n: dlg.setLabelText(f"Inserted: {n}"))
                t.error.connect(lambda msg: (dlg.close(), QMessageBox.critical(self, "Error", str(msg))))
                t.finished.connect(lambda cnt, dn: (dlg.close(), self.refresh_all(), QMessageBox.information(self, "Done", f"Imported {cnt} rows for {dn}")))
                self._register_worker(t)
                t.start()

    def toggle_theme(self):
        self.is_dark_mode = not self.is_dark_mode
        if self.is_dark_mode:
            dark_ss = """
                QMainWindow, QWidget { background-color: #1e1e1e; color: #d4d4d4; }
                QTableWidget, QTreeWidget, QListWidget, QPlainTextEdit, QTableView {
                    background-color: #252526; color: #d4d4d4; border: 1px solid #3e3e42; alternate-background-color: #2d2d30;
                }
                QTableView::indicator, QTableWidget::indicator { width: 18px; height: 18px; }
                QHeaderView::section { background-color: #333333; color: #ffffff; padding: 4px; border: 1px solid #3e3e42; }
                QLineEdit, QComboBox, QDateEdit, QSpinBox { background-color: #333333; color: #d4d4d4; border: 1px solid #555; padding: 3px; border-radius: 3px; }
                QPushButton { background-color: #0e639c; color: #ffffff; border: none; padding: 6px 12px; border-radius: 3px; font-weight: bold; }
                QPushButton:hover { background-color: #1177bb; }
                QTabBar::tab { background-color: #2d2d30; color: #d4d4d4; padding: 8px 16px; border: 1px solid #3e3e42; }
                QTabBar::tab:selected { background-color: #1e1e1e; font-weight: bold; border-bottom: 2px solid #0e639c; }
                QToolBar { border: none; background-color: #2d2d30; }
                QMenu { background-color: #252526; color: #d4d4d4; border: 1px solid #3e3e42; }
                QMenu::item:selected { background-color: #0e639c; }
            """
            self.setStyleSheet(dark_ss)
            self.address_bar.setStyleSheet("background-color: #333333; color: #ffffff; padding: 4px; font-weight: bold; border: 1px solid #555;")
            self.ms_address_bar.setStyleSheet("background-color: #333333; color: #ffffff; padding: 4px; font-weight: bold; border: 1px solid #555;")
        else:
            self.setStyleSheet("")
            self.address_bar.setStyleSheet("background-color: #ffffff; color: #000000; padding: 4px; font-weight: bold; border: 1px solid #ccc;")
            self.ms_address_bar.setStyleSheet("background-color: #e8f4f8; color: #000000; padding: 4px; font-weight: bold; border: 1px solid #b8daff;")
        
        self.update_charts() 

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
            
        finally:
            QApplication.restoreOverrideCursor()
            dlg.close()

    # ---------- Advanced Drives Table ----------
    def refresh_drives(self):
        self.drives_table.setSortingEnabled(False)
        self.drives_table.setRowCount(0)
        
        self.ex_drive.blockSignals(True)
        self.ex_drive.clear()
        self.ex_drive.addItem("Any Drive")
        
        self.as_drive.blockSignals(True)
        self.as_drive.clear()
        self.as_drive.addItem("Any Drive")
        
        self.stat_drive.blockSignals(True)
        self.stat_drive.clear()
        self.stat_drive.addItem("Any Drive")
        
        summary = self.db.drives_summary()
        self.drives_table.setRowCount(len(summary))
        
        for row, (drive_name, purchase_date, scanned_at, csv_path, file_count, total_size) in enumerate(summary):
            age_str, age_days = age_from_date(parse_purchase_date(purchase_date))
            
            self.ex_drive.addItem(drive_name)
            self.as_drive.addItem(drive_name)
            self.stat_drive.addItem(drive_name)
            
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.setContentsMargins(0,0,0,0)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk_box = QCheckBox()
            chk_box.setProperty("drive_name", drive_name)
            chk_box.toggled.connect(self.update_selected_label)
            chk_layout.addWidget(chk_box)
            
            self.drives_table.setCellWidget(row, 0, chk_widget)
            self.drives_table.setItem(row, 1, SmartTableItem(str(drive_name), str(drive_name).lower()))
            self.drives_table.setItem(row, 2, SmartTableItem(str(file_count), file_count))
            self.drives_table.setItem(row, 3, SmartTableItem(human_size(total_size), total_size))
            self.drives_table.setItem(row, 4, SmartTableItem(age_str, age_days))
            self.drives_table.setItem(row, 5, SmartTableItem(str(scanned_at), str(scanned_at)))
            
        self.drives_table.setColumnWidth(0, 50)
        self.drives_table.setColumnWidth(1, 200)
        self.drives_table.setColumnWidth(2, 120)
        self.drives_table.setColumnWidth(3, 120)
        
        self.ex_drive.blockSignals(False)
        self.as_drive.blockSignals(False)
        self.stat_drive.blockSignals(False)
        
        self.drives_table.setSortingEnabled(True)
        self.update_selected_label()

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
        if QMessageBox.question(self, "Delete drives", f"Delete drives and records?\n{names}", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes: 
            return
        
        cur = self.db.conn.cursor()
        for dname in sel_items:
            cur.execute("SELECT csv_path FROM drives WHERE drive_name = ?", (dname,))
            res = cur.fetchone()
            if res and res[0] and os.path.exists(res[0]):
                try: 
                    shutil.move(res[0], OLD_DATA_DIR / Path(res[0]).name)
                except Exception: 
                    pass
            self.db.delete_drive(dname)
        self.refresh_all()

    # ---------- Dynamic Advanced Charts & Statistics ----------
    def update_charts(self):
        if not MATPLOTLIB_AVAILABLE or not PANDAS_AVAILABLE or not self.figure: 
            return
            
        mode = self.chart_combo.currentIndex()
        c_type = self.stat_chart_type.currentText()
        target_drive = self.stat_drive.currentText()
        
        d_from = self.stat_date_from.date().toString("yyyy-MM-dd")
        d_to = self.stat_date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
        
        drive_filter_sql = "" if target_drive == "Any Drive" else f"AND drive = '{target_drive}'"
        date_filter_sql = f"AND modified >= '{d_from}' AND modified <= '{d_to}'"
        
        self.figure.clear()
        bg_color = '#1e1e1e' if self.is_dark_mode else '#ffffff'
        text_color = 'white' if self.is_dark_mode else 'black'
        spine_color = '#555' if self.is_dark_mode else '#ccc'
        
        self.figure.patch.set_facecolor(bg_color)
        ax = self.figure.add_subplot(111)
        ax.set_facecolor(bg_color)
        
        cur = self.db.conn.cursor()
        
        try:
            if mode == 0 or mode == 1:
                cur.execute(f"SELECT drive, COUNT(id), COALESCE(SUM(size),0) FROM files WHERE is_folder=0 {date_filter_sql} GROUP BY drive;")
                rows = [{"drive": d, "files": f, "size": s} for d, f, s in cur.fetchall()]
                if target_drive != "Any Drive": 
                    rows = [r for r in rows if r["drive"] == target_drive]
                if not rows: 
                    return
                df = pd.DataFrame(rows)
                
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
                cur.execute(f"SELECT COALESCE(NULLIF(extension, ''), 'unknown') as ext, COUNT(*), COALESCE(SUM(size),0) FROM files WHERE is_folder=0 {drive_filter_sql} {date_filter_sql} GROUP BY ext;")
                ext_data = cur.fetchall()
                if not ext_data: 
                    return
                df = pd.DataFrame(ext_data, columns=["ext", "count", "size"])
                if mode == 2:
                    df = df.sort_values(by="count", ascending=True).tail(15)
                    if c_type == "Bar Chart": 
                        ax.bar(df["ext"], df["count"], color='#2ecc71')
                    elif c_type == "Line Chart": 
                        ax.plot(df["ext"], df["count"], marker='s', color='#2ecc71')
                    else: 
                        ax.barh(df["ext"], df["count"], color='#2ecc71')
                    ax.set_title(f"Top 15 File Formats by Frequency ({target_drive})")
                else:
                    df = df.sort_values(by="size", ascending=True).tail(15)
                    if c_type == "Bar Chart": 
                        ax.bar(df["ext"], df["size"] / (1024**3), color='#9b59b6')
                    elif c_type == "Line Chart": 
                        ax.plot(df["ext"], df["size"] / (1024**3), marker='s', color='#9b59b6')
                    else: 
                        ax.barh(df["ext"], df["size"] / (1024**3), color='#9b59b6')
                    ax.set_title(f"Top 15 Formats by Storage Space ({target_drive})")
                if c_type != "Horizontal Bar": 
                    ax.tick_params(axis='x', rotation=45)
                
            elif mode == 4:
                cur.execute(f"SELECT size FROM files WHERE is_folder=0 {drive_filter_sql} {date_filter_sql}")
                bins = {'<1 MB': 0, '1-10 MB': 0, '10-100 MB': 0, '100MB-1GB': 0, '>1 GB': 0}
                for (sz,) in cur.fetchall():
                    if sz is None: continue
                    mb = sz / (1024 * 1024)
                    if mb < 1: bins['<1 MB'] += 1
                    elif mb < 10: bins['1-10 MB'] += 1
                    elif mb < 100: bins['10-100 MB'] += 1
                    elif mb < 1024: bins['100MB-1GB'] += 1
                    else: bins['>1 GB'] += 1
                if c_type == "Horizontal Bar": 
                    ax.barh(list(bins.keys()), list(bins.values()), color='#e74c3c')
                elif c_type == "Line Chart": 
                    ax.plot(list(bins.keys()), list(bins.values()), marker='o', color='#e74c3c')
                else: 
                    ax.bar(list(bins.keys()), list(bins.values()), color='#e74c3c')
                ax.set_title(f"File Size Distribution ({target_drive})")
                
            elif mode == 5:
                cur.execute(f"SELECT SUBSTR(modified, 1, 4) as yr, COUNT(*) FROM files WHERE modified != '' AND is_folder=0 {drive_filter_sql} {date_filter_sql} GROUP BY yr;")
                data = cur.fetchall()
                if not data: return
                valid_data = [(int(y), c) for y, c in data if y and str(y).isdigit() and 1980 < int(y) <= datetime.now().year + 1]
                if not valid_data: return
                df = pd.DataFrame(valid_data, columns=["year", "count"]).sort_values(by="year")
                if c_type == "Bar Chart": 
                    ax.bar(df["year"], df["count"], color='#f1c40f')
                else: 
                    ax.plot(df["year"], df["count"], marker='o', color='#f1c40f', linestyle='-', linewidth=2)
                    ax.fill_between(df["year"], df["count"], color='#f1c40f', alpha=0.2)
                ax.set_title(f"File Modification Timeline ({target_drive})")
                ax.grid(True, linestyle='--', alpha=0.3, color=spine_color)
                
            elif mode == 6:
                cur.execute(f"SELECT name, size FROM files WHERE is_folder=0 {drive_filter_sql} {date_filter_sql} ORDER BY size DESC LIMIT 20;")
                data = cur.fetchall()
                if not data: return
                df = pd.DataFrame(data, columns=["name", "size"]).sort_values(by="size", ascending=True)
                ax.barh(df["name"], df["size"] / (1024**3), color='#e84393')
                ax.set_xlabel("Size (GB)")
                ax.set_title(f"Top 20 Largest Files ({target_drive})")
                
            elif mode == 7:
                cur.execute(f"SELECT SUBSTR(modified, 1, 4) as yr, SUM(size) FROM files WHERE modified != '' AND is_folder=0 {drive_filter_sql} {date_filter_sql} GROUP BY yr;")
                data = cur.fetchall()
                if not data: return
                valid_data = [(int(y), s / (1024**3)) for y, s in data if y and str(y).isdigit() and 1980 < int(y) <= datetime.now().year + 1]
                if not valid_data: return
                df = pd.DataFrame(valid_data, columns=["year", "size"]).sort_values(by="year")
                if c_type == "Bar Chart": 
                    ax.bar(df["year"], df["size"], color='#00cec9')
                else:
                    ax.plot(df["year"], df["size"], marker='s', color='#00cec9', linestyle='-', linewidth=2)
                    ax.fill_between(df["year"], df["size"], color='#00cec9', alpha=0.2)
                ax.set_title(f"Storage Usage by Year ({target_drive})")
                ax.grid(True, linestyle='--', alpha=0.3, color=spine_color)
                
            elif mode == 8:
                cur.execute(f"SELECT modified FROM files WHERE modified != '' AND is_folder=0 {drive_filter_sql} {date_filter_sql}")
                bins = {'<1 Month': 0, '1-6 Months': 0, '6m-1 Year': 0, '1-3 Years': 0, '>3 Years': 0}
                now = datetime.now()
                for (m,) in cur.fetchall():
                    try:
                        d = datetime.fromisoformat(m[:19])
                        days = (now - d).days
                        if days < 30: bins['<1 Month'] += 1
                        elif days < 180: bins['1-6 Months'] += 1
                        elif days < 365: bins['6m-1 Year'] += 1
                        elif days < 1095: bins['1-3 Years'] += 1
                        else: bins['>3 Years'] += 1
                    except: pass
                if c_type == "Horizontal Bar": 
                    ax.barh(list(bins.keys()), list(bins.values()), color='#8e44ad')
                elif c_type == "Line Chart": 
                    ax.plot(list(bins.keys()), list(bins.values()), marker='o', color='#8e44ad')
                else: 
                    ax.bar(list(bins.keys()), list(bins.values()), color='#8e44ad')
                ax.set_title(f"File Age Distribution ({target_drive})")

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

    def _get_native_icon(self, real_path: str, is_folder: bool, ext: str = "") -> QIcon:
        """Icon cache prevents system freeze when loading thousands of native icons."""
        if is_folder:
            if "folder" not in self._icon_cache:
                self._icon_cache["folder"] = self.style().standardIcon(QStyle.SP_DirIcon)
            return self._icon_cache["folder"]
            
        ext = str(ext).lower()
        if ext not in self._icon_cache:
            if HAS_ICON_PROVIDER and self.icon_provider and real_path and os.path.exists(real_path):
                self._icon_cache[ext] = self.icon_provider.icon(QFileInfo(real_path))
            else:
                self._icon_cache[ext] = self.style().standardIcon(QStyle.SP_FileIcon)
        return self._icon_cache[ext]

    # ---------- Global Explorer Logic ----------
    def refresh_folder_tree(self):
        self.folder_tree.clear()
        root = QTreeWidgetItem(self.folder_tree, ["/ (Home)"])
        root.setData(0, Qt.UserRole, "")
        root.setIcon(0, self.style().standardIcon(QStyle.SP_DirHomeIcon) if hasattr(QStyle, 'SP_DirHomeIcon') else self.style().standardIcon(QStyle.SP_DirIcon))
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
            child.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon))
            child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        item.setData(0, Qt.UserRole + 1, True)

    def on_folder_click(self, item: QTreeWidgetItem, col: int):
        self.load_directory(item.data(0, Qt.UserRole))

    def load_directory(self, prefix: str):
        self.current_explorer_prefix = prefix
        self.address_bar.setText(prefix if prefix else "/")
        folders = self._get_subfolders(prefix)
        cur = self.db.conn.cursor()
        
        target_drive = self.ex_drive.currentText()
        drive_filter_sql = "" if target_drive == "Any Drive" else f"AND drive = '{target_drive}'"
        
        if not prefix: 
            cur.execute(f"SELECT relpath, name, size, extension, modified, drive, fullpath FROM files WHERE INSTR(relpath, '/') = 0 AND is_folder = 0 {drive_filter_sql} LIMIT {MAX_RENDER_ROWS}")
        else:
            plen = len(prefix) + 1
            cur.execute(f"SELECT relpath, name, size, extension, modified, drive, fullpath FROM files WHERE relpath LIKE ? AND INSTR(SUBSTR(relpath, {plen}), '/') = 0 AND is_folder = 0 {drive_filter_sql} LIMIT {MAX_RENDER_ROWS}", (f"{prefix}%",))
            
        self._populate_file_table(folders, cur.fetchall(), prefix)

    def navigate_up(self):
        curr = self.current_explorer_prefix.strip("/")
        if not curr: 
            return
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
            if not found: 
                break
                
        if current_item: 
            self.folder_tree.clearSelection()
            current_item.setSelected(True)
            self.folder_tree.scrollToItem(current_item)

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
        
        query = f"SELECT relpath, name, size, extension, modified, drive, fullpath, is_folder FROM files WHERE (name LIKE ? OR relpath LIKE ?) AND is_folder = 0 {drive_filter_sql} LIMIT {MAX_RENDER_ROWS}"
        
        self.search_thread = SearchThread(DB_FILE, query, (f"%{txt}%", f"%{txt}%"), self)
        self.search_thread.finished.connect(lambda rows, t=txt: self._on_quick_search_done(rows, t))
        self.search_thread.error.connect(self._on_search_error)
        self.search_dlg.canceled.connect(self.search_thread.cancel)
        self.search_thread.start()

    def _on_quick_search_done(self, rows, txt):
        if self.search_dlg: self.search_dlg.close()
        
        if len(rows) == MAX_RENDER_ROWS:
            self.status.showMessage(f"Showing Top {MAX_RENDER_ROWS} Search Results to ensure speed. Use Advanced Search for precision.", 5000)
        else:
            self.status.showMessage(f"Search Complete: Found {len(rows)} files.", 5000)
            
        self.address_bar.setText(f"Search Results for: '{txt}'")
        self._populate_file_table([], rows, "", is_search=True)
        self.preview_text.setPlainText(f"Search results for: '{txt}'")
        self.preview_image.clear()

    def _on_search_error(self, err_msg):
        if self.search_dlg: self.search_dlg.close()
        QMessageBox.critical(self, "Search Error", f"An error occurred during search:\n{err_msg}")

    def clear_explorer_search(self):
        self.ex_search.clear()
        self.load_directory(self.current_explorer_prefix)

    def _populate_file_table(self, folders, files, current_prefix, is_search=False):
        fmap = {}
        for rp, name, size, ext, mod, drive, fp, *rest in files:
            is_f = rest[0] if rest else 0
            rp = str(rp) if rp else ""
            if rp not in fmap: 
                fmap[rp] = {'name': str(name) if name else "", 'size': size or 0, 'ext': str(ext) if ext else "", 'mod': str(mod) if mod else "", 'drives': set(), 'fullpath': fp, 'is_folder': is_f}
            if drive: 
                fmap[rp]['drives'].add(str(drive))
            
        rows = []
        row_idx = 1
        
        for f in folders:
            cur = self.db.conn.cursor()
            cur.execute("SELECT fullpath FROM files WHERE relpath LIKE ? AND fullpath != '' LIMIT 1", (f"{current_prefix}{f}/%",))
            r = cur.fetchone()
            sample_real = os.path.dirname(r[0]) if r and r[0] else ""
            
            icon = self._get_native_icon(sample_real, True, "")
            user_data = ("folder", f"{current_prefix}{f}/")
            rows.append({
                "display": [str(row_idx), f, "", "File folder", "", ""],
                "sort_keys": [row_idx, (0, f.lower()), (0, -1), (0, "file folder"), (0, ""), (0, "")],
                "user_data": user_data,
                "user_data_1": sample_real,
                "user_data_2": True,
                "icon": icon
            })
            row_idx += 1
            
        for rp, info in fmap.items():
            name_disp = rp if is_search else info['name']
            icon = self._get_native_icon(info['fullpath'], False, info['ext'])
            drives_str = ", ".join(sorted(info['drives']))
            ext_str = info['ext'] or "file"
            
            rows.append({
                "display": [str(row_idx), name_disp, human_size(info['size']), ext_str, info['mod'], drives_str],
                "sort_keys": [row_idx, (1, name_disp.lower()), (1, info['size']), (1, ext_str.lower()), (1, info['mod']), (1, drives_str.lower())],
                "user_data": ("file", rp),
                "user_data_1": info['fullpath'],
                "user_data_2": False,
                "icon": icon
            })
            row_idx += 1
            
        model = FastTableModel(["S.No", "Name", "Size", "Type", "Modified", "Found In Drives"], rows)
        self.file_table.setModel(model)
        self.file_table.setColumnWidth(0, 60); self.file_table.setColumnWidth(1, 350); self.file_table.setColumnWidth(2, 100)
        self.file_table.setColumnWidth(3, 100); self.file_table.setColumnWidth(4, 180)

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
            self.open_local_file(path)

    def on_file_click(self, index: QModelIndex):
        model = self.file_table.model()
        if not model: return
        data = model.data(model.index(index.row(), 1), Qt.UserRole)
        if not data: return
        typ, payload = data
        
        self.preview_image.clear()
        if typ == "folder": 
            self.preview_text.setPlainText(f"Folder:\n{payload}")
            return
            
        cur = self.db.conn.cursor()
        cur.execute("SELECT drive, fullpath, size, sha FROM files WHERE relpath = ?;", (payload,))
        rows = cur.fetchall()
        if not rows: return
        
        lines = [f"File: {model.data(model.index(index.row(), 1), Qt.DisplayRole)}", f"Path: {payload}", f"\nAvailable across {len(rows)} drive(s):"]
        sample = None
        for d, full, size, sha in rows:
            lines.append(f" • Drive [{d}]\n   ↳ {full if full else 'N/A'}\n   ↳ Size: {human_size(size)} | SHA: {sha[:12] if sha else 'None'}\n")
            if not sample and full and os.path.exists(full) and os.path.splitext(full)[1].lower() in (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".ico"): 
                sample = full
                
        self.preview_text.setPlainText("\n".join(lines))
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
            cur.execute("SELECT COUNT(*), SUM(size) FROM files WHERE relpath LIKE ?", (f"{relpath}%",))
            cnt, sz = cur.fetchone()
            layout.addRow("Type:", QLabel("File Folder"))
            layout.addRow("Location:", QLabel(relpath))
            layout.addRow("Contains:", QLabel(f"{cnt} files"))
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
                if typ == "file":
                    act_open = QAction("Open Local File (if available)", self)
                    act_open.triggered.connect(lambda: self.open_local_file(relpath))
                    menu.addAction(act_open)
                
                act_copy = QAction("Copy Relative Path", self)
                act_copy.triggered.connect(lambda: QApplication.clipboard().setText(relpath))
                
                act_prop = QAction("Properties", self)
                act_prop.triggered.connect(lambda: self.show_properties(relpath, typ=="folder"))
                
                menu.addAction(act_copy)
                menu.addAction(act_prop)
                menu.addSeparator()
                
        act_add_ms = QAction(f"⭐ Add {len(sel_rows)} Selected to MySpace Sandbox", self)
        act_add_ms.triggered.connect(lambda: self.add_selected_to_myspace(table_source=table_source))
        menu.addAction(act_add_ms)
        menu.exec(table_source.viewport().mapToGlobal(pos))

    def on_image_loaded(self, path, pix):
        if isinstance(pix, QPixmap) and not pix.isNull(): self.preview_image.setPixmap(pix)
        
    def on_ms_image_loaded(self, path, pix):
        if isinstance(pix, QPixmap) and not pix.isNull(): self.ms_preview_image.setPixmap(pix)

    def open_local_file(self, relpath: str):
        cur = self.db.conn.cursor()
        cur.execute("SELECT fullpath FROM files WHERE relpath = ? LIMIT 1;", (relpath,))
        r = cur.fetchone()
        if r and r[0] and os.path.exists(r[0]):
            try: 
                os.startfile(r[0]) if sys.platform=="win32" else os.system(f"open '{r[0]}'" if sys.platform=="darwin" else f"xdg-open '{r[0]}'")
            except Exception as e: 
                QMessageBox.warning(self, "Open", str(e))
        else: 
            QMessageBox.information(self, "Open", "No accessible path for this file on the local machine.")

    # ---------- MySpace Virtual Sandbox Logic ----------
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
        root.setIcon(0, self.style().standardIcon(QStyle.SP_DirHomeIcon) if hasattr(QStyle, 'SP_DirHomeIcon') else self.style().standardIcon(QStyle.SP_DirIcon))
        root.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        root.setExpanded(True)

    def _get_ms_subfolders(self, parent_path: str) -> List[str]:
        cur = self.db.conn.cursor()
        cur.execute("SELECT name FROM myspace WHERE parent_path = ? AND is_folder = 1 ORDER BY name", (parent_path,))
        return [str(r[0]) for r in cur.fetchall() if r[0]]

    def on_ms_folder_expand(self, item: QTreeWidgetItem):
        if item.data(0, Qt.UserRole + 1): return
        parent_path = item.data(0, Qt.UserRole)
        for sf in self._get_ms_subfolders(parent_path):
            child = QTreeWidgetItem(item, [sf])
            child.setData(0, Qt.UserRole, f"{parent_path}{sf}/")
            child.setIcon(0, self.style().standardIcon(QStyle.SP_DirIcon))
            child.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        item.setData(0, Qt.UserRole + 1, True)

    def on_ms_folder_click(self, item: QTreeWidgetItem, col: int):
        self.load_myspace_directory(item.data(0, Qt.UserRole))

    def load_myspace_directory(self, parent_path: str):
        self.current_myspace_prefix = parent_path
        self.ms_address_bar.setText(parent_path)
        self.ms_search.clear()
        
        folders = self._get_ms_subfolders(parent_path)
        cur = self.db.conn.cursor()
        cur.execute("SELECT id, name, size, extension, real_path, modified FROM myspace WHERE parent_path = ? AND is_folder = 0", (parent_path,))
        files = cur.fetchall()
        
        rows = []
        row_idx = 1
        dir_icon = self.style().standardIcon(QStyle.SP_DirIcon)
        
        for f in folders:
            rows.append({
                "display": [str(row_idx), f, "", "Virtual Folder", "Inside Sandbox"],
                "sort_keys": [row_idx, (0, f.lower()), (0, -1), (0, "virtual folder"), (0, "")],
                "user_data": ("folder", f"{parent_path}{f}/", -1),
                "icon": dir_icon
            })
            row_idx += 1
            
        for db_id, n, s, ext, rp, mod in files:
            icon = self._get_native_icon(rp, False, ext)
            s_val = s if s else 0
            rows.append({
                "display": [str(row_idx), str(n), human_size(s_val), str(ext) if ext else "file", str(rp) if rp else ""],
                "sort_keys": [row_idx, (1, str(n).lower()), (1, s_val), (1, str(ext).lower() if ext else ""), (1, str(rp).lower() if rp else "")],
                "user_data": ("file", str(rp), db_id),
                "icon": icon
            })
            row_idx += 1
            
        model = FastTableModel(["S.No", "Name", "Size", "Type", "Real Target Source"], rows)
        self.ms_file_table.setModel(model)
        self.ms_file_table.setColumnWidth(0, 60); self.ms_file_table.setColumnWidth(1, 350)
        self.ms_file_table.setColumnWidth(2, 100); self.ms_file_table.setColumnWidth(3, 100)

    def ms_search_changed(self, text):
        model = self.ms_file_table.model()
        if not model: return
        text = text.lower()
        for r in range(model.rowCount()):
            name = model.data(model.index(r, 1), Qt.DisplayRole)
            if text in str(name).lower():
                self.ms_file_table.setRowHidden(r, False)
            else:
                self.ms_file_table.setRowHidden(r, True)

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
                            cur.execute("INSERT INTO myspace (parent_path, name, is_folder, real_path, size, extension, modified) VALUES (?, ?, 0, ?, ?, ?, ?)",
                                        (curr_parent, f, fp, sz, ext, mod))
                            added_files += 1
                else:
                    f = os.path.basename(p)
                    sz = os.path.getsize(p)
                    mod = datetime.fromtimestamp(os.path.getmtime(p)).isoformat()
                    ext = os.path.splitext(f)[1].lower()
                    cur.execute("INSERT INTO myspace (parent_path, name, is_folder, real_path, size, extension, modified) VALUES (?, ?, 0, ?, ?, ?, ?)",
                                (base_dest, f, p, sz, ext, mod))
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
        
        self.ms_preview_image.clear()
        if typ == "folder":
            self.ms_preview_text.setPlainText(f"Virtual Folder:\n{path}")
            return
            
        cur = self.db.conn.cursor()
        cur.execute("SELECT real_path, size, extension, modified FROM myspace WHERE id = ?", (db_id,))
        row = cur.fetchone()
        if not row: return
        
        real_path, size, ext, mod = row
        lines = [
            f"File: {model.data(model.index(index.row(), 1), Qt.DisplayRole)}",
            f"Virtual Container: {self.current_myspace_prefix}",
            f"Real Target Data: {real_path}",
            f"Size: {human_size(size)}",
            f"Modified: {mod}"
        ]
        self.ms_preview_text.setPlainText("\n".join(lines))
        
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
                        current_item = child; break
            if current_item: 
                self.ms_folder_tree.clearSelection()
                current_item.setSelected(True)
        else:
            if path and os.path.exists(path):
                try: os.startfile(path) if sys.platform=="win32" else os.system(f"open '{path}'" if sys.platform=="darwin" else f"xdg-open '{path}'")
                except Exception as e: QMessageBox.warning(self, "Open", str(e))
            else:
                QMessageBox.warning(self, "Not Found", "Target file is missing locally.")

    def ms_context_menu(self, pos):
        idx = self.ms_file_table.indexAt(pos)
        menu = QMenu(self)
        act_new_folder = QAction("Create New Virtual Folder", self)
        act_new_folder.triggered.connect(self.ms_create_folder)
        menu.addAction(act_new_folder)
        
        if idx.isValid():
            model = self.ms_file_table.model()
            data = model.data(model.index(idx.row(), 1), Qt.UserRole)
            if data:
                menu.addSeparator()
                typ, path, db_id = data
                act_rename = QAction("Rename in Sandbox", self)
                act_rename.triggered.connect(lambda: self.ms_rename_item(typ, path, db_id, idx.row()))
                
                act_move = QAction("Move to Virtual Folder...", self)
                act_move.triggered.connect(lambda: self.ms_move_item(typ, path, db_id, idx.row()))
                
                act_delete = QAction("Remove from Sandbox", self)
                act_delete.triggered.connect(lambda: self.ms_delete_item(typ, path, db_id))
                
                menu.addAction(act_rename)
                menu.addAction(act_move)
                menu.addSeparator()
                menu.addAction(act_delete)
                
        menu.exec(self.ms_file_table.viewport().mapToGlobal(pos))

    def ms_create_folder(self):
        name, ok = QInputDialog.getText(self, "New Virtual Folder", "Folder Name:")
        if not ok or not name.strip(): return
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
        new_name, ok = QInputDialog.getText(self, "Rename", "New Name:", QLineEdit.Normal, str(old_name))
        if not ok or not new_name.strip() or new_name == old_name: return
        new_name = new_name.strip()
        cur = self.db.conn.cursor()
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
        if not ok or dest == self.current_myspace_prefix: return
        
        model = self.ms_file_table.model()
        old_name = model.data(model.index(row_idx, 1), Qt.DisplayRole)
        if typ == "file": 
            cur.execute("UPDATE myspace SET parent_path = ? WHERE id = ?", (dest, db_id))
        else:
            cur.execute("UPDATE myspace SET parent_path = ? WHERE parent_path = ? AND name = ? AND is_folder = 1", (dest, self.current_myspace_prefix, old_name))
            old_full_path, new_full_path = path, f"{dest}{old_name}/"
            cur.execute("UPDATE myspace SET parent_path = ? || SUBSTR(parent_path, LENGTH(?) + 1) WHERE parent_path LIKE ?", (new_full_path, old_full_path, f"{old_full_path}%"))
            
        self.db.conn.commit()
        self.refresh_myspace_tree()
        self.load_myspace_directory(self.current_myspace_prefix)

    def add_selected_to_myspace(self, table_source=None):
        if table_source is None: 
            table_source = self.file_table
        sel_rows = table_source.selectionModel().selectedRows()
        if not sel_rows: return
        
        model = table_source.model()
        if not model: return
        
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
        finally:
            QApplication.restoreOverrideCursor()
            
        if self.tabs.tabText(self.tabs.currentIndex()) == "⭐ MySpace Sandbox":
            self.load_myspace_directory(self.current_myspace_prefix)
        QMessageBox.information(self, "Success", f"Recursively mapped {added_files} files and {added_folders} folders into Sandbox:\n{base_dest}")

    # ---------- Precision Advanced Search Logic ----------
    def run_advanced_search(self):
        name = self.as_name.text().strip()
        match_type = self.as_match_type.currentText()
        item_type = self.as_type.currentText()
        folder = self.as_folder.text().strip()
        ext = self.as_ext.text().strip()
        drive = self.as_drive.currentText()
        min_sz = self.as_min_size.text().strip()
        max_sz = self.as_max_size.text().strip()
        
        d_from = self.as_date_from.date().toString("yyyy-MM-dd")
        d_to = self.as_date_to.date().toString("yyyy-MM-dd") + "T23:59:59"
        
        params = []
        if item_type == "Folders Only":
            query = """
            SELECT DISTINCT 
                CASE WHEN is_folder = 1 THEN relpath ELSE SUBSTR(relpath, 1, LENGTH(relpath) - LENGTH(COALESCE(name, ''))) END AS rp,
                '' AS n, 
                0 AS s, 
                'Folder' AS e, 
                MAX(modified) AS m, 
                drive AS d, 
                CASE WHEN is_folder = 1 THEN fullpath ELSE SUBSTR(fullpath, 1, LENGTH(fullpath) - LENGTH(COALESCE(name, ''))) END AS fp,
                1 AS is_f
            FROM files 
            WHERE 1=1
            """
            if folder:
                folder_clean = folder.replace("\\", "/")
                query += " AND (CASE WHEN is_folder = 1 THEN relpath ELSE SUBSTR(relpath, 1, LENGTH(relpath) - LENGTH(COALESCE(name, ''))) END) LIKE ?"
                params.append(f"%{folder_clean}%")
                
            if name:
                query += " AND (CASE WHEN is_folder = 1 THEN relpath ELSE SUBSTR(relpath, 1, LENGTH(relpath) - LENGTH(COALESCE(name, ''))) END) LIKE ?"
                params.append(f"%{name}%")
                
            if drive and drive != "Any Drive":
                query += " AND drive = ?"
                params.append(drive)
                
            query += " GROUP BY rp, d"
        else:
            query = "SELECT relpath, name, size, extension, modified, drive, fullpath, is_folder FROM files WHERE 1=1"
            if item_type == "Files Only": 
                query += " AND is_folder = 0"
                
            if name:
                if match_type == "Contains": query += " AND name LIKE ?"; params.append(f"%{name}%")
                elif match_type == "Exact Match": query += " AND name = ?"; params.append(name)
                elif match_type == "Starts With": query += " AND name LIKE ?"; params.append(f"{name}%")
                elif match_type == "Ends With": query += " AND name LIKE ?"; params.append(f"%{name}")
                
            if folder: 
                folder_clean = folder.replace("\\", "/")
                query += " AND SUBSTR(relpath, 1, LENGTH(relpath) - LENGTH(COALESCE(name, ''))) LIKE ?"
                params.append(f"%{folder_clean}%")
                
            if drive and drive != "Any Drive": 
                query += " AND drive = ?"; params.append(drive)
                
            if ext: 
                if not ext.startswith("."): ext = "." + ext
                query += " AND extension = ?"; params.append(ext.lower())
            if min_sz:
                try: query += " AND size >= ?"; params.append(int(float(min_sz) * 1024 * 1024))
                except Exception: pass
            if max_sz:
                try: query += " AND size <= ?"; params.append(int(float(max_sz) * 1024 * 1024))
                except Exception: pass
                
            query += " AND modified >= ? AND modified <= ?"
            params.extend([d_from, d_to])
            
        self.search_dlg = QProgressDialog("Advanced Searching... Please wait.", "Cancel", 0, 0, self)
        self.search_dlg.setWindowModality(Qt.WindowModal)
        self.search_dlg.show()
        
        self.search_thread = SearchThread(DB_FILE, query, params, self)
        
        if item_type == "Folders Only":
            self.search_thread.finished.connect(lambda rows: self._on_advanced_folder_search_done(rows, folder, name, match_type))
        else:
            self.search_thread.finished.connect(self._on_advanced_search_done)
            
        self.search_thread.error.connect(self._on_search_error)
        self.search_dlg.canceled.connect(self.search_thread.cancel)
        self.search_thread.start()

    def _on_advanced_folder_search_done(self, rows, target_folder, target_name, match_type):
        if self.search_dlg: self.search_dlg.close()
        
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
                else: continue
                
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
        self.status.showMessage(f"Search Complete: Found {len(rows)} items.", 5000)
        self._render_search_results(rows)

    def _render_search_results(self, rows):
        self.as_table.setUpdatesEnabled(False)
        self.as_table.setSortingEnabled(False)
        
        table_rows = []
        dir_icon = self.style().standardIcon(QStyle.SP_DirIcon)
        
        for r_idx, (rp, n, s, e, m, d, fp, is_f) in enumerate(rows):
            n_str = str(n) if n else ""
            if is_f and not n_str:
                clean_rp = str(rp).rstrip('/')
                n_str = clean_rp.split('/')[-1] if clean_rp else "/"
                
            icon = self._get_native_icon(fp, bool(is_f), e)
            
            table_rows.append({
                "display": [str(r_idx+1), n_str, str(rp), str(d), human_size(s or 0), str(e) if e else "Folder", str(m)],
                "sort_keys": [r_idx+1, (0 if is_f else 1, n_str.lower()), (0 if is_f else 1, str(rp).lower()), (0 if is_f else 1, str(d).lower()), (0 if is_f else 1, s or 0), (0 if is_f else 1, str(e).lower() if e else "folder"), (0 if is_f else 1, str(m).lower())],
                "user_data": ("folder" if is_f else "file", str(rp)),
                "user_data_1": str(fp) if fp else str(rp),
                "user_data_2": bool(is_f),
                "icon": icon
            })
            
        model = FastTableModel(["S.No", "Name", "RelPath", "Drive", "Size", "Type", "Modified"], table_rows)
        self.as_table.setModel(model)
        
        self.as_table.setColumnWidth(0, 60); self.as_table.setColumnWidth(1, 200)
        self.as_table.setColumnWidth(2, 350); self.as_table.setColumnWidth(4, 100)
            
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
        self.as_date_from.setDate(QDate(1990, 1, 1))
        self.as_date_to.setDate(QDate.currentDate().addDays(1))
        if self.as_table.model():
            self.as_table.model().rows = []
            self.as_table.model().layoutChanged.emit()

    def export_advanced_search(self):
        model = self.as_table.model()
        if not model or len(model.rows) == 0: 
            return QMessageBox.warning(self, "Empty", "No results to export.")
        path, _ = QFileDialog.getSaveFileName(self, "Save search results", str(DATA_DIR/f"search_{now_ts()}.csv"), "CSV (*.csv)")
        if not path: 
            return
        try:
            rows = [["S.No", "Name", "RelPath", "Drive", "Size", "Type", "Modified"]]
            for r in model.rows:
                rows.append(r["display"])
            with open(path, "w", encoding="utf-8", newline="") as fh: 
                csv.writer(fh).writerows(rows)
            QMessageBox.information(self, "Export", f"Saved {len(model.rows)} results to {path}")
        except Exception as e: 
            QMessageBox.critical(self, "Error", f"Failed to export: {e}")

    def on_as_double_click(self, index: QModelIndex):
        model = self.as_table.model()
        if not model: return
        real_path = model.rows[index.row()]["user_data_1"]
        is_f = model.rows[index.row()]["user_data_2"]
        if is_f:
            QMessageBox.information(self, "Folder Record", f"Database Path:\n{real_path}")
        else:
            if real_path and os.path.exists(real_path):
                try: os.startfile(real_path) if sys.platform=="win32" else os.system(f"open '{real_path}'" if sys.platform=="darwin" else f"xdg-open '{real_path}'")
                except Exception as e: QMessageBox.warning(self, "Open", str(e))
            else: 
                QMessageBox.warning(self, "Not Found", "Target file is disconnected or unavailable locally.")

    def as_context_menu(self, pos):
        if not self.as_table.itemAt(pos): return
        
        sel_rows = self.as_table.selectionModel().selectedRows()
        menu = QMenu(self)
        
        if len(sel_rows) == 1:
            model = self.as_table.model()
            row_data = model.rows[sel_rows[0].row()]
            real_path = row_data["user_data_1"]
            is_f = row_data["user_data_2"]
            rel_path = row_data["user_data"][1]
            
            if not is_f:
                act_open = QAction("Open Local File", self)
                act_open.triggered.connect(lambda: self.on_as_double_click(sel_rows[0]))
                menu.addAction(act_open)
                
            act_copy = QAction("Copy Path", self)
            act_copy.triggered.connect(lambda: QApplication.clipboard().setText(real_path if real_path else rel_path))
            
            act_prop = QAction("Properties", self)
            act_prop.triggered.connect(lambda: self.show_properties(rel_path, is_f))
            
            menu.addAction(act_copy)
            menu.addAction(act_prop)
            menu.addSeparator()
            
        act_add_ms = QAction(f"⭐ Add {len(sel_rows)} Selected to MySpace Sandbox", self)
        act_add_ms.triggered.connect(lambda: self.add_selected_to_myspace(table_source=self.as_table))
        menu.addAction(act_add_ms)
        menu.exec(self.as_table.viewport().mapToGlobal(pos))

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
        t.finished.connect(lambda res: (dlg.close(), setattr(self, 'last_compare_result', res), self.tabs.setCurrentIndex(4), self.display_compare_category("dup_by_sha")))
        self._register_worker(t)
        t.start()

    def run_compare_mode(self, category: str):
        if not self.last_compare_result or set(self.last_compare_result.get("selected_drives", [])) != set(self.selected_drives()): 
            return self.compare_selected()
        self.display_compare_category(category)

    def display_compare_category(self, category: str):
        if not self.last_compare_result: 
            return QMessageBox.information(self, "No data", "Run Compare first.")
        res = self.last_compare_result
        rows = []
        headers = []
        if category in ("dup_by_sha", "same_content_diff_path"): 
            headers = ["sha", "relpath", "drive", "size", "fullpath"]
            rows = [(d["sha"], d["relpath"], d["drive"], human_size(d["size"]), d["fullpath"]) for d in res[category]]
        elif category in ("same_name_diff_location", "name_conflicts"): 
            headers = ["name", "relpath", "drive", "size", "sha", "fullpath"]
            rows = [(d["name"], d["relpath"], d["drive"], human_size(d["size"]), d.get("sha", ""), d.get("fullpath", "")) for d in res[category]]
        elif category == "missing": 
            headers = ["relpath", "present_drive"]
            rows = [(d["relpath"], d["present_drive"]) for d in res["missing"]]
        
        self.comp_table.setUpdatesEnabled(False)
        self.comp_table.setSortingEnabled(False)
        
        full_headers = ["S.No"] + headers
        table_rows = []
        for r_idx, row_data in enumerate(rows):
            disp = [str(r_idx+1)] + [str(x) for x in row_data]
            sorts = [r_idx+1] + [(1, str(x).lower()) for x in row_data]
            table_rows.append({
                "display": disp,
                "sort_keys": sorts,
                "user_data": None,
                "icon": None
            })
            
        model = FastTableModel(full_headers, table_rows)
        self.comp_table.setModel(model)
        self.comp_table.setColumnWidth(0, 60)
        self.comp_table.setSortingEnabled(True)
        self.comp_table.setUpdatesEnabled(True)

    def comp_context_menu(self, pos):
        if not self.comp_table.itemAt(pos): return
        menu = QMenu(self)
        act_export = QAction("Export selected rows", self)
        act_export.triggered.connect(self.export_selected_comp_rows)
        menu.addAction(act_export)
        menu.exec(self.comp_table.viewport().mapToGlobal(pos))

    def export_selected_comp_rows(self):
        sel = self.comp_table.selectionModel().selectedRows()
        if not sel: return
        model = self.comp_table.model()
        if not model: return
        
        rows = []
        for idx in sel:
            rows.append(model.rows[idx.row()]["display"])
            
        path, _ = QFileDialog.getSaveFileName(self, "Save rows", str(DATA_DIR/f"selected_{now_ts()}.csv"), "CSV (*.csv)")
        if not path: return
        with open(path, "w", encoding="utf-8", newline="") as fh: 
            csv.writer(fh).writerows(rows)
        QMessageBox.information(self, "Export", f"Saved {len(rows)} rows.")

    def export_last_compare(self):
        if not self.last_compare_result: return
        path, _ = QFileDialog.getSaveFileName(self, "Save report", str(DATA_DIR/f"report_{now_ts()}.csv"), "CSV (*.csv)")
        if not path: return
        try:
            tmp = str(Path(path).with_suffix(".tmp"))
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                res = self.last_compare_result
                w.writerows([["meta", "selected_drives", ",".join(res["selected_drives"])], ["meta", "total_relpaths", res["total_relpaths"]]])
                for cat, label in [("dup_by_sha", "duplicate"), ("same_content_diff_path", "same_content_diff_path")]:
                    for d in res[cat]: w.writerow([label, d["sha"], d["relpath"], d["drive"], d["size"], d["fullpath"]])
                for cat, label in [("same_name_diff_location", "same_name_diff_location"), ("name_conflicts", "name_conflict")]:
                    for d in res[cat]: w.writerow([label, d.get("name",""), d.get("relpath",""), d.get("drive",""), d.get("size",""), d.get("sha",""), d.get("fullpath","")])
                for d in res["missing"]: w.writerow(["missing", d.get("relpath",""), d.get("present_drive","")])
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
        path = item.data(Qt.UserRole)
        self.current_report_path = path
        self.apply_report_filter()

    def apply_report_filter(self):
        if not hasattr(self, 'current_report_path') or not self.current_report_path: return
        
        path = self.current_report_path
        limit_text = self.rep_limit.currentText()
        limit = float('inf') if limit_text == "All" else int(limit_text)
        filter_text = self.rep_filter.text().lower()
        
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                reader = csv.reader(fh)
                try: headers = next(reader)
                except StopIteration: return
                
                rows = []
                count = 0
                for row in reader:
                    if filter_text:
                        if not any(filter_text in str(cell).lower() for cell in row): continue
                    
                    disp = [str(count+1)] + row
                    sorts = [count+1] + [(1, str(x).lower()) for x in row]
                    rows.append({
                        "display": disp,
                        "sort_keys": sorts,
                        "user_data": None,
                        "icon": None
                    })
                    count += 1
                    if count >= limit: break
                    
            full_headers = ["S.No"] + headers
            model = FastTableModel(full_headers, rows)
            self.rep_table.setModel(model)
            self.rep_table.setColumnWidth(0, 60)
            
        except Exception as e:
            QMessageBox.warning(self, "Load Error", str(e))

    def open_report(self, item: QListWidgetItem):
        self.load_report_to_table(item)

    def closeEvent(self, ev):
        for w in list(self._workers):
            try: 
                if hasattr(w, "cancel"): w.cancel()
                if w.isRunning(): w.wait(2000)
            except Exception: pass
        try: self.db.close()
        except Exception: pass
        super().closeEvent(ev)

def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    win = DriveExplorerWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
