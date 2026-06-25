"""BondScribe live transcription backend.

Streams audio → Faster-Whisper STT → WebSocket, served to the desktop app.
Builds on the audio/stt/transcript modules in this package.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from .audio import AudioSource
from .stt import STTPipeline, PRESETS
from .transcript import TranscriptBuffer

load_dotenv()

VERSION = "0.2.4"

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
LOGS_DIR = ROOT / "logs"
LOG_RETAIN_DAYS = 90


# --------------------------------------------------------------------------
# Transcript log writer — appends each final segment to a text file
# --------------------------------------------------------------------------

class TranscriptLogWriter:
    """Writes final transcript segments to a plain-text log file, one per session."""

    def __init__(self, logs_dir: Path = LOGS_DIR) -> None:
        self._dir = logs_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self._path = self._dir / f"{stamp}.txt"
        self._fh = open(self._path, "a", encoding="utf-8")
        self._prune_old()
        print(f"[log] writing transcript log to {self._path}")

    def write(self, seg) -> None:
        ts = datetime.fromtimestamp(seg.t0).strftime("%H:%M:%S")
        self._fh.write(f"[{ts}] {seg.text}\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def _prune_old(self) -> None:
        cutoff = time.time() - LOG_RETAIN_DAYS * 86400
        for f in self._dir.glob("*.txt"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    print(f"[log] pruned old log: {f.name}")
            except Exception:
                pass


class AppState:
    def __init__(self) -> None:
        self.buffer = TranscriptBuffer()
        self.clients: set[WebSocket] = set()
        self.clients_lock = asyncio.Lock()
        self.source: Optional[AudioSource] = None
        self.pipeline: Optional[STTPipeline] = None
        self.worker: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.stop_flag = threading.Event()
        self.log_writer: Optional[TranscriptLogWriter] = None
        # Startup runs in a background thread so the HTTP server can bind
        # immediately and /api/health can report progress (the first-run model
        # download can take several minutes). One of:
        #   "loading-models" | "ready" | "error"
        self.model_status: str = "loading-models"
        self.model_error: Optional[str] = None
        # Live model switching state:
        self.switching: bool = False
        self.switch_error: Optional[str] = None


state = AppState()


def _build_pipeline() -> None:
    """Start audio + load the STT models in the background.

    Model loading (and the first-run download of the Whisper weights) is slow,
    so we do it off the server's startup path. /api/health reports
    "loading-models" until this finishes, then "ready" — or "error" with a
    message if audio/model init fails (e.g. no microphone)."""
    try:
        mode = os.getenv("AUDIO_MODE", "mic")
        device_env = os.getenv("AUDIO_DEVICE_INDEX")
        device_index = int(device_env) if device_env else None

        print(f"[main] starting audio in mode={mode} device={device_index}")
        source = AudioSource(mode=mode, device_index=device_index)
        source.start()
        # Publish the source early so the level meter works while models load.
        state.source = source

        state.pipeline = STTPipeline(source)  # loads VAD + Whisper (may download)
        state.model_status = "ready"
        print("[main] models ready")

        state.worker = threading.Thread(target=_stt_worker, daemon=True, name="stt-worker")
        state.worker.start()
    except Exception as exc:
        state.model_status = "error"
        state.model_error = str(exc)
        print(f"[main] startup failed: {exc}")


def _stt_worker() -> None:
    assert state.pipeline is not None and state.loop is not None
    try:
        for seg in state.pipeline.run():
            if state.stop_flag.is_set():
                break
            kind = getattr(seg, "kind", "final")
            if kind == "final":
                state.buffer.append(seg)
                if state.log_writer:
                    try:
                        state.log_writer.write(seg)
                    except Exception:
                        pass
            msg_type = "interim" if kind == "interim" else "segment"
            payload = json.dumps({"type": msg_type, **TranscriptBuffer.to_dict(seg)})
            asyncio.run_coroutine_threadsafe(_broadcast(payload), state.loop)
    except Exception as exc:
        print(f"[main] STT worker crashed: {exc}")


async def _broadcast(payload: str) -> None:
    async with state.clients_lock:
        dead: list[WebSocket] = []
        for ws in state.clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            state.clients.discard(ws)


async def _status_broadcaster() -> None:
    """Broadcasts {type:status, paused, level} ~10Hz while the app runs."""
    try:
        while not state.stop_flag.is_set():
            await asyncio.sleep(0.1)
            if not state.clients or state.source is None:
                continue
            payload = json.dumps({
                "type": "status",
                "paused": state.source.is_paused(),
                "level": round(state.source.level(), 4),
            })
            await _broadcast(payload)
    except asyncio.CancelledError:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.loop = asyncio.get_running_loop()
    state.log_writer = TranscriptLogWriter()

    # Build audio + models in the background so the HTTP server binds right away
    # and the UI can show first-run "loading model" progress instead of looking
    # frozen until a multi-GB download finishes.
    builder = threading.Thread(target=_build_pipeline, daemon=True, name="model-builder")
    builder.start()
    status_task = asyncio.create_task(_status_broadcaster())

    try:
        yield
    finally:
        state.stop_flag.set()
        status_task.cancel()
        if state.log_writer:
            state.log_writer.close()
        if state.source:
            state.source.stop()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def index():
    content = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})


@app.get("/api/transcript")
async def get_transcript():
    return [TranscriptBuffer.to_dict(s) for s in state.buffer.all()]


@app.get("/api/version")
async def version():
    return {"version": VERSION}


def _preset_list() -> list[dict]:
    return [
        {"key": k, "label": v["label"], "description": v["description"],
         "final": v["final"], "interim": v["interim"]}
        for k, v in PRESETS.items()
    ]


@app.get("/api/model")
async def model_info():
    p = state.pipeline
    presets = _preset_list()
    if not p:
        return {"final": "loading…", "interim": "loading…", "device": "unknown",
                "preset": None, "switching": False, "presets": presets}
    import torch  # type: ignore
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return {
        "final": p.final_model_name,
        "interim": p.interim_model_name,
        "device": device,
        "preset": getattr(p, "preset", None),
        "switching": state.switching,
        "error": state.switch_error,
        "presets": presets,
    }


@app.post("/api/model")
async def set_model(payload: dict = Body(...)):
    preset = (payload or {}).get("preset")
    if preset not in PRESETS:
        return JSONResponse({"ok": False, "error": "unknown preset"}, status_code=400)
    if not state.pipeline:
        return JSONResponse({"ok": False, "error": "pipeline not ready"}, status_code=503)
    if state.switching:
        return {"ok": True, "switching": True}  # one already in flight

    state.switching = True
    state.switch_error = None

    def _do_switch() -> None:
        try:
            state.pipeline.apply_preset(preset)
        except Exception as exc:  # keep old model on failure, surface the error
            state.switch_error = str(exc)
            print(f"[main] model switch to '{preset}' failed: {exc}")
        finally:
            state.switching = False

    threading.Thread(target=_do_switch, daemon=True, name="model-switch").start()
    return {"ok": True, "switching": True}


@app.get("/api/health")
async def health():
    # Models load in a background thread (see _build_pipeline), so the server
    # answers immediately while weights download. The renderer polls this to
    # drive its loading overlay.
    if state.model_status == "error":
        return {"status": "error", "error": state.model_error or "unknown"}
    if state.pipeline is None:
        return {"status": "loading-models"}
    return {"status": "ready"}


class PauseBody(BaseModel):
    paused: bool


@app.post("/api/audio/pause")
async def audio_pause(body: PauseBody):
    if state.source is None:
        return {"ok": False, "paused": False}
    state.source.set_paused(body.paused)
    return {"ok": True, "paused": state.source.is_paused()}


@app.get("/api/audio/status")
async def audio_status():
    if state.source is None:
        return {"paused": False, "level": 0.0}
    return {"paused": state.source.is_paused(), "level": round(state.source.level(), 4)}


@app.get("/api/open-logs")
async def open_logs():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(["explorer", str(LOGS_DIR)])
    return {"ok": True}


@app.websocket("/ws/transcript")
async def ws_transcript(ws: WebSocket):
    await ws.accept()
    async with state.clients_lock:
        state.clients.add(ws)
    try:
        for seg in state.buffer.all():
            await ws.send_text(json.dumps({"type": "segment", **TranscriptBuffer.to_dict(seg)}))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        async with state.clients_lock:
            state.clients.discard(ws)
