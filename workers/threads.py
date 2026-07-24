# workers/threads.py

import os
import shutil
import sqlite3
import csv
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List

from PySide6.QtCore import QThread, Signal

# --- Custom Modules ---
from config import DB_FILE, CSV_DIR
from database.db_manager import CatalogDB
from utils import sha256_file, now_ts

class WorkerBase(QThread):
    error = Signal(str)
    def __init__(self, parent=None): 
        super().__init__(parent)
        self._cancel_requested = False
    def cancel(self): 
        self._cancel_requested = True

class CopyThread(WorkerBase):
    progress = Signal(str)
    finished = Signal()
    def __init__(self, src: str, dst_dir: str, is_folder: bool, parent=None):
        super().__init__(parent)
        self.src = src
        self.dst_dir = dst_dir
        self.is_folder = is_folder

    def run(self):
        try:
            name = os.path.basename(self.src.rstrip('/\\'))
            dst_path = os.path.join(self.dst_dir, name)
            self.progress.emit(f"Copying {name} to {self.dst_dir}...")
            if self.is_folder: shutil.copytree(self.src, dst_path, dirs_exist_ok=True)
            else: shutil.copy2(self.src, dst_path)
            self.finished.emit()
        except Exception as e: 
            self.error.emit(str(e))

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
            cur = self.conn.cursor()
            cur.execute(self.query, self.params)
            rows = cur.fetchall()
            self.conn.close()
            self.conn = None
            if not self._is_cancelled: 
                self.finished.emit(rows)
        except Exception as e: 
            self.error.emit(str(e))

class ScanThread(WorkerBase):
    progress = Signal(int)
    finished = Signal(str, str, str)
    def __init__(self, folder: str, drive_name: str, purchase_date: str, compute_sha: bool, workers: int = 6, batch_size: int = 2000, write_csv: bool = True, update_map: dict = None, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.drive_name = drive_name
        self.purchase_date = purchase_date
        self.compute_sha = compute_sha
        self.workers = max(1, workers)
        self.batch_size = max(128, batch_size)
        self.write_csv = write_csv
        self.update_map = update_map or {} # Dictionary of relpath -> (size, modified, sha)
        
    def run(self):
        try:
            all_paths = []
            for root, dirs, files in os.walk(self.folder):
                for d in dirs: all_paths.append((os.path.join(root, d), True))
                for f in files: all_paths.append((os.path.join(root, f), False))
                
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
                        return (rel + "/", name, 0, "Folder", modified, "", self.drive_name, p, 1)
                    else:
                        size = int(st.st_size)
                        file_sha = ""
                        
                        # SMART UPDATE LOGIC: Skip hashing if untouched
                        if rel in self.update_map:
                            old_size, old_mod, old_sha = self.update_map[rel]
                            if size == old_size and modified == old_mod:
                                file_sha = old_sha
                            elif self.compute_sha and size > 0:
                                file_sha = sha256_file(p)
                        elif self.compute_sha and size > 0:
                            file_sha = sha256_file(p)
                            
                        return (rel, name, size, os.path.splitext(name)[1].lower(), modified, file_sha, self.drive_name, p, 0)
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
                        if csv_w: csv_w.writerow([res[0], res[1], res[2], res[3], res[4], res[5], res[7], res[8]])
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
            if csv_fh: csv_fh.close()
            
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
    def __init__(self, csv_path: str, drive_name: str, purchase_date: str = "", comment: str = "", parent=None): 
        super().__init__(parent)
        self.csv_path = csv_path
        self.drive_name = drive_name
        self.purchase_date = purchase_date
        self.comment = comment

    def run(self):
        try: 
            db = CatalogDB(DB_FILE)
            inserted = db.import_csv(Path(self.csv_path), self.drive_name, progress_callback=lambda n: self.progress.emit(n))
            db.insert_drive(self.drive_name, self.purchase_date, self.csv_path, self.comment)
            db.close()
            self.finished.emit(inserted, self.drive_name)
        except Exception as e: 
            self.error.emit(f"{e}\n{traceback.format_exc()}")

class DeleteDriveThread(WorkerBase):
    progress = Signal(int, str)
    finished = Signal()
    def __init__(self, drive_names: List[str], db_path: str, parent=None):
        super().__init__(parent)
        self.drive_names = drive_names
        self.db_path = db_path
        
    def run(self):
        try:
            conn = sqlite3.connect(str(self.db_path))
            cur = conn.cursor()
            
            total = len(self.drive_names)
            for i, dname in enumerate(self.drive_names):
                if self._cancel_requested: break
                self.progress.emit(int((i / total) * 50), f"Deleting files for '{dname}'...")
                cur.execute("DELETE FROM files WHERE drive = ?;", (dname,))
                cur.execute("DELETE FROM drives WHERE drive_name = ?;", (dname,))
                conn.commit()
                
            if not self._cancel_requested:
                self.progress.emit(60, "Reclaiming space (Vacuuming database)... This may take a minute.")
                conn.execute("VACUUM;")
                self.progress.emit(100, "Deletion Complete")
                
            conn.close()
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
            
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
            
            self.progress.emit(10, "Indexing Files via SQL Engine...")
            placeholders = ','.join('?' for _ in self.selected_drives)
            drive_tuple = tuple(self.selected_drives)
            
            res_dict = {
                "selected_drives": self.selected_drives,
                "dup_by_sha": [],
                "same_content_diff_path": [],
                "same_name_diff_location": [],
                "name_conflicts": [],
                "missing_relpath": [],
                "missing_name": [],
                "total_relpaths": 0
            }
            
            self.progress.emit(30, "Finding SHA Duplicates...")
            sha_dup_query = f"""
                SELECT sha, relpath, drive, size, fullpath 
                FROM files 
                WHERE sha IN (
                    SELECT sha FROM files 
                    WHERE drive IN ({placeholders}) AND is_folder=0 AND sha != ''
                    GROUP BY sha HAVING COUNT(DISTINCT drive) > 1
                ) AND drive IN ({placeholders}) AND is_folder=0
            """
            cur.execute(sha_dup_query, drive_tuple + drive_tuple)
            res_dict["dup_by_sha"] = [{"sha": row[0], "relpath": row[1], "drive": row[2], "size": row[3], "fullpath": row[4]} for row in cur.fetchall()]
            
            self.progress.emit(45, "Finding Content Diff Path...")
            same_content_query = f"""
                SELECT sha, relpath, drive, size, fullpath 
                FROM files 
                WHERE sha IN (
                    SELECT sha FROM files 
                    WHERE drive IN ({placeholders}) AND is_folder=0 AND sha != ''
                    GROUP BY sha HAVING COUNT(DISTINCT relpath) > 1
                ) AND drive IN ({placeholders}) AND is_folder=0
            """
            cur.execute(same_content_query, drive_tuple + drive_tuple)
            res_dict["same_content_diff_path"] = [{"sha": row[0], "relpath": row[1], "drive": row[2], "size": row[3], "fullpath": row[4]} for row in cur.fetchall()]
            
            self.progress.emit(60, "Finding Name Conflicts...")
            name_conflict_query = f"""
                SELECT name, relpath, drive, size, sha, fullpath 
                FROM files 
                WHERE name IN (
                    SELECT name FROM files 
                    WHERE drive IN ({placeholders}) AND is_folder=0 AND name != ''
                    GROUP BY name HAVING COUNT(DISTINCT size) > 1
                ) AND drive IN ({placeholders}) AND is_folder=0
            """
            cur.execute(name_conflict_query, drive_tuple + drive_tuple)
            res_dict["name_conflicts"] = [{"name": row[0], "relpath": row[1], "drive": row[2], "size": row[3], "sha": row[4], "fullpath": row[5]} for row in cur.fetchall()]

            self.progress.emit(70, "Finding Same Name Diff Location...")
            same_name_query = f"""
                SELECT name, relpath, drive, size, sha, fullpath 
                FROM files 
                WHERE name IN (
                    SELECT name FROM files 
                    WHERE drive IN ({placeholders}) AND is_folder=0 AND name != ''
                    GROUP BY name HAVING COUNT(DISTINCT relpath) > 1 AND COUNT(DISTINCT drive) > 1
                ) AND drive IN ({placeholders}) AND is_folder=0
            """
            cur.execute(same_name_query, drive_tuple + drive_tuple)
            res_dict["same_name_diff_location"] = [{"name": row[0], "relpath": row[1], "drive": row[2], "size": row[3], "sha": row[4], "fullpath": row[5]} for row in cur.fetchall()]

            self.progress.emit(85, "Finding Missing Files (RelPath)...")
            missing_relpath_query = f"""
                SELECT relpath, GROUP_CONCAT(DISTINCT drive), MAX(fullpath)
                FROM files 
                WHERE drive IN ({placeholders}) AND is_folder=0 
                GROUP BY relpath 
                HAVING COUNT(DISTINCT drive) < ?
            """
            cur.execute(missing_relpath_query, drive_tuple + (len(self.selected_drives),))
            all_drives_set = set(self.selected_drives)
            for rp, present_csv, sample_full in cur.fetchall():
                present_set = set(present_csv.split(','))
                missing_set = all_drives_set - present_set
                res_dict["missing_relpath"].append({
                    "relpath": rp, "missing_in": ", ".join(sorted(missing_set)), 
                    "present_in": ", ".join(sorted(present_set)), "fullpath": sample_full
                })

            self.progress.emit(95, "Finding Missing Files (Name)...")
            missing_name_query = f"""
                SELECT name, GROUP_CONCAT(DISTINCT drive), MAX(fullpath)
                FROM files 
                WHERE drive IN ({placeholders}) AND is_folder=0 
                GROUP BY name 
                HAVING COUNT(DISTINCT drive) < ?
            """
            cur.execute(missing_name_query, drive_tuple + (len(self.selected_drives),))
            for name, present_csv, sample_full in cur.fetchall():
                present_set = set(present_csv.split(','))
                missing_set = all_drives_set - present_set
                res_dict["missing_name"].append({
                    "name": name, "missing_in": ", ".join(sorted(missing_set)), 
                    "present_in": ", ".join(sorted(present_set)), "fullpath": sample_full
                })
            
            conn.close()
            self.progress.emit(100, "Done")
            self.finished.emit(res_dict)
        except Exception as e: 
            self.error.emit(f"{e}\n{traceback.format_exc()}")

class ChartWorker(WorkerBase):
    finished_data = Signal(int, str, str, object)
    progress = Signal(int)
    
    def __init__(self, db_path, mode, c_type, target_drive, d_from, d_to, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.mode = mode
        self.c_type = c_type
        self.target_drive = target_drive
        self.d_from = d_from
        self.d_to = d_to

    def run(self):
        try:
            conn = sqlite3.connect(str(self.db_path))
            cur = conn.cursor()
            
            # Smartly parse Global Drives if comma separated (from new UI system)
            if self.target_drive.startswith("GLOBAL_FILTER:"):
                drives_list = self.target_drive.replace("GLOBAL_FILTER:", "").split("||")
                placeholders = ",".join("?" for _ in drives_list)
                drive_filter = f"AND drive IN ({placeholders})"
                params = tuple(drives_list)
            else:
                drive_filter = "" if self.target_drive == "Any Drive" else f"AND drive = '{self.target_drive}'"
                params = ()
                
            date_filter = f"AND modified >= '{self.d_from}' AND modified <= '{self.d_to}'"
            
            data = None
            
            if self.mode in (0, 1):
                cur.execute(f"SELECT drive, COUNT(id) as cnt, COALESCE(SUM(size),0) as sz FROM files WHERE is_folder=0 {date_filter} GROUP BY drive;")
                data = cur.fetchall()
            elif self.mode in (2, 3):
                cur.execute(f"SELECT COALESCE(NULLIF(extension, ''), 'unknown') as ext, COUNT(*) as cnt, COALESCE(SUM(size),0) as sz FROM files WHERE is_folder=0 {drive_filter} {date_filter} GROUP BY ext;", params)
                data = cur.fetchall()
            elif self.mode == 4:
                cur.execute(f"SELECT size FROM files WHERE is_folder=0 {drive_filter} {date_filter}", params)
                data = cur.fetchall()
            elif self.mode == 5:
                cur.execute(f"SELECT SUBSTR(modified, 1, 4) as yr, COUNT(*) as cnt FROM files WHERE modified != '' AND is_folder=0 {drive_filter} {date_filter} GROUP BY yr;", params)
                data = cur.fetchall()
            elif self.mode == 6:
                cur.execute(f"SELECT name, size FROM files WHERE is_folder=0 {drive_filter} {date_filter} ORDER BY size DESC LIMIT 20;", params)
                data = cur.fetchall()
            elif self.mode == 7:
                cur.execute(f"SELECT SUBSTR(modified, 1, 4) as yr, SUM(size) as sz FROM files WHERE modified != '' AND is_folder=0 {drive_filter} {date_filter} GROUP BY yr;", params)
                data = cur.fetchall()
            elif self.mode == 8:
                cur.execute(f"SELECT modified FROM files WHERE modified != '' AND is_folder=0 {drive_filter} {date_filter}", params)
                data = cur.fetchall()
            elif self.mode == 9:
                if self.target_drive == "Any Drive" or self.target_drive.startswith("GLOBAL_FILTER:"):
                    data = "NEED_DRIVE"
                else:
                    cur.execute("SELECT drive_name FROM drives WHERE drive_name != ?", (self.target_drive,))
                    other_drives = [r[0] for r in cur.fetchall()]
                    data = []
                    for i, d in enumerate(other_drives):
                        if self._cancel_requested: break
                        self.progress.emit(int((i / len(other_drives)) * 100))
                        
                        query = """
                            SELECT COUNT(*), COALESCE(SUM(f1.size), 0)
                            FROM (SELECT DISTINCT name, size FROM files WHERE drive=? AND is_folder=0) f1
                            JOIN (SELECT DISTINCT name, size FROM files WHERE drive=? AND is_folder=0) f2
                            ON f1.name = f2.name AND f1.size = f2.size
                        """
                        cur.execute(query, (self.target_drive, d))
                        res = cur.fetchone()
                        if res and res[0] > 0:
                            data.append((d, res[0], res[1]))
                            
                    self.progress.emit(100)

            conn.close()
            if not self._cancel_requested:
                self.finished_data.emit(self.mode, self.c_type, self.target_drive, data)
        except Exception as e:
            self.error.emit(str(e))
