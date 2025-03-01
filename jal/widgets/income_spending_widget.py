import logging
from datetime import datetime
from dateutil import tz

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QLabel, QDateTimeEdit, QPushButton, QTableView, QHeaderView
from PySide6.QtGui import QFont
from PySide6.QtSql import QSqlTableModel
from jal.widgets.abstract_operation_details import AbstractOperationDetails
from jal.widgets.reference_selector import AccountSelector, PeerSelector
from jal.widgets.account_select import OptionalCurrencyComboBox
from jal.constants import TransactionType
from jal.db.helpers import db_connection, executeSQL, load_icon
from jal.widgets.delegates import WidgetMapperDelegateBase, FloatDelegate, CategorySelectorDelegate, TagSelectorDelegate


# ----------------------------------------------------------------------------------------------------------------------
class IncomeSpendingWidgetDelegate(WidgetMapperDelegateBase):
    def __init__(self, parent=None):
        WidgetMapperDelegateBase.__init__(self, parent)
        self.delegates = {'timestamp': self.timestamp_delegate}


# ----------------------------------------------------------------------------------------------------------------------
class IncomeSpendingWidget(AbstractOperationDetails):
    def __init__(self, parent=None):
        AbstractOperationDetails.__init__(self, parent)
        self.name = "Income/Spending"
        self.operation_type = TransactionType.Action

        self.category_delegate = CategorySelectorDelegate()
        self.tag_delegate = TagSelectorDelegate()
        self.float_delegate = FloatDelegate(2)

        self.date_label = QLabel(self)
        self.details_label = QLabel(self)
        self.account_label = QLabel(self)
        self.peer_label = QLabel(self)

        self.main_label.setText(self.tr("Income / Spending"))
        self.date_label.setText(self.tr("Date/Time"))
        self.details_label.setText(self.tr("Details"))
        self.account_label.setText(self.tr("Account"))
        self.peer_label.setText(self.tr("Peer"))

        self.timestamp_editor = QDateTimeEdit(self)
        self.timestamp_editor.setCalendarPopup(True)
        self.timestamp_editor.setTimeSpec(Qt.UTC)
        self.timestamp_editor.setFixedWidth(self.timestamp_editor.fontMetrics().horizontalAdvance("00/00/0000 00:00:00") * 1.25)
        self.timestamp_editor.setDisplayFormat("dd/MM/yyyy hh:mm:ss")
        self.account_widget = AccountSelector(self)
        self.peer_widget = PeerSelector(self)
        self.a_currency = OptionalCurrencyComboBox(self)
        self.a_currency.setText(self.tr("Paid in foreign currency:"))
        self.add_button = QPushButton(load_icon("add.png"), '', self)
        self.add_button.setToolTip(self.tr("Add detail"))
        self.del_button = QPushButton(load_icon("remove.png"), '', self)
        self.del_button.setToolTip(self.tr("Remove detail"))
        self.copy_button = QPushButton(load_icon("copy.png"), '', self)
        self.copy_button.setToolTip(self.tr("Copy detail"))
        self.details_table = QTableView(self)
        self.details_table.horizontalHeader().setFont(self.bold_font)
        self.details_table.setAlternatingRowColors(True)
        self.details_table.verticalHeader().setVisible(False)
        self.details_table.verticalHeader().setMinimumSectionSize(20)
        self.details_table.verticalHeader().setDefaultSectionSize(20)

        self.layout.addWidget(self.date_label, 1, 0, 1, 1, Qt.AlignLeft)
        self.layout.addWidget(self.details_label, 2, 0, 1, 1, Qt.AlignLeft)

        self.layout.addWidget(self.timestamp_editor, 1, 1, 1, 4)
        self.layout.addWidget(self.add_button, 2, 1, 1, 1)
        self.layout.addWidget(self.copy_button, 2, 2, 1, 1)
        self.layout.addWidget(self.del_button, 2, 3, 1, 1)

        self.layout.addWidget(self.account_label, 1, 5, 1, 1, Qt.AlignRight)
        self.layout.addWidget(self.peer_label, 2, 5, 1, 1, Qt.AlignRight)

        self.layout.addWidget(self.account_widget, 1, 6, 1, 1)
        self.layout.addWidget(self.peer_widget, 2, 6, 1, 1)

        self.layout.addWidget(self.a_currency, 1, 7, 1, 1)

        self.layout.addWidget(self.commit_button, 0, 9, 1, 1)
        self.layout.addWidget(self.revert_button, 0, 10, 1, 1)

        self.layout.addWidget(self.details_table, 4, 0, 1, 11)
        self.layout.addItem(self.horizontalSpacer, 1, 8, 1, 1)

        self.add_button.clicked.connect(self.addChild)
        self.copy_button.clicked.connect(self.copyChild)
        self.del_button.clicked.connect(self.delChild)

        super()._init_db("actions")
        self.model.beforeInsert.connect(self.before_record_insert)
        self.model.beforeUpdate.connect(self.before_record_update)
        self.mapper.setItemDelegate(IncomeSpendingWidgetDelegate(self.mapper))

        self.details_model = DetailsModel(self.details_table, db_connection())
        self.details_model.setTable("action_details")
        self.details_model.setEditStrategy(QSqlTableModel.OnManualSubmit)
        self.details_table.setModel(self.details_model)
        self.details_model.dataChanged.connect(self.onDataChange)

        self.account_widget.changed.connect(self.mapper.submit)
        self.peer_widget.changed.connect(self.mapper.submit)
        self.a_currency.changed.connect(self.mapper.submit)
        self.a_currency.name_updated.connect(self.details_model.setAltCurrency)

        self.mapper.addMapping(self.timestamp_editor, self.model.fieldIndex("timestamp"))
        self.mapper.addMapping(self.account_widget, self.model.fieldIndex("account_id"))
        self.mapper.addMapping(self.peer_widget, self.model.fieldIndex("peer_id"))
        self.mapper.addMapping(self.a_currency, self.model.fieldIndex("alt_currency_id"))

        self.details_table.setItemDelegateForColumn(2, self.category_delegate)
        self.details_table.setItemDelegateForColumn(3, self.tag_delegate)
        self.details_table.setItemDelegateForColumn(4, self.float_delegate)
        self.details_table.setItemDelegateForColumn(5, self.float_delegate)

        self.model.select()
        self.details_model.select()
        self.details_model.configureView()

    def setId(self, id):
        super().setId(id)
        self.details_model.setFilter(f"action_details.pid = {id}")

    @Slot()
    def addChild(self):
        new_record = self.details_model.record()
        new_record.setNull("tag_id")
        new_record.setValue("amount", 0)
        new_record.setValue("amount_alt", 0)
        if not self.details_model.insertRecord(-1, new_record):
            logging.fatal(self.tr("Failed to add new record: ") + self.details_model.lastError().text())
            return

    @Slot()
    def copyChild(self):
        idx = self.details_table.selectionModel().selection().indexes()
        src_record = self.details_model.record(idx[0].row())
        new_record = self.details_model.record()
        new_record.setValue("category_id", src_record.value("category_id"))
        if src_record.value("tag_id"):
            new_record.setValue("tag_id", src_record.value("tag_id"))
        else:
            new_record.setNull("tag_id")
        new_record.setValue("amount", src_record.value("amount"))
        new_record.setValue("amount_alt", src_record.value("amount_alt"))
        new_record.setValue("note", src_record.value("note"))
        if not self.details_model.insertRecord(-1, new_record):
            logging.fatal(self.tr("Failed to add new record: ") + self.details_model.lastError().text())
            return

    @Slot()
    def delChild(self):
        selection = self.details_table.selectionModel().selection().indexes()
        for idx in selection:
            self.details_model.removeRow(idx.row())
            self.onDataChange(idx, idx, None)

    @Slot()
    def saveChanges(self):
        if not self.model.submitAll():
            logging.fatal(self.tr("Operation submit failed: ") + self.model.lastError().text())
            return
        pid = self.model.data(self.model.index(0, self.model.fieldIndex("id")))
        if pid is None:  # we just have saved new action record and need last inserted id
            pid = self.model.query().lastInsertId()
        for row in range(self.details_model.rowCount()):
            self.details_model.setData(self.details_model.index(row, self.details_model.fieldIndex("pid")), pid)
        if not self.details_model.submitAll():
            logging.fatal(self.tr("Operation details submit failed: ") + self.details_model.lastError().text())
            return
        self.modified = False
        self.commit_button.setEnabled(False)
        self.revert_button.setEnabled(False)
        self.dbUpdated.emit()

    @Slot()
    def revertChanges(self):
        self.model.revertAll()
        self.details_model.revertAll()
        self.modified = False
        self.commit_button.setEnabled(False)
        self.revert_button.setEnabled(False)

    def createNew(self, account_id=0):
        super().createNew(account_id)
        self.details_model.setFilter(f"action_details.pid = 0")

    def prepareNew(self, account_id):
        new_record = super().prepareNew(account_id)
        new_record.setValue("timestamp", int(datetime.now().replace(tzinfo=tz.tzutc()).timestamp()))
        new_record.setValue("account_id", account_id)
        new_record.setValue("peer_id", 0)
        new_record.setValue("alt_currency_id", None)
        return new_record

    def copyNew(self):
        old_id = self.model.record(self.mapper.currentIndex()).value(0)
        super().copyNew()
        self.details_model.setFilter(f"action_details.pid = 0")
        query = executeSQL("SELECT * FROM action_details WHERE pid = :pid ORDER BY id DESC",
                           [(":pid", old_id)])
        while query.next():
            new_record = query.record()
            new_record.setNull("id")
            new_record.setNull("pid")
            assert self.details_model.insertRows(0, 1)
            self.details_model.setRecord(0, new_record)

    def copyToNew(self, row):
        new_record = self.model.record(row)
        new_record.setNull("id")
        new_record.setValue("timestamp", int(datetime.now().replace(tzinfo=tz.tzutc()).timestamp()))
        return new_record

    def before_record_insert(self, record):
        if record.value("alt_currency_id") == 0:
            record.setNull("alt_currency_id")

    def before_record_update(self, _row, record):
        self.before_record_insert(record)   # processing is the same as before insert


class DetailsModel(QSqlTableModel):
    def __init__(self, parent_view, db):
        super().__init__(parent=parent_view, db=db)
        self._columns = ["id", "pid", self.tr("Category"), self.tr("Tag"),
                         self.tr("Amount"), self.tr("Amount"), self.tr("Note")]
        self.deleted = []
        self.alt_currency_name = ''
        self._view = parent_view

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if section == 5:
                return self._columns[section] + ', ' + self.alt_currency_name
            else:
                return self._columns[section]
        return None

    def removeRow(self, row, parent=None):
        self.deleted.append(row)
        super().removeRow(row)

    def submitAll(self):
        result = super().submitAll()
        if result:
            self.deleted = []
        return result

    def revertAll(self):
        self.deleted = []
        super().revertAll()

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.FontRole and (index.row() in self.deleted):
            font = QFont()
            font.setStrikeOut(True)
            return font
        return super().data(index, role)

    def configureView(self):
        self._view.setColumnHidden(0, True)
        self._view.setColumnHidden(1, True)
        self._view.setColumnHidden(5, True)
        self._view.setColumnWidth(2, 200)
        self._view.setColumnWidth(3, 200)
        self._view.setColumnWidth(4, 100)
        self._view.setColumnWidth(5, 100)
        self._view.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self._view.horizontalHeader().moveSection(6, 0)

    def setAltCurrency(self, currency_name):
        if currency_name:
            self._view.setColumnHidden(5, False)
            self.alt_currency_name = currency_name
            self.headerDataChanged.emit(Qt.Horizontal, 5, 5)
        else:
            self._view.setColumnHidden(5, True)
