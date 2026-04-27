"""
pages/3_Export.py — Export search results to PDF (sales-ready list view).
"""
import streamlit as st
import sys, os, io, textwrap
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DB_PATH
import sqlite3, json

def run():
    #st.set_page_config(page_title="Export · Lead Finder", page_icon="📄", layout="wide")

    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');
    html,body,[class*="css"]{ font-family:'DM Sans',sans-serif; }
    #MainMenu,footer,header{ visibility:hidden; }
    .block-container{ padding:2rem 2rem 4rem; max-width:900px; }
    .pg-title { font-family:'DM Mono',monospace; font-size:1.4rem; font-weight:500; color:#111827; margin:0; }
    .pg-sub   { font-size:0.8rem; color:#6b7280; margin-top:.2rem; }
    .preview-card { background:#fff; border:1px solid #e5e7eb; border-radius:10px;
                    padding:1rem 1.25rem; margin-bottom:.6rem; }
    .preview-title { font-size:.9rem; font-weight:600; color:#111827; margin:0 0 2px; }
    .preview-co    { font-size:.8rem; color:#374151; margin:0; }
    .preview-score { font-family:'DM Mono',monospace; font-size:1.1rem; font-weight:500; }
    .preview-reason{ font-size:.75rem; color:#374151; background:#fffbeb; border:1px solid #fde68a;
                    border-left:3px solid #f59e0b; padding:6px 9px; border-radius:0 5px 5px 0;
                    margin-top:6px; line-height:1.5; }
    .stButton>button { background:#111827!important; color:#fff!important; border:none!important;
        border-radius:8px!important; font-weight:500!important; }
    .stButton>button:hover{ background:#1f2937!important; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="padding:1.5rem 0 1rem;border-bottom:1px solid #e5e7eb;margin-bottom:1.5rem;">
    <p class="pg-title">📄 export</p>
    <p class="pg-sub">Download search results as a PDF · Sales-ready list format</p>
    </div>
    """, unsafe_allow_html=True)



    # ── UI ────────────────────────────────────────────────────────────────────────
    total_db, last_save = db_stats_export()

    if total_db == 0:
        st.markdown("""
        <div style="text-align:center;padding:3rem 2rem;color:#9ca3af;">
        <div style="font-size:2rem;margin-bottom:.5rem;">🗄️</div>
        <div style="font-size:.9rem;font-weight:500;color:#4b5563;">No leads in database</div>
        <div>Run a search first, then come back to export</div>
        </div>
        """, unsafe_allow_html=True)
        st.stop()

    st.markdown(f"""
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;
                padding:10px 14px;font-size:.78rem;color:#065f46;margin-bottom:1.5rem;">
    💾 <strong>{total_db}</strong> leads available in database · Last saved: {last_save}
    </div>
    """, unsafe_allow_html=True)

    # Export filters
    st.markdown("### Filter leads to export")
    col1, col2, col3 = st.columns(3)
    with col1:
        exp_priority = st.multiselect("Priority", ["High","Medium","Low"],
                                    default=["High","Medium"])
    with col2:
        exp_min_score = st.slider("Min score (/13)", 0, 13, 8)
    with col3:
        exp_kw = st.text_input("Keyword filter", placeholder="company or title…")

    # Export options
    st.markdown("### Export options")
    c1, c2, c3 = st.columns(3)
    with c1:
        report_title = st.text_input("Report title", value="Lead Report — VE")
    with c2:
        include_reason = st.checkbox("Include 'Why they will buy'", value=True)
    with c3:
        include_trace  = st.checkbox("Include scoring trace", value=False)

    # Load and preview
    jobs = load_for_export(
        priority_filter=exp_priority if exp_priority else None,
        min_score=exp_min_score,
        kw_filter=exp_kw,
    )

    st.markdown(f"### Preview ({len(jobs)} leads)")

    if not jobs:
        st.warning("No leads match your filters — try lowering the min score or adding more priority tiers.")
    else:
        # Stats row
        high_n = sum(1 for j in jobs if j.get("priority")=="High")
        med_n  = sum(1 for j in jobs if j.get("priority")=="Medium")
        low_n  = sum(1 for j in jobs if j.get("priority")=="Low")
        st.markdown(f"""
        <div style="display:flex;gap:.75rem;padding:.7rem 1.1rem;background:#f9fafb;
                    border:1px solid #e5e7eb;border-radius:8px;margin-bottom:1rem;flex-wrap:wrap;">
        <div style="text-align:center;flex:1;">
            <div style="font-family:'DM Mono',monospace;font-size:1.1rem;font-weight:500;">{len(jobs)}</div>
            <div style="font-size:.62rem;color:#9ca3af;text-transform:uppercase;">Total</div>
        </div>
        <div style="text-align:center;flex:1;">
            <div style="font-family:'DM Mono',monospace;font-size:1.1rem;font-weight:500;color:#ef4444;">{high_n}</div>
            <div style="font-size:.62rem;color:#9ca3af;text-transform:uppercase;">High</div>
        </div>
        <div style="text-align:center;flex:1;">
            <div style="font-family:'DM Mono',monospace;font-size:1.1rem;font-weight:500;color:#f59e0b;">{med_n}</div>
            <div style="font-size:.62rem;color:#9ca3af;text-transform:uppercase;">Medium</div>
        </div>
        <div style="text-align:center;flex:1;">
            <div style="font-family:'DM Mono',monospace;font-size:1.1rem;font-weight:500;color:#94a3b8;">{low_n}</div>
            <div style="font-size:.62rem;color:#9ca3af;text-transform:uppercase;">Low</div>
        </div>
        </div>
        """, unsafe_allow_html=True)

        # Card preview (first 5)
        for job in jobs[:5]:
            p   = (job.get("priority","Low")).lower()
            sc  = job.get("score", 0)
            sc_c = {"high":"#ef4444","medium":"#f59e0b","low":"#94a3b8"}.get(p,"#94a3b8")
            raw_reason = job.get("buy_reason","")
            if "→" in raw_reason:
                parts = [x.strip() for x in raw_reason.split("→")]
                labels = ["Signal","Meaning","Why VE"]
                r_parts = " <span style='color:#f59e0b;font-weight:700;margin:0 3px;'>→</span> ".join(
                    f"<b style='font-size:.67rem;text-transform:uppercase;color:#92400e;'>{labels[i]}</b> {pt}"
                    for i, pt in enumerate(parts[:3])
                )
                reason_html = f'<div class="preview-reason">{r_parts}</div>'
            elif raw_reason:
                reason_html = f'<div class="preview-reason">{raw_reason}</div>'
            else:
                reason_html = ""

            meta = " · ".join(filter(None,[job.get("location",""), job.get("posted_at","")[:10]]))
            st.markdown(f"""
            <div class="preview-card" style="border-left:3px solid {sc_c};">
            <div style="display:flex;align-items:flex-start;gap:1rem;">
                <div style="flex:1;">
                <p class="preview-title">{job.get("title","")}</p>
                <p class="preview-co">{job.get("company","")}
                    {"&nbsp;·&nbsp;<span style='font-size:.7rem;color:#9ca3af;'>"+meta+"</span>" if meta else ""}
                </p>
                {reason_html if include_reason else ""}
                </div>
                <div style="text-align:center;flex-shrink:0;width:50px;">
                <div class="preview-score" style="color:{sc_c};">{sc}</div>
                <div style="font-size:.62rem;color:#9ca3af;">/13</div>
                </div>
            </div>
            </div>
            """, unsafe_allow_html=True)

        if len(jobs) > 5:
            st.caption(f"+ {len(jobs)-5} more leads in the export")

        # Generate and download PDF
        st.markdown("---")
        if st.button("📥 Generate & Download PDF", use_container_width=False):
            with st.spinner("Building PDF…"):
                try:
                    import reportlab
                    pdf_buf, err = build_pdf(
                        jobs,
                        title=report_title,
                        include_reason=include_reason,
                        include_trace=include_trace,
                    )
                    if err:
                        st.error(f"PDF error: {err}")
                    else:
                        filename = f"leads_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
                        st.download_button(
                            label="⬇️ Download PDF",
                            data=pdf_buf,
                            file_name=filename,
                            mime="application/pdf",
                            use_container_width=False,
                        )
                        st.success(f"✅ PDF ready — {len(jobs)} leads exported")
                except ImportError:
                    st.error("reportlab is not installed. Run: `pip install reportlab`")





# ── Load data ─────────────────────────────────────────────────────────────────
def load_for_export(priority_filter=None, min_score=0, kw_filter=""):
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        q = "SELECT * FROM leads WHERE score >= ?"
        p = [min_score]
        if priority_filter:
            q += " AND priority IN (" + ",".join("?"*len(priority_filter)) + ")"
            p += priority_filter
        if kw_filter.strip():
            k = "%" + kw_filter.strip().lower() + "%"
            q += " AND (LOWER(title) LIKE ? OR LOWER(company) LIKE ? OR LOWER(search_kw) LIKE ?)"
            p += [k, k, k]
        q += " ORDER BY score DESC"
        rows = conn.execute(q, p).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def db_stats_export():
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        last  = conn.execute("SELECT MAX(saved_at) FROM leads").fetchone()[0]
        conn.close()
        return total, (last or "")[:10]
    except Exception:
        return 0, ""


# ── PDF builder using reportlab ───────────────────────────────────────────────
def build_pdf(jobs, title="Lead Report", include_reason=True, include_trace=False):
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle, HRFlowable)
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    except ImportError:
        return None, "reportlab not installed"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=18*mm, bottomMargin=18*mm,
    )

    styles = getSampleStyleSheet()
    # Custom styles
    H1  = ParagraphStyle("H1",  fontSize=18, fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#111827"), spaceAfter=4)
    SUB = ParagraphStyle("SUB", fontSize=9,  fontName="Helvetica",
                          textColor=colors.HexColor("#6b7280"), spaceAfter=16)
    SEC = ParagraphStyle("SEC", fontSize=8,  fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#374151"),
                          backColor=colors.HexColor("#f9fafb"),
                          borderPadding=(4,6,4,6), spaceAfter=2)
    TH  = ParagraphStyle("TH",  fontSize=7.5, fontName="Helvetica-Bold",
                          textColor=colors.HexColor("#374151"))
    TD  = ParagraphStyle("TD",  fontSize=8,  fontName="Helvetica",
                          textColor=colors.HexColor("#111827"), leading=11)
    TDS = ParagraphStyle("TDS", fontSize=7.5, fontName="Helvetica",
                          textColor=colors.HexColor("#6b7280"), leading=10)
    REASON = ParagraphStyle("RSN", fontSize=7.5, fontName="Helvetica-Oblique",
                             textColor=colors.HexColor("#374151"),
                             backColor=colors.HexColor("#fffbeb"),
                             borderPadding=(3,5,3,5), leading=10)

    PRIORITY_COLORS = {
        "High":   colors.HexColor("#fef2f2"),
        "Medium": colors.HexColor("#fffbeb"),
        "Low":    colors.HexColor("#f8fafc"),
    }
    PRIORITY_TEXT = {
        "High":   colors.HexColor("#b91c1c"),
        "Medium": colors.HexColor("#b45309"),
        "Low":    colors.HexColor("#64748b"),
    }
    SCORE_COLORS = {
        "High":   colors.HexColor("#ef4444"),
        "Medium": colors.HexColor("#f59e0b"),
        "Low":    colors.HexColor("#94a3b8"),
    }

    story = []
    now = datetime.now().strftime("%d %b %Y, %H:%M")

    # Cover / header
    story.append(Paragraph(title, H1))
    story.append(Paragraph(
        f"Generated: {now} &nbsp;·&nbsp; {len(jobs)} leads &nbsp;·&nbsp; "
        f"High: {sum(1 for j in jobs if j.get('priority')=='High')} &nbsp;·&nbsp; "
        f"Medium: {sum(1 for j in jobs if j.get('priority')=='Medium')}",
        SUB))
    story.append(HRFlowable(width="100%", thickness=0.5,
                             color=colors.HexColor("#e5e7eb"), spaceAfter=12))

    # Table header
    col_widths = [90, 110, 52, 65, 35]  # company, role, location, priority, score
    if include_reason:
        col_widths.append(175)           # reason

    header_row = [
        Paragraph("Company", TH),
        Paragraph("Role", TH),
        Paragraph("Location", TH),
        Paragraph("Priority", TH),
        Paragraph("Score", TH),
    ]
    if include_reason:
        header_row.append(Paragraph("Why they will buy", TH))

    table_data = [header_row]
    row_styles = []

    for i, job in enumerate(jobs):
        pri   = job.get("priority", "Low")
        score = job.get("score", 0)
        loc   = (job.get("location","") or "")[:24]
        url   = job.get("url","")

        title_cell = Paragraph(
            f'<a href="{url}" color="#111827"><b>{job.get("title","")}</b></a>'
            if url else f'<b>{job.get("title","")}</b>',
            TD)
        company_cell = Paragraph(job.get("company",""), TD)
        loc_cell     = Paragraph(loc, TDS)

        # Priority badge cell
        pri_style = ParagraphStyle(
            "PRI", fontSize=7, fontName="Helvetica-Bold",
            textColor=PRIORITY_TEXT.get(pri, colors.black),
            alignment=TA_CENTER)
        pri_cell = Paragraph(pri.upper(), pri_style)

        # Score cell
        sc_style = ParagraphStyle(
            "SC", fontSize=11, fontName="Helvetica-Bold",
            textColor=SCORE_COLORS.get(pri, colors.gray),
            alignment=TA_CENTER)
        sc_cell = Paragraph(f"{score}/13", sc_style)

        row = [company_cell, title_cell, loc_cell, pri_cell, sc_cell]

        if include_reason:
            raw_reason = job.get("buy_reason","")
            if "→" in raw_reason:
                # Format the three parts with labels
                parts  = [p.strip() for p in raw_reason.split("→")]
                labels = ["Signal", "Meaning", "Why VE"]
                lines  = []
                for ri, part in enumerate(parts[:3]):
                    lbl = labels[ri] if ri < 3 else ""
                    lines.append(f"<b>{lbl}:</b> {part}")
                reason_text = " → ".join(lines)
            else:
                reason_text = raw_reason or "—"
            row.append(Paragraph(reason_text, REASON))

        table_data.append(row)

        # Alternating row background
        bg = colors.HexColor("#fafafa") if i % 2 == 0 else colors.white
        row_styles.append(("BACKGROUND", (0, i+1), (-1, i+1), bg))
        # Priority cell tint
        row_styles.append(("BACKGROUND", (3, i+1), (3, i+1),
                            PRIORITY_COLORS.get(pri, colors.white)))

    table = Table(table_data, colWidths=[w*mm for w in col_widths], repeatRows=1)
    base_style = [
        ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("TEXTCOLOR",   (0, 0), (-1, 0), colors.HexColor("#374151")),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ("GRID",        (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e7eb")),
        ("LINEBELOW",   (0, 0), (-1, 0),  0.8, colors.HexColor("#cbd5e1")),
        ("VALIGN",      (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",(0, 0), (-1, -1), 5),
    ] + row_styles

    table.setStyle(TableStyle(base_style))
    story.append(table)

    # Footer
    story.append(Spacer(1, 8*mm))
    story.append(HRFlowable(width="100%", thickness=0.3,
                             color=colors.HexColor("#e5e7eb"), spaceBefore=4))
    story.append(Paragraph(
        f"Lead Identification Framework V3 · VE · Exported {now}",
        ParagraphStyle("FTR", fontSize=7, fontName="Helvetica",
                       textColor=colors.HexColor("#9ca3af"), alignment=TA_CENTER)
    ))

    doc.build(story)
    buf.seek(0)
    return buf, None
