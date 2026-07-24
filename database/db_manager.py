# database/db_manager.py

import sqlite3
import csv
import os
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

# ---------------- Database ----------------
class CatalogDB:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA mmap_size=268435456;") 
        self.conn.execute("PRAGMA temp_store=MEMORY;")
        self.conn.execute("PRAGMA cache_size=-100000;")
        self._ensure_schema()

    def _ensure_schema(self):
        c = self.conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS drives (id INTEGER PRIMARY KEY, drive_name TEXT UNIQUE, purchase_date TEXT, scanned_at TEXT, csv_path TEXT, comment TEXT DEFAULT '');")
        c.execute("CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, relpath TEXT, name TEXT, size INTEGER, extension TEXT, modified TEXT, sha TEXT, drive TEXT, fullpath TEXT, is_folder INTEGER DEFAULT 0);")
        c.execute("CREATE TABLE IF NOT EXISTS myspace (id INTEGER PRIMARY KEY, parent_path TEXT, name TEXT, is_folder INTEGER, real_path TEXT, size INTEGER, extension TEXT, modified TEXT);")
        
        # Gracefully add columns to existing databases without breaking
        try: c.execute("ALTER TABLE files ADD COLUMN is_folder INTEGER DEFAULT 0;")
        except sqlite3.OperationalError: pass
        
        try: c.execute("ALTER TABLE drives ADD COLUMN comment TEXT DEFAULT '';")
        except sqlite3.OperationalError: pass
            
        # Creates highly optimized indexes gracefully
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_relpath ON files(relpath);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_sha ON files(sha);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_drive ON files(drive);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_isfolder ON files(is_folder);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_ext ON files(extension);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_size ON files(size);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_mod ON files(modified);")
        c.execute("CREATE INDEX IF NOT EXISTS idx_files_drive_ext ON files(drive, extension);")
        self.conn.commit()

    def insert_drive(self, drive_name: str, purchase_date: str, csv_path: str = "", comment: str = ""):
        # Check if drive exists to preserve comment if updating
        cur = self.conn.cursor()
        cur.execute("SELECT comment FROM drives WHERE drive_name = ?", (drive_name,))
        row = cur.fetchone()
        existing_comment = row[0] if row else comment
        
        cur.execute(
            "INSERT OR REPLACE INTO drives (drive_name,purchase_date,scanned_at,csv_path,comment) VALUES (?,?,?,?,?);",
            (drive_name, purchase_date or "", datetime.now().isoformat(), csv_path or "", existing_comment)
        )
        self.conn.commit()

    def update_drive_comment(self, drive_name: str, comment: str):
        self.conn.cursor().execute("UPDATE drives SET comment = ? WHERE drive_name = ?", (comment, drive_name))
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
                relpath = row[0]
                name = row[1] if len(row) > 1 and row[1] else os.path.basename(relpath)
                size = int(row[2]) if len(row) > 2 and row[2] else 0
                ext = row[3] if len(row) > 3 else os.path.splitext(name)[1].lower()
                mod = row[4] if len(row) > 4 else ""
                sha = row[5] if len(row) > 5 else ""
                fullpath = row[6] if len(row) > 6 else ""
                is_f = int(row[7]) if len(row) > 7 and row[7].isdigit() else 0
                
                batch.append((relpath, name, size, ext, mod, sha, drive_name, fullpath, is_f))
                
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

    def get_folder_stats(self, prefix: str, target_drive: str) -> dict:
        cur = self.conn.cursor()
        drive_filter = f" AND drive = '{target_drive}'" if target_drive and target_drive != "Any Drive" else ""
        cur.execute(f"SELECT is_folder, size, relpath FROM files WHERE relpath LIKE ? AND relpath != ? {drive_filter}", (f"{prefix}%", prefix))
        rows = cur.fetchall()
        
        tot_all = len(rows)
        tot_size = sum((r[1] or 0) for r in rows)
        return { "total_items": tot_all, "total_size": tot_size }

    def drives_summary(self) -> List[Tuple]:
        cur = self.conn.cursor()
        cur.execute("SELECT d.drive_name, d.purchase_date, d.scanned_at, d.csv_path, d.comment, COUNT(f.id) as file_count, COALESCE(SUM(f.size),0) as total_size FROM drives d LEFT JOIN files f ON f.drive = d.drive_name WHERE f.is_folder = 0 OR f.is_folder IS NULL GROUP BY d.drive_name ORDER BY d.scanned_at DESC;")
        return cur.fetchall()

    def delete_drive(self, drive_name: str):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM files WHERE drive = ?;", (drive_name,))
        cur.execute("DELETE FROM drives WHERE drive_name = ?;", (drive_name,))
        self.conn.commit()
        self.conn.execute("VACUUM;")
        
    def close(self):
        try: self.conn.close()
        except Exception: pass
