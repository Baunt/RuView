import asyncio
import contextlib
import csv
import io
import ipaddress
import json
import os
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import fitdecode
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .raw import inspect_capture
from .reference import read_fit
from .report import build_report
from .ruview import poll
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
BLOCKS = {"seated", "supine", "motion", "empty", "unmarked"}


class Connection(BaseModel):
    url: str = "http://127.0.0.1:3080"
    source_address: str | None = None
    node_id: int = Field(default=1, ge=0, le=255)
    token: str | None = Field(default=None, max_length=8192)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Csak a szerver alapcímét add meg, például http://127.0.0.1:3080")
        _ = parsed.port
        return value.rstrip("/")

    @field_validator("source_address")
    @classmethod
    def valid_source(cls, value):
        return str(ipaddress.ip_address(value)) if value else None

    @field_validator("token")
    @classmethod
    def valid_token(cls, value):
        if value and any(ord(c) < 33 or ord(c) > 126 for c in value):
            raise ValueError("A token nem tartalmazhat szóközt vagy vezérlőkaraktert.")
        return value


class Start(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    initial_block: str = "seated"


class Marker(BaseModel):
    kind: str
    note: str = Field(default="", max_length=500)


class Alignment(BaseModel):
    offset_s: float = Field(default=0, ge=-86400, le=86400, allow_inf_nan=False)
    uncertainty_s: float | None = Field(default=None, ge=0, le=3600, allow_inf_nan=False)


class Station:
    def __init__(self, root, polling):
        self.store = Store(root)
        self.polling = polling
        self.latest = None
        self.generation = 0
        config_path = self.store.root / "connection.json"
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
        self.connection = Connection(**config)
        self.token = os.environ.get("RUVIEW_API_TOKEN", "")
        token_path = os.environ.get("RUVIEW_TOKEN_FILE")
        if token_path:
            path = Path(token_path)
            if path.stat().st_mode & 0o077:
                raise ValueError("RUVIEW_TOKEN_FILE jogosultsága legyen 0600.")
            self.token = path.read_text().strip()

    def public_connection(self):
        return {**self.connection.model_dump(exclude={"token"}), "token_set": bool(self.token)}

    async def loop(self):
        while True:
            config, generation = self.connection, self.generation
            try:
                observation = await asyncio.to_thread(poll, config.url, self.token, config.source_address, config.node_id)
                if generation == self.generation:
                    self.latest = observation
                    if self.store.active and observation["request_started_at"] >= self.store.get(self.store.active)["started_at"]:
                        self.store.append(observation)
                        if time.time() - self.store.get(self.store.active)["started_at"] >= 28800:
                            self.store.stop()
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                self.latest = {"received_at": time.time(), "status": "unavailable", "reasons": ["adapter_error"], "hr_bpm": None}
                # Persist failures too, so recording cannot silently erase outage time.
                self.store.append(self.latest)
            await asyncio.sleep(1)


def create_app(data_dir=None, polling=True):
    @asynccontextmanager
    async def lifespan(app):
        app.state.station = Station(data_dir or os.environ.get("AUTOTUNER_DATA", ROOT / "data"), polling)
        task = asyncio.create_task(app.state.station.loop()) if polling else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        app.state.station.store.close()

    app = FastAPI(title="RuView AutoTuner measurement station", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
                return Response("Más eredetű kérés nem engedélyezett.", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        return response

    def station():
        return app.state.station

    def get_session(sid):
        try:
            return station().store.get(str(uuid.UUID(sid)))
        except (ValueError, KeyError):
            raise HTTPException(404, "A felvétel nem található.")

    def report(sid):
        session = get_session(sid)
        if session["status"] == "recording":
            session["ended_at"] = time.time()
        store = station().store
        return build_report(session, store.observations(sid), store.reference(sid), store.markers(sid))

    @app.get("/api/status")
    def status():
        s = station()
        latest = s.latest
        if latest and time.time()-latest["received_at"] > 4:
            latest = {**latest, "hr_bpm": None, "br_rpm": None, "status": "unavailable", "reasons": ["station_poll_stale"]}
        return {"connection": s.public_connection(), "latest": latest, "active_session_id": s.store.active,
                "station_version": "0.1.0", "raw_capture_status": "raw_capture_required"}

    @app.post("/api/connection")
    async def connection(value: Connection):
        s = station()
        if s.store.active:
            raise HTTPException(409, "Felvétel közben nem változtatható a kapcsolat.")
        # null keeps the token, empty string clears it. Never persist the credential.
        if value.token is not None:
            s.token = value.token
        s.connection = value.model_copy(update={"token": None})
        s.generation += 1
        s.latest = None
        path = s.store.root / "connection.json"
        path.write_text(json.dumps(s.connection.model_dump(exclude={"token"})))
        path.chmod(0o600)
        return s.public_connection()

    @app.get("/api/sessions")
    def sessions():
        return station().store.sessions()

    @app.post("/api/sessions/start")
    def start(value: Start):
        if value.initial_block not in BLOCKS:
            raise HTTPException(422, "Ismeretlen mérési blokk.")
        try:
            return station().store.start(value.name.strip(), value.initial_block,
                                         station().connection.model_dump(exclude={"token"}))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/sessions/stop")
    def stop():
        try:
            return station().store.stop()
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/sessions/{sid}/markers")
    def marker(sid: str, value: Marker):
        get_session(sid)
        if value.kind not in BLOCKS | {"sync", "note"}:
            raise HTTPException(422, "Ismeretlen eseménytípus.")
        try:
            return station().store.marker(sid, value.kind, value.note)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/sessions/{sid}/report")
    def session_report(sid: str):
        return report(sid)

    @app.post("/api/sessions/{sid}/alignment")
    def alignment(sid: str, value: Alignment):
        get_session(sid)
        result = {**value.model_dump(), "set_at": time.time(), "method": "manual_fixed"}
        station().store.update(sid, {"alignment": result})
        return result

    async def upload_bytes(file, limit):
        payload = await file.read(limit+1)
        await file.close()
        if len(payload) > limit:
            raise HTTPException(413, "A fájl meghaladja a megengedett méretet.")
        return payload

    @app.post("/api/sessions/{sid}/fit")
    async def fit_import(sid: str, file: UploadFile):
        get_session(sid)
        payload = await upload_bytes(file, 20_000_000)
        try:
            reference = await asyncio.to_thread(read_fit, payload)
        except (fitdecode.FitError, ValueError, TypeError, EOFError, OverflowError):
            raise HTTPException(422, "Nem olvasható érvényes, CRC-ellenőrzött FIT-pulzusadat. Exportáld a Garmin-tevékenység eredeti FIT-fájlját.")
        name = f"reference-{uuid.uuid4()}.fit"
        reference.update(sha256=station().store.artifact(sid, name, payload), artifact=name,
                         original_name=Path(file.filename or "activity.fit").name,
                         source="fit_import", imported_at=time.time())
        station().store.save_reference(sid, reference)
        return {k: v for k, v in reference.items() if k != "points"}

    @app.post("/api/sessions/{sid}/raw")
    async def raw_import(sid: str, file: UploadFile):
        session = get_session(sid)
        if session["status"] == "recording":
            raise HTTPException(409, "Előbb állítsd le a felvételt.")
        payload = await upload_bytes(file, 40_000_000)
        try:
            evidence = await asyncio.to_thread(inspect_capture, payload)
        except (ValueError, OSError, KeyError, TypeError, EOFError):
            raise HTTPException(422, "Hibás vagy lezáratlan ruview-raw-v1 felvétel. Ellenőrizd a collector kimenetét.")
        if session["connection"]["node_id"] not in evidence["node_ids"]:
            raise HTTPException(422, "A nyers felvétel nem tartalmazza a kiválasztott node-ot.")
        # UTC overlap is diagnostic only: two hosts' clock synchronization is unverified.
        evidence["utc_overlap_s"] = max(0, min(session["ended_at"], evidence["last_raw_at"])-max(session["started_at"], evidence["first_raw_at"]))
        if evidence["utc_overlap_s"] <= 0:
            raise HTTPException(422, "A nyers felvétel UTC-időtartománya nem fedi ezt a mérést.")
        name = f"raw-{uuid.uuid4()}.jsonl.gz"
        station().store.artifact(sid, name, payload)
        evidence["artifact"] = name
        station().store.update(sid, {"raw_capture": evidence})
        return evidence

    @app.get("/api/sessions/{sid}/export")
    def export(sid: str):
        session = get_session(sid)
        if session["status"] == "recording":
            raise HTTPException(409, "Exportálás előtt állítsd le a felvételt.")
        result, output = report(sid), io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("session.json", json.dumps(session, indent=2, ensure_ascii=False))
            archive.writestr("report.json", json.dumps(result, indent=2, ensure_ascii=False))
            archive.writestr("observations.jsonl", "\n".join(json.dumps(o, ensure_ascii=False) for o in station().store.observations(sid)))
            ref = station().store.reference(sid)
            if ref:
                archive.writestr("reference.json", json.dumps(ref, ensure_ascii=False))
            table = io.StringIO()
            writer = csv.DictWriter(table, fieldnames=["timestamp", "reference_hr", "ruview_hr", "eligible", "block", "reason"])
            writer.writeheader()
            writer.writerows(result["timeline"])
            archive.writestr("timeline.csv", table.getvalue())
            checksums = {}
            for path in sorted((station().store.root / sid).iterdir()):
                if path.is_file():
                    import hashlib
                    payload = path.read_bytes()
                    checksums[path.name] = hashlib.sha256(payload).hexdigest()
                    archive.writestr("originals/" + path.name, payload)
            archive.writestr("checksums.json", json.dumps(checksums, indent=2))
        return Response(output.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="ruview-{sid}.zip"'})

    dist = ROOT / "web" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def home():
            return FileResponse(dist / "index.html")
    return app


def main():
    import uvicorn
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("AUTOTUNER_PORT", "8787")), access_log=False)


if __name__ == "__main__":
    main()
