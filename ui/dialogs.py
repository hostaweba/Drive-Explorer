# ui/dialogs.py

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton

# ---------------- UI Models & Custom Widgets ----------------
class ConflictDialog(QDialog):
    def __init__(self, item_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Item Exists")
        self.setModal(True)
        self.choice = "skip"
        
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"An item named <b>'{item_name}'</b> already exists in this location.<br>What would you like to do?"))
        
        btn_layout = QHBoxLayout()
        btn_replace = QPushButton("Replace")
        btn_replace.clicked.connect(self._do_replace)
        btn_keep = QPushButton("Keep Both")
        btn_keep.clicked.connect(self._do_keep)
        btn_skip = QPushButton("Skip")
        btn_skip.clicked.connect(self._do_skip)
        btn_skip_all = QPushButton("Skip All")
        btn_skip_all.clicked.connect(self._do_skip_all)
        
        btn_layout.addWidget(btn_replace)
        btn_layout.addWidget(btn_keep)
        btn_layout.addWidget(btn_skip)
        btn_layout.addWidget(btn_skip_all)
        layout.addLayout(btn_layout)
        
    def _do_replace(self): 
        self.choice = "replace"
        self.accept()
        
    def _do_keep(self): 
        self.choice = "keep"
        self.accept()
        
    def _do_skip(self): 
        self.choice = "skip"
        self.accept()
        
    def _do_skip_all(self): 
        self.choice = "skip_all"
        self.accept()