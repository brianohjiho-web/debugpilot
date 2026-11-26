"""The web app: serves the page and the /api endpoints.

Where code runs:
  - RUNNER_URL set (the Docker setup): code runs in the separate, locked-down runner container.
  - RUNNER_URL not set (quick local try): code runs on this machine. Only paste code you trust.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.agent import ClaudeLLM, DebugAgent
from agent.examples import EXAMPLES
from agent.github import GitHubError, open_pull_request, pr_body
from runner.languages import LABELS, TEST_RULES
from runner.languages import run as run_local

RUNNER_URL = os.environ.get("RUNNER_URL", "").rstrip("/")
HERE = Path(__file__).parent

# A portable C/C++ compiler unzipped into the DebugPilot folder (w64devkit) is picked up
# automatically, so there's nothing to install or add to PATH.
for _kit in sorted(HERE.glob("w64devkit*/bin")) + sorted(HERE.glob("w64devkit*/w64devkit/bin")):
    if (_kit / "gcc.exe").exists() or (_kit / "gcc").exists():
        os.environ["PATH"] = str(_kit) + os.pathsep + os.environ.get("PATH", "")
        break


def run_code(language: str, source: str, test: str, setup: str = "") -> dict:
    if RUNNER_URL:
        r = httpx.post(f"{RUNNER_URL}/run", json={"language": language, "source": source,
                                                   "test": test, "setup": setup}, timeout=120)
        r.raise_for_status()
        return r.json()
    return run_local(language, source, test, setup).to_dict()


def make_llm():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return ClaudeLLM()


app = FastAPI(title="DebugPilot debug agent")


@app.middleware("http")
async def no_stale_pages(request, call_next):
    """Pages always load fresh, so a change never hides behind a browser's saved copy."""
    resp = await call_next(request)
    if (resp.headers.get("content-type") or "").startswith(("text/html", "text/css")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


PUBLIC = os.environ.get("DEBUGPILOT_PUBLIC") == "1"
# On a public website only "Scan a site" is open. Pasted code has no sandbox there, and the
# watcher / pull-request parts share one database, so those are switched off.
_PUBLIC_BLOCKED = ("/api/debug", "/api/pr", "/api/ingest", "/api/projects", "/api/issues", "/api/demo/reset")


UNLOCK_CODE = os.environ.get("DEBUGPILOT_UNLOCK_CODE", "")


def _unlocked(request) -> bool:
    import hmac
    return bool(UNLOCK_CODE) and hmac.compare_digest(request.cookies.get("dp_unlock", ""), UNLOCK_CODE)


@app.middleware("http")
async def public_mode(request, call_next):
    if PUBLIC:
        p = request.url.path
        if p == "/unlock":
            import hmac
            ok = bool(UNLOCK_CODE) and hmac.compare_digest(request.query_params.get("code", ""), UNLOCK_CODE)
            if not ok:
                return JSONResponse({"detail": "Wrong code."}, status_code=403)
            resp = RedirectResponse("/")
            resp.set_cookie("dp_unlock", UNLOCK_CODE, max_age=30 * 86400, httponly=True, secure=True, samesite="lax")
            return resp
        if not _unlocked(request):
            if p.startswith(_PUBLIC_BLOCKED) and p != "/api/debug":
                return JSONResponse({"detail": "This is switched off on the public website."}, status_code=403)
    return await call_next(request)


class DebugRequest(BaseModel):
    language: str
    source: str = Field(max_length=100_000)
    error: str = Field(default="", max_length=20_000)


class PRRequest(BaseModel):
    repo: str
    path: str
    report: dict
    base: str | None = None


@app.get("/")
def index():
    return FileResponse(HERE / "web" / "index.html")


@app.get("/api/config")
def config():
    return {
        "languages": {k: LABELS[k] for k in TEST_RULES},
        "claude": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "public": PUBLIC,
        "github": bool(os.environ.get("GITHUB_TOKEN")),
        "sandbox": "container" if RUNNER_URL else "this machine",
        "examples": {k: {"source": v["source"], "error": v["error"]} for k, v in EXAMPLES.items()},
    }


_EXAMPLE_CACHE: dict = {}


def _example_result(language: str, source: str, error: str) -> dict:
    """The built-in examples never change, so run each once and reuse the answer."""
    key = (language, source.strip())
    if key not in _EXAMPLE_CACHE:
        _EXAMPLE_CACHE[key] = DebugAgent(runner=run_code, llm=None).debug(language, source, error).to_dict()
    return _EXAMPLE_CACHE[key]


def _warm_examples():
    for lang, e in EXAMPLES.items():
        try:
            _example_result(lang, e["source"], e["error"])
        except Exception:
            pass


@app.on_event("startup")
def _warm_on_start():
    if PUBLIC:
        threading.Thread(target=_warm_examples, daemon=True).start()


@app.post("/api/debug")
def debug(req: DebugRequest, request: Request):
    if PUBLIC and not _unlocked(request):
        # Visitors can only try the built-in examples (no AI, no running their own code).
        if not any(req.source.strip() == e["source"].strip() for e in EXAMPLES.values()):
            return {"language": req.language, "mode": "demo", "steps": [], "answer": None, "attempts": 0,
                    "error": "This is a preview. On the public website you can try the built-in example (press Load example, then Debug). Pasting your own code is switched off."}
        return _example_result(req.language, req.source, req.error)
    agent = DebugAgent(runner=run_code, llm=make_llm())
    return agent.debug(req.language, req.source, req.error).to_dict()


@app.post("/api/pr")
def pull_request(req: PRRequest):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise HTTPException(400, "Set GITHUB_TOKEN in .env to open pull requests.")
    rep = req.report
    if not rep.get("verified") or not rep.get("answer"):
        raise HTTPException(400, "Only verified fixes can become a pull request.")
    a = rep["answer"]
    try:
        url = open_pull_request(token, req.repo, req.path, a["fixed_source"], a.get("pr_title") or "Fix from debug agent",
                                pr_body(rep), req.base or None)
    except GitHubError as e:
        raise HTTPException(400, str(e))
    return {"url": url}


# ======================================================================
# Website watching: crashes from real visitors -> automatic, tested fixes
# ======================================================================
import json as _json
import shutil
import time as _time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from agent import autofix, store
from agent.crashes import fingerprint, normalize

DEMO_DIR = Path(os.environ.get("DEBUGPILOT_DEMO_DIR") or HERE / "demo-site")
DEMO_KEY = "dp_demo"
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"}
_rate: dict[str, deque] = defaultdict(deque)
RATE_PER_MIN = 300

store.init()


def _seed_demo():
    """A ready-made local project for the demo shop, so the loop works on first run."""
    if os.environ.get("DEBUGPILOT_DEMO", "1") != "1" or store.project_by_key(DEMO_KEY):
        return
    store.create_project("Little Shop (demo)", "local", str(DEMO_DIR), "", "", True, url_prefix="demo", key=DEMO_KEY)


_seed_demo()


class NoCacheDemo(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-store"
        return resp


app.mount("/demo-booking", NoCacheDemo(directory=HERE / "demo-booking", html=True), name="demo-booking")
app.mount("/demo-power", NoCacheDemo(directory=HERE / "demo-power", html=True), name="demo-power")
app.mount("/demo", NoCacheDemo(directory=DEMO_DIR, html=True), name="demo")


@app.get("/watch.js")
def watch_js():
    return FileResponse(HERE / "watcher" / "watch.js", media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"})


@app.options("/api/ingest")
def ingest_preflight():
    return Response(status_code=204, headers=CORS)


@app.post("/api/ingest")
async def ingest(request: Request):
    """Receives crash reports from watch.js on any website. Always answers fast."""
    raw = await request.body()
    if len(raw) > 20_000:
        return Response(status_code=413, headers=CORS)
    try:
        event = _json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return Response(status_code=400, headers=CORS)
    proj = store.project_by_key(str(event.get("key", "")))
    if not proj:
        return Response(status_code=404, headers=CORS)
    q, now = _rate[proj["key"]], _time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        return Response(status_code=429, headers=CORS)
    q.append(now)

    ev, frame = normalize(event, proj.get("path_prefix") or "", proj.get("url_prefix") or "")
    iss, fresh = store.record_event(proj["id"], fingerprint(ev["type"], frame, ev["message"]), frame, ev)
    if proj["auto_fix"] and (fresh or iss["status"] == "new"):
        autofix.queue_fix(iss["id"], run_code, make_llm)
    return JSONResponse({"ok": True, "issue": iss["id"]}, headers=CORS)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    source: str = Field(pattern="^(github|local)$")
    repo: str = Field(min_length=1, max_length=300)
    branch: str = ""
    path_prefix: str = ""
    url_prefix: str = ""
    auto_fix: bool = True


@app.get("/api/projects")
def list_projects():
    return store.projects()


@app.post("/api/projects")
def add_project(p: ProjectIn):
    if p.source == "local" and os.environ.get("DEBUGPILOT_ALLOW_LOCAL", "1") != "1":
        raise HTTPException(400, "Local folders are turned off on this server. Use a GitHub repository.")
    if p.source == "github" and "/" not in p.repo:
        raise HTTPException(400, "Use the owner/name form, like brianoh/my-site.")
    return store.create_project(p.name, p.source, p.repo.strip(), p.branch.strip(), p.path_prefix.strip(), p.auto_fix, p.url_prefix.strip())


@app.get("/api/projects/{pid}/issues")
def project_issues(pid: int):
    if not store.project(pid):
        raise HTTPException(404, "No such project")
    return store.issues(pid)


@app.get("/api/issues/{iid}")
def get_issue(iid: int):
    iss = store.issue(iid)
    if not iss:
        raise HTTPException(404, "No such issue")
    return iss


@app.post("/api/issues/{iid}/fix")
def refix(iid: int):
    if not store.issue(iid):
        raise HTTPException(404, "No such issue")
    started = autofix.queue_fix(iid, run_code, make_llm)
    return {"started": started}


@app.post("/api/issues/{iid}/apply")
def apply_fix(iid: int):
    try:
        return autofix.apply_local_fix(iid)
    except autofix.SourceError as e:
        raise HTTPException(400, str(e))


@app.post("/api/demo/reset")
def reset_demo():
    """Put the demo shop's bugs back and clear its issues, so the demo can be shown again."""
    proj = store.project_by_key(DEMO_KEY)
    if not proj:
        raise HTTPException(404, "The demo project is turned off")
    shutil.copyfile(DEMO_DIR / "_original" / "cart.js", DEMO_DIR / "cart.js")
    store.reset_project_issues(proj["id"])
    return {"ok": True}




# ---------------------------------------------------------------- Scan any website (nothing to install)
import asyncio
import sys


def _run_async(coro):
    """Run a scan in its own event loop. On Windows the hidden browser needs the Proactor loop,
    even when the server runs in auto-restart mode (which switches the default loop)."""
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as r:
            return r.run(coro)
    return asyncio.run(coro)
import re
import threading
import time
import uuid
from urllib.parse import urlparse

from agent import scanner

SCANS: dict[str, dict] = {}
SCAN_DIR = Path(os.environ.get("DEBUGPILOT_DATA") or HERE / "data") / "scans"
_scan_slots = threading.Semaphore(int(os.environ.get("DEBUGPILOT_SCAN_SLOTS", "2")))


class ScanIn(BaseModel):
    url: str = Field(max_length=500)
    equipment: bool = False       # read-only scan of a device page (UPS, PDU, cooling, switchgear)
    permission: bool = False      # the person confirmed they're allowed to test that equipment


_PROXY_HEADERS = ("cf-connecting-ip", "cf-ray", "x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded")


def _from_this_computer(request: Request) -> bool:
    """True only for someone sitting at this PC. Visitors coming through a share link (tunnel)
    also arrive from 127.0.0.1, so a forwarding header or a non-local Host name means 'outside'."""
    host = (request.client.host if request.client else "") or ""
    if not (host in ("127.0.0.1", "::1", "localhost") or host.startswith("::ffff:127.")):
        return False
    if any(h in request.headers for h in _PROXY_HEADERS):
        return False
    name = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
    return name in ("localhost", "127.0.0.1", "::1", "")


def _scan_job(job_id: str, url: str, allow_private: bool, equipment: bool = False):
    job = SCANS[job_id]
    with _scan_slots:
        job["status"] = "running"
        try:
            def found(bugs):   # crashes so far, shown on the report page while the scan runs
                job["found"] = [{"id": b["id"], "type": b["type"], "message": b["message"][:200],
                                 "where": f"{(b.get('frame') or {}).get('func') or '?'}() · {((b.get('frame') or {}).get('url') or '').split('/')[-1]}:{(b.get('frame') or {}).get('line') or '?'}",
                                 "status": b.get("status") or "found"}
                                for b in bugs]
            rep = _run_async(scanner.scan(url, llm=make_llm(), allow_private=allow_private,
                                           progress=lambda m: job.__setitem__("progress", m), on_found=found,
                                           equipment=equipment))
            # Fixed code files stay on the server; the report only lists them.
            job["fixed_files"] = rep.pop("fixed_files", {}) or {}
            rep["fix_files"] = [{"url": u, "name": (urlparse(u).path.rsplit("/", 1)[-1] or "script.js")} for u in job["fixed_files"]]
            rep["own_demo"] = bool(job["fixed_files"]) and all(_demo_file(url, u) for u in job["fixed_files"])
            job["report"] = rep
            job["status"] = "done"
            _save_job(job)
        except scanner.ScanBlocked as e:
            job["status"], job["error"] = "error", str(e)
        except Exception as e:  # browser crashed, timeout, etc.
            job["status"], job["error"] = "error", f"The scan stopped: {str(e).splitlines()[0][:200]}"
        job["progress"] = ""


def _save_job(job: dict):
    """Keep finished reports on disk so a report link still works after a restart."""
    try:
        d = SCAN_DIR / job["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(_json.dumps(job), encoding="utf-8")
    except Exception:
        pass


def _load_job(job_id: str):
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        return None
    try:
        return _json.loads((SCAN_DIR / job_id / "job.json").read_text(encoding="utf-8"))
    except Exception:
        return None


_scan_hits: dict[str, deque] = defaultdict(deque)
SCANS_PER_HOUR = int(os.environ.get("DEBUGPILOT_SCANS_PER_HOUR", "10"))


def _limit_scans(request: Request):
    """Public site only: each visitor gets a few scans per hour."""
    if not PUBLIC:
        return
    fwd = request.headers.get("x-forwarded-for", "")
    who = fwd.split(",")[0].strip() or (request.client.host if request.client else "?")
    q, now = _scan_hits[who], _time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= SCANS_PER_HOUR:
        raise HTTPException(429, f"You've reached {SCANS_PER_HOUR} scans this hour. Please try again later.")
    q.append(now)


@app.post("/api/scan")
def start_scan(body: ScanIn, request: Request):
    url = body.url.strip()
    # Private addresses are blocked, except this server's own demo shop (or when you allow it for local testing).
    target = urlparse(url if "://" in url else "http://" + url)
    same = lambda a, b: a.replace("127.0.0.1", "localhost") == b.replace("127.0.0.1", "localhost")
    local = f"127.0.0.1:{(request.scope.get('server') or ('', 8000))[1]}"
    own_demo = target.path.startswith("/demo") and (same(target.netloc, request.url.netloc) or same(target.netloc, local))
    if own_demo:
        # Scan this server's own demo sites over the loopback address. Behind GitHub Codespaces or a
        # share link the public address can sit behind a sign-in page that the hidden browser can't pass.
        url = f"http://{local}{target.path}" + (f"?{target.query}" if target.query else "")
    allow_private = own_demo or os.environ.get("DEBUGPILOT_SCAN_ALLOW_PRIVATE") == "1"
    if body.equipment:
        if not body.permission:
            raise HTTPException(400, "Tick the box to confirm you have permission to test this equipment.")
        # Device pages live on private networks. Only allow that when DebugPilot runs on your own computer,
        # so a copy hosted online can never be used to poke at someone else's network.
        if not _from_this_computer(request) and os.environ.get("DEBUGPILOT_SCAN_ALLOW_PRIVATE") != "1":
            raise HTTPException(400, "Equipment on a local network can only be scanned when DebugPilot runs on your own computer.")
        allow_private = True
    try:
        url = scanner.check_url(url, allow_private, "http" if body.equipment else "https")
    except scanner.ScanBlocked as e:
        raise HTTPException(400, str(e))
    busy = sum(1 for j in SCANS.values() if j["status"] in ("queued", "running"))
    if busy >= 6:
        raise HTTPException(429, "Too many scans running. Try again in a minute.")
    _limit_scans(request)
    job_id = uuid.uuid4().hex[:12]
    SCANS[job_id] = {"id": job_id, "url": url, "status": "queued", "progress": "Waiting to start", "started": time.time()}
    SCANS[job_id]["equipment"] = body.equipment
    threading.Thread(target=_scan_job, args=(job_id, url, allow_private, body.equipment), daemon=True).start()
    return SCANS[job_id]


@app.get("/api/scan/{job_id}")
def scan_status(job_id: str):
    job = SCANS.get(job_id) or _load_job(job_id)
    if not job:
        raise HTTPException(404, "No such scan")
    return {k: v for k, v in job.items() if k != "fixed_files"}


# ---- Fixing the site: download the fixed file, apply it (DebugPilot's own demo sites), or send it to GitHub ----
DEMO_DIRS = {"demo-booking": HERE / "demo-booking", "demo-power": HERE / "demo-power", "demo": DEMO_DIR}


def _demo_file(page_url: str, file_url: str):
    """The local file behind one of DebugPilot's own demo sites, or None for any other site."""
    pu, fu = urlparse(page_url), urlparse(file_url)
    if pu.netloc != fu.netloc:
        return None
    parts = fu.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] not in DEMO_DIRS or not parts[0] == pu.path.strip("/").split("/")[0]:
        return None
    if not re.fullmatch(r"[\w.-]+\.js", parts[1]):
        return None
    f = DEMO_DIRS[parts[0]] / parts[1]
    return f if f.is_file() else None


def _done_job(job_id: str) -> dict:
    job = SCANS.get(job_id)
    if not job or job.get("status") != "done":
        raise HTTPException(404, "That report isn't available anymore. Scan again.")
    return job


@app.get("/api/scan/{job_id}/fixed")
def download_fixed(job_id: str, file: str):
    job = _done_job(job_id)
    f = (job.get("fixed_files") or {}).get(file)
    if not f:
        raise HTTPException(404, "No fixed version of that file.")
    name = urlparse(file).path.rsplit("/", 1)[-1] or "script.js"
    return Response(f["fixed"], media_type="application/javascript",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/scan/{job_id}/apply")
def apply_fixes(job_id: str):
    """Writes the tested fixes into DebugPilot's own demo site, so a new scan shows them fixed."""
    job = _done_job(job_id)
    page = job["report"]["url"]
    done = []
    for u, f in (job.get("fixed_files") or {}).items():
        path = _demo_file(page, u)
        if not path:
            raise HTTPException(400, "DebugPilot can only change its own demo sites. For your own site, download the fixed file or send it to GitHub.")
        current = path.read_text()
        merged = autofix.merge_fix(f["original"], f["fixed"], current)
        if merged is None:
            raise HTTPException(409, f"{path.name} changed since the scan. Scan again, then apply.")
        path.write_text(merged)
        done.append(path.name)
    if not done:
        raise HTTPException(400, "There are no tested fixes to apply.")
    return {"applied": done}


@app.post("/api/demo/reset-all")
def reset_all_demos():
    """Puts the bugs back in every demo site."""
    for d in DEMO_DIRS.values():
        orig = d / "_original"
        if orig.is_dir():
            for f in orig.iterdir():
                if f.is_file():
                    shutil.copyfile(f, d / f.name)
    return {"ok": True}


class FixPR(BaseModel):
    file: str
    repo: str
    path: str
    base: str = ""


@app.post("/api/scan/{job_id}/pr")
def fixes_to_github(job_id: str, req: FixPR):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise HTTPException(400, "Add a GITHUB_TOKEN in DebugPilot's settings (.env) to send fixes to GitHub.")
    job = _done_job(job_id)
    f = (job.get("fixed_files") or {}).get(req.file)
    if not f:
        raise HTTPException(404, "No fixed version of that file.")
    proven = [b for b in job["report"]["bugs"] if b.get("status") == "proven" and b.get("script") == req.file]
    title = "Fix " + (f"{len(proven)} crashes" if len(proven) > 1 else "a crash") + f" found by DebugPilot in {req.path.rsplit('/', 1)[-1]}"
    body = "DebugPilot found these crashes on " + job["report"]["url"] + " and tested each fix in the live page:\n\n" + "\n".join(
        f"- {(b.get('fix') or {}).get('summary') or b['message']}" for b in proven) + "\n\nReview before merging."
    try:
        url = open_pull_request(token, req.repo.strip(), req.path, f["fixed"], title, body, req.base or None)
    except GitHubError as e:
        raise HTTPException(400, str(e))
    return {"url": url}


@app.get("/api/scan/{job_id}/video/{name}")
def scan_video(job_id: str, name: str):
    if not re.fullmatch(r"[0-9a-f]{12}", job_id) or not re.fullmatch(r"bug\d+-(before|after)\.webm", name):
        raise HTTPException(404, "No such video")
    f = SCAN_DIR / job_id / name
    if not f.exists():
        raise HTTPException(404, "No such video")
    return FileResponse(f, media_type="video/webm")


@app.get("/scan")
def scan_page():
    return FileResponse(HERE / "web" / "scan.html")


@app.get("/api/demo/recommendations")
async def demo_recommendations():
    await asyncio.sleep(1.4)   # slow on purpose, for the speed check
    return ["Travel mug", "Tote bag", "Sticker pack"]


@app.get("/report/{job_id}")
def report_page(job_id: str):
    return FileResponse(HERE / "web" / "report.html")


@app.get("/img/{name}")
def image(name: str):
    if name not in ("cat-happy.svg", "cat-search.svg", "cat-yay.svg"):
        raise HTTPException(404, "No such image")
    return FileResponse(HERE / "web" / "img" / name, media_type="image/svg+xml")


@app.get("/theme.css")
def theme_css():
    return FileResponse(HERE / "web" / "theme.css", media_type="text/css")
