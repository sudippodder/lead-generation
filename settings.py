"""
pages/2_Settings.py — Settings for the 5-step lead filter (V6 PDF framework).
Each section maps 1:1 to a step in the filter. Changes take effect on the next search.
"""
import streamlit as st
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import get_cfg, save_setting, reset_all, DEFAULTS

# st.set_page_config handled by app.py — removed to prevent import-time crash


cfg = get_cfg()
pending = {}

def _badge(key):
    saved = cfg.get(key)
    is_mod = saved is not None and saved != DEFAULTS.get(key)
    return '<span class="badge-mod">modified</span>' if is_mod else '<span class="badge-def">default</span>'

def _editable(key, height=140):
    cfg = get_cfg()
    """Render a textarea for a list constant. Returns new list."""
    current = cfg.get(key, DEFAULTS.get(key, []))
    if isinstance(current, list):
        text_val = "\n".join(current)
    else:
        text_val = str(current)
    new_text = st.text_area(
        key, value=text_val, height=height,
        label_visibility="collapsed",
        key=f"ta_{key}",
        help=f"One keyword per line. Currently {len(current) if isinstance(current,list) else 1} entries.",
    )
    if isinstance(current, list):
        return [x.strip() for x in new_text.splitlines() if x.strip()]
    return new_text.strip()

def _step_header(num, color, bg, title, rule, action):
    st.markdown(f"""
    <div class="step-header">
      <div class="step-num" style="background:{bg};color:{color};">{num}</div>
      <div>
        <div class="step-title">{title}</div>
        <div class="step-rule">{rule}</div>
      </div>
      <div style="margin-left:auto;font-size:.72rem;font-weight:600;
                  color:{color};background:{bg};padding:3px 9px;border-radius:5px;">
        {action}
      </div>
    </div>
    """, unsafe_allow_html=True)



def run():
    cfg = get_cfg()
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');
    html,body,[class*="css"]{ font-family:'DM Sans',sans-serif; }
    #MainMenu,footer,header{ visibility:hidden; }
    .block-container{ padding:2rem 2rem 4rem; max-width:1100px; }

    .pg-header { padding:1.5rem 0 1rem; border-bottom:1px solid #e5e7eb; margin-bottom:1.5rem; }
    .pg-title  { font-family:'DM Mono',monospace; font-size:1.4rem; font-weight:500; 
     margin:0; }
    .pg-sub    { font-size:0.8rem; color:#6b7280; margin-top:.2rem; }

    .step-header { display:flex; align-items:center; gap:12px; padding:12px 16px;
                border-radius:10px 10px 0 0; border:1px solid #e5e7eb;
                border-bottom:none; margin-top:1.5rem; }
    .step-num  { width:28px; height:28px; border-radius:50%; display:flex; align-items:center;
                justify-content:center; font-size:12px; font-weight:700; flex-shrink:0; }
    .step-title{ font-size:.95rem; font-weight:600;  }
    .step-rule { font-size:.78rem; color:#6b7280; margin-top:2px; }

    .step-body { border:1px solid #e5e7eb; border-radius:0 0 10px 10px;
                padding:14px 16px; background:#fff; margin-bottom:.5rem; }

    .list-box  { border:1px solid #e5e7eb; border-radius:8px; overflow:hidden; }
    .list-label{ font-size:.7rem; font-weight:600; text-transform:uppercase;
                letter-spacing:.05em; color:#6b7280; padding:6px 10px 4px;
                background:#f9fafb; border-bottom:1px solid #e5e7eb; }

    .tag-keep   { display:inline-block; padding:2px 8px; border-radius:4px;
                font-size:.72rem; background:#f0fdf4; color:#166534;
                border:1px solid #bbf7d0; margin:2px; }
    .tag-reject { display:inline-block; padding:2px 8px; border-radius:4px;
                font-size:.72rem; background:#fef2f2; color:#b91c1c;
                border:1px solid #fecaca; margin:2px; }

    .badge-mod { display:inline-block; padding:1px 7px; border-radius:99px; font-size:.65rem;
                font-weight:600; background:#fef9c3; color:#854d0e;
                border:1px solid #fde68a; margin-left:6px; }
    .badge-def { display:inline-block; padding:1px 7px; border-radius:99px; font-size:.65rem;
                font-weight:600; background:#f0fdf4; color:#166534;
                border:1px solid #bbf7d0; margin-left:6px; }

    div[data-testid="stTextArea"] textarea {
        font-family:'DM Mono',monospace; font-size:.77rem; line-height:1.5;
    }
    .stButton>button { background:#111827!important; color:#fff!important; border:none!important;
        border-radius:8px!important; font-weight:500!important; font-size:.875rem!important; }
    .stButton>button:hover{ background:#1f2937!important; }
    </style>
    """, unsafe_allow_html=True)

    # ── Header ────────────────────────────────────────────────────────────────────
    st.markdown("""
    <div class="pg-header">
    <p class="pg-title">⚙️ Filter settings</p>
    <p class="pg-sub">5-step lead evaluation · V6 framework · Changes apply to the next search</p>
    </div>
    """, unsafe_allow_html=True)
    # ══════════════════════════════════════════════════════════════════════════════
    # STEP 1 — Type of Hiring
    # ══════════════════════════════════════════════════════════════════════════════
    _step_header("1", "#166534", "#f0fdf4",
        "Type of Hiring",
        "KEEP execution roles · REJECT capability roles · First check on job title",
        "TITLE CHECK")

    with st.container():
        st.markdown('<div class="step-body">', unsafe_allow_html=True)
        c1, c2 = st.columns(2)

        with c1:
            st.markdown(
                f'<div class="list-label">✅ KEEP — Execution roles {_badge("SERVICEABLE_ROLES")}</div>',
                unsafe_allow_html=True)
            st.caption("Title must contain at least one of these to pass Step 1.")
            pending["SERVICEABLE_ROLES"] = _editable("SERVICEABLE_ROLES", height=220)

        with c2:
            st.markdown(
                f'<div class="list-label">❌ REJECT — Capability roles (ANY match = instant reject) {_badge("REJECT_ROLES")}</div>',
                unsafe_allow_html=True)
            st.caption("If ANY of these appear in the job title → reject immediately, no further checks.")
            pending["REJECT_ROLES"] = _editable("REJECT_ROLES", height=220)

        st.markdown('</div>', unsafe_allow_html=True)


    # ══════════════════════════════════════════════════════════════════════════════
    # STEP 2 — Company Check
    # ══════════════════════════════════════════════════════════════════════════════
    _step_header("2", "#b91c1c", "#fef2f2",
        "Company Check",
        "Reject HR/staffing/consulting · Reject large enterprise (1000+) · Must pass ALL checks",
        "COMPANY CHECK")

    with st.container():
        st.markdown('<div class="step-body">', unsafe_allow_html=True)
        c1, c2 = st.columns(2)

        with c1:
            st.markdown(
                f'<div class="list-label">❌ REJECT — Company types {_badge("REJECT_COMPANIES")}</div>',
                unsafe_allow_html=True)
            st.caption("Matches company name or first 300 chars of JD. HR/staffing/consulting/agencies.")
            pending["REJECT_COMPANIES"] = _editable("REJECT_COMPANIES", height=180)

            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(
                f'<div class="list-label">❌ REJECT — Known large enterprises {_badge("KNOWN_ENTERPRISE")}</div>',
                unsafe_allow_html=True)
            st.caption("Company names matched against the hiring company. Partial match.")
            pending["KNOWN_ENTERPRISE"] = _editable("KNOWN_ENTERPRISE", height=180)

        with c2:
            st.markdown(
                f'<div class="list-label">❌ REJECT — Enterprise language in JD {_badge("ENTERPRISE_SIGNALS")}</div>',
                unsafe_allow_html=True)
            st.caption("2 or more of these in the JD text = reject. Indicates large corp.")
            pending["ENTERPRISE_SIGNALS"] = _editable("ENTERPRISE_SIGNALS", height=160)

            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(
                f'<div class="list-label">⚠️ NOTE — Size threshold</div>',
                unsafe_allow_html=True)
            size_val = cfg.get("MAX_COMPANY_SIZE", 1000)
            new_size = st.number_input("Reject companies with employees ≥",
                                    min_value=50, max_value=5000, value=int(size_val), step=50,
                                    key="MAX_COMPANY_SIZE_input")
            pending["MAX_COMPANY_SIZE"] = int(new_size)
            st.caption("Used when company size is detectable in the JD.")

        st.markdown('</div>', unsafe_allow_html=True)


    # ══════════════════════════════════════════════════════════════════════════════
    # STEP 3 — Hiring Pattern
    # ══════════════════════════════════════════════════════════════════════════════
    _step_header("3", "#b45309", "#fffbeb",
        "Hiring Pattern",
        "Need at least ONE: 3+ same roles · Same job repeated · Same role across locations",
        "PATTERN CHECK")

    with st.container():
        st.markdown('<div class="step-body">', unsafe_allow_html=True)
        st.caption("If the company has 2+ open roles in the dataset, this step passes automatically. "
                "Otherwise at least one keyword below must appear in the JD.")
        pending["HIRING_PATTERN_SIGNALS"] = _editable("HIRING_PATTERN_SIGNALS", height=140)
        st.markdown('</div>', unsafe_allow_html=True)


    # ══════════════════════════════════════════════════════════════════════════════
    # STEP 4 — What Work Is Increasing?
    # ══════════════════════════════════════════════════════════════════════════════
    _step_header("4", "#1d4ed8", "#eff6ff",
        "What Work Is Increasing?",
        "Must identify SPECIFIC workload — not just 'scaling' or 'growing team'",
        "WORKLOAD CHECK")

    with st.container():
        st.markdown('<div class="step-body">', unsafe_allow_html=True)
        c1, c2 = st.columns(2)

        with c1:
            st.markdown(
                f'<div class="list-label">✅ VALID — Specific workload signals {_badge("WORKLOAD_SIGNALS")}</div>',
                unsafe_allow_html=True)
            st.caption('Good: "support load increasing" · "ticket volume" · "overwhelmed". At least one required.')
            pending["WORKLOAD_SIGNALS"] = _editable("WORKLOAD_SIGNALS", height=200)

        with c2:
            st.markdown(
                f'<div class="list-label">❌ INVALID — Vague signals (reject if ONLY these exist) {_badge("VAGUE_SIGNALS")}</div>',
                unsafe_allow_html=True)
            st.caption('Bad: "company is growing" · "scaling team" · "exciting opportunity". Not evidence of work increase.')
            pending["VAGUE_SIGNALS"] = _editable("VAGUE_SIGNALS", height=200)

        st.markdown('</div>', unsafe_allow_html=True)


    # ══════════════════════════════════════════════════════════════════════════════
    # STEP 5 — Remote Compatibility
    # ══════════════════════════════════════════════════════════════════════════════
    _step_header("5", "#6d28d9", "#f5f3ff",
        "Remote Compatibility",
        "Remote / hybrid → continue · Onsite-only → reject (can't serve)",
        "REMOTE CHECK")

    with st.container():
        st.markdown('<div class="step-body">', unsafe_allow_html=True)
        st.markdown(
            f'<div class="list-label">❌ REJECT — Onsite-only phrases {_badge("ONSITE_BLOCKERS")}</div>',
            unsafe_allow_html=True)
        st.caption("If ANY of these appear in the JD → hard reject. Remote staffing is not viable.")
        pending["ONSITE_BLOCKERS"] = _editable("ONSITE_BLOCKERS", height=140)
        st.markdown('</div>', unsafe_allow_html=True)


    # ══════════════════════════════════════════════════════════════════════════════
    # Save / Reset
    # ══════════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    c_save, c_reset, c_status = st.columns([2, 2, 4])

    with c_save:
        if st.button("💾 Save all settings", use_container_width=True):
            for key, value in pending.items():
                save_setting(key, value)
            st.success("✅ Saved. Next search will use these filter rules.")
            st.rerun()

    with c_reset:
        if st.button("↩️ Reset all to defaults", use_container_width=True):
            reset_all()
            st.success("Reset to defaults.")
            st.rerun()

    with c_status:
        modified = [k for k in pending if isinstance(pending[k], list)
                    and pending[k] != DEFAULTS.get(k)]
    if modified:
        st.markdown(
            f'<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;'
            f'padding:8px 12px;font-size:.76rem;color:#92400e;">⚠ '
            f'<strong>{len(modified)}</strong> step(s) modified from defaults: '
            f'{", ".join(modified[:3])}{"…" if len(modified)>3 else ""}</div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;'
            'padding:8px 12px;font-size:.76rem;color:#166534;">✓ All filter steps at defaults</div>',
            unsafe_allow_html=True)