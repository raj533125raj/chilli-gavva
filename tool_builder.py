"""Builds interactive mini-tool pages from approved tool specs.

Income stream A: simple self-contained web tools that help creators/writers
and attract organic search traffic. No ads yet — just useful free tools.

A "tool spec" is a JSON blob stored in the approvals table (kind='tool_draft').
Once you approve a spec, tool_builder generates a zero-dependency HTML+JS page
in site/tools/<slug>/index.html. You upload site/ yourself; nothing auto-publishes.

Spec format (stored as approval payload JSON):
{
  "title": "Word Counter",
  "description": "Count words, characters and sentences in any text",
  "category": "writing",
  "inputs": [
    {"id": "text", "type": "textarea", "label": "Paste your text",
     "placeholder": "Type or paste here..."}
  ],
  "outputs": [
    {"id": "words",  "label": "Words",       "formula": "text.trim().split(/\\\\s+/).filter(Boolean).length"},
    {"id": "chars",  "label": "Characters",  "formula": "text.length"},
    {"id": "sentences","label":"Sentences",  "formula": "text.split(/[.!?]+/).filter(s=>s.trim()).length"}
  ]
}

Input types supported: textarea, text, number, range, select
Output types: number (default), text, percentage

CLI:
  python3 tool_builder.py                   # build all approved unbuilt tools
  python3 tool_builder.py list              # list approved tool specs
  python3 tool_builder.py spec <title>      # print a sample spec
"""
import datetime
import html
import json
import os
import re
import sqlite3
import sys

import guardrails

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SITE_DIR  = os.path.join(BASE_DIR, "site", "tools")

# ── HTML template ──────────────────────────────────────────────────────────────

TOOL_PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box}}
body{{font:17px/1.65 system-ui,sans-serif;max-width:38rem;margin:2.5rem auto;
     padding:0 1.2rem;color:#1b1b1f;background:#fff}}
h1{{font-size:1.55rem;line-height:1.25;margin-bottom:.4rem}}
.desc{{color:#555;margin-bottom:1.6rem}}
label{{display:block;font-weight:600;margin-bottom:.3rem}}
textarea,input[type=text],input[type=number]{{
  width:100%;border:1.5px solid #ccc;border-radius:6px;padding:.6rem .8rem;
  font:inherit;resize:vertical}}
input[type=range]{{width:100%;margin:.4rem 0}}
select{{width:100%;border:1.5px solid #ccc;border-radius:6px;
        padding:.55rem .8rem;font:inherit;background:#fff}}
.field{{margin-bottom:1.2rem}}
.results{{margin-top:1.6rem;display:grid;
          grid-template-columns:repeat(auto-fill,minmax(9rem,1fr));gap:.8rem}}
.stat{{background:#f4f6fa;border-radius:8px;padding:.8rem 1rem;text-align:center}}
.stat-val{{font-size:1.8rem;font-weight:700;color:#1a56db;line-height:1.2}}
.stat-lbl{{font-size:.8rem;color:#555;margin-top:.25rem}}
.range-val{{font-weight:700;color:#1a56db}}
footer{{margin-top:3rem;font-size:.8rem;color:#777;border-top:1px solid #eee;padding-top:1rem}}
@media(prefers-color-scheme:dark){{
  body{{background:#111;color:#ddd}}
  textarea,input,select{{background:#1e1e1e;border-color:#444;color:#ddd}}
  .stat{{background:#1e1e1e}}
  .desc{{color:#999}}footer{{color:#666;border-color:#333}}
}}
</style>
</head>
<body>
<h1>{title}</h1>
<p class="desc">{description}</p>
<div id="form">{form_html}</div>
<div class="results" id="results">{results_html}</div>
<footer>Free tool · no data stored · no ads · {year}</footer>
<script>
(function(){{
  var inputs = document.querySelectorAll('#form textarea, #form input, #form select');
  function calc(){{
    {input_vars}
    {output_calcs}
  }}
  inputs.forEach(function(el){{el.addEventListener('input',calc)}});
  calc();
}})();
</script>
</body></html>
"""

# ── helpers ────────────────────────────────────────────────────────────────────

def _h(s):
    return html.escape(str(s), quote=True)

def slugify(text, fallback="tool"):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]
    return slug or fallback

def _build_form(inputs):
    parts = []
    for inp in inputs:
        iid   = _h(inp["id"])
        label = _h(inp.get("label", inp["id"]))
        ph    = _h(inp.get("placeholder", ""))
        itype = inp.get("type", "textarea")
        extra = ""
        if itype == "textarea":
            extra = f'<textarea id="{iid}" rows="6" placeholder="{ph}"></textarea>'
        elif itype == "range":
            mn = inp.get("min", 0); mx = inp.get("max", 100); step = inp.get("step", 1)
            default = inp.get("default", mn)
            extra = (f'<input type="range" id="{iid}" min="{mn}" max="{mx}" '
                     f'step="{step}" value="{default}">'
                     f'<span class="range-val" id="{iid}-val">{default}</span>')
        elif itype == "select":
            opts = "".join(f'<option value="{_h(o)}">{_h(o)}</option>'
                           for o in inp.get("options", []))
            extra = f'<select id="{iid}">{opts}</select>'
        else:
            extra = f'<input type="{_h(itype)}" id="{iid}" placeholder="{ph}">'
        parts.append(f'<div class="field"><label for="{iid}">{label}</label>{extra}</div>')
    return "\n".join(parts)

def _build_results(outputs):
    parts = []
    for out in outputs:
        oid   = _h(out["id"])
        label = _h(out.get("label", out["id"]))
        parts.append(
            f'<div class="stat"><div class="stat-val" id="out-{oid}">—</div>'
            f'<div class="stat-lbl">{label}</div></div>'
        )
    return "\n".join(parts)

def _build_js(inputs, outputs):
    # input variable extraction
    var_lines = []
    for inp in inputs:
        iid   = inp["id"]
        itype = inp.get("type", "textarea")
        if itype == "range":
            var_lines.append(
                f'var {iid} = parseFloat(document.getElementById("{iid}").value)||0;'
                f'document.getElementById("{iid}-val").textContent={iid};'
            )
        elif itype == "number":
            var_lines.append(
                f'var {iid} = parseFloat(document.getElementById("{iid}").value)||0;'
            )
        else:
            var_lines.append(
                f'var {iid} = document.getElementById("{iid}").value;'
            )

    # output calculation — formula is trusted JS from the spec author (us)
    calc_lines = []
    for out in outputs:
        oid     = out["id"]
        formula = out.get("formula", "0")
        fmt     = out.get("format", "number")
        if fmt == "percentage":
            calc_lines.append(
                f'try{{var _v{oid}={formula};'
                f'document.getElementById("out-{oid}").textContent='
                f'(+_v{oid}).toFixed(1)+"%";}}catch(e){{}}'
            )
        elif fmt == "text":
            calc_lines.append(
                f'try{{document.getElementById("out-{oid}").textContent={formula};}}catch(e){{}}'
            )
        else:
            calc_lines.append(
                f'try{{var _v{oid}={formula};'
                f'document.getElementById("out-{oid}").textContent='
                f'(Math.round(_v{oid}*100)/100).toLocaleString();}}catch(e){{}}'
            )
    return "\n    ".join(var_lines), "\n    ".join(calc_lines)


def render_tool(spec):
    """Build a self-contained HTML string from a validated tool spec dict."""
    inputs  = spec.get("inputs", [])
    outputs = spec.get("outputs", [])
    input_vars, output_calcs = _build_js(inputs, outputs)
    return TOOL_PAGE.format(
        title=_h(spec["title"]),
        description=_h(spec.get("description", "")),
        form_html=_build_form(inputs),
        results_html=_build_results(outputs),
        input_vars=input_vars,
        output_calcs=output_calcs,
        year=datetime.datetime.now().year,
    )


# ── validation ─────────────────────────────────────────────────────────────────

REQUIRED_KEYS = {"title", "inputs", "outputs"}
ALLOWED_INPUT_TYPES = {"textarea", "text", "number", "range", "select"}
MAX_FORMULA_LEN = 400

def validate_spec(spec):
    """Return list of error strings; empty list = valid."""
    errs = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]
    for k in REQUIRED_KEYS:
        if k not in spec:
            errs.append(f"missing required key: {k}")
    title = spec.get("title", "")
    if not title or len(title) > 120:
        errs.append("title must be 1-120 chars")
    desc = spec.get("description", "")
    if len(desc) > 500:
        errs.append("description max 500 chars")
    inputs = spec.get("inputs", [])
    if not isinstance(inputs, list) or len(inputs) == 0:
        errs.append("inputs must be a non-empty list")
    else:
        ids = set()
        for i, inp in enumerate(inputs):
            if "id" not in inp:
                errs.append(f"input[{i}] missing id")
            elif inp["id"] in ids:
                errs.append(f"duplicate input id: {inp['id']}")
            else:
                ids.add(inp["id"])
            itype = inp.get("type", "textarea")
            if itype not in ALLOWED_INPUT_TYPES:
                errs.append(f"input[{i}] unknown type: {itype}")
    outputs = spec.get("outputs", [])
    if not isinstance(outputs, list) or len(outputs) == 0:
        errs.append("outputs must be a non-empty list")
    else:
        oids = set()
        for i, out in enumerate(outputs):
            if "id" not in out:
                errs.append(f"output[{i}] missing id")
            elif out["id"] in oids:
                errs.append(f"duplicate output id: {out['id']}")
            else:
                oids.add(out["id"])
            formula = out.get("formula", "")
            if not formula:
                errs.append(f"output[{i}] missing formula")
            if len(formula) > MAX_FORMULA_LEN:
                errs.append(f"output[{i}] formula too long (max {MAX_FORMULA_LEN})")
    return errs


# ── database ───────────────────────────────────────────────────────────────────

def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS tool_pages (
        approval_id INTEGER PRIMARY KEY,
        slug TEXT,
        title TEXT,
        built_ts TEXT
    )""")
    conn.commit()
    return conn


def submit_spec(spec, db_path=None):
    """Validate spec and queue it for human approval.
    Returns (approval_id, errors). If errors is non-empty, nothing was queued."""
    errs = validate_spec(spec)
    if errs:
        return None, errs
    aid = guardrails.request_approval(
        kind="tool_draft",
        title=spec["title"],
        payload=json.dumps(spec, ensure_ascii=False),
        db_path=db_path,
    )
    return aid, []


def build(db_path=None, site_dir=None):
    """Build HTML pages for all approved unbuilt tool specs. Returns list of slugs."""
    site_dir = site_dir or SITE_DIR
    os.makedirs(site_dir, exist_ok=True)
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT a.id, a.title, a.payload FROM approvals a "
        "WHERE a.kind = 'tool_draft' AND a.status = 'approved' "
        "AND a.id NOT IN (SELECT approval_id FROM tool_pages) ORDER BY a.id"
    ).fetchall()
    built = []
    for approval_id, title, payload in rows:
        try:
            spec = json.loads(payload)
        except json.JSONDecodeError:
            continue
        errs = validate_spec(spec)
        if errs:
            continue
        html_out = render_tool(spec)
        slug = slugify(spec["title"], f"tool-{approval_id}")
        folder = os.path.join(site_dir, slug)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
            f.write(html_out)
        conn.execute(
            "INSERT INTO tool_pages (approval_id, slug, title, built_ts) VALUES (?, ?, ?, ?)",
            (approval_id, slug, spec["title"], datetime.datetime.now().isoformat(timespec="seconds")),
        )
        built.append(slug)
    conn.commit()

    # Rebuild tools index
    pages = conn.execute("SELECT slug, title FROM tool_pages ORDER BY approval_id").fetchall()
    conn.close()
    if pages:
        items = "\n".join(
            f'<li><a href="{_h(s)}/">{_h(t)}</a></li>' for s, t in pages
        )
        idx_html = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Free Tools</title>'
            '<style>body{font:17px/1.65 system-ui,sans-serif;max-width:38rem;'
            'margin:2.5rem auto;padding:0 1.2rem;color:#1b1b1f}</style></head>'
            '<body><h1>Free Tools</h1>'
            f'<ul>{items}</ul>'
            '<footer style="margin-top:3rem;font-size:.8rem;color:#777">'
            'All tools run in your browser. No data is sent anywhere.</footer>'
            '</body></html>'
        )
        with open(os.path.join(site_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(idx_html)
    return built


# ── sample specs (for the agent to use as starting point) ──────────────────────

SAMPLE_SPECS = {
    "word-counter": {
        "title": "Word Counter",
        "description": "Count words, characters, and sentences in any text. Free and instant.",
        "category": "writing",
        "inputs": [
            {"id": "text", "type": "textarea", "label": "Paste your text",
             "placeholder": "Type or paste your content here..."}
        ],
        "outputs": [
            {"id": "words",    "label": "Words",            "formula": "text.trim().split(/\\s+/).filter(Boolean).length"},
            {"id": "chars",    "label": "Characters",       "formula": "text.length"},
            {"id": "nospace",  "label": "Chars (no spaces)","formula": "text.replace(/\\s/g,'').length"},
            {"id": "sentences","label": "Sentences",        "formula": "text.split(/[.!?]+/).filter(s=>s.trim()).length"},
            {"id": "paras",    "label": "Paragraphs",       "formula": "text.split(/\\n\\s*\\n/).filter(p=>p.trim()).length"},
        ]
    },
    "reading-time": {
        "title": "Reading Time Calculator",
        "description": "Estimate how long it takes to read your article or blog post.",
        "category": "writing",
        "inputs": [
            {"id": "text", "type": "textarea", "label": "Paste your article",
             "placeholder": "Paste your full article text..."},
            {"id": "wpm", "type": "range", "label": "Reading speed (words per minute)",
             "min": 100, "max": 400, "step": 10, "default": 230}
        ],
        "outputs": [
            {"id": "minutes", "label": "Minutes to read", "formula": "(text.trim().split(/\\s+/).filter(Boolean).length/wpm).toFixed(1)"},
            {"id": "words",   "label": "Total words",     "formula": "text.trim().split(/\\s+/).filter(Boolean).length"},
        ]
    },
    "hashtag-counter": {
        "title": "Hashtag Counter",
        "description": "Count and extract hashtags from your social media post.",
        "category": "social",
        "inputs": [
            {"id": "text", "type": "textarea", "label": "Paste your post",
             "placeholder": "#hashtag example post..."}
        ],
        "outputs": [
            {"id": "count",    "label": "Hashtags",      "formula": "(text.match(/#[\\w]+/g)||[]).length"},
            {"id": "chars",    "label": "Characters",    "formula": "text.length"},
            {"id": "pct",      "label": "Hashtag %",     "formula": "text.length?(((text.match(/#[\\w]+/g)||[]).join('').length/text.length)*100).toFixed(1):0", "format": "percentage"},
        ]
    },
}


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv):
    if not argv or argv[0] == "build":
        out = build()
        if out:
            print(f"Built {len(out)} tool(s): {', '.join(out)}")
            print("Upload site/tools/ yourself. Nothing was published.")
        else:
            print("No new tools to build. (Approve a tool_draft in the approval queue first.)")
        return 0

    if argv[0] == "list":
        import guardrails as gr
        conn = gr.connect()
        rows = conn.execute(
            "SELECT id, title, status, ts FROM approvals WHERE kind = 'tool_draft' ORDER BY id DESC"
        ).fetchall()
        conn.close()
        if not rows:
            print("No tool specs queued yet.")
            return 0
        for rid, title, status, ts in rows:
            print(f"  #{rid}  [{status:8}]  {title}  ({ts[:10]})")
        return 0

    if argv[0] == "spec" and len(argv) >= 2:
        name = argv[1].lower().replace(" ", "-")
        spec = SAMPLE_SPECS.get(name)
        if not spec:
            print(f"Available: {', '.join(SAMPLE_SPECS)}")
            return 1
        print(json.dumps(spec, indent=2, ensure_ascii=False))
        return 0

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
