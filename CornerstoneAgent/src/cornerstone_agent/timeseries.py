"""SQLite 时序库（A1 长周期采集）。"""

from __future__ import annotations

import csv
import io
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from .metrics import MetricPoint


SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ts_epoch REAL NOT NULL,
    instrument_id TEXT NOT NULL,
    lab_id TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    point_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_samples_inst_ts ON samples (instrument_id, ts_epoch);

CREATE TABLE IF NOT EXISTS points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL,
    ts_epoch REAL NOT NULL,
    instrument_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    value_num REAL,
    value_bool INTEGER,
    value_text TEXT,
    unit TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (sample_id) REFERENCES samples(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_points_inst_metric_ts
    ON points (instrument_id, metric, ts_epoch);
"""


class TimeseriesStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._conn:
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        cols = {str(r[1]) for r in self._conn.execute("PRAGMA table_info(samples)")}
        if "job_id" not in cols:
            self._conn.execute(
                "ALTER TABLE samples ADD COLUMN job_id TEXT NOT NULL DEFAULT ''"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_job_ts ON samples (job_id, ts_epoch)"
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert_sample(
        self,
        *,
        instrument_id: str,
        lab_id: str = "",
        agent_id: str = "",
        ok: bool,
        error: str = "",
        duration_ms: int = 0,
        points: Sequence[MetricPoint] | None = None,
        ts_epoch: float | None = None,
        ts: str | None = None,
        job_id: str = "",
    ) -> int:
        epoch = float(ts_epoch if ts_epoch is not None else time.time())
        iso = ts or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
        pts = list(points or [])
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO samples (
                    ts, ts_epoch, instrument_id, lab_id, agent_id, job_id,
                    ok, error, duration_ms, point_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    iso,
                    epoch,
                    instrument_id,
                    lab_id or "",
                    agent_id or "",
                    job_id or "",
                    1 if ok else 0,
                    error or "",
                    int(duration_ms),
                    len(pts),
                ),
            )
            sample_id = int(cur.lastrowid)
            if pts:
                cur.executemany(
                    """
                    INSERT INTO points (
                        sample_id, ts_epoch, instrument_id, metric,
                        value_num, value_bool, value_text, unit
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            sample_id,
                            epoch,
                            instrument_id,
                            p.metric,
                            p.value_num,
                            None if p.value_bool is None else (1 if p.value_bool else 0),
                            p.value_text,
                            p.unit or "",
                        )
                        for p in pts
                    ],
                )
            self._conn.commit()
            return sample_id

    def prune(self, retention_days: int, *, job_id: str | None = None) -> int:
        days = max(1, int(retention_days))
        cutoff = time.time() - days * 86400.0
        with self._lock:
            cur = self._conn.cursor()
            if job_id is None:
                cur.execute("SELECT id FROM samples WHERE ts_epoch < ?", (cutoff,))
            else:
                cur.execute(
                    "SELECT id FROM samples WHERE job_id = ? AND ts_epoch < ?",
                    (job_id, cutoff),
                )
            ids = [int(r[0]) for r in cur.fetchall()]
            if not ids:
                return 0
            qmarks = ",".join("?" * len(ids))
            cur.execute(f"DELETE FROM points WHERE sample_id IN ({qmarks})", ids)
            cur.execute(f"DELETE FROM samples WHERE id IN ({qmarks})", ids)
            self._conn.commit()
            return len(ids)

    def prune_jobs(self, jobs: Sequence[tuple[str, int]], *, orphan_days: int = 30) -> int:
        total = 0
        seen: set[str] = set()
        for job_id, days in jobs:
            jid = str(job_id or "").strip()
            if not jid or jid in seen:
                continue
            seen.add(jid)
            total += self.prune(days, job_id=jid)
        total += self.prune(orphan_days, job_id="")
        return total

    def latest_sample(self, instrument_id: str, *, job_id: str | None = None) -> Optional[dict[str, Any]]:
        sql = """
            SELECT * FROM samples
            WHERE instrument_id = ?
        """
        args: list[Any] = [instrument_id]
        if job_id:
            sql += " AND job_id = ?"
            args.append(job_id)
        sql += " ORDER BY ts_epoch DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def list_samples(
        self,
        instrument_id: str,
        *,
        hours: float = 24.0,
        limit: int = 200,
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        t_from = time.time() - max(0.1, float(hours)) * 3600.0
        lim = max(1, min(int(limit), 2000))
        sql = """
            SELECT id, ts, ts_epoch, ok, error, duration_ms, point_count, job_id
            FROM samples
            WHERE instrument_id = ? AND ts_epoch >= ?
        """
        args: list[Any] = [instrument_id, t_from]
        if job_id:
            sql += " AND job_id = ?"
            args.append(job_id)
        sql += " ORDER BY ts_epoch DESC LIMIT ?"
        args.append(lim)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def list_metrics(
        self,
        instrument_id: str,
        *,
        hours: float = 24.0,
        job_id: str | None = None,
    ) -> list[str]:
        t_from = time.time() - max(0.1, float(hours)) * 3600.0
        sql = """
            SELECT DISTINCT p.metric FROM points p
            JOIN samples s ON s.id = p.sample_id
            WHERE p.instrument_id = ? AND p.ts_epoch >= ?
        """
        args: list[Any] = [instrument_id, t_from]
        if job_id:
            sql += " AND s.job_id = ?"
            args.append(job_id)
        sql += " ORDER BY p.metric"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [str(r[0]) for r in rows]

    def query_series(
        self,
        instrument_id: str,
        metric: str,
        *,
        hours: float = 24.0,
        limit: int = 2000,
        job_id: str | None = None,
    ) -> list[dict[str, Any]]:
        t_from = time.time() - max(0.1, float(hours)) * 3600.0
        lim = max(1, min(int(limit), 10000))
        sql = """
            SELECT p.ts_epoch, s.ts, p.value_num, p.value_bool, p.value_text, p.unit
            FROM points p
            JOIN samples s ON s.id = p.sample_id
            WHERE p.instrument_id = ? AND p.metric = ? AND p.ts_epoch >= ?
        """
        args: list[Any] = [instrument_id, metric, t_from]
        if job_id:
            sql += " AND s.job_id = ?"
            args.append(job_id)
        sql += " ORDER BY p.ts_epoch ASC LIMIT ?"
        args.append(lim)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            val: Any = r["value_num"]
            if val is None and r["value_bool"] is not None:
                val = bool(r["value_bool"])
            elif val is None:
                val = r["value_text"]
            out.append(
                {
                    "ts": r["ts"],
                    "ts_epoch": r["ts_epoch"],
                    "value": val,
                    "unit": r["unit"] or "",
                }
            )
        return out

    def sample_values(self, sample_id: int) -> dict[str, Any]:
        sid = int(sample_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM samples WHERE id = ?",
                (sid,),
            ).fetchone()
            if not row:
                return {"sample": None, "values": []}
            pts = self._conn.execute(
                """
                SELECT metric, value_num, value_bool, value_text, unit
                FROM points WHERE sample_id = ? ORDER BY metric
                """,
                (sid,),
            ).fetchall()
        sample = dict(row)
        values = []
        for r in pts:
            val: Any = r["value_num"]
            if val is None and r["value_bool"] is not None:
                val = bool(r["value_bool"])
            elif val is None:
                val = r["value_text"]
            values.append({"metric": r["metric"], "value": val, "unit": r["unit"] or ""})
        return {"sample": sample, "values": values}

    def latest_values(self, instrument_id: str, *, job_id: str | None = None) -> dict[str, Any]:
        sample = self.latest_sample(instrument_id, job_id=job_id)
        if not sample:
            return {"sample": None, "values": []}
        return self.sample_values(int(sample["id"]))

    def sample_stats(
        self,
        instrument_id: str | None = None,
        *,
        hours: float = 24.0,
        job_id: str | None = None,
    ) -> dict[str, Any]:
        t_from = time.time() - max(0.1, float(hours)) * 3600.0
        sql = "SELECT COUNT(*) AS n, SUM(ok) AS ok_n FROM samples WHERE ts_epoch >= ?"
        args: list[Any] = [t_from]
        if instrument_id:
            sql += " AND instrument_id = ?"
            args.append(instrument_id)
        if job_id:
            sql += " AND job_id = ?"
            args.append(job_id)
        with self._lock:
            row = self._conn.execute(sql, args).fetchone()
        n = int(row["n"] or 0) if row else 0
        ok_n = int(row["ok_n"] or 0) if row else 0
        return {"samples": n, "ok": ok_n, "fail": max(0, n - ok_n), "hours": hours}

    def export_csv(
        self,
        instrument_id: str,
        *,
        hours: float = 24.0,
        metric: str | None = None,
        job_id: str | None = None,
    ) -> str:
        t_from = time.time() - max(0.1, float(hours)) * 3600.0
        sql = """
            SELECT s.ts, p.instrument_id, s.job_id, p.metric, p.value_num, p.value_bool, p.value_text, p.unit
            FROM points p
            JOIN samples s ON s.id = p.sample_id
            WHERE p.instrument_id = ? AND p.ts_epoch >= ?
        """
        args: list[Any] = [instrument_id, t_from]
        if metric:
            sql += " AND p.metric = ?"
            args.append(metric)
        if job_id:
            sql += " AND s.job_id = ?"
            args.append(job_id)
        sql += " ORDER BY p.ts_epoch ASC, p.metric ASC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ts", "instrument_id", "job_id", "metric", "value_num", "value_bool", "value_text", "unit"])
        for r in rows:
            w.writerow(
                [
                    r["ts"],
                    r["instrument_id"],
                    r["job_id"] or "",
                    r["metric"],
                    "" if r["value_num"] is None else r["value_num"],
                    "" if r["value_bool"] is None else r["value_bool"],
                    r["value_text"] or "",
                    r["unit"] or "",
                ]
            )
        return buf.getvalue()
