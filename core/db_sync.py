"""
Periodic sync job that keeps data/stocktraders_cache.db fresh for the 9
DB-backed base operations (see core/local_db.py). Talks directly to the
live stocktradersai.vn endpoints via plain HTTP - deliberately NOT going
through core/executor.py's call(), so this can never recurse into its own
DB-read path regardless of STOCKTRADERS_DB_MODE.

Write strategy: "INSERT OR REPLACE" only, keyed on each table's existing
primary key (date[+ticker/path]) - never a blanket DELETE before insert.
A day with no rows in a given sync run (weekend, public holiday, or a
transient empty upstream response) simply isn't touched; whatever was
already in the DB for that day stays exactly as it was. A day that DOES
come back gets its row(s) overwritten with the latest values - never
duplicated, never appended as a second row for the same key.

Run once (e.g. for testing, or from a systemd/cron timer instead of the
built-in scheduler):
    python -m core.db_sync --once

Run as a long-lived scheduler (APScheduler, same library and daily-cron
pattern as re-api/db.py):
    python -m core.db_sync
Interval is controlled by STOCKTRADERS_DB_SYNC_MINUTES (default 30).
"""

import argparse
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

import requests

from core.local_db import DB_PATH, _connect, _safe_literal, _to_float, _to_int, init_schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("stocktraders_db_sync")

SERVER_URL = "https://stocktradersai.vn"
REQUEST_TIMEOUT = 180
SYNC_INTERVAL_MINUTES = int(os.getenv("STOCKTRADERS_DB_SYNC_MINUTES", "30"))


def _post(path: str, payload: Dict[str, Any]) -> Any:
    resp = requests.post(f"{SERVER_URL}{path}", json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _sync_stock_wave(conn) -> int:
    """getStockWave requires a date filter (no "give me everything" mode) -
    resync the current year plus the previous one, which covers "recent
    enough to matter" without re-pulling the full multi-year history (that
    came from the one-time CSV import) on every run."""
    written = 0
    this_year = datetime.now().year
    for year in (this_year - 1, this_year):
        try:
            data = _post("/service/data/getStockWave", {"date": str(year)})
        except requests.RequestException as e:
            log.warning("getStockWave %s fetch failed: %s", year, e)
            continue
        wave_datas = data.get("waveDatas", []) if isinstance(data, dict) else []
        name = data.get("name") or "ALL" if isinstance(data, dict) else "ALL"
        for w in wave_datas:
            if not w.get("date"):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO stock_wave VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    w["date"], name,
                    _to_int(w.get("buy")), _to_int(w.get("sell")),
                    _to_int(w.get("waitbuy")), _to_int(w.get("waitsell")),
                    _to_int(w.get("total")), _to_int(w.get("reliability")),
                    str(w.get("tickerB") or []), str(w.get("tickerS") or []),
                    str(w.get("tickerWB") or []), str(w.get("tickerwWS") or []),
                ),
            )
            written += 1
    return written


def _sync_stock_signal(conn) -> int:
    written = 0
    try:
        raw = _post("/service/data/getStockSignal", {})
    except requests.RequestException as e:
        log.warning("getStockSignal fetch failed: %s", e)
        return 0
    for item in raw or []:
        ticker = str(item.get("ticker") or "").upper()
        if not ticker:
            continue
        for s in item.get("signalDatas") or []:
            if not s.get("date"):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO stock_signal VALUES (?,?,?,?,?,?,?,?)",
                (
                    ticker, s["date"],
                    _to_float(s.get("smdt")), _to_float(s.get("price")),
                    _to_float(s.get("percent")), _to_float(s.get("ave")),
                    _to_float(s.get("hold")), _to_int(s.get("trade")),
                ),
            )
            written += 1
    return written


def _sync_smdt_family(conn, path: str, table: str) -> int:
    written = 0
    try:
        raw = _post(f"/service/data/{path}", {})
    except requests.RequestException as e:
        log.warning("%s fetch failed: %s", path, e)
        return 0
    for item in raw or []:
        key_value = item.get("keyValue") or ""
        if not key_value:
            continue
        key_name = item.get("keyName") or ""
        for s in item.get("smdts") or []:
            if not s.get("date"):
                continue
            conn.execute(
                f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?)",
                (key_name, key_value, s["date"], _to_float(s.get("smdt"))),
            )
            written += 1
    return written


def _sync_cash_flow_branch(conn) -> int:
    written = 0
    try:
        raw = _post("/service/data/getCashFlowBranch", {})
    except requests.RequestException as e:
        log.warning("getCashFlowBranch fetch failed: %s", e)
        return 0
    for rec in raw or []:
        rec_date = rec.get("date")
        if not rec_date:
            continue
        for b in rec.get("cashFlowBranchDatas") or []:
            path = b.get("path")
            if not path:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO cash_flow_branch VALUES (?,?,?,?)",
                (rec_date, b.get("name") or "", path, b.get("content") or ""),
            )
            written += 1
    return written


def _sync_cash_flow_ticker(conn) -> int:
    written = 0
    try:
        raw = _post("/service/data/getCashFlowTicker", {})
    except requests.RequestException as e:
        log.warning("getCashFlowTicker fetch failed: %s", e)
        return 0
    for rec in raw or []:
        rec_date = rec.get("date")
        if not rec_date:
            continue
        for t in rec.get("cashTickerDatas") or []:
            ticker = str(t.get("ticker") or "").upper()
            if not ticker:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO cash_flow_ticker VALUES (?,?,?,?,?,?)",
                (
                    rec_date, ticker, t.get("content") or "", t.get("type") or "",
                    _to_float(t.get("price")), t.get("percent") or "",
                ),
            )
            written += 1
    return written


def _sync_wave_bottom_confirm_pairs(conn) -> int:
    written = 0
    try:
        raw = _post("/service/data/getWaveBottomConfirmPairs", {})
    except requests.RequestException as e:
        log.warning("getWaveBottomConfirmPairs fetch failed: %s", e)
        return 0
    for p in (raw or {}).get("pairs", []):
        confirm_date = p.get("confirm_wave_date")
        if not confirm_date:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO wave_bottom_confirm_pairs VALUES (?,?,?)",
            (confirm_date, p.get("prepare_bottom_date") or "", _to_int(p.get("reliability"))),
        )
        written += 1
    return written


def sync_once() -> Dict[str, int]:
    conn = _connect()
    counts: Dict[str, int] = {}
    try:
        init_schema(conn)
        counts["stock_wave"] = _sync_stock_wave(conn)
        counts["stock_signal"] = _sync_stock_signal(conn)
        counts["smdt_ticker"] = _sync_smdt_family(conn, "getSMDTTicker", "smdt_ticker")
        counts["smdt_branch"] = _sync_smdt_family(conn, "getSMDTBranch", "smdt_branch")
        counts["smdt_ticker_cross"] = _sync_smdt_family(conn, "getSMDTTickerCross", "smdt_ticker_cross")
        counts["smdt_branch_cross"] = _sync_smdt_family(conn, "getSMDTBranchCross", "smdt_branch_cross")
        counts["cash_flow_branch"] = _sync_cash_flow_branch(conn)
        counts["cash_flow_ticker"] = _sync_cash_flow_ticker(conn)
        counts["wave_bottom_confirm_pairs"] = _sync_wave_bottom_confirm_pairs(conn)
        conn.commit()
    finally:
        conn.close()
    return counts


def run_scheduler() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    def job():
        log.info("Sync starting...")
        counts = sync_once()
        for table, n in counts.items():
            log.info("  %s: %s rows upserted", table, f"{n:,}")
        log.info("Sync finished.")

    job()  # run once immediately on startup, same as re-api/db.py
    scheduler = BlockingScheduler()
    scheduler.add_job(job, trigger="interval", minutes=SYNC_INTERVAL_MINUTES)
    log.info("Scheduler started: every %s minutes", SYNC_INTERVAL_MINUTES)
    scheduler.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync the 9 DB-backed base operations from the live API.")
    parser.add_argument("--once", action="store_true", help="Run a single sync pass and exit.")
    args = parser.parse_args()

    log.info("DB path: %s", DB_PATH)
    if args.once:
        counts = sync_once()
        for table, n in counts.items():
            log.info("%s: %s rows upserted", table, f"{n:,}")
        return

    run_scheduler()


if __name__ == "__main__":
    main()
