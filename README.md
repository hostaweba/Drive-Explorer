# Drive Explorer 🚀

**Drive Explorer** is a high-performance, locally-indexed file management and analysis suite built with Python and PySide6. It allows you to scan massive external drives or local directories, index them into a lightning-fast SQLite database, and analyze, search, and view your files without needing the physical drives continuously connected.

## ✨ Key Features

* **⚡ Smart Indexing & Updates:** Multi-threaded file scanning with optional SHA-256 hash generation. The new **Smart Update** model skips expensive hashing for files with unchanged sizes and timestamps, drastically minimizing hardware wear. Powered by SQLite `WAL` mode for high-performance data persistence.

* **🗄️ Offline Browsing & Governance:** Browse the contents of disconnected drives instantly. Use **Global Filtering** to lock the entire application (Search, Stats, Timeline) to specific drives, and add persistent custom **Comments** to drives for better organization.

* **🔍 Advanced Precision Search:** Filter files globally by size, modification date, extension, or specific match types. Features advanced **Exclusions** (ignore specific words or extensions) and a **Smart Post-Filter** popup to easily uncheck extensions from your results.

* **🎬 Dedicated Pro Viewers:** 
  * **Images:** Features "Fit Best", "Original Size", **"Fit Width" (Webpage-style vertical smooth scrolling)**, and "Fit Height". Includes mouse-anchored zoom, pan, rotate, flip, and an advanced Slideshow with **Vertical Auto-Scroll** capabilities. Rendered in a true borderless fullscreen with a sleek, unified auto-hiding tool panel.
  * **Media:** Built-in video and audio player with timeline seeking, volume, mute, shuffle, loop, and the ability to minimize to the background while you browse.
  * **Text/Code:** Native syntax viewing with smooth scrolling and high-performance large-file loading.

* **📊 Statistics & Analytics:** Interactive, colorful Matplotlib charts visualizing storage usage, file age, format distribution, and drive overlap.

* **📅 Timeline Diary:** A calendar-based activity viewer to see exactly what files were modified on any given day or month.

* **⭐ MySpace Sandbox:** A virtual workspace where you can copy, cut, and organize files into virtual folders without altering the real files on your disk. Features intelligent conflict resolution.

* **⚖️ Drive Comparison:** Advanced SQL-driven analysis to find exact duplicates, name conflicts, and missing files across multiple drives.

* **⌨️ Keyboard Driven:** Extensive global shortcuts for tab navigation, searching, and pro-viewer media control.

* **🌙 Native Dark Mode:** Sleek, eye-friendly dark interface built dynamically across all dialogs and viewers.

## 🏗️ Project Architecture

The application is built using a clean, modular architecture separating the UI, background workers, and database logic:

```text
DriveExplorer/
│
├── main.py                  # Application entry point
├── config.py                # Global constants and directory paths
├── utils.py                 # Standalone helper functions
│
├── database/
│   └── db_manager.py        # SQLite catalog initialization, WAL mode, and queries
│
├── workers/
│   └── threads.py           # QThreads for non-blocking scanning, searching, and charting
│
└── ui/
    ├── main_window.py       # Core layout and tab management
    ├── tables.py            # Custom QAbstractTableModel and View logic
    ├── dialogs.py           # Pop-up dialogs (e.g., Conflict Resolution, Help)
    └── viewers.py           # Dedicated media, image, and text players

```

## 🛠️ Prerequisites & Installation

**Drive Explorer** requires **Python 3.8+**.

1. **Clone or Download the repository.**
2. **Install the required dependencies.** It is highly recommended to use a virtual environment.

```bash
pip install PySide6

```

**Optional (but highly recommended) dependencies for advanced features:**

* For Statistics and Charts: `pip install pandas matplotlib`

## 🚀 Usage

To launch Drive Explorer, simply run the `main.py` file from your terminal:

```bash
python main.py

```

### Getting Started:

1. Go to the **Drives Dashboard**.
2. Click **Scan New Folder** in the top toolbar to index a local directory or external drive.
3. *(Later)* Use **Smart Update** on an existing drive to instantly refresh changes without rescanning everything.
4. Once indexed, use the **Global Explorer** or **Fast Explorer** to browse your files instantly.
5. Double-click any supported image, video, audio, or text file to open the built-in **Pro Viewer**.

## ⌨️ Essential Keyboard Shortcuts

### Global App Navigation

| Shortcut | Action |
| --- | --- |
| `Ctrl + Tab` / `Ctrl + Shift + Tab` | Navigate between tabs |
| `Ctrl + 1`, `2`, `3`... | Jump to a specific tab |
| `Ctrl + F` | Smart focus on the active tab's search bar |
| `Ctrl + T` | Toggle Dark/Light Theme |
| `Ctrl + M` | Restore background media players |

### Pro Internal Viewer

| Shortcut | Action |
| --- | --- |
| `Left / Right Arrows` | Previous / Next File |
| `F11` / `Esc` | Toggle True Fullscreen / Exit Viewer |
| `H` | Hide/Show the unified bottom tool panel |
| `W` | Fit to Width (Perfect for vertical reading/webtoon scrolling) |
| `S` | Toggle Slideshow & Vertical Auto-Scroll |
| `R` / `F` / `Shift+F` | Rotate 90° / Flip Horizontally / Flip Vertically |
| `Ctrl + Scroll Wheel` | Mouse-anchored Zoom In/Out |
| `0` | Reset image to Original Size |
| `Space` | Play/Pause (in Media Viewer) |
| `Shift + Left/Right` | Seek backward/forward 5 seconds in Media |
| `B` | Send media player to background (keep audio playing) |

## 📁 Data Storage

All application data is stored locally within the `data/` folder generated in the root directory upon first launch.

* **`catalog.db`**: The highly optimized SQLite database (using `WAL` journaling) containing all file metadata.
* **`csvs/`**: User-controlled backups and generated reports of individual drive scans.

