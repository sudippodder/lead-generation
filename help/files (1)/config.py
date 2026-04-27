"""
config.py — Shared constants + dynamic config loader.
All pages import get_cfg() to get live config that respects Settings page changes.
V6: Boolean search model, execution-only roles, 5-step filter.
"""
import sqlite3, json, os

DB_PATH = os.path.join(os.path.dirname(__file__), "auth_db.sqlite")

# ── Defaults (used when no override saved in DB) ──────────────────────────────

DEFAULTS = {

    # ── STEP 1: Execution roles we CAN serve ─────────────────────────────────
    # Keep a lead ONLY if the role matches one of these
    "SERVICEABLE_ROLES": [
        # Customer / technical support
        "customer support", "technical support", "support specialist",
        "customer success", "client support", "help desk", "service desk",
        "customer service", "support agent", "support representative",
        "support engineer", "tier 1", "tier 2",
        # QA / testing
        "qa engineer", "qa analyst", "quality assurance", "software tester",
        "test engineer", "manual tester", "automation tester", "qa specialist",
        # Operations / admin
        "operations associate", "ops associate", "operations coordinator",
        "operations analyst", "business operations", "back office",
        "data entry", "admin", "virtual assistant", "va ",
        "administrative", "office coordinator",
        # Implementation / onboarding
        "implementation specialist", "implementation consultant",
        "onboarding specialist", "integration specialist",
        "solutions engineer",  # execution-type only
        # Mid-level developers (non-complex, repeatable)
        "frontend developer", "backend developer", "full stack developer",
        "php developer", "wordpress developer", "shopify developer",
        "react developer", "node developer", "python developer",
        "junior developer", "mid-level developer", "software developer",
        "web developer", "mobile developer",
        # Finance / accounts
        "bookkeeper", "accountant", "payroll", "accounts payable",
        "accounts receivable", "billing specialist", "finance assistant",
        # Content / creative (execution)
        "content writer", "copywriter", "social media manager",
        "graphic designer", "video editor", "motion designer",
        # Digital marketing (execution)
        "seo specialist", "seo analyst", "ppc specialist",
        "paid media specialist", "email marketer", "performance marketer",
        "digital marketing specialist", "marketing coordinator",
    ],

    # ── STEP 1: Capability roles — REJECT immediately if ANY match ────────────
    "REJECT_ROLES": [
        # AI / ML / Data science
        "machine learning", "data scientist", "ai engineer", "ml engineer",
        "ai researcher", "deep learning", "nlp engineer", "llm",
        "generative ai", "computer vision", "data engineer",
        # Architecture / principal / senior leadership
        "architect", "principal engineer", "principal developer",
        "staff engineer", "distinguished engineer",
        # Research
        "research engineer", "research scientist", "applied research",
        # C-suite / VP / Head (ownership roles)
        "chief", "cto", "cpo", "coo", "vp of", "vice president",
        "head of engineering", "head of product", "head of technology",
        "director of engineering", "engineering manager",
    ],

    # ── STEP 2: Company types to REJECT ──────────────────────────────────────
    "REJECT_COMPANIES": [
        # HR / staffing / recruiting
        "staffing", "recruitment", "recruiting", "talent acquisition",
        "headhunter", "executive search", "hr solutions", "hr consulting",
        "workforce solutions", "manpower", "hays", "adecco", "randstad",
        "robert half", "kforce", "kelly services", "insight global",
        # Consulting / services / solutions
        "consulting", "consultancy", "advisory", "management consulting",
        "it services", "it solutions", "tech solutions", "managed services",
        "outsourcing provider", "bpo ", "business process outsourcing",
        # Marketplace / platform hiring for others
        "marketplace", "platform for", "freelance platform",
        "gig platform", "on-demand platform",
        # Agencies (they compete with us)
        "digital agency", "marketing agency", "creative agency",
        "seo agency", "ppc agency", "media agency",
    ],

    # ── STEP 2: Enterprise signals in JD ─────────────────────────────────────
    "ENTERPRISE_SIGNALS": [
        "fortune 500", "fortune500", "s&p 500", "global enterprise",
        "100,000+", "50,000+", "10,000+ employees", "5,000+ employees",
        "worldwide offices", "publicly traded", "nasdaq listed",
        "nyse listed", "inc 500 company", "1,000+ employees",
    ],

    # ── STEP 2: Known large enterprises — auto-reject ────────────────────────
    "KNOWN_ENTERPRISE": [
        "netflix", "nike", "amazon", "google", "meta", "microsoft", "apple",
        "linkedin", "salesforce", "oracle", "ibm", "accenture", "deloitte",
        "mckinsey", "pwc", "kpmg", "ey ", "ernst & young", "bain ", "bcg ",
        "jpmorgan", "goldman sachs", "bank of america", "citibank",
        "wells fargo", "walmart", "procter & gamble", "unilever", "nestle",
        "coca-cola", "pepsico", "johnson & johnson", "pfizer", "chevron",
        "shell ", "bp ", "exxon", "boeing", "lockheed", "samsung", "lg ",
        "sony ", "tencent", "alibaba", "baidu", "uber", "lyft", "airbnb",
        "doordash", "palantir", "snowflake", "stripe", "shopify", "hubspot",
        "zendesk", "twilio", "atlassian", "servicenow", "workday", "sap ",
        "adobe ", "autodesk", "intuit", "paypal",
    ],

    # ── STEP 3: Hiring pattern signals (need ≥1 for a KEEP) ──────────────────
    "HIRING_PATTERN_SIGNALS": [
        "multiple openings", "several positions", "hiring multiple",
        "multiple roles", "3 openings", "4 openings", "5 openings",
        "high volume", "bulk hiring", "mass hiring",
        "same role", "repeated hiring", "ongoing hiring",
        "across locations", "multiple locations",
    ],

    # ── STEP 4: Workload signals — must identify WHAT work is increasing ──────
    "WORKLOAD_SIGNALS": [
        # Customer support load
        "support load", "ticket volume", "support volume", "high volume",
        "customer queries", "growing support", "support demand",
        "support requests increasing", "more tickets",
        # QA workload
        "qa workload", "testing backlog", "regression load",
        "release cycle", "sprint velocity",
        # Operations workload
        "processing volume", "operational load", "workflow increasing",
        "transaction volume", "order volume",
        # General capacity
        "capacity gap", "bandwidth issue", "resource gap",
        "overwhelmed", "stretched", "team at capacity",
        "increasing demand", "growing workload", "workload spike",
        "load spike", "surge", "ramp up",
        # Urgency
        "immediately", "urgently", "asap", "as soon as possible",
        "urgent hire", "start asap", "immediate start",
    ],

    # ── STEP 4: Vague/generic signals that are NOT valid workload evidence ────
    "VAGUE_SIGNALS": [
        "company is growing", "we are scaling", "scaling team",
        "maybe they need help", "join a growing team",
        "exciting opportunity", "dynamic environment",
        "fast-paced environment",
    ],

    # ── ICP positive signals (used in scoring) ────────────────────────────────
    "ICP_STARTUP": [
        "startup", "early-stage", "seed", "series a", "series b", "pre-ipo",
        "founded in 20", "we are a small", "small team", "bootstrapped",
        "venture-backed", "newly funded", "recently funded",
    ],
    "ICP_SCALING": [
        "scaling", "rapidly growing", "fast-growing", "hypergrowth",
        "expanding team", "growing team", "team expansion", "building out",
        "hiring across", "we are growing", "join our growing",
    ],
    "ICP_REMOTE": [
        "remote", "distributed", "work from anywhere", "fully remote",
        "remote-first", "remote friendly", "hybrid", "async",
        "global team", "international team", "work from home",
    ],
    "ICP_OUTSOURCE": [
        "lean team", "small team", "tight budget", "cost-effective",
        "flexible", "fast turnaround", "contractor", "freelancer",
        "agency partner", "outsource", "offshore", "nearshore", "staff aug",
    ],

    # ── Capacity signals (kept for scoring compat) ────────────────────────────
    "CAPACITY_SIGNALS": [
        "immediately", "urgently", "asap", "as soon as possible", "urgent hire",
        "multiple openings", "several positions", "rapidly", "quickly",
        "we are building", "we are expanding", "we are scaling",
        "newly created role", "new role", "first hire", "building the team",
        "extra capacity", "additional support", "bandwidth", "overwhelmed",
        "growing workload", "increasing demand", "new market",
        "high volume", "load spike", "surge",
    ],

    # ── Onsite hard-blockers ──────────────────────────────────────────────────
    "ONSITE_BLOCKERS": [
        "onsite only", "on-site only", "must be in office", "in-person only",
        "no remote", "not remote", "local candidates only",
        "relocation required", "must relocate", "in office 5 days",
        "5 days in office",
    ],

    # ── Boolean search template (shown in Settings, used as search hint) ──────
    "BOOLEAN_SEARCH_TEMPLATE": (
        'site:linkedin.com/jobs\n'
        '("customer support" OR "technical support" OR "support specialist" OR '
        '"QA engineer" OR "implementation specialist" OR "operations associate")\n'
        '("hiring multiple" OR "multiple openings" OR "scaling team" OR "high volume")\n'
        '"remote" ("USA" OR "United States")\n'
        '-"AI" -"machine learning" -"data scientist"\n'
        '-"principal" -"architect" -"research"\n'
        '-"consulting" -"staffing" -"recruitment" -"agency"\n'
        '-"solutions" -"partners" -"services"'
    ),

    # ── Scoring thresholds ────────────────────────────────────────────────────
    "MIN_SCORE_KEEP":             8,
    "HIGH_PRIORITY_THRESHOLD":   10,
    "MEDIUM_PRIORITY_THRESHOLD":  7,
    "MAX_COMPANY_SIZE":         1000,   # reject companies with 1000+ employees
}


def _get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_settings_table():
    with _get_conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )""")
        conn.commit()


def save_setting(key, value):
    ensure_settings_table()
    with _get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                     (key, json.dumps(value)))
        conn.commit()


def load_setting(key, default=None):
    ensure_settings_table()
    with _get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row:
        try:
            return json.loads(row[0])
        except Exception:
            return default
    return default


def get_cfg():
    """
    Returns the live config dict. Settings page overrides take precedence
    over defaults. Call this at the top of any function that uses constants.
    """
    cfg = {}
    for key, default in DEFAULTS.items():
        saved = load_setting(key)
        cfg[key] = saved if saved is not None else default
    return cfg


def reset_all():
    """Wipe all saved settings — revert to defaults."""
    ensure_settings_table()
    with _get_conn() as conn:
        conn.execute("DELETE FROM settings")
        conn.commit()
