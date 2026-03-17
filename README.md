# Drive Explorer 

**Drive Explorer** is a high-performance, PySide6-based desktop application designed for unrestricted file searching, cataloging, and analysis across multiple drives. It allows you to index offline storage, visualize disk usage, find duplicates, and build virtual file structures without altering your physical disks.

## ✨ Key Features

* **Fast Global Indexing:** Scan entire drives/folders and store metadata (including SHA-256 hashes) in a highly optimized, WAL-mode SQLite database. Browse disconnected drives offline.

* **⚡ Fast & Global Explorers:** Navigate through massive file structures instantly. Includes ultra-fast search and filtering.

* **⭐ MySpace Sandbox:** A virtual filesystem! Drag-and-drop, cut, copy, paste, and organize files into virtual folders without moving the physical files on your disk.
  
* **📊 Statistics & Charts:** Generate insightful visualizations (Bar, Line, Horizontal Bar charts) powered by Pandas and Matplotlib. Analyze storage usage by year, top formats, file age distribution, and drive overlap.
  
* **🔍 Advanced Search:** Precision querying by item name, folder path, match mode, extension, size ranges, and modification dates.
  
* **⚖️ Drive Comparisons:** Select multiple drives to instantly identify exact duplicates (SHA), same-name conflicts, or missing files across backups.
  
* **👀 Built-in Internal Viewer:** Preview files directly inside the app without opening external software. Supports images, text/code files, and multimedia (Video/Audio).
  
* **📑 Advanced Reports:** Automatically save and review your searches and comparison results as CSV reports.
  
* **🎨 Custom Icons & Dark Mode:** Toggleable Dark/Light themes and support for custom file-extension icons.

## 🛠️ Prerequisites & Installation

### Requirements
* Python 3.8+
* `PySide6` (Core GUI framework)
* `pandas` (Data processing for charts)
* `matplotlib` (Data visualization)

### Installation
1. Clone or download the source code.
2. Install the required dependencies using pip:
   ```bash
   pip install PySide6 pandas matplotlib
   ```
   *(Optional but recommended)*: To ensure the built-in video and audio player works, make sure your OS has the necessary media codecs installed, as it relies on `PySide6.QtMultimedia`.

### Running the App
Run the script directly from your terminal:
```bash
python drive_explorer.py
```

## ⌨️ Keyboard Shortcuts & Controls

The app features several quality-of-life shortcuts to speed up your workflow:

| Global Shortcuts | Action |
| :--- | :--- |
| `Ctrl + F` | Focus the filter/search bar. |
| `Up / Down Arrows` | Navigate tables and auto-update the Details/Image Preview. |
| `Shift / Ctrl + Click` | Multi-select items (Status bar automatically shows total selected size). |
| `Enter` | Open the selected folder or launch the real file. |

| MySpace Sandbox | Action |
| :--- | :--- |
| `Ctrl + C` | Copy virtual item(s) (Supports folders recursively). |
| `Ctrl + X` | Cut virtual item(s). |
| `Ctrl + V` | Paste item(s) into current Sandbox Folder (Features Conflict Resolution). |
| `Delete` | Remove selected items from the Sandbox (Does **not** delete real files). |

## 📂 Folder Structure & Data Persistence

Upon first run, the app will automatically create the following directories in the same folder as the script:
* `data/`: Contains the core `catalog.db` SQLite database.
* `data/csvs/`: Stores CSV backups of your drive scans.
* `data/old_drives/`: Archives CSVs of deleted drive records.
* `icons/`: Custom icon directory.

### Custom File Icons
You can customize the icons used in the explorer views. Simply drop a `.png`, `.jpg`, or `.ico` file into the `icons/` folder and name it after the file extension you want it to represent (e.g., `jpg.png`, `mp4.ico`, `py.png`). The app will automatically map them.

## 🧠 Core Modules Explanation

1.  **Drives Dashboard:** Manage your indexed drives. Add new scans, import CSVs, or delete old indexes.

2.  **Global Explorer:** Navigate your indexed files exactly like a standard file manager, but with the ability to see files from offline/disconnected drives.

3.  **Fast Explorer:** A streamlined, high-speed variant of the global explorer optimized for rapid, flat-list filtering without deep hierarchy calculations.

4.  **MySpace Sandbox:** Create a pristine, organized virtual directory using files scattered across dozens of different drives. You can export this sandbox to a real directory later.

5.  **Advanced Search:** Complex SQL-backed search with multiple parameters.

6.  **Comparisons:** The deduplication engine. Finds exact byte-for-byte duplicates (using SHA-256) or partial matches to help you clean up redundant backups.

7.  **Advanced Reports:** A viewer for all the historical CSV exports you've generated during comparisons and searches.

8.  **Statistics & Charts:** Interactive visual dashboard plotting your data distribution.

---
*Note: Depending on your system, calculating SHA-256 hashes during the initial drive scan can be slow. If you are scanning massive drives and do not need exact duplicate detection, you can uncheck "Compute SHA-256" during the scan prompt.*
