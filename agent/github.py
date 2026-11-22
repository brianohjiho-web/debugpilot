"""Open a draft pull request with the agent's fix.

Draft on purpose: a human reviews and merges. The agent never pushes to
the main branch and never merges anything by itself.
"""

from __future__ import annotations

import base64
import re
import time

import httpx

API = "https://api.github.com"


class GitHubError(Exception):
    pass


def _check(r: httpx.Response, what: str):
    if r.status_code >= 300:
        try:
            msg = r.json().get("message", r.text)
        except Exception:
            msg = r.text
        raise GitHubError(f"{what} failed ({r.status_code}): {msg}")


def open_pull_request(token: str, repo: str, path: str, new_content: str, title: str, body: str,
                      base: str | None = None) -> str:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise GitHubError("Repository should look like owner/name")
    path = path.strip().lstrip("/")
    if not path or ".." in path.split("/"):
        raise GitHubError("File path should be a path inside the repo, like src/shop.py")
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    with httpx.Client(base_url=API, headers=h, timeout=20) as gh:
        if not base:
            r = gh.get(f"/repos/{repo}"); _check(r, "Reading the repository")
            base = r.json()["default_branch"]
        r = gh.get(f"/repos/{repo}/git/ref/heads/{base}"); _check(r, f"Reading branch {base}")
        base_sha = r.json()["object"]["sha"]

        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "fix"
        branch = f"debug-agent/{slug}-{int(time.time())}"
        r = gh.post(f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        _check(r, "Creating the branch")

        r = gh.get(f"/repos/{repo}/contents/{path}", params={"ref": base})
        file_sha = r.json().get("sha") if r.status_code == 200 else None
        if r.status_code not in (200, 404):
            _check(r, f"Reading {path}")

        payload = {"message": title, "branch": branch,
                   "content": base64.b64encode(new_content.encode()).decode()}
        if file_sha:
            payload["sha"] = file_sha
        r = gh.put(f"/repos/{repo}/contents/{path}", json=payload); _check(r, f"Committing {path}")

        r = gh.post(f"/repos/{repo}/pulls", json={"title": title, "head": branch, "base": base,
                                                  "body": body, "draft": True})
        _check(r, "Opening the pull request")
        return r.json()["html_url"]


def pr_body(report: dict) -> str:
    a = report.get("answer") or {}
    tests = report.get("after") or {}
    rows = "\n".join(f"- {'✅' if x['ok'] else '❌'} `{x['name']}`" for x in tests.get("results", []))
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(a.get("next_steps", []), 1))
    return f"""## Summary
{a.get('summary', '')}

## Root cause
{a.get('root_cause', '')}

## Next steps
{steps}

## Proof
The test below failed on the original code and passes on this fix (run in an isolated sandbox).

{rows}

```
{a.get('test_code', '').strip()}
```

---
Opened as a draft by the debug agent. Please review before merging.
"""
