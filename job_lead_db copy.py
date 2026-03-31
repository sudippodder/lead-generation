import streamlit as st
import feedparser
import requests
import hashlib
import sqlite3
import json
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
# DATABASE  (SQLite — auto-created on first run)
# ══════════════════════════════════════════════════════════════════════════════
DB_PATH = "auth_db.sqlite"

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id          TEXT PRIMARY KEY,
                source      TEXT,
                title       TEXT,
                company     TEXT,
                location    TEXT,
                url         TEXT,
                posted_at   TEXT,
                description TEXT,
                salary      TEXT,
                schedule    TEXT,
                score       INTEGER,
                priority    TEXT,
                reason      TEXT,
                factors     TEXT,
                search_kw   TEXT,
                saved_at    TEXT
            )
        """)
        conn.commit()

def db_save_jobs(jobs, search_kw):
    """Insert or replace jobs into DB. Returns (inserted, skipped) counts."""
    inserted, skipped = 0, 0
    saved_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for job in jobs:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO leads
                    (id, source, title, company, location, url, posted_at,
                     description, salary, schedule, score, priority, reason,
                     factors, search_kw, saved_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    job["id"], job["source"], job["title"], job["company"],
                    job["location"], job["url"], job["posted_at"],
                    job["description"], job.get("salary",""), job.get("schedule",""),
                    job.get("score", 0), job.get("priority","Low"),
                    job.get("reason",""), json.dumps(job.get("factors",{})),
                    search_kw, saved_at,
                ))
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    inserted += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1
        conn.commit()
    return inserted, skipped

def db_load_jobs(keyword_filter="", priority_filter=None, min_score=0):
    """Load jobs from DB with optional filters."""
    query = "SELECT * FROM leads WHERE score >= ?"
    params = [min_score]
    if priority_filter:
        placeholders = ",".join("?" * len(priority_filter))
        query += f" AND priority IN ({placeholders})"
        params += priority_filter
    if keyword_filter.strip():
        kw = "%" + keyword_filter.strip().lower() + "%"
        query += " AND (LOWER(title) LIKE ? OR LOWER(company) LIKE ? OR LOWER(search_kw) LIKE ?)"
        params += [kw, kw, kw]
    query += " ORDER BY score DESC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    jobs = []
    for row in rows:
        job = dict(row)
        try:
            job["factors"] = json.loads(job.get("factors") or "{}")
        except Exception:
            job["factors"] = {}
        jobs.append(job)
    return jobs

def db_stats():
    with get_conn() as conn:
        total   = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        high    = conn.execute("SELECT COUNT(*) FROM leads WHERE priority='High'").fetchone()[0]
        medium  = conn.execute("SELECT COUNT(*) FROM leads WHERE priority='Medium'").fetchone()[0]
        sources = conn.execute("SELECT DISTINCT source FROM leads").fetchall()
        last    = conn.execute("SELECT MAX(saved_at) FROM leads").fetchone()[0]
    return {"total": total, "high": high, "medium": medium,
            "sources": [r[0] for r in sources], "last": (last or "")[:16]}

def db_clear():
    with get_conn() as conn:
        conn.execute("DELETE FROM leads")
        conn.commit()

# Initialise DB on startup
init_db()

# ══════════════════════════════════════════════════════════════════════════════
# SCORING ENGINE
# ══════════════════════════════════════════════════════════════════════════════
TARGET_ROLES = [
    "marketing", "growth", "performance", "demand generation", "seo", "sem",
    "paid media", "content", "brand", "social media", "email marketing",
    "product marketing", "revenue", "operations", "ops", "strategy",
    "business development", "partnerships", "go-to-market", "gtm",
    "sales", "account", "customer success",
]
GROWTH_SIGNALS = [
    "series a", "series b", "series c", "seed round", "raised", "funding",
    "fast-growing", "rapidly scaling", "expanding", "hypergrowth",
    "recently launched", "new market", "scaling team", "growing team",
    "backed by", "venture", "recently hired", "headcount growth",
]
CLARITY_POSITIVE = [
    "requirements", "responsibilities", "you will", "you'll", "must have",
    "nice to have", "tools", "kpi", "metrics", "roi", "budget", "manage",
    "own", "lead", "drive", "report to",
]
CLARITY_NEGATIVE = ["various duties", "other tasks", "as needed", "miscellaneous"]

def parse_date(s):
    if not s: return None
    s = s.strip()
    for fmt in ["%Y-%m-%dT%H:%M:%S%z","%Y-%m-%dT%H:%M:%SZ","%Y-%m-%d",
                "%a, %d %b %Y %H:%M:%S %z","%a, %d %b %Y %H:%M:%S GMT"]:
        try: return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except: pass
    m = re.search(r"(\d+)\s*(day|hour|week|month)", s.lower())
    if m:
        n, u = int(m.group(1)), m.group(2)
        d = {"day":timedelta(days=n),"hour":timedelta(hours=n),
             "week":timedelta(weeks=n),"month":timedelta(days=n*30)}.get(u)
        if d: return datetime.now(timezone.utc) - d
    return None

def score_role_relevance(job):
    text = (job["title"] + " " + job["description"]).lower()
    hits = sum(1 for r in TARGET_ROLES if r in text)
    if hits == 0: return 0, "no role match"
    if hits == 1: return 1, "weak role match"
    if hits == 2: return 2, "partial role match"
    return 3, "strong role match"

def score_recency(job):
    dt = parse_date(job.get("posted_at",""))
    if dt is None: return 1, "date unknown"
    age = (datetime.now(timezone.utc) - dt).days
    if age <= 7:  return 2, f"posted {age}d ago"
    if age <= 14: return 1, f"posted {age}d ago"
    return 0, f"posted {age}d ago (stale)"

def score_hiring_volume(company_jobs):
    n = len(company_jobs)
    if n >= 3: return 2, f"{n} open roles"
    if n == 2: return 1, "2 open roles"
    return 0, "1 open role"

def score_company_signal(job):
    text = (job["title"]+" "+job["company"]+" "+job["description"]).lower()
    hits = [s for s in GROWTH_SIGNALS if s in text]
    if len(hits) >= 2: return 2, "strong growth signals"
    if len(hits) == 1: return 1, hits[0]
    return 0, "no growth signal"

def score_jd_clarity(job):
    desc = job.get("description","").lower()
    if len(desc) < 80: return 0, "very short JD"
    if any(n in desc for n in CLARITY_NEGATIVE): return 0, "generic JD"
    if sum(1 for p in CLARITY_POSITIVE if p in desc) >= 4: return 1, "clear JD"
    return 0, "vague JD"

def build_reason(role_r, recency_r, volume_r, signal_r, clarity_r, total):
    parts = []
    if recency_r[0] == 2:  parts.append(recency_r[1])
    if volume_r[0] >= 1:   parts.append(volume_r[1])
    if signal_r[0] >= 1:   parts.append(signal_r[1])
    if role_r[0] == 3:     parts.append("exact role match")
    if clarity_r[0] == 1:  parts.append("well-defined JD")
    if not parts:
        parts = [recency_r[1], role_r[1]]
    return " · ".join(parts[:3])

def priority_label(score):
    if score >= 7: return "High"
    if score >= 5: return "Medium"
    return "Low"

def score_lead(job, company_jobs):
    role_r    = score_role_relevance(job)
    recency_r = score_recency(job)
    volume_r  = score_hiring_volume(company_jobs)
    signal_r  = score_company_signal(job)
    clarity_r = score_jd_clarity(job)
    total = min(10, role_r[0]+recency_r[0]+volume_r[0]+signal_r[0]+clarity_r[0])
    job["score"]    = total
    job["priority"] = priority_label(total)
    job["reason"]   = build_reason(role_r, recency_r, volume_r, signal_r, clarity_r, total)
    job["factors"]  = {
        "role_relevance": role_r, "recency": recency_r,
        "hiring_volume": volume_r, "company_signal": signal_r, "jd_clarity": clarity_r,
    }
    return job

# ══════════════════════════════════════════════════════════════════════════════
# FETCHERS
# ══════════════════════════════════════════════════════════════════════════════
def uid(s):
    return hashlib.md5(s.encode()).hexdigest()[:12]

def _base(source):
    return {"source":source,"title":"","company":"","location":"",
            "url":"","posted_at":"","description":"","salary":"","schedule":""}

def fetch_serpapi(keyword, location, api_key, num=30):
    jobs = []
    if not api_key: return jobs
    loc = location if location and location.lower() != "worldwide" else ""
    try:
        resp = requests.get("https://serpapi.com/search", timeout=20, params={
            "engine":"google_jobs","q":keyword,"location":loc,"num":num,"api_key":api_key})
        data = resp.json()
        if "error" in data:
            st.session_state["serp_error"] = data["error"]
            return jobs
        for j in data.get("jobs_results",[]):
            exts = j.get("detected_extensions",{})
            b = _base("serpapi")
            b.update({"id":uid(j.get("job_id",j.get("title","")+j.get("company_name",""))),
                      "title":j.get("title",""),"company":j.get("company_name",""),
                      "location":j.get("location",""),"url":j.get("share_link",""),
                      "posted_at":exts.get("posted_at",""),"description":j.get("description","")[:700],
                      "salary":exts.get("salary",""),"schedule":exts.get("schedule_type","")})
            jobs.append(b)
    except Exception as e:
        st.session_state["serp_error"] = str(e)
    return jobs

def fetch_linkedin(keyword, location="worldwide"):
    kw  = keyword.replace(" ","%20")
    url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
           "?keywords="+kw+"&location="+location+"&start=0")
    jobs = []
    try:
        resp = requests.get(url, timeout=12,
                            headers={"User-Agent":"Mozilla/5.0 (compatible; JobScanner/1.0)"})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text,"html.parser")
            for card in soup.find_all("div", class_="base-card"):
                te = card.find("h3", class_="base-search-card__title")
                ce = card.find("h4", class_="base-search-card__subtitle")
                le = card.find("span", class_="job-search-card__location")
                ae = card.find("a", class_="base-card__full-link")
                de = card.find("time")
                if te and ce:
                    b = _base("linkedin")
                    b.update({"id":uid(ae["href"] if ae else str(te)),
                              "title":te.get_text(strip=True),"company":ce.get_text(strip=True),
                              "location":le.get_text(strip=True) if le else "",
                              "url":ae["href"].split("?")[0] if ae else "",
                              "posted_at":de.get("datetime","") if de else ""})
                    jobs.append(b)
    except: pass
    return jobs

def fetch_indeed(keyword):
    kw  = keyword.replace(" ","+")
    url = "https://www.indeed.com/rss?q="+kw+"&sort=date&fromage=14"
    jobs = []
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent":"Mozilla/5.0"})
        for e in feed.entries:
            b = _base("indeed")
            b.update({"id":uid(e.get("link",e.get("title",""))),
                      "title":e.get("title",""),"company":e.get("author",""),
                      "url":e.get("link",""),"posted_at":e.get("published",""),
                      "description":BeautifulSoup(e.get("summary",""),"html.parser").get_text()[:600]})
            jobs.append(b)
    except: pass
    return jobs

def fetch_remotive(keyword):
    jobs = []
    try:
        resp = requests.get("https://remotive.com/api/remote-jobs",
                            params={"search":keyword,"limit":50}, timeout=12)
        if resp.status_code == 200:
            for r in resp.json().get("jobs",[]):
                b = _base("remotive")
                b.update({"id":uid(str(r.get("id",r.get("url","")))),
                          "title":r.get("title",""),"company":r.get("company_name",""),
                          "location":r.get("candidate_required_location","Remote"),
                          "url":r.get("url",""),"posted_at":r.get("publication_date",""),
                          "description":BeautifulSoup(r.get("description",""),"html.parser").get_text()[:600],
                          "salary":r.get("salary","")})
                jobs.append(b)
    except: pass
    return jobs

# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
def normalize(raw):
    seen, clean = set(), []
    for job in raw:
        if job["id"] in seen: continue
        seen.add(job["id"])
        for f in ("title","company","description"):
            job[f] = re.sub(r"\s+"," ", job.get(f,"")).strip()
        clean.append(job)
    return clean

def run_pipeline(keywords, sources, location, serpapi_key, status_ph):
    raw = []
    st.session_state.pop("serp_error", None)
    for kw in keywords:
        status_ph.markdown(
            '<div class="status-line">→ fetching <b>'+kw+'</b>…</div>',
            unsafe_allow_html=True)
        if "SerpApi (Google Jobs)" in sources:
            raw += fetch_serpapi(kw, location, serpapi_key)
        if "LinkedIn" in sources:  raw += fetch_linkedin(kw, location)
        if "Indeed"   in sources:  raw += fetch_indeed(kw)
        if "Remotive" in sources:  raw += fetch_remotive(kw)
        time.sleep(0.6)
    status_ph.markdown(
        '<div class="status-line">→ scoring '+str(len(raw))+' results…</div>',
        unsafe_allow_html=True)
    jobs = normalize(raw)
    by_company = defaultdict(list)
    for j in jobs:
        by_company[j["company"].lower()].append(j)
    for job in jobs:
        score_lead(job, by_company[job["company"].lower()])
    jobs.sort(key=lambda j: j["score"], reverse=True)
    status_ph.empty()
    return jobs

# ══════════════════════════════════════════════════════════════════════════════
# RENDER HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def score_color(s):
    if s >= 7: return "#ef4444"
    if s >= 5: return "#f59e0b"
    return "#94a3b8"

def bar_pct(s):
    return str(round(s/10*100))+"%"

def render_stats(jobs, extra_label=""):
    high_n = sum(1 for j in jobs if j["priority"]=="High")
    med_n  = sum(1 for j in jobs if j["priority"]=="Medium")
    low_n  = sum(1 for j in jobs if j["priority"]=="Low")
    serp_n = sum(1 for j in jobs if j["source"]=="serpapi")
    html = (
        '<div class="stats-bar">'
        '<div class="stat-item"><div class="stat-val">'+str(len(jobs))+'</div>'
        '<div class="stat-lbl">'+extra_label+'Total</div></div>'
        + ('<div class="stat-item"><div class="stat-val" style="color:#7c3aed">'+str(serp_n)+'</div>'
           '<div class="stat-lbl">SerpApi</div></div>' if serp_n else "")
        + '<div class="stat-item"><div class="stat-val" style="color:#ef4444">'+str(high_n)+'</div>'
          '<div class="stat-lbl">High</div></div>'
          '<div class="stat-item"><div class="stat-val" style="color:#f59e0b">'+str(med_n)+'</div>'
          '<div class="stat-lbl">Medium</div></div>'
          '<div class="stat-item"><div class="stat-val" style="color:#94a3b8">'+str(low_n)+'</div>'
          '<div class="stat-lbl">Low</div></div>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

def render_cards(filtered, view_mode):
    if not filtered:
        st.markdown(
            '<div class="empty-state"><div class="empty-icon">🔍</div>'
            '<div class="empty-title">No leads match your filters</div>'
            '<div>Try lowering the min score or enabling more tiers</div></div>',
            unsafe_allow_html=True)
        return

    c1, c2, _ = st.columns([2,2,3])
    with c1:
        sort_by = st.selectbox("Sort",["Score ↓","Score ↑","Company A–Z"],
                               label_visibility="collapsed")
    avail = sorted({j["source"] for j in filtered})
    #st.markdown("--- ")
    #st.markdown(avail)
    #src_f = []
    # with c2:
    #     avail = sorted({j["source"] for j in filtered})
    #     src_f = st.multiselect("Source", avail, default=avail,
    #                            label_visibility="collapsed")
    #st.markdown(src_f)
    src_f = avail
    if sort_by == "Score ↓": filtered.sort(key=lambda j: j["score"], reverse=True)
    elif sort_by == "Score ↑": filtered.sort(key=lambda j: j["score"])
    else: filtered.sort(key=lambda j: j["company"].lower())
    if src_f: filtered = [j for j in filtered if j["source"] in src_f]
    view_mode = "Cards"
    if view_mode == "Table":
        rows = ""
        for job in filtered:
            p, sc = job["priority"].lower(), job["score"]
            url   = job.get("url","")
            tc    = ('<a href="'+url+'" target="_blank" style="color:#111827;text-decoration:none;font-weight:500;">'
                     +job["title"]+' ↗</a>') if url else job["title"]
            rows += ("<tr><td>"+job["company"]+"</td><td>"+tc+"</td>"
                     "<td>"+job.get("location","")+"</td>"
                     '<td class="score-td"><span class="badge badge-'+p+'">'+job["priority"]+"</span></td>"
                     '<td class="score-td" style="color:'+score_color(sc)+'">'+str(sc)+"/10</td>"
                     "<td style='font-size:0.75rem;color:#374151;'>"+job.get("reason","")+"</td></tr>")
        st.markdown(
            '<div style="overflow-x:auto"><table class="tbl"><thead><tr>'
            '<th>Company</th><th>Role</th><th>Location</th>'
            '<th>Priority</th><th>Score</th><th>Reason</th>'
            "</tr></thead><tbody>"+rows+"</tbody></table></div>",
            unsafe_allow_html=True)
    else:
        FACTOR_LABELS = {"role_relevance":"role","recency":"recency",
                         "hiring_volume":"volume","company_signal":"signal","jd_clarity":"clarity"}
        FACTOR_MAX    = {"role_relevance":"3","recency":"2","hiring_volume":"2",
                         "company_signal":"2","jd_clarity":"1"}
        for job in filtered:
            p, sc   = job["priority"].lower(), job["score"]
            sc_c    = score_color(sc)
            factors = job.get("factors",{})
            url     = job.get("url","")

            pills = ""
            for fk, fl in FACTOR_LABELS.items():
                fval, ftxt = factors.get(fk,(0,""))
                cls = "factor-pill scored" if fval > 0 else "factor-pill"
                pills += ('<span class="'+cls+'">'+fl+"&nbsp;"+str(fval)
                          +"/"+FACTOR_MAX[fk]
                          +("&nbsp;·&nbsp;"+ftxt if ftxt else "")+"</span>")

            src_cls    = "source-serp" if job["source"]=="serpapi" else (
                         "source-db"   if job.get("from_db") else "")
            link_html  = ('<a href="'+url+'" target="_blank" style="font-size:0.7rem;color:#6366f1;'
                          'text-decoration:none;margin-left:6px;">↗ view job</a>') if url else ""
            salary_html= ('<span style="font-size:0.7rem;color:#059669;font-weight:500;margin-left:8px;">'
                          +job["salary"]+"</span>") if job.get("salary") else ""
            meta_parts = [p2 for p2 in [job.get("location",""), job.get("posted_at","")[:10]] if p2]
            meta_html  = " · ".join(meta_parts)
            saved_badge= ('<span class="badge badge-saved" style="font-size:0.6rem;margin-left:6px;">saved</span>'
                          ) if job.get("from_db") else ""

            st.markdown(
                '<div class="lead-card '+p+'">'
                '<div style="display:flex;align-items:flex-start;gap:1rem;">'
                '<div style="flex:1;min-width:0;">'
                '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:5px;">'
                '<span class="badge badge-'+p+'">'+job["priority"]+' Priority</span>'
                '<span class="source-pill '+src_cls+'">'+job["source"]+"</span>"
                +saved_badge+salary_html+link_html
                +"</div>"
                '<p class="lead-title">'+job["title"]+"</p>"
                '<p class="lead-company">'+job["company"]+"</p>"
                +('<p class="lead-meta">'+meta_html+"</p>" if meta_html else "")
                +'<div style="margin-top:7px">'+pills+"</div>"
                +'<div class="lead-reason"><strong>Why this lead:</strong> '+job.get("reason","")+"</div>"
                +"</div>"
                '<div class="score-circle" style="flex-shrink:0;width:56px;">'
                '<div class="score-main" style="color:'+sc_c+'">'+str(sc)+"</div>"
                '<div class="score-denom">/10</div>'
                '<div class="score-bar-wrap"><div class="score-bar-bg">'
                '<div class="score-bar-fill" style="width:'+bar_pct(sc)+';background:'+sc_c+';"></div>'
                "</div></div></div></div></div>",
                unsafe_allow_html=True)

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
.app-title  { font-family: 'DM Mono', monospace; font-size: 1.5rem; font-weight: 500;
              color: #111827; letter-spacing: -0.02em; margin: 0; }
.app-sub    { font-size: 0.825rem; color: #6b7280; margin-top: 0.2rem; }

.mode-bar { display:flex; gap:10px; padding:10px 14px; background:#f9fafb;
            border:1px solid #e5e7eb; border-radius:8px; margin-bottom:1.5rem;
            align-items:center; }
.mode-label { font-size:0.78rem; color:#374151; }
.mode-active { font-weight:600; color:#111827; }

.badge { display:inline-block; padding:2px 10px; border-radius:99px; font-size:0.68rem;
         font-weight:600; font-family:'DM Mono',monospace; letter-spacing:0.05em; text-transform:uppercase; }
.badge-high   { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; }
.badge-medium { background:#fffbeb; color:#b45309; border:1px solid #fde68a; }
.badge-low    { background:#f8fafc; color:#64748b; border:1px solid #e2e8f0; }
.badge-saved  { background:#f0fdf4; color:#166534; border:1px solid #bbf7d0; }

.factor-pill { display:inline-flex; align-items:center; gap:4px; padding:2px 8px;
               border-radius:5px; font-size:0.65rem; font-family:'DM Mono',monospace;
               background:#f1f5f9; color:#475569; border:1px solid #e2e8f0; margin:2px 2px 0 0; }
.factor-pill.scored { background:#f0fdf4; color:#166534; border-color:#bbf7d0; }

.source-pill { display:inline-block; padding:1px 8px; border-radius:4px; font-size:0.65rem;
               font-family:'DM Mono',monospace; background:#f3f4f6; color:#374151; border:1px solid #e5e7eb; }
.source-serp { background:#faf5ff; color:#7c3aed; border-color:#e9d5ff; }
.source-db   { background:#ecfdf5; color:#065f46; border-color:#6ee7b7; }

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

.score-circle { text-align:center; }
.score-main   { font-family:'DM Mono',monospace; font-size:1.5rem; font-weight:500; line-height:1; }
.score-denom  { font-size:0.65rem; color:#9ca3af; }
.score-bar-wrap { margin-top:4px; width:48px; }
.score-bar-bg   { height:3px; background:#e5e7eb; border-radius:2px; overflow:hidden; }
.score-bar-fill { height:100%; border-radius:2px; }

.stats-bar { display:flex; gap:0.75rem; padding:0.75rem 1.25rem; background:#f9fafb;
             border:1px solid #e5e7eb; border-radius:8px; margin-bottom:1.25rem; }
.stat-item { text-align:center; flex:1; }
.stat-val  { font-family:'DM Mono',monospace; font-size:1.15rem; font-weight:500; color:#111827; }
.stat-lbl  { font-size:0.65rem; color:#9ca3af; text-transform:uppercase; letter-spacing:0.05em; }

.legend { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:1rem; }
.legend-item { display:flex; align-items:center; gap:6px; font-size:0.75rem; color:#6b7280; }
.legend-dot  { width:8px; height:8px; border-radius:50%; flex-shrink:0; }

.tbl { width:100%; border-collapse:collapse; font-size:0.8rem; }
.tbl th { text-align:left; padding:8px 12px; background:#f9fafb; border-bottom:2px solid #e5e7eb;
          font-weight:600; color:#374151; font-size:0.72rem; text-transform:uppercase;
          letter-spacing:0.04em; white-space:nowrap; }
.tbl td { padding:9px 12px; border-bottom:1px solid #f1f5f9; vertical-align:top; color:#374151; }
.tbl tr:hover td { background:#fafafa; }
.tbl td.score-td { font-family:'DM Mono',monospace; font-weight:500; text-align:center; white-space:nowrap; }

.db-info { background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px;
           padding:9px 14px; font-size:0.78rem; color:#065f46; margin-bottom:10px; }
.db-warn { background:#fffbeb; border:1px solid #fde68a; border-radius:8px;
           padding:9px 14px; font-size:0.78rem; color:#92400e; margin-bottom:10px; }

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
    # st.markdown("### 🎯 Mode")
    # mode = st.radio(
    #     "mode",
    #     ["🔍 Search & Save to DB", "🗄️ View from DB"],
    #     label_visibility="collapsed",
    # )

    # st.markdown("---")
    mode = "🗄️ View from DB"
    # ── Mode 1: Search & Save ─────────────────────────────────────────────
    if mode == "🔍 Search & Save to DB":
        st.markdown("### Search")
        keyword_input = st.text_area(
            "Keywords", height=100, help="One keyword per line",
            value="Performance Marketer\nGrowth Manager\nMarketing Operations",
        )
        # location = st.text_input("Location", value="United States",
        #                          help="e.g. United States, Remote, London, worldwide")
        location = "United States"
        # sources = st.multiselect(
        #     "Sources",
        #     ["SerpApi (Google Jobs)","LinkedIn","Indeed","Remotive"],
        #     default=["SerpApi (Google Jobs)","Remotive"],
        # )
        sources = ["LinkedIn","Indeed","Remotive"]
        serpapi_key = ""
        if "SerpApi (Google Jobs)" in sources:
            st.markdown(
                '<div class="api-box">🔑 SerpApi key required — '
                '<a href="https://serpapi.com/manage-api-key" target="_blank">get free key ↗</a>'
                '<br>100 free searches / month</div>',
                unsafe_allow_html=True)
            serpapi_key = st.text_input(
                "SerpApi key", type="password",
                placeholder="paste your key here…",
                label_visibility="collapsed")

        # st.markdown("---")
        # st.markdown("### View")
        #view_mode = st.radio("View", ["Cards","Table"], horizontal=True)
        view_mode = "Cards"
        st.markdown("---")
        search_btn = st.button("Search & Save", use_container_width=True)

    # ── Mode 2: View from DB ──────────────────────────────────────────────
    else:
        st.markdown("### Job Leads")
        db_keyword = st.text_input("Keyword filter", placeholder="company, title, keyword…")
        db_priority = st.multiselect("Priority", ["High","Medium","Low"],
                                     default=["High","Medium"])
        db_min_score = st.slider("Min score (/10)", 0, 10, 4)

        # st.markdown("---")
        #view_mode = st.radio("View", ["Cards","Table"], horizontal=True)
        view_mode = "Cards"
        # DB stats
        stats = db_stats()
        if stats["total"] > 0:
            #Sources: '+", ".join(stats["sources"])+"</div>
            st.markdown(
                # '<div class="db-info">💾 <strong>'+str(stats["total"])+'</strong> leads saved'
                # +'<br>Last save: '+stats["last"][:10]
                # +
                '<br>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="db-warn">DB is empty — run a Search first</div>',
                unsafe_allow_html=True)

        st.markdown("---")
        search_btn = st.button("Search", use_container_width=True)

        # if st.button("🗑️ Clear DB", use_container_width=True):
        #     db_clear()
        #     st.success("DB cleared.")
        #     st.rerun()

    # ══════════════════════════════════════════════════════════════════════════════
    # MAIN AREA
    # ══════════════════════════════════════════════════════════════════════════════
    st.markdown("""
    <div class="app-header">
    <p class="app-title">job lead finder</p>
    <p class="app-sub">Role Relevance · Recency · Hiring Volume · Company Signal · JD Clarity — scored /10</p>
    </div>
    """, unsafe_allow_html=True)

    # Mode indicator banner
    if mode == "🔍 Search & Save to DB":
        st.markdown(
            '<div class="mode-bar">'
            '<span style="width:8px;height:8px;border-radius:50%;background:#6366f1;flex-shrink:0;display:inline-block;"></span>'
            '<span class="mode-label mode-active">Search & Save mode</span>'
            '<span class="mode-label" style="margin-left:auto;color:#9ca3af;">Results will be saved to local DB</span>'
            '</div>',
            unsafe_allow_html=True)
    else:
        stats = db_stats()
        # st.markdown(
        #     '<div class="mode-bar" style="background:#f0fdf4;border-color:#bbf7d0;">'
        #     '<span style="width:8px;height:8px;border-radius:50%;background:#22c55e;flex-shrink:0;display:inline-block;"></span>'
        #     '<span class="mode-label mode-active" style="color:#065f46;">View from DB mode</span>'
        #     '<span class="mode-label" style="margin-left:auto;color:#6b7280;">'
        #     +str(stats["total"])+" leads in database</span>"
        #     '</div>',
        #     unsafe_allow_html=True)

    status_box = st.empty()

    # ── Legend ────────────────────────────────────────────────────────────────────
    st.markdown("""
    <div class="legend">
    <div class="legend-item"><div class="legend-dot" style="background:#ef4444"></div>High priority (7–10)</div>
    <div class="legend-item"><div class="legend-dot" style="background:#f59e0b"></div>Medium priority (5–6)</div>
    <div class="legend-item"><div class="legend-dot" style="background:#cbd5e1"></div>Low priority (&lt;5)</div>
    </div>
    """, unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════════
    # MODE 1: SEARCH & SAVE
    # ══════════════════════════════════════════════════════════════════════════════
    if mode == "🔍 Search & Save to DB":
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
                    jobs = run_pipeline(keywords, sources, location,
                                        serpapi_key.strip(), status_box)
                if st.session_state.get("serp_error"):
                    st.error("SerpApi: " + st.session_state["serp_error"])

                # Save to DB
                kw_str = ", ".join(keywords)
                inserted, skipped = db_save_jobs(jobs, kw_str)
                # st.success(
                #     f"✅ Saved **{inserted}** new leads to DB "
                #     f"({skipped} duplicates skipped) — {len(jobs)} total fetched")

                st.session_state["search_jobs"]    = jobs
                st.session_state["search_done"]    = True
                st.session_state["search_keywords"]= kw_str

        if st.session_state.get("search_done") and "search_jobs" in st.session_state:
            jobs = st.session_state["search_jobs"]
            render_stats(jobs)
            render_cards(jobs, view_mode)
        elif not search_btn:
            st.markdown(
                '<div class="empty-state"><div class="empty-icon">🎯</div>'
                '<div class="empty-title">Ready to search</div>'
                '<div>Enter keywords in the sidebar and hit Search &amp; Save</div></div>',
                unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════════════════
    # MODE 2: VIEW FROM DB
    # ══════════════════════════════════════════════════════════════════════════════
    else:
        if search_btn:
            jobs = db_load_jobs(
                keyword_filter=db_keyword,
                priority_filter=db_priority if db_priority else None,
                min_score=db_min_score,
            )
            # Mark as from DB for badge display
            for j in jobs:
                j["from_db"] = True
            st.session_state["db_jobs"]  = jobs
            st.session_state["db_loaded"] = True

        if st.session_state.get("db_loaded") and "db_jobs" in st.session_state:
            jobs = st.session_state["db_jobs"]
            if not jobs:
                st.markdown(
                    '<div class="empty-state"><div class="empty-icon">🗄️</div>'
                    '<div class="empty-title">No leads found in DB</div>'
                    '<div>Run a search first, or adjust your filters</div></div>',
                    unsafe_allow_html=True)
            else:
                render_stats(jobs, "DB ")
                render_cards(jobs, view_mode)
        elif not search_btn:
            st.markdown(
                '<div class="empty-state"><div class="empty-icon">🗄️</div>'
                '<div class="empty-title">Load your saved leads</div>'
                '<div>Hit "Load from DB" to view all saved leads, or filter by keyword / priority</div></div>',
                unsafe_allow_html=True)
