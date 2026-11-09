"""ops_dashboard.py — Self-contained HTML Operations Dashboard for Chilli Gavva robot.

Renders ops_report.py's JSON summary into a single private HTML page
at exported_content/ops/index.html (or specified output path).

Cards included:
  1. Robot Runs (runs per day with inline SVG bar chart)
  2. Failures & Retries (step breakdown, queued retries, inline SVG chart)
  3. AI Providers (status UP/DOWN, ok vs fail counts, inline bar chart)
  4. Drafts Pipeline (made, approved, rejected, pending counts)
  5. Upload Packs Ready (Gumroad/Topmate ready packs count)
  6. Marketing Assets (carousels, pins, reels, blogs, SEO metadata, inline SVG chart)

Key constraints:
  - Self-contained HTML: no external JS libraries, no remote fonts, no external CSS/images.
  - Tiny inline SVG bar charts.
  - Tenglish house style + English labels (Telugu sentence words + English nouns).
  - Strictly counts only: NO money amounts, currency symbols, or financial figures.
  - No brand logos or names.
  - Deterministic outputs and atomic writes via temp file and os.replace.
  - Output goes outside private pack folders (default: exported_content/ops/index.html).

CLI:
  python3 ops_dashboard.py [--days 7] [--db-path PATH] [--out-path PATH]
"""

import argparse
import html
import os
import pathlib
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import ops_report

DEFAULT_OUT_DIR = "exported_content/ops"
DEFAULT_OUT_FILE = "exported_content/ops/index.html"


def generate_bar_chart_svg(
    items: List[Tuple[str, int]],
    width: int = 340,
    height: int = 140,
    bar_color: str = "#3b82f6",
    empty_label: str = "Data ledu",
) -> str:
    """Generate a tiny, valid, self-contained inline SVG bar chart."""
    if not items:
        return (
            f'<svg viewBox="0 0 {width} {height}" class="chart-svg" width="{width}" height="{height}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{html.escape(empty_label)}">'
            f'<rect width="{width}" height="{height}" fill="#f8fafc" rx="6"/>'
            f'<text x="{width / 2}" y="{height / 2 + 5}" text-anchor="middle" '
            f'fill="#94a3b8" font-size="12" font-family="sans-serif">{html.escape(empty_label)}</text>'
            f'</svg>'
        )

    padding_top = 22
    padding_bottom = 26
    padding_left = 12
    padding_right = 12

    chart_w = width - padding_left - padding_right
    chart_h = height - padding_top - padding_bottom

    max_val = max((val for _, val in items), default=0)
    # Ensure scale is at least 1 to avoid zero division
    scale_max = max(max_val, 1)

    n = len(items)
    slot_w = chart_w / n
    bar_w = max(slot_w * 0.65, 4)
    bar_gap = (slot_w - bar_w) / 2

    elements = [
        f'<svg viewBox="0 0 {width} {height}" class="chart-svg" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Operations Bar Chart">',
        f'<rect width="{width}" height="{height}" fill="#f8fafc" rx="6" />',
    ]

    # Baseline axis line
    baseline_y = padding_top + chart_h
    elements.append(
        f'<line x1="{padding_left}" y1="{baseline_y}" x2="{width - padding_right}" y2="{baseline_y}" '
        f'stroke="#cbd5e1" stroke-width="1"/>'
    )

    for i, (label, val) in enumerate(items):
        cx = padding_left + i * slot_w + bar_gap
        bar_height = (val / scale_max) * chart_h if val > 0 else 2
        bar_y = baseline_y - bar_height

        clean_label = html.escape(str(label))
        clean_val = str(val)

        # Bar rect
        fill = bar_color if val > 0 else "#e2e8f0"
        elements.append(
            f'<rect x="{cx:.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" height="{bar_height:.1f}" '
            f'fill="{fill}" rx="3" aria-label="{clean_label}: {clean_val}"/>'
        )

        # Value text on top of bar
        if val > 0:
            val_y = max(bar_y - 4, 12)
            elements.append(
                f'<text x="{cx + bar_w / 2:.1f}" y="{val_y:.1f}" text-anchor="middle" '
                f'fill="#1e293b" font-size="10" font-weight="600" font-family="sans-serif">{clean_val}</text>'
            )

        # X-axis label
        label_y = baseline_y + 14
        display_label = clean_label[-5:] if len(clean_label) > 6 else clean_label
        elements.append(
            f'<text x="{cx + bar_w / 2:.1f}" y="{label_y:.1f}" text-anchor="middle" '
            f'fill="#64748b" font-size="9" font-family="sans-serif">{display_label}</text>'
        )

    elements.append("</svg>")
    return "\n".join(elements)


def render_dashboard_html(data: Dict[str, Any]) -> str:
    """Generate self-contained HTML for ops dashboard using ops_report JSON data."""
    days = data.get("period_days", 7)
    gen_at = html.escape(str(data.get("generated_at", "")[:19]).replace("T", " "))

    # 1. Runs per day card
    runs_by_day = data.get("runs_per_day", {})
    sorted_days = sorted(runs_by_day.items())
    total_runs = sum(runs_by_day.values())
    runs_chart_data = [(k, int(v)) for k, v in sorted_days]
    runs_chart_svg = generate_bar_chart_svg(runs_chart_data, bar_color="#2563eb", empty_label="Runs record avvaledu")

    # 2. Failures & retries card
    fails = data.get("failures", {})
    total_fails = int(fails.get("total", 0))
    by_step = fails.get("by_step", {})
    retries = int(data.get("retries_queued", 0))

    sorted_steps = sorted(by_step.items(), key=lambda x: x[1], reverse=True)[:6]
    fail_chart_data = [(k, int(v)) for k, v in sorted_steps]
    fail_chart_svg = generate_bar_chart_svg(
        fail_chart_data, bar_color="#ef4444", empty_label="Zero failures! Anni smooth ga run ayyayi"
    )

    # 3. AI Providers card
    providers = data.get("providers", {})
    prov_rows = []
    prov_chart_data = []
    for p_name in sorted(providers.keys()):
        p_info = providers[p_name]
        status = p_info.get("status", "UNKNOWN")
        ok_count = int(p_info.get("ok", 0))
        fail_count = int(p_info.get("fail", 0))
        prov_chart_data.append((p_name, ok_count))

        badge_class = "badge-up" if status == "UP" else "badge-down"
        prov_rows.append(
            f'<tr>'
            f'<td><strong>{html.escape(p_name)}</strong></td>'
            f'<td><span class="badge {badge_class}">{status}</span></td>'
            f'<td>{ok_count} ok</td>'
            f'<td>{fail_count} fail</td>'
            f'</tr>'
        )
    prov_chart_svg = generate_bar_chart_svg(
        prov_chart_data, bar_color="#10b981", empty_label="AI usage data ledu"
    )

    # 4. Drafts card
    drafts = data.get("drafts", {})
    d_made = int(drafts.get("made", 0))
    d_appr = int(drafts.get("approved", 0))
    d_rej = int(drafts.get("rejected", 0))
    d_pend = int(drafts.get("pending", 0))
    draft_chart_data = [
        ("Made", d_made),
        ("Appr", d_appr),
        ("Rej", d_rej),
        ("Pend", d_pend),
    ]
    draft_chart_svg = generate_bar_chart_svg(
        draft_chart_data, bar_color="#8b5cf6", empty_label="Drafts levu"
    )

    # 5. Packs ready card
    packs_ready = int(data.get("packs_ready", 0))

    # 6. Marketing files card
    mkt = data.get("marketing_files", {})
    m_total = int(mkt.get("total_files", 0))
    m_carousels = int(mkt.get("carousels", 0))
    m_pins = int(mkt.get("pins", 0))
    m_reels = int(mkt.get("reels", 0))
    m_blogs = int(mkt.get("blog_posts", 0))
    m_seo = int(mkt.get("seo_meta", 0))
    m_keywords = int(mkt.get("keywords", 0))

    mkt_chart_data = [
        ("Carsl", m_carousels),
        ("Pins", m_pins),
        ("Reels", m_reels),
        ("Blogs", m_blogs),
        ("SEO", m_seo),
    ]
    mkt_chart_svg = generate_bar_chart_svg(
        mkt_chart_data, bar_color="#f59e0b", empty_label="Marketing files levu"
    )

    by_pack = mkt.get("by_pack", {})
    pack_breakdown_items = []
    for pid in sorted(by_pack.keys()):
        pack_breakdown_items.append(f"<li><span>{html.escape(pid)}</span><strong>{by_pack[pid]} files</strong></li>")
    pack_breakdown_html = "\n".join(pack_breakdown_items) if pack_breakdown_items else "<li><span>Files em levu</span></li>"

    html_content = f"""<!DOCTYPE html>
<html lang="te-IN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Chilli Gavva Robot — Ops Dashboard</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --card-border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #38bdf8;
      --success: #34d399;
      --danger: #f87171;
      --warning: #fbbf24;
      --primary: #60a5fa;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px;
    }}
    header {{
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }}
    h1 {{
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.02em;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .subhead {{
      font-size: 13px;
      color: var(--text-muted);
    }}
    .meta-tag {{
      display: inline-block;
      padding: 4px 10px;
      background: #0284c7;
      color: #ffffff;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 600;
    }}
    .dashboard-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 20px;
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      padding-bottom: 10px;
    }}
    .card-title {{
      font-size: 15px;
      font-weight: 600;
      color: var(--text);
    }}
    .card-badge {{
      font-size: 11px;
      color: var(--text-muted);
      background: rgba(255, 255, 255, 0.05);
      padding: 2px 8px;
      border-radius: 4px;
    }}
    .metric-hero {{
      display: flex;
      align-items: baseline;
      gap: 10px;
    }}
    .metric-hero .big-val {{
      font-size: 32px;
      font-weight: 700;
      color: var(--primary);
    }}
    .metric-hero .hero-label {{
      font-size: 13px;
      color: var(--text-muted);
    }}
    .chart-container {{
      width: 100%;
      overflow-x: auto;
      border-radius: 6px;
      background: #f8fafc;
      padding: 4px;
    }}
    .chart-svg {{
      display: block;
      width: 100%;
      height: auto;
    }}
    .data-table {{
      width: 100%;
      font-size: 12px;
      border-collapse: collapse;
      margin-top: 4px;
    }}
    .data-table th, .data-table td {{
      padding: 6px 8px;
      text-align: left;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    }}
    .data-table th {{
      color: var(--text-muted);
      font-weight: 500;
    }}
    .badge {{
      display: inline-block;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 700;
    }}
    .badge-up {{ background: #065f46; color: #a7f3d0; }}
    .badge-down {{ background: #991b1b; color: #fecaca; }}
    .stats-row {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
    }}
    .stat-pill {{
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 6px;
      padding: 8px 10px;
      display: flex;
      flex-direction: column;
    }}
    .stat-pill .pill-val {{
      font-size: 18px;
      font-weight: 700;
      color: var(--text);
    }}
    .stat-pill .pill-lbl {{
      font-size: 11px;
      color: var(--text-muted);
    }}
    ul.item-list {{
      list-style: none;
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 12px;
      max-height: 120px;
      overflow-y: auto;
    }}
    ul.item-list li {{
      display: flex;
      justify-content: space-between;
      padding: 4px 6px;
      background: rgba(255, 255, 255, 0.02);
      border-radius: 4px;
    }}
    footer {{
      margin-top: 30px;
      padding-top: 16px;
      border-top: 1px solid var(--card-border);
      font-size: 12px;
      color: var(--text-muted);
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 8px;
    }}
  </style>
</head>
<body>

  <header>
    <div>
      <h1>🤖 Chilli Gavva Robot — Ops Dashboard</h1>
      <div class="subhead">Weekly operations monitor &amp; health metrics (counts only, private internal view)</div>
    </div>
    <div>
      <span class="meta-tag">Last {days} days summary</span>
    </div>
  </header>

  <main class="dashboard-grid">

    <!-- Card 1: Runs per day -->
    <section class="card" aria-label="Robot Runs Card">
      <div class="card-header">
        <h2 class="card-title">📅 Runs (Rojuki runs)</h2>
        <span class="card-badge">Total: {total_runs}</span>
      </div>
      <div class="metric-hero">
        <span class="big-val">{total_runs}</span>
        <span class="hero-label">total task executions last {days} days lo</span>
      </div>
      <div class="chart-container">
        {runs_chart_svg}
      </div>
      <p class="subhead">Roju variga tasks run ayina volume. High peaks unte robot active ga pani chesindi.</p>
    </section>

    <!-- Card 2: Failures & Retries -->
    <section class="card" aria-label="Failures and Retries Card">
      <div class="card-header">
        <h2 class="card-title">⚠️ Failures &amp; Retries (Lopalu)</h2>
        <span class="card-badge" style="color: {'#34d399' if total_fails == 0 else '#f87171'};">
          {'Zero failures ✓' if total_fails == 0 else f'{total_fails} fails'}
        </span>
      </div>
      <div class="stats-row">
        <div class="stat-pill">
          <span class="pill-val" style="color: {'#34d399' if total_fails == 0 else '#f87171'};">{total_fails}</span>
          <span class="pill-lbl">Failed tasks</span>
        </div>
        <div class="stat-pill">
          <span class="pill-val" style="color: {'#94a3b8' if retries == 0 else '#fbbf24'};">{retries}</span>
          <span class="pill-lbl">Retries queued</span>
        </div>
      </div>
      <div class="chart-container">
        {fail_chart_svg}
      </div>
      <p class="subhead">Eina steps lo errors vachayo bar chart lo chudandi. Auto-retry unte safe ga retry avutundi.</p>
    </section>

    <!-- Card 3: AI Providers -->
    <section class="card" aria-label="AI Providers Card">
      <div class="card-header">
        <h2 class="card-title">🔌 AI Providers (LLM Status)</h2>
        <span class="card-badge">{len(providers)} active</span>
      </div>
      <div class="chart-container">
        {prov_chart_svg}
      </div>
      {f'''<table class="data-table">
        <thead>
          <tr><th>Provider</th><th>Status</th><th>Ok</th><th>Fail</th></tr>
        </thead>
        <tbody>
          {''.join(prov_rows)}
        </tbody>
      </table>''' if prov_rows else '<p class="subhead">Last 7 days lo direct provider calls em record avvaledu.</p>'}
      <p class="subhead">AI engines up/down status mariyu call success ratios.</p>
    </section>

    <!-- Card 4: Drafts Pipeline -->
    <section class="card" aria-label="Drafts Pipeline Card">
      <div class="card-header">
        <h2 class="card-title">📝 Drafts Pipeline (Product ideas)</h2>
        <span class="card-badge">Pending: {d_pend}</span>
      </div>
      <div class="stats-row">
        <div class="stat-pill">
          <span class="pill-val" style="color: #60a5fa;">{d_made}</span>
          <span class="pill-lbl">Made</span>
        </div>
        <div class="stat-pill">
          <span class="pill-val" style="color: #34d399;">{d_appr}</span>
          <span class="pill-lbl">Approved</span>
        </div>
        <div class="stat-pill">
          <span class="pill-val" style="color: #f87171;">{d_rej}</span>
          <span class="pill-lbl">Rejected</span>
        </div>
        <div class="stat-pill">
          <span class="pill-val" style="color: #fbbf24;">{d_pend}</span>
          <span class="pill-lbl">Pending review</span>
        </div>
      </div>
      <div class="chart-container">
        {draft_chart_svg}
      </div>
      <p class="subhead">Scout drafts review cheyadaniki Telegram lo /list command vadandi.</p>
    </section>

    <!-- Card 5: Upload Packs Ready -->
    <section class="card" aria-label="Upload Packs Ready Card">
      <div class="card-header">
        <h2 class="card-title">📦 Upload Packs (Ready packs)</h2>
        <span class="card-badge">{packs_ready} ready</span>
      </div>
      <div class="metric-hero">
        <span class="big-val" style="color: #34d399;">{packs_ready}</span>
        <span class="hero-label">packs Gumroad/Topmate upload kosam full ready</span>
      </div>
      <div class="stat-pill" style="margin-top: 8px;">
        <span class="pill-val" style="font-size: 14px;">product.zip + listing.txt ✓</span>
        <span class="pill-lbl">Anni required pack assets complete ga verify ayyayi</span>
      </div>
      <p class="subhead">Telegram lo /packs kotti details chudochu. Manual upload checklist LAUNCH.md lo untundi.</p>
    </section>

    <!-- Card 6: Marketing Assets -->
    <section class="card" aria-label="Marketing Assets Card">
      <div class="card-header">
        <h2 class="card-title">🎨 Marketing Assets (Promotions)</h2>
        <span class="card-badge">Total: {m_total} files</span>
      </div>
      <div class="stats-row">
        <div class="stat-pill">
          <span class="pill-val">{m_carousels}</span>
          <span class="pill-lbl">Carousel slides</span>
        </div>
        <div class="stat-pill">
          <span class="pill-val">{m_pins}</span>
          <span class="pill-lbl">Pinterest pins</span>
        </div>
        <div class="stat-pill">
          <span class="pill-val">{m_reels}</span>
          <span class="pill-lbl">Reel frames</span>
        </div>
        <div class="stat-pill">
          <span class="pill-val">{m_blogs}</span>
          <span class="pill-lbl">Blog posts</span>
        </div>
      </div>
      <div class="chart-container">
        {mkt_chart_svg}
      </div>
      <div>
        <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 4px;">Packs breakdown:</div>
        <ul class="item-list">
          {pack_breakdown_html}
        </ul>
      </div>
      <p class="subhead">Auto-generated social assets: IG carousels, pins, reels mariyu SEO articles.</p>
    </section>

  </main>

  <footer>
    <div>Chilli Gavva Operations Dashboard — Internal Private View</div>
    <div>Generated at: {gen_at} UTC</div>
  </footer>

</body>
</html>
"""
    return html_content


def render_dashboard_to_file(
    out_path: str = DEFAULT_OUT_FILE,
    data: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
    days: int = 7,
    products_dir: Optional[str] = None,
    marketing_dir: Optional[str] = None,
) -> pathlib.Path:
    """Generate and write ops dashboard HTML atomically to out_path."""
    if data is None:
        data = ops_report.generate_ops_summary(
            db_path=db_path,
            days=days,
            products_dir=products_dir,
            marketing_dir=marketing_dir,
        )

    content = render_dashboard_html(data)

    target_path = pathlib.Path(out_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write using tempfile and os.replace in same directory
    fd, tmp_file = tempfile.mkstemp(
        dir=target_path.parent, prefix=".tmp_ops_dash_", suffix=".html"
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_file, target_path)

    return target_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Chilli Gavva operations dashboard HTML page.")
    parser.add_argument("--days", type=int, default=7, help="Number of days to summarize (default 7).")
    parser.add_argument("--db-path", default=None, help="Path to sqlite database.")
    parser.add_argument("--products-dir", default=None, help="Path to products directory.")
    parser.add_argument("--marketing-dir", default=None, help="Path to marketing directory.")
    parser.add_argument("--out-path", default=DEFAULT_OUT_FILE, help=f"Destination HTML file (default {DEFAULT_OUT_FILE}).")
    args = parser.parse_args()

    out_file = render_dashboard_to_file(
        out_path=args.out_path,
        db_path=args.db_path,
        days=args.days,
        products_dir=args.products_dir,
        marketing_dir=args.marketing_dir,
    )
    print(f"Ops dashboard successfully rendered to {out_file}")


if __name__ == "__main__":
    main()
