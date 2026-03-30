# utils.py
import hashlib
from datetime import datetime, date
from typing import Tuple

from config import DATA_DIR, CSV_DIR, OLD_DATA_DIR, ICONS_DIR

# ----------------------

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    OLD_DATA_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

def now_ts() -> str: 
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def human_size(num_bytes: int | None | float) -> str:
    if num_bytes is None: 
        return "0 B"
    try: 
        n = int(num_bytes)
    except Exception: 
        return "0 B"
    if n < 1024: 
        return f"{n} B"
    n_kb = n / 1024.0
    if n_kb < 1024: 
        return f"{n_kb:.1f} KB"
    n_mb = n_kb / 1024.0
    if n_mb < 1024: 
        return f"{n_mb:.2f} MB" if n_mb < 10 else f"{n_mb:.1f} MB"
    n_gb = n_mb / 1024.0
    return f"{n_gb:.2f} GB" if n_gb < 10 else f"{n_gb:.1f} GB"

def sha256_file(path: str, chunk=1024*1024) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk)
                if not b: 
                    break
                h.update(b)
        return h.hexdigest()
    except Exception: 
        return ""

def parse_purchase_date(s: str) -> date | None:
    if not s: 
        return None
    s = s.strip()
    try:
        if "/" in s:
            parts = s.split("/")
            if len(parts) == 2 and len(parts[1]) == 4: 
                return date(int(parts[1]), int(parts[0]), 1)
            if len(parts) == 3: 
                return date(int(parts[0]), int(parts[1]), int(parts[2]))
        if "-" in s:
            parts = s.split("-")
            if len(parts) == 3: 
                return date(int(parts[0]), int(parts[1]), int(parts[2]))
            if len(parts) == 2: 
                return date(int(parts[0]), int(parts[1]), 1)
        if len(s) == 4 and s.isdigit(): 
            return date(int(s), 1, 1)
    except Exception: 
        pass
    return None

def age_from_date(d: date) -> Tuple[str, int]:
    if not d: 
        return "unknown", -1
    today = date.today()
    days_total = (today - d).days
    years = today.year - d.year
    months = today.month - d.month
    days = today.day - d.day
    
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
    if years: 
        parts.append(f"{years}y")
    if months: 
        parts.append(f"{months}m")
    if days: 
        parts.append(f"{days}d")
    return (" ".join(parts) if parts else "0d", days_total)


