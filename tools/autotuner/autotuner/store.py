"""SQLite metadata plus immutable, checksummed original artifacts."""
import gzip
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "station.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS observations (session_id TEXT, timestamp REAL, value TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS observations_session ON observations(session_id, timestamp);
          CREATE TABLE IF NOT EXISTS markers (session_id TEXT, timestamp REAL, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS reference_tracks (session_id TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.active = None
        self.stream = None
        for session in self.sessions():
            if session["status"] == "recording":
                saved = self.observations(session["id"])
                session.update(status="interrupted", ended_at=saved[-1]["received_at"] if saved else session["started_at"], recovery_note="Az alkalmazás a lezárás előtt leállt. A SQLite-megfigyelések megmaradtak; a tömörített stream hiányos lehet. A végidő az utolsó megőrzött megfigyelés.")
                self.save(session)

    def save(self, session):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (session["id"], json.dumps(session, allow_nan=False)))

    def sessions(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT value FROM sessions ORDER BY rowid DESC")]

    def update(self, sid, changes):
        with self.lock:
            session = self.get(sid)
            session.update(changes)
            self.save(session)
            return session

    def get(self, sid):
        with self.lock:
            row = self.db.execute("SELECT value FROM sessions WHERE id=?", (sid,)).fetchone()
            if not row:
                raise KeyError(sid)
            return json.loads(row[0])

    def start(self, name, block, connection):
        with self.lock:
            if self.active:
                raise ValueError("Már fut felvétel. Előbb állítsd le.")
            sid = str(uuid.uuid4())
            folder = self.root / sid
            folder.mkdir(mode=0o700)
            self.stream = gzip.open(folder / "observations.jsonl.gz", "xt", encoding="utf-8")
            started_at, mono = time.time(), time.monotonic_ns()
            session = {"id": sid, "name": name, "initial_block": block, "started_at": started_at,
                       "schema_version": 1, "station_version": "0.1.0",
                       "capture_kind": "ruview_public_api", "ruview_build": None,
                       "clock_anchor": {"utc": started_at, "monotonic_ns": mono},
                       "ended_at": None, "status": "recording", "connection": connection,
                       "alignment": {"offset_s": 0, "uncertainty_s": None}, "raw_capture": None}
            self.save(session)
            self.active = sid
            return session

    def append(self, observation):
        with self.lock:
            if self.active:
                observation = dict(observation)
                anchor = self.get(self.active).get("clock_anchor")
                mono = observation.get("received_monotonic_ns")
                if anchor and mono is not None:
                    error = observation["received_at"] - (anchor["utc"]+(mono-anchor["monotonic_ns"])/1e9)
                    observation["clock_error_s"] = error
                    observation["clock_valid"] = abs(error) <= .1
                    if abs(error) > .1:
                        observation.update(hr_bpm=None, br_rpm=None, status="unavailable",
                                           reasons=[*observation.get("reasons", []), "host_clock_discontinuity"])
                encoded = json.dumps(observation, separators=(",", ":"), allow_nan=False)
                with self.db:
                    self.db.execute("INSERT INTO observations VALUES (?, ?, ?)", (self.active, observation["received_at"], encoded))
                self.stream.write(encoded + "\n")
                self.stream.flush()

    def stop(self):
        with self.lock:
            if not self.active:
                raise ValueError("Nincs futó felvétel.")
            session = self.get(self.active)
            self.stream.close()
            session.update(status="completed", ended_at=time.time())
            path = self.root / self.active / "observations.jsonl.gz"
            session["observations_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            self.save(session)
            self.active, self.stream = None, None
            return session

    def observations(self, sid):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT value FROM observations WHERE session_id=? ORDER BY timestamp", (sid,))]

    def marker(self, sid, kind, note):
        with self.lock:
            if sid != self.active:
                raise ValueError("Esemény csak futó felvételen jelölhető.")
            marker = {"timestamp": time.time(), "kind": kind, "note": note}
            with self.db:
                self.db.execute("INSERT INTO markers VALUES (?, ?, ?)", (sid, marker["timestamp"], json.dumps(marker)))
            return marker

    def markers(self, sid):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT value FROM markers WHERE session_id=? ORDER BY timestamp", (sid,))]

    def artifact(self, sid, name, payload):
        self.get(sid)
        path = self.root / sid / name
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o600)
        return hashlib.sha256(payload).hexdigest()

    def reference(self, sid):
        with self.lock:
            row = self.db.execute("SELECT value FROM reference_tracks WHERE session_id=?", (sid,)).fetchone()
            return json.loads(row[0]) if row else None

    def save_reference(self, sid, reference):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO reference_tracks VALUES (?, ?)", (sid, json.dumps(reference, allow_nan=False)))

    def close(self):
        if self.active:
            self.stop()
        self.db.close()
