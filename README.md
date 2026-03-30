
# Drive Explorer 🚀

**Drive Explorer** is a high-performance, locally-indexed file management and analysis suite built with Python and PySide6. It allows you to scan massive external drives or local directories, index them into a lightning-fast SQLite database, and analyze, search, and view your files without needing the physical drives continuously connected.

## ✨ Key Features

* **⚡ High-Speed Indexing:** Multi-threaded file scanning with optional SHA-256 hash generation for duplicate detection.

* **🗄️ Offline Browsing:** Browse the contents of disconnected drives instantly via the Global and Fast Explorer tabs.

* **🔍 Advanced Precision Search:** Filter files globally by size, modification date, extension, or specific match types.

* **📊 Statistics & Analytics:** Interactive, colorful Matplotlib charts visualizing storage usage, file age, format distribution, and drive overlap.

* **📅 Timeline Diary:** A calendar-based activity viewer to see exactly what files were modified on any given day or month.

* **⭐ MySpace Sandbox:** A virtual workspace where you can copy, cut, and organize files into virtual folders without altering the real files on your disk.

* **⚖️ Drive Comparison:** Advanced SQL-driven analysis to find exact duplicates, name conflicts, and missing files across multiple drives.

* **🎬 Dedicated Pro Viewers:** * **Images:** Zoom, pan, rotate, flip, and animated auto-play slideshows.

  * **Media:** Built-in video and audio player with timeline seeking, volume, mute, shuffle, and loop.

  * **Text/Code:** Native syntax viewing with smooth scrolling.

* **⌨️ Keyboard Driven:** Extensive global shortcuts for tab navigation, searching, and media control.

* **🌙 Dark Mode:** Native toggle for a sleek, eye-friendly dark interface.


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
│   └── db_manager.py        # SQLite catalog initialization and queries
│
├── workers/
│   └── threads.py           # QThreads for non-blocking scanning, searching, and charting
│
└── ui/
    ├── main_window.py       # Core layout and tab management
    ├── tables.py            # Custom QAbstractTableModel and View logic
    ├── dialogs.py           # Pop-up dialogs (e.g., Conflict Resolution)
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
3. Once indexed, use the **Global Explorer** or **Fast Explorer** to browse your files instantly.
4. Double-click any supported image, video, audio, or text file to open the built-in **Pro Viewer**.

## ⌨️ Essential Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `Ctrl + Tab` / `Ctrl + Shift + Tab` | Navigate between tabs |
| `Ctrl + 1`, `2`, `3`... | Jump to a specific tab |
| `Ctrl + F` | Smart focus on the active tab's search bar |
| `F11` / `Esc` | Toggle Fullscreen / Exit Viewer |
| `Space` | Play/Pause (in Media Viewer) |
| `Left / Right Arrows` | Next/Prev Image OR Seek 5s in Media |
| `Shift + F` / `F` | Flip Image Vertically / Horizontally |
| `B` | Send media player to background |
| `Ctrl + M` | Restore background media players |
| `Ctrl + T` | Toggle Dark/Light Theme |

## 📁 Data Storage

All application data is stored locally within the `data/` folder generated in the root directory upon first launch. 
* **`catalog.db`**: The SQLite database containing all file metadata.
* **`csvs/`**: Backups of individual drive scans.
