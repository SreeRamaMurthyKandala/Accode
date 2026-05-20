"""Accode web frontend — a chat UI over the agentic loop.

The agent loop is synchronous and runs in a background thread per session.
It emits events into a queue; an SSE endpoint streams them to the browser.
Permission prompts travel the other way: the loop thread blocks on a queue
until the browser POSTs a decision.

Run:
    python frontend/server.py --config config.yaml --cwd .
Then open the printed http://127.0.0.1:8730 URL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import queue
import sys
import threading
import uuid
from pathlib import Path

# Allow `python frontend/server.py` to find the sibling `accode` package: when
# a script is run directly only its own directory is on sys.path, not the repo
# root one level up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from rich.console import Console

from accode.agent.events import QueueSink
from accode.agent.llm import AgentLLM
from accode.agent.loop import run_agent
from accode.agent.permissions import PermissionGate
from accode.config import load_config
from accode.context import Context

_STATIC = Path(__file__).parent / "static"
_PERMISSION_TIMEOUT_S = 600   # if the browser never answers, deny after 10 min

# populated by main() before uvicorn starts
SETTINGS: dict = {"config_path": None, "cwd": str(Path.cwd())}
SESSIONS: dict[str, "Session"] = {}

app = FastAPI(title="Accode")


class Session:
    """One chat session: its own LLM client, history, event queue and gate."""

    def __init__(self) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.cfg = load_config(SETTINGS["config_path"])
        self.cwd = Path(SETTINGS["cwd"]).resolve()
        self.ctx = Context(cfg=self.cfg, cwd=self.cwd, console=Console())
        self.llm = AgentLLM(self.cfg)            # raises ValueError if no API key
        self.events: queue.Queue = queue.Queue()
        self.sink = QueueSink(self.events)
        self.messages: list = []
        self.busy = False
        self.cancel_requested = False
        self._pending: dict[str, queue.Queue] = {}
        self.gate = PermissionGate(self.cfg, auto_approve=False, prompter=self._prompt)

    # runs on the loop thread; blocks until the browser answers
    def _prompt(self, tool, label: str) -> str:
        pid = uuid.uuid4().hex[:8]
        answer_q: queue.Queue = queue.Queue()
        self._pending[pid] = answer_q
        self.sink.emit("permission_request", id=pid, tool=tool.name, label=label)
        try:
            decision = answer_q.get(timeout=_PERMISSION_TIMEOUT_S)
        except queue.Empty:
            decision = "deny"
        self._pending.pop(pid, None)
        self.sink.emit("permission_resolved", id=pid, tool=tool.name, decision=decision)
        return decision

    def answer_permission(self, pid: str, decision: str) -> bool:
        q = self._pending.get(pid)
        if q is None:
            return False
        q.put(decision)
        return True

    def send(self, text: str) -> None:
        self.busy = True
        self.cancel_requested = False

        def _work() -> None:
            try:
                self.messages = run_agent(
                    text, self.ctx, self.llm, self.gate, self.sink,
                    messages=self.messages,
                    cancel_check=lambda: self.cancel_requested,
                )
            except Exception as exc:   # surface any loop-level error to the UI
                self.sink.emit("error", text=repr(exc))
            finally:
                self.busy = False
                self.sink.emit("turn_end")

        threading.Thread(target=_work, daemon=True).start()


def _get(sid: str) -> Session:
    session = SESSIONS.get(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session")
    return session


class MessageIn(BaseModel):
    text: str


class PermissionIn(BaseModel):
    id: str
    decision: str


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "sessions": len(SESSIONS)}


@app.post("/api/sessions")
async def create_session() -> dict:
    try:
        session = Session()
    except (ValueError, FileNotFoundError) as exc:   # missing API key / config
        raise HTTPException(status_code=400, detail=str(exc))
    SESSIONS[session.id] = session
    return {"session_id": session.id, "cwd": str(session.cwd), "model": session.llm.model}


@app.post("/api/sessions/{sid}/message")
async def post_message(sid: str, body: MessageIn) -> dict:
    session = _get(sid)
    if session.busy:
        raise HTTPException(status_code=409, detail="Agent is busy")
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Empty message")
    session.send(body.text.strip())
    return {"ok": True}


@app.post("/api/sessions/{sid}/permission")
async def post_permission(sid: str, body: PermissionIn) -> dict:
    session = _get(sid)
    decision = body.decision if body.decision in ("once", "always", "deny") else "deny"
    return {"ok": session.answer_permission(body.id, decision)}


@app.post("/api/sessions/{sid}/cancel")
async def post_cancel(sid: str) -> dict:
    _get(sid).cancel_requested = True
    return {"ok": True}


@app.get("/api/sessions/{sid}/stream")
async def stream(sid: str) -> StreamingResponse:
    session = _get(sid)

    async def gen():
        yield _sse({"kind": "connected", "session": sid})
        idle = 0
        while True:
            try:
                event = session.events.get_nowait()
                yield _sse(event)
                idle = 0
            except queue.Empty:
                await asyncio.sleep(0.12)
                idle += 1
                if idle >= 100:           # ~12s keepalive comment
                    idle = 0
                    yield ": keepalive\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


def main() -> None:
    parser = argparse.ArgumentParser(description="Accode web frontend")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--cwd", default=str(Path.cwd()), help="Agent working directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8730)
    args = parser.parse_args()
    SETTINGS["config_path"] = args.config
    SETTINGS["cwd"] = str(Path(args.cwd).resolve())

    import uvicorn

    print(f"Accode frontend -> http://{args.host}:{args.port}   (cwd: {SETTINGS['cwd']})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
