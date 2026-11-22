from fastapi.testclient import TestClient

import server
from agent.examples import EXAMPLES

client = TestClient(server.app)


def test_config_lists_all_languages():
    cfg = client.get("/api/config").json()
    assert set(cfg["languages"]) == set(EXAMPLES)


def test_debug_example_in_demo_mode(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ex = EXAMPLES["c"]
    rep = client.post("/api/debug", json={"language": "c", "source": ex["source"], "error": ex["error"]}).json()
    assert rep["verified"]


def test_pr_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    r = client.post("/api/pr", json={"repo": "a/b", "path": "x.py", "report": {}})
    assert r.status_code == 400


def test_page_loads():
    assert "DebugPilot" in client.get("/").text


def test_equipment_scan_needs_permission_and_this_computer():
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app)
    r = c.post("/api/scan", json={"url": "192.168.1.50", "equipment": True})
    assert r.status_code == 400 and "permission" in r.json()["detail"]
    # The test client isn't "this computer", like a copy hosted online: local devices stay off limits.
    r = c.post("/api/scan", json={"url": "192.168.1.50", "equipment": True, "permission": True})
    assert r.status_code == 400 and "your own computer" in r.json()["detail"]
