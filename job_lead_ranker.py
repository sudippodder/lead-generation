import streamlit as st
import feedparser
import requests
import hashlib
import time
import re
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from collections import defaultdict

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Job Lead Finder",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# SCORING ENGINE  (matches user spec exactly)
# ══════════════════════════════════════════════════════════════════════════════

# Target roles — edit to match your niche
TARGET_ROLES = [
    "marketing", "growth", "performance", "demand generation", "seo", "sem",
    "paid media", "content", "brand", "social media", "email marketing",
    "product marketing", "revenue", "operations", "ops", "strategy",
    "business development", "partnerships", "go-to-market", "gtm",
    "sales", "account", "customer success",
]

# Company growth signals
GROWTH_SIGNALS = [
    "series a", "series b", "series c", "seed round", "raised", "funding",
    "fast-growing", "rapidly scaling", "expanding", "hypergrowth",
    "recently launched", "new market", "scaling team", "growing team",
    "backed by", "venture", "recently hired", "headcount growth",
]

# JD clarity markers (good JD = specific, not vague)
CLARITY_POSITIVE = [
    "requirements", "responsibilities", "you will", "you'll", "must have",
    "nice to have", "tools", "kpi", "metrics", "roi", "budget", "manage",
    "own", "lead", "drive", "report to",
]
CLARITY_NEGATIVE = ["various duties", "other tasks", "as needed", "miscellaneous"]


def parse_date(date_str):
    """Try to parse a date string into a datetime. Returns None on failure."""
    if not date_str:
        return None
    date_str = date_str.strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    # Fallback: relative strings like "3 days ago"
    m = re.search(r"(\d+)\s*(day|hour|week|month)", date_str.lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"day": timedelta(days=n), "hour": timedelta(hours=n),
                 "week": timedelta(weeks=n), "month": timedelta(days=n*30)}.get(unit)
        if delta:
            return datetime.now(timezone.utc) - delta
    return None


def score_role_relevance(job):
    """0–3: how well the job title/desc matches target roles."""
    text = (job["title"] + " " + job["description"]).lower()
    hits = sum(1 for r in TARGET_ROLES if r in text)
    if hits == 0:   return 0, "no role match"
    if hits == 1:   return 1, "weak role match"
    if hits == 2:   return 2, "partial role match"
    return 3, "strong role match"


def score_recency(job):
    """0–2: freshness of posting."""
    dt = parse_date(job.get("posted_at", ""))
    if dt is None:
        return 1, "date unknown"
    age = (datetime.now(timezone.utc) - dt).days
    if age <= 7:    return 2, f"posted {age}d ago"
    if age <= 14:   return 1, f"posted {age}d ago"
    return 0, f"posted {age}d ago (stale)"


def score_hiring_volume(company_jobs):
    """0–2: number of open roles at this company."""
    n = len(company_jobs)
    if n >= 3:  return 2, f"{n} open roles"
    if n == 2:  return 1, "2 open roles"
    return 0, "1 open role"


def score_company_signal(job):
    """0–2 (optional): growth / scaling language."""
    text = (job["title"] + " " + job["company"] + " " + job["description"]).lower()
    hits = [s for s in GROWTH_SIGNALS if s in text]
    if len(hits) >= 2: return 2, "strong growth signals"
    if len(hits) == 1: return 1, hits[0]
    return 0, "no growth signal"


def score_jd_clarity(job):
    """0–1 (optional): specificity of job description."""
    desc = job.get("description", "").lower()
    if len(desc) < 80:
        return 0, "very short JD"
    neg = any(n in desc for n in CLARITY_NEGATIVE)
    pos = sum(1 for p in CLARITY_POSITIVE if p in desc)
    if neg:         return 0, "generic JD"
    if pos >= 4:    return 1, "clear JD"
    return 0, "vague JD"


def build_reason(role_r, recency_r, volume_r, signal_r, clarity_r, total):
    """Compose a 1-line sales-ready insight."""
    parts = []
    if recency_r[0] == 2:   parts.append(recency_r[1])
    if volume_r[0] >= 1:    parts.append(volume_r[1])
    if signal_r[0] >= 1:    parts.append(signal_r[1])
    if role_r[0] == 3:      parts.append("exact role match")
    if clarity_r[0] == 1:   parts.append("well-defined JD")
    if not parts:
        parts.append(recency_r[1])
        parts.append(role_r[1])
    return " · ".join(parts[:3])


def priority_label(score):
    if score >= 7:  return "High"
    if score >= 5:  return "Medium"
    return "Low"


def score_lead(job, company_jobs):
    """Score a job against all 5 factors. Returns enriched job dict."""
    role_r     = score_role_relevance(job)
    recency_r  = score_recency(job)
    volume_r   = score_hiring_volume(company_jobs)
    signal_r   = score_company_signal(job)
    clarity_r  = score_jd_clarity(job)

    total = role_r[0] + recency_r[0] + volume_r[0] + signal_r[0] + clarity_r[0]
    total = min(10, total)

    job["score"]    = total
    job["priority"] = priority_label(total)
    job["reason"]   = build_reason(role_r, recency_r, volume_r, signal_r, clarity_r, total)
    job["factors"]  = {
        "role_relevance":  role_r,
        "recency":         recency_r,
        "hiring_volume":   volume_r,
        "company_signal":  signal_r,
        "jd_clarity":      clarity_r,
    }
    return job


# ══════════════════════════════════════════════════════════════════════════════
# FETCHERS
# ══════════════════════════════════════════════════════════════════════════════

def uid(s):
    return hashlib.md5(s.encode()).hexdigest()[:12]

def _base(source):
    return {"source": source, "title": "", "company": "", "location": "",
            "url": "", "posted_at": "", "description": "", "salary": "", "schedule": ""}


def fetch_serpapi(keyword, location, api_key, num=30):
    jobs = []
    if not api_key:
        return jobs
    loc = location if location and location.lower() != "worldwide" else ""
    try:
        resp = requests.get("https://serpapi.com/search", timeout=20, params={
            "engine": "google_jobs", "q": keyword, "location": loc,
            "num": num, "api_key": api_key,
        })
        data = resp.json()
        if "error" in data:
            st.session_state["serp_error"] = data["error"]
            return jobs
        for j in data.get("jobs_results", []):
            exts = j.get("detected_extensions", {})
            desc = j.get("description", "")
            b = _base("serpapi")
            b.update({
                "id":          uid(j.get("job_id", j.get("title","") + j.get("company_name",""))),
                "title":       j.get("title", ""),
                "company":     j.get("company_name", ""),
                "location":    j.get("location", ""),
                "url":         j.get("share_link", ""),
                "posted_at":   exts.get("posted_at", ""),
                "description": desc[:700],
                "salary":      exts.get("salary", ""),
                "schedule":    exts.get("schedule_type", ""),
            })
            jobs.append(b)
    except Exception as e:
        st.session_state["serp_error"] = str(e)
    return jobs


def fetch_linkedin(keyword, location="worldwide"):
    kw  = keyword.replace(" ", "%20")
    url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
           "?keywords=" + kw + "&location=" + location + "&start=0")
    jobs = []
    try:
        resp = requests.get(url, timeout=12,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; JobScanner/1.0)"})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.find_all("div", class_="base-card"):
                te = card.find("h3", class_="base-search-card__title")
                ce = card.find("h4", class_="base-search-card__subtitle")
                le = card.find("span", class_="job-search-card__location")
                ae = card.find("a", class_="base-card__full-link")
                de = card.find("time")
                if te and ce:
                    b = _base("linkedin")
                    b.update({
                        "id":        uid(ae["href"] if ae else str(te)),
                        "title":     te.get_text(strip=True),
                        "company":   ce.get_text(strip=True),
                        "location":  le.get_text(strip=True) if le else "",
                        "url":       ae["href"].split("?")[0] if ae else "",
                        "posted_at": de.get("datetime","") if de else "",
                    })
                    jobs.append(b)
    except Exception:
        pass
    return jobs


def fetch_indeed(keyword):
    kw  = keyword.replace(" ", "+")
    url = "https://www.indeed.com/rss?q=" + kw + "&sort=date&fromage=14"
    jobs = []
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
        for e in feed.entries:
            b = _base("indeed")
            b.update({
                "id":          uid(e.get("link", e.get("title",""))),
                "title":       e.get("title", ""),
                "company":     e.get("author", ""),
                "url":         e.get("link", ""),
                "posted_at":   e.get("published", ""),
                "description": BeautifulSoup(e.get("summary",""), "html.parser").get_text()[:600],
            })
            jobs.append(b)
    except Exception:
        pass
    return jobs


def fetch_remotive(keyword):
    jobs = []
    try:
        resp = requests.get("https://remotive.com/api/remote-jobs",
                            params={"search": keyword, "limit": 50}, timeout=12)
        if resp.status_code == 200:
            for r in resp.json().get("jobs", []):
                b = _base("remotive")
                b.update({
                    "id":          uid(str(r.get("id", r.get("url","")))),
                    "title":       r.get("title", ""),
                    "company":     r.get("company_name", ""),
                    "location":    r.get("candidate_required_location", "Remote"),
                    "url":         r.get("url", ""),
                    "posted_at":   r.get("publication_date", ""),
                    "description": BeautifulSoup(r.get("description",""), "html.parser").get_text()[:600],
                    "salary":      r.get("salary", ""),
                })
                jobs.append(b)
    except Exception:
        pass
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

def normalize(raw):
    seen, clean = set(), []
    for job in raw:
        if job["id"] in seen:
            continue
        seen.add(job["id"])
        for f in ("title", "company", "description"):
            job[f] = re.sub(r"\s+", " ", job.get(f, "")).strip()
        clean.append(job)
    return clean


def run_pipeline(keywords, sources, location, serpapi_key, status_ph):
    raw = []
    st.session_state.pop("serp_error", None)

    for kw in keywords:
        status_ph.markdown(
            '<div class="status-line">→ fetching <b>' + kw + '</b>…</div>',
            unsafe_allow_html=True,
        )
        if "SerpApi (Google Jobs)" in sources:
            raw += fetch_serpapi(kw, location, serpapi_key)
        if "LinkedIn"  in sources: raw += fetch_linkedin(kw, location)
        if "Indeed"    in sources: raw += fetch_indeed(kw)
        if "Remotive"  in sources: raw += fetch_remotive(kw)
        time.sleep(0.6)

    status_ph.markdown(
        '<div class="status-line">→ scoring ' + str(len(raw)) + ' results…</div>',
        unsafe_allow_html=True,
    )
    jobs = normalize(raw)

    # Group by company for hiring-volume scoring
    by_company = defaultdict(list)
    for j in jobs:
        by_company[j["company"].lower()].append(j)

    for job in jobs:
        company_jobs = by_company[job["company"].lower()]
        score_lead(job, company_jobs)

    jobs.sort(key=lambda j: j["score"], reverse=True)
    status_ph.empty()
    return jobs


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
def run():
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding: 2rem 2rem 4rem; max-width: 1200px; }

.app-header { padding: 1.75rem 0 1.25rem; border-bottom: 1px solid #e5e7eb; margin-bottom: 1.75rem; }
.app-title  { font-family: 'DM Mono', monospace; font-size: 1.5rem; font-weight: 500; color: #111827; letter-spacing: -0.02em; margin: 0; }
.app-sub    { font-size: 0.825rem; color: #6b7280; margin-top: 0.2rem; }

/* Priority badges */
.badge { display:inline-block; padding:2px 10px; border-radius:99px; font-size:0.68rem;
         font-weight:600; font-family:'DM Mono',monospace; letter-spacing:0.05em; text-transform:uppercase; }
.badge-high   { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; }
.badge-medium { background:#fffbeb; color:#b45309; border:1px solid #fde68a; }
.badge-low    { background:#f8fafc; color:#64748b; border:1px solid #e2e8f0; }

/* Score breakdown pills */
.factor-pill { display:inline-flex; align-items:center; gap:4px; padding:2px 8px;
               border-radius:5px; font-size:0.65rem; font-family:'DM Mono',monospace;
               background:#f1f5f9; color:#475569; border:1px solid #e2e8f0; margin:2px 2px 0 0; }
.factor-pill.scored { background:#f0fdf4; color:#166534; border-color:#bbf7d0; }

/* Source pill */
.source-pill { display:inline-block; padding:1px 8px; border-radius:4px; font-size:0.65rem;
               font-family:'DM Mono',monospace; background:#f3f4f6; color:#374151; border:1px solid #e5e7eb; }
.source-serp { background:#faf5ff; color:#7c3aed; border-color:#e9d5ff; }

/* Lead card */
.lead-card { background:#ffffff; border:1px solid #e5e7eb; border-radius:10px;
             padding:1rem 1.25rem; margin-bottom:0.65rem; }
.lead-card.high   { border-left:3px solid #ef4444; }
.lead-card.medium { border-left:3px solid #f59e0b; }
.lead-card.low    { border-left:3px solid #cbd5e1; }

.lead-title   { font-size:0.9rem; font-weight:600; color:#111827; margin:0 0 1px; }
.lead-company { font-size:0.8rem; color:#374151; font-weight:500; margin:0; }
.lead-meta    { font-size:0.72rem; color:#9ca3af; margin-top:3px; }
.lead-reason  { font-size:0.78rem; color:#374151; background:#f9fafb; border:1px solid #e5e7eb;
                border-radius:6px; padding:6px 10px; margin-top:8px; line-height:1.5; }
.lead-reason strong { color:#111827; }

/* Score display */
.score-circle { text-align:center; }
.score-main   { font-family:'DM Mono',monospace; font-size:1.5rem; font-weight:500; line-height:1; }
.score-denom  { font-size:0.65rem; color:#9ca3af; }
.score-bar-wrap { margin-top:4px; width:48px; }
.score-bar-bg   { height:3px; background:#e5e7eb; border-radius:2px; overflow:hidden; }
.score-bar-fill { height:100%; border-radius:2px; }

/* Stats bar */
.stats-bar { display:flex; gap:0.75rem; padding:0.75rem 1.25rem; background:#f9fafb;
             border:1px solid #e5e7eb; border-radius:8px; margin-bottom:1.25rem; }
.stat-item { text-align:center; flex:1; }
.stat-val  { font-family:'DM Mono',monospace; font-size:1.15rem; font-weight:500; color:#111827; }
.stat-lbl  { font-size:0.65rem; color:#9ca3af; text-transform:uppercase; letter-spacing:0.05em; }

/* Score legend */
.legend { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:1rem; }
.legend-item { display:flex; align-items:center; gap:6px; font-size:0.75rem; color:#6b7280; }
.legend-dot  { width:8px; height:8px; border-radius:50%; flex-shrink:0; }

/* Table view */
.tbl { width:100%; border-collapse:collapse; font-size:0.8rem; }
.tbl th { text-align:left; padding:8px 12px; background:#f9fafb; border-bottom:2px solid #e5e7eb;
          font-weight:600; color:#374151; font-size:0.72rem; text-transform:uppercase;
          letter-spacing:0.04em; white-space:nowrap; }
.tbl td { padding:9px 12px; border-bottom:1px solid #f1f5f9; vertical-align:top; color:#374151; }
.tbl tr:hover td { background:#fafafa; }
.tbl td.score-td { font-family:'DM Mono',monospace; font-weight:500; text-align:center; white-space:nowrap; }

/* Sidebar */
section[data-testid="stSidebar"] {  border-right:1px solid #e5e7eb; }
section[data-testid="stSidebar"] .block-container { padding:1.5rem 1rem; }
.stButton > button { background:#111827 !important; color:#ffffff !important; border:none !important;
    border-radius:8px !important; font-family:'DM Sans',sans-serif !important; font-weight:500 !important;
    font-size:0.875rem !important; padding:0.6rem 1.5rem !important; width:100% !important; }
.stButton > button:hover { background:#1f2937 !important; }
.api-box { background:#faf5ff; border:1px solid #e9d5ff; border-radius:8px; padding:9px 12px;
           margin-bottom:8px; font-size:0.76rem; color:#6d28d9; }
.api-box a { color:#7c3aed; }
.empty-state { text-align:center; padding:3.5rem 2rem; color:#9ca3af; }
.empty-icon  { font-size:2.25rem; margin-bottom:0.75rem; }
.empty-title { font-size:0.95rem; font-weight:500; color:#4b5563; margin-bottom:0.25rem; }
.status-line { font-family:'DM Mono',monospace; font-size:0.72rem; color:#6b7280; padding:0.25rem 0; }
</style>
""", unsafe_allow_html=True)

    st.markdown("### 🎯 Search")
    keyword_input = st.text_area(
        "Keywords", height=110, help="One keyword per line",
        value="Performance Marketer\nGrowth Manager\nMarketing Operations",
    )
    location = st.text_input("Location", value="United States",
                             help="e.g. United States, Remote, London, worldwide")
    sources = st.multiselect(
        "Sources",
        ["SerpApi (Google Jobs)", "LinkedIn", "Indeed", "Remotive"],
        default=["SerpApi (Google Jobs)", "Remotive"],
    )
    serpapi_key = ""
    if "SerpApi (Google Jobs)" in sources:
        st.markdown(
            '<div class="api-box">🔑 SerpApi key required — '
            '<a href="https://serpapi.com/manage-api-key" target="_blank">get free key ↗</a>'
            '<br>100 free searches / month</div>',
            unsafe_allow_html=True,
        )
        serpapi_key = st.text_input(
            "SerpApi key", type="password",
            placeholder="paste your key here…",
            label_visibility="collapsed",
        )

    st.markdown("---")
    st.markdown("### 🔧 Filters")
    priority_filter = st.multiselect(
        "Priority", ["High", "Medium", "Low"], default=["High", "Medium"],
    )
    min_score = st.slider("Min score (/10)", 0, 10, 4)

    st.markdown("---")
    view_mode  = st.radio("View", ["Cards", "Table"], horizontal=True)
    search_btn = st.button("Search leads", use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

    st.markdown("""
    <div class="app-header">
    <p class="app-title">job lead finder</p>
    <p class="app-sub">Role Relevance · Recency · Hiring Volume · Company Signal · JD Clarity — scored /10</p>
    </div>
    """, unsafe_allow_html=True)

    status_box = st.empty()

    if search_btn:
        keywords = [k.strip() for k in keyword_input.splitlines() if k.strip()]
        if not keywords:
            st.warning("Enter at least one keyword.")
        elif not sources:
            st.warning("Select at least one source.")
        elif "SerpApi (Google Jobs)" in sources and not serpapi_key.strip():
            st.error("Paste your SerpApi key to use Google Jobs, or deselect it.")
        else:
            with st.spinner(""):
                jobs = run_pipeline(keywords, sources, location, serpapi_key.strip(), status_box)
            st.session_state["jobs"]     = jobs
            st.session_state["searched"] = True
            if st.session_state.get("serp_error"):
                st.error("SerpApi: " + st.session_state["serp_error"])


    # ══════════════════════════════════════════════════════════════════════════════
    # RESULTS
    # ══════════════════════════════════════════════════════════════════════════════
    if st.session_state.get("searched") and "jobs" in st.session_state:
        jobs = st.session_state["jobs"]
        filtered = [j for j in jobs
                    if j["priority"] in priority_filter and j["score"] >= min_score]

        high_n = sum(1 for j in jobs if j["priority"] == "High")
        med_n  = sum(1 for j in jobs if j["priority"] == "Medium")
        low_n  = sum(1 for j in jobs if j["priority"] == "Low")
        serp_n = sum(1 for j in jobs if j["source"] == "serpapi")

        # Stats bar
        st.markdown(
            '<div class="stats-bar">'
            '<div class="stat-item"><div class="stat-val">' + str(len(jobs)) + '</div><div class="stat-lbl">Total</div></div>'
            + ('<div class="stat-item"><div class="stat-val" style="color:#7c3aed">' + str(serp_n) + '</div><div class="stat-lbl">SerpApi</div></div>' if serp_n else "")
            + '<div class="stat-item"><div class="stat-val" style="color:#ef4444">' + str(high_n) + '</div><div class="stat-lbl">High</div></div>'
            '<div class="stat-item"><div class="stat-val" style="color:#f59e0b">' + str(med_n) + '</div><div class="stat-lbl">Medium</div></div>'
            '<div class="stat-item"><div class="stat-val" style="color:#94a3b8">' + str(low_n) + '</div><div class="stat-lbl">Low</div></div>'
            '<div class="stat-item"><div class="stat-val">' + str(len(filtered)) + '</div><div class="stat-lbl">Showing</div></div>'
            '</div>',
            unsafe_allow_html=True,
        )

        # Score legend
        st.markdown("""
        <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#ef4444"></div>High priority (7–10)</div>
        <div class="legend-item"><div class="legend-dot" style="background:#f59e0b"></div>Medium priority (5–6)</div>
        <div class="legend-item"><div class="legend-dot" style="background:#cbd5e1"></div>Low priority (&lt;5)</div>
        </div>
        """, unsafe_allow_html=True)

        if not filtered:
            st.markdown(
                '<div class="empty-state"><div class="empty-icon">🔍</div>'
                '<div class="empty-title">No leads match your filters</div>'
                '<div>Try lowering the min score or enabling more priority tiers</div></div>',
                unsafe_allow_html=True,
            )
        else:
            # Sort + source filter row
            c1, c2, c3 = st.columns([2, 2, 3])
            with c1:
                sort_by = st.selectbox("Sort", ["Score ↓","Score ↑","Company A–Z"],
                                    label_visibility="collapsed")
            with c2:
                avail_src = sorted({j["source"] for j in filtered})
                src_filter = st.multiselect("Source", avail_src, default=avail_src,
                                            label_visibility="collapsed")

            if sort_by == "Score ↓":    filtered.sort(key=lambda j: j["score"], reverse=True)
            elif sort_by == "Score ↑":  filtered.sort(key=lambda j: j["score"])
            else:                       filtered.sort(key=lambda j: j["company"].lower())
            if src_filter:
                filtered = [j for j in filtered if j["source"] in src_filter]

            # ── TABLE VIEW ──────────────────────────────────────────────────────
            if view_mode == "Table":
                rows = ""
                for job in filtered:
                    p     = job["priority"].lower()
                    sc    = job["score"]
                    sc_c  = score_color(sc)
                    url   = job.get("url","")
                    title_cell = ('<a href="' + url + '" target="_blank" style="color:#111827;text-decoration:none;font-weight:500;">'
                                + job["title"] + ' ↗</a>') if url else job["title"]
                    rows += (
                        "<tr>"
                        "<td>" + job["company"] + "</td>"
                        "<td>" + title_cell + "</td>"
                        "<td>" + job.get("location","") + "</td>"
                        '<td class="score-td"><span class="badge badge-' + p + '">' + job["priority"] + '</span></td>'
                        '<td class="score-td" style="color:' + sc_c + '">' + str(sc) + "/10</td>"
                        "<td style='font-size:0.75rem;color:#374151;'>" + job.get("reason","") + "</td>"
                        "</tr>"
                    )
                st.markdown(
                    '<div style="overflow-x:auto"><table class="tbl"><thead><tr>'
                    "<th>Company</th><th>Role</th><th>Location</th>"
                    "<th>Priority</th><th>Score</th><th>Reason</th>"
                    "</tr></thead><tbody>" + rows + "</tbody></table></div>",
                    unsafe_allow_html=True,
                )

            # ── CARD VIEW ────────────────────────────────────────────────────────
            else:
                for job in filtered:
                    p       = job["priority"].lower()
                    sc      = job["score"]
                    sc_c    = score_color(sc)
                    factors = job.get("factors", {})
                    url     = job.get("url","")

                    # Factor pills
                    factor_labels = {
                        "role_relevance": "role", "recency": "recency",
                        "hiring_volume": "volume", "company_signal": "signal",
                        "jd_clarity": "clarity",
                    }
                    pills_html = ""
                    for fk, flabel in factor_labels.items():
                        fval, ftxt = factors.get(fk, (0,""))
                        cls = "factor-pill scored" if fval > 0 else "factor-pill"
                        pills_html += (
                            '<span class="' + cls + '">'
                            + flabel + "&nbsp;" + str(fval)
                            + ("/" + ("3" if fk=="role_relevance" else "2" if fk!="jd_clarity" else "1"))
                            + ("&nbsp;·&nbsp;" + ftxt if ftxt else "")
                            + "</span>"
                        )

                    src_cls    = "source-serp" if job["source"] == "serpapi" else ""
                    link_html  = ('<a href="' + url + '" target="_blank" style="font-size:0.7rem;color:#6366f1;text-decoration:none;margin-left:6px;">↗ view job</a>') if url else ""
                    salary_html= ('<span style="font-size:0.7rem;color:#059669;font-weight:500;margin-left:8px;">' + job["salary"] + "</span>") if job.get("salary") else ""
                    meta_parts = [p2 for p2 in [job.get("location",""), job.get("posted_at","")[:10]] if p2]
                    meta_html  = " · ".join(meta_parts)

                    st.markdown(
                        '<div class="lead-card ' + p + '">'
                        '<div style="display:flex;align-items:flex-start;gap:1rem;">'
                        '<div style="flex:1;min-width:0;">'
                        '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:5px;">'
                        '<span class="badge badge-' + p + '">' + job["priority"] + ' Priority</span>'
                        '<span class="source-pill ' + src_cls + '">' + job["source"] + "</span>"
                        + salary_html + link_html
                        + "</div>"
                        '<p class="lead-title">' + job["title"] + "</p>"
                        '<p class="lead-company">' + job["company"] + "</p>"
                        + ('<p class="lead-meta">' + meta_html + "</p>" if meta_html else "")
                        + '<div style="margin-top:7px">' + pills_html + "</div>"
                        + '<div class="lead-reason"><strong>Why this lead:</strong> ' + job.get("reason","") + "</div>"
                        + "</div>"
                        '<div class="score-circle" style="flex-shrink:0;width:56px;">'
                        '<div class="score-main" style="color:' + sc_c + '">' + str(sc) + "</div>"
                        '<div class="score-denom">/10</div>'
                        '<div class="score-bar-wrap"><div class="score-bar-bg">'
                        '<div class="score-bar-fill" style="width:' + bar_pct(sc) + ';background:' + sc_c + ';"></div>'
                        "</div></div>"
                        "</div></div></div>",
                        unsafe_allow_html=True,
                    )

    else:
        st.markdown(
            '<div class="empty-state"><div class="empty-icon">🎯</div>'
            '<div class="empty-title">Ready to find leads</div>'
            '<div>Enter keywords in the sidebar and hit Search</div></div>',
            unsafe_allow_html=True,
        )



def score_color(score):
    if score >= 7: return "#ef4444"
    if score >= 5: return "#f59e0b"
    return "#94a3b8"

def bar_pct(score):
    return str(round(score / 10 * 100)) + "%"


