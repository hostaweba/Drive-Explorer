# Technical Architecture: Drive Explorer

## 1. Overview
Drive Explorer is a multi-threaded, PySide6-based desktop application designed for high-performance file cataloging, indexing, and virtual management. It relies heavily on an embedded SQLite database optimized for large-scale hierarchical and relational data processing, bypassing native OS file system limitations for offline drives.

## 2. Technology Stack & Dependencies
* **Core Language:** Python 3.8+ (relies on `from __future__ import annotations` and type hinting).
* **GUI Framework:** `PySide6` (Qt for Python). Utilizes `QtWidgets`, `QtCore`, `QtGui`, and optionally `QtMultimedia` for the internal media player.
* **Database:** Built-in `sqlite3` engine.
* **Data Analysis & Visualization (Optional):** `pandas` and `matplotlib.backends.backend_qtagg` for rendering complex statistical charts directly into the Qt application.
* **Concurrency:** `concurrent.futures.ThreadPoolExecutor` for I/O bound OS-level scanning, and `PySide6.QtCore.QThread` for asynchronous GUI tasks and SQL querying.

## 3. Database Architecture (SQLite)
The application uses a highly optimized SQLite database (`catalog.db`) to store file metadata. 

### 3.1. Performance Pragmas
To handle millions of rows efficiently, the connection initializes with aggressive performance pragmas:
* `PRAGMA journal_mode=WAL;` (Write-Ahead Logging for concurrent reads/writes)
* `PRAGMA synchronous=NORMAL;`
* `PRAGMA mmap_size=268435456;` (Memory mapping up to 256MB)
* `PRAGMA temp_store=MEMORY;` (Keeps temporary tables and indices in RAM)

### 3.2. Schema
* **`drives`**: Tracks indexed drives (`drive_name`, `purchase_date`, `scanned_at`, `csv_path`).
* **`files`**: The core catalog. Stores file metadata (`relpath`, `name`, `size`, `extension`, `modified`, `sha`, `drive`, `fullpath`, `is_folder`).
* **`myspace`**: Represents the virtual Sandbox. Uses an adjacency-list style model (`parent_path`, `name`, `is_folder`, `real_path`, `size`, `extension`, `modified`) to construct virtual trees independent of physical constraints.

### 3.3. Indexing Strategy
Extensive indexing is applied to `files` columns (`relpath`, `name`, `sha`, `drive`, `is_folder`, `extension`, `size`, `modified`) to ensure rapid querying during Global Search and Duplicate Comparison.

## 4. Concurrency & Threading Model
To maintain a responsive 60FPS UI while processing millions of records, the app extensively uses background workers inherited from `QThread`. Communication with the main thread is handled strictly via Qt `Signal`s.

* **`ScanThread`**: Uses `os.walk` paired with a `ThreadPoolExecutor` to concurrently hash (`hashlib.sha256`) and `os.stat` files. Batches SQL `INSERT` statements in chunks of 2,000 to maximize disk write throughput.
* **`SearchThread`**: Executes heavy `LIKE` queries in the background, emitting lists of tuples upon completion.
* **`ChartWorker`**: Offloads complex analytical SQL aggregations (e.g., grouping by year, extension, or cross-joining for drive overlap) to prevent UI blocking before passing data to Pandas/Matplotlib.
* **`CompareThread`**: Executes complex, multi-layered SQL subqueries to find overlapping sets (SHA duplicates, name conflicts, missing files) across selected drives.
* **`ImageLoader`**: Asynchronously loads and scales preview images using `QPixmap` to prevent the UI thread from hanging on massive image files.

## 5. Core UI Components (Model/View)
The application utilizes Qt's Model/View architecture for high-performance rendering of massive datasets.

* **`FastTableModel`**: A custom subclass of `QAbstractTableModel`. It maintains an in-memory list of dictionary rows (`all_rows` and `filtered_rows`). It overrides `data()`, `sort()`, and provides a custom `set_advanced_filter()` method for instantaneous in-memory searching without hitting the database repeatedly.
* **`ActionTableView` & `SandboxTableView`**: Subclasses of `QTableView` implementing drag-and-drop mechanics (`dragEnterEvent`, `dropEvent`) and custom context menus based on the selected row's data.

## 6. Advanced Subsystems

### 6.1. The SQL Comparison Engine
Instead of loading dictionaries into Python memory to find duplicates, the app relies heavily on the SQLite engine using `GROUP BY`, `HAVING COUNT(DISTINCT ...)`, and subqueries. 
* *Example:* Finding files with the same SHA but different relative paths across specific drives is executed entirely within SQL, returning only the final localized result set to Python.

### 6.2. MySpace Sandbox (Virtual File System)
The Sandbox bypasses physical OS limits by allowing users to create virtual hierarchies. 
* **Recursive Mapping:** When a physical folder is dropped into the sandbox, the app reconstructs its relative hierarchy inside the `myspace` table using purely string-based `parent_path` logic.
* **Conflict Resolution:** Includes a custom dialog to handle virtual namespace collisions (Keep Both, Replace, Skip) when copying or moving files within the sandbox.

### 6.3. Dynamic Internal Viewer
The `InternalViewer` dynamically checks file extensions and instantiates the appropriate PySide6 widget:
* `ScaledImageLabel` for graphics.
* `QPlainTextEdit` for text/code (truncating at 2MB to prevent memory exhaustion).
* `QMediaPlayer` and `QVideoWidget` for audio/video playback (if OS codecs are present).
