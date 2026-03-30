#!/usr/bin/env python3
"""
Drive Explorer 
Entry Point
"""
import sys
import os

from PySide6.QtWidgets import QApplication

# Custom Modules
from config import APP_TITLE
from utils import ensure_dirs
from ui.main_window import DriveExplorerWindow

# Suppress Qt ICC Profile terminal spam for images
os.environ["QT_IMAGEIO_DISABLE_ICC"] = "1"

def main():
    ensure_dirs()  # Safely build data directories before UI loads
    
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    
    win = DriveExplorerWindow()
    win.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()