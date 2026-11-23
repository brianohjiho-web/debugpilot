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


def test_share_link_visitors_cannot_scan_equipment():
    """Through a share link (tunnel) visitors arrive from 127.0.0.1 too; they must still count as outside."""
    from fastapi.testclient import TestClient
    import server
    c = TestClient(server.app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 5555))
    body = {"url": "http://192.168.1.20/", "equipment": True, "permission": True}
    r = c.post("/api/scan", json=body, headers={"cf-connecting-ip": "203.0.113.9"})
    assert r.status_code == 400 and "own computer" in r.json()["detail"]
    r = c.post("/api/scan", json=body, headers={"host": "abc.trycloudflare.com"})
    assert r.status_code == 400 and "own computer" in r.json()["detail"]


def test_apply_fixes_only_touches_demo_files(tmp_path):
    import server
    page = "http://127.0.0.1:8000/demo-booking/"
    assert server._demo_file(page, "http://127.0.0.1:8000/demo-booking/booking.js").name == "booking.js"
    assert server._demo_file(page, "http://127.0.0.1:8000/demo-booking/../server.py") is None
    assert server._demo_file(page, "http://evil.example/demo-booking/booking.js") is None
    assert server._demo_file("https://shop.example/", "https://shop.example/app.js") is None
