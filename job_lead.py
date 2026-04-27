import streamlit as st
import feedparser
import requests
import pandas as pd
import hashlib
import time
import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup

# ─── Page config ────────────────────────────────────────────────────────────
# st.set_page_config(
#     page_title="Job Lead Finder",
#     page_icon="🎯",
#     layout="wide",
#     initial_sidebar_state="expanded",
# )

# ─── Custom CSS ─────────────────────────────────────────────────────────────


# ─── Pipeline logic (reused from script, no CSV) ────────────────────────────

TARGET_INDUSTRIES = [
    "fintech", "healthtech", "saas", "logistics",
    "edtech", "proptech", "legal tech", "medtech",
]

SCORING_RULES = {
    "title_senior":  {"keywords": ["CTO", "VP", "Head", "Director", "Chief"], "score": 30},
    "title_tech":    {"keywords": ["Engineering", "Technical", "Software", "Platform"], "score": 15},
    "size_mid":      {"keywords": ["51-200", "201-500", "11-50"], "score": 20},
    "urgency":       {"keywords": ["immediately", "urgent", "asap", "fast-growing",
                                   "rapidly", "scaling", "series a", "series b"], "score": 25},
    "stack_match":   {"keywords": ["react", "node", "python", "django", "fastapi",
                                   "kubernetes", "aws", "typescript"], "score": 20},
    "large_corp":    {"keywords": ["fortune 500", "global enterprise", "10,000+"], "score": -20},
    "low_budget":    {"keywords": ["volunteer", "unpaid", "equity only"], "score": -50},
}


def uid(s):
    return hashlib.md5(s.encode()).hexdigest()[:12]


def fetch_linkedin(keyword, location="worldwide"):
    kw = keyword.replace(" ", "%20")
    url = (f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
           f"?keywords={kw}&location={location}&start=0")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobScanner/1.0)"}
    jobs = []
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for card in soup.find_all("div", class_="base-card"):
                title_el   = card.find("h3", class_="base-search-card__title")
                company_el = card.find("h4", class_="base-search-card__subtitle")
                loc_el     = card.find("span", class_="job-search-card__location")
                link_el    = card.find("a", class_="base-card__full-link")
                date_el    = card.find("time")
                if title_el and company_el:
                    jobs.append({
                        "id":        uid(link_el["href"] if link_el else str(title_el)),
                        "source":    "linkedin",
                        "title":     title_el.get_text(strip=True),
                        "company":   company_el.get_text(strip=True),
                        "location":  loc_el.get_text(strip=True) if loc_el else "",
                        "url":       link_el["href"].split("?")[0] if link_el else "",
                        "posted_at": date_el.get("datetime", "") if date_el else "",
                        "description": "",
                    })
    except Exception:
        pass
    return jobs


def fetch_indeed(keyword):
    kw = keyword.replace(" ", "+")
    url = f"https://www.indeed.com/rss?q={kw}&sort=date&fromage=3"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobScanner/1.0)"}
    jobs = []
    try:
        feed = feedparser.parse(url, request_headers=headers)
        for e in feed.entries:
            jobs.append({
                "id":        uid(e.get("link", e.get("title", ""))),
                "source":    "indeed",
                "title":     e.get("title", ""),
                "company":   e.get("author", ""),
                "location":  "",
                "url":       e.get("link", ""),
                "posted_at": e.get("published", ""),
                "description": BeautifulSoup(
                    e.get("summary", ""), "html.parser"
                ).get_text()[:500],
            })
    except Exception:
        pass
    return jobs


def fetch_remotive(keyword):
    jobs = []
    try:
        resp = requests.get(
            "https://remotive.com/api/remote-jobs",
            params={"search": keyword, "limit": 50},
            timeout=12,
        )
        if resp.status_code == 200:
            for j in resp.json().get("jobs", []):
                jobs.append({
                    "id":        uid(str(j.get("id", j.get("url", "")))),
                    "source":    "remotive",
                    "title":     j.get("title", ""),
                    "company":   j.get("company_name", ""),
                    "location":  j.get("candidate_required_location", "Remote"),
                    "url":       j.get("url", ""),
                    "posted_at": j.get("publication_date", ""),
                    "description": BeautifulSoup(
                        j.get("description", ""), "html.parser"
                    ).get_text()[:500],
                })
    except Exception:
        pass
    return jobs


def normalize(raw):
    seen, clean = set(), []
    for job in raw:
        if job["id"] in seen:
            continue
        seen.add(job["id"])
        job["title"]       = job["title"].strip()
        job["company"]     = job["company"].strip()
        job["description"] = re.sub(r"\s+", " ", job.get("description", "")).strip()
        clean.append(job)
    return clean


def score_job(job):
    text = (job["title"] + " " + job["company"] + " " + job["description"]).lower()
    score, matched = 0, []
    for rule_name, rule in SCORING_RULES.items():
        for kw in rule["keywords"]:
            if kw.lower() in text:
                score += rule["score"]
                matched.append(rule_name)
                break
    for ind in TARGET_INDUSTRIES:
        if ind.lower() in text:
            score += 15
            matched.append(f"industry:{ind}")
            break
    return max(0, min(100, score)), list(set(matched))


def tier(score):
    if score >= 60: return "HOT"
    if score >= 35: return "WARM"
    return "COLD"


def run_pipeline(keywords, sources, location, status_placeholder):
    all_jobs = []
    for kw in keywords:
        status_placeholder.markdown(
            f'<div class="status-line">→ fetching <b>{kw}</b>…</div>',
            unsafe_allow_html=True,
        )
        if "LinkedIn"  in sources: all_jobs += fetch_linkedin(kw, location)
        if "Indeed"    in sources: all_jobs += fetch_indeed(kw)
        if "Remotive"  in sources: all_jobs += fetch_remotive(kw)
        time.sleep(0.8)

    status_placeholder.markdown(
        f'<div class="status-line">→ normalizing {len(all_jobs)} results…</div>',
        unsafe_allow_html=True,
    )
    jobs = normalize(all_jobs)

    for job in jobs:
        job["score"], job["signals"] = score_job(job)
        job["tier"] = tier(job["score"])

    jobs.sort(key=lambda j: j["score"], reverse=True)
    status_placeholder.empty()
    return jobs

def run():
    # ─── Sidebar ────────────────────────────────────────────────────────────────
    st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}

/* Hide default streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding: 2rem 2rem 4rem; max-width: 1100px; }

/* App header */
.app-header {
    padding: 2rem 0 1.5rem;
    border-bottom: 1px solid #e5e7eb;
    margin-bottom: 2rem;
}
.app-title {
    font-family: 'DM Mono', monospace;
    font-size: 1.6rem;
    font-weight: 500;
    color: #111827;
    letter-spacing: -0.02em;
    margin: 0;
}
.app-sub {
    font-size: 0.875rem;
    color: #6b7280;
    margin-top: 0.25rem;
}

/* Tier badges */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 99px;
    font-size: 0.7rem;
    font-weight: 600;
    font-family: 'DM Mono', monospace;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}
.badge-hot  { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; }
.badge-warm { background:#fffbeb; color:#b45309; border:1px solid #fde68a; }
.badge-cold { background:#f0fdf4; color:#166534; border:1px solid #bbf7d0; }

/* Source pill */
.source-pill {
    display: inline-block;
    padding: 1px 8px;
    border-radius: 4px;
    font-size: 0.68rem;
    font-family: 'DM Mono', monospace;
    background: #f3f4f6;
    color: #374151;
    border: 1px solid #e5e7eb;
}

/* Score ring */
.score-wrap {
    text-align: center;
}
.score-num {
    font-family: 'DM Mono', monospace;
    font-size: 1.4rem;
    font-weight: 500;
    line-height: 1;
}
.score-label {
    font-size: 0.65rem;
    color: #9ca3af;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}

/* Job card */
.job-card {
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    margin-bottom: 0.75rem;
    transition: border-color 0.15s, box-shadow 0.15s;
}
.job-card:hover {
    border-color: #d1d5db;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}
.job-card.hot  { border-left: 3px solid #ef4444; }
.job-card.warm { border-left: 3px solid #f59e0b; }
.job-card.cold { border-left: 3px solid #22c55e; }

.job-title {
    font-size: 0.95rem;
    font-weight: 600;
    color: #111827;
    margin: 0 0 2px;
}
.job-company {
    font-size: 0.825rem;
    color: #4b5563;
    margin: 0;
}
.job-meta {
    font-size: 0.75rem;
    color: #9ca3af;
    margin-top: 4px;
}
.job-desc {
    font-size: 0.78rem;
    color: #6b7280;
    margin-top: 6px;
    line-height: 1.55;
}
.signal-tag {
    display: inline-block;
    background: #f0f9ff;
    color: #0369a1;
    border: 1px solid #bae6fd;
    border-radius: 4px;
    font-size: 0.65rem;
    padding: 1px 6px;
    margin: 2px 2px 0 0;
    font-family: 'DM Mono', monospace;
}

/* Stats bar */
.stats-bar {
    display: flex;
    gap: 1rem;
    padding: 0.875rem 1.25rem;
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 8px;
    margin-bottom: 1.25rem;
}
.stat-item { text-align: center; flex: 1; }
.stat-val  { font-family: 'DM Mono', monospace; font-size: 1.25rem; font-weight: 500; color: #111827; }
.stat-lbl  { font-size: 0.7rem; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em; }

/* Sidebar */
section[data-testid="stSidebar"] {
    border-right: 1px solid #e5e7eb;
}
section[data-testid="stSidebar"] .block-container {
    padding: 1.5rem 1rem;
}

/* Search button */
.stButton > button {
    background: #111827 !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 8px !important;
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
    padding: 0.6rem 1.5rem !important;
    width: 100% !important;
    transition: background 0.15s !important;
}
.stButton > button:hover {
    background: #1f2937 !important;
}

/* Empty state */
.empty-state {
    text-align: center;
    padding: 4rem 2rem;
    color: #9ca3af;
}
.empty-icon {
    font-size: 2.5rem;
    margin-bottom: 0.75rem;
}
.empty-title {
    font-size: 1rem;
    font-weight: 500;
    color: #4b5563;
    margin-bottom: 0.25rem;
}

/* Progress text */
.status-line {
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
    color: #6b7280;
    padding: 0.3rem 0;
}
</style>
""", unsafe_allow_html=True)

    st.markdown("### 🎯 Search")

    keyword_input = st.text_area(
        "Keywords",
        value="CTO\nVP Engineering\nHead of Engineering",
        height=110,
        help="One keyword per line",
    )

    location = st.text_input("Location", value="worldwide",
                            help="e.g. United States, Remote, London")

    sources = st.multiselect(
        "Sources",
        ["LinkedIn", "Indeed", "Remotive"],
        default=["LinkedIn", "Remotive"],
    )

    st.markdown("---")
    st.markdown("### 🔧 Filters")

    tier_filter = st.multiselect(
        "Show tiers",
        ["HOT", "WARM", "COLD"],
        default=["HOT", "WARM"],
    )

    min_score = st.slider("Min score", 0, 100, 30)

    st.markdown("---")
    search_btn = st.button("Search leads", use_container_width=True)

    # ─── Main area ──────────────────────────────────────────────────────────────

    st.markdown("""
    <div class="app-header">
    <p class="app-title">job lead finder</p>
    <p class="app-sub">Extract · Normalize · Score · Prioritize — no paid API</p>
    </div>
    """, unsafe_allow_html=True)

    status_box = st.empty()

    if search_btn:
        keywords = [k.strip() for k in keyword_input.splitlines() if k.strip()]
        if not keywords:
            st.warning("Enter at least one keyword.")
        elif not sources:
            st.warning("Select at least one source.")
        else:
            with st.spinner(""):
                jobs = run_pipeline(keywords, sources, location, status_box)
            st.session_state["jobs"] = jobs
            st.session_state["searched"] = True

    # ─── Results ────────────────────────────────────────────────────────────────

    if st.session_state.get("searched") and "jobs" in st.session_state:
        jobs = st.session_state["jobs"]

        # Apply filters
        filtered = [
            j for j in jobs
            if j["tier"] in tier_filter and j["score"] >= min_score
        ]

        # Stats bar
        hot_n  = sum(1 for j in jobs if j["tier"] == "HOT")
        warm_n = sum(1 for j in jobs if j["tier"] == "WARM")
        cold_n = sum(1 for j in jobs if j["tier"] == "COLD")

        st.markdown(f"""
        <div class="stats-bar">
        <div class="stat-item">
            <div class="stat-val">{len(jobs)}</div>
            <div class="stat-lbl">Total found</div>
        </div>
        <div class="stat-item">
            <div class="stat-val" style="color:#ef4444">{hot_n}</div>
            <div class="stat-lbl">Hot leads</div>
        </div>
        <div class="stat-item">
            <div class="stat-val" style="color:#f59e0b">{warm_n}</div>
            <div class="stat-lbl">Warm leads</div>
        </div>
        <div class="stat-item">
            <div class="stat-val" style="color:#22c55e">{cold_n}</div>
            <div class="stat-lbl">Cold leads</div>
        </div>
        <div class="stat-item">
            <div class="stat-val">{len(filtered)}</div>
            <div class="stat-lbl">Showing</div>
        </div>
        </div>
        """, unsafe_allow_html=True)

        if not filtered:
            st.markdown("""
            <div class="empty-state">
            <div class="empty-icon">🔍</div>
            <div class="empty-title">No results match your filters</div>
            <div>Try lowering the min score or adding more tiers</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            # Sort controls
            col_sort, col_spacer = st.columns([2, 4])
            with col_sort:
                sort_by = st.selectbox(
                    "Sort by",
                    ["Score (high → low)", "Score (low → high)", "Company A–Z"],
                    label_visibility="collapsed",
                )

            if sort_by == "Score (high → low)":
                filtered.sort(key=lambda j: j["score"], reverse=True)
            elif sort_by == "Score (low → high)":
                filtered.sort(key=lambda j: j["score"])
            elif sort_by == "Company A–Z":
                filtered.sort(key=lambda j: j["company"].lower())

            # Render cards
            for job in filtered:
                t = job["tier"].lower()
                score = job["score"]

                badge_cls = f"badge-{t}"
                card_cls  = t

                score_color = (
                    "#ef4444" if t == "hot" else
                    "#f59e0b" if t == "warm" else
                    "#22c55e"
                )

                signals_html = "".join(
                    f'<span class="signal-tag">{s}</span>'
                    for s in job.get("signals", [])
                    if s
                ) if isinstance(job.get("signals"), list) else ""

                desc = job.get("description", "")
                desc_html = (
                    f'<div class="job-desc">{desc[:220]}{"…" if len(desc) > 220 else ""}</div>'
                    if desc else ""
                )

                location_str = job.get("location", "")
                posted_str   = job.get("posted_at", "")[:10]
                meta_parts   = [p for p in [location_str, posted_str] if p]
                meta_html    = " · ".join(meta_parts) if meta_parts else ""

                url = job.get("url", "")
                link_html = (
                    f' <a href="{url}" target="_blank" style="font-size:0.72rem;color:#6366f1;'
                    f'text-decoration:none;margin-left:6px;">↗ view</a>'
                    if url else ""
                )
                html_content = f'<div style="margin-top:6px">tttt{signals_html}</div>' if signals_html else ""
                st.markdown(f"""
<div class="job-card {card_cls}">
<div style="display:flex;align-items:flex-start;gap:1rem;">
<div style="flex:1;min-width:0;">
<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:3px;">
<span class="badge {badge_cls}">{job['tier']}</span>
<span class="source-pill">{job['source']}</span>
{link_html}
</div>
<p class="job-title">{job['title']}</p>
<p class="job-company">{job['company']}</p>
{f'<p class="job-meta">{meta_html}</p>' if meta_html else ""}
{desc_html}
{html_content}
</div>
<div class="score-wrap" style="flex-shrink:0;width:52px;">
<div class="score-num" style="color:{score_color}">{score}</div>
<div class="score-label">score</div>
</div>
</div>
</div>
""", unsafe_allow_html=True)

    else:
        st.markdown("""
        <div class="empty-state">
        <div class="empty-icon">🎯</div>
        <div class="empty-title">Ready to find leads</div>
        <div>Enter keywords in the sidebar and hit Search</div>
        </div>
        """, unsafe_allow_html=True)