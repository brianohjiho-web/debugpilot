"""Scanner: finds crashes on a site with nothing installed, and only calls a fix 'proven' after the live replay."""
import asyncio, functools, http.server, json, os, shutil, threading
from pathlib import Path

import pytest

from agent import scanner

CHROME = os.environ.get("DEBUGPILOT_CHROME") or shutil.which("google-chrome")
needs_browser = pytest.mark.skipif(not CHROME, reason="needs Chrome")
SITE = Path(__file__).parent / "other-site"


def test_private_addresses_are_blocked():
    for u in ("http://127.0.0.1/", "http://192.168.0.1", "localhost:8000"):
        with pytest.raises(scanner.ScanBlocked):
            scanner.check_url(u)
    assert scanner.check_url("127.0.0.1:8000", allow_private=True) == "https://127.0.0.1:8000"


@pytest.fixture()
def site():
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    h.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def fake_llm(old, new):
    def llm(messages):
        assert "lookup" in messages[0]["content"]
        return json.dumps({"summary": "Searching an unknown word crashes.", "root_cause": "TAGS[word] is undefined.",
                           "next_steps": ["Guard the lookup"], "old_code": old, "new_code": new, "confidence": 90})
    return llm


@needs_browser
def test_finds_and_proves_fix_on_a_site_it_doesnt_own(site, monkeypatch):
    monkeypatch.setenv("DEBUGPILOT_CHROME", CHROME)
    llm = fake_llm("return TAGS[word].join(\", \");", "return (TAGS[word] || []).join(\", \");")
    r = asyncio.run(scanner.scan(site, llm=llm, allow_private=True, max_tries=20))
    assert len(r["bugs"]) == 1
    b = r["bugs"][0]
    assert b["frame"]["func"] == "lookup" and b["reproduced"]
    assert b["status"] == "proven"
    assert any("Search" in s for s in b["steps_text"])


@needs_browser
def test_a_fix_that_doesnt_work_is_not_called_proven(site, monkeypatch):
    monkeypatch.setenv("DEBUGPILOT_CHROME", CHROME)
    llm = fake_llm("return TAGS[word].join(\", \");", "return TAGS[word].join(\" / \");")  # still crashes
    r = asyncio.run(scanner.scan(site, llm=llm, allow_private=True, max_tries=20))
    assert r["bugs"][0]["status"] == "unproven"


def test_speed_report_flags_big_images_and_slow_apis():
    t = {"ttfb": 1200, "dcl": 2000, "load": 7000, "fcp": 1500, "lcp": 5200, "cls": 0.02}
    reqs = [{"url": "https://s.com/hero.png", "type": "image", "ms": 6000, "bytes": 1_500_000},
            {"url": "https://s.com/api/recs", "type": "fetch", "ms": 1400, "bytes": 900},
            {"url": "https://s.com/app.js", "type": "script", "ms": 200, "bytes": 20_000}]
    r = scanner.speed_report("https://s.com/", t, reqs)
    grades = {m["label"]: m["grade"] for m in r["metrics"]}
    assert grades["Server response"] == "ok" and grades["Main content shows"] == "poor" and grades["Page size"] == "good"
    kinds = [x["kind"] for x in r["tips"]]
    assert kinds[:3] == ["server", "image", "api"]
    assert r["slowest"][0]["name"] == "s.com/hero.png"


@needs_browser
def test_scan_runs_speed_check_and_can_still_record_replays(site, monkeypatch, tmp_path):
    monkeypatch.setenv("DEBUGPILOT_CHROME", CHROME)
    llm = fake_llm("return TAGS[word].join(\", \");", "return (TAGS[word] || []).join(\", \");")
    r = asyncio.run(scanner.scan(site, llm=llm, allow_private=True, max_tries=10, video_dir=str(tmp_path)))
    b = r["bugs"][0]
    assert b["video_before"] and b["video_after"]
    assert (tmp_path / b["video_before"]).stat().st_size > 10_000
    assert r["speed"][0]["metrics"] and r["speed"][0]["requests"] >= 2


EQUIP = Path(__file__).parent / "equipment-site"


@pytest.fixture()
def device():
    """A pretend UPS web page that records every request it gets."""
    seen = []

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(EQUIP), **k)

        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append(("GET", self.path))
            return super().do_GET()

        def do_POST(self):
            seen.append(("POST", self.path))
            self.send_response(200); self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_port}/index.html", seen
    srv.shutdown()


def test_equipment_skip_words():
    for safe in ("Status", "Battery", "Battery Runtime", "Event Log", "Input Voltage", "Information", "IP Address"):
        assert not scanner.EQUIP_SKIP.search(safe), safe
    for control in ("Shutdown", "Turn Off UPS", "Run Self Test", "Outlet Control", "Settings", "Reboot", "Clear Alarms", "/cgi/shutdown"):
        assert scanner.EQUIP_SKIP.search(control), control


@needs_browser
def test_equipment_mode_is_read_only_and_still_finds_crashes(device, monkeypatch):
    monkeypatch.setenv("DEBUGPILOT_CHROME", CHROME)
    url, seen = device
    llm = lambda m: json.dumps({"summary": "The battery page breaks when the temperature sensor sends no reading.",
                                "root_cause": "temp is null.", "next_steps": ["Show a dash when there's no reading"],
                                "old_code": 'r.temp.toFixed(1) + " C"', "new_code": '(r.temp == null ? "--" : r.temp.toFixed(1) + " C")',
                                "fix_plain": "If the sensor sends nothing, the page shows a dash instead of breaking.", "confidence": 90})
    r = asyncio.run(scanner.scan(url, llm=llm, allow_private=True, equipment=True))
    assert r["equipment_mode"]
    assert r["url"].startswith("http://")
    # Nothing that changes the device was sent or opened.
    assert not [s for s in seen if s[0] != "GET"], seen
    assert not [p for _, p in seen if "shutdown" in p or "outlets" in p], seen
    # A page that tries to send something by itself is stopped, and the report says so.
    assert any("ack-events" in x for x in r["blocked"]), r["blocked"]
    # Only pages and links were used: no typing, no buttons.
    for b in r["bugs"]:
        assert all(st["do"] in ("open", "click") for st in b["steps"])
    # It still found the real problem on the battery page, and proved the fix.
    bat = [b for b in r["bugs"] if (b["frame"] or {}).get("func") == "showTemp"]
    assert bat and bat[0]["status"] == "proven"


@needs_browser
@pytest.mark.parametrize("folder", ["demo-booking", "demo-power"])
def test_demo_sites_give_three_proven_fixes(folder, monkeypatch):
    monkeypatch.setenv("DEBUGPILOT_CHROME", CHROME)
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(Path(__file__).parents[1] / folder))
    h.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = asyncio.run(scanner.scan(f"http://127.0.0.1:{srv.server_port}/", allow_private=True))
    finally:
        srv.shutdown()
    assert len(r["bugs"]) == 3
    assert all(b["status"] == "proven" for b in r["bugs"]), [(b["message"], b.get("note")) for b in r["bugs"]]
