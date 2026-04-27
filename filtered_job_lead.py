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

# st.set_page_config(
#     page_title="Job Lead Finder",
#     page_icon="🎯",
#     layout="wide",
#     initial_sidebar_state="expanded",
# )


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
        conn.execute("""CREATE TABLE IF NOT EXISTS leads (
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
            buy_reason  TEXT,
            step_trace  TEXT,
            factors     TEXT,
            search_kw   TEXT,
            saved_at    TEXT,
            UNIQUE(title, company)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS rejected (
            id            TEXT PRIMARY KEY,
            source        TEXT,
            title         TEXT,
            company       TEXT,
            reject_step   TEXT,
            reject_reason TEXT,
            search_kw     TEXT,
            saved_at      TEXT,
            UNIQUE(title, company)
        )""")
        # Migrate existing DB: add unique index if table already existed without it
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_title_company "
                "ON leads(title, company)"
            )
        except Exception:
            pass
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_rejected_title_company "
                "ON rejected(title, company)"
            )
        except Exception:
            pass
        conn.commit()


def db_save(jobs, rejected, search_kw):
    """Insert jobs and rejected entries into DB. Returns (inserted, skipped)."""
    saved_at = datetime.now(timezone.utc).isoformat()
    ins = skip = 0
    with get_conn() as conn:
        for job in jobs:
            print(["""INSERT OR IGNORE INTO leads
                    (id, source, title, company, location, url, posted_at,
                     description, salary, schedule, score, priority,
                     buy_reason, step_trace, factors, search_kw, saved_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job["id"], job["source"], job["title"], job["company"],
                     job.get("location",""), job.get("url",""), job.get("posted_at",""),
                     job.get("description",""), job.get("salary",""), job.get("schedule",""),
                     job.get("score", 0), job.get("priority", "Low"),
                     job.get("buy_reason",""), job.get("step_trace",""),
                     json.dumps(job.get("factors",{})), search_kw, saved_at)])
            try:
                conn.execute("""INSERT OR IGNORE INTO leads
                    (id, source, title, company, location, url, posted_at,
                     description, salary, schedule, score, priority,
                     buy_reason, step_trace, factors, search_kw, saved_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job["id"], job["source"], job["title"], job["company"],
                     job.get("location",""), job.get("url",""), job.get("posted_at",""),
                     job.get("description",""), job.get("salary",""), job.get("schedule",""),
                     job.get("score", 0), job.get("priority", "Low"),
                     job.get("buy_reason",""), job.get("step_trace",""),
                     json.dumps(job.get("factors",{})), search_kw, saved_at))

                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    ins += 1
                else:
                    skip += 1
            except Exception:
                skip += 1
        for r in rejected:
            try:
                conn.execute("""INSERT OR IGNORE INTO rejected
                    (id, source, title, company, reject_step, reject_reason, search_kw, saved_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (r["id"], r["source"], r["title"], r["company"],
                     r.get("reject_step",""), r.get("reject_reason",""), search_kw, saved_at))
            except Exception:
                pass
        conn.commit()
    return ins, skip
 
def db_load(kw_filter="", priority_filter=None, min_score=0):
    """Load leads from DB with optional filters."""
    q = "SELECT * FROM leads WHERE score >= ?"
    p = [min_score]
    if priority_filter:
        q += " AND priority IN (" + ",".join("?" * len(priority_filter)) + ")"
        p += priority_filter
    if kw_filter.strip():
        k = "%" + kw_filter.strip().lower() + "%"
        q += " AND (LOWER(title) LIKE ? OR LOWER(company) LIKE ? OR LOWER(search_kw) LIKE ?)"
        p += [k, k, k]
    q += " ORDER BY score DESC"
    with get_conn() as conn:
        rows = conn.execute(q, p).fetchall()
    out = []
    for row in rows:
        j = dict(row)
        try:
            j["factors"] = json.loads(j.get("factors") or "{}")
        except Exception:
            j["factors"] = {}
        j["from_db"] = True
        out.append(j)
    return out
 
def db_stats():
    with get_conn() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        high     = conn.execute("SELECT COUNT(*) FROM leads WHERE priority='High'").fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM rejected").fetchone()[0]
        last     = conn.execute("SELECT MAX(saved_at) FROM leads").fetchone()[0]
    return {"total": total, "high": high, "rejected": rejected, "last": (last or "")[:16]}
 
def db_clear():
    with get_conn() as conn:
        #conn.execute("DELETE FROM leads")
        #conn.execute("DELETE FROM rejected")
        #conn.execute("DROP TABLE IF EXISTS leads")
        conn.execute("DROP TABLE IF EXISTS rejected")
        conn.commit()
 
# Initialise DB on startup
init_db()

# ══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════════════
# AI ENGINE  — Claude API for filter, scoring and reason generation
# ══════════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
AI_MODEL          = "claude-sonnet-4-20250514"

def _call_claude(system_prompt, user_prompt, api_key, max_tokens=400):
    """Single call to Claude API. Returns response text or None on error."""
    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": AI_MODEL,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=25,
        )
        data = resp.json()
        if "error" in data:
            return None, data["error"].get("message", "Unknown API error")
        return data["content"][0]["text"].strip(), None
    except Exception as e:
        return None, str(e)


# ── AI Upgrade 1: Company + role classifier ──────────────────────────────────
AI_FILTER_SYSTEM = """You are a lead qualification expert for a software dev services company (VE).
Your job: decide if a job posting represents a company that could realistically become a client.

We want: startups, scale-ups, SMBs (10–500 people) that are growing and would benefit from
outsourced dev capacity, remote staff augmentation, or flexible tech talent.

We do NOT want:
- Large enterprises (Netflix, Nike, Google, Accenture, Big 4, Fortune 500, etc.)
- Companies clearly hiring only to fill in-house permanent roles with no outsourcing signal
- Onsite-only roles with no remote flexibility
- Roles completely unrelated to tech, product, marketing, operations or growth

Respond ONLY with valid JSON. No explanation outside the JSON.
Format exactly:
{"pass": true, "reason": "one sentence why this is a good lead"}
OR
{"pass": false, "reason": "one sentence why this is rejected", "step": "Step 1|Step 2|Step 3|Step 4"}"""

def _text(job):
    """Full searchable text: title + company + description, lowercased."""
    return (job.get("title","") + " " + job.get("company","") + " " + job.get("description","")).lower()

def ai_hard_filter(job, api_key):
    """
    AI-powered replacement for rule-based hard_filter.
    Returns (passed:bool, step:str, reason:str).
    Falls back to rule-based if API call fails.
    """
    prompt = f"""Job title: {job.get('title','')}
Company: {job.get('company','')}
Location: {job.get('location','')}
Description (first 400 chars): {job.get('description','')[:400]}

Should this lead pass our quality gate?"""

    text, err = _call_claude(AI_FILTER_SYSTEM, prompt, api_key, max_tokens=120)
    if err or not text:
        # Fallback to rule-based
        return hard_filter(job)
    try:
        # Strip possible markdown fences
        clean = re.sub(r"```json|```", "", text).strip()
        result = json.loads(clean)
        passed = bool(result.get("pass", False))
        reason = result.get("reason", "")
        step   = result.get("step", "") if not passed else ""
        return passed, step, reason
    except Exception:
        return hard_filter(job)


# ── AI Upgrade 2: Holistic lead scoring ──────────────────────────────────────
AI_SCORE_SYSTEM = """You are a sales intelligence analyst for a software dev outsourcing company (VE).
Score job postings based on how likely the hiring company is to buy from us.

Scoring criteria (respond with exact integer scores):
- role_relevance: 0-3 (how well the role matches services we offer: dev, growth, ops, marketing)
- hiring_intent: 0-3 (urgency, volume, expansion signals)
- company_fit: 0-2 (startup/SMB/scaling, not enterprise)
- remote_signal: 0-2 (remote-friendly, distributed, open to external talent)
- buying_trigger: 0-3 (multiple roles, growth stage, cost/speed/flexibility need)

Also write:
- buy_reason: one punchy sentence answering "Why will this company buy from us?"
  Be specific. Use actual signals from the posting. Not generic.
  BAD: "They are hiring and may need help"
  GOOD: "Series B fintech hiring 4 growth roles in 60 days — classic scaling crunch, ideal for flex staffing"

Respond ONLY with valid JSON, no markdown:
{"role_relevance":2,"hiring_intent":3,"company_fit":2,"remote_signal":1,"buying_trigger":2,
 "buy_reason":"..."}"""

# List of job roles your company can actually serve
# Filter jobs that are relevant to your services.
# SERVICEABLE_ROLES = [
#     "marketing", "growth", "performance marketer", "demand generation", "seo", "sem",
#     "paid media", "paid search", "paid social", "content", "brand", "social media",
#     "email marketing", "product marketing", "revenue", "operations", "ops", "strategy",
#     "business development", "partnerships", "go-to-market", "gtm", "sales",
#     "account manager", "customer success", "performance", "digital marketing",
#     "data analyst", "analytics", "community", "influencer", "pr ", "public relations",
#     "copywriter", "copywriting", "media buyer", "media planning",
# ]

SERVICEABLE_ROLES = [
    "ai/ml", "engineer", "wordpress", "developer", "ai", "engineer", "machine learning", "laravel", "php"
]
# Keywords that indicate large enterprise companies
# Avoid companies that won’t outsource easily
# ENTERPRISE_SIGNALS = [
#     "fortune 500", "fortune500", "s&p 500", "global enterprise", "100,000+", "50,000+",
#     "10,000+ employees", "worldwide offices", "publicly traded", "nasdaq listed",
#     "nyse listed", "inc 500 company",
# ]
 
ENTERPRISE_SIGNALS = [
    "publicly traded", "nasdaq listed"
]
#Hardcoded list of big companies
# Instant rejection of big brands
# KNOWN_ENTERPRISE = [
#     "netflix", "nike", "amazon", "google", "meta", "microsoft", "apple", "linkedin",
#     "salesforce", "oracle", "ibm", "accenture", "deloitte", "mckinsey", "pwc", "kpmg",
#     "ey ", "ernst & young", "bain ", "bcg ", "booz", "jpmorgan", "goldman sachs",
#     "bank of america", "citibank", "wells fargo", "walmart", "procter & gamble",
#     "unilever", "nestlé", "nestle", "coca-cola", "pepsico", "johnson & johnson",
#     "pfizer", "abbvie", "eli lilly", "chevron", "shell ", "bp ", "exxon", "boeing",
#     "lockheed", "raytheon", "general electric", "ge ", "ford ", "gm ", "toyota",
#     "volkswagen", "samsung", "lg ", "sony ", "tencent", "alibaba", "baidu",
#     "uber", "lyft", "airbnb", "doordash", "palantir", "snowflake", "stripe",
#     "shopify", "hubspot", "zendesk", "twilio", "atlassian", "servicenow",
#     "workday", "sap ", "adobe ", "autodesk", "intuit", "paypal",
# ]

KNOWN_ENTERPRISE = [
    "netflix"
]
# ICP = Ideal Customer Profile
# What it means:
# Signals that company is a startup
# ICP_STARTUP = [
#     "startup", "early-stage", "seed", "series a", "series b", "pre-ipo",
#     "founded in 20", "founded 20", "we are a small", "small team",
#     "bootstrapped", "venture-backed", "newly funded", "recently funded",
# ]
ICP_STARTUP = ["freelance","developer","atilus","marketing"]
# Company is growing fast
# Growing companies:
# ➡ Need extra help → perfect outsourcing clients
# ICP_SCALING = [
#     "scaling", "rapidly growing", "fast-growing", "hypergrowth",
#     "expanding team", "growing team", "team expansion", "building out",
#     "hiring across", "we are growing", "join our growing",
# ]

ICP_SCALING = []
# Company is remote-friendly
# Remote companies are:
# ✅ Open to offshore / outsourced teams
# ICP_REMOTE = [
#     "remote", "distributed", "work from anywhere", "fully remote",
#     "remote-first", "remote friendly", "hybrid", "async", "asynchronous",
#     "global team", "international team", "work from home",
# ]

ICP_REMOTE = []
# Signals company is already open to outsourcing
# This is a strong buying signal
# ➡ They already understand outsourcing
# ICP_OUTSOURCE = [
#     "lean team", "small team", "tight budget", "cost-effective",
#     "flexible", "fast turnaround", "contractor", "freelancer",
#     "agency partner", "outsource", "offshore", "nearshore", "staff aug",
# ]

ICP_OUTSOURCE = ["outsource","freelance"]

# Company is under pressure / needs help urgently
# Detect pain points

# ➡ Pain = higher chance to buy
# CAPACITY_SIGNALS = [
#     "immediately", "urgently", "asap", "as soon as possible", "urgent hire",
#     "multiple openings", "several positions", "rapidly", "quickly", "fast-paced",
#     "we are building", "we are expanding", "we are scaling", "newly created role",
#     "new role", "first hire", "building the team", "team of one", "currently a team of",
#     "extra capacity", "additional support", "bandwidth", "overwhelmed", "need help",
#     "growing workload", "increasing demand", "new market", "new product launch",
# ]

CAPACITY_SIGNALS = [
    "immediately", "urgently"
]
# Jobs that require physical presence
# Remote / offshore work

# So these are hard blockers
# ONSITE_BLOCKERS = [
#     "onsite only", "on-site only", "must be in office", "in-person only",
#     "no remote", "not remote", "local candidates only", "relocation required",
#     "must relocate", "in office 5 days", "5 days in office",
# ]

ONSITE_BLOCKERS = [
    "remote", "distributed"
]
# Role is relevant ✅
# Not enterprise ❌
# Has startup/scaling/remote signal ✅
# Shows urgency or growth ✅
# Not onsite-only ❌

def ai_score_lead(job, company_open_roles, api_key):
    """
    AI-powered holistic scoring. Falls back to rule-based score_lead on failure.
    """
    prompt = f"""Job title: {job.get('title','')}
Company: {job.get('company','')}
Location: {job.get('location','')}
Open roles at this company in our dataset: {company_open_roles}
Description: {job.get('description','')[:500]}
Salary info: {job.get('salary','')}
Schedule: {job.get('schedule','')}

Score this lead."""

    text, err = _call_claude(AI_SCORE_SYSTEM, prompt, api_key, max_tokens=200)
    if err or not text:
        return score_lead(job)   # fallback
    try:
        clean  = re.sub(r"```json|```", "", text).strip()
        result = json.loads(clean)

        f_role    = (int(result.get("role_relevance", 0)), "")
        f_intent  = (int(result.get("hiring_intent",  0)), "")
        f_fit     = (int(result.get("company_fit",    0)), "")
        f_remote  = (int(result.get("remote_signal",  0)), "")
        f_trigger = (int(result.get("buying_trigger", 0)), "")

        total = min(13, f_role[0]+f_intent[0]+f_fit[0]+f_remote[0]+f_trigger[0])

        if total >= 10:   priority = "High"
        elif total >= 7:  priority = "Medium"
        else:             priority = "Low"

        buy_reason  = result.get("buy_reason", "").strip()
        step_trace  = (f"AI·role={f_role[0]}/3 intent={f_intent[0]}/3 "
                       f"fit={f_fit[0]}/2 remote={f_remote[0]}/2 trigger={f_trigger[0]}/3")

        job["score"]      = total
        job["priority"]   = priority
        job["buy_reason"] = buy_reason if buy_reason else "See factor scores"
        job["step_trace"] = step_trace
        job["factors"]    = {
            "role_relevance": f_role,
            "hiring_intent":  f_intent,
            "company_fit":    f_fit,
            "remote_signal":  f_remote,
            "buying_trigger": f_trigger,
        }
        job["scored_by"] = "ai"
        return job
    except Exception:
        job["scored_by"] = "rules_fallback"
        return score_lead(job)


# ── AI Upgrade 3: Sales-ready reason rewriter ─────────────────────────────────
AI_REASON_SYSTEM = """You are a sharp B2B sales copywriter for a software dev outsourcing company.
Rewrite the "why this company will buy from us" sentence.

Rules:
- Max 20 words
- Reference specific signals from the data (company size, role count, growth stage, urgency)
- Answer EXACTLY: why is this company likely to BUY from us?
- Sound like a human sales note, not a robot
- No filler words like "potentially", "may", "might", "could"
- If there are no strong signals, say so honestly in one short sentence

Return ONLY the sentence. No quotes, no punctuation at the end."""

def ai_rewrite_reason(job, api_key):
    """Post-process: rewrite buy_reason for all passed leads in a batch."""
    prompt = (f"Company: {job.get('company','')} | "
              f"Role: {job.get('title','')} | "
              f"Score: {job.get('score',0)}/13 | "
              f"Open roles: {job.get('company_open_roles',1)} | "
              f"Current reason: {job.get('buy_reason','')} | "
              f"Signals: {job.get('step_trace','')}")
    text, err = _call_claude(AI_REASON_SYSTEM, prompt, api_key, max_tokens=60)
    if not err and text:
        job["buy_reason"] = text
        job["reason_by"]  = "ai"
    return job

def hard_filter(job):
    """
    7-step quality gate. Returns (passed:bool, reject_step:str, reject_reason:str).
    Must be called BEFORE scoring.
    """
    title   = job.get("title","").lower()
    company = job.get("company","").lower()
    text    = _text(job)

    # Step 1 — role relevance
    role_match = any(r in title or r in text[:200] for r in SERVICEABLE_ROLES)
    if not role_match:
        return False, "Step 1", f"Role not serviceable: '{job.get('title','')}'"

    # Step 2a — known enterprise blocklist
    for ent in KNOWN_ENTERPRISE:
        if ent in company:
            return False, "Step 2", f"Known enterprise: {job.get('company','')} — unlikely to outsource"

    # Step 2b — enterprise language in JD
    ent_hits = [s for s in ENTERPRISE_SIGNALS if s in text]
    if len(ent_hits) >= 2:
        return False, "Step 2", f"Enterprise signals: {', '.join(ent_hits[:2])}"

    # Step 2c — must have at least one ICP signal (startup OR scaling OR remote OR outsource)
    matches1 = [s for s in ICP_STARTUP if s in text]
    print(f"Matches1 ({len(matches1)}): {matches1} : {text}")
    matches2 = [s for s in ICP_SCALING if s in text]
    print(f"Matches2 ({len(matches2)}): {matches2} : {text}")
    matches3 = [s for s in ICP_REMOTE if s in text]
    print(f"Matches3 ({len(matches3)}): {matches3} : {text}")
    matches4 = [s for s in ICP_OUTSOURCE if s in text]
    print(f"Matches4 ({len(matches4)}): {matches4} : {text}")

    icp_hit = (any(s in text for s in ICP_STARTUP) or
               any(s in text for s in ICP_SCALING) or
               any(s in text for s in ICP_REMOTE)  or
               any(s in text for s in ICP_OUTSOURCE))
    if not icp_hit:
        return False, "Step 2", "No ICP signal — not startup/scaling/remote/outsource-friendly"

    # Step 3 — role signal: does this role give us confidence the company needs help?
    cap_hit = any(s in text for s in CAPACITY_SIGNALS)
    multi   = job.get("company_open_roles", 1) >= 2
    if not cap_hit and not multi:
        return False, "Step 3", "No capacity signal — no urgency, volume or expansion language"

    # Step 4 — onsite-only hard block
    onsite = any(s in text for s in ONSITE_BLOCKERS)
    if onsite:
        return False, "Step 4", "Onsite-only role — remote staffing not viable"

    return True, "", ""


def score_lead(job):
    """
    Step 6 scoring — only called for jobs that passed hard_filter.
    Returns enriched job with score /13, priority, buy_reason, step_trace, factors.
    """
    text  = _text(job)
    title = job.get("title","").lower()

    # ── Factor 1: Role relevance (0–3) ───────────────────────────────────
    role_hits = sum(1 for r in SERVICEABLE_ROLES if r in title or r in text[:300])
    if role_hits >= 4:   f_role = (3, "exact role match")
    elif role_hits >= 2: f_role = (2, "strong role match")
    elif role_hits == 1: f_role = (1, "partial role match")
    else:                f_role = (0, "weak match")

    # ── Factor 2: Hiring intent (0–3) ────────────────────────────────────
    cap_hits  = sum(1 for s in CAPACITY_SIGNALS if s in text)
    multi_roles = job.get("company_open_roles", 1)
    if cap_hits >= 3 or multi_roles >= 4:   f_intent = (3, f"{multi_roles} roles · urgent")
    elif cap_hits >= 2 or multi_roles >= 2: f_intent = (2, f"{multi_roles} roles · clear intent")
    elif cap_hits >= 1:                     f_intent = (1, "some urgency signal")
    else:                                   f_intent = (0, "low intent signal")

    # ── Factor 3: Company fit / ICP (0–2) ────────────────────────────────
    startup_hit  = any(s in text for s in ICP_STARTUP)
    scaling_hit  = any(s in text for s in ICP_SCALING)
    outsrc_hit   = any(s in text for s in ICP_OUTSOURCE)
    fit_signals  = sum([startup_hit, scaling_hit, outsrc_hit])
    if fit_signals >= 2:   f_fit = (2, "startup + scaling")
    elif fit_signals == 1: f_fit = (1, "one ICP signal")
    else:                  f_fit = (0, "weak ICP fit")

    # ── Factor 4: Remote signal (0–2) ────────────────────────────────────
    remote_hits = sum(1 for r in ICP_REMOTE if r in text)
    if remote_hits >= 3:   f_remote = (2, "remote-first")
    elif remote_hits >= 1: f_remote = (1, "remote-friendly")
    else:                  f_remote = (0, "no remote signal")

    # ── Factor 5: Buying trigger (0–3) ───────────────────────────────────
    trigger_score = 0
    trigger_parts = []
    if multi_roles >= 3:
        trigger_score += 2
        trigger_parts.append(f"{multi_roles} open roles")
    elif multi_roles >= 2:
        trigger_score += 1
        trigger_parts.append("multiple roles")
    growth_hits = sum(1 for s in ICP_SCALING if s in text)
    if growth_hits >= 2:
        trigger_score += 1
        trigger_parts.append("strong growth")
    elif growth_hits == 1:
        trigger_score += 1
        matched_signals = [s for s in ICP_SCALING if s in text]
        trigger_parts.append(matched_signals[0] if matched_signals else "growth signal")
    cap_strong = any(s in text for s in ["immediately","asap","urgent","as soon as possible"])
    if cap_strong:
        trigger_score += 1
        trigger_parts.append("urgent hire")
    f_trigger = (min(3, trigger_score), " · ".join(trigger_parts) if trigger_parts else "no strong trigger")

    total = f_role[0] + f_intent[0] + f_fit[0] + f_remote[0] + f_trigger[0]
    total = min(13, total)

    # Priority bands: /13
    if total >= 10:   priority = "High"
    elif total >= 7:  priority = "Medium"
    else:             priority = "Low"

    # ── Build "Why will they BUY from us?" answer ─────────────────────────
    buy_parts = []
    if f_intent[0] >= 2:
        buy_parts.append(f_intent[1])
    if f_trigger[0] >= 2 and f_trigger[1]:
        buy_parts.append(f_trigger[1])
    if f_fit[0] >= 1:
        buy_parts.append(f_fit[1])
    if f_remote[0] >= 1:
        buy_parts.append(f_remote[1])
    if f_role[0] >= 2:
        buy_parts.append(f_role[1])
    if not buy_parts:
        buy_parts = [f_role[1], f_intent[1]]
    #print(buy_parts)    
    unique_parts = [p for p in dict.fromkeys(buy_parts) if p]
    buy_reason = " · ".join(unique_parts[:4])
    #buy_reason = " · ".join(dict.fromkeys(buy_parts)[:4])  # dedupe, max 4

    # ── Step trace for transparency ───────────────────────────────────────
    step_trace = (
        f"S1:role={f_role[0]}/3 "
        f"S2:intent={f_intent[0]}/3 "
        f"S3:fit={f_fit[0]}/2 "
        f"S4:remote={f_remote[0]}/2 "
        f"S5:trigger={f_trigger[0]}/3"
    )

    job["score"]      = total
    job["priority"]   = priority
    job["buy_reason"] = buy_reason
    job["step_trace"] = step_trace
    job["factors"]    = {
        "role_relevance": f_role,
        "hiring_intent":  f_intent,
        "company_fit":    f_fit,
        "remote_signal":  f_remote,
        "buying_trigger": f_trigger,
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

def parse_date(s):
    if not s: return None
    for fmt in ["%Y-%m-%dT%H:%M:%S%z","%Y-%m-%dT%H:%M:%SZ","%Y-%m-%d",
                "%a, %d %b %Y %H:%M:%S %z","%a, %d %b %Y %H:%M:%S GMT"]:
        try: return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except: pass
    m = re.search(r"(\d+)\s*(day|hour|week|month)", s.lower())
    if m:
        n,u = int(m.group(1)), m.group(2)
        d = {"day":timedelta(days=n),"hour":timedelta(hours=n),
             "week":timedelta(weeks=n),"month":timedelta(days=n*30)}.get(u)
        if d: return datetime.now(timezone.utc) - d
    return None

def fetch_serpapi(kw, loc, key, num=30):
    jobs = []
    if not key: return jobs
    l = loc if loc and loc.lower() != "worldwide" else ""
    try:
        resp = requests.get("https://serpapi.com/search", timeout=20,
                            params={"engine":"google_jobs","q":kw,"location":l,"num":num,"api_key":key})
        data = resp.json()
        if "error" in data:
            st.session_state["serp_error"] = data["error"]; return jobs
        for j in data.get("jobs_results",[]):
            exts = j.get("detected_extensions",{})
            b = _base("serpapi")
            b.update({"id":uid(j.get("job_id",j.get("title","")+j.get("company_name",""))),
                      "title":j.get("title",""),"company":j.get("company_name",""),
                      "location":j.get("location",""),"url":j.get("share_link",""),
                      "posted_at":exts.get("posted_at",""),
                      "description":j.get("description","")[:700],
                      "salary":exts.get("salary",""),"schedule":exts.get("schedule_type","")})
            jobs.append(b)
    except Exception as e: st.session_state["serp_error"] = str(e)
    return jobs

def fetch_linkedin(kw, loc="worldwide"):
    k = kw.replace(" ","%20")
    url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
           "?keywords="+k+"&location="+loc+"&start=0")
    jobs = []
    try:
        resp = requests.get(url,timeout=12,
                            headers={"User-Agent":"Mozilla/5.0 (compatible; JobScanner/1.0)"})
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text,"html.parser")
            for card in soup.find_all("div",class_="base-card"):
                te=card.find("h3",class_="base-search-card__title")
                ce=card.find("h4",class_="base-search-card__subtitle")
                le=card.find("span",class_="job-search-card__location")
                ae=card.find("a",class_="base-card__full-link")
                de=card.find("time")
                if te and ce:
                    b=_base("linkedin")
                    b.update({"id":uid(ae["href"] if ae else str(te)),
                              "title":te.get_text(strip=True),"company":ce.get_text(strip=True),
                              "location":le.get_text(strip=True) if le else "",
                              "url":ae["href"].split("?")[0] if ae else "",
                              "posted_at":de.get("datetime","") if de else ""})
                    jobs.append(b)
    except: pass
    return jobs

def fetch_indeed(kw):
    k=kw.replace(" ","+")
    url="https://www.indeed.com/rss?q="+k+"&sort=date&fromage=14"
    jobs=[]
    try:
        feed=feedparser.parse(url,request_headers={"User-Agent":"Mozilla/5.0"})
        for e in feed.entries:
            b=_base("indeed")
            b.update({"id":uid(e.get("link",e.get("title",""))),
                      "title":e.get("title",""),"company":e.get("author",""),
                      "url":e.get("link",""),"posted_at":e.get("published",""),
                      "description":BeautifulSoup(e.get("summary",""),"html.parser").get_text()[:600]})
            jobs.append(b)
    except: pass
    return jobs

def fetch_remotive(kw):
    jobs=[]
    try:
        resp=requests.get("https://remotive.com/api/remote-jobs",
                          params={"search":kw,"limit":50},timeout=12)
        if resp.status_code==200:
            for r in resp.json().get("jobs",[]):
                b=_base("remotive")
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
    seen,clean=[],[]
    ids=set()
    for job in raw:
        if job["id"] in ids: continue
        ids.add(job["id"])
        for f in ("title","company","description"):
            job[f]=re.sub(r"\s+"," ",job.get(f,"")).strip()
        clean.append(job)
    return clean

def run_pipeline(keywords, sources, location, serpapi_key, status_ph):
    raw=[]
    st.session_state.pop("serp_error",None)
    for kw in keywords:
        status_ph.markdown(
            '<div class="status-line">→ fetching <b>'+kw+'</b>…</div>',
            unsafe_allow_html=True)
        if "SerpApi (Google Jobs)" in sources: raw+=fetch_serpapi(kw,location,serpapi_key)
        if "LinkedIn"  in sources: raw+=fetch_linkedin(kw,location)
        if "Indeed"    in sources: raw+=fetch_indeed(kw)
        if "Remotive"  in sources: raw+=fetch_remotive(kw)
        time.sleep(0.6)

    status_ph.markdown(
        '<div class="status-line">→ applying quality gate to '+str(len(raw))+' results…</div>',
        unsafe_allow_html=True)
    jobs = normalize(raw)

    # Group by company for multi-role count
    by_company = defaultdict(list)
    for j in jobs:
        by_company[j["company"].lower()].append(j)
    for j in jobs:
        j["company_open_roles"] = len(by_company[j["company"].lower()])

    passed, rejected = [], []
    for job in jobs:
        ok, step, reason = hard_filter(job)
        if not ok:
            rejected.append({"id":job["id"],"source":job["source"],
                             "title":job["title"],"company":job["company"],
                             "reject_step":step,"reject_reason":reason})
        else:
            score_lead(job)
            passed.append(job)

    passed.sort(key=lambda j: j["score"], reverse=True)
    status_ph.empty()
    return passed, rejected

# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE  (AI-aware version)
# ══════════════════════════════════════════════════════════════════════════════
def normalize(raw):
    seen, clean = set(), []
    for job in raw:
        if job["id"] in seen: continue
        seen.add(job["id"])
        for f in ("title","company","description"):
            job[f] = re.sub(r"\s+", " ", job.get(f,"")).strip()
        clean.append(job)
    return clean


def run_pipeline(keywords, sources, location, serpapi_key, ai_key, ai_mode, status_ph):
    """
    ai_mode: "off" | "filter_only" | "score_only" | "full"
    """
    raw = []
    st.session_state.pop("serp_error", None)

    use_ai_filter = ai_mode in ("filter_only", "full") and bool(ai_key)
    use_ai_score  = ai_mode in ("score_only",  "full") and bool(ai_key)
    use_ai_reason = ai_mode == "full" and bool(ai_key)

    for kw in keywords:
        status_ph.markdown(
            '<div class="status-line">→ fetching <b>'+kw+'</b>…</div>',
            unsafe_allow_html=True)
        if "SerpApi (Google Jobs)" in sources: raw += fetch_serpapi(kw, location, serpapi_key)
        if "LinkedIn"  in sources: raw += fetch_linkedin(kw, location)
        if "Indeed"    in sources: raw += fetch_indeed(kw)
        if "Remotive"  in sources: raw += fetch_remotive(kw)
        time.sleep(0.6)

    jobs = normalize(raw)

    # Attach company open-role count for scoring context
    by_company = defaultdict(list)
    for j in jobs: by_company[j["company"].lower()].append(j)
    for j in jobs: j["company_open_roles"] = len(by_company[j["company"].lower()])

    # ── Stage 1: Hard filter ──────────────────────────────────────────────
    filter_label = "AI filter" if use_ai_filter else "rule filter"
    status_ph.markdown(
        '<div class="status-line">→ applying '+filter_label+' to '+str(len(jobs))+' jobs…</div>',
        unsafe_allow_html=True)

    passed, rejected = [], []
    for i, job in enumerate(jobs):
        if use_ai_filter:
            ok, step, reason = ai_hard_filter(job, ai_key)
            time.sleep(0.3)   # gentle rate limiting
        else:
            ok, step, reason = hard_filter(job)

        if not ok:
            rejected.append({"id":job["id"],"source":job["source"],
                             "title":job["title"],"company":job["company"],
                             "reject_step":step,"reject_reason":reason})
        else:
            passed.append(job)

        if (i+1) % 10 == 0:
            status_ph.markdown(
                '<div class="status-line">→ filtering… '+str(i+1)+'/'+str(len(jobs))+'</div>',
                unsafe_allow_html=True)

    # ── Stage 2: Score ────────────────────────────────────────────────────
    score_label = "AI scoring" if use_ai_score else "rule scoring"
    status_ph.markdown(
        '<div class="status-line">→ '+score_label+' '+str(len(passed))+' leads…</div>',
        unsafe_allow_html=True)

    for i, job in enumerate(passed):
        if use_ai_score:
            ai_score_lead(job, job.get("company_open_roles",1), ai_key)
            time.sleep(0.3)
        else:
            score_lead(job)
            job["scored_by"] = "rules"

        if (i+1) % 5 == 0:
            status_ph.markdown(
                '<div class="status-line">→ scoring… '+str(i+1)+'/'+str(len(passed))+'</div>',
                unsafe_allow_html=True)

    # ── Stage 3: Rewrite reasons (only in full AI mode) ───────────────────
    if use_ai_reason and passed:
        status_ph.markdown(
            '<div class="status-line">→ AI rewriting buy-reasons…</div>',
            unsafe_allow_html=True)
        for job in passed:
            ai_rewrite_reason(job, ai_key)
            time.sleep(0.2)

    passed.sort(key=lambda j: j["score"], reverse=True)
    status_ph.empty()
    return passed, rejected

# RENDER HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def score_color(s):
    if s >= 10: return "#ef4444"
    if s >= 7:  return "#f59e0b"
    return "#94a3b8"

def bar_pct(s, mx=13):
    return str(round(s/mx*100))+"%"

FACTOR_LABELS = {
    "role_relevance":"role","hiring_intent":"intent",
    "company_fit":"fit","remote_signal":"remote","buying_trigger":"trigger"
}
FACTOR_MAX = {
    "role_relevance":"3","hiring_intent":"3",
    "company_fit":"2","remote_signal":"2","buying_trigger":"3"
}

def render_stats(passed, rejected_count, fetched_total, extra=""):
    high_n = sum(1 for j in passed if j["priority"]=="High")
    med_n  = sum(1 for j in passed if j["priority"]=="Medium")
    low_n  = sum(1 for j in passed if j["priority"]=="Low")
    pass_pct = round(len(passed)/max(fetched_total,1)*100)
    st.markdown(
        '<div class="stats-bar">'
        '<div class="stat-item"><div class="stat-val">'+str(fetched_total)+'</div><div class="stat-lbl">'+extra+'Fetched</div></div>'
        '<div class="stat-item"><div class="stat-val" style="color:#22c55e">'+str(len(passed))+'</div><div class="stat-lbl">Passed gate</div></div>'
        '<div class="stat-item"><div class="stat-val" style="color:#ef4444">'+str(rejected_count)+'</div><div class="stat-lbl">Rejected</div></div>'
        '<div class="stat-item"><div class="stat-val" style="color:#ef4444">'+str(high_n)+'</div><div class="stat-lbl">High</div></div>'
        '<div class="stat-item"><div class="stat-val" style="color:#f59e0b">'+str(med_n)+'</div><div class="stat-lbl">Medium</div></div>'
        '<div class="stat-item"><div class="stat-val" style="color:#94a3b8">'+str(low_n)+'</div><div class="stat-lbl">Low</div></div>'
        '<div class="stat-item"><div class="stat-val">'+str(pass_pct)+'%</div><div class="stat-lbl">Quality rate</div></div>'
        '</div>',
        unsafe_allow_html=True)
    # Quality bar
    rej_w  = str(round(rejected_count/max(fetched_total,1)*100))
    pass_w = str(pass_pct)
    st.markdown(
        '<div class="quality-bar">'
        '<div style="width:'+pass_w+'%;background:#22c55e;"></div>'
        '<div style="width:'+rej_w+'%;background:#fca5a5;"></div>'
        '</div>',
        unsafe_allow_html=True)

def render_leads(filtered, view_mode, show_trace=False):
    if not filtered:
        st.markdown(
            '<div class="empty-state"><div class="empty-icon">🔍</div>'
            '<div class="empty-title">No qualifying leads found</div>'
            '<div>The quality gate filtered all results — try different keywords or sources</div></div>',
            unsafe_allow_html=True)
        return

    c1, c2, _ = st.columns([2,2,3])
    with c1:
        sort_by = st.selectbox("Sort",["Score ↓","Score ↑","Company A–Z"],
                               label_visibility="collapsed")
    with c2:
        avail = sorted({j["source"] for j in filtered})
        src_f = st.multiselect("Source", avail, default=avail,
                               label_visibility="collapsed")

    if sort_by=="Score ↓": filtered.sort(key=lambda j:j["score"],reverse=True)
    elif sort_by=="Score ↑": filtered.sort(key=lambda j:j["score"])
    else: filtered.sort(key=lambda j:j["company"].lower())
    if src_f: filtered=[j for j in filtered if j["source"] in src_f]

    if view_mode=="Table":
        rows=""
        for job in filtered:
            p,sc=job["priority"].lower(),job["score"]
            url=job.get("url","")
            tc=('<a href="'+url+'" target="_blank" style="color:#111827;text-decoration:none;font-weight:500;">'
                +job["title"]+' ↗</a>') if url else job["title"]
            rows+=("<tr><td>"+job["company"]+"</td><td>"+tc+"</td>"
                   "<td>"+job.get("location","")+"</td>"
                   '<td class="score-td"><span class="badge badge-'+p+'">'+job["priority"]+"</span></td>"
                   '<td class="score-td" style="color:'+score_color(sc)+'">'+str(sc)+"/13</td>"
                   "<td style='font-size:0.73rem;color:#374151;'>"+job.get("buy_reason","")+"</td></tr>")
        st.markdown(
            '<div style="overflow-x:auto"><table class="tbl"><thead><tr>'
            '<th>Company</th><th>Role</th><th>Location</th>'
            '<th>Priority</th><th>Score</th><th>Why they will buy</th>'
            "</tr></thead><tbody>"+rows+"</tbody></table></div>",
            unsafe_allow_html=True)
    else:
        for job in filtered:
            p,sc=job["priority"].lower(),job["score"]
            sc_c=score_color(sc)
            factors=job.get("factors",{})
            url=job.get("url","")

            pills=""
            for fk,fl in FACTOR_LABELS.items():
                fval,ftxt=factors.get(fk,(0,""))
                cls=("factor-pill scored" if fval>0 else "factor-pill zero")
                pills+=('<span class="'+cls+'">'+fl+" "+str(fval)+"/"+FACTOR_MAX[fk]
                        +(" · "+ftxt if ftxt else "")+"</span>")

            src_cls=("source-serp" if job["source"]=="serpapi" else
                     "source-db"   if job.get("from_db") else "")
            link_html=('<a href="'+url+'" target="_blank" style="font-size:0.7rem;color:#6366f1;'
                       'text-decoration:none;margin-left:6px;">↗ view job</a>') if url else ""
            salary_html=('<span style="font-size:0.7rem;color:#059669;font-weight:500;margin-left:8px;">'
                         +job["salary"]+"</span>") if job.get("salary") else ""
            meta_parts=[p2 for p2 in [job.get("location",""),job.get("posted_at","")[:10]] if p2]
            meta_html=" · ".join(meta_parts)
            saved_badge=('<span class="badge badge-saved" style="font-size:0.58rem;margin-left:5px;">saved</span>'
                         ) if job.get("from_db") else ""
            trace_html=('<div class="step-trace">'+job.get("step_trace","")+"</div>"
                        ) if show_trace and job.get("step_trace") else ""

            st.markdown(
                '<div class="lead-card '+p+'">'
                '<div style="display:flex;align-items:flex-start;gap:1rem;">'
                '<div style="flex:1;min-width:0;">'
                '<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-bottom:4px;">'
                '<span class="badge badge-'+p+'">'+job["priority"]+' Priority</span>'
                '<span class="source-pill '+src_cls+'">'+job["source"]+"</span>"
                +saved_badge+salary_html+link_html
                +"</div>"
                '<p class="lead-title">'+job["title"]+"</p>"
                '<p class="lead-company">'+job["company"]+"</p>"
                +('<p class="lead-meta">'+meta_html+"</p>" if meta_html else "")
                +'<div style="margin-top:6px">'+pills+"</div>"
                +'<div class="buy-reason"><strong>Why they will buy:</strong> '+job.get("buy_reason","")+"</div>"
                +trace_html
                +"</div>"
                '<div class="score-wrap" style="flex-shrink:0;">'
                '<div class="score-main" style="color:'+sc_c+'">'+str(sc)+"</div>"
                '<div class="score-denom">/13</div>'
                '<div class="score-bar-bg">'
                '<div class="score-bar-fill" style="width:'+bar_pct(sc)+';background:'+sc_c+'"></div>'
                "</div></div></div></div>",
                unsafe_allow_html=True)

def render_rejected(rejected, limit=30):
    if not rejected: return
    with st.expander(f"🚫 Rejected by quality gate ({len(rejected)} leads filtered out)", expanded=False):
        st.markdown(
            '<div style="font-size:0.75rem;color:#6b7280;margin-bottom:8px;">'
            'These leads did not pass the hard filter and were NOT scored. '
            'This is intentional — quality over quantity.</div>',
            unsafe_allow_html=True)
        for r in rejected[:limit]:
            step_color={"Step 1":"#6366f1","Step 2":"#ef4444",
                        "Step 3":"#f59e0b","Step 4":"#64748b"}.get(r.get("reject_step",""),"#9ca3af")
            st.markdown(
                '<div class="rejected-card">'
                '<div style="flex:1;min-width:0;">'
                '<div class="rejected-title">'+r.get("title","")+"</div>"
                '<div class="rejected-company">'+r.get("company","")+" · "+r.get("source","")+"</div>"
                +"</div>"
                '<div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">'
                '<span style="font-size:0.65rem;font-weight:600;color:'+step_color+';">'
                +r.get("reject_step","")+"</span>"
                '<span class="rejected-reason">'+r.get("reject_reason","")[:60]+"</span>"
                "</div></div>",
                unsafe_allow_html=True)
        if len(rejected) > limit:
            st.caption(f"+ {len(rejected)-limit} more rejected leads not shown")

# ══════════════════════════════════════════════════════════════════════════════

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

.app-header { padding: 1.5rem 0 1rem; border-bottom: 1px solid #e5e7eb; margin-bottom: 1.5rem; }
.app-title  { font-family:'DM Mono',monospace; font-size:1.45rem; font-weight:500; color:#111827; letter-spacing:-0.02em; margin:0; }
.app-sub    { font-size:0.8rem; color:#6b7280; margin-top:0.2rem; }

.mode-bar { display:flex; gap:10px; padding:9px 14px; border:1px solid #e5e7eb; border-radius:8px;
            margin-bottom:1.25rem; align-items:center; font-size:0.78rem; }
.mode-bar.save { background:#f5f3ff; border-color:#ddd6fe; color:#5b21b6; }
.mode-bar.view { background:#f0fdf4; border-color:#bbf7d0; color:#065f46; }
.mode-dot  { width:7px; height:7px; border-radius:50%; flex-shrink:0; }

.badge { display:inline-block; padding:2px 9px; border-radius:99px; font-size:0.67rem;
         font-weight:600; font-family:'DM Mono',monospace; letter-spacing:0.05em; text-transform:uppercase; }
.badge-high     { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; }
.badge-medium   { background:#fffbeb; color:#b45309; border:1px solid #fde68a; }
.badge-low      { background:#f8fafc; color:#64748b; border:1px solid #e2e8f0; }
.badge-rejected { background:#fafafa; color:#9ca3af; border:1px solid #e5e7eb; }
.badge-saved    { background:#f0fdf4; color:#166534; border:1px solid #bbf7d0; }

.factor-pill { display:inline-flex; align-items:center; gap:3px; padding:2px 7px;
               border-radius:4px; font-size:0.63rem; font-family:'DM Mono',monospace;
               background:#f1f5f9; color:#475569; border:1px solid #e2e8f0; margin:2px 2px 0 0; }
.factor-pill.scored { background:#f0fdf4; color:#166534; border-color:#bbf7d0; }
.factor-pill.zero   { background:#fafafa; color:#9ca3af; border-color:#f1f5f9; }

.source-pill { display:inline-block; padding:1px 7px; border-radius:4px; font-size:0.63rem;
               font-family:'DM Mono',monospace; background:#f3f4f6; color:#374151; border:1px solid #e5e7eb; }
.source-serp { background:#faf5ff; color:#7c3aed; border-color:#e9d5ff; }
.source-db   { background:#ecfdf5; color:#065f46; border-color:#6ee7b7; }

.lead-card { background:#fff; border:1px solid #e5e7eb; border-radius:10px;
             padding:1rem 1.25rem; margin-bottom:0.6rem; }
.lead-card.high   { border-left:3px solid #ef4444; }
.lead-card.medium { border-left:3px solid #f59e0b; }
.lead-card.low    { border-left:3px solid #cbd5e1; }

.rejected-card { background:#fafafa; border:1px solid #f1f5f9; border-radius:8px;
                 padding:0.65rem 1rem; margin-bottom:0.4rem; display:flex;
                 align-items:center; gap:10px; }
.rejected-title   { font-size:0.8rem; font-weight:500; color:#6b7280; }
.rejected-company { font-size:0.75rem; color:#9ca3af; }
.rejected-reason  { font-size:0.72rem; color:#ef4444; margin-left:auto; white-space:nowrap;
                    font-family:'DM Mono',monospace; }

.lead-title   { font-size:0.9rem; font-weight:600; color:#111827; margin:0 0 1px; }
.lead-company { font-size:0.8rem; color:#374151; font-weight:500; margin:0; }
.lead-meta    { font-size:0.7rem; color:#9ca3af; margin-top:3px; }
.buy-reason   { font-size:0.8rem; color:#111827; background:#fffbeb; border:1px solid #fde68a;
                border-left:3px solid #f59e0b; border-radius:0 6px 6px 0;
                padding:7px 10px; margin-top:8px; line-height:1.55; }
.buy-reason strong { color:#92400e; }
.step-trace   { font-size:0.7rem; color:#6b7280; background:#f8fafc; border:1px solid #e2e8f0;
                border-radius:5px; padding:5px 9px; margin-top:6px; line-height:1.6; font-family:'DM Mono',monospace; }

.score-wrap  { text-align:center; min-width:52px; }
.score-main  { font-family:'DM Mono',monospace; font-size:1.4rem; font-weight:500; line-height:1; }
.score-denom { font-size:0.6rem; color:#9ca3af; }
.score-bar-bg   { height:3px; background:#e5e7eb; border-radius:2px; overflow:hidden; margin-top:4px; }
.score-bar-fill { height:100%; border-radius:2px; }

.stats-bar { display:flex; gap:0.6rem; padding:0.7rem 1.1rem; background:#f9fafb;
             border:1px solid #e5e7eb; border-radius:8px; margin-bottom:1rem; flex-wrap:wrap; }
.stat-item { text-align:center; flex:1; min-width:60px; }
.stat-val  { font-family:'DM Mono',monospace; font-size:1.1rem; font-weight:500; color:#111827; }
.stat-lbl  { font-size:0.62rem; color:#9ca3af; text-transform:uppercase; letter-spacing:0.04em; }

.quality-bar { display:flex; height:6px; border-radius:3px; overflow:hidden; margin-bottom:1rem; }

.tbl { width:100%; border-collapse:collapse; font-size:0.78rem; }
.tbl th { text-align:left; padding:7px 11px; background:#f9fafb; border-bottom:2px solid #e5e7eb;
          font-weight:600; color:#374151; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.04em; }
.tbl td { padding:8px 11px; border-bottom:1px solid #f1f5f9; vertical-align:top; color:#374151; }
.tbl tr:hover td { background:#fafafa; }
.tbl td.score-td { font-family:'DM Mono',monospace; font-weight:500; text-align:center; white-space:nowrap; }

section[data-testid="stSidebar"] {  border-right:1px solid #e5e7eb; }
section[data-testid="stSidebar"] .block-container { padding:1.5rem 1rem; }
.stButton > button { background:#111827 !important; color:#fff !important; border:none !important;
    border-radius:8px !important; font-weight:500 !important; font-size:0.875rem !important;
    padding:0.6rem 1.5rem !important; width:100% !important; }
.stButton > button:hover { background:#1f2937 !important; }
.api-box { background:#faf5ff; border:1px solid #e9d5ff; border-radius:8px; padding:9px 12px;
           margin-bottom:8px; font-size:0.75rem; color:#6d28d9; }
.api-box a { color:#7c3aed; }
.db-info { background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px;
           padding:8px 12px; font-size:0.75rem; color:#065f46; margin-bottom:8px; }
.db-warn { background:#fffbeb; border:1px solid #fde68a; border-radius:8px;
           padding:8px 12px; font-size:0.75rem; color:#92400e; margin-bottom:8px; }
.empty-state { text-align:center; padding:3rem 2rem; color:#9ca3af; }
.empty-icon  { font-size:2rem; margin-bottom:0.5rem; }
.empty-title { font-size:0.9rem; font-weight:500; color:#4b5563; margin-bottom:0.2rem; }
.status-line { font-family:'DM Mono',monospace; font-size:0.72rem; color:#6b7280; padding:0.2rem 0; }
</style>
""", unsafe_allow_html=True)

    st.markdown("### 🎯 Mode")
    mode = st.radio("mode",
                    ["🔍 Search & Save to DB", "🗄️ View from DB"],
                    label_visibility="collapsed")
    st.markdown("---")

    if mode == "🔍 Search & Save to DB":
        st.markdown("### Search")
        keyword_input = st.text_area(
            "Keywords", height=100, help="One keyword per line",
            value="AI development\nWordpress development\nLaravel development")
        location = st.text_input("Location", value="United States",
                                 help="e.g. United States, Remote, London, worldwide")
        sources = st.multiselect("Sources",
            ["SerpApi (Google Jobs)", "LinkedIn", "Indeed", "Remotive"],
            default=["LinkedIn", "Indeed", "Remotive"])
        serpapi_key = ""
        if "SerpApi (Google Jobs)" in sources:
            st.markdown(
                '<div class="api-box">🔑 SerpApi key — '
                '<a href="https://serpapi.com/manage-api-key" target="_blank">get free key ↗</a></div>',
                unsafe_allow_html=True)
            serpapi_key = st.text_input("SerpApi key", type="password",
                placeholder="paste your SerpApi key…", label_visibility="collapsed")

        st.markdown("---")
        st.markdown("### 🤖 AI Mode")

        # Visual AI mode selector
        # ai_mode = st.radio(
        #     "AI mode",
        #     ["off", "filter_only", "score_only", "full"],
        #     format_func=lambda x: {
        #         "off":         "⚙️  Rules only (free, fast)",
        #         "filter_only": "🔍 AI filter + rule score",
        #         "score_only":  "📊 Rule filter + AI score",
        #         "full":        "✨ Full AI (filter + score + reason)",
        #     }[x],
        #     label_visibility="collapsed",
        # )
        ai_mode = 'off'
        ai_key = ""
        if ai_mode != "off":
            st.markdown(
                '<div class="api-box" style="background:#f0fdf4;border-color:#bbf7d0;color:#065f46;">'
                '🔑 Anthropic key required — '
                '<a href="https://console.anthropic.com/settings/keys" target="_blank" '
                'style="color:#059669;">get key ↗</a>'
                '<br>~$0.002–0.006 per lead evaluated</div>',
                unsafe_allow_html=True)
            ai_key = st.text_input("Anthropic API key", type="password",
                placeholder="sk-ant-…", label_visibility="collapsed")

        # Mode info box
        mode_info = {
            "off":         ("⚙️", "#f9fafb", "#374151", "Keyword rules only. Fast, free. Good starting point."),
            "filter_only": ("🔍", "#faf5ff", "#5b21b6", "AI decides pass/fail. Rules score. Best for noisy sources."),
            "score_only":  ("📊", "#fffbeb", "#92400e", "Rules filter. AI scores holistically. Good balance."),
            "full":        ("✨", "#f0fdf4", "#065f46", "Full AI pipeline. Best quality. ~$0.01–0.03 per search."),
        }
        
        
        icon, bg, col, desc = mode_info[ai_mode]
        st.markdown(
            f'<div style="background:{bg};border:1px solid;border-color:{col}33;border-radius:7px;'
            f'padding:8px 11px;font-size:0.75rem;color:{col};margin-top:6px;">'
            f'<strong>{icon} {ai_mode.replace("_"," ").title()}</strong><br>{desc}</div>',
            unsafe_allow_html=True)

        st.markdown("---")
        view_mode  = st.radio("View", ["Cards", "Table"], horizontal=True)
        show_trace = st.checkbox("Show scoring trace", value=False)
        st.markdown("---")
        search_btn = st.button("Search & Save", use_container_width=True)

    else:
        st.markdown("### Filter DB")
        db_keyword   = st.text_input("Keyword filter", placeholder="company, title, keyword…")
        db_priority  = st.multiselect("Priority", ["High","Medium","Low"], default=["High","Medium"])
        db_min_score = st.slider("Min score (/13)", 0, 13, 5)
        st.markdown("---")
        view_mode  = st.radio("View", ["Cards","Table"], horizontal=True)
        show_trace = st.checkbox("Show scoring trace", value=False)
        stats = db_stats()
        if stats["total"] > 0:
            st.markdown(
                '<div class="db-info">💾 <strong>'+str(stats["total"])+'</strong> leads saved'
                '<br><strong>'+str(stats["high"])+'</strong> High priority'
                '<br><strong>'+str(stats["rejected"])+'</strong> rejected entries'
                '<br>Last save: '+stats["last"][:10]+"</div>",
                unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="db-warn">DB is empty — run a Search first</div>',
                unsafe_allow_html=True)
        st.markdown("---")
        search_btn = st.button("Load from DB", use_container_width=True)
        if st.button("🗑️ Clear DB", use_container_width=True):
            db_clear(); st.success("DB cleared."); st.rerun()


    # ══════════════════════════════════════════════════════════════════════════════
    # MAIN
    # ══════════════════════════════════════════════════════════════════════════════
    st.markdown("""
    <div class="app-header">
    <p class="app-title">job lead finder</p>
    <p class="app-sub">Quality-gated · 7-step evaluation · Scored /13 · AI-powered pipeline</p>
    </div>
    """, unsafe_allow_html=True)

    if mode == "🔍 Search & Save to DB":
        ai_label = {"off":"rules only","filter_only":"AI filter","score_only":"AI score","full":"full AI"}.get(ai_mode,"")
        st.markdown(
            '<div class="mode-bar save">'
            '<div class="mode-dot" style="background:#7c3aed;"></div>'
            '<strong>Search & Save</strong>'
            '<span style="margin-left:8px;opacity:.8;font-size:0.75rem;">mode: '+ai_label+'</span>'
            '<span style="margin-left:auto;opacity:.6;font-size:0.73rem;">Qualified leads saved to DB</span>'
            '</div>', unsafe_allow_html=True)
    else:
        stats = db_stats()
        st.markdown(
            '<div class="mode-bar view">'
            '<div class="mode-dot" style="background:#22c55e;"></div>'
            '<strong>View from DB</strong>'
            '<span style="margin-left:auto;opacity:.7;font-size:0.73rem;">'+str(stats["total"])+" leads in database</span>"
            '</div>', unsafe_allow_html=True)

    status_box = st.empty()

    # ── Mode 1: Search & Save ──────────────────────────────────────────────────
    if mode == "🔍 Search & Save to DB":
        if search_btn:
            keywords = [k.strip() for k in keyword_input.splitlines() if k.strip()]
            if not keywords:
                st.warning("Enter at least one keyword.")
            elif not sources:
                st.warning("Select at least one source.")
            elif "SerpApi (Google Jobs)" in sources and not serpapi_key.strip():
                st.error("Paste your SerpApi key, or deselect SerpApi.")
            elif ai_mode != "off" and not ai_key.strip():
                st.error("Paste your Anthropic API key to use AI mode, or switch to Rules only.")
            else:
                with st.spinner(""):
                    passed, rejected = run_pipeline(
                        keywords, sources, location,
                        serpapi_key.strip(), ai_key.strip(), ai_mode, status_box)
                if st.session_state.get("serp_error"):
                    st.error("SerpApi: "+st.session_state["serp_error"])
                kw_str = ", ".join(keywords)
                ins, skip = db_save(passed, rejected, kw_str)
                ai_count = sum(1 for j in passed if j.get("scored_by")=="ai")
                st.success(
                    f"✅ **{len(passed)}** leads passed · **{len(rejected)}** rejected · "
                    f"**{ins}** saved to DB · **{ai_count}** AI-scored")
                st.session_state["s_passed"]   = passed
                st.session_state["s_rejected"] = rejected
                st.session_state["s_total"]    = len(passed)+len(rejected)
                st.session_state["s_done"]     = True

        if st.session_state.get("s_done"):
            passed   = st.session_state["s_passed"]
            rejected = st.session_state["s_rejected"]
            total    = st.session_state["s_total"]
            render_stats(passed, len(rejected), total)
            render_leads(passed, view_mode, show_trace)
            render_rejected(rejected)
        elif not search_btn:
            st.markdown(
                '<div class="empty-state"><div class="empty-icon">🎯</div>'
                '<div class="empty-title">Ready to search</div>'
                '<div>Choose your AI mode in the sidebar, enter keywords, and hit Search &amp; Save.</div></div>',
                unsafe_allow_html=True)

    # ── Mode 2: View from DB ───────────────────────────────────────────────────
    else:
        if search_btn:
            jobs = db_load(
                kw_filter=db_keyword,
                priority_filter=db_priority if db_priority else None,
                min_score=db_min_score)
            st.session_state["db_jobs"]   = jobs
            st.session_state["db_loaded"] = True

        if st.session_state.get("db_loaded") and "db_jobs" in st.session_state:
            jobs = st.session_state["db_jobs"]
            if not jobs:
                st.markdown(
                    '<div class="empty-state"><div class="empty-icon">🗄️</div>'
                    '<div class="empty-title">No leads match your filters</div>'
                    '<div>Run a search first, or relax your filters</div></div>',
                    unsafe_allow_html=True)
            else:
                stats2 = db_stats()
                render_stats(jobs, stats2["rejected"], stats2["total"]+stats2["rejected"], extra="DB ")
                render_leads(jobs, view_mode, show_trace)
        elif not search_btn:
            st.markdown(
                '<div class="empty-state"><div class="empty-icon">🗄️</div>'
                '<div class="empty-title">Load your saved leads</div>'
                '<div>Hit "Load from DB" to view all saved leads,<br>'
                'or filter by keyword / priority first</div></div>',
                unsafe_allow_html=True)
