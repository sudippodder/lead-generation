"""
job_lead_search.py — Job Lead Finder Pipeline
================================================
Aligned to flow diagram:
  Start → Scrape (Playwright) → Normalize → Filter (4 steps) →
  Structured Extraction AI → Score (5 factors) → Threshold →
  Push to Google Sheets → Sort → Sales Outreach → END

BLOCKED features (not in diagram):
  - Step 5 Remote hard filter (remote = scoring factor only)
  - Multiple data sources (only Playwright)
  - Claude AI modes (ai_hard_filter, ai_score_lead, ai_rewrite_reason)
  - View from DB mode
  - Buy Reason generation
  See backup: job_lead_search_22april26.py for original code.
"""

import streamlit as st
import feedparser
import hashlib
import requests
import sqlite3
import json
import time
import re
import sys
import os
from urllib.parse import quote
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import get_cfg
from search_engine import fetch_jobs_playwright

# ── Optional dependencies (graceful fallback if not installed) ────────────────
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import gspread
    from google.oauth2.service_account import Credentials as ServiceAccountCredentials
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False


# ══════════════════════════════════════════════════════════════════════════════
# DATABASE  (SQLite — internal storage + deduplication)
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
    """
    Insert leads into DB with three-layer duplicate prevention:
      1. PRIMARY KEY on id (hash)       — catches identical fetches
      2. UNIQUE index on (title,company) — catches same job with different id hash
      3. Pre-flight URL check            — catches same posting from different sources
    Returns (inserted, skipped).
    """
    saved_at = datetime.now(timezone.utc).isoformat()
    ins = skip = 0

    with get_conn() as conn:
        # Build a fast in-memory set of already-stored (title+company) and urls
        existing_keys = set()
        existing_urls = set()
        for row in conn.execute("SELECT LOWER(title)||'|||'||LOWER(company), url FROM leads"):
            existing_keys.add(row[0])
            if row[1]:
                existing_urls.add(row[1].strip().lower())

        for job in jobs:
            title   = (job.get("title","") or "").strip()
            company = (job.get("company","") or "").strip()
            url     = (job.get("url","") or "").strip().lower()

            # Layer 2 & 3 pre-flight check
            composite_key = title.lower() + "|||" + company.lower()
            if composite_key in existing_keys:
                skip += 1
                continue
            if url and url in existing_urls:
                skip += 1
                continue

            try:
                conn.execute("""INSERT OR IGNORE INTO leads
                    (id, source, title, company, location, url, posted_at,
                     description, salary, schedule, score, priority,
                     buy_reason, step_trace, factors, search_kw, saved_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job["id"], job["source"], title, company,
                     job.get("location",""), job.get("url",""), job.get("posted_at",""),
                     job.get("description",""), job.get("salary",""), job.get("schedule",""),
                     job.get("score", 0), job.get("priority","Low"),
                     job.get("buy_reason",""), job.get("step_trace",""),
                     json.dumps(job.get("factors",{})), search_kw, saved_at))
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    ins += 1
                    existing_keys.add(composite_key)
                    if url:
                        existing_urls.add(url)
                else:
                    skip += 1
            except Exception:
                skip += 1

        for r in rejected:
            rtitle   = (r.get("title","") or "").strip()
            rcompany = (r.get("company","") or "").strip()
            try:
                conn.execute("""INSERT OR IGNORE INTO rejected
                    (id, source, title, company, reject_step, reject_reason, search_kw, saved_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (r["id"], r["source"], rtitle, rcompany,
                     r.get("reject_step",""), r.get("reject_reason",""), search_kw, saved_at))
            except Exception:
                pass

        conn.commit()
    return ins, skip

def db_stats():
    with get_conn() as conn:
        total    = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        high     = conn.execute("SELECT COUNT(*) FROM leads WHERE priority='High'").fetchone()[0]
        rejected = conn.execute("SELECT COUNT(*) FROM rejected").fetchone()[0]
        last     = conn.execute("SELECT MAX(saved_at) FROM leads").fetchone()[0]
    return {"total": total, "high": high, "rejected": rejected, "last": (last or "")[:16]}

# Initialise DB on startup
init_db()


# ══════════════════════════════════════════════════════════════════════════════
# SCORING CONSTANTS  (loaded dynamically from config — edit in Settings page)
# ══════════════════════════════════════════════════════════════════════════════

# Global defaults — overridden by _load_constants() from config DB
SERVICEABLE_ROLES = [
    "marketing", "growth", "performance marketer", "demand generation", "seo", "sem",
    "paid media", "paid search", "paid social", "content", "brand", "social media",
    "email marketing", "product marketing", "revenue", "operations", "ops", "strategy",
    "business development", "partnerships", "go-to-market", "gtm", "sales",
    "account manager", "customer success", "performance", "digital marketing",
    "data analyst", "analytics", "community", "influencer", "pr ", "public relations",
    "copywriter", "copywriting", "media buyer", "media planning",
]

REJECT_ROLES = []
REJECT_COMPANIES = []
HIRING_PATTERN_SIGNALS = []
WORKLOAD_SIGNALS = []
VAGUE_SIGNALS = []

ENTERPRISE_SIGNALS = [
    "fortune 500", "fortune500", "s&p 500", "global enterprise", "100,000+", "50,000+",
    "10,000+ employees", "worldwide offices", "publicly traded", "nasdaq listed",
    "nyse listed", "inc 500 company",
]

KNOWN_ENTERPRISE = [
    "netflix", "nike", "amazon", "google", "meta", "microsoft", "apple", "linkedin",
    "salesforce", "oracle", "ibm", "accenture", "deloitte", "mckinsey", "pwc", "kpmg",
    "ey ", "ernst & young", "bain ", "bcg ", "booz", "jpmorgan", "goldman sachs",
    "bank of america", "citibank", "wells fargo", "walmart", "procter & gamble",
    "unilever", "nestlé", "nestle", "coca-cola", "pepsico", "johnson & johnson",
    "pfizer", "abbvie", "eli lilly", "chevron", "shell ", "bp ", "exxon", "boeing",
    "lockheed", "raytheon", "general electric", "ge ", "ford ", "gm ", "toyota",
    "volkswagen", "samsung", "lg ", "sony ", "tencent", "alibaba", "baidu",
    "uber", "lyft", "airbnb", "doordash", "palantir", "snowflake", "stripe",
    "shopify", "hubspot", "zendesk", "twilio", "atlassian", "servicenow",
    "workday", "sap ", "adobe ", "autodesk", "intuit", "paypal",
]

ICP_STARTUP = [
    "startup", "early-stage", "seed", "series a", "series b", "pre-ipo",
    "founded in 20", "founded 20", "we are a small", "small team",
    "bootstrapped", "venture-backed", "newly funded", "recently funded",
]

ICP_SCALING = [
    "scaling", "rapidly growing", "fast-growing", "hypergrowth",
    "expanding team", "growing team", "team expansion", "building out",
    "hiring across", "we are growing", "join our growing",
]

ICP_REMOTE = [
    "remote", "distributed", "work from anywhere", "fully remote",
    "remote-first", "remote friendly", "hybrid", "async", "asynchronous",
    "global team", "international team", "work from home",
]

ICP_OUTSOURCE = [
    "lean team", "small team", "tight budget", "cost-effective",
    "flexible", "fast turnaround", "contractor", "freelancer",
    "agency partner", "outsource", "offshore", "nearshore", "staff aug",
]

CAPACITY_SIGNALS = [
    "immediately", "urgently", "asap", "as soon as possible", "urgent hire",
    "multiple openings", "several positions", "rapidly", "quickly", "fast-paced",
    "we are building", "we are expanding", "we are scaling", "newly created role",
    "new role", "first hire", "building the team", "team of one", "currently a team of",
    "extra capacity", "additional support", "bandwidth", "overwhelmed", "need help",
    "growing workload", "increasing demand", "new market", "new product launch",
]

# ONSITE_BLOCKERS kept for reference but NOT used in hard_filter (Step 5 BLOCKED)
ONSITE_BLOCKERS = [
    "onsite only", "on-site only", "must be in office", "in-person only",
    "no remote", "not remote", "local candidates only", "relocation required",
    "must relocate", "in office 5 days", "5 days in office",
]

# ── Growth signals (NEW — used in Step 2 "Company Growing?") ────────────────
GROWTH_SIGNALS_BROAD = [
    "growing", "expanding", "scaling", "building", "hiring",
    "new role", "new position", "growth", "team growth",
    "increasing", "launch", "expansion", "ramp", "ramping",
]


def _load_constants():
    """Pull live constants from config. Called at pipeline start so Settings changes take effect."""
    cfg = get_cfg()
    global SERVICEABLE_ROLES, REJECT_ROLES, REJECT_COMPANIES
    global ENTERPRISE_SIGNALS, KNOWN_ENTERPRISE
    global ICP_STARTUP, ICP_SCALING, ICP_REMOTE, ICP_OUTSOURCE
    global CAPACITY_SIGNALS, ONSITE_BLOCKERS
    global HIRING_PATTERN_SIGNALS, WORKLOAD_SIGNALS, VAGUE_SIGNALS
    SERVICEABLE_ROLES     = cfg["SERVICEABLE_ROLES"]
    REJECT_ROLES          = cfg["REJECT_ROLES"]
    REJECT_COMPANIES      = cfg["REJECT_COMPANIES"]
    ENTERPRISE_SIGNALS    = cfg["ENTERPRISE_SIGNALS"]
    KNOWN_ENTERPRISE      = cfg["KNOWN_ENTERPRISE"]
    ICP_STARTUP           = cfg["ICP_STARTUP"]
    ICP_SCALING           = cfg["ICP_SCALING"]
    ICP_REMOTE            = cfg["ICP_REMOTE"]
    ICP_OUTSOURCE         = cfg["ICP_OUTSOURCE"]
    CAPACITY_SIGNALS      = cfg["CAPACITY_SIGNALS"]
    ONSITE_BLOCKERS       = cfg["ONSITE_BLOCKERS"]
    HIRING_PATTERN_SIGNALS= cfg["HIRING_PATTERN_SIGNALS"]
    WORKLOAD_SIGNALS      = cfg["WORKLOAD_SIGNALS"]
    VAGUE_SIGNALS         = cfg["VAGUE_SIGNALS"]

# Load defaults on module import
_load_constants()


# ══════════════════════════════════════════════════════════════════════════════
# TEXT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _text(job):
    """Full searchable text: title + company + description, lowercased."""
    return (job.get("title","") + " " + job.get("company","") + " " + job.get("description","")).lower()

def _title_text(job):
    """Title + description only, lowercased."""
    return (job.get("title","") + " " + job.get("description","")).lower()


# ══════════════════════════════════════════════════════════════════════════════
# HARD FILTER — 4 steps matching flow diagram
# ══════════════════════════════════════════════════════════════════════════════

def hard_filter(job):
    """
    Quality gate — 4 steps matching flow diagram (Step 5 BLOCKED).
    Returns (passed:bool, reject_step:str, reject_reason:str).
    A lead is REJECTED at the FIRST failing step.
    Only leads that pass ALL steps reach score_lead().

    Diagram flow:
      Step 1: Execution Roles?   → No → Reject
      Step 2: Company Growing?   → No → Reject
      Step 3: 3+ Similar Roles?  → No → Reject
      Step 4: Work Increase Clear? → No → Reject
      [Step 5: Remote — BLOCKED, remote is scoring factor only]
    """
    title   = job.get("title","").lower()
    company = job.get("company","").lower()
    text    = _text(job)

    # ─────────────────────────────────────────────────────────────────────
    # STEP 1 — Execution Roles?
    # Keep:  support, QA, ops, implementation, mid-level dev
    # Reject: AI/ML/data, architect/principal, research, C-suite
    # ─────────────────────────────────────────────────────────────────────
    reject_role_hit = next(
        (r for r in REJECT_ROLES if r in title),
        None
    )
    if reject_role_hit:
        return False, "Step 1", f"Capability role rejected: '{reject_role_hit}' in title — not outsourceable"

    role_match = any(r in title for r in SERVICEABLE_ROLES)
    if not role_match:
        return False, "Step 1", f"Role not execution-type: '{job.get('title','')}'"

    # ─────────────────────────────────────────────────────────────────────
    # STEP 2 — Company Growing?
    # First: reject enterprises / staffing (not growing / not a buyer)
    # Then:  REQUIRE at least one growth signal (diagram: No → Reject)
    # ─────────────────────────────────────────────────────────────────────
    for ent in KNOWN_ENTERPRISE:
        if ent in company:
            return False, "Step 2", f"Known large enterprise: {job.get('company','')} — not growing/not a buyer"

    reject_co_hit = next(
        (r for r in REJECT_COMPANIES if r in company or r in text[:300]),
        None
    )
    if reject_co_hit:
        return False, "Step 2", f"Company type rejected: '{reject_co_hit}' — HR/staffing/consulting/agency"

    ent_hits = [s for s in ENTERPRISE_SIGNALS if s in text]
    if len(ent_hits) >= 2:
        return False, "Step 2", f"Enterprise signals: {', '.join(ent_hits[:2])}"

    # NEW: Require at least one growth signal (diagram: "Company Growing?" → No → Reject)
    growth_pool = ICP_SCALING + ICP_STARTUP + GROWTH_SIGNALS_BROAD
    growth_hit = any(s in text for s in growth_pool)
    if not growth_hit:
        return False, "Step 2", "No company growth signal detected — company not visibly growing"

    # ─────────────────────────────────────────────────────────────────────
    # STEP 3 — 3+ Similar Roles?
    # Need at least ONE hiring pattern signal
    # ─────────────────────────────────────────────────────────────────────
    multi_roles   = job.get("company_open_roles", 1)
    pattern_hit   = any(s in text for s in HIRING_PATTERN_SIGNALS)

    if multi_roles < 2 and not pattern_hit:
        return False, "Step 3", "No hiring pattern — single generic role, no volume or repetition signal"

    # ─────────────────────────────────────────────────────────────────────
    # STEP 4 — Work Increase Clear?
    # Must identify SPECIFIC workload increase — not just "scaling"
    # ─────────────────────────────────────────────────────────────────────
    workload_hit = any(s in text for s in WORKLOAD_SIGNALS)
    if not workload_hit:
        # Check if only vague signals exist — reject
        vague_hit = any(s in text for s in VAGUE_SIGNALS)
        if vague_hit or not any(s in text for s in CAPACITY_SIGNALS):
            return False, "Step 4", "No clear workload signal — only vague growth language, not specific capacity pressure"

    # ─────────────────────────────────────────────────────────────────────
    # STEP 5 — Remote compatibility — BLOCKED per diagram alignment
    # Remote is used in scoring (Factor 4) only, not as a hard filter.
    # Original code checked ONSITE_BLOCKERS here.
    # See backup: job_lead_search_22april26.py
    # ─────────────────────────────────────────────────────────────────────

    return True, "", ""


# ══════════════════════════════════════════════════════════════════════════════
# SCORING ENGINE — 5 factors matching flow diagram
# ══════════════════════════════════════════════════════════════════════════════

def score_lead(job):
    """
    Scoring engine — 5 factors from diagram, ONLY for leads that passed hard_filter.

    Factors:
      Execution Signal  0–3  (role repetition / volume)
      Hiring Intent     0–3  (urgency + active vs generic)
      Company Fit       0–2  (strong/weak/none)
      Remote Signal     0–2  (remote / hybrid / onsite)
      Buying Trigger    0–3  (workload spike clarity)
    Total = /13

    [Buy Reason generation BLOCKED — Sales Outreach handles messaging via OpenAI]
    """
    text        = _text(job)
    title       = job.get("title","").lower()
    multi_roles = job.get("company_open_roles", 1)

    # ── Factor 1: Execution Signal (0–3) ────────────────────────────────
    if multi_roles >= 3 and any(s in text for s in HIRING_PATTERN_SIGNALS):
        f_exec = (3, f"{multi_roles} same/similar roles")
    elif multi_roles >= 3:
        f_exec = (3, f"{multi_roles} open roles")
    elif multi_roles == 2 and any(s in text for s in HIRING_PATTERN_SIGNALS):
        f_exec = (2, "2–3 similar roles")
    elif multi_roles == 2:
        f_exec = (1, "2 different roles")
    else:
        f_exec = (0, "single role")

    # ── Factor 2: Hiring Intent (0–3) ────────────────────────────────────
    urgent_hit  = any(s in text for s in ["immediately","asap","urgent","as soon as possible","urgent hire","start asap"])
    pattern_hit = any(s in text for s in HIRING_PATTERN_SIGNALS)
    cap_hits    = sum(1 for s in CAPACITY_SIGNALS if s in text)

    if urgent_hit and (multi_roles >= 2 or pattern_hit):
        f_intent = (3, "urgent + repeated hiring")
    elif urgent_hit:
        f_intent = (3, "urgent hire")
    elif multi_roles >= 2 or pattern_hit:
        f_intent = (2, "multiple / repeated roles")
    elif cap_hits >= 1:
        f_intent = (1, "active hiring signal")
    else:
        f_intent = (0, "generic posting")

    # ── Factor 3: Company Fit (0–2) ──────────────────────────────────────
    startup_hit = any(s in text for s in ICP_STARTUP)
    outsrc_hit  = any(s in text for s in ICP_OUTSOURCE)
    scaling_hit = any(s in text for s in ICP_SCALING)

    if (startup_hit or outsrc_hit) and scaling_hit:
        f_fit = (2, "strong fit — startup/scaling/lean")
    elif startup_hit or outsrc_hit or scaling_hit:
        f_fit = (1, "weak fit — some ICP signal")
    else:
        f_fit = (0, "no fit signal")

    # ── Factor 4: Remote Signal (0–2) ────────────────────────────────────
    remote_txt = text[:500]
    is_fully_remote = any(r in remote_txt for r in ["fully remote","remote-first","work from anywhere","100% remote","remote only"])
    is_hybrid       = any(r in remote_txt for r in ["hybrid","flexible location","partially remote"])
    is_remote       = any(r in remote_txt for r in ["remote","distributed","global team","work from home"])

    if is_fully_remote:
        f_remote = (2, "fully remote")
    elif is_hybrid:
        f_remote = (1, "hybrid")
    elif is_remote:
        f_remote = (2, "remote")
    else:
        f_remote = (0, "no remote signal")

    # ── Factor 5: Buying Trigger (0–3) ────────────────────────────────────
    workload_hit = any(s in text for s in WORKLOAD_SIGNALS)
    specific_workload = any(s in text for s in [
        "support load", "ticket volume", "high volume", "load spike",
        "surge", "overwhelmed", "stretched", "at capacity",
        "growing workload", "workload spike", "capacity gap",
        "qa workload", "processing volume",
    ])

    if specific_workload and multi_roles >= 2:
        f_trigger = (3, "clear workload spike + volume hiring")
    elif specific_workload:
        f_trigger = (3, "clear workload spike")
    elif workload_hit and multi_roles >= 2:
        f_trigger = (2, "workload signal + multiple roles")
    elif workload_hit or multi_roles >= 2:
        f_trigger = (1, "weak buying signal")
    else:
        f_trigger = (0, "no trigger")

    # ── Total Score ──────────────────────────────────────────────────────
    total = f_exec[0] + f_intent[0] + f_fit[0] + f_remote[0] + f_trigger[0]
    total = min(13, total)

    # Priority threshold (from config)
    cfg = get_cfg()
    high_t = cfg.get("HIGH_PRIORITY_THRESHOLD", 10)
    med_t  = cfg.get("MEDIUM_PRIORITY_THRESHOLD", 7)
    if total >= high_t:   priority = "High"
    elif total >= med_t:  priority = "Medium"
    else:                 priority = "Low"

    step_trace = (
        f"S1:exec={f_exec[0]}/3 "
        f"S2:intent={f_intent[0]}/3 "
        f"S3:fit={f_fit[0]}/2 "
        f"S4:remote={f_remote[0]}/2 "
        f"S5:trigger={f_trigger[0]}/3"
    )

    job["score"]      = total
    job["priority"]   = priority
    job["buy_reason"] = ""  # BLOCKED — Sales Outreach handles messaging
    job["step_trace"] = step_trace
    job["factors"]    = {
        "role_relevance": f_exec,
        "hiring_intent":  f_intent,
        "company_fit":    f_fit,
        "remote_signal":  f_remote,
        "buying_trigger": f_trigger,
    }
    return job


# ══════════════════════════════════════════════════════════════════════════════
# STRUCTURED EXTRACTION AI (NEW — OpenAI, diagram step between filter & score)
# ══════════════════════════════════════════════════════════════════════════════

EXTRACTION_SYSTEM = """You are a structured data extraction engine for a B2B lead qualification system.
Extract structured facts from job postings. Respond with valid JSON only — no markdown, no explanation."""

def ai_structured_extract(job, api_key, model="gpt-4o-mini"):
    """
    Structured Extraction AI — dedicated pipeline step (diagram: between filter & score).
    Uses OpenAI to extract structured data from job descriptions.
    Enriches the job dict with AI-extracted fields for better scoring context.
    Falls back gracefully if API call fails.
    """
    if not api_key or not HAS_OPENAI:
        job["ai_extracted"] = {}
        job["extraction_status"] = "skipped"
        return job

    try:
        client = OpenAI(api_key=api_key)
        prompt = f"""Analyze this job posting and extract structured data.

Job Title: {job.get('title', '')}
Company: {job.get('company', '')}
Location: {job.get('location', '')}
Description: {job.get('description', '')[:800]}

Extract the following as JSON:
{{
    "company_stage": "startup|scaleup|smb|enterprise|unknown",
    "team_size_estimate": "small (<50)|medium (50-200)|large (200-1000)|enterprise (1000+)|unknown",
    "growth_signals": ["list of specific growth indicators found"],
    "workload_indicators": ["list of specific workload pressure indicators"],
    "remote_compatibility": "fully_remote|hybrid|onsite|unknown",
    "outsourcing_fit": "high|medium|low",
    "key_skills": ["top 3-5 skills required"],
    "urgency_level": "urgent|moderate|low"
}}"""

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.1,
        )

        text = response.choices[0].message.content.strip()
        clean = re.sub(r"```json|```", "", text).strip()
        extracted = json.loads(clean)

        job["ai_extracted"] = extracted
        job["extraction_status"] = "success"
        return job
    except Exception as e:
        job["ai_extracted"] = {}
        job["extraction_status"] = f"failed: {str(e)[:60]}"
        return job


# ══════════════════════════════════════════════════════════════════════════════
# PUSH TO GOOGLE SHEETS (NEW — diagram step after scoring)
# ══════════════════════════════════════════════════════════════════════════════

def push_to_google_sheets(jobs, rejected, sheet_id, credentials_path):
    """
    Push leads + rejected to Google Sheets (diagram: after scoring/threshold).
    Creates/updates 'Leads' and 'Rejected' worksheets.
    Returns (success:bool, message:str).
    """
    if not sheet_id or not credentials_path:
        return False, "Google Sheets not configured (missing GOOGLE_SHEET_ID or GOOGLE_SHEETS_CREDENTIALS_JSON)"

    if not HAS_GSPREAD:
        return False, "gspread not installed — run: pip install gspread google-auth"

    try:
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        try:
            # Try to parse credentials_path as JSON string
            creds_dict = json.loads(credentials_path)
            print("Using credentials from JSON string")
            creds = ServiceAccountCredentials.from_service_account_info(creds_dict, scopes=scopes)
        except json.JSONDecodeError:
            # Fallback to file path
            if not os.path.exists(credentials_path):
                return False, "Credentials file not found and is not valid JSON."
            print(f"Using credentials file at: {credentials_path}")
            creds = ServiceAccountCredentials.from_service_account_file(credentials_path, scopes=scopes)

        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
        print("Google Sheets connection successful")
        print(creds)
        print(gc)
        print("Google Sheets workbook: ", sh)
        # ── Leads worksheet ──────────────────────────────────────────────
        try:
            ws = sh.worksheet("Leads")
            ws.clear()
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet("Leads", rows=max(len(jobs)+1, 2), cols=10)

        headers = ["Company", "Title", "Location", "Score", "Priority",
                    "Source", "URL", "Posted At", "Saved At"]
        rows = [headers]
        for job in jobs:
            rows.append([
                job.get("company", ""),
                job.get("title", ""),
                job.get("location", ""),
                str(job.get("score", 0)),
                job.get("priority", ""),
                job.get("source", ""),
                job.get("url", ""),
                job.get("posted_at", ""),
                datetime.now(timezone.utc).isoformat()[:16],
            ])
        ws.update(rows, value_input_option='RAW')
        print("Google Sheets Leads worksheet updated successfully")
        # ── Rejected worksheet ───────────────────────────────────────────
        try:
            ws_r = sh.worksheet("Rejected")
            ws_r.clear()
        except gspread.WorksheetNotFound:
            ws_r = sh.add_worksheet("Rejected", rows=max(len(rejected)+1, 2), cols=6)

        r_headers = ["Company", "Title", "Source", "Reject Step", "Reject Reason", "Saved At"]
        r_rows = [r_headers]
        for r in rejected:
            r_rows.append([
                r.get("company", ""),
                r.get("title", ""),
                r.get("source", ""),
                r.get("reject_step", ""),
                r.get("reject_reason", ""),
                datetime.now(timezone.utc).isoformat()[:16],
            ])
        ws_r.update(r_rows, value_input_option='RAW')

        return True, f"✅ Pushed {len(jobs)} leads + {len(rejected)} rejected to Google Sheets"
    except Exception as e:
        return False, f"Google Sheets error: {str(e)[:100]}"


# ══════════════════════════════════════════════════════════════════════════════
# SALES OUTREACH (NEW — OpenAI, diagram: final step)
# ══════════════════════════════════════════════════════════════════════════════

OUTREACH_SYSTEM = """You are a B2B sales copywriter for VE (Virtual Employee) — a remote staffing and outsourcing company.
VE provides: IT staffing (developers, QA, DevOps), Digital Marketing (SEO, PPC, social, email, analytics),
Content & Creative (writers, designers, video), Finance & Accounts (bookkeeping, payroll),
Admin & Operations (VA, data entry, ops support) — all remote, flexible, cost-effective.
Write concise, personalized cold outreach emails. No subject line — body only."""

def generate_outreach_email(job, api_key, model="gpt-4o-mini"):
    """
    Sales Outreach — generate personalized cold email for high-priority leads.
    Returns email body text or error message.
    """
    if not api_key or not HAS_OPENAI:
        return ""

    try:
        client = OpenAI(api_key=api_key)

        # Build context from AI extraction if available
        ai_data = job.get("ai_extracted", {})
        stage_info = f"\nCompany stage: {ai_data.get('company_stage', 'unknown')}" if ai_data else ""
        fit_info = f"\nOutsourcing fit: {ai_data.get('outsourcing_fit', 'unknown')}" if ai_data else ""

        prompt = f"""Write a short, personalized B2B cold outreach email.

Target company: {job.get('company', '')}
They are hiring for: {job.get('title', '')}
Location: {job.get('location', '')}
Our lead score: {job.get('score', 0)}/13 ({job.get('priority', '')} priority){stage_info}{fit_info}
Job description excerpt: {job.get('description', '')[:300]}

Write a 3-4 sentence email that:
1. Opens with a specific observation about their hiring (reference the actual role)
2. Connects their need to VE's relevant service
3. Ends with a clear, low-pressure CTA

Tone: professional, warm, specific. Return ONLY the email body text. No subject line."""

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": OUTREACH_SYSTEM},
                {"role": "user", "content": prompt}
            ],
            max_tokens=250,
            temperature=0.7,
        )

        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[Outreach generation failed: {str(e)[:60]}]"


# ══════════════════════════════════════════════════════════════════════════════
# NORMALIZE + UID
# ══════════════════════════════════════════════════════════════════════════════
def _base(source):
    return {"source":source,"title":"","company":"","location":"",
            "url":"","posted_at":"","description":"","salary":"","schedule":""}

def uid(s):
    """Stable dedup ID — normalise whitespace and case before hashing."""
    normalised = re.sub(r"\s+", " ", s.strip().lower())
    return hashlib.md5(normalised.encode()).hexdigest()[:12]

def normalize(raw):
    seen, clean = set(), []
    for job in raw:
        if job["id"] in seen: continue
        seen.add(job["id"])
        for f in ("title","company","description"):
            job[f] = re.sub(r"\s+", " ", job.get(f,"")).strip()
        clean.append(job)
    return clean

def fetch_serpapi(kw, loc, key, num=30):
    """Fetch jobs from SerpApi. kw can be a string or list of strings."""
    jobs = []
    if not key: return jobs
    keywords = kw if isinstance(kw, list) else [kw]
    l = loc if loc and loc.lower() != "worldwide" else ""
    for keyword in keywords:
        try:
            resp = requests.get("https://serpapi.com/search", timeout=20,
                                params={"engine":"google_jobs","q":keyword,"location":l,"num":num,"api_key":key})
            data = resp.json()
            if "error" in data:
                st.session_state["serp_error"] = data["error"]; continue
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
    # if not isinstance(kw, str):
    #     raise ValueError(f"Expected string keyword, got {type(kw)}")
    # k = quote(kw)    
    # k = kw.replace(" ","%20")
    for query in kw:
        scoped = query if "site:" in query.lower() else "site:linkedin.com/jobs " + query
        print(scoped)
        urls = fetch_linkedin_links(scoped, loc)
    print(urls)
    
    return urls
def fetch_linkedin_links(k, loc: str) -> list:
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
    """Fetch jobs from Indeed RSS. kw can be a string or list of strings."""
    keywords = kw if isinstance(kw, list) else [kw]
    jobs=[]
    for keyword in keywords:
        k=keyword.replace(" ","+")
        url="https://www.indeed.com/rss?q="+k+"&sort=date&fromage=14"
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
    """Fetch jobs from Remotive API. kw can be a string or list of strings."""
    keywords = kw if isinstance(kw, list) else [kw]
    jobs=[]
    for keyword in keywords:
        try:
            resp=requests.get("https://remotive.com/api/remote-jobs",
                              params={"search":keyword,"limit":50},timeout=12)
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
# PIPELINE — matches flow diagram exactly
# ══════════════════════════════════════════════════════════════════════════════

def run_pipeline(google_queries, google_max_urls, status_ph, selected_sources=None, serpapi_key="", location="worldwide"):
    """
    Pipeline matching flow diagram:
      Fetch (selected sources) → Normalize → Filter (4 steps) → Structured Extraction AI →
      Score (5 factors) → Threshold → Google Sheets → Sort → Sales Outreach → END
    """
    # Reload constants from config so Settings changes take effect
    _load_constants()
    cfg = get_cfg()

    openai_key   = cfg.get("OPENAI_API_KEY", "")
    openai_model = cfg.get("OPENAI_MODEL", "gpt-4o-mini")
    sheet_id     = cfg.get("GOOGLE_SHEET_ID", "")
    sheet_creds  = cfg.get("GOOGLE_SHEETS_CREDENTIALS_JSON", "")

    if selected_sources is None:
        selected_sources = ["Playwright"]

    raw = []
    sheets_msg = ""

    # ── Stage 1: Fetch from selected sources ──────────────────────────────
    if google_queries:
        source_count = len(selected_sources)
        status_ph.markdown(
            '<div class="status-line">→ Fetching from ' + str(source_count) + ' source(s): '
            + ', '.join(selected_sources) + '…</div>',
            unsafe_allow_html=True)

        # ── Playwright (Google → LinkedIn) ────────────────────────────────
        if "Playwright" in selected_sources:
            status_ph.markdown(
                '<div class="status-line">→ [1/' + str(source_count) + '] Playwright scraping… ~30–60s</div>',
                unsafe_allow_html=True)
            try:
                playwright_jobs = fetch_jobs_playwright(
                    queries=google_queries,
                    num_results=google_max_urls,
                )
                raw += playwright_jobs
                print(f"[Playwright] Got {len(playwright_jobs)} jobs")
            except Exception as e:
                status_ph.markdown(
                    f'<div class="status-line" style="color:#ef4444;">⚠ Playwright error: {str(e)[:80]}</div>',
                    unsafe_allow_html=True)
                print(f"[Playwright] Error: {e}")

        # ── LinkedIn (API scraper) ────────────────────────────────────────
        if "LinkedIn" in selected_sources:
            status_ph.markdown(
                '<div class="status-line">→ LinkedIn API scraping…</div>',
                unsafe_allow_html=True)
            try:
                linkedin_jobs = fetch_linkedin(kw=google_queries, loc=location)
                raw += linkedin_jobs
                print(f"[LinkedIn] Got {len(linkedin_jobs)} jobs")
            except Exception as e:
                print(f"[LinkedIn] Error: {e}")

        # ── SerpApi ──────────────────────────────────────────────────────
        if "SerpApi" in selected_sources:
            status_ph.markdown(
                '<div class="status-line">→ SerpApi fetching…</div>',
                unsafe_allow_html=True)
            try:
                serpapi_jobs = fetch_serpapi(kw=google_queries, loc=location, key=serpapi_key, num=30)
                raw += serpapi_jobs
                print(f"[SerpApi] Got {len(serpapi_jobs)} jobs")
                if st.session_state.get("serp_error"):
                    status_ph.markdown(
                        f'<div class="status-line" style="color:#ef4444;">⚠ SerpApi: {st.session_state["serp_error"][:60]}</div>',
                        unsafe_allow_html=True)
            except Exception as e:
                print(f"[SerpApi] Error: {e}")

        # ── Indeed ───────────────────────────────────────────────────────
        if "Indeed" in selected_sources:
            status_ph.markdown(
                '<div class="status-line">→ Indeed RSS fetching…</div>',
                unsafe_allow_html=True)
            try:
                indeed_jobs = fetch_indeed(kw=google_queries)
                raw += indeed_jobs
                print(f"[Indeed] Got {len(indeed_jobs)} jobs")
            except Exception as e:
                print(f"[Indeed] Error: {e}")

        # ── Remotive ─────────────────────────────────────────────────────
        if "Remotive" in selected_sources:
            status_ph.markdown(
                '<div class="status-line">→ Remotive API fetching…</div>',
                unsafe_allow_html=True)
            try:
                remotive_jobs = fetch_remotive(kw=google_queries)
                raw += remotive_jobs
                print(f"[Remotive] Got {len(remotive_jobs)} jobs")
            except Exception as e:
                print(f"[Remotive] Error: {e}")

        time.sleep(0.3)

    # ── Stage 2: Clean & Normalize Data ───────────────────────────────────
    status_ph.markdown(
        '<div class="status-line">→ Cleaning & normalizing data…</div>',
        unsafe_allow_html=True)
    jobs = normalize(raw)

    # Attach company open-role count for scoring context
    by_company = defaultdict(list)
    for j in jobs: by_company[j["company"].lower()].append(j)
    for j in jobs: j["company_open_roles"] = len(by_company[j["company"].lower()])

    # ── Stage 3: Hard Filter (4 steps) ────────────────────────────────────
    status_ph.markdown(
        '<div class="status-line">→ Filtering ' + str(len(jobs)) + ' jobs (4-step quality gate)…</div>',
        unsafe_allow_html=True)

    passed, rejected = [], []
    for i, job in enumerate(jobs):
        ok, step, reason = hard_filter(job)
        if not ok:
            rejected.append({"id":job["id"], "source":job["source"],
                             "title":job["title"], "company":job["company"],
                             "location":job.get("location",""),
                             "url":job.get("url",""),
                             "reject_step":step, "reject_reason":reason})
        else:
            passed.append(job)

        if (i+1) % 10 == 0:
            status_ph.markdown(
                '<div class="status-line">→ filtering… ' + str(i+1) + '/' + str(len(jobs)) + '</div>',
                unsafe_allow_html=True)

    # ── Stage 4: Structured Extraction AI ─────────────────────────────────
    ai_extract_count = 0
    if openai_key and passed:
        status_ph.markdown(
            '<div class="status-line">→ AI structured extraction (' + str(len(passed)) + ' leads)…</div>',
            unsafe_allow_html=True)
        for i, job in enumerate(passed):
            ai_structured_extract(job, openai_key, openai_model)
            if job.get("extraction_status") == "success":
                ai_extract_count += 1
            time.sleep(0.3)
            if (i+1) % 5 == 0:
                status_ph.markdown(
                    '<div class="status-line">→ extracting… ' + str(i+1) + '/' + str(len(passed)) + '</div>',
                    unsafe_allow_html=True)

    # ── Stage 5: Scoring Engine (5 factors) ───────────────────────────────
    status_ph.markdown(
        '<div class="status-line">→ Scoring ' + str(len(passed)) + ' leads (5-factor engine)…</div>',
        unsafe_allow_html=True)

    for job in passed:
        score_lead(job)

    # ── Stage 6: Score > Threshold (High Priority / Store Only) ───────────
    # Already handled by score_lead() → priority = High/Medium/Low

    # ── Stage 7: Push to Google Sheets ────────────────────────────────────
    # print(sheet_id+" sheet_id")
    # print(sheet_creds+" sheet_creds")
    # print(passed)
    # print(rejected)
    if sheet_id and sheet_creds:
        status_ph.markdown(
            '<div class="status-line">→ Pushing to Google Sheets…</div>',
            unsafe_allow_html=True)
        gs_ok, sheets_msg = push_to_google_sheets(passed, rejected, sheet_id, sheet_creds)
    else:
        sheets_msg = "⚠️ Google Sheets not configured"

    # ── Stage 8: Sort by Score ────────────────────────────────────────────
    passed.sort(key=lambda j: j["score"], reverse=True)

    # ── Stage 9: Sales Outreach ───────────────────────────────────────────
    outreach_count = 0
    if openai_key and passed:
        high_leads = [j for j in passed if j.get("priority") == "High"]
        if high_leads:
            status_ph.markdown(
                '<div class="status-line">→ Generating outreach emails for '
                + str(len(high_leads)) + ' high-priority leads…</div>',
                unsafe_allow_html=True)
            for job in high_leads:
                job["outreach_draft"] = generate_outreach_email(job, openai_key, openai_model)
                if job["outreach_draft"] and not job["outreach_draft"].startswith("["):
                    outreach_count += 1
                time.sleep(0.3)

    status_ph.empty()
    return passed, rejected, sheets_msg, ai_extract_count, outreach_count


# ══════════════════════════════════════════════════════════════════════════════
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

def render_stats(passed, rejected_count, fetched_total):
    high_n = sum(1 for j in passed if j["priority"]=="High")
    med_n  = sum(1 for j in passed if j["priority"]=="Medium")
    low_n  = sum(1 for j in passed if j["priority"]=="Low")
    pass_pct = round(len(passed)/max(fetched_total,1)*100)
    st.markdown(
        '<div class="stats-bar">'
        '<div class="stat-item"><div class="stat-val">'+str(fetched_total)+'</div><div class="stat-lbl">Fetched</div></div>'
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
            '<div>The quality gate filtered all results — try different search queries</div></div>',
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
                   "</tr>")
        st.markdown(
            '<div style="overflow-x:auto"><table class="tbl"><thead><tr>'
            '<th>Company</th><th>Role</th><th>Location</th>'
            '<th>Priority</th><th>Score</th>'
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

            link_html=('<a href="'+url+'" target="_blank" style="font-size:0.7rem;color:#6366f1;'
                       'text-decoration:none;margin-left:6px;">↗ view job</a>') if url else ""
            salary_html=('<span style="font-size:0.7rem;color:#059669;font-weight:500;margin-left:8px;">'
                         +job["salary"]+"</span>") if job.get("salary") else ""
            meta_parts=[p2 for p2 in [job.get("location",""),job.get("posted_at","")[:10]] if p2]
            meta_html=" · ".join(meta_parts)

            trace_html=('<div class="step-trace">'+job.get("step_trace","")+"</div>"
                        ) if show_trace and job.get("step_trace") else ""

            # AI extraction badge
            ai_data = job.get("ai_extracted", {})
            extract_html = ""
            if ai_data:
                stage = ai_data.get("company_stage", "")
                fit = ai_data.get("outsourcing_fit", "")
                remote_c = ai_data.get("remote_compatibility", "")
                urgency = ai_data.get("urgency_level", "")
                extract_parts = []
                if stage and stage != "unknown": extract_parts.append(f"Stage: {stage}")
                if fit: extract_parts.append(f"Fit: {fit}")
                if remote_c and remote_c != "unknown": extract_parts.append(f"Remote: {remote_c}")
                if urgency: extract_parts.append(f"Urgency: {urgency}")
                if extract_parts:
                    extract_html = (
                        '<div class="extraction-info">'
                        '<span class="extraction-badge">🤖 AI</span> '
                        + " · ".join(extract_parts)
                        + '</div>'
                    )

            # Outreach draft (for High priority leads)
            outreach_html = ""
            draft = job.get("outreach_draft", "")
            if draft and not draft.startswith("["):
                # Escape HTML in the draft text
                safe_draft = draft.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
                outreach_html = (
                    '<div class="outreach-draft">'
                    '<div class="outreach-header">📧 Draft Outreach Email</div>'
                    '<div class="outreach-body">' + safe_draft + '</div>'
                    '</div>'
                )

            st.markdown(
                '<div class="lead-card '+p+'">'
                '<div style="display:flex;align-items:flex-start;gap:1rem;">'
                '<div style="flex:1;min-width:0;">'
                '<div style="display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-bottom:4px;">'
                '<span class="badge badge-'+p+'">'+job["priority"]+' Priority</span>'
                '<span class="source-pill">'+job["source"]+"</span>"
                +salary_html+link_html
                +"</div>"
                '<p class="lead-title">'+job["title"]+"</p>"
                '<p class="lead-company">'+job["company"]+"</p>"
                +('<p class="lead-meta">'+meta_html+"</p>" if meta_html else "")
                +'<div style="margin-top:6px">'+pills+"</div>"
                +extract_html
                +outreach_html
                +trace_html
                +"</div>"
                '<div class="score-wrap" style="flex-shrink:0;">'
                '<div class="score-main" style="color:'+sc_c+'">'+str(sc)+"</div>"
                '<div class="score-denom">/13</div>'
                '<div class="score-bar-bg">'
                '<div class="score-bar-fill" style="width:'+bar_pct(sc)+';background:'+sc_c+'"></div>'
                "</div></div></div></div>",
                unsafe_allow_html=True)

def render_rejected(rejected, limit=50):
    if not rejected: return

    # Group by step for summary counts
    step_counts = {}
    for r in rejected:
        s = r.get("reject_step", "Unknown")
        step_counts[s] = step_counts.get(s, 0) + 1

    with st.expander(f"🚫 Rejected by quality gate ({len(rejected)} leads filtered out)", expanded=False):
        # Summary bar
        summary_html = '<div style="display:flex;gap:10px;margin-bottom:10px;flex-wrap:wrap;">'
        step_labels = {
            "Step 1": ("Execution Roles", "#6366f1"),
            "Step 2": ("Company Growing", "#ef4444"),
            "Step 3": ("Hiring Pattern", "#f59e0b"),
            "Step 4": ("Workload Signal", "#64748b"),
        }
        for step, count in sorted(step_counts.items()):
            label, color = step_labels.get(step, (step, "#9ca3af"))
            summary_html += (f'<div style="background:{color}11;border:1px solid {color}33;'
                            f'border-radius:6px;padding:4px 10px;font-size:0.7rem;">'
                            f'<span style="font-weight:600;color:{color};">{step}</span> '
                            f'<span style="color:#6b7280;">{label}</span> '
                            f'<span style="font-weight:700;color:{color};">{count}</span></div>')
        summary_html += '</div>'
        st.markdown(summary_html, unsafe_allow_html=True)

        st.markdown(
            '<div style="font-size:0.72rem;color:#6b7280;margin-bottom:10px;">'
            'These leads did not pass the 4-step hard filter. The <b>matched keyword</b> '
            'that triggered rejection is shown in the reason. Click the job link to verify.</div>',
            unsafe_allow_html=True)

        # Filter by step
        step_filter = st.multiselect(
            "Filter by step",
            options=sorted(step_counts.keys()),
            default=sorted(step_counts.keys()),
            key="rejected_step_filter",
            label_visibility="collapsed",
        )
        filtered_rejected = [r for r in rejected if r.get("reject_step") in step_filter]

        for r in filtered_rejected[:limit]:
            step = r.get("reject_step", "")
            reason = r.get("reject_reason", "")
            url = r.get("url", "")
            title = r.get("title", "")
            company = r.get("company", "")
            source = r.get("source", "")
            location = r.get("location", "")

            step_color = {"Step 1":"#6366f1","Step 2":"#ef4444",
                          "Step 3":"#f59e0b","Step 4":"#64748b"}.get(step, "#9ca3af")
            step_label = step_labels.get(step, (step, "#9ca3af"))[0]

            # Build title with link
            if url:
                title_html = (f'<a href="{url}" target="_blank" '
                              f'style="color:#374151;text-decoration:none;font-weight:600;font-size:0.82rem;">'
                              f'{title} <span style="font-size:0.65rem;color:#6366f1;">↗ view</span></a>')
            else:
                title_html = f'<span style="font-weight:600;font-size:0.82rem;color:#374151;">{title}</span>'

            # Build meta line
            meta_parts = [p for p in [company, source, location] if p]
            meta_html = ' · '.join(meta_parts)

            # Highlight the matched keyword in the reason
            # The reason format is like: "Capability role rejected: 'keyword' in title — explanation"
            highlighted_reason = reason
            # Highlight quoted keywords in the reason
            import re as _re
            highlighted_reason = _re.sub(
                r"'([^']+)'",
                r'<span style="background:#fef2f2;color:#b91c1c;padding:0 4px;border-radius:3px;'
                r'font-weight:600;font-family:DM Mono,monospace;font-size:0.7rem;">\1</span>',
                highlighted_reason
            )

            st.markdown(
                f'<div class="rejected-card-detail">'
                f'<div style="display:flex;align-items:flex-start;gap:10px;">'
                f'<div style="flex-shrink:0;margin-top:2px;">'
                f'<span style="display:inline-block;padding:2px 8px;border-radius:5px;'
                f'font-size:0.63rem;font-weight:700;font-family:DM Mono,monospace;'
                f'background:{step_color}15;color:{step_color};border:1px solid {step_color}33;">'
                f'{step}</span></div>'
                f'<div style="flex:1;min-width:0;">'
                f'<div>{title_html}</div>'
                f'<div style="font-size:0.72rem;color:#9ca3af;margin-top:1px;">{meta_html}</div>'
                f'<div style="margin-top:5px;font-size:0.73rem;color:#6b7280;line-height:1.5;">'
                f'<span style="font-weight:600;color:{step_color};margin-right:4px;">{step_label}:</span>'
                f'{highlighted_reason}</div>'
                f'</div></div></div>',
                unsafe_allow_html=True)

        if len(filtered_rejected) > limit:
            st.caption(f"+ {len(filtered_rejected)-limit} more rejected leads not shown")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN UI — Search mode only (View from DB mode BLOCKED)
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

    .pipeline-bar { display:flex; gap:6px; padding:9px 14px; border:1px solid #e5e7eb; border-radius:8px;
                margin-bottom:1.25rem; align-items:center; font-size:0.72rem; color:#374151;
                background:#f9fafb; flex-wrap:wrap; }
    .pipeline-step { display:inline-flex; align-items:center; gap:3px; padding:2px 8px;
                border-radius:5px; font-size:0.65rem; font-family:'DM Mono',monospace; }
    .pipeline-step.active { background:#f0fdf4; color:#166534; border:1px solid #bbf7d0; }
    .pipeline-step.inactive { background:#fafafa; color:#9ca3af; border:1px solid #e5e7eb; }
    .pipeline-arrow { color:#d1d5db; font-size:0.7rem; }

    .badge { display:inline-block; padding:2px 9px; border-radius:99px; font-size:0.67rem;
            font-weight:600; font-family:'DM Mono',monospace; letter-spacing:0.05em; text-transform:uppercase; }
    .badge-high     { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; }
    .badge-medium   { background:#fffbeb; color:#b45309; border:1px solid #fde68a; }
    .badge-low      { background:#f8fafc; color:#64748b; border:1px solid #e2e8f0; }

    .factor-pill { display:inline-flex; align-items:center; gap:3px; padding:2px 7px;
                border-radius:4px; font-size:0.63rem; font-family:'DM Mono',monospace;
                background:#f1f5f9; color:#475569; border:1px solid #e2e8f0; margin:2px 2px 0 0; }
    .factor-pill.scored { background:#f0fdf4; color:#166534; border-color:#bbf7d0; }
    .factor-pill.zero   { background:#fafafa; color:#9ca3af; border-color:#f1f5f9; }

    .source-pill { display:inline-block; padding:1px 7px; border-radius:4px; font-size:0.63rem;
                font-family:'DM Mono',monospace; background:#faf5ff; color:#7c3aed; border:1px solid #e9d5ff; }

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

    .rejected-card-detail { background:#fff; border:1px solid #f1f5f9; border-radius:8px;
                    padding:10px 14px; margin-bottom:6px; border-left:3px solid #e5e7eb; }
    .rejected-card-detail:hover { border-left-color:#ef4444; background:#fefefe; }

    .lead-title   { font-size:0.9rem; font-weight:600; color:#111827; margin:0 0 1px; }
    .lead-company { font-size:0.8rem; color:#374151; font-weight:500; margin:0; }
    .lead-meta    { font-size:0.7rem; color:#9ca3af; margin-top:3px; }

    .step-trace   { font-size:0.7rem; color:#6b7280; background:#f8fafc; border:1px solid #e2e8f0;
                    border-radius:5px; padding:5px 9px; margin-top:6px; line-height:1.6; font-family:'DM Mono',monospace; }

    .extraction-info { font-size:0.7rem; color:#475569; background:#f5f3ff; border:1px solid #e9d5ff;
                      border-radius:5px; padding:4px 9px; margin-top:6px; }
    .extraction-badge { font-size:0.63rem; font-weight:600; color:#7c3aed; background:#ede9fe;
                       padding:1px 6px; border-radius:3px; }

    .outreach-draft { background:#f0fdf4; border:1px solid #bbf7d0; border-left:3px solid #22c55e;
                     border-radius:0 8px 8px 0; padding:10px 14px; margin-top:8px; }
    .outreach-header { font-size:0.7rem; font-weight:600; color:#166534; margin-bottom:6px;
                      text-transform:uppercase; letter-spacing:0.04em; }
    .outreach-body { font-size:0.8rem; color:#374151; line-height:1.6; }

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

    .config-box { padding:8px 12px; border-radius:7px; font-size:0.75rem; margin-bottom:6px; }
    .config-ok { background:#f0fdf4; border:1px solid #bbf7d0; color:#065f46; }
    .config-warn { background:#fffbeb; border:1px solid #fde68a; color:#92400e; }
    .config-err { background:#fef2f2; border:1px solid #fecaca; color:#991b1b; }

    .stButton > button { background:#111827 !important; color:#fff !important; border:none !important;
        border-radius:8px !important; font-weight:500 !important; font-size:0.875rem !important;
        padding:0.6rem 1.5rem !important; width:100% !important; }
    .stButton > button:hover { background:#1f2937 !important; }

    .empty-state { text-align:center; padding:3rem 2rem; color:#9ca3af; }
    .empty-icon  { font-size:2rem; margin-bottom:0.5rem; }
    .empty-title { font-size:0.9rem; font-weight:500; color:#4b5563; margin-bottom:0.2rem; }
    .status-line { font-family:'DM Mono',monospace; font-size:0.72rem; color:#6b7280; padding:0.2rem 0; }

    .sheets-result { padding:8px 12px; border-radius:7px; font-size:0.75rem; margin-top:8px; }
    .sheets-ok { background:#f0fdf4; border:1px solid #bbf7d0; color:#065f46; }
    .sheets-fail { background:#fef2f2; border:1px solid #fecaca; color:#991b1b; }
    </style>
    """, unsafe_allow_html=True)

    # ── Header ────────────────────────────────────────────────────────────
    st.markdown("""
    <div class="app-header">
    <p class="app-title">job lead finder</p>
    <p class="app-sub">Playwright → Filter (4-step) → AI Extract → Score /13 → Google Sheets → Sales Outreach</p>
    </div>
    """, unsafe_allow_html=True)

    # ── Pipeline visualization ────────────────────────────────────────────
    cfg = get_cfg()
    openai_key = cfg.get("OPENAI_API_KEY", "")
    sheet_id   = cfg.get("GOOGLE_SHEET_ID", "")
    sheet_creds= cfg.get("GOOGLE_SHEETS_CREDENTIALS_JSON", "")
    has_openai = bool(openai_key) and HAS_OPENAI
    has_sheets = bool(sheet_id) and bool(sheet_creds) and HAS_GSPREAD

    steps_html = ""
    pipeline_steps = [
        ("Fetch Sources", True),
        ("Normalize", True),
        ("Filter (4-step)", True),
        ("AI Extract", has_openai),
        ("Score /13", True),
        ("Google Sheets", has_sheets),
        ("Sort", True),
        ("Outreach", has_openai),
    ]
    for i, (label, active) in enumerate(pipeline_steps):
        cls = "active" if active else "inactive"
        steps_html += f'<span class="pipeline-step {cls}">{label}</span>'
        if i < len(pipeline_steps) - 1:
            steps_html += '<span class="pipeline-arrow">→</span>'

    st.markdown(
        '<div class="pipeline-bar">' + steps_html + '</div>',
        unsafe_allow_html=True)

    # ── Search Configuration ──────────────────────────────────────────────
    st.markdown("### 🔍 Search Configuration")

    # ── Data Source selector ──────────────────────────────────────────────
    ALL_SOURCES = ["Playwright", "LinkedIn", "SerpApi", "Indeed", "Remotive"]
    selected_sources = st.multiselect(
        "📡 Data Sources",
        ALL_SOURCES,
        default=["Playwright"],
        help="Choose one or more data sources to fetch job leads from.",
        key="source_selector",
    )

    # Source descriptions
    source_info = {
        "Playwright": "🎭 Google → LinkedIn (Playwright browser) — most comprehensive, slowest",
        "LinkedIn": "🔗 LinkedIn guest API — fast, limited results",
        "SerpApi": "🔍 Google Jobs via SerpApi — reliable, requires API key",
        "Indeed": "📋 Indeed RSS feed — fast, free, limited detail",
        "Remotive": "🌍 Remotive API — remote jobs only, free",
    }
    if selected_sources:
        info_html = '<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:8px 12px;font-size:0.72rem;color:#6b7280;margin-bottom:8px;">'
        for src in selected_sources:
            info_html += f'<div style="margin:2px 0;">{source_info.get(src, src)}</div>'
        info_html += '</div>'
        st.markdown(info_html, unsafe_allow_html=True)

    # ── SerpApi key (shown only when SerpApi is selected) ─────────────────
    serpapi_key_input = "25541839543f37f0fdda2044e1acdcd2b8ab197eecfab88b492a8a1d7052ae26"
    # if "SerpApi" in selected_sources:
    #     serpapi_key_input = st.text_input(
    #         "🔑 SerpApi Key",
    #         type="password",
    #         value=cfg.get("SERPAPI_KEY", ""),
    #         help="Get your key at serpapi.com. Leave empty to skip SerpApi.",
    #         key="serpapi_key_input",
    #     )

    # ── Location (for LinkedIn / SerpApi) ─────────────────────────────────
    location_input = "worldwide"
    if any(s in selected_sources for s in ["LinkedIn", "SerpApi"]):
        location_input = st.text_input(
            "📍 Location",
            value="worldwide",
            help="Location filter for LinkedIn and SerpApi (e.g. 'United States', 'London', 'worldwide')",
            key="location_input",
        )

    st.markdown("""\n<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;
            padding:10px 12px;font-size:0.76rem;color:#1e40af;margin-bottom:6px;">
<strong>🔍 Search Queries</strong><br>
Type your search queries, one per line. Used by all selected sources.<br>
For Playwright, <code>site:linkedin.com</code> is added automatically.<br>
<b>Examples:</b><br>
<code>hiring multiple developer UK remote</code><br>
<code>SEO manager startup scaling remote</code><br>
<code>growth marketing hiring urgently series a</code>
</div>""", unsafe_allow_html=True)

    google_query_input = st.text_area(
        "Search queries",
        height=110,
        value="hiring multiple developer UK remote\nSEO manager startup scaling remote\ngrowth marketing hiring urgently",
        help="One search query per line. Used by all selected data sources.",
        label_visibility="collapsed",
        key="google_queries_input",
    )
    google_queries = [q.strip() for q in google_query_input.splitlines() if q.strip()]

    google_max_urls = st.slider(
        "Max LinkedIn pages to fetch", 5, 50, 20,
        help="Each page takes ~1-2 seconds. 20 pages ≈ 30s. Applies to Playwright source.",
    )

    st.markdown("---")

    # ── Display options ───────────────────────────────────────────────────
    col_v1, col_v2 = st.columns(2)
    with col_v1:
        view_mode = st.radio("View", ["Cards", "Table"], horizontal=True)
    with col_v2:
        show_trace = st.checkbox("Show scoring trace", value=False)

    st.markdown("---")

    # ── Config status ─────────────────────────────────────────────────────
    if has_openai:
        st.markdown(
            '<div class="config-box config-ok">✅ <strong>OpenAI configured</strong> — '
            'AI extraction + outreach emails enabled</div>',
            unsafe_allow_html=True)
    else:
        missing = []
        if not HAS_OPENAI: missing.append("pip install openai")
        if not openai_key: missing.append("set OPENAI_API_KEY in Settings")
        st.markdown(
            '<div class="config-box config-warn">⚠️ <strong>OpenAI not configured</strong> — '
            + ", ".join(missing) + '. AI features will be skipped.</div>',
            unsafe_allow_html=True)

    if has_sheets:
        st.markdown(
            '<div class="config-box config-ok">✅ <strong>Google Sheets configured</strong> — '
            'leads will be pushed after scoring</div>',
            unsafe_allow_html=True)
    else:
        missing_g = []
        if not HAS_GSPREAD: missing_g.append("pip install gspread google-auth")
        if not sheet_id: missing_g.append("set GOOGLE_SHEET_ID in Settings")
        if not sheet_creds: missing_g.append("set GOOGLE_SHEETS_CREDENTIALS_JSON in Settings")
        st.markdown(
            '<div class="config-box config-warn">⚠️ <strong>Google Sheets not configured</strong> — '
            + ", ".join(missing_g) + '. Leads saved to local DB only.</div>',
            unsafe_allow_html=True)

    st.markdown("---")

    # ── Search button ─────────────────────────────────────────────────────
    search_btn = st.button("🔍 Search & Process", use_container_width=True)

    status_box = st.empty()

    # ── Execute pipeline ──────────────────────────────────────────────────
    if search_btn:
        if not google_queries:
            st.error("Add at least one search query.")
        elif not selected_sources:
            st.error("Select at least one data source.")
        else:
            with st.spinner(""):
                passed, rejected, sheets_msg, ai_count, outreach_count = run_pipeline(
                    google_queries, google_max_urls, status_box,
                    selected_sources=selected_sources,
                    serpapi_key=serpapi_key_input,
                    location=location_input)

            # Save to internal DB
            kw_str = ", ".join(google_queries[:3])
            ins, skip = db_save(passed, rejected, kw_str)

            # Success summary
            parts = [
                f"**{len(passed)}** leads passed",
                f"**{len(rejected)}** rejected",
                f"**{ins}** saved to DB",
            ]
            if ai_count > 0:
                parts.append(f"**{ai_count}** AI-extracted")
            if outreach_count > 0:
                parts.append(f"**{outreach_count}** outreach emails")

            st.success("✅ " + " · ".join(parts))
            #print(sheets_msg+" Sheet Msg")
            # Google Sheets result
            if sheets_msg:
                is_ok = sheets_msg.startswith("✅")
                cls = "sheets-ok" if is_ok else "sheets-fail"
                st.markdown(
                    f'<div class="sheets-result {cls}">{sheets_msg}</div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    f'<div class="sheets-result sheets-fail">❌ Google Sheets configured error</div>',
                    unsafe_allow_html=True) 
            st.session_state["s_passed"]   = passed
            st.session_state["s_rejected"] = rejected
            st.session_state["s_total"]    = len(passed) + len(rejected)
            st.session_state["s_done"]     = True

    # ── Render results ────────────────────────────────────────────────────
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
            '<div>Enter your Google search queries above and hit Search &amp; Process.<br>'
            'The pipeline will: scrape → filter → extract → score → export → outreach</div></div>',
            unsafe_allow_html=True)
