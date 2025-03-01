from jal.db.helpers import executeSQL
from constants import PredefinedAsset, DividendSubtype


# ----------------------------------------------------------------------------------------------------------------------
# Helper functions for test set creation unification
# ----------------------------------------------------------------------------------------------------------------------

# ----------------------------------------------------------------------------------------------------------------------
# Create assets in database with PredefinedAsset.Stock type : assets is a list of tuples (asset_id, symbol, full_name)
def create_stocks(assets):
    for asset in assets:
        assert executeSQL("INSERT INTO assets (id, name, type_id, full_name) "
                          "VALUES (:id, :name, :type, :full_name)",
                          [(":id", asset[0]), (":name", asset[1]),
                           (":type", PredefinedAsset.Stock), (":full_name", asset[2])], commit=True) is not None


# ----------------------------------------------------------------------------------------------------------------------
# Create assets in database with PredefinedAsset.Stock type : assets is a list of tuples
# (symbol, full_name, isin, asset_type, country_id)
def create_assets(assets):
    for asset in assets:
        assert executeSQL("INSERT INTO assets (name, full_name, isin, type_id, country_id) "
                          "VALUES (:name, :full_name, :isin, :type_id, :country_id)",
                          [(":name", asset[0]), (":full_name", asset[1]), (":isin", asset[2]),
                           (":type_id", asset[3]), (":country_id", asset[4])], commit=True) is not None


# ----------------------------------------------------------------------------------------------------------------------
# Insert quotes for asset_id into database. Quotes is a list of (timestamp, quote) tuples
def create_quotes(asset_id, quotes):
    for quote in quotes:
        assert executeSQL("INSERT INTO quotes (timestamp, asset_id, quote) VALUES (:timestamp, :asset_id, :quote)",
                          [(":timestamp", quote[0]), (":asset_id", asset_id), (":quote", quote[1])],
                          commit=True) is not None


# ----------------------------------------------------------------------------------------------------------------------
# Create actions in database: actions is a list of tuples
# (timestamp, account, peer, [(category, amount, [note]), (category, amount, [note]), ...])
def create_actions(actions):
    for action in actions:
        query = executeSQL("INSERT INTO actions (timestamp, account_id, peer_id) "
                           "VALUES (:timestamp, :account, :peer)",
                           [(":timestamp", action[0]), (":account", action[1]), (":peer", action[2])], commit=True)
        assert query is not None
        action_id = query.lastInsertId()
        for detail in action[3]:
            note = detail[2] if len(detail) > 2 else ''
            assert executeSQL("INSERT INTO action_details (pid, category_id, amount, note) "
                              "VALUES (:pid, :category, :amount, :note)",
                              [(":pid", action_id), (":category", detail[0]), (":amount", detail[1]), (":note", note)],
                              commit=True) is not None


# ----------------------------------------------------------------------------------------------------------------------
# Create dividends in database: dividends is a list of dividends as tuples
# (timestamp, account, asset_id, amount, tax, note)
def create_dividends(dividends):
    for dividend in dividends:
        assert executeSQL("INSERT INTO dividends (timestamp, type, account_id, asset_id, amount, tax, note) "
                          "VALUES (:timestamp, :div_type, :account_id, :asset_id, :amount, :tax, :note)",
                          [(":timestamp", dividend[0]), (":div_type", DividendSubtype.Dividend),
                           (":account_id", dividend[1]), (":asset_id", dividend[2]), (":amount", dividend[3]),
                           (":tax", dividend[4]), (":note", dividend[5])], commit=True) is not None


# ----------------------------------------------------------------------------------------------------------------------
# Create dividends with type "interest" in database: coupons is a list of interests as tuples
# (timestamp, account, asset_id, amount, tax, note, number)
def create_coupons(coupons):
    for coupon in coupons:
        assert executeSQL("INSERT INTO dividends (timestamp, type, account_id, asset_id, amount, tax, note, number) "
                          "VALUES (:timestamp, :div_type, :account_id, :asset_id, :amount, :tax, :note, :number)",
                          [(":timestamp", coupon[0]), (":div_type", DividendSubtype.BondInterest),
                           (":account_id", coupon[1]), (":asset_id", coupon[2]), (":amount", coupon[3]),
                           (":tax", coupon[4]), (":note", coupon[5]), (":number", coupon[6])], commit=True) is not None


# ----------------------------------------------------------------------------------------------------------------------
# Create dividends in database: dividends is a list of dividends as tuples
# (timestamp, account, asset_id, qty, quote, tax, note)
def create_stock_dividends(dividends):
    for dividend in dividends:
        create_quotes(dividend[2], [(dividend[0], dividend[4])])
        assert executeSQL("INSERT INTO dividends (timestamp, type, account_id, asset_id, amount, tax, note) "
                          "VALUES (:timestamp, :div_type, :account_id, :asset_id, :amount, :tax, :note)",
                          [(":timestamp", dividend[0]), (":div_type", DividendSubtype.StockDividend),
                           (":account_id", dividend[1]), (":asset_id", dividend[2]), (":amount", dividend[3]),
                           (":tax", dividend[5]), (":note", dividend[6])], commit=True) is not None


# ----------------------------------------------------------------------------------------------------------------------
# Create trades for given account_id in database: trades is a list of trades as tuples
# (timestamp, settlement, asset_id, qty, price, fee, [number])
def create_trades(account_id, trades):
    for trade in trades:
        number = trade[6] if len(trade) > 6 else ''
        assert executeSQL("INSERT INTO trades (timestamp, settlement, account_id, asset_id, qty, price, fee, number) "
                          "VALUES (:timestamp, :settlement, :account_id, :asset, :qty, :price, :fee, :number)",
                          [(":timestamp", trade[0]), (":settlement", trade[1]), (":account_id", account_id),
                           (":asset", trade[2]), (":qty", trade[3]), (":price", trade[4]), (":fee", trade[5]),
                           (":number", number)], commit=True) is not None


# ----------------------------------------------------------------------------------------------------------------------
# Create corporate actions for given account_id in database: actions is a list of tuples
# (timestamp, type, asset_old, qty_old, asset_new, qty_new, basis, note)
def create_corporate_actions(account_id, actions):
    for action in actions:
        assert executeSQL("INSERT INTO corp_actions (timestamp, account_id, type, asset_id, qty, asset_id_new, "
                          "qty_new, basis_ratio, note) VALUES (:timestamp, :account_id, :type, :asset_id, :qty, "
                          ":asset_id_new, :qty_new, :basis, :note)",
                          [(":timestamp", action[0]), (":account_id", account_id), (":type", action[1]),
                           (":asset_id", action[2]), (":qty", action[3]), (":asset_id_new", action[4]),
                           (":qty_new", action[5]), (":basis", action[6]), (":note", action[7])],
                          commit=True) is not None
