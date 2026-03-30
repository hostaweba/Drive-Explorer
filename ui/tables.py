# ui/tables.py
from typing import Dict, List

from PySide6.QtCore import Qt, Signal, QModelIndex, QAbstractTableModel, QItemSelection
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QTableView, QAbstractItemView, QTableWidgetItem

# ------------------------------------------------------

class SmartTableItem(QTableWidgetItem):
    def __init__(self, display_str, sort_value, is_folder=False):
        super().__init__(str(display_str))
        self.sort_value = sort_value
        self.is_folder = is_folder
        
    def __lt__(self, other):
        if isinstance(other, SmartTableItem):
            if self.is_folder != other.is_folder: 
                return self.is_folder 
            try: 
                return self.sort_value < other.sort_value
            except TypeError: 
                return str(self.sort_value) < str(other.sort_value)
        return super().__lt__(other)

class FastTableModel(QAbstractTableModel):
    def __init__(self, headers: List[str], rows: List[Dict], icon_lookup_func, parent=None):
        super().__init__(parent)
        self.headers = headers
        self.all_rows = rows
        self.filtered_rows = list(rows)
        self.icon_lookup = icon_lookup_func

    def set_advanced_filter(self, general_text: str, name_text: str, ext_text: str):
        self.layoutAboutToBeChanged.emit()
        
        if not general_text and not name_text and not ext_text:
            self.filtered_rows = list(self.all_rows)
        else:
            self.filtered_rows = []
            g_txt = general_text.lower()
            n_txt = name_text.lower()
            e_txt = ext_text.lower().strip('.')
            
            for r in self.all_rows:
                disp = r["display"]
                
                if g_txt:
                    row_str = " ".join(str(c).lower() for c in disp)
                    if g_txt not in row_str:
                        continue
                
                if n_txt:
                    name_val = str(disp[1] if len(disp) > 1 else disp[0]).lower()
                    if n_txt not in name_val:
                        continue
                        
                if e_txt:
                    ext_val = r.get("ext_meta", "").lower().strip('.')
                    if e_txt not in ext_val:
                        continue
                        
                self.filtered_rows.append(r)
                
        self.layoutChanged.emit()

    def set_filter(self, text: str):
        self.set_advanced_filter(text, "", "")

    def rowCount(self, parent=QModelIndex()): 
        return len(self.filtered_rows)
        
    def columnCount(self, parent=QModelIndex()): 
        return len(self.headers)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid(): 
            return None
        row = self.filtered_rows[index.row()]
        col = index.column()
        
        if role == Qt.DisplayRole: 
            return row["display"][col]
        elif role == Qt.UserRole: 
            return row["user_data"]
        elif role == Qt.UserRole + 1: 
            return row.get("user_data_1", None)
        elif role == Qt.UserRole + 2: 
            return row.get("user_data_2", None)
        elif role == Qt.DecorationRole and col == 1: 
            return self.icon_lookup(row.get("ext_meta", ""), row.get("is_folder_meta", False))
        elif role == Qt.TextAlignmentRole:
            if col == 0 or (self.headers[col] in ["Size", "Total Items", "Total Size", "Global Copies"]): 
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        return None

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal: 
            return self.headers[section]
        return None

    def sort(self, column: int, order=Qt.AscendingOrder):
        self.layoutAboutToBeChanged.emit()
        reverse = (order == Qt.DescendingOrder)
        self.filtered_rows.sort(key=lambda x: x["sort_keys"][column], reverse=reverse)
        self.layoutChanged.emit()

class ActionTableView(QTableView):
    itemSelectionChanged = Signal(QModelIndex)
    multiSelectionChanged = Signal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        
    def selectionChanged(self, selected: QItemSelection, deselected: QItemSelection):
        super().selectionChanged(selected, deselected)
        indexes = self.selectionModel().selectedRows()
        if indexes: 
            self.itemSelectionChanged.emit(indexes[0])
            self.multiSelectionChanged.emit(indexes)
        else: 
            self.multiSelectionChanged.emit([])

class SandboxTableView(ActionTableView):
    filesDropped = Signal(list)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls(): 
            event.acceptProposedAction()
        else: 
            super().dragEnterEvent(event)
            
    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls(): 
            event.acceptProposedAction()
        else: 
            super().dragMoveEvent(event)
            
    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            urls = [url.toLocalFile() for url in event.mimeData().urls()]
            self.filesDropped.emit(urls)
            event.acceptProposedAction()
        else: 
            super().dropEvent(event)