"""Turn a browser crash report into something the agent can work on.

1. Read the stack trace (Chrome, Firefox and Safari write them differently).
2. Pick the frame that points at the website's own code.
3. Map that URL to a file in the repo, and give the crash a fingerprint so
   repeats of the same bug group together.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

CHROME = re.compile(r"^\s*at\s+(?:(?P<func>.+?)\s+\()?(?P<url>(?:https?|file)://[^\s)]+?):(?P<line>\d+):(?P<col>\d+)\)?\s*$")
FIREFOX = re.compile(r"^\s*(?P<func>[^@\s]*)@(?P<url>(?:https?|file)://[^\s]+?):(?P<line>\d+):(?P<col>\d+)\s*$")

LANG_BY_EXT = {".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript"}


def parse_stack(stack: str) -> list[dict]:
    frames = []
    for line in (stack or "").splitlines():
        m = CHROME.match(line) or FIREFOX.match(line)
        if not m:
            continue
        func = (m.group("func") or "").strip() or "(anonymous)"
        func = func.replace("Object.", "").replace("HTMLButtonElement.", "")
        frames.append({"func": func, "url": m.group("url"), "line": int(m.group("line")), "col": int(m.group("col"))})
    return frames


def pick_frame(frames: list[dict], page: str = "") -> dict | None:
    """The first frame from the site's own scripts (not the watcher, not a browser extension)."""
    page_host = urlparse(page).netloc if page else ""
    own = [f for f in frames if not f["url"].endswith("/watch.js") and urlparse(f["url"]).scheme in ("http", "https", "file")]
    same_site = [f for f in own if not page_host or urlparse(f["url"]).netloc == page_host]
    return (same_site or own or [None])[0]


def repo_path(url: str, path_prefix: str = "", strip_prefix: str = "") -> str:
    """https://shop.com/js/cart.js -> js/cart.js (plus the project's folder prefix, e.g. public/js/cart.js)."""
    path = urlparse(url).path.lstrip("/")
    if strip_prefix and path.startswith(strip_prefix.strip("/") + "/"):
        path = path[len(strip_prefix.strip("/")) + 1:]
    prefix = (path_prefix or "").strip("/")
    return f"{prefix}/{path}" if prefix else path


def language_for(path: str) -> str | None:
    for ext, lang in LANG_BY_EXT.items():
        if path.endswith(ext):
            return lang
    return None


def fingerprint(err_type: str, frame: dict | None, message: str) -> str:
    if frame:
        return f"{err_type}|{frame.get('func')}|{frame.get('file')}"
    # No usable frame: group by the message with numbers removed.
    return f"{err_type}|{re.sub(r'\\d+', 'N', message or '')[:120]}"


def normalize(event: dict, path_prefix: str = "", strip_prefix: str = "") -> tuple[dict, dict]:
    """Return (clean_event, frame_with_repo_file)."""
    ev = {
        "type": str(event.get("type") or "Error")[:80],
        "message": str(event.get("message") or "")[:500],
        "stack": str(event.get("stack") or "")[:4000],
        "page": str(event.get("page") or "")[:500],
        "browser": str(event.get("browser") or "")[:200],
        "at": str(event.get("at") or "")[:40],
    }
    frame = pick_frame(parse_stack(ev["stack"]), ev["page"])
    if frame:
        frame = dict(frame, file=repo_path(frame["url"], path_prefix, strip_prefix))
    return ev, (frame or {})
