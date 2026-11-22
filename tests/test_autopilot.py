"""Website autopilot: stack parsing, crash grouping, and the full crash → fix → apply loop (no LLM, no GitHub)."""
import json, os, shutil, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHROME = "TypeError: Cannot read properties of undefined (reading 'name')\n    at cheapestItem (http://localhost:8000/demo/cart.js:36:24)\n    at checkout (http://localhost:8000/demo/app.js:24:20)"
FIREFOX = "cheapestItem@http://localhost:8000/demo/cart.js:36:24\ncheckout@http://localhost:8000/demo/app.js:24:20"


def test_parse_chrome_and_firefox_stacks():
    from agent.crashes import parse_stack
    for st in (CHROME, FIREFOX):
        f = parse_stack(st)
        assert f[0]["func"] == "cheapestItem" and f[0]["line"] == 36 and f[0]["url"].endswith("/demo/cart.js")


def test_fingerprint_groups_same_crash():
    from agent.crashes import normalize, fingerprint
    ea, fa = normalize({"type": "TypeError", "message": "x", "stack": CHROME, "page": "/"}, "", "demo")
    eb, fb = normalize({"type": "TypeError", "message": "x", "stack": CHROME.replace("36:24", "37:2"), "page": "/other"}, "", "demo")
    assert fa["file"] == "cart.js"
    assert fingerprint(ea["type"], fa, ea["message"]) == fingerprint(eb["type"], fb, eb["message"])


def test_merge_fix_places_two_fixes_in_one_file():
    from agent.autofix import merge_fix
    orig = "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n"
    fix1 = orig.replace("b\n", "B\n")
    fix2 = orig.replace("i\n", "I\n")
    assert merge_fix(orig, fix2, fix1) == "a\nB\nc\nd\ne\nf\ng\nh\nI\nj\n"
    assert merge_fix(orig, orig.replace("b\n", "X\n"), fix1) is None   # same spot changed: refuse


@pytest.fixture()
def client(tmp_path, monkeypatch):
    site = tmp_path / "demo-site"
    shutil.copytree(ROOT / "demo-site", site)
    shutil.copy(site / "_original" / "cart.js", site / "cart.js")
    monkeypatch.setenv("DEBUGPILOT_DB", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("DEBUGPILOT_DEMO_DIR", str(site))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    import importlib, agent.store, agent.autofix, server
    for m in (agent.store, agent.autofix, server):
        importlib.reload(m)
    from fastapi.testclient import TestClient
    with TestClient(server.app) as c:
        c.site = site
        yield c


def _wait(c, iid):
    for _ in range(100):
        i = c.get(f"/api/issues/{iid}").json()
        if i["status"] != "fixing":
            return i
        time.sleep(0.2)
    raise AssertionError("fix never finished")


def test_full_loop_crash_fix_apply(client):
    c = client
    stacks = {
        "cheapestItem": "TypeError: Cannot read properties of undefined (reading 'name')\n    at cheapestItem (http://t/demo/cart.js:36:24)",
        "applyCoupon": "TypeError: Cannot read properties of undefined (reading 'percent')\n    at applyCoupon (http://t/demo/cart.js:20:30)",
    }
    ids = []
    for _ in range(2):  # same crash twice groups into one issue
        for st in stacks.values():
            r = c.post("/api/ingest", content=json.dumps({"key": "dp_demo", "type": "TypeError", "message": st.split(": ", 1)[1].split("\n")[0], "stack": st, "page": "http://t/demo/"}))
            assert r.status_code == 200
            ids.append(r.json()["issue"])
    assert len(set(ids)) == 2
    for iid in set(ids):
        i = _wait(c, iid)
        assert i["status"] == "fix_ready", i["note"]
        assert i["count"] == 2 and i["report"]["verified"]
        assert any(not x["ok"] for x in i["report"]["before"]["results"])
        assert all(x["ok"] for x in i["report"]["after"]["results"])
    for iid in sorted(set(ids)):  # two fixes to the same file both apply
        r = c.post(f"/api/issues/{iid}/apply")
        assert r.status_code == 200 and r.json()["status"] == "fixed", r.text
    src = (c.site / "cart.js").read_text()
    assert "skus.length === 0" in src or "length" in src
    assert src != (c.site / "_original" / "cart.js").read_text()
    assert c.post("/api/demo/reset").status_code == 200
    assert (c.site / "cart.js").read_text() == (c.site / "_original" / "cart.js").read_text()
    assert c.get("/api/projects/1/issues").json() == []


def test_ingest_rejects_unknown_key(client):
    r = client.post("/api/ingest", content=json.dumps({"key": "nope", "type": "Error", "message": "x", "stack": CHROME}))
    assert r.status_code in (403, 404)
