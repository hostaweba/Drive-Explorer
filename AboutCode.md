
# Technical Info: Drive Explorer 🛠️

## 1. Architectural Overview
Drive Explorer is a high-performance desktop application designed for large-scale file indexing, offline browsing, and media consumption. 

The codebase has been refactored from a monolithic script into a strict **Modular Architecture**. This separation of concerns ensures that the Presentation Layer (UI), Data Layer (Database), and Concurrency Layer (Threads) are completely decoupled, making the application highly maintainable, scalable, and easy to debug.

## 2. Tech Stack & Dependencies
* **Core Language:** Python 3.8+
* **GUI Framework:** PySide6 (Qt for Python)
* **Database:** SQLite3 (Native)
* **Concurrency:** `concurrent.futures.ThreadPoolExecutor` and `PySide6.QtCore.QThread`
* **Data Visualization:** `matplotlib`, `pandas` (Optional/Dynamic imports)
* **Media Handling:** `PySide6.QtMultimedia`, `PySide6.QtMultimediaWidgets`

## 3. Directory Structure & Module Responsibilities

The application follows a standard MVC-inspired structure:

```text
DriveExplorer/
│
├── main.py                  # The Application Bootstrapper
├── config.py                # Centralized Environment Variables & Constants
├── utils.py                 # Pure Python Helper Functions
│
├── database/
│   └── db_manager.py        # Data Access Object (DAO) for SQLite
│
├── workers/
│   └── threads.py           # Background processing (Concurrency)
│
└── ui/
    ├── main_window.py       # Core UI Layout and Tab Routing
    ├── tables.py            # QAbstractTableModel implementations
    ├── viewers.py           # Dedicated Media/Image/Text Player classes
    └── dialogs.py           # Modal popup management
```

### Module Breakdown
* **`config.py`**: Defines all static paths (`DATA_DIR`, `DB_FILE`) and constants (`MAX_RENDER_ROWS`). Imported globally.

* **`utils.py`**: Contains stateless mathematical and string-parsing logic (`human_size()`, `sha256_file()`). It has zero dependencies on PySide6.

* **`db_manager.py`**: Houses the `CatalogDB` class. It manages the SQLite connection using `PRAGMA journal_mode=WAL` for high-speed writes and handles all SQL execution. It is decoupled from the UI.

* **`threads.py`**: Contains all `QThread` classes (`ScanThread`, `CompareThread`, etc.). This isolates heavy blocking operations (like file I/O or SHA-256 hashing) from the main GUI thread, communicating back to the UI strictly via Qt `Signal` emissions.

* **`ui/viewers.py`**: Isolates the complex event handling (key presses, wheel events) and rendering logic for different media types (`QGraphicsView` for images, `QMediaPlayer` for video/audio).

## 4. Core Mechanisms & Data Flow

### A. High-Speed Indexing (The `ScanThread`)
File scanning is bottlenecked by disk I/O, not CPU. To achieve maximum throughput:
1. `os.walk()` rapidly builds a flat list of all target paths.

2. A `ThreadPoolExecutor` dispatches multiple worker threads to run `os.stat()` and (optionally) `hashlib.sha256()` concurrently.

3. Results are batched (default size: 2000) and bulk-inserted into SQLite via `executemany()` to minimize database lock contention.

### B. UI Concurrency via Signals
To prevent GUI freezing during database operations, workers emit signals.
* **Example Flow:** User clicks "Scan" -> `main_window.py` instantiates `ScanThread` -> Connects `ScanThread.progress(int)` to `QProgressDialog.setValue` -> Starts thread. The UI updates natively without blocking.

### C. The Fast Table Engine
Rendering 50,000 rows in standard GUI tables causes severe lag. Drive Explorer solves this by using the Model/View architecture:
* `FastTableModel` inherits from `QAbstractTableModel`.

* Data is stored natively as a list of dictionaries in Python memory.

* The table only renders the cells currently visible on the screen, allowing instant scrolling and instantaneous, layout-level filtering.

### D. Dedicated Viewers Architecture
Instead of a single, bloated viewer class, media rendering uses a Factory-like pattern based on file extensions:
* **Image Logic:** Utilizes `QGraphicsScene` for hardware-accelerated rendering, allowing precise coordinate transformations (zooming via matrix scaling, rotation).

* **Media Logic:** Instantiates `QMediaPlayer`. Audio files dynamically swap the `QVideoWidget` for a lightweight `QLabel` overlay to conserve resources.

* **Keyboard Routing:** Key events are handled locally within the dedicated viewer classes using dynamic `QShortcut` arrays that are cleared and rebuilt when the media type changes, preventing input conflicts.

## 5. Developer Guide: How to Extend the App

### Adding a New Tab
1. Open `ui/main_window.py`.

2. Inside `_build_ui()`, instantiate a new `QWidget` and layout.

3. Append it to the main `QTabWidget` via `self.tabs.addTab(new_widget, "Tab Name")`.

### Adding a New Background Task
1. Open `workers/threads.py`.

2. Create a new class inheriting from `WorkerBase` or `QThread`.

3. Define your custom `Signal` attributes at the class level.

4. Override the `run(self)` method with your blocking logic.

5. In `ui/main_window.py`, instantiate the thread, connect its signals to your UI slots, and register it using `self._register_worker()`.

### Modifying the Database Schema
1. Open `database/db_manager.py`.

2. Update the SQL strings in `_ensure_schema()`.

3. Wrap any destructive `ALTER TABLE` statements in `try/except sqlite3.OperationalError` blocks to ensure backward compatibility with existing user databases.
