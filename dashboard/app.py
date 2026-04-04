"""Drifter dashboard — FastAPI + htmx + Jinja2 + SSE."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import sqlite3
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import tomllib
import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

# ── Paths ─────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "drifter.db"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ── Config ────────────────────────────────────────────────────────────────

def load_config() -> dict[str, Any]:
    path = ROOT / "drifter.toml"
    if path.exists():
        return tomllib.loads(path.read_text(encoding="utf-8"))
    return {}


def get_dashboard_config() -> dict[str, Any]:
    return load_config().get("dashboard", {})


# ── Auth ──────────────────────────────────────────────────────────────────

_sessions: dict[str, bool] = {}


def check_password(password: str) -> bool:
    expected = get_dashboard_config().get("password", "")
    if not expected:
        return True  # no password configured = open
    return hmac.compare_digest(password, expected)


def get_session(request: Request) -> str | None:
    token = request.cookies.get("drifter_session")
    if token and token in _sessions:
        return token
    return None


def require_auth(request: Request) -> None:
    cfg = get_dashboard_config()
    if not cfg.get("password"):
        return  # no password = no auth required
    if not get_session(request):
        raise HTTPException(status_code=401)


# ── Database (read-only) ─────────────────────────────────────────────────

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def query(sql: str, params: tuple = ()) -> list[dict]:
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def get_seq() -> int:
    row = query_one("SELECT value FROM seq_counter WHERE id = 1")
    return row["value"] if row else 0


# ── CLI wrapper (writes) ─────────────────────────────────────────────────

def drifter_bin() -> str:
    for candidate in [
        ROOT / "rust" / "target" / "release" / "drifter",
        ROOT / "rust" / "target" / "debug" / "drifter",
    ]:
        if candidate.exists():
            return str(candidate)
    return "drifter"


def run_cli(*args: str) -> str:
    cmd = [drifter_bin(), "--db", str(DB_PATH), *args]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)
    return result.stdout.strip()


# ── App ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=lifespan)


# ── Auth routes ───────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if not check_password(password):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Wrong password"}, status_code=401
        )
    token = secrets.token_urlsafe(32)
    _sessions[token] = True
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("drifter_session", token, httponly=True, max_age=86400 * 7)
    return response


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("drifter_session")
    if token:
        _sessions.pop(token, None)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("drifter_session")
    return response


# ── Auth middleware ────────────────────────────────────────────────────────

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    public = {"/login", "/favicon.ico"}
    if request.url.path not in public and get_dashboard_config().get("password"):
        if not get_session(request):
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)


# ── Pages ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    agents = query(
        "SELECT name, model, status, hypothesis, immortal, last_cycle_at, "
        "last_dream_at, created_at FROM agents ORDER BY name"
    )
    channels = query("SELECT name, description FROM channels ORDER BY name")
    recent = query(
        "SELECT m.seq, m.agent_name, c.name as channel, m.type, m.content, m.created_at "
        "FROM messages m JOIN channels c ON m.channel_id = c.id "
        "ORDER BY m.seq DESC LIMIT 20"
    )
    pending = query("SELECT COUNT(*) as n FROM proposals WHERE status = 'pending'")
    return templates.TemplateResponse("index.html", {
        "request": request,
        "agents": agents,
        "channels": channels,
        "recent": recent,
        "pending_proposals": pending[0]["n"] if pending else 0,
    })


@app.get("/channel/{name}", response_class=HTMLResponse)
async def channel_page(request: Request, name: str):
    channel = query_one("SELECT id, name, description FROM channels WHERE name = ?", (name,))
    if not channel:
        raise HTTPException(404, "Channel not found")
    messages = query(
        "SELECT seq, agent_name, type, content, metadata, created_at "
        "FROM messages WHERE channel_id = ? ORDER BY seq DESC LIMIT 100",
        (channel["id"],),
    )
    messages.reverse()
    return templates.TemplateResponse("channel.html", {
        "request": request,
        "channel": channel,
        "messages": messages,
    })


@app.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    agents = query(
        "SELECT name, model, status, hypothesis, immortal, last_cycle_at, "
        "last_dream_at, created_at FROM agents ORDER BY name"
    )
    return templates.TemplateResponse("agents.html", {
        "request": request,
        "agents": agents,
    })


@app.get("/proposals", response_class=HTMLResponse)
async def proposals_page(request: Request):
    proposals = query(
        "SELECT id, proposed_by, agent_name, hypothesis, status, created_at, reviewed_at "
        "FROM proposals ORDER BY created_at DESC"
    )
    return templates.TemplateResponse("proposals.html", {
        "request": request,
        "proposals": proposals,
    })


# ── htmx partials (write actions) ────────────────────────────────────────

@app.post("/action/post", response_class=HTMLResponse)
async def action_post(
    request: Request,
    channel: str = Form(...),
    message: str = Form(...),
    agent: str = Form(default="daniel"),
):
    try:
        run_cli("post", channel, message, "--agent", agent, "--type", "text")
    except subprocess.CalledProcessError as e:
        return HTMLResponse(f'<div class="error">{e.stderr}</div>', status_code=400)
    return HTMLResponse('<div class="success">Posted</div>')


@app.post("/action/approve/{proposal_id}")
async def action_approve(proposal_id: str):
    try:
        run_cli("approve", proposal_id)
    except subprocess.CalledProcessError as e:
        return HTMLResponse(f'<div class="error">{e.stderr}</div>', status_code=400)
    return RedirectResponse("/proposals", status_code=303)


@app.post("/action/reject/{proposal_id}")
async def action_reject(proposal_id: str):
    try:
        run_cli("reject", proposal_id)
    except subprocess.CalledProcessError as e:
        return HTMLResponse(f'<div class="error">{e.stderr}</div>', status_code=400)
    return RedirectResponse("/proposals", status_code=303)


# ── SSE ───────────────────────────────────────────────────────────────────

@app.get("/sse/messages")
async def sse_messages(request: Request, channel: str | None = None, since_seq: int = 0):
    async def stream():
        last_seq = since_seq or get_seq()
        while True:
            if await request.is_disconnected():
                break
            current_seq = get_seq()
            if current_seq > last_seq:
                # Fetch new messages
                if channel:
                    ch = query_one("SELECT id FROM channels WHERE name = ?", (channel,))
                    if ch:
                        msgs = query(
                            "SELECT m.seq, m.agent_name, c.name as channel, m.type, "
                            "m.content, m.created_at "
                            "FROM messages m JOIN channels c ON m.channel_id = c.id "
                            "WHERE m.channel_id = ? AND m.seq > ? ORDER BY m.seq",
                            (ch["id"], last_seq),
                        )
                    else:
                        msgs = []
                else:
                    msgs = query(
                        "SELECT m.seq, m.agent_name, c.name as channel, m.type, "
                        "m.content, m.created_at "
                        "FROM messages m JOIN channels c ON m.channel_id = c.id "
                        "WHERE m.seq > ? ORDER BY m.seq",
                        (last_seq,),
                    )
                for msg in msgs:
                    data = json.dumps(msg)
                    yield f"data: {data}\n\n"
                    last_seq = msg["seq"]
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    cfg = get_dashboard_config()
    port = int(cfg.get("port", 8080))
    uvicorn.run("dashboard.app:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
