"""
config.py — Shared constants + dynamic config loader.
All pages import get_cfg() to get live config that respects Settings page changes.
"""
import sqlite3, json, os

DB_PATH = os.path.join(os.path.dirname(__file__), "auth_db.sqlite")

# ── Defaults (used when no override saved in DB) ──────────────────────────────

DEFAULTS = {
    "SERVICEABLE_ROLES": [
        "marketing", "growth", "performance marketer", "demand generation",
        "seo", "sem", "paid media", "paid search", "paid social", "content",
        "brand", "social media", "email marketing", "product marketing",
        "revenue", "operations", "ops", "digital marketing", "data analyst",
        "analytics", "community", "copywriter", "copywriting", "media buyer",
        "developer", "engineer", "devops", "qa engineer", "qa analyst",
        "ml engineer", "ai engineer", "full stack", "backend", "frontend",
        "bookkeeper", "accountant", "payroll", "accounts payable",
        "virtual assistant", "va ", "data entry", "admin",
    ],
    "ENTERPRISE_SIGNALS": [
        "fortune 500", "fortune500", "s&p 500", "global enterprise",
        "100,000+", "50,000+", "10,000+ employees", "worldwide offices",
        "publicly traded", "nasdaq listed", "nyse listed", "inc 500 company",
    ],
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
    "CAPACITY_SIGNALS": [
        "immediately", "urgently", "asap", "as soon as possible", "urgent hire",
        "multiple openings", "several positions", "rapidly", "quickly",
        "we are building", "we are expanding", "we are scaling",
        "newly created role", "new role", "first hire", "building the team",
        "extra capacity", "additional support", "bandwidth", "overwhelmed",
        "growing workload", "increasing demand", "new market",
    ],
    "ONSITE_BLOCKERS": [
        "onsite only", "on-site only", "must be in office", "in-person only",
        "no remote", "not remote", "local candidates only",
        "relocation required", "must relocate", "in office 5 days",
        "5 days in office",
    ],
    # Scoring thresholds
    "MIN_SCORE_KEEP": 8,
    "HIGH_PRIORITY_THRESHOLD": 10,
    "MEDIUM_PRIORITY_THRESHOLD": 7,
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
