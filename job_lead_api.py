import streamlit as st
import feedparser
import requests
import hashlib
import time
import re
from bs4 import BeautifulSoup

st.set_page_config(
    page_title="Job Lead Finder",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Scoring config ────────────────────────────────────────────────────────────
TARGET_INDUSTRIES = [
    "fintech", "healthtech", "saas", "logistics",
    "edtech", "proptech", "legal tech", "medtech",
]
SCORING_RULES = {
    "title_senior": {"keywords": ["CTO", "VP", "Head", "Director", "Chief"],          "score": 30},
    "title_tech":   {"keywords": ["Engineering", "Technical", "Software", "Platform"], "score": 15},
    "size_mid":     {"keywords": ["51-200", "201-500", "11-50"],                       "score": 20},
    "urgency":      {"keywords": ["immediately", "urgent", "asap", "fast-growing",
                                  "rapidly", "scaling", "series a", "series b"],       "score": 25},
    "stack_match":  {"keywords": ["react", "node", "python", "django", "fastapi",
                                  "kubernetes", "aws", "typescript"],                  "score": 20},
    "large_corp":   {"keywords": ["fortune 500", "global enterprise", "10,000+"],     "score": -20},
    "low_budget":   {"keywords": ["volunteer", "unpaid", "equity only"],              "score": -50},
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def uid(s):
    return hashlib.md5(s.encode()).hexdigest()[:12]

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
        if ind in text:
            score += 15
            matched.append("industry:" + ind)
            break
    return max(0, min(100, score)), list(set(matched))

def tier(score):
    if score >= 60: return "HOT"
    if score >= 35: return "WARM"
    return "COLD"

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

def _empty_job(source):
    return {"source": source, "title": "", "company": "", "location": "",
            "url": "", "posted_at": "", "description": "", "salary": "", "schedule": ""}

# ── Fetchers ──────────────────────────────────────────────────────────────────
def fetch_serpapi(keyword, location, api_key, num=30):
    jobs = []
    if not api_key:
        return jobs
    loc = location if location and location.lower() != "worldwide" else ""
    params = {"engine": "google_jobs", "q": keyword, "location": loc,
              "num": num, "api_key": api_key}
    try:
        resp = requests.get("https://serpapi.com/search", params=params, timeout=20)
        data = resp.json()
        if "error" in data:
            st.session_state["serp_error"] = data["error"]
            return jobs
        for j in data.get("jobs_results", []):
            exts = j.get("detected_extensions", {})
            desc = j.get("description", "")
            if exts.get("salary"):
                desc += " " + exts["salary"]
            jobs.append({
                "id":          uid(j.get("job_id", j.get("title","") + j.get("company_name",""))),
                "source":      "serpapi",
                "title":       j.get("title", ""),
                "company":     j.get("company_name", ""),
                "location":    j.get("location", ""),
                "url":         j.get("share_link", ""),
                "posted_at":   exts.get("posted_at", ""),
                "description": desc[:600],
                "salary":      exts.get("salary", ""),
                "schedule":    exts.get("schedule_type", ""),
            })
    except Exception as e:
        st.session_state["serp_error"] = str(e)
    return jobs

def fetch_linkedin(keyword, location="worldwide"):
    kw = keyword.replace(" ", "%20")
    url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
           "?keywords=" + kw + "&location=" + location + "&start=0")
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
                    j = _empty_job("linkedin")
                    j.update({
                        "id":        uid(link_el["href"] if link_el else str(title_el)),
                        "title":     title_el.get_text(strip=True),
                        "company":   company_el.get_text(strip=True),
                        "location":  loc_el.get_text(strip=True) if loc_el else "",
                        "url":       link_el["href"].split("?")[0] if link_el else "",
                        "posted_at": date_el.get("datetime", "") if date_el else "",
                    })
                    jobs.append(j)
    except Exception:
        pass
    return jobs

def fetch_indeed(keyword):
    kw = keyword.replace(" ", "+")
    url = "https://www.indeed.com/rss?q=" + kw + "&sort=date&fromage=3"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobScanner/1.0)"}
    jobs = []
    try:
        feed = feedparser.parse(url, request_headers=headers)
        for e in feed.entries:
            j = _empty_job("indeed")
            j.update({
                "id":          uid(e.get("link", e.get("title", ""))),
                "title":       e.get("title", ""),
                "company":     e.get("author", ""),
                "url":         e.get("link", ""),
                "posted_at":   e.get("published", ""),
                "description": BeautifulSoup(e.get("summary",""), "html.parser").get_text()[:500],
            })
            jobs.append(j)
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
                j = _empty_job("remotive")
                j.update({
                    "id":          uid(str(r.get("id", r.get("url","")))),
                    "title":       r.get("title", ""),
                    "company":     r.get("company_name", ""),
                    "location":    r.get("candidate_required_location", "Remote"),
                    "url":         r.get("url", ""),
                    "posted_at":   r.get("publication_date", ""),
                    "description": BeautifulSoup(r.get("description",""), "html.parser").get_text()[:500],
                    "salary":      r.get("salary", ""),
                })
                jobs.append(j)
    except Exception:
        pass
    return jobs

# ── Pipeline ──────────────────────────────────────────────────────────────────
def run_pipeline(keywords, sources, location, serpapi_key, status_ph):
    all_jobs = []
    st.session_state.pop("serp_error", None)
    for kw in keywords:
        status_ph.markdown(
            '<div class="status-line">→ fetching <b>' + kw + '</b>…</div>',
            unsafe_allow_html=True,
        )
        if "SerpApi (Google Jobs)" in sources:
            all_jobs += fetch_serpapi(kw, location, serpapi_key)
        if "LinkedIn" in sources:
            all_jobs += fetch_linkedin(kw, location)
        if "Indeed" in sources:
            all_jobs += fetch_indeed(kw)
        if "Remotive" in sources:
            all_jobs += fetch_remotive(kw)
        time.sleep(0.6)
    status_ph.markdown(
        '<div class="status-line">→ normalizing ' + str(len(all_jobs)) + ' results…</div>',
        unsafe_allow_html=True,
    )
    jobs = normalize(all_jobs)
    for job in jobs:
        job["score"], job["signals"] = score_job(job)
        job["tier"] = tier(job["score"])
    jobs.sort(key=lambda j: j["score"], reverse=True)
    status_ph.empty()
    return jobs

# ── Sidebar ───────────────────────────────────────────────────────────────────
def run():
    
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    #MainMenu, footer, header { visibility: hidden; }
    .block-container { padding: 2rem 2rem 4rem; max-width: 1100px; }
    .app-header { padding: 2rem 0 1.5rem; border-bottom: 1px solid #e5e7eb; margin-bottom: 2rem; }
    .app-title  { font-family: 'DM Mono', monospace; font-size: 1.6rem; font-weight: 500; color: #111827; letter-spacing: -0.02em; margin: 0; }
    .app-sub    { font-size: 0.875rem; color: #6b7280; margin-top: 0.25rem; }
    .badge { display:inline-block; padding:2px 10px; border-radius:99px; font-size:0.7rem; font-weight:600; font-family:'DM Mono',monospace; letter-spacing:0.05em; text-transform:uppercase; }
    .badge-hot  { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; }
    .badge-warm { background:#fffbeb; color:#b45309; border:1px solid #fde68a; }
    .badge-cold { background:#f0fdf4; color:#166534; border:1px solid #bbf7d0; }
    .source-pill { display:inline-block; padding:1px 8px; border-radius:4px; font-size:0.68rem; font-family:'DM Mono',monospace; background:#f3f4f6; color:#374151; border:1px solid #e5e7eb; }
    .source-serp { background:#faf5ff; color:#7c3aed; border-color:#e9d5ff; }
    .score-wrap  { text-align:center; }
    .score-num   { font-family:'DM Mono',monospace; font-size:1.4rem; font-weight:500; line-height:1; }
    .score-label { font-size:0.65rem; color:#9ca3af; text-transform:uppercase; letter-spacing:0.06em; }
    .job-card { background:#ffffff; border:1px solid #e5e7eb; border-radius:10px; padding:1rem 1.25rem; margin-bottom:0.75rem; }
    .job-card.hot  { border-left:3px solid #ef4444; }
    .job-card.warm { border-left:3px solid #f59e0b; }
    .job-card.cold { border-left:3px solid #22c55e; }
    .job-title   { font-size:0.95rem; font-weight:600; color:#111827; margin:0 0 2px; }
    .job-company { font-size:0.825rem; color:#4b5563; margin:0; }
    .job-meta    { font-size:0.75rem; color:#9ca3af; margin-top:4px; }
    .job-desc    { font-size:0.78rem; color:#6b7280; margin-top:6px; line-height:1.55; }
    .signal-tag  { display:inline-block; background:#f0f9ff; color:#0369a1; border:1px solid #bae6fd; border-radius:4px; font-size:0.65rem; padding:1px 6px; margin:2px 2px 0 0; font-family:'DM Mono',monospace; }
    .stats-bar  { display:flex; gap:1rem; padding:0.875rem 1.25rem; background:#f9fafb; border:1px solid #e5e7eb; border-radius:8px; margin-bottom:1.25rem; }
    .stat-item  { text-align:center; flex:1; }
    .stat-val   { font-family:'DM Mono',monospace; font-size:1.25rem; font-weight:500; color:#111827; }
    .stat-lbl   { font-size:0.7rem; color:#9ca3af; text-transform:uppercase; letter-spacing:0.05em; }
    section[data-testid="stSidebar"] {  border-right:1px solid #e5e7eb; }
    section[data-testid="stSidebar"] .block-container { padding:1.5rem 1rem; }
    .stButton > button { background:#111827 !important; color:#ffffff !important; border:none !important; border-radius:8px !important; font-family:'DM Sans',sans-serif !important; font-weight:500 !important; font-size:0.875rem !important; padding:0.6rem 1.5rem !important; width:100% !important; }
    .stButton > button:hover { background:#1f2937 !important; }
    .api-box { background:#faf5ff; border:1px solid #e9d5ff; border-radius:8px; padding:10px 12px; margin-bottom:8px; font-size:0.78rem; color:#6d28d9; }
    .api-box a { color:#7c3aed; }
    .empty-state { text-align:center; padding:4rem 2rem; color:#9ca3af; }
    .empty-icon  { font-size:2.5rem; margin-bottom:0.75rem; }
    .empty-title { font-size:1rem; font-weight:500; color:#4b5563; margin-bottom:0.25rem; }
    .status-line { font-family:'DM Mono',monospace; font-size:0.75rem; color:#6b7280; padding:0.3rem 0; }
    </style>
    """, unsafe_allow_html=True)
    st.markdown("### 🎯 Search")

    keyword_input = st.text_area(
        "Keywords", value="CTO\nVP Engineering\nHead of Engineering",
        height=110, help="One keyword per line",
    )
    location = st.text_input("Location", value="United States",
                             help="e.g. United States, Remote, London, worldwide")
    sources = st.multiselect(
        "Sources",
        ["SerpApi (Google Jobs)", "LinkedIn", "Indeed", "Remotive"],
        default=["SerpApi (Google Jobs)", "Remotive"],
    )

    serpapi_key = "25541839543f37f0fdda2044e1acdcd2b8ab197eecfab88b492a8a1d7052ae26"
    if "SerpApi (Google Jobs)" in sources:
        st.markdown(
            '<div class="api-box">🔑 SerpApi key required &mdash; '
            '<a href="https://serpapi.com/manage-api-key" target="_blank">get free key ↗</a>'
            '<br>100 free searches / month on free plan</div>',
            unsafe_allow_html=True,
        )
        # serpapi_key = st.text_input(
        #     "SerpApi key", type="password",
        #     placeholder="paste your key here…",
        #     label_visibility="collapsed",
        # )

    st.markdown("---")
    st.markdown("### 🔧 Filters")
    tier_filter = st.multiselect("Show tiers", ["HOT","WARM","COLD"], default=["HOT","WARM"])
    min_score   = st.slider("Min score", 0, 100, 30)
    st.markdown("---")
    search_btn  = st.button("Search leads", use_container_width=True)

    # ── Main ──────────────────────────────────────────────────────────────────────
    st.markdown("""
    <div class="app-header">
    <p class="app-title">job lead finder</p>
    <p class="app-sub">SerpApi Google Jobs · LinkedIn · Indeed · Remotive — scored &amp; prioritized</p>
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
            st.error("Paste your SerpApi key above, or deselect SerpApi.")
        else:
            with st.spinner(""):
                jobs = run_pipeline(keywords, sources, location, serpapi_key.strip(), status_box)
            st.session_state["jobs"]     = jobs
            st.session_state["searched"] = True
            if st.session_state.get("serp_error"):
                st.error("SerpApi error: " + st.session_state["serp_error"])

    # ── Results ───────────────────────────────────────────────────────────────────
    if st.session_state.get("searched") and "jobs" in st.session_state:
        jobs = st.session_state["jobs"]
        filtered = [j for j in jobs if j["tier"] in tier_filter and j["score"] >= min_score]

        hot_n  = sum(1 for j in jobs if j["tier"] == "HOT")
        warm_n = sum(1 for j in jobs if j["tier"] == "WARM")
        cold_n = sum(1 for j in jobs if j["tier"] == "COLD")
        serp_n = sum(1 for j in jobs if j["source"] == "serpapi")

        st.markdown(
            '<div class="stats-bar">'
            '<div class="stat-item"><div class="stat-val">' + str(len(jobs)) + '</div><div class="stat-lbl">Total</div></div>'
            '<div class="stat-item"><div class="stat-val" style="color:#7c3aed">' + str(serp_n) + '</div><div class="stat-lbl">SerpApi</div></div>'
            '<div class="stat-item"><div class="stat-val" style="color:#ef4444">' + str(hot_n) + '</div><div class="stat-lbl">Hot</div></div>'
            '<div class="stat-item"><div class="stat-val" style="color:#f59e0b">' + str(warm_n) + '</div><div class="stat-lbl">Warm</div></div>'
            '<div class="stat-item"><div class="stat-val" style="color:#22c55e">' + str(cold_n) + '</div><div class="stat-lbl">Cold</div></div>'
            '<div class="stat-item"><div class="stat-val">' + str(len(filtered)) + '</div><div class="stat-lbl">Showing</div></div>'
            '</div>',
            unsafe_allow_html=True,
        )

        if not filtered:
            st.markdown(
                '<div class="empty-state"><div class="empty-icon">🔍</div>'
                '<div class="empty-title">No results match your filters</div>'
                '<div>Try lowering the min score or adding more tiers</div></div>',
                unsafe_allow_html=True,
            )
        else:
            col_sort, col_src, _ = st.columns([2, 2, 3])
            with col_sort:
                sort_by = st.selectbox("Sort", ["Score ↓","Score ↑","Company A–Z"],
                                    label_visibility="collapsed")
            with col_src:
                available_sources = sorted({j["source"] for j in filtered})
                src_filter = st.multiselect("Source", available_sources, default=available_sources,
                                            label_visibility="collapsed")

            if sort_by == "Score ↓":
                filtered.sort(key=lambda j: j["score"], reverse=True)
            elif sort_by == "Score ↑":
                filtered.sort(key=lambda j: j["score"])
            else:
                filtered.sort(key=lambda j: j["company"].lower())

            if src_filter:
                filtered = [j for j in filtered if j["source"] in src_filter]

            for job in filtered:
                t           = job["tier"].lower()
                score       = job["score"]
                score_color = "#ef4444" if t=="hot" else "#f59e0b" if t=="warm" else "#22c55e"
                src_cls     = "source-serp" if job["source"] == "serpapi" else ""

                signals_html = "".join(
                    '<span class="signal-tag">' + s + '</span>'
                    for s in (job.get("signals") or []) if s
                )
                desc      = job.get("description", "")
                desc_html = ('<div class="job-desc">' + desc[:240] + ("…" if len(desc)>240 else "") + "</div>") if desc else ""

                salary_html = (
                    '<span style="font-size:0.72rem;color:#059669;font-weight:500;margin-left:8px;">'
                    + job["salary"] + "</span>"
                ) if job.get("salary") else ""

                schedule_html = (
                    '<span style="font-size:0.7rem;color:#6b7280;margin-left:6px;">'
                    + job["schedule"] + "</span>"
                ) if job.get("schedule") else ""

                meta_parts = [p for p in [job.get("location",""), job.get("posted_at","")[:10]] if p]
                meta_html  = " · ".join(meta_parts)

                url       = job.get("url","")
                link_html = ('<a href="' + url + '" target="_blank" style="font-size:0.72rem;color:#6366f1;text-decoration:none;margin-left:6px;">↗ view</a>') if url else ""

                st.markdown(
                    '<div class="job-card ' + t + '">'
                    '<div style="display:flex;align-items:flex-start;gap:1rem;">'
                    '<div style="flex:1;min-width:0;">'
                    '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:4px;">'
                    '<span class="badge badge-' + t + '">' + job["tier"] + "</span>"
                    '<span class="source-pill ' + src_cls + '">' + job["source"] + "</span>"
                    + salary_html + schedule_html + link_html +
                    "</div>"
                    '<p class="job-title">' + job["title"] + "</p>"
                    '<p class="job-company">' + job["company"] + "</p>"
                    + ('<p class="job-meta">' + meta_html + "</p>" if meta_html else "")
                    + desc_html
                    + ('<div style="margin-top:6px">' + signals_html + "</div>" if signals_html else "")
                    + "</div>"
                    '<div class="score-wrap" style="flex-shrink:0;width:52px;">'
                    '<div class="score-num" style="color:' + score_color + '">' + str(score) + "</div>"
                    '<div class="score-label">score</div>'
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