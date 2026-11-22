"""Scan any public website for crashes, with nothing installed on the site.

How it works, in plain words:
  1. Open the page in a hidden browser, like a visitor would.
  2. Try things a real visitor might: click each button, leave boxes blank, type odd values.
     Every try starts from a fresh page, so each crash comes with exact steps to repeat it.
  3. Keep a copy of every script the site loads (they're public; browsers download them anyway).
  4. For each crash: find the line in the script, ask Claude for a fix, then PROVE it:
     replay the same steps twice, once with the real script (must crash) and once with the
     fixed script swapped in (must not crash). Only then is the fix marked "proven".

Safety: it skips buttons that look like they spend money, delete things or log out, never submits
forms that send data to a server, never leaves the site, and won't scan private network addresses.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import time
from urllib.parse import urljoin, urlparse

from .crashes import parse_stack
from .demo_fixes import DEMO_FIXES

ODD_VALUES = ["", "zzz", "-1"]
SKIP_WORDS = re.compile(
    r"\b(delete|remove account|pay|payment|purchase|buy|place order|order now|log ?out|sign ?out|"
    r"unsubscribe|subscribe|donate|send|book now|confirm)\b", re.I)
MAX_SCRIPT = 3_000_000
# Equipment mode (UPS, PDU, cooling and switchgear web pages): anything that sounds like it controls the device.
EQUIP_SKIP = re.compile(
    r"shut ?down|reboot|restart|reset|power ?(off|on|cycle)|turn ?(off|on)|\boff\b|\bon\b|battery ?test|self ?test|\btest\b|"
    r"calibrat|bypass|transfer|outlet|load ?(shed|off|on)|sleep|hibernate|\bwake|schedule|\bsave|apply|submit|update|upgrade|"
    r"firmware|install|delete|remove|\bclear|erase|\bformat\b|factory|default|config|setting|set ?up|\bedit\b|change|\badd\b|"
    r"enable|disable|\bstart\b|\bstop\b|\brun\b|execute|acknowledge|silence|mute|alarm ?(off|clear)|log ?out|sign ?out|"
    r"password|\busers?\b", re.I)

# Runs in the page: list the things a visitor can click or type into.
EXPLORE_JS = r"""
() => {
  const path = el => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && e !== document.body; e = e.parentElement) {
      if (e.id && document.querySelectorAll('#' + CSS.escape(e.id)).length === 1) { parts.unshift('#' + CSS.escape(e.id)); break; }
      let i = 1; for (let s = e.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === e.tagName) i++;
      parts.unshift(e.tagName.toLowerCase() + ':nth-of-type(' + i + ')');
    }
    return (parts[0] && parts[0][0] === '#' ? '' : 'body > ') + parts.join(' > ');
  };
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && !el.disabled; };
  const label = el => {
    const t = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('title') || '').trim();
    if (t) return t.replace(/\s+/g, ' ').slice(0, 60);
    const l = el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    return ((l && l.innerText) || el.getAttribute('placeholder') || el.name || el.type || el.tagName).trim().slice(0, 60);
  };
  const inLabel = el => {
    const t = (el.getAttribute('aria-label') || '').trim(); if (t) return t.slice(0, 60);
    const l = (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]')) || el.closest('label');
    const lt = l && [...l.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
    return (lt || (l && l.innerText) || el.getAttribute('placeholder') || el.name || el.type || 'text box').trim().replace(/\s+/g, ' ').slice(0, 60);
  };
  const postForm = el => { const f = el.form || el.closest('form'); return !!(f && (f.method || '').toLowerCase() === 'post'); };
  const clicks = [...document.querySelectorAll('button, [role=button], input[type=button], input[type=submit], a[href^="#"], a[href^="javascript:"], [onclick]')]
    .filter(visible).map(el => ({ sel: path(el), label: label(el), submitsPost: (el.type === 'submit') && postForm(el), group: path(el.closest('form, section, fieldset, article, div') || document.body) }));
  const inputs = [...document.querySelectorAll('input:not([type]), input[type=text], input[type=search], input[type=number], input[type=tel], input[type=email], input[type=url], textarea')]
    .filter(visible).map(el => ({ sel: path(el), label: inLabel(el), group: path(el.closest('form, section, fieldset, article, div') || document.body) }));
  const links = [...document.querySelectorAll('a[href]')].map(a => a.href);
  const navs = [...document.querySelectorAll('a[href], [role=tab]')].filter(visible)
    .filter(el => el.getAttribute('role') === 'tab' || (el.href && el.origin === location.origin && !/^(#|javascript:)/i.test(el.getAttribute('href') || '')))
    .map(el => ({ sel: path(el), label: label(el), href: el.href || '' }));
  return { clicks, inputs, links, navs };
}
"""


# Drawn on top of the page while recording a replay video: a caption, a cursor ring, and a red banner on a crash.
OVERLAY_JS = r"""
(() => {
  const mk = () => {
    if (document.getElementById('__dp')) return;
    const d = document.createElement('div'); d.id = '__dp';
    d.style.cssText = 'position:fixed;left:16px;bottom:16px;z-index:2147483647;font:600 18px system-ui,sans-serif;padding:10px 16px;border-radius:12px;background:#20264a;color:#fff;box-shadow:0 8px 24px rgba(0,0,0,.35);max-width:85vw;pointer-events:none';
    const c = document.createElement('div'); c.id = '__dpc';
    c.style.cssText = 'position:fixed;z-index:2147483647;width:30px;height:30px;margin:-15px 0 0 -15px;border-radius:50%;border:3px solid #f4a283;background:rgba(244,162,131,.35);pointer-events:none;left:-100px;top:-100px;transition:left .35s,top .35s';
    document.documentElement.append(d, c);
  };
  window.__dp = (text, kind) => { mk(); const d = document.getElementById('__dp'); d.textContent = text;
    d.style.background = kind === 'bad' ? '#c0392b' : kind === 'ok' ? '#2f8a63' : '#20264a'; };
  window.__dpCursor = (x, y) => { mk(); const c = document.getElementById('__dpc'); c.style.left = x + 'px'; c.style.top = y + 'px'; };
  window.addEventListener('error', e => setTimeout(() => window.__dp('Crashed: ' + (e.message || 'error'), 'bad'), 0));
})();
"""

# Runs in the page after it loads: the browser's own timing numbers.
PERF_JS = r"""
() => new Promise(res => {
  let lcp = 0, cls = 0;
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) lcp = e.startTime; }).observe({ type: 'largest-contentful-paint', buffered: true }); } catch (e) {}
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) cls += e.value; }).observe({ type: 'layout-shift', buffered: true }); } catch (e) {}
  setTimeout(() => {
    const n = performance.getEntriesByType('navigation')[0] || {};
    const fcp = (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime || 0;
    res({ ttfb: n.responseStart || 0, dcl: n.domContentLoadedEventEnd || 0, load: n.loadEventEnd || 0, fcp, lcp, cls });
  }, 300);
})
"""


class ScanBlocked(ValueError):
    pass


def _private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ScanBlocked(f"Couldn't find the website {host}.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def check_url(url: str, allow_private: bool = False, scheme: str = "https") -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = scheme + "://" + url        # device pages on a local network are usually plain http
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ScanBlocked("Enter a website address like example.com.")
    if not allow_private and _private(u.hostname):
        raise ScanBlocked("That address is on a private network, so it can't be scanned.")
    return url


def _step_text(step: dict) -> str:
    if step["do"] == "open":
        return f"Open {step['url']}"
    if step["do"] == "fill":
        return f"Type {json.dumps(step['value'])} into “{step['label']}”" if step["value"] else f"Leave “{step['label']}” empty"
    return f"Click “{step['label']}”"


def _spoken(step: dict) -> str:
    """A step as a spoken line, without the symbols that sound odd when read out."""
    if step["do"] == "fill":
        return f"Type {step['value']} into {step['label']}" if step["value"] else f"Leave {step['label']} empty"
    return f"Click {step.get('label') or 'the button'}"


def _top_frame(stack: str, scripts: dict) -> dict | None:
    frames = parse_stack(stack or "")
    for f in frames:
        if f.get("url") in scripts:
            return f
    return frames[0] if frames else None


def _key(err: dict) -> str:
    f = err.get("frame") or {}
    if f.get("func") or f.get("url"):
        return f"{err['type']}|{f.get('func')}|{f.get('url')}"
    return f"{err['type']}|{re.sub(r'[0-9]+', 'N', err['message'])[:120]}"


class Session:
    """One hidden browser, shared by all the tries in a scan."""

    def __init__(self, allow_private: bool, read_only: bool = False):
        self.allow_private = allow_private
        self.read_only = read_only        # equipment mode: only GET requests ever leave the browser
        self.blocked: list[str] = []
        self._host_ok: dict[str, bool] = {}

    async def __aenter__(self):
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        exe = os.environ.get("DEBUGPILOT_CHROME") or None
        self.browser = await self._pw.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        return self

    async def __aexit__(self, *a):
        await self.browser.close()
        await self._pw.stop()

    async def _guard(self, route):
        req = route.request
        if self.read_only and req.method not in ("GET", "HEAD", "OPTIONS"):
            # A POST/PUT/DELETE is how a device page saves settings or sends a command. Never let one through.
            if len(self.blocked) < 50:
                self.blocked.append(f"{req.method} {req.url[:200]}")
            return await route.abort()
        host = urlparse(route.request.url).hostname or ""
        if not self.allow_private and route.request.url.startswith("http"):
            if host not in self._host_ok:
                try:
                    self._host_ok[host] = not _private(host)
                except ScanBlocked:
                    self._host_ok[host] = False
            if not self._host_ok[host]:
                return await route.abort()
        await route.continue_()

    async def run(self, steps: list[dict], swap: dict[str, str] | None = None, scripts: dict | None = None,
                  settle: float = 0.25, video: str | None = None, tag: str = "", voice: str = "") -> dict:
        """Do the steps on a fresh page. Returns crashes, console errors and failed requests.
        `swap` replaces a script URL with different source (used to test a fix in the live page).
        `video` saves a replay of the steps to that path (.webm); `voice` ("before"/"after") adds a voice-over."""
        opts = {"viewport": {"width": 1280, "height": 720 if video else 900}}
        if video:
            import tempfile
            vdir = tempfile.mkdtemp(prefix="dpvid")
            opts.update(record_video_dir=vdir, record_video_size={"width": 1280, "height": 720})
            settle = 1.0
        ctx = await self.browser.new_context(**opts)
        if video:
            await ctx.add_init_script(OVERLAY_JS)
        errors, console, failed = [], [], []
        try:
            async def router(route):
                if swap and route.request.url in swap:
                    return await route.fulfill(status=200, content_type="application/javascript", body=swap[route.request.url],
                                               headers={"cache-control": "no-store"})
                # Pictures, video and fonts don't make a page crash, so tries skip them to load much faster.
                # (The speed check uses its own page and still downloads everything.)
                if not video and route.request.resource_type in ("image", "media", "font"):
                    return await route.abort()
                await self._guard(route)
            await ctx.route("**/*", router)
            page = await ctx.new_page()
            t_start = time.monotonic()     # the video starts about when the page is created
            cues: list[tuple[float, str]] = []
            page.on("pageerror", lambda e: errors.append({"type": e.name or "Error", "message": e.message, "stack": e.stack or ""}))
            page.on("console", lambda m: m.type == "error" and console.append(m.text[:300]))
            page.on("requestfailed", lambda r: failed.append(f"{r.url[:200]} ({r.failure})") if r.resource_type not in ("image", "media", "font") else None)
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))

            async def keep(resp):
                if scripts is None or resp.request.resource_type != "script":
                    return
                try:
                    body = await resp.text()
                except Exception:
                    return
                if len(body) <= MAX_SCRIPT:
                    scripts[resp.url] = body
            page.on("response", lambda r: asyncio.ensure_future(keep(r)))

            async def say(text, kind="", speech=""):
                """Show a caption on the video, and queue the spoken line for the voice-over."""
                if not video:
                    return 0
                try:
                    await page.evaluate("([t, k]) => window.__dp && window.__dp(t, k)", [text, kind])
                except Exception:
                    pass
                if voice and speech:
                    cues.append((time.monotonic() - t_start, speech))
                    return int((0.35 + 0.36 * len(speech.split())) * 1000)   # time to say it
                return 0

            for idx, st in enumerate(steps):
                n_before = len(errors)
                try:
                    if video and st["do"] != "open":
                        talk = await say(f"{tag} · Step {idx + 1}: {_step_text(st)}", speech=_spoken(st) + ".")
                        box = await page.locator(st["sel"]).first.bounding_box(timeout=3000)
                        if box:
                            await page.evaluate("([x, y]) => window.__dpCursor(x, y)", [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
                        await page.wait_for_timeout(max(700, talk - 300))
                    if st["do"] == "open":
                        # Wait for the page's code, then give everything else up to 4 s. Big sites keep loading
                        # extras for a long time, and the scan doesn't need to wait for all of them.
                        resp = await page.goto(st["url"], wait_until="domcontentloaded", timeout=20000)
                        if idx == 0 and resp is not None and resp.status >= 400:
                            status = resp.status
                        try:
                            await page.wait_for_load_state("load", timeout=4000)
                        except Exception:
                            pass
                        await page.wait_for_timeout(250)
                        if video:
                            intro = "Here's the site as it is." if voice == "before" else "Now the same steps, with the fix."
                            talk = await say(f"{tag} · Step 1: {_step_text(st)}", speech=intro + " First, open the page.")
                            await page.wait_for_timeout(max(900, talk))
                    elif st["do"] == "fill":
                        await page.fill(st["sel"], st["value"], timeout=3000)
                    elif st["do"] == "click":
                        await page.click(st["sel"], timeout=3000, no_wait_after=True)
                        await page.wait_for_timeout(int(settle * 1000))
                except Exception as e:  # the element went away, the page navigated, etc.
                    if st["do"] == "open":
                        return {"errors": errors, "console": console, "failed": failed, "load_error": str(e).split("\n")[0]}
                for e in errors[n_before:]:
                    e.setdefault("after_step", idx + 1)
            await page.wait_for_timeout(300)
            if video:
                if errors:
                    talk = await say(f"{tag} · Crashed ✕  {errors[0]['type']}: {errors[0]['message'][:90]}", "bad", speech="And the page crashed.")
                else:
                    talk = await say(f"{tag} · No crash ✓  The page kept working", "ok", speech="No crash. The page keeps working.")
                await page.wait_for_timeout(max(1600, talk + 600))
            return {"errors": errors, "console": console, "failed": failed, "page": page.url, "cues": cues,
                    "status": locals().get("status")}
        finally:
            vid = page.video if video and 'page' in locals() else None
            await ctx.close()
            if vid:
                try:
                    import shutil as _sh
                    os.makedirs(os.path.dirname(video), exist_ok=True)
                    _sh.move(await vid.path(), video)
                except Exception:
                    pass


async def measure(s: "Session", url: str) -> dict:
    """Load the page once with an empty cache and time everything it downloads."""
    ctx = await s.browser.new_context(viewport={"width": 1280, "height": 900})
    reqs = []
    try:
        await ctx.route("**/*", s._guard)
        page = await ctx.new_page()
        # Measure like a visitor on a phone with a 4G connection (the same setting Lighthouse uses),
        # not like this server on a fast data-center network.
        try:
            cdp = await ctx.new_cdp_session(page)
            await cdp.send("Network.enable")
            await cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 150,
                           "downloadThroughput": 1_638_400 / 8, "uploadThroughput": 750_000 / 8})
        except Exception:
            pass

        async def done(req):
            try:
                sizes = await req.sizes()
                t = req.timing
                ms = max(0.0, (t.get("responseEnd") or 0))
                reqs.append({"url": req.url, "type": req.resource_type, "ms": round(ms),
                             "bytes": int(sizes.get("responseBodySize", 0) + sizes.get("responseHeadersSize", 0))})
            except Exception:
                pass
        pending = []
        page.on("requestfinished", lambda r: pending.append(asyncio.ensure_future(done(r))))
        await page.goto(url, wait_until="load", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        timing = await page.evaluate(PERF_JS)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await ctx.close()
    return speed_report(url, timing, reqs)


def _grade(v: float, good: float, ok: float) -> str:
    return "good" if v <= good else "ok" if v <= ok else "poor"


def _mb(b: int) -> str:
    return f"{b / 1_000_000:.1f} MB" if b >= 100_000 else f"{max(1, round(b / 1000))} KB"


def _short(u: str) -> str:
    p = urlparse(u)
    path = p.path if len(p.path) < 60 else "…" + p.path[-57:]
    return (p.netloc + path) if p.netloc else u[:60]


def speed_report(url: str, t: dict, reqs: list[dict]) -> dict:
    total = sum(r["bytes"] for r in reqs)
    lcp = t.get("lcp") or t.get("fcp") or t.get("load") or 0
    metrics = [
        {"label": "Server response", "value": t["ttfb"], "unit": "ms", "grade": _grade(t["ttfb"], 800, 1800),
         "help": "Time until the server starts answering"},
        {"label": "Main content shows", "value": lcp, "unit": "ms", "grade": _grade(lcp, 2500, 4000),
         "help": "When the biggest thing on screen appears (LCP)"},
        {"label": "Fully loaded", "value": t["load"], "unit": "ms", "grade": _grade(t["load"], 3000, 6000),
         "help": "When the browser finished loading the page"},
        {"label": "Page size", "value": total, "unit": "bytes", "grade": _grade(total, 2_000_000, 5_000_000),
         "help": f"{len(reqs)} downloads in total"},
        {"label": "Layout jumps", "value": round(t.get("cls") or 0, 3), "unit": "", "grade": _grade(t.get("cls") or 0, 0.1, 0.25),
         "help": "How much things move around while loading (CLS)"},
    ]
    tips = []
    if t["ttfb"] > 800:
        tips.append({"kind": "server", "text": f"The server takes {t['ttfb'] / 1000:.1f} s just to start answering. A faster host, or saving a ready-made copy of the page (caching), would help."})
    for r in sorted(reqs, key=lambda r: -r["bytes"]):
        if r["type"] == "image" and r["bytes"] > 300_000:
            tips.append({"kind": "image", "text": f"The image {_short(r['url'])} is {_mb(r['bytes'])}. Making it smaller (for example, saving it as WebP) can cut it to a fraction of the size."})
        elif r["type"] == "script" and r["bytes"] > 300_000:
            tips.append({"kind": "script", "text": f"The code file {_short(r['url'])} is {_mb(r['bytes'])}. Making it smaller, or loading it after the page shows, would help."})
    for r in sorted(reqs, key=lambda r: -r["ms"]):
        if r["type"] in ("fetch", "xhr") and r["ms"] > 800:
            tips.append({"kind": "api", "text": f"Getting data from {_short(r['url'])} took {r['ms'] / 1000:.1f} s. Making that faster, or showing the page without waiting for it, would help."})
    if total > 2_000_000 and not any(x["kind"] == "image" for x in tips):
        tips.append({"kind": "size", "text": f"The page downloads {_mb(total)} in total. Most fast pages are under 2 MB."})
    if len(reqs) > 80:
        tips.append({"kind": "count", "text": f"The page downloads {len(reqs)} separate files. Combining some of them would cut the waiting."})
    slowest = sorted(reqs, key=lambda r: -r["ms"])[:6]
    biggest = sorted(reqs, key=lambda r: -r["bytes"])[:6]
    for lst in (slowest, biggest):
        for r in lst:
            r["name"] = _short(r["url"])
    return {"url": url, "network": "Phone on 4G (150 ms delay, 1.6 Mbps)", "metrics": metrics, "tips": tips[:8], "slowest": slowest, "biggest": biggest,
            "requests": len(reqs), "bytes": total}


def _equipment_plans(url: str, found: dict, limit: int) -> list[dict]:
    """Read-only tries for a device page: load it, then follow each in-site link or tab, one at a time.
    Nothing that sounds like a control is ever pressed, and no box is ever typed into."""
    plans = [{"why": "Load the page", "steps": [{"do": "open", "url": url}]}]
    for n in found.get("navs", []):
        if EQUIP_SKIP.search(n["label"] or "") or EQUIP_SKIP.search(urlparse(n["href"] or "").path):
            continue
        plans.append({"why": f"Open “{n['label']}”", "steps": [{"do": "open", "url": url}, {"do": "click", "sel": n["sel"], "label": n["label"]}]})
    return plans[:limit]


def _plans(url: str, found: dict, limit: int) -> list[dict]:
    """Turn what's on the page into a list of tries, simplest first."""
    clicks = [c for c in found["clicks"] if not c["submitsPost"] and not SKIP_WORDS.search(c["label"] or "")]
    plans = [{"why": "Load the page", "steps": [{"do": "open", "url": url}]}]
    for c in clicks:
        plans.append({"why": f"Click “{c['label']}”", "steps": [{"do": "open", "url": url}, {"do": "click", **c}]})
    for inp in found["inputs"]:
        near = [c for c in clicks if c["group"] == inp["group"]] or clicks[:4]
        for v in ODD_VALUES:
            for c in near[:4]:
                plans.append({"why": f"Odd input in “{inp['label']}”", "steps": [
                    {"do": "open", "url": url}, {"do": "fill", "sel": inp["sel"], "label": inp["label"], "value": v}, {"do": "click", **c}]})
    # A few two-click paths (e.g. add something to a list, then act on it).
    for a in clicks[:6]:
        for b in clicks[:6]:
            if a is not b:
                plans.append({"why": f"“{a['label']}” then “{b['label']}”", "steps": [
                    {"do": "open", "url": url}, {"do": "click", **a}, {"do": "click", **b}]})
    seen, out = set(), []
    for p in plans:
        sig = json.dumps([(s["do"], s.get("sel"), s.get("value")) for s in p["steps"]])
        if sig not in seen:
            seen.add(sig)
            out.append(p)
    return out[:limit]


def pick_pages(url: str, host: str, links: list[str], n: int, equipment: bool = False) -> list[str]:
    """Which other pages to scan. Pages *under* the one you pasted come first (paste /shop and you get
    /shop/cart before the site's Home or About), closest ones first. Then other pages on the same site."""
    start = urlparse(url)
    base = start.path if start.path.endswith("/") else start.path.rsplit("/", 1)[0] + "/"
    seen, under, other = {url.split("#")[0].rstrip("/")}, [], []
    for link in links:
        u = urlparse(link)
        clean = link.split("#")[0]
        key = clean.rstrip("/")
        if u.netloc != host or u.scheme not in ("http", "https") or key in seen:
            continue
        if re.search(r"\.(pdf|zip|png|jpe?g|gif|svg|mp4|webp)$|log-?out|sign-?out|instructions", clean, re.I):
            continue
        if equipment and EQUIP_SKIP.search(u.path + "?" + u.query):
            continue
        seen.add(key)
        if u.path.startswith(base) and len(u.path) > len(base):
            under.append((u.path.count("/"), clean))
        else:
            other.append(clean)
    under.sort(key=lambda x: x[0])
    return ([c for _, c in under] + other)[:n]


def _code_window(src: str, line: int, col: int, around: int = 40) -> tuple[str, int]:
    lines = src.split("\n")
    if 0 < line <= len(lines) and len(lines[line - 1]) > 2000:   # minified: one giant line
        s = lines[line - 1]
        a = max(0, col - 1500)
        return s[a:col + 1500], line
    a, b = max(0, line - 1 - around), min(len(lines), line + around)
    return "\n".join(f"{i + 1:>5} | {lines[i]}" for i in range(a, b)), a + 1


FIX_PROMPT = """You are DebugPilot. A website crashed for a visitor. You can see the site's public script but you don't own it,
so you suggest a fix and it will be tested by swapping your fixed script into the live page and repeating the visitor's steps.

Website: {page}
What the visitor did:
{steps}

The crash:
{etype}: {message}
{stack}

Script: {script_url} (crash at line {line}, column {col})
```
{window}
```

Reply with ONLY a JSON object:
{{
  "summary": "one plain sentence a site owner understands: what breaks for the visitor",
  "root_cause": "2-3 plain sentences: why the code crashes",
  "next_steps": ["2-4 short actions for the site owner"],
  "fix_plain": "one short sentence with no technical words: what the fix changes for the visitor",
  "old_code": "an exact, contiguous piece of the script (copied character for character, without the line-number gutter) that you are changing; must appear exactly once",
  "new_code": "the replacement for old_code",
  "confidence": 0-100
}}
Keep the change as small as possible. Don't change behavior for normal input."""


def _parse_fix(text: str) -> dict:
    a, b = text.find("{"), text.rfind("}")
    d = json.loads(text[a:b + 1])
    return {"summary": str(d.get("summary", "")), "root_cause": str(d.get("root_cause", "")),
            "next_steps": [str(x) for x in (d.get("next_steps") or [])][:5],
            "old_code": str(d.get("old_code", "")), "new_code": str(d.get("new_code", "")), "fix_plain": str(d.get("fix_plain", "")),
            "confidence": int(d.get("confidence") or 0)}


def suggest_fix(bug: dict, src: str, llm, retry_note: str = "") -> dict | None:
    f = bug["frame"] or {}
    func = f.get("func") or ""
    if llm is None:
        d = DEMO_FIXES.get(func)
        if d and src.count(d["old"]) == 1:
            a = d["answer"]
            return {"summary": a["summary"], "root_cause": a["root_cause"], "next_steps": a["next_steps"], "fix_plain": a.get("fix_plain", ""),
                    "old_code": d["old"], "new_code": d["new"], "confidence": a["confidence"], "mode": "demo"}
        return None
    window, _ = _code_window(src, f.get("line") or 1, f.get("col") or 1)
    prompt = FIX_PROMPT.format(page=bug["page"], steps="\n".join(f"{i + 1}. {s}" for i, s in enumerate(bug["steps_text"])),
                               etype=bug["type"], message=bug["message"], stack="\n".join(bug["stack"].split("\n")[:6]),
                               script_url=f.get("url"), line=f.get("line"), col=f.get("col"), window=window)
    if retry_note:
        prompt += "\n\nYour previous attempt didn't work: " + retry_note
    fix = _parse_fix(llm([{"role": "user", "content": prompt}]))
    fix["mode"] = "claude"
    return fix


async def scan(url: str, llm=None, allow_private: bool = False, max_tries: int = 80, max_pages: int = 5,
               progress=lambda msg: None, video_dir: str | None = None,
               on_found=lambda bugs: None, equipment: bool = False) -> dict:
    """equipment=True: read-only scan for device web pages (UPS, PDU, cooling, switchgear).
    It only opens pages and follows links/tabs, never types or presses buttons, and blocks every
    request that could change a setting or send a command."""
    if equipment:
        max_pages = max(max_pages, 8)
    url = check_url(url, allow_private, "http" if equipment else "https")
    t0 = time.time()
    report = {"url": url, "pages": [], "bugs": [], "console": [], "failed": [], "tries": 0, "mode": "claude" if llm else "demo",
              "equipment_mode": equipment}
    scripts: dict[str, str] = {}
    bugs: dict[str, dict] = {}

    async with Session(allow_private, read_only=equipment) as s:
        # Which pages? The start page plus a couple of same-site links.
        progress("Opening the site")
        pages = [url]
        first = await s.run([{"do": "open", "url": url}], scripts=scripts)
        if first.get("load_error"):
            raise ScanBlocked(f"The page didn't load: {first['load_error']}")
        if first.get("status"):
            code = first["status"]
            what = {404: "not found", 401: "asks for a login", 403: "blocked", 500: "a server error", 502: "a server error", 503: "unavailable"}.get(code, "an error")
            raise ScanBlocked(f"That page didn't load: the website answered with error {code} ({what}). Check the link and try again.")
        host = urlparse(first.get("page") or url).netloc
        report["console"] += first["console"]
        report["failed"] += first["failed"]

        explore_ctx = await s.browser.new_context()
        await explore_ctx.route("**/*", s._guard)
        ep = await explore_ctx.new_page()
        found_by_page = {}
        try:
            await ep.goto(url, wait_until="load", timeout=20000)
            await ep.wait_for_timeout(500)
            found = await ep.evaluate(EXPLORE_JS)
            found_by_page[url] = found
            pages += pick_pages(url, host, found["links"], max_pages - 1, equipment)
            for pu in pages[1:]:
                if pu not in found_by_page:
                    try:
                        await ep.goto(pu, wait_until="load", timeout=20000)
                        await ep.wait_for_timeout(400)
                        found_by_page[pu] = await ep.evaluate(EXPLORE_JS)
                    except Exception:
                        found_by_page[pu] = {"clicks": [], "inputs": [], "links": [], "navs": []}
        finally:
            await explore_ctx.close()
        report["pages"] = pages

        plans = []
        per_page = max(10, max_tries // max(1, len(pages)))
        for pu in pages:
            plans += (_equipment_plans if equipment else _plans)(pu, found_by_page[pu], per_page)
        plans = plans[:max_tries]

        # Run the tries 6 at a time and record crashes the moment they happen,
        # so the report page can show them while the scan is still going.
        sem = asyncio.Semaphore(8)
        done = 0

        def record(plan, r):
            report["tries"] += 1
            for e in r["errors"]:
                e["frame"] = _top_frame(e["stack"], scripts)
                k = _key(e)
                steps = plan["steps"][:e.get("after_step", len(plan["steps"]))]
                if k in bugs:
                    bugs[k]["times"] += 1
                    if len(steps) < len(bugs[k]["steps"]):        # keep the shortest way to make it crash
                        bugs[k].update(steps=steps, steps_text=[_step_text(x) for x in steps])
                    continue
                bugs[k] = {"id": len(bugs) + 1, "key": k, "type": e["type"], "message": e["message"], "stack": e["stack"],
                           "frame": e["frame"], "page": plan["steps"][0]["url"], "steps": steps,
                           "steps_text": [_step_text(x) for x in steps], "times": 1}
                on_found(list(bugs.values()))
            for c in r["console"]:
                if c not in report["console"]:
                    report["console"].append(c)
            for f in r["failed"]:
                if f not in report["failed"]:
                    report["failed"].append(f)

        async def attempt(plan):
            nonlocal done
            async with sem:
                r = await s.run(plan["steps"], scripts=scripts)
            record(plan, r)
            done += 1
            progress(f"Trying things a visitor might do ({done} of {len(plans)})")

        await asyncio.gather(*(attempt(p) for p in plans))

        # Speed check and fixing run at the same time.
        # Only the page you pasted gets a speed check: on a slow-phone connection each check
        # takes a while, and the main page is the one visitors land on.
        speed_pages = pages[:1]
        report["speed"] = [None] * len(speed_pages)

        async def speed_one(n, pu):
            try:
                report["speed"][n] = await measure(s, pu)
            except Exception as e:
                report["speed"][n] = {"url": pu, "error": str(e).split("\n")[0][:200]}

        fix_sem = asyncio.Semaphore(3)
        fixed = 0

        async def fix_one(bug):
            nonlocal fixed
            async with fix_sem:
                await _fix_and_prove(s, bug, scripts, llm, others=[b for b in bugs.values() if b is not bug])
            fixed += 1
            progress(f"Fixing crashes ({fixed} of {len(bugs)}) and checking speed")

        progress("Fixing crashes and checking speed" if bugs else "Checking speed")
        await asyncio.gather(*(speed_one(n, pu) for n, pu in enumerate(speed_pages)), *(fix_one(b) for b in bugs.values()))
        report["bugs"] = sorted(bugs.values(), key=lambda b: b["id"])

        # Replay videos. The "after" clip uses every proven fix together, so one fix's clip
        # doesn't stumble into a different, separate bug further along the same steps.
        if video_dir:
            combined: dict[str, str] = {}
            for b in report["bugs"]:
                if b.get("status") == "proven":
                    cur = combined.get(b["script"], scripts[b["script"]])
                    if cur.count(b["fix"]["old_code"]) == 1:
                        combined[b["script"]] = cur.replace(b["fix"]["old_code"], b["fix"]["new_code"], 1)
            vid_sem = asyncio.Semaphore(3)
            todo = [b for b in report["bugs"] if b.get("reproduced")]
            if todo:
                progress(f"Recording replay videos ({len(todo)})")

            async def video_one(b):
                async with vid_sem:
                    await _record(s, b, video_dir, combined)
            await asyncio.gather(*(video_one(b) for b in todo))

    report["blocked"] = s.blocked[:20] if equipment else []
    report["console"] = report["console"][:20]
    report["failed"] = report["failed"][:20]
    report["seconds"] = round(time.time() - t0, 1)
    return report


async def _record(s: Session, bug: dict, video_dir: str, combined: dict[str, str]) -> None:
    """Two short clips: the real script crashing, and (if there's a proven fix) the fixed script not crashing."""
    before = os.path.join(video_dir, f"bug{bug['id']}-before.webm")
    r = await s.run(bug["steps"], video=before, tag="BEFORE · site as it is", voice="before")
    bug["cues_before"] = [[round(t, 2), line] for t, line in r.get("cues", [])]
    if os.path.exists(before):
        bug["video_before"] = os.path.basename(before)
    if bug.get("status") == "proven" and bug["script"] in combined:
        after = os.path.join(video_dir, f"bug{bug['id']}-after.webm")
        r = await s.run(bug["steps"], swap=combined, video=after, tag="AFTER · with the fix", voice="after")
        bug["cues_after"] = [[round(t, 2), line] for t, line in r.get("cues", [])]
        if os.path.exists(after):
            bug["video_after"] = os.path.basename(after)


def _still_crashes(result: dict, bug: dict) -> bool:
    for e in result["errors"]:
        f = (_top_frame(e["stack"], {}) or {})
        bf = bug["frame"] or {}
        if f.get("func") == bf.get("func") and f.get("url") == bf.get("url") and e["type"] == bug["type"]:
            return True
        if e["message"] == bug["message"]:
            return True
    return False


async def _fix_and_prove(s: Session, bug: dict, scripts: dict, llm, others: list[dict] = ()) -> None:
    f = bug["frame"] or {}
    script_url = f.get("url")
    bug["script"] = script_url
    bug["status"] = "found"
    src = scripts.get(script_url or "")
    if not src:
        bug["note"] = "The crash happened in code I couldn't download (an inline or browser script), so there's no fix to test."
        return
    bug["window"], bug["window_start"] = _code_window(src, f.get("line") or 1, f.get("col") or 1, around=6)

    # 1. Does it crash again with the same steps? (A crash we can't repeat can't be proven fixed.)
    orig = await s.run(bug["steps"])
    bug["reproduced"] = _still_crashes(orig, bug)
    if not bug["reproduced"]:
        bug["note"] = "This crash didn't happen again when I repeated the steps, so it may depend on timing or the network."
        return

    note = ""
    for attempt in range(2 if llm else 1):
        try:
            fix = await asyncio.to_thread(suggest_fix, bug, src, llm, note)
        except Exception as e:
            bug["note"] = f"Couldn't get a fix: {e}"
            return
        if not fix:
            bug["note"] = "No fix yet. Writing fixes for this site needs the AI connected (an Anthropic API key in DebugPilot's settings)."
            return
        bug["fix"] = fix
        if not fix["old_code"] or src.count(fix["old_code"]) != 1:
            note = "old_code was not found exactly once in the script. Copy it exactly, without line numbers."
            bug["note"] = "The suggested change didn't match the script exactly."
            continue
        patched = src.replace(fix["old_code"], fix["new_code"], 1)
        after = await s.run(bug["steps"], swap={script_url: patched})
        bug["after_errors"] = [f"{e['type']}: {e['message']}" for e in after["errors"]][:5]
        if _still_crashes(after, bug):
            note = "With your change swapped in, the same steps still crash: " + "; ".join(bug["after_errors"])
            bug["note"] = "The first fix didn't stop the crash."
            continue
        new = [e for e in after["errors"] if not any(_still_crashes({"errors": [e]}, o) for o in others)]
        if new:
            note = "Your change stopped the crash but caused a new one: " + "; ".join(f"{e['type']}: {e['message']}" for e in new)
            bug["note"] = "The fix caused a different crash."
            continue
        bug["status"] = "proven"
        later = [o["id"] for o in others if _still_crashes(after, o)]
        bug["note"] = (f"With this fixed, the same steps go on to hit crash #{later[0]}, which is a separate bug." if later else "")
        return
    bug["status"] = "unproven"
