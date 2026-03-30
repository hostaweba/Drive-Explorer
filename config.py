import os
from pathlib import Path

# ---------------- Constants & Utilities ----------------
APP_TITLE = "Drive Explorer"
DATA_DIR = Path("data")
DB_FILE = DATA_DIR / "catalog.db"
CSV_DIR = DATA_DIR / "csvs"
OLD_DATA_DIR = DATA_DIR / "old_drives"
ICONS_DIR = Path("icons")
MAX_RENDER_ROWS = 25000  