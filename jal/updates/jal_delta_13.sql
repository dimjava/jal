BEGIN TRANSACTION;
--------------------------------------------------------------------------------
PRAGMA foreign_keys = 0;

--------------------------------------------------------------------------------
-- Add field 'basis_ratio to corp_actions table
--------------------------------------------------------------------------------
CREATE TABLE sqlitestudio_temp_table AS SELECT * FROM corp_actions;

DROP TABLE corp_actions;

CREATE TABLE corp_actions (
    id           INTEGER     PRIMARY KEY
                             UNIQUE
                             NOT NULL,
    timestamp    INTEGER     NOT NULL,
    number       TEXT (32)   DEFAULT (''),
    account_id   INTEGER     REFERENCES accounts (id) ON DELETE CASCADE
                                                      ON UPDATE CASCADE
                             NOT NULL,
    type         INTEGER     NOT NULL,
    asset_id     INTEGER     REFERENCES assets (id) ON DELETE RESTRICT
                                                    ON UPDATE CASCADE
                             NOT NULL,
    qty          REAL        NOT NULL,
    asset_id_new INTEGER     REFERENCES assets (id) ON DELETE RESTRICT
                                                    ON UPDATE CASCADE
                             NOT NULL,
    qty_new      REAL        NOT NULL,
    basis_ratio  REAL        NOT NULL
                             DEFAULT (1),
    note         TEXT (1024)
);

INSERT INTO corp_actions (
                             id,
                             timestamp,
                             number,
                             account_id,
                             type,
                             asset_id,
                             qty,
                             asset_id_new,
                             qty_new,
                             note
                         )
                         SELECT id,
                                timestamp,
                                number,
                                account_id,
                                type,
                                asset_id,
                                qty,
                                asset_id_new,
                                qty_new,
                                note
                           FROM sqlitestudio_temp_table;

DROP TABLE sqlitestudio_temp_table;

CREATE TRIGGER corp_after_delete
         AFTER DELETE
            ON corp_actions
      FOR EACH ROW
BEGIN
    DELETE FROM ledger
          WHERE timestamp >= OLD.timestamp;
    DELETE FROM sequence
          WHERE timestamp >= OLD.timestamp;
    DELETE FROM ledger_sums
          WHERE timestamp >= OLD.timestamp;
END;

CREATE TRIGGER corp_after_insert
         AFTER INSERT
            ON corp_actions
      FOR EACH ROW
BEGIN
    DELETE FROM ledger
          WHERE timestamp >= NEW.timestamp;
    DELETE FROM sequence
          WHERE timestamp >= NEW.timestamp;
    DELETE FROM ledger_sums
          WHERE timestamp >= NEW.timestamp;
END;

CREATE TRIGGER corp_after_update
         AFTER UPDATE OF timestamp,
                         account_id,
                         type,
                         asset_id,
                         qty,
                         asset_id_new,
                         qty_new
            ON corp_actions
      FOR EACH ROW
BEGIN
    DELETE FROM ledger
          WHERE timestamp >= OLD.timestamp OR
                timestamp >= NEW.timestamp;
    DELETE FROM sequence
          WHERE timestamp >= OLD.timestamp OR
                timestamp >= NEW.timestamp;
    DELETE FROM ledger_sums
          WHERE timestamp >= OLD.timestamp OR
                timestamp >= NEW.timestamp;
END;

PRAGMA foreign_keys = 1;

--------------------------------------------------------------------------------
-- Include 'basis_ratio' in all_operations view
--------------------------------------------------------------------------------
DROP VIEW all_operations;

CREATE VIEW all_operations AS
    SELECT m.type,
           m.id,
           m.timestamp,
           m.account_id,
           a.name AS account,
           m.num_peer,
           m.asset_id,
           s.name AS asset,
           s.full_name AS asset_name,
           m.note,
           m.note2,
           m.amount,
           m.qty_trid,
           m.price,
           m.fee_tax,
           coalesce(money.sum_amount, 0) + coalesce(debt.sum_amount, 0) AS t_amount,
           m.t_qty,
           c.name AS currency,
           CASE WHEN m.timestamp <= a.reconciled_on THEN 1 ELSE 0 END AS reconciled
      FROM (
               SELECT 1 AS type,
                      o.id,
                      timestamp,
                      p.name AS num_peer,
                      account_id,
                      sum(d.sum) AS amount,
                      o.alt_currency_id AS asset_id,
                      NULL AS qty_trid,
                      sum(d.alt_sum) AS price,
                      NULL AS fee_tax,
                      NULL AS t_qty,
                      NULL AS note,
                      NULL AS note2,
                      o.id AS operation_id
                 FROM actions AS o
                      LEFT JOIN
                      agents AS p ON o.peer_id = p.id
                      LEFT JOIN
                      action_details AS d ON o.id = d.pid
                GROUP BY o.id
               UNION ALL
               SELECT 2 AS type,
                      d.id,
                      d.timestamp,
                      d.number AS num_peer,
                      d.account_id,
                      d.sum AS amount,
                      d.asset_id,
                      SUM(coalesce(l.amount, 0) ) AS qty_trid,
                      NULL AS price,
                      d.sum_tax AS fee_tax,
                      NULL AS t_qty,
                      d.note AS note,
                      c.name AS note2,
                      d.id AS operation_id
                 FROM dividends AS d
                      LEFT JOIN
                      ledger AS l ON d.asset_id = l.asset_id AND
                                     d.account_id = l.account_id AND
                                     l.book_account = 4 AND
                                     l.timestamp <= d.timestamp
                      LEFT JOIN
                      countries AS c ON d.tax_country_id = c.id
                GROUP BY d.id
               UNION ALL
               SELECT 5 AS type,
                      ca.id,
                      ca.timestamp,
                      ca.number AS num_peer,
                      ca.account_id,
                      ca.qty AS amount,
                      ca.asset_id,
                      ca.qty_new AS qty_trid,
                      ca.basis_ratio AS price,
                      ca.type AS fee_tax,
                      l.sum_amount AS t_qty,
                      a.name AS note,
                      a.full_name AS note2,
                      ca.id AS operation_id
                 FROM corp_actions AS ca
                      LEFT JOIN
                      assets AS a ON ca.asset_id_new = a.id
                      LEFT JOIN
                      sequence AS q ON q.type = 5 AND
                                       ca.id = q.operation_id
                      LEFT JOIN
                      ledger_sums AS l ON l.sid = q.id AND
                                          l.asset_id = ca.asset_id_new AND
                                          l.book_account = 4
               UNION ALL
               SELECT 3 AS type,
                      t.id,
                      t.timestamp,
                      t.number AS num_peer,
                      t.account_id,
-                     (t.price * t.qty) AS amount,
                      t.asset_id,
                      t.qty AS qty_trid,
                      t.price AS price,
                      t.fee AS fee_tax,
                      l.sum_amount AS t_qty,
                      NULL AS note,
                      NULL AS note2,
                      t.id AS operation_id
                 FROM trades AS t
                      LEFT JOIN
                      sequence AS q ON q.type = 3 AND
                                       t.id = q.operation_id
                      LEFT JOIN
                      ledger_sums AS l ON l.sid = q.id AND
                                          l.book_account = 4
               UNION ALL
               SELECT 4 AS type,
                      r.tid,
                      r.timestamp,
                      c.name AS num_peer,
                      r.account_id,
                      r.amount,
                      NULL AS asset_id,
                      r.type AS qty_trid,
                      r.rate AS price,
                      NULL AS fee_tax,
                      NULL AS t_qty,
                      n.note,
                      a.name AS note2,
                      r.id AS operation_id
                 FROM transfers AS r
                      LEFT JOIN
                      transfer_notes AS n ON r.tid = n.tid
                      LEFT JOIN
                      transfers AS tr ON r.tid = tr.tid AND
                                         r.type = -tr.type
                      LEFT JOIN
                      accounts AS a ON a.id = tr.account_id
                      LEFT JOIN
                      assets AS c ON c.id = a.currency_id
                ORDER BY timestamp
           )
           AS m
           LEFT JOIN
           accounts AS a ON m.account_id = a.id
           LEFT JOIN
           assets AS s ON m.asset_id = s.id
           LEFT JOIN
           assets AS c ON a.currency_id = c.id
           LEFT JOIN
           sequence AS q ON m.type = q.type AND
                            m.operation_id = q.operation_id
           LEFT JOIN
           ledger_sums AS money ON money.sid = q.id AND
                                   money.book_account = 3
           LEFT JOIN
           ledger_sums AS debt ON debt.sid = q.id AND
                                  debt.book_account = 5;
--------------------------------------------------------------------------------
-- Set new DB schema version
UPDATE settings SET value=13 WHERE name='SchemaVersion';

COMMIT;

