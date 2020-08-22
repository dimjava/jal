import datetime
import logging
import xlsxwriter
import pandas as pd
from constants import BookAccount, PredefinedAsset, ColumnWidth
from view_delegate import *
from DB.helpers import executeSQL, readSQLrecord
from CustomUI.helpers import UseSqlQuery, ConfigureTableView
from reports.helpers import xslxFormat, xlsxWriteRow
from PySide2.QtWidgets import QDialog, QFileDialog
from PySide2.QtCore import Qt, QObject, Property, Slot, Signal, QAbstractTableModel
from PySide2 import QtCore
from PySide2.QtSql import QSqlQuery
from UI.ui_deals_export_dlg import Ui_DealsExportDlg

class ReportType:
    IncomeSpending = 1
    ProfitLoss = 2
    Deals = 3

TOTAL_NAME = 'TOTAL'


class PandasModel(QAbstractTableModel):
    CATEGORY_LEVEL_SEPARATOR = chr(127)
    CATEGORY_INTEND = "  "

    def __init__(self, data):
        QAbstractTableModel.__init__(self)
        self._data = data

    def rowCount(self, parent=None):
        return self._data.shape[0]

    def columnCount(self, parnet=None):
        return self._data.shape[1] + 1    # +1 as extra leftmost column serves as a category header

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid():
            if role == Qt.DisplayRole:
                if index.column() == 0:
                    row_header = str(self._data.index[index.row()])
                    level = row_header.count(self.CATEGORY_LEVEL_SEPARATOR)
                    if level > 0:
                        row_header = row_header.rsplit(self.CATEGORY_LEVEL_SEPARATOR, 1)[1]
                    for i in range(level):
                        row_header = self.CATEGORY_INTEND + row_header
                    return row_header
                else:
                    return self._data.iloc[index.row(), index.column() - 1]
        return None

    def headerData(self, col, orientation, role):
        if (orientation == Qt.Horizontal and role == Qt.DisplayRole):
            if col == 0:        # Leftmost column serves as a category header
                return None
            if col == self._data.shape[1]:   # Rightmost total header
                return str(self._data.columns[col-1][0])
            col_date = datetime(year=int(self._data.columns[col-1][0]), month=int(self._data.columns[col-1][1]), day=1)
            return col_date.strftime("%Y %b")
        return None


class ReportParamsDialog(QDialog, Ui_DealsExportDlg):
    def __init__(self, parent, db):
        QDialog.__init__(self)
        self.setupUi(self)

        self.FileSelectBtn.setFixedWidth(self.FileSelectBtn.fontMetrics().width(" ... "))
        self.AccountWidget.init_db(db)
        self.FileSelectBtn.pressed.connect(self.OnFileBtn)

        self.ToDate.setDate(QtCore.QDate.currentDate())

        # center dialog with respect to parent window
        x = parent.x() + parent.width()/2 - self.width()/2
        y = parent.y() + parent.height()/2 - self.height()/2
        self.setGeometry(x, y, self.width(), self.height())

    @Slot()
    def OnFileBtn(self):
        filename = QFileDialog.getSaveFileName(self, self.tr("Save deals report to:"), ".",
                                               self.tr("Excel file (*.xlsx)"))
        if filename[0]:
            if filename[1] == self.tr("Excel file (*.xlsx)") and filename[0][-5:] != '.xlsx':
                self.Filename.setText(filename[0] + '.xlsx')
            else:
                self.Filename.setText(filename[0])

    def getFrom(self):
        return self.FromDate.dateTime().toSecsSinceEpoch()

    def getTo(self):
        return self.ToDate.dateTime().toSecsSinceEpoch()

    def getGroupByDates(self):
        if self.DateGroupCheckBox.isChecked():
            return True
        else:
            return False

    def getFilename(self):
        return self.Filename.text()

    def getAccount(self):
        return self.AccountWidget.selected_id

    begin = Property(int, fget=getFrom)
    end = Property(int, fget=getTo)
    group_dates = Property(bool, fget=getGroupByDates)
    filename = Property(int, fget=getFilename)
    account = Property(int, fget=getAccount)


PREPARE_REPORT_QUERY = 0
SHOW_REPORT = 1
REPORT_COLUMNS = 2

class Reports(QObject):
    report_failure = Signal(str)

    def __init__(self, db, report_table_view):
        super().__init__()

        self.db = db
        self.table_view = report_table_view
        self.delegates = []
        self.current_report = None
        self.query = None
        self.dataframe = None
        self.model = None

        self.workbook = None
        self.formats = None

        self.reports = {
            ReportType.IncomeSpending: (self.prepareIncomeSpendingReport,
                                        self.showPandasReport,
                                        []),
            ReportType.ProfitLoss: (self.prepareProfitLossReport,
                                    self.showSqlQueryReport,
                                    [("period", "Period", ColumnWidth.FOR_DATETIME, None, ReportsYearMonthDelegate),
                                    ("transfer", "In / Out", None, None, ReportsFloat2Delegate),
                                    ("assets", "Assets value", None, None, ReportsFloat2Delegate),
                                    ("result", "Total result", None, None, ReportsFloat2Delegate),
                                    ("profit", "Profit / Loss", None, None, ReportsProfitDelegate),
                                    ("dividend", "Returns", None, None, ReportsFloat2Delegate),
                                    ("tax_fee", "Taxes & Fees", None, None, ReportsFloat2Delegate)]),
            ReportType.Deals: (self.prepareDealsReport,
                               self.showSqlQueryReport,
                               [("asset", "Asset", 300, None, None),
                               ("open_timestamp", "Open Date", ColumnWidth.FOR_DATETIME, None, ReportsTimestampDelegate),
                               ("close_timestamp", "Close Date", ColumnWidth.FOR_DATETIME, None, ReportsTimestampDelegate),
                               ("open_price", "Open Price", None, None, ReportsFloat4Delegate),
                               ("close_price", "Close Price", None, None, ReportsFloat4Delegate),
                               ("qty", "Qty", None, None, ReportsFloatDelegate),
                               ("fee", "Fee", None, None, ReportsFloat2Delegate),
                               ("profit", "P/L", None, None, ReportsProfitDelegate),
                               ("rel_profit", "P/L, %", None, None, ReportsProfitDelegate)])
        }

    def runReport(self, report_type, begin=0, end=0, account_id=0, group_dates=0):
        self.reports[report_type][PREPARE_REPORT_QUERY](begin, end, account_id, group_dates)
        self.reports[report_type][SHOW_REPORT](report_type)

    def showSqlQueryReport(self, report_type):
        self.model = UseSqlQuery(self.db, self.query, self.reports[report_type][REPORT_COLUMNS])
        self.delegates = ConfigureTableView(self.table_view, self.model, self.reports[report_type][REPORT_COLUMNS])
        self.model.select()

    def showPandasReport(self, report_type):
        self.model = PandasModel(self.dataframe)
        self.table_view.setModel(self.model)
        self.delegates = []
        for column in range(self.model.columnCount()):
            if column == 0:
                self.table_view.setColumnWidth(column, 300)
            else:
                self.table_view.setColumnWidth(column, 100)
            self.delegates.append(ReportsPandasDelegate(self.table_view))
            self.table_view.setItemDelegateForColumn(column, self.delegates[-1])
        font = self.table_view.horizontalHeader().font()
        font.setBold(True)
        self.table_view.horizontalHeader().setFont(font)
        self.table_view.show()

    def saveReport(self):
        filename, filter = QFileDialog.getSaveFileName(None, "Save deals report to:", ".", "Excel file (*.xlsx)")
        if filename:
            if filter == self.tr("Excel file (*.xlsx)") and filename[-5:] != '.xlsx':
                filename = filename + '.xlsx'
        print(filename)

    def prepareIncomeSpendingReport(self, begin, end, account_id, group_dates):
        _ = executeSQL(self.db, "DELETE FROM t_months")
        _ = executeSQL(self.db, "DELETE FROM t_pivot")
        _ = executeSQL(self.db,
                       "INSERT INTO t_months (month, asset_id, last_timestamp) "
                      "SELECT strftime('%s', datetime(timestamp, 'unixepoch', 'start of month') ) "
                      "AS month, asset_id, MAX(timestamp) AS last_timestamp "
                      "FROM quotes AS q "
                      "LEFT JOIN assets AS a ON q.asset_id=a.id "
                      "WHERE a.type_id=:asset_money "
                      "GROUP BY month, asset_id",
                       [(":asset_money", PredefinedAsset.Money)])
        _ = executeSQL(self.db,
            "INSERT INTO t_pivot (row_key, col_key, value) "
            "SELECT strftime('%s', datetime(t.timestamp, 'unixepoch', 'start of month') ) AS row_key, "
            "t.category_id AS col_key, sum(-t.amount * coalesce(q.quote, 1)) AS value "
            "FROM ledger AS t "
            "LEFT JOIN t_months AS d ON row_key = d.month AND t.asset_id = d.asset_id "
            "LEFT JOIN quotes AS q ON d.last_timestamp = q.timestamp AND t.asset_id = q.asset_id "
            "WHERE (t.book_account=:book_costs OR t.book_account=:book_incomes) "
            "AND t.timestamp>=:begin AND t.timestamp<=:end "
            "GROUP BY row_key, col_key",
            [(":book_costs", BookAccount.Costs), (":book_incomes", BookAccount.Incomes),
             (":begin", begin), (":end", end)])
        self.db.commit()
        self.query = executeSQL(self.db,
                                "SELECT c.id, c.level, c.L0, c.L1, c.L2, c.path, "
                                "strftime('%Y', datetime(p.row_key, 'unixepoch')) AS year, "
                                "strftime('%m', datetime(p.row_key, 'unixepoch')) AS month, p.value "
                                "FROM categories_tree AS c "
                                "LEFT JOIN t_pivot AS p ON p.col_key=c.id "
                                "ORDER BY c.path, year, month")
        table = []
        while self.query.next():
            row = readSQLrecord(self.query)
            value = row[8] if row[8] != '' else 0
            table.append({
                'category': row[5],
                'Y': row[6],
                'M': row[7],
                'turnover': value
            })
        data = pd.DataFrame(table)
        data = pd.pivot_table(data, index=['category'], columns=['Y', 'M'], values='turnover',
                              aggfunc=sum, fill_value=0.0, margins=True, margins_name=TOTAL_NAME)
        if data.columns[0][1] == '':   # if some categories have no data and we have null 1st column
            data = data.drop(columns=[data.columns[0]])
        self.dataframe = data

    def prepareDealsReport(self, begin, end, account_id, group_dates):
        if account_id == 0:
            self.report_failure.emit("Deals report requires exact account")
            return None
        if group_dates == 1:
            self.query = executeSQL(self.db,
                               "SELECT asset, "
                               "strftime('%s', datetime(open_timestamp, 'unixepoch', 'start of day')) as open_timestamp, "
                               "strftime('%s', datetime(close_timestamp, 'unixepoch', 'start of day')) as close_timestamp, "
                               "SUM(open_price*qty)/SUM(qty) as open_price, SUM(close_price*qty)/SUM(qty) AS close_price, "
                               "SUM(qty) as qty, SUM(fee) as fee, SUM(profit) as profit, "
                               "coalesce(100*SUM(qty*(close_price-open_price)-fee)/SUM(qty*open_price), 0) AS rel_profit "
                               "FROM deals_ext "
                               "WHERE account_id=:account_id AND close_timestamp>=:begin AND close_timestamp<=:end "
                               "GROUP BY asset, open_timestamp, close_timestamp "
                               "ORDER BY close_timestamp, open_timestamp",
                               [(":account_id", account_id), (":begin", begin), (":end", end)], forward_only=False)
        else:
            self.query = executeSQL(self.db, "SELECT asset, open_timestamp, close_timestamp, open_price, close_price, "
                                        "qty, fee, profit, rel_profit FROM deals_ext "
                                        "WHERE account_id=:account_id AND close_timestamp>=:begin AND close_timestamp<=:end",
                               [(":account_id", account_id), (":begin", begin), (":end", end)], forward_only=False)

    def prepareProfitLossReport(self, begin, end, account_id, group_dates):
        if account_id == 0:
            self.report_failure.emit("Profit/Loss report requires exact account")
            return None
        _ = executeSQL(self.db, "DELETE FROM t_months")
        _ = executeSQL(self.db, "INSERT INTO t_months(asset_id, month, last_timestamp) "
                                "SELECT DISTINCT(l.asset_id) AS asset_id, m.m_start, MAX(q.timestamp) AS last_timestamp "
                                "FROM ledger AS l "
                                "LEFT JOIN "
                                "(WITH RECURSIVE months(m_start) AS "
                                "( "
                                "  VALUES(CAST(strftime('%s', date(:begin, 'unixepoch', 'start of month')) AS INTEGER)) "
                                "  UNION ALL "
                                "  SELECT CAST(strftime('%s', date(m_start, 'unixepoch', '+1 month')) AS INTEGER) "
                                "  FROM months "
                                "  WHERE m_start < :end "
                                ") "
                                "SELECT m_start FROM months) AS m "
                                "LEFT JOIN quotes AS q ON q.timestamp<=m.m_start AND q.asset_id=l.asset_id "
                                "WHERE l.timestamp>=:begin AND l.timestamp<=:end AND l.account_id=:account_id "
                                "GROUP BY m.m_start, l.asset_id "
                                "ORDER BY m.m_start, l.asset_id",
                       [(":account_id", account_id), (":begin", begin), (":end", end)])
        self.db.commit()
        self.query = executeSQL(self.db,
            "SELECT DISTINCT(m.month) AS period, coalesce(t.transfer, 0) AS transfer, coalesce(a.assets, 0) AS assets, "
            "coalesce(p.result, 0) AS result, coalesce(o.profit, 0) AS profit, coalesce(d.dividend, 0) AS dividend, "
            "coalesce(f.tax_fee, 0) AS tax_fee "
            "FROM t_months AS m "
            "LEFT JOIN ( "
            "  SELECT mt.month, SUM(-l.amount) AS transfer "
            "  FROM t_months AS mt "
            "  LEFT JOIN ledger AS l ON mt.month = "
            "  CAST(strftime('%s', date(l.timestamp, 'unixepoch', 'start of month')) AS INTEGER) "
            "  AND mt.asset_id=l.asset_id "
            "  WHERE l.book_account=:book_transfers AND l.account_id=:account_id GROUP BY mt.month "
            ") AS t ON t.month = m.month "
            "LEFT JOIN ( "
            "  SELECT ma.month, SUM(l.amount*q.quote) AS assets "
            "  FROM t_months AS ma "
            "  LEFT JOIN ledger AS l ON l.timestamp<=ma.month AND l.asset_id=ma.asset_id "
            "  LEFT JOIN quotes AS q ON ma.last_timestamp=q.timestamp AND ma.asset_id=q.asset_id "
            "  WHERE l.account_id = 76 AND (l.book_account=:book_money OR l.book_account=:book_assets) "
            "  GROUP BY ma.month "
            ") AS a ON a.month = m.month "
            "LEFT JOIN ( "
            "  SELECT CAST(strftime('%s', date(l.timestamp, 'unixepoch', 'start of month')) AS INTEGER) AS month,"
            "  SUM(-l.amount) as result"
            "  FROM ledger AS l  "
            "  WHERE (l.book_account=:book_costs OR l.book_account=:book_incomes) AND l.account_id=:account_id "
            "  GROUP BY month "
            ") AS p ON p.month = m.month "
            "LEFT JOIN ( "
            "  SELECT CAST(strftime('%s', date(l.timestamp, 'unixepoch', 'start of month')) "
            "  AS INTEGER) AS month, SUM(-l.amount) as profit "
            "  FROM ledger AS l "
            "  WHERE (l.book_account=:book_costs OR l.book_account=:book_incomes) "
            "  AND category_id=9 AND l.account_id=:account_id "
            "  GROUP BY month "
            ") AS o ON o.month = m.month "
            "LEFT JOIN ( "
            "  SELECT CAST(strftime('%s', date(l.timestamp, 'unixepoch', 'start of month')) AS INTEGER) "
            "  AS month, SUM(-l.amount) as dividend "
            "  FROM ledger AS l "
            "  WHERE (l.book_account=:book_costs OR l.book_account=:book_incomes) "
            "  AND (l.category_id=7 OR l.category_id=8) AND l.account_id=:account_id "
            "  GROUP BY month "
            ") AS d ON d.month = m.month "
            "LEFT JOIN ( "
            "  SELECT CAST(strftime('%s', date(l.timestamp, 'unixepoch', 'start of month')) "
            "  AS INTEGER) AS month, SUM(-l.amount) as tax_fee "
            "  FROM ledger AS l "
            "  WHERE l.book_account=:book_costs AND l.category_id<>7 AND l.category_id<>8 AND l.account_id=:account_id "
            "  GROUP BY month "
            ") AS f ON f.month = m.month",
            [(":account_id", account_id), (":book_costs", BookAccount.Costs), (":book_incomes", BookAccount.Incomes),
             (":book_money", BookAccount.Money), (":book_assets", BookAccount.Assets),
             (":book_transfers", BookAccount.Transfers)],
                           forward_only=False)


########################################################################################################################
########################################################################################################################
########################################################################################################################
########################################################################################################################
    def create_report(self, parent, report_type):
        if report_type == self.DEALS_REPORT:
            dialog_title = "Prepare deals report"
        elif report_type == self.PROFIT_LOSS_REPORT:
            dialog_title = "Prepare profit/loss report"
        elif report_type == self.INCOME_SPENDING_REPORT:
            dialog_title = "Prepare income/spending report"
        else:
            logging.warning("Unknown report type")

        dialog = ReportParamsDialog(parent, self.db)
        dialog.setWindowTitle(dialog_title)
        if dialog.exec_():
            if report_type == self.DEALS_REPORT:
                self.save_deals(dialog.filename, dialog.account, dialog.begin, dialog.end, dialog.group_dates)
            elif report_type == self.PROFIT_LOSS_REPORT:
                self.save_profit_loss(dialog.filename, dialog.account, dialog.begin, dialog.end)
            elif report_type == self.INCOME_SPENDING_REPORT:
                self.save_income_sending(dialog.filename, dialog.begin, dialog.end)

    def save_deals(self, report_filename, account_id, begin, end, group_dates):
        self.workbook = xlsxwriter.Workbook(filename=report_filename)
        self.formats = xslxFormat(self.workbook)
        sheet = self.workbook.add_worksheet(name="Deals")

        query = QSqlQuery(self.db)
        if group_dates:
            query.prepare("SELECT asset, "
                          "strftime('%s', datetime(open_timestamp, 'unixepoch', 'start of day')) as open_timestamp, "
                          "strftime('%s', datetime(close_timestamp, 'unixepoch', 'start of day')) as close_timestamp, "
                          "SUM(open_price*qty)/SUM(qty) as open_price, SUM(close_price*qty)/SUM(qty) AS close_price, "
                          "SUM(qty) as qty, SUM(fee) as fee, SUM(profit) as profit, "
                          "coalesce(100*SUM(qty*(close_price-open_price)-fee)/SUM(qty*open_price), 0) AS rel_profit "
                          "FROM deals_ext "
                          "WHERE account_id=:account_id AND close_timestamp>=:begin AND close_timestamp<=:end "
                          "GROUP BY asset, open_timestamp, close_timestamp "
                          "ORDER BY close_timestamp, open_timestamp")
        else:
            query.prepare("SELECT asset, open_timestamp, close_timestamp, open_price, close_price, "
                          "qty, fee, profit, rel_profit FROM deals_ext "
                          "WHERE account_id=:account_id AND close_timestamp>=:begin AND close_timestamp<=:end")
        query.bindValue(":account_id", account_id)
        query.bindValue(":begin", begin)
        query.bindValue(":end", end)
        assert query.exec_()

        header_row = {
            0: ("Asset", self.formats.ColumnHeader(), 15, 0, 1),
            1: ("Date", self.formats.ColumnHeader(), 0, 1, 0),
            3: ("Price", self.formats.ColumnHeader(), 0, 1, 0),
            5: ("Qty", self.formats.ColumnHeader(), 10, 0, 1),
            6: ("Fre", self.formats.ColumnHeader(), 10, 0, 1),
            7: ("Profit / Loss", self.formats.ColumnHeader(), 10, 0, 1),
            8: ("Profit / Loss, %", self.formats.ColumnHeader(), 8, 0, 1)
        }
        xlsxWriteRow(sheet, 0, header_row)

        header_row = {
            1: ("Open", self.formats.ColumnHeader(), 20, 0, 0),
            2: ("Close", self.formats.ColumnHeader(), 20, 0, 0),
            3: ("Open", self.formats.ColumnHeader(), 10, 0, 0),
            4: ("Close", self.formats.ColumnHeader(), 10, 0, 0)
        }
        xlsxWriteRow(sheet, 1, header_row)

        row = 2
        while query.next():
            sheet.write(row, 0, query.value('asset'), self.formats.Text(row))
            open_timestamp = int(query.value("open_timestamp"))
            close_timestamp = int(query.value("close_timestamp"))
            if group_dates:
                sheet.write(row, 1, datetime.datetime.fromtimestamp(open_timestamp).strftime('%d.%m.%Y'),
                            self.formats.Text(row))
                sheet.write(row, 2, datetime.datetime.fromtimestamp(close_timestamp).strftime('%d.%m.%Y'),
                            self.formats.Text(row))
            else:
                sheet.write(row, 1, datetime.datetime.fromtimestamp(open_timestamp).strftime('%d.%m.%Y %H:%M:%S'),
                            self.formats.Text(row))
                sheet.write(row, 2, datetime.datetime.fromtimestamp(close_timestamp).strftime('%d.%m.%Y %H:%M:%S'),
                            self.formats.Text(row))
            sheet.write(row, 3, float(query.value('open_price')), self.formats.Number(row, 4))
            sheet.write(row, 4, float(query.value('close_price')), self.formats.Number(row, 4))
            sheet.write(row, 5, float(query.value('qty')), self.formats.Number(row, 0, True))
            sheet.write(row, 6, float(query.value('fee')), self.formats.Number(row, 4))
            sheet.write(row, 7, float(query.value('profit')), self.formats.Number(row, 2))
            sheet.write(row, 8, float(query.value('rel_profit')), self.formats.Number(row, 2))
            row = row + 1

        self.workbook.close()

    def save_profit_loss(self, report_filename, account_id, begin, end):
        self.workbook = xlsxwriter.Workbook(filename=report_filename)
        self.formats = xslxFormat(self.workbook)
        sheet = self.workbook.add_worksheet(name="P&L")

        query = QSqlQuery(self.db)
        assert query.exec_("DELETE FROM t_months")
        query.prepare("INSERT INTO t_months(asset_id, month, last_timestamp) "
                      "SELECT DISTINCT(l.asset_id) AS asset_id, m.m_start, MAX(q.timestamp) AS last_timestamp "
                      "FROM ledger AS l "
                      "LEFT JOIN "
                      "(WITH RECURSIVE months(m_start) AS "
                      "( "
                      "  VALUES(CAST(strftime('%s', date(:begin, 'unixepoch', 'start of month')) AS INTEGER)) "
                      "  UNION ALL "
                      "  SELECT CAST(strftime('%s', date(m_start, 'unixepoch', '+1 month')) AS INTEGER) "
                      "  FROM months "
                      "  WHERE m_start < :end "
                      ") "
                      "SELECT m_start FROM months) AS m "
                      "LEFT JOIN quotes AS q ON q.timestamp<=m.m_start AND q.asset_id=l.asset_id "
                      "WHERE l.timestamp>=:begin AND l.timestamp<=:end AND l.account_id=:account_id "
                      "GROUP BY m.m_start, l.asset_id "
                      "ORDER BY m.m_start, l.asset_id")
        query.bindValue(":account_id", account_id)
        query.bindValue(":begin", begin)
        query.bindValue(":end", end)
        assert query.exec_()

        query.prepare(
            "SELECT DISTINCT(m.month) AS period, coalesce(t.transfer, 0) AS transfer, coalesce(a.assets, 0) AS assets, "
            "coalesce(p.result, 0) AS result, coalesce(o.profit, 0) AS profit, coalesce(d.dividend, 0) AS dividend, "
            "coalesce(f.tax_fee, 0) AS tax_fee "
            "FROM t_months AS m "
            "LEFT JOIN ( "
            "  SELECT mt.month, SUM(-l.amount) AS transfer "
            "  FROM t_months AS mt "
            "  LEFT JOIN ledger AS l ON mt.month = "
            "  CAST(strftime('%s', date(l.timestamp, 'unixepoch', 'start of month')) AS INTEGER) "
            "  AND mt.asset_id=l.asset_id "
            "  WHERE l.book_account=:book_transfers AND l.account_id=:account_id GROUP BY mt.month "
            ") AS t ON t.month = m.month "
            "LEFT JOIN ( "
            "  SELECT ma.month, SUM(l.amount*q.quote) AS assets "
            "  FROM t_months AS ma "
            "  LEFT JOIN ledger AS l ON l.timestamp<=ma.month AND l.asset_id=ma.asset_id "
            "  LEFT JOIN quotes AS q ON ma.last_timestamp=q.timestamp AND ma.asset_id=q.asset_id "
            "  WHERE l.account_id = 76 AND (l.book_account=:book_money OR l.book_account=:book_assets) "
            "  GROUP BY ma.month "
            ") AS a ON a.month = m.month "
            "LEFT JOIN ( "
            "  SELECT CAST(strftime('%s', date(l.timestamp, 'unixepoch', 'start of month')) AS INTEGER) AS month,"
            "  SUM(-l.amount) as result"
            "  FROM ledger AS l  "
            "  WHERE (l.book_account=:book_costs OR l.book_account=:book_incomes) AND l.account_id=:account_id "
            "  GROUP BY month "
            ") AS p ON p.month = m.month "
            "LEFT JOIN ( "
            "  SELECT CAST(strftime('%s', date(l.timestamp, 'unixepoch', 'start of month')) "
            "  AS INTEGER) AS month, SUM(-l.amount) as profit "
            "  FROM ledger AS l "
            "  WHERE (l.book_account=:book_costs OR l.book_account=:book_incomes) "
            "  AND category_id=9 AND l.account_id=:account_id "
            "  GROUP BY month "
            ") AS o ON o.month = m.month "
            "LEFT JOIN ( "
            "  SELECT CAST(strftime('%s', date(l.timestamp, 'unixepoch', 'start of month')) AS INTEGER) "
            "  AS month, SUM(-l.amount) as dividend "
            "  FROM ledger AS l "
            "  WHERE (l.book_account=:book_costs OR l.book_account=:book_incomes) "
            "  AND (l.category_id=7 OR l.category_id=8) AND l.account_id=:account_id "
            "  GROUP BY month "
            ") AS d ON d.month = m.month "
            "LEFT JOIN ( "
            "  SELECT CAST(strftime('%s', date(l.timestamp, 'unixepoch', 'start of month')) "
            "  AS INTEGER) AS month, SUM(-l.amount) as tax_fee "
            "  FROM ledger AS l "
            "  WHERE l.book_account=:book_costs AND l.category_id<>7 AND l.category_id<>8 AND l.account_id=:account_id "
            "  GROUP BY month "
            ") AS f ON f.month = m.month")
        query.bindValue(":account_id", account_id)
        query.bindValue(":book_costs", BookAccount.Costs)
        query.bindValue(":book_incomes", BookAccount.Incomes)
        query.bindValue(":book_money", BookAccount.Money)
        query.bindValue(":book_assets", BookAccount.Assets)
        query.bindValue(":book_transfers", BookAccount.Transfers)
        assert query.exec_()

        sheet.write(0, 0, "Period", self.formats.ColumnHeader())
        sheet.write(0, 1, "In / Out", self.formats.ColumnHeader())
        sheet.write(0, 2, "Assets value", self.formats.ColumnHeader())
        sheet.write(0, 3, "Total result", self.formats.ColumnHeader())
        sheet.write(0, 4, "Profit / Loss", self.formats.ColumnHeader())
        sheet.write(0, 5, "Dividends, Coupons, Interest", self.formats.ColumnHeader())
        sheet.write(0, 6, "Taxes & Fees", self.formats.ColumnHeader())
        sheet.set_column(0, 6, 15)
        row = 1
        while query.next():
            period = int(query.value("period"))
            sheet.write(row, 0, datetime.datetime.fromtimestamp(period).strftime('%Y %B'),
                        self.formats.Text(row))
            sheet.write(row, 1, float(query.value("transfer")), self.formats.Number(row, 2))
            sheet.write(row, 2, float(query.value('assets')), self.formats.Number(row, 2))
            sheet.write(row, 3, float(query.value('result')), self.formats.Number(row, 2))
            sheet.write(row, 4, float(query.value('profit')), self.formats.Number(row, 2))
            sheet.write(row, 5, float(query.value('dividend')), self.formats.Number(row, 2))
            sheet.write(row, 6, float(query.value('tax_fee')), self.formats.Number(row, 2))
            row = row + 1

        self.workbook.close()

    def save_income_sending(self, report_filename, begin, end):
        self.workbook = xlsxwriter.Workbook(filename=report_filename)
        self.formats = xslxFormat(self.workbook)
        sheet = self.workbook.add_worksheet(name="Income & Spending")

        query = QSqlQuery(self.db)
        assert query.exec_("DELETE FROM t_months")

        query.prepare("INSERT INTO t_months (month, asset_id, last_timestamp) "
                      "SELECT strftime('%s', datetime(timestamp, 'unixepoch', 'start of month') ) "
                      "AS month, asset_id, MAX(timestamp) AS last_timestamp "
                      "FROM quotes AS q "
                      "LEFT JOIN assets AS a ON q.asset_id=a.id "
                      "WHERE a.type_id=:asset_money "
                      "GROUP BY month, asset_id")
        query.bindValue(":asset_money", PredefinedAsset.Money)
        assert query.exec_()

        query.prepare(
            "SELECT strftime('%s', datetime(t.timestamp, 'unixepoch', 'start of month') ) AS month_timestamp, "
            "datetime(t.timestamp, 'unixepoch', 'start of month') AS month_date, a.name AS account, "
            "c.name AS currency, coalesce(q.quote, 1) AS rate, s.name AS category, sum(-t.amount) AS turnover "
            "FROM ledger AS t "
            "LEFT JOIN accounts AS a ON t.account_id = a.id "
            "LEFT JOIN assets AS c ON t.asset_id = c.id "
            "LEFT JOIN categories AS s ON t.category_id = s.id "
            "LEFT JOIN t_months AS d ON month_timestamp = d.month AND t.asset_id = d.asset_id "
            "LEFT JOIN quotes AS q ON d.last_timestamp = q.timestamp AND d.asset_id = q.asset_id "
            "WHERE (t.book_account=:book_costs OR t.book_account=:book_incomes) "
            "AND t.timestamp>=:begin AND t.timestamp<=:end "
            "GROUP BY month_timestamp, t.account_id, t.asset_id, t.category_id "
            "ORDER BY currency, month_timestamp, category")
        query.bindValue(":book_costs", BookAccount.Costs)
        query.bindValue(":book_incomes", BookAccount.Incomes)
        query.bindValue(":begin", begin)
        query.bindValue(":end", end)
        assert query.exec_()

        sheet.write(0, 0, "Period", self.formats.ColumnHeader())
        sheet.write(0, 1, "Account", self.formats.ColumnHeader())
        sheet.write(0, 2, "Currency", self.formats.ColumnHeader())
        sheet.write(0, 3, "Currency rate", self.formats.ColumnHeader())
        sheet.write(0, 4, "Category", self.formats.ColumnHeader())
        sheet.write(0, 5, "Turnover", self.formats.ColumnHeader())
        sheet.set_column(0, 7, 15)
        row = 1
        while query.next():
            period = int(query.value("month_timestamp"))
            sheet.write(row, 0, datetime.datetime.fromtimestamp(period).strftime('%Y %B'),
                        self.formats.Text(row))
            sheet.write(row, 1, query.value("account"), self.formats.Text(row))
            sheet.write(row, 2, query.value('currency'), self.formats.Text(row))
            sheet.write(row, 3, float(query.value('rate')), self.formats.Number(row, 2))
            sheet.write(row, 4, query.value('category'), self.formats.Text(row))
            sheet.write(row, 5, float(query.value('turnover')), self.formats.Number(row, 2))
            row = row + 1

        self.workbook.close()
