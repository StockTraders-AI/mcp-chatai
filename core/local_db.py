"""
Local SQLite cache for the 9 "base" StockTraders API operations (see
plan: 8 raw sources + getWaveBottomConfirmPairs, which is purely derived
from getStockWave). Replaces live HTTP calls for exactly these 9
operationIds in core/executor.py, behind the STOCKTRADERS_DB_MODE env
flag - every other tool keeps calling the live API unchanged.

Two entry points:
  - load_csv_into_db(): one-time/rerunnable ingest from the CSV export
    produced by re-api/export_9_api_csv.py (data/api_export_csv/*.csv)
    into data/stocktraders_cache.db.
  - read_from_db(operation_id, args): returns the exact same JSON shape
    the live API returns for that operationId + args, using ONLY the
    params stocktraders-mcp actually exposes in data/openapi/stock_api.json
    (a strict subset of what the real re-api routers support - lastDays/
    baseDate combos etc. are NOT replicated here because stocktraders-mcp
    never exposes them as tool args in the first place).

Returns None when the DB has never been synced for that operation at all
(so the caller can fall back to a live call) - NOT when a specific filter
just matches zero rows (that returns an empty list/dict, same as a live
call that happens to match nothing).
"""

import ast
import csv
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("STOCKTRADERS_DB_PATH", str(BASE_DIR / "data" / "stocktraders_cache.db")))
CSV_DIR = Path(os.getenv("STOCKTRADERS_CSV_DIR", str(BASE_DIR / "api_export_csv")))

DB_BACKED_OPERATIONS = {
    "getStockWave",
    "getStockSignal",
    "getSMDTTicker",
    "getSMDTBranch",
    "getSMDTTickerCross",
    "getSMDTBranchCross",
    "getCashFlowBranch",
    "getCashFlowTicker",
    "getWaveBottomConfirmPairs",
}


def db_mode_enabled() -> bool:
    return str(os.getenv("STOCKTRADERS_DB_MODE", "")).strip() == "1"


# ============================================================
# SCHEMA + INGEST
# ============================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_wave (
    date TEXT PRIMARY KEY,
    name TEXT,
    buy INTEGER, sell INTEGER, waitbuy INTEGER, waitsell INTEGER,
    total INTEGER, reliability INTEGER,
    tickerB TEXT, tickerS TEXT, tickerWB TEXT, tickerwWS TEXT
);

CREATE TABLE IF NOT EXISTS stock_signal (
    ticker TEXT, date TEXT, smdt REAL, price REAL, percent REAL,
    ave REAL, hold REAL, trade INTEGER,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS smdt_ticker (
    keyName TEXT, keyValue TEXT, date TEXT, smdt REAL,
    PRIMARY KEY (keyValue, date)
);
CREATE TABLE IF NOT EXISTS smdt_branch (
    keyName TEXT, keyValue TEXT, date TEXT, smdt REAL,
    PRIMARY KEY (keyValue, date)
);
CREATE TABLE IF NOT EXISTS smdt_ticker_cross (
    keyName TEXT, keyValue TEXT, date TEXT, smdt REAL,
    PRIMARY KEY (keyValue, date)
);
CREATE TABLE IF NOT EXISTS smdt_branch_cross (
    keyName TEXT, keyValue TEXT, date TEXT, smdt REAL,
    PRIMARY KEY (keyValue, date)
);

CREATE TABLE IF NOT EXISTS cash_flow_branch (
    date TEXT, name TEXT, path TEXT, content TEXT,
    PRIMARY KEY (date, path)
);
CREATE TABLE IF NOT EXISTS cash_flow_ticker (
    date TEXT, ticker TEXT, content TEXT, type TEXT, price REAL, percent TEXT,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS wave_bottom_confirm_pairs (
    confirm_wave_date TEXT PRIMARY KEY,
    prepare_bottom_date TEXT,
    reliability INTEGER
);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    conn = conn or _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        if own:
            conn.close()


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_csv(name: str) -> List[Dict[str, str]]:
    path = CSV_DIR / f"{name}.csv"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_csv_into_db(csv_dir: Optional[str] = None) -> Dict[str, int]:
    """(re)load all 9 CSVs into SQLite. Safe to rerun - each table is
    cleared and reloaded whole, since the CSVs are themselves a full
    historical export, not an incremental delta."""
    global CSV_DIR
    if csv_dir:
        CSV_DIR = Path(csv_dir)

    conn = _connect()
    counts: Dict[str, int] = {}
    try:
        init_schema(conn)

        rows = _read_csv("getStockWave")
        conn.execute("DELETE FROM stock_wave")
        conn.executemany(
            "INSERT OR REPLACE INTO stock_wave VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    r["date"], r.get("name") or "ALL",
                    _to_int(r.get("buy")), _to_int(r.get("sell")),
                    _to_int(r.get("waitbuy")), _to_int(r.get("waitsell")),
                    _to_int(r.get("total")), _to_int(r.get("reliability")),
                    r.get("tickerB") or "[]", r.get("tickerS") or "[]",
                    r.get("tickerWB") or "[]", r.get("tickerwWS") or "[]",
                )
                for r in rows if r.get("date")
            ],
        )
        counts["stock_wave"] = len(rows)

        rows = _read_csv("getStockSignal")
        conn.execute("DELETE FROM stock_signal")
        conn.executemany(
            "INSERT OR REPLACE INTO stock_signal VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    (r.get("ticker") or "").upper(), r["date"],
                    _to_float(r.get("smdt")), _to_float(r.get("price")),
                    _to_float(r.get("percent")), _to_float(r.get("ave")),
                    _to_float(r.get("hold")), _to_int(r.get("trade")),
                )
                for r in rows if r.get("date") and r.get("ticker")
            ],
        )
        counts["stock_signal"] = len(rows)

        smdt_family = [
            ("getSMDTTicker", "smdt_ticker"),
            ("getSMDTBranch", "smdt_branch"),
            ("getSMDTTickerCross", "smdt_ticker_cross"),
            ("getSMDTBranchCross", "smdt_branch_cross"),
        ]
        for csv_name, table in smdt_family:
            rows = _read_csv(csv_name)
            conn.execute(f"DELETE FROM {table}")
            conn.executemany(
                f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?)",
                [
                    (r.get("keyName") or "", r.get("keyValue") or "", r["date"], _to_float(r.get("smdt")))
                    for r in rows if r.get("date") and r.get("keyValue")
                ],
            )
            counts[table] = len(rows)

        rows = _read_csv("getCashFlowBranch")
        conn.execute("DELETE FROM cash_flow_branch")
        conn.executemany(
            "INSERT OR REPLACE INTO cash_flow_branch VALUES (?,?,?,?)",
            [
                (r["date"], r.get("name") or "", r.get("path") or "", r.get("content") or "")
                for r in rows if r.get("date") and r.get("path")
            ],
        )
        counts["cash_flow_branch"] = len(rows)

        rows = _read_csv("getCashFlowTicker")
        conn.execute("DELETE FROM cash_flow_ticker")
        conn.executemany(
            "INSERT OR REPLACE INTO cash_flow_ticker VALUES (?,?,?,?,?,?)",
            [
                (
                    r["date"], (r.get("ticker") or "").upper(), r.get("content") or "",
                    r.get("type") or "", _to_float(r.get("price")), r.get("percent") or "",
                )
                for r in rows if r.get("date") and r.get("ticker")
            ],
        )
        counts["cash_flow_ticker"] = len(rows)

        rows = _read_csv("getWaveBottomConfirmPairs")
        conn.execute("DELETE FROM wave_bottom_confirm_pairs")
        conn.executemany(
            "INSERT OR REPLACE INTO wave_bottom_confirm_pairs VALUES (?,?,?)",
            [
                (r["confirm_wave_date"], r.get("prepare_bottom_date") or "", _to_int(r.get("reliability")))
                for r in rows if r.get("confirm_wave_date")
            ],
        )
        counts["wave_bottom_confirm_pairs"] = len(rows)

        conn.commit()
    finally:
        conn.close()

    return counts


def _table_has_rows(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None


# ============================================================
# DATE-PREFIX MATCHING (mirrors the live routers' YYYY/YYYY-MM/YYYY-MM-DD contract)
# ============================================================

def _date_matches(row_date: str, date_filter: Optional[str]) -> bool:
    if not date_filter:
        return True
    parts = date_filter.split("-")
    if len(parts) == 1:
        return row_date.startswith(parts[0])
    if len(parts) == 2:
        return row_date.startswith(f"{parts[0]}-{parts[1]}")
    return row_date == date_filter


# ============================================================
# READERS - one per DB-backed operationId, matching the exact param set
# stocktraders-mcp exposes in data/openapi/stock_api.json
# ============================================================

def _read_stock_wave(conn: sqlite3.Connection, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not _table_has_rows(conn, "stock_wave"):
        return None
    date_filter = args.get("date")
    name_filter = str(args.get("name") or "ALL").upper()

    rows = conn.execute(
        "SELECT * FROM stock_wave WHERE UPPER(name) = ? ORDER BY date", (name_filter,)
    ).fetchall()

    # tickerB/tickerS/tickerWB/tickerwWS are per-ticker breakdown lists that
    # can run tens of KB per day - fine for a single exact day, but
    # multiplying that across a month/year range blows past model context
    # limits (hit in production: 2 month-level calls alone produced 160k+
    # tokens against GPT-4o's 128k cap). The tool guide (core/knowledge.py)
    # never asks for these fields anyway - only buy/sell/waitbuy/waitsell/
    # total/reliability - so they're only included for a single exact-date
    # lookup (YYYY-MM-DD), never for a month/year range.
    include_ticker_breakdown = bool(date_filter) and len(date_filter.split("-")) == 3

    wave_datas = []
    for row in rows:
        if not _date_matches(row["date"], date_filter):
            continue
        entry = {
            "date": row["date"],
            "buy": row["buy"], "sell": row["sell"],
            "waitbuy": row["waitbuy"], "waitsell": row["waitsell"],
            "total": row["total"], "reliability": row["reliability"],
        }
        if include_ticker_breakdown:
            entry["tickerB"] = _safe_literal(row["tickerB"])
            entry["tickerS"] = _safe_literal(row["tickerS"])
            entry["tickerWB"] = _safe_literal(row["tickerWB"])
            entry["tickerwWS"] = _safe_literal(row["tickerwWS"])
        wave_datas.append(entry)

    return {"name": name_filter, "waveDatas": wave_datas}


def _safe_literal(value: Any) -> Any:
    if not value:
        return []
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return []


def _read_stock_signal(conn: sqlite3.Connection, args: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    if not _table_has_rows(conn, "stock_signal"):
        return None
    ticker_filter = str(args.get("ticker") or "").upper().strip()
    date_filter = args.get("date")
    year_filter = args.get("year")
    trade_filter = args.get("trade")

    query = "SELECT * FROM stock_signal"
    params: List[Any] = []
    if ticker_filter:
        query += " WHERE ticker = ?"
        params.append(ticker_filter)
    query += " ORDER BY ticker, date"

    rows = conn.execute(query, params).fetchall()

    by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if date_filter and not _date_matches(row["date"], date_filter):
            continue
        if year_filter and not row["date"].startswith(str(year_filter)):
            continue
        if trade_filter is not None and row["trade"] != int(trade_filter):
            continue
        by_ticker.setdefault(row["ticker"], []).append({
            "date": row["date"], "smdt": row["smdt"], "price": row["price"],
            "percent": row["percent"], "ave": row["ave"], "hold": row["hold"],
            "trade": row["trade"],
        })

    return [{"ticker": ticker, "signalDatas": datas} for ticker, datas in by_ticker.items()]


def _read_smdt_family(
    conn: sqlite3.Connection, table: str, args: Dict[str, Any]
) -> Optional[List[Dict[str, Any]]]:
    if not _table_has_rows(conn, table):
        return None

    key_value = str(args.get("keyValue") or "").strip().casefold()
    key_name = str(args.get("keyName") or "").strip().casefold()
    path_filter = str(args.get("path") or "").strip().casefold()
    date_filter = args.get("date")

    query = f"SELECT * FROM {table}"
    params: List[Any] = []
    where = []
    if key_value:
        where.append("LOWER(keyValue) = ?")
        params.append(key_value)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY keyValue, date"

    rows = conn.execute(query, params).fetchall()

    by_key: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        if key_name and key_name not in str(row["keyName"] or "").casefold():
            continue
        if path_filter and path_filter not in str(row["keyValue"] or "").casefold():
            continue
        if not _date_matches(row["date"], date_filter):
            continue
        group_key = (row["keyName"], row["keyValue"])
        entry = by_key.setdefault(group_key, {
            "keyName": row["keyName"], "keyValue": row["keyValue"], "smdts": [],
        })
        entry["smdts"].append({"date": row["date"], "smdt": row["smdt"]})

    return list(by_key.values())


def _read_cash_flow_branch(conn: sqlite3.Connection, args: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    if not _table_has_rows(conn, "cash_flow_branch"):
        return None
    date_filter = args.get("date")
    name_filter = str(args.get("name") or "").casefold()
    path_filter = str(args.get("path") or "").casefold()

    rows = conn.execute("SELECT * FROM cash_flow_branch ORDER BY date").fetchall()

    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not _date_matches(row["date"], date_filter):
            continue
        if name_filter and name_filter not in str(row["name"] or "").casefold():
            continue
        if path_filter and path_filter not in str(row["path"] or "").casefold():
            continue
        by_date.setdefault(row["date"], []).append({
            "name": row["name"], "path": row["path"], "content": row["content"],
        })

    return [{"date": d, "cashFlowBranchDatas": items} for d, items in by_date.items()]


def _read_cash_flow_ticker(conn: sqlite3.Connection, args: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    if not _table_has_rows(conn, "cash_flow_ticker"):
        return None
    ticker_filter = str(args.get("ticker") or "").upper().strip()
    date_filter = args.get("date")
    exclude_content = args.get("exclude_content")

    query = "SELECT * FROM cash_flow_ticker"
    params: List[Any] = []
    if ticker_filter:
        query += " WHERE ticker = ?"
        params.append(ticker_filter)
    query += " ORDER BY date"

    rows = conn.execute(query, params).fetchall()

    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if date_filter and row["date"] != date_filter:
            continue
        if exclude_content and row["content"] == exclude_content:
            continue
        by_date.setdefault(row["date"], []).append({
            "ticker": row["ticker"], "content": row["content"], "type": row["type"],
            "price": row["price"], "percent": row["percent"],
        })

    return [{"date": d, "cashTickerDatas": items} for d, items in by_date.items()]


def _read_wave_bottom_confirm_pairs(conn: sqlite3.Connection, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not _table_has_rows(conn, "wave_bottom_confirm_pairs"):
        return None
    date_from = args.get("dateFrom")
    date_to = args.get("dateTo")

    rows = conn.execute(
        "SELECT * FROM wave_bottom_confirm_pairs ORDER BY confirm_wave_date"
    ).fetchall()

    pairs = []
    for row in rows:
        d = row["confirm_wave_date"]
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        pairs.append({
            "prepare_bottom_date": row["prepare_bottom_date"],
            "confirm_wave_date": d,
            "reliability": row["reliability"],
        })

    return {"dateFrom": date_from, "dateTo": date_to, "count": len(pairs), "pairs": pairs}


_TABLE_BY_OPERATION = {
    "getSMDTTicker": "smdt_ticker",
    "getSMDTBranch": "smdt_branch",
    "getSMDTTickerCross": "smdt_ticker_cross",
    "getSMDTBranchCross": "smdt_branch_cross",
}


def read_from_db(operation_id: str, args: Dict[str, Any]) -> Any:
    """Returns None if this operation's table has never been synced (caller
    should fall back to a live call) - never raises on a plain 'no rows
    matched this filter' case, which just returns an empty list/dict."""
    if operation_id not in DB_BACKED_OPERATIONS:
        return None
    if not DB_PATH.exists():
        return None

    conn = _connect()
    try:
        if operation_id == "getStockWave":
            return _read_stock_wave(conn, args)
        if operation_id == "getStockSignal":
            return _read_stock_signal(conn, args)
        if operation_id in _TABLE_BY_OPERATION:
            return _read_smdt_family(conn, _TABLE_BY_OPERATION[operation_id], args)
        if operation_id == "getCashFlowBranch":
            return _read_cash_flow_branch(conn, args)
        if operation_id == "getCashFlowTicker":
            return _read_cash_flow_ticker(conn, args)
        if operation_id == "getWaveBottomConfirmPairs":
            return _read_wave_bottom_confirm_pairs(conn, args)
        return None
    finally:
        conn.close()


if __name__ == "__main__":
    counts = load_csv_into_db()
    for table, n in counts.items():
        print(f"[local_db] {table}: {n:,} rows from CSV")
    print(f"[local_db] DB written to {DB_PATH}")
