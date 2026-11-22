"""Where projects and crashes are kept: one small SQLite file.

A project is one website. An issue is a group of crashes that are the same bug
(same error type, same function, same file), so 500 visitors hitting one bug is
one issue with a count of 500, not 500 issues.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("DEBUGPILOT_DB", Path(__file__).resolve().parent.parent / "data" / "debugpilot.db"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  source TEXT NOT NULL,            -- 'github' or 'local'
  repo TEXT,                       -- owner/name for github, folder path for local
  branch TEXT,
  path_prefix TEXT DEFAULT '',     -- where the site's files live inside the repo, e.g. 'public/'
  url_prefix TEXT DEFAULT '',      -- part of the URL path to drop first, e.g. 'demo' for /demo/cart.js
  auto_fix INTEGER DEFAULT 1,
  created REAL
);
CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL,
  fingerprint TEXT NOT NULL,
  type TEXT, message TEXT, func TEXT, file TEXT, line INTEGER,
  count INTEGER DEFAULT 0,
  first_seen REAL, last_seen REAL,
  status TEXT DEFAULT 'new',       -- new, fixing, fix_ready, pr_open, fixed, needs_human, no_source
  sample TEXT,                     -- last event, JSON
  report TEXT,                     -- agent report, JSON
  pr_url TEXT,
  note TEXT,
  UNIQUE(project_id, fingerprint)
);
"""


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.executescript(SCHEMA)


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for k in ("sample", "report"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def create_project(name: str, source: str, repo: str, branch: str = "", path_prefix: str = "", auto_fix: bool = True,
                   url_prefix: str = "", key: str | None = None) -> dict:
    key = key or "dp_" + secrets.token_urlsafe(12)
    with _lock, _conn() as c:
        cur = c.execute(
            "INSERT INTO projects (key,name,source,repo,branch,path_prefix,url_prefix,auto_fix,created) VALUES (?,?,?,?,?,?,?,?,?)",
            (key, name, source, repo, branch, path_prefix, url_prefix, int(auto_fix), time.time()))
        return _row(c.execute("SELECT * FROM projects WHERE id=?", (cur.lastrowid,)).fetchone())


def projects() -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT p.*, (SELECT COUNT(*) FROM issues i WHERE i.project_id=p.id AND i.status NOT IN ('fixed')) AS open_issues "
                         "FROM projects p ORDER BY created DESC").fetchall()
        return [_row(r) for r in rows]


def project_by_key(key: str) -> dict | None:
    with _lock, _conn() as c:
        return _row(c.execute("SELECT * FROM projects WHERE key=?", (key,)).fetchone())


def project(pid: int) -> dict | None:
    with _lock, _conn() as c:
        return _row(c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())


def record_event(project_id: int, fingerprint: str, frame: dict, event: dict) -> tuple[dict, bool]:
    """Add one crash. Returns (issue, is_new_or_reopened)."""
    now = time.time()
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM issues WHERE project_id=? AND fingerprint=?", (project_id, fingerprint)).fetchone()
        if row is None:
            cur = c.execute(
                "INSERT INTO issues (project_id,fingerprint,type,message,func,file,line,count,first_seen,last_seen,status,sample) "
                "VALUES (?,?,?,?,?,?,?,1,?,?, 'new', ?)",
                (project_id, fingerprint, event.get("type"), event.get("message"), frame.get("func"), frame.get("file"),
                 frame.get("line"), now, now, json.dumps(event)))
            return _row(c.execute("SELECT * FROM issues WHERE id=?", (cur.lastrowid,)).fetchone()), True
        reopened = row["status"] == "fixed"
        c.execute("UPDATE issues SET count=count+1, last_seen=?, message=?, sample=?, status=CASE WHEN status='fixed' THEN 'new' ELSE status END WHERE id=?",
                  (now, event.get("message"), json.dumps(event), row["id"]))
        return _row(c.execute("SELECT * FROM issues WHERE id=?", (row["id"],)).fetchone()), reopened


def issues(project_id: int) -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM issues WHERE project_id=? ORDER BY last_seen DESC", (project_id,)).fetchall()
        return [_row(r) for r in rows]


def issue(iid: int) -> dict | None:
    with _lock, _conn() as c:
        return _row(c.execute("SELECT * FROM issues WHERE id=?", (iid,)).fetchone())


def update_issue(iid: int, **fields):
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k}=?")
        vals.append(json.dumps(v) if k in ("report", "sample") and not isinstance(v, str) and v is not None else v)
    with _lock, _conn() as c:
        c.execute(f"UPDATE issues SET {', '.join(cols)} WHERE id=?", (*vals, iid))


def reset_project_issues(project_id: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM issues WHERE project_id=?", (project_id,))
