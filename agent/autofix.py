"""The automatic part: a crash comes in, and a tested fix goes out.

  crash -> find the file in the repo -> run the debug agent -> proven fix?
     yes -> GitHub project: open a draft pull request
            local project:  "Apply fix" button (a person still decides)
     no  -> mark it "needs a human" with everything the agent found
"""

from __future__ import annotations

import base64
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import httpx

from . import store
from .agent import DebugAgent
from .crashes import language_for
from .demo_fixes import demo_answer_for
from .github import GitHubError, open_pull_request, pr_body

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="autofix")
_running: set[int] = set()
_running_lock = threading.Lock()


class SourceError(Exception):
    pass


def _local_file(project: dict, path: str) -> Path:
    root = Path(project["repo"]).resolve()
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        raise SourceError("That file is outside the project folder.")
    return target


def read_source(project: dict, path: str) -> str:
    if not path:
        raise SourceError("The crash didn't point to a file in your code.")
    if project["source"] == "local":
        f = _local_file(project, path)
        if not f.is_file():
            raise SourceError(f"{path} isn't in the project folder.")
        return f.read_text()
    # GitHub (works without a token for public repos)
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    params = {"ref": project["branch"]} if project.get("branch") else None
    r = httpx.get(f"https://api.github.com/repos/{project['repo']}/contents/{path}", headers=headers, params=params, timeout=20)
    if r.status_code == 404:
        raise SourceError(f"{path} wasn't found in {project['repo']}. Check the folder setting for this website.")
    if r.status_code >= 300:
        raise SourceError(f"GitHub said {r.status_code} when reading {path}.")
    data = r.json()
    if isinstance(data, list) or data.get("encoding") != "base64":
        raise SourceError(f"{path} isn't a single file.")
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


def error_text(ev: dict) -> str:
    return (f"{ev.get('type')}: {ev.get('message')}\n{ev.get('stack', '')}\n\n"
            f"Happened on page: {ev.get('page')}\nBrowser: {ev.get('browser')}")


def queue_fix(issue_id: int, runner: Callable, llm_factory: Callable) -> bool:
    """Start a background fix unless one is already running for this issue."""
    with _running_lock:
        if issue_id in _running:
            return False
        _running.add(issue_id)
    store.update_issue(issue_id, status="fixing", note="")
    _pool.submit(_run_safely, issue_id, runner, llm_factory)
    return True


def _run_safely(issue_id, runner, llm_factory):
    try:
        fix_issue(issue_id, runner, llm_factory())
    except Exception as e:  # keep the worker alive and say what happened
        store.update_issue(issue_id, status="needs_human", note=f"The agent stopped: {e}")
    finally:
        with _running_lock:
            _running.discard(issue_id)


def fix_issue(issue_id: int, runner: Callable, llm) -> dict:
    iss = store.issue(issue_id)
    proj = store.project(iss["project_id"])
    path = iss.get("file") or ""
    lang = language_for(path)
    if not lang:
        store.update_issue(issue_id, status="no_source", note=f"DebugPilot can fix JavaScript and TypeScript files from websites; this crash points to '{path or 'unknown'}'.")
        return store.issue(issue_id)
    try:
        source = read_source(proj, path)
    except (SourceError, httpx.HTTPError) as e:
        store.update_issue(issue_id, status="no_source", note=str(e))
        return store.issue(issue_id)

    demo = None if llm else demo_answer_for(iss.get("func") or "", source)
    rep = DebugAgent(runner=runner, llm=llm).debug(lang, source, error_text(iss.get("sample") or {}), demo_answer=demo)
    report = rep.to_dict()
    report["original_source"] = source
    report["path"] = path

    if rep.error:
        store.update_issue(issue_id, status="needs_human", report=report, note=rep.error)
    elif rep.answer and rep.answer.get("category") == "not_code":
        store.update_issue(issue_id, status="needs_human", report=report, note="The agent thinks this isn't a code bug.")
    elif not rep.verified:
        store.update_issue(issue_id, status="needs_human", report=report, note="The agent couldn't prove a fix, so nothing was opened.")
    elif proj["source"] == "github" and os.environ.get("GITHUB_TOKEN"):
        try:
            url = open_pull_request(os.environ["GITHUB_TOKEN"], proj["repo"], path, rep.answer["fixed_source"],
                                    rep.answer.get("pr_title") or "Fix from DebugPilot", pr_body(report) + _crash_note(iss),
                                    proj.get("branch") or None)
            store.update_issue(issue_id, status="pr_open", report=report, pr_url=url, note="Draft pull request opened.")
        except GitHubError as e:
            store.update_issue(issue_id, status="fix_ready", report=report, note=f"Fix is verified, but the pull request failed: {e}")
    else:
        note = ("Fix is verified. Press Apply to write it into the project folder." if proj["source"] == "local"
                else "Fix is verified. Add a GITHUB_TOKEN to open pull requests automatically.")
        store.update_issue(issue_id, status="fix_ready", report=report, note=note)
    return store.issue(issue_id)


def _crash_note(iss: dict) -> str:
    s = iss.get("sample") or {}
    return (f"\n\n## The crash\nSeen **{iss.get('count')}** time(s) on real visits, most recently on `{s.get('page', '')}`.\n\n"
            f"```\n{s.get('type')}: {s.get('message')}\n{s.get('stack', '')[:1500]}\n```\n")


def merge_fix(original: str, fixed: str, current: str):
    """Apply the fix's changes onto the current file, like a patch.
    Each changed block (with 2 lines of context) must appear exactly once in the current file.
    Returns the merged text, or None if a block can't be placed safely."""
    if current == original:
        return fixed
    import difflib
    a, b = original.splitlines(keepends=True), fixed.splitlines(keepends=True)
    out = current
    for tag, i1, i2, j1, j2 in reversed(difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()):
        if tag == "equal":
            continue
        lo, hi = max(0, i1 - 2), min(len(a), i2 + 2)
        old = "".join(a[lo:hi])
        new = "".join(a[lo:i1]) + "".join(b[j1:j2]) + "".join(a[i2:hi])
        if not old or out.count(old) != 1:
            return None
        out = out.replace(old, new, 1)
    return out


def apply_local_fix(issue_id: int) -> dict:
    """Local projects only: write the verified fix into the file (what merging the PR would do)."""
    iss = store.issue(issue_id)
    proj = store.project(iss["project_id"])
    rep = iss.get("report") or {}
    if proj["source"] != "local":
        raise SourceError("Apply only works for local projects. GitHub projects get a pull request instead.")
    if iss["status"] != "fix_ready" or not rep.get("verified"):
        raise SourceError("Only a verified fix can be applied.")
    f = _local_file(proj, rep["path"])
    current = f.read_text()
    merged = merge_fix(rep.get("original_source", ""), rep["answer"]["fixed_source"], current)
    if merged is None:
        raise SourceError("The file changed in the same spot since the fix was made. Run the agent again.")
    f.write_text(merged)
    store.update_issue(issue_id, status="fixed", note="Fix applied to the project folder.")
    return store.issue(issue_id)
