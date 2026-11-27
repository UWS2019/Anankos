"""SQLite event log (evidence trail for police / insurance)."""

from __future__ import annotations

import json
import sqlite3
import threading

from .models import Detection, Incident, Packet

SCHEMA = """
CREATE TABLE IF NOT EXISTS packets (
  id INTEGER PRIMARY KEY, t REAL, monitor TEXT, zone TEXT, freq_mhz REAL, rssi_dbfs REAL,
  modulation TEXT, unit_us REAL, length INTEGER, repeats INTEGER, fingerprint TEXT, symbols TEXT);
CREATE INDEX IF NOT EXISTS packets_t ON packets(t);
CREATE TABLE IF NOT EXISTS detections (
  id INTEGER PRIMARY KEY, incident_id INTEGER, t REAL, kind TEXT, severity TEXT,
  monitor TEXT, zone TEXT, message TEXT, details TEXT);
CREATE INDEX IF NOT EXISTS detections_t ON detections(t);
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY, opened REAL, updated REAL, severity TEXT, title TEXT, zone TEXT,
  kinds TEXT, acknowledged_by TEXT, acknowledged_at REAL);
"""


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def packet(self, p: Packet) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO packets(t,monitor,zone,freq_mhz,rssi_dbfs,modulation,unit_us,length,repeats,fingerprint,symbols)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (p.t, p.monitor, p.zone, p.freq_hz / 1e6, p.rssi_dbfs, p.modulation, p.unit_us,
                 p.length, p.repeats, p.fingerprint, ",".join(map(str, p.symbols))))

    def detection(self, d: Detection, incident_id: int | None) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO detections(incident_id,t,kind,severity,monitor,zone,message,details)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (incident_id, d.t, d.kind, d.severity.label(), d.monitor, d.zone, d.message,
                 json.dumps(d.details, default=str)))

    def incident(self, inc: Incident) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO incidents(id,opened,updated,severity,title,zone,kinds,acknowledged_by,acknowledged_at)"
                " VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,"
                " severity=excluded.severity, title=excluded.title, kinds=excluded.kinds,"
                " acknowledged_by=excluded.acknowledged_by, acknowledged_at=excluded.acknowledged_at",
                (inc.id, inc.opened, inc.updated, inc.severity.label(), inc.title, inc.zone,
                 ",".join(sorted(inc.kinds)), inc.acknowledged_by, inc.acknowledged_at))

    def max_incident_id(self) -> int:
        with self.lock:
            row = self.db.execute("SELECT MAX(id) FROM incidents").fetchone()
        return int(row[0] or 0)

    def commit(self) -> None:
        with self.lock:
            self.db.commit()

    def recent_incidents(self, limit: int = 100) -> list[dict]:
        with self.lock:
            cur = self.db.execute(
                "SELECT id,opened,updated,severity,title,zone,kinds,acknowledged_by,acknowledged_at"
                " FROM incidents ORDER BY updated DESC LIMIT ?", (limit,))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def close(self) -> None:
        with self.lock:
            self.db.commit()
            self.db.close()
