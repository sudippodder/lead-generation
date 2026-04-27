"""
pages/2_Settings.py  — Dynamic configuration for scoring constants.
Changes here immediately affect the Search page on next run.
"""
import streamlit as st
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import get_cfg, save_setting, reset_all, DEFAULTS

st.set_page_config(page_title="Settings · Lead Finder", page_icon="⚙️", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');
html,body,[class*="css"]{ font-family:'DM Sans',sans-serif; }
#MainMenu,footer,header{ visibility:hidden; }
.block-container{ padding:2rem 2rem 4rem; max-width:1100px; }
.pg-title  { font-family:'DM Mono',monospace; font-size:1.4rem; font-weight:500; color:#111827; margin:0; }
.pg-sub    { font-size:0.8rem; color:#6b7280; margin-top:.2rem; }
.section-head { font-size:0.7rem; font-weight:600; color:#6b7280; text-transform:uppercase;
                letter-spacing:.06em; margin:1.5rem 0 .4rem; }
.info-box  { background:#f0f9ff; border:1px solid #bae6fd; border-radius:8px;
             padding:10px 14px; font-size:0.78rem; color:#0369a1; margin-bottom:1rem; }
.warn-box  { background:#fffbeb; border:1px solid #fde68a; border-radius:8px;
             padding:10px 14px; font-size:0.78rem; color:#92400e; margin-bottom:1rem; }
.modified-badge { display:inline-block; padding:1px 7px; border-radius:99px; font-size:0.65rem;
                  font-weight:600; background:#fef9c3; color:#854d0e; border:1px solid #fde68a;
                  margin-left:6px; }
.default-badge  { display:inline-block; padding:1px 7px; border-radius:99px; font-size:0.65rem;
                  font-weight:600; background:#f0fdf4; color:#166534; border:1px solid #bbf7d0;
                  margin-left:6px; }
div[data-testid="stTextArea"] textarea { font-family:'DM Mono',monospace; font-size:0.78rem; }
.stButton>button { background:#111827!important; color:#fff!important; border:none!important;
    border-radius:8px!important; font-weight:500!important; font-size:0.875rem!important; }
.stButton>button:hover { background:#1f2937!important; }
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="padding:1.5rem 0 1rem;border-bottom:1px solid #e5e7eb;margin-bottom:1.5rem;">
  <p class="pg-title">⚙️ settings</p>
  <p class="pg-sub">Edit scoring constants · Changes apply immediately to the next search</p>
</div>
""", unsafe_allow_html=True)

st.markdown("""
<div class="info-box">
Each list below is one keyword/phrase per line. The search pipeline reads these live — 
edit and save, then run a new search to see the effect. Blank lines are ignored.
</div>
""", unsafe_allow_html=True)

cfg = get_cfg()

# Helper: list → textarea string
def to_text(lst): return "\n".join(lst)
# Helper: textarea string → list
def from_text(s): return [x.strip() for x in s.splitlines() if x.strip()]

# Track if anything was modified
modified_keys = []
for key in DEFAULTS:
    if isinstance(DEFAULTS[key], list):
        saved = cfg.get(key)
        if saved is not None and saved != DEFAULTS[key]:
            modified_keys.append(key)


# ── Section renderer helper ───────────────────────────────────────────────────
def setting_section(key, label, description, color="#6b7280"):
    current = cfg.get(key, DEFAULTS[key])
    is_modified = current != DEFAULTS[key]
    badge = '<span class="modified-badge">modified</span>' if is_modified else '<span class="default-badge">default</span>'
    st.markdown(f'<p class="section-head">{label} {badge}</p>', unsafe_allow_html=True)
    st.caption(description)
    col_edit, col_info = st.columns([3, 1])
    with col_edit:
        new_val_text = st.text_area(
            label, value=to_text(current),
            height=160, label_visibility="collapsed",
            key=f"ta_{key}"
        )
    with col_info:
        st.markdown(f"""
        <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;
                    padding:10px 12px;font-size:0.73rem;color:#6b7280;margin-top:2px;">
          <strong style="color:#374151">{len(current)}</strong> entries<br>
          {"<span style='color:#854d0e'>⚠ customised</span>" if is_modified else
           "<span style='color:#166534'>✓ default</span>"}
        </div>
        """, unsafe_allow_html=True)
        if is_modified:
            if st.button("Reset", key=f"rst_{key}", use_container_width=True):
                save_setting(key, DEFAULTS[key])
                st.success(f"{label} reset.")
                st.rerun()
    return from_text(new_val_text)


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🎯 Execution roles (KEEP)",
    "🚫 Reject roles & companies",
    "📋 Hiring patterns & workload",
    "🌍 ICP & Remote signals",
    "⚙️ Enterprise blocklist",
    "📊 Scoring thresholds",
])

pending = {}

with tab1:
    st.markdown("#### Step 1 — Execution roles VE CAN serve")
    st.markdown("""
    <div class="info-box">
    <strong>V6 Rule:</strong> A lead passes Step 1 ONLY if the job title contains one of these execution-type roles.
    These are repeatable, scalable roles — support, QA, ops, mid-level dev, etc.
    </div>
    """, unsafe_allow_html=True)
    pending["SERVICEABLE_ROLES"] = setting_section(
        "SERVICEABLE_ROLES",
        "Serviceable (execution) roles",
        "Job title must match one of these. Non-matching titles are rejected at Step 1.",
    )
    st.markdown("---")
    st.markdown("#### Step 0 — Boolean search template")
    st.caption("Reference template for crafting Google/LinkedIn boolean searches. Edit to match your current focus.")
    bt_current = cfg.get("BOOLEAN_SEARCH_TEMPLATE", "")
    bt_new = st.text_area("Boolean template", value=bt_current, height=160,
                          help="Reference only — shown in search page as a hint",
                          key="ta_BOOLEAN_SEARCH_TEMPLATE")
    pending["BOOLEAN_SEARCH_TEMPLATE"] = bt_new

with tab2:
    st.markdown("#### Step 1 — Capability roles to REJECT immediately")
    st.markdown("""
    <div class="warn-box">
    <strong>V6 Rule:</strong> If ANY of these appear in the job title → REJECT immediately, no further checks.
    These are high-ownership roles that cannot be outsourced (AI/ML, architect, principal, C-suite).
    </div>
    """, unsafe_allow_html=True)
    pending["REJECT_ROLES"] = setting_section(
        "REJECT_ROLES",
        "Reject roles (capability hiring)",
        "If ANY keyword matches the job title → instant reject. No exceptions.",
    )
    st.markdown("---")
    st.markdown("#### Step 2 — Company types to REJECT")
    st.markdown("""
    <div class="warn-box">
    <strong>V6 Rule:</strong> Reject if the company name or description contains these.
    HR/staffing/consulting companies are competitors or non-buyers.
    </div>
    """, unsafe_allow_html=True)
    pending["REJECT_COMPANIES"] = setting_section(
        "REJECT_COMPANIES",
        "Reject company types",
        "If ANY keyword matches company name or first 300 chars of JD → reject.",
    )

with tab3:
    st.markdown("#### Step 3 — Hiring pattern signals")
    st.markdown("""
    <div class="info-box">
    <strong>V6 Rule:</strong> At least ONE of these must be present (or company has 2+ open roles).
    Signals: 3+ same roles, same job repeated, same role across locations.
    </div>
    """, unsafe_allow_html=True)
    pending["HIRING_PATTERN_SIGNALS"] = setting_section(
        "HIRING_PATTERN_SIGNALS",
        "Hiring pattern signals",
        "Need ≥1 match (or multi-role count ≥2) to pass Step 3.",
    )
    st.markdown("---")
    st.markdown("#### Step 4 — Workload signals (specific capacity pressure)")
    st.markdown("""
    <div class="info-box">
    <strong>V6 Rule:</strong> Must identify WHAT work is increasing — not just "we are scaling".
    Good: "support load increasing", "ticket volume". Bad: "company is growing".
    </div>
    """, unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        pending["WORKLOAD_SIGNALS"] = setting_section(
            "WORKLOAD_SIGNALS",
            "Workload signals (specific)",
            "Specific capacity pressure words. At least one required to pass Step 4.",
        )
    with c2:
        pending["VAGUE_SIGNALS"] = setting_section(
            "VAGUE_SIGNALS",
            "Vague signals (reject if ONLY these)",
            "Generic growth language. If only vague signals exist and no workload signals → reject.",
        )
    st.markdown("---")
    pending["CAPACITY_SIGNALS"] = setting_section(
        "CAPACITY_SIGNALS",
        "Capacity / urgency signals (scoring)",
        "Used in scoring only (not filtering). Urgency words for Hiring Intent factor.",
    )
    pending["ONSITE_BLOCKERS"] = setting_section(
        "ONSITE_BLOCKERS",
        "Onsite blockers (Step 5)",
        "Phrases indicating onsite-only. Hard reject — remote staffing not viable.",
    )

with tab6:
    st.markdown("#### ICP positive signals (used in scoring only)")
    c1, c2 = st.columns(2)
    with c1:
        pending["ICP_STARTUP"] = setting_section(
            "ICP_STARTUP", "Startup / early-stage signals",
            "Language indicating early-stage or funded company. Used in Company Fit scoring.")
        pending["ICP_SCALING"] = setting_section(
            "ICP_SCALING", "Scaling / growth signals",
            "Language indicating team growth. Used in Company Fit + Buying Trigger scoring.")
    with c2:
        pending["ICP_REMOTE"] = setting_section(
            "ICP_REMOTE", "Remote / distributed signals",
            "Used in Remote Signal scoring (Factor 4).")
        pending["ICP_OUTSOURCE"] = setting_section(
            "ICP_OUTSOURCE", "Outsource / lean team signals",
            "Language indicating flexibility preference. Used in Company Fit scoring.")

with tab4:
    st.markdown("#### Scoring thresholds")
    st.caption("These control how scores translate to priority labels and final filtering.")
    cfg_thresh = get_cfg()

    col1, col2, col3 = st.columns(3)
    with col1:
        high_t = st.number_input(
            "High priority threshold (≥)",
            min_value=1, max_value=13,
            value=cfg_thresh.get("HIGH_PRIORITY_THRESHOLD", 10),
            help="Score ≥ this → High Priority")
    with col2:
        med_t = st.number_input(
            "Medium priority threshold (≥)",
            min_value=1, max_value=13,
            value=cfg_thresh.get("MEDIUM_PRIORITY_THRESHOLD", 7),
            help="Score ≥ this and < High → Medium Priority")
    with col3:
        min_keep = st.number_input(
            "Minimum score to keep (/13)",
            min_value=0, max_value=13,
            value=cfg_thresh.get("MIN_SCORE_KEEP", 8),
            help="Leads below this score are dropped in final filter")

    st.markdown("""
    <div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;
                padding:12px 16px;font-size:0.8rem;color:#374151;margin-top:1rem;">
      <strong>Current band preview:</strong><br>
      🔴 High = score ≥ {high} &nbsp;·&nbsp;
      🟡 Medium = {med}–{high_m} &nbsp;·&nbsp;
      ⚪ Low = &lt;{med} &nbsp;·&nbsp;
      🚫 Dropped = &lt;{keep}
    </div>
    """.format(
        high=high_t, med=med_t, high_m=high_t-1, keep=min_keep
    ), unsafe_allow_html=True)

    pending["HIGH_PRIORITY_THRESHOLD"] = int(high_t)
    pending["MEDIUM_PRIORITY_THRESHOLD"] = int(med_t)
    pending["MIN_SCORE_KEEP"] = int(min_keep)


# ── Save / Reset all ──────────────────────────────────────────────────────────
st.markdown("---")
col_save, col_reset, col_status = st.columns([2, 2, 4])

with col_save:
    if st.button("💾 Save all settings", use_container_width=True):
        for key, value in pending.items():
            save_setting(key, value)
        st.success("✅ All settings saved. Next search will use these values.")
        st.rerun()

with col_reset:
    if st.button("↩️ Reset all to defaults", use_container_width=True):
        reset_all()
        st.success("Settings reset to defaults.")
        st.rerun()

with col_status:
    total_modified = len(modified_keys)
    if total_modified > 0:
        st.markdown(
            f'<div class="warn-box" style="margin:0;">⚠ <strong>{total_modified}</strong> '
            f'setting(s) differ from defaults: {", ".join(modified_keys)}</div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;'
            'padding:10px 14px;font-size:0.78rem;color:#166534;">✓ All settings are at defaults</div>',
            unsafe_allow_html=True)
