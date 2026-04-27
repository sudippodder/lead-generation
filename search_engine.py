"""
search_engine.py
================
Playwright-based Google → LinkedIn job scraper.
Bypasses Google's bot detection by rendering pages in a real Chromium browser.

Usage (called from 1_Search.py run_pipeline):
    from search_engine import fetch_jobs_playwright

    jobs = fetch_jobs_playwright(
        queries=["SEO manager startup UK remote"],
        num_results=10,
    )
"""

import asyncio
import hashlib
import re
import sys
import threading
from bs4 import BeautifulSoup


# ── Stealth context helper ────────────────────────────────────────────────────

async def make_stealth_context(playwright):
    """
    Launch Chromium with stealth settings that minimise bot-detection signals.
    Returns (browser, context).
    """
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--disable-gpu",
            "--window-size=1280,800",
        ],
    )
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    # Remove webdriver fingerprint
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins',   { get: () => [1,2,3,4,5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
        window.chrome = { runtime: {} };
    """)
    # Block heavy assets — faster and smaller fingerprint surface
    await context.route(
        "**/*.{png,jpg,jpeg,gif,webp,mp4,woff,woff2,ttf,svg,ico}",
        lambda route: route.abort(),
    )
    return browser, context


# ── Stage 1: Google search → LinkedIn URLs ────────────────────────────────────

async def _google_search_playwright(page, query: str, num_results: int) -> list:
    """
    Navigate to Google, run the search, and extract LinkedIn job URLs.
    Returns a list of clean linkedin.com/jobs URLs.
    """
    search_url = (
        "https://www.google.com/search"
        f"?q={query.replace(' ', '+')}&hl=en&gl=us&num={min(num_results * 2, 20)}"
    )
    print(f"[Google] {search_url}")

    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"[Google] Navigation error: {e}")
        return []

    # Dismiss consent popups if present
    for btn_text in ["Accept all", "I agree", "Agree", "Accept"]:
        try:
            await page.click(f"button:has-text('{btn_text}')", timeout=2000)
            await asyncio.sleep(1)
            break
        except Exception:
            pass

    # Human-like pause
    await asyncio.sleep(2)

    html = await page.content()

    # Detect CAPTCHA / block page
    if "unusual traffic" in html.lower() or "captcha" in html.lower():
        print("[Google] CAPTCHA / block detected. Waiting before retry…")
        await asyncio.sleep(15)
        return []

    # Extract LinkedIn job URLs
    soup  = BeautifulSoup(html, "html.parser")
    found = []
    seen  = set()

    for a in soup.select("a[href]"):
        href = a.get("href", "")

        # Unwrap Google redirect  /url?q=https://...
        if "/url?" in href:
            m = re.search(r"[?&]q=([^&]+)", href)
            if m:
                from urllib.parse import unquote
                href = unquote(m.group(1))

        if "linkedin.com/jobs" in href and href.startswith("http"):
            clean = href.split("?")[0].rstrip("/")
            if clean not in seen:
                seen.add(clean)
                found.append(clean)

    print(f"[Google] Found {len(found)} LinkedIn URLs")
    return found[:num_results]


# ── Stage 2: LinkedIn job page → full details ─────────────────────────────────

async def _scrape_linkedin_page(page, url: str, query: str = "") -> dict:
    """
    Visit a LinkedIn job page and extract all fields.
    Returns a job dict or None if the page is empty / blocked.
    """
    print(f"[LinkedIn] {url}")
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(1.5)

        # Expand "Show more" description if present
        try:
            await page.click(
                "button.show-more-less-html__button--more",
                timeout=2000,
            )
            await asyncio.sleep(0.8)
        except Exception:
            pass

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        def _sel(selector):
            el = soup.select_one(selector)
            return el.get_text(strip=True) if el else ""

        # Title
        title = (
            _sel("h1.top-card-layout__title") or
            _sel("h1.jobs-unified-top-card__job-title") or
            _sel("h1.t-24.t-bold") or
            _sel("h1")
        )
        # Company
        company = (
            _sel("a.topcard__org-name-link") or
            _sel("a[data-tracking-control-name='public_jobs_topcard-org-name']") or
            _sel("div.job-details-jobs-unified-top-card__company-name a") or
            _sel(".topcard__flavor--metadata")
        )
        # Location
        location = ""
        for sel in [
            "span.topcard__flavor--bullet",
            "span.job-details-jobs-unified-top-card__bullet",
            "span[class*='location']",
        ]:
            el = soup.select_one(sel)
            if el:
                t = el.get_text(strip=True)
                if t and len(t) > 2:
                    location = t
                    break

        # Posted date
        posted = ""
        for el in soup.select("span.posted-time-ago__text, time, span[class*='posted']"):
            t = el.get("datetime") or el.get_text(strip=True)
            if t and any(w in t.lower() for w in ["ago", "week", "day", "month", "hour", "just"]):
                posted = t
                break

        # Description
        description = ""
        for sel in [
            "div.description__text",
            "div.show-more-less-html__markup",
            "div#job-details",
            "section.description div",
        ]:
            el = soup.select_one(sel)
            if el:
                description = el.get_text(separator=" ", strip=True)[:1500]
                break

        if not title and not company:
            print(f"[LinkedIn] Empty page — skipping {url}")
            return None

        # Stable ID from URL or hash
        id_match = re.search(r"/view/(\d+)", url)
        job_id   = id_match.group(1) if id_match else hashlib.md5(url.encode()).hexdigest()[:12]

        return {
            "id":          job_id,
            "source":      "google→linkedin",
            "title":       title.strip(),
            "company":     company.strip(),
            "location":    location.strip(),
            "url":         url,
            "posted_at":   posted,
            "description": description,
            "salary":      "",
            "schedule":    "",
            "query":       query,
        }

    except Exception as e:
        print(f"[LinkedIn] Error scraping {url}: {e}")
        return None


# ── Main async orchestrator ───────────────────────────────────────────────────

async def _run_playwright(queries: list, num_results: int) -> list:
    """
    Full async pipeline:
      1. For each query  → Google (Playwright) → collect LinkedIn URLs
      2. For each URL    → scrape LinkedIn job page → collect job dict
    """
    from playwright.async_api import async_playwright

    all_url_pairs = []   # list of (url, query)
    seen_urls     = set()
    jobs          = []

    async with async_playwright() as pw:
        browser, context = await make_stealth_context(pw)
        page = await context.new_page()

        # Stage 1 — Google search for every query
        for query in queries:
            scoped = query if "site:" in query.lower() else "site:linkedin.com/jobs " + query
            print(scoped)
            #site:linkedin.com/jobs growth marketing hiring urgently
            urls   = await _google_search_playwright(page, scoped, num_results)
            for u in urls:
                if u not in seen_urls:
                    seen_urls.add(u)
                    all_url_pairs.append((u, query))
            await asyncio.sleep(3)   # polite gap between Google searches

        print(f"[Pipeline] {len(all_url_pairs)} unique LinkedIn URLs to scrape")

        # Stage 2 — scrape each LinkedIn page
        for i, (url, query) in enumerate(all_url_pairs):
            job = await _scrape_linkedin_page(page, url, query)
            if job:
                jobs.append(job)
            await asyncio.sleep(1.5)
            if (i + 1) % 5 == 0:
                print(f"[Pipeline] Scraped {i+1}/{len(all_url_pairs)}")

        await browser.close()

    return jobs


# ── Thread wrapper (makes async work inside Streamlit) ────────────────────────

def _run_in_thread(queries, num_results, container):
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    else:
        loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        container["results"] = loop.run_until_complete(
            _run_playwright(queries, num_results)
        )
    except Exception as e:
        container["error"] = str(e)
    finally:
        loop.close()


def fetch_jobs_playwright(queries: list, num_results: int = 10) -> list:
    """
    Public entry point — called from run_pipeline in 1_Search.py.

    queries     : list of search strings — site:linkedin.com/jobs prepended automatically
    num_results : max LinkedIn pages to fetch per query

    Returns a list of job dicts compatible with normalize() → hard_filter() → score_lead().
    Raises RuntimeError on critical failure.
    """
    if not queries:
        return []

    container = {}
    t = threading.Thread(
        target=_run_in_thread,
        args=(queries, num_results, container),
        daemon=True,
    )
    t.start()
    t.join(timeout=300)   # 5-minute hard timeout

    if "error" in container:
        raise RuntimeError(container["error"])
    return container.get("results", [])


# ── Backward-compat stubs ─────────────────────────────────────────────────────
def _google_search(*args, **kwargs):
    raise NotImplementedError("Use fetch_jobs_playwright(queries, num_results)")
