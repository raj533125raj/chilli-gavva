"""builder_signature.py — Email Signature Pack Builder (BINNY-V4C).

Builds professional, production-ready HTML email signature templates and previews:
- FORMAT = 'email_signature'
- KEYWORDS = ['email signature', 'signature template']
- 6 distinct table-based HTML email signature templates:
    1. Minimal Clean (Simple text-focused layout with subtle divider)
    2. Executive Split (Two-column layout with logo/avatar slot and details)
    3. Modern Corporate (Header accent bar, badge styling, compact contact grid)
    4. Creative Left-Border (Bold colored left border with prominent typography)
    5. Compact Horizontal (One-row horizontal card for concise threads)
    6. Friendly Photo-Card (Card-style layout with avatar placeholder and social links)
- Inline CSS only (no external stylesheets)
- Width <= 600px, compliant with Gmail and Outlook rendering engines
- Web-safe fonts (Arial, Helvetica, Trebuchet MS, Georgia, Verdana, Tahoma, sans-serif)
- Optional placeholder logo slot (using data URI or lightweight inline placeholder)
- Placeholders: [NAME], [ROLE], [PHONE], [EMAIL], [WEBSITE]
- Pillow-rendered layout preview PNGs (600x260 / 600x240, not browser screenshots)
- README-HOW-TO-INSTALL.txt in Tenglish house style ('Settings lo Signature section open cheyandi')
- Deterministic, atomic file writes
- No currency or money amounts anywhere
"""

import os
import re
import tempfile
import html
from html.parser import HTMLParser

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

FORMAT = "email_signature"
KEYWORDS = ["email signature", "signature template"]

PLACEHOLDERS = [
    "[NAME]",
    "[ROLE]",
    "[PHONE]",
    "[EMAIL]",
    "[WEBSITE]",
]

OPTIONAL_PLACEHOLDERS = [
    "[COMPANY]",
    "[ADDRESS]",
    "[DISCLAIMER]",
]

ALL_PLACEHOLDERS = PLACEHOLDERS + OPTIONAL_PLACEHOLDERS

WEB_SAFE_FONTS = [
    "Arial",
    "Helvetica",
    "Trebuchet MS",
    "Verdana",
    "Georgia",
    "Tahoma",
    "Times New Roman",
    "Courier New",
    "sans-serif",
    "serif",
    "monospace",
]

TEMPLATE_DEFS = [
    {
        "id": "template_1_minimal_clean",
        "name": "Minimal Clean",
        "filename": "signature_1_minimal_clean.html",
        "preview": "preview_1_minimal_clean.png",
        "description": "Clean, text-focused email signature with subtle divider and crisp hierarchy.",
        "primary_color": "#1f2937",
        "accent_color": "#2563eb",
        "bg_color": "#ffffff",
        "text_color": "#4b5563",
    },
    {
        "id": "template_2_executive_split",
        "name": "Executive Split",
        "filename": "signature_2_executive_split.html",
        "preview": "preview_2_executive_split.png",
        "description": "Two-column layout with logo or avatar container on left and structured contact details on right.",
        "primary_color": "#111827",
        "accent_color": "#0d9488",
        "bg_color": "#f9fafb",
        "text_color": "#374151",
    },
    {
        "id": "template_3_modern_corporate",
        "name": "Modern Corporate",
        "filename": "signature_3_modern_corporate.html",
        "preview": "preview_3_modern_corporate.png",
        "description": "Structured corporate template with accent top border, badges, and clean contact table.",
        "primary_color": "#0f172a",
        "accent_color": "#3b82f6",
        "bg_color": "#ffffff",
        "text_color": "#334155",
    },
    {
        "id": "template_4_creative_left_border",
        "name": "Creative Left Border",
        "filename": "signature_4_creative_left_border.html",
        "preview": "preview_4_creative_left_border.png",
        "description": "Bold vertical accent border on left, highlighting personal brand and contact channels.",
        "primary_color": "#18181b",
        "accent_color": "#e11d48",
        "bg_color": "#ffffff",
        "text_color": "#52525b",
    },
    {
        "id": "template_5_compact_horizontal",
        "name": "Compact Horizontal",
        "filename": "signature_5_compact_horizontal.html",
        "preview": "preview_5_compact_horizontal.png",
        "description": "Ultra-compact single/two-row signature ideal for mobile devices and high-frequency email replies.",
        "primary_color": "#1e293b",
        "accent_color": "#6366f1",
        "bg_color": "#ffffff",
        "text_color": "#64748b",
    },
    {
        "id": "template_6_friendly_card",
        "name": "Friendly Card",
        "filename": "signature_6_friendly_card.html",
        "preview": "preview_6_friendly_card.png",
        "description": "Rounded card design with soft background, avatar frame, and organized multi-row details.",
        "primary_color": "#1e1e24",
        "accent_color": "#059669",
        "bg_color": "#f8fafc",
        "text_color": "#475569",
    },
]


class HTMLTagValidator(HTMLParser):
    """Checks for balanced HTML tags, self-closing tag rules, and inline CSS restrictions."""

    SELF_CLOSING = {"img", "br", "hr", "input", "meta", "link", "col", "base"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []
        self.tags_encountered = []
        self.has_external_css = False
        self.has_script = False
        self.has_style_tag = False

    def handle_starttag(self, tag, attrs):
        self.tags_encountered.append(tag)
        attr_dict = dict(attrs)

        if tag == "link" and attr_dict.get("rel") == "stylesheet":
            self.has_external_css = True
            self.errors.append("External stylesheet found: " + str(attr_dict.get("href")))

        if tag == "script":
            self.has_script = True
            self.errors.append("Script tag is not allowed in email signature HTML")

        if tag == "style":
            self.has_style_tag = True

        if tag not in self.SELF_CLOSING:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.SELF_CLOSING:
            return
        if not self.stack:
            self.errors.append(f"Unexpected closing tag </{tag}> with empty stack")
            return
        expected = self.stack.pop()
        if expected != tag:
            self.errors.append(f"Mismatched tag: expected </{expected}>, got </{tag}>")

    def is_valid(self):
        return len(self.stack) == 0 and len(self.errors) == 0


def validate_html(html_text: str) -> tuple[bool, list[str]]:
    """Validates that HTML signature has balanced tags and no forbidden elements.

    Returns (is_valid, list_of_errors).
    """
    if not html_text or not isinstance(html_text, str):
        return False, ["HTML content is empty or not a string"]

    parser = HTMLTagValidator()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception as exc:
        return False, [f"HTML parsing exception: {exc}"]

    errors = list(parser.errors)
    if parser.stack:
        errors.append(f"Unclosed HTML tags remaining: {', '.join(parser.stack)}")

    return len(errors) == 0, errors


def check_email_signature_rules(html_text: str) -> tuple[bool, list[str]]:
    """Checks Outlook and Gmail email signature rules:

    - Width <= 600px
    - Table-based layout
    - Web-safe fonts
    - Inline CSS only (no external styles, no script)
    - All 5 canonical placeholders present or replaceable
    """
    valid, errors = validate_html(html_text)
    if not valid:
        return False, errors

    rule_errors = []

    # Check for width > 600
    width_matches = re.findall(r'width\s*[:=]\s*["\']?(\d+)', html_text, re.IGNORECASE)
    for w in width_matches:
        if int(w) > 600:
            rule_errors.append(f"Found width exceeding 600px: {w}px")

    # Table layout requirement
    if "<table" not in html_text.lower():
        rule_errors.append("Signature must use table-based layout (<table...>)")

    # Check for external stylesheets or script tags
    if re.search(r"<link[^>]+rel=[\"']?stylesheet", html_text, re.IGNORECASE):
        rule_errors.append("External stylesheet found; email signatures must use inline CSS only")
    if re.search(r"<script", html_text, re.IGNORECASE):
        rule_errors.append("Script tag found; email signatures must not contain scripts")

    # Check fonts are web-safe
    font_matches = re.findall(r'font-family\s*:\s*([^;"]+)', html_text, re.IGNORECASE)
    for font_str in font_matches:
        fonts = [f.strip().strip("'\"") for f in font_str.split(",")]
        # ensure at least one recognized web-safe font is in the stack
        has_web_safe = any(
            any(ws.lower() == f.lower() for ws in WEB_SAFE_FONTS)
            for f in fonts
        )
        if not has_web_safe:
            rule_errors.append(f"Font stack '{font_str}' does not include recognized web-safe fonts")

    return len(rule_errors) == 0, rule_errors


def generate_template_1(spec: dict | None = None) -> str:
    """Template 1: Minimal Clean

    Text-focused layout with a neat accent divider and clear hierarchy.
    """
    spec = spec or {}
    primary = spec.get("primary_color", "#1f2937")
    accent = spec.get("accent_color", "#2563eb")
    text_color = spec.get("text_color", "#4b5563")

    return f"""<!-- Template 1: Minimal Clean (Width: 520px, Web-safe Arial/Helvetica) -->
<table cellpadding="0" cellspacing="0" border="0" style="width: 520px; max-width: 520px; font-family: Arial, Helvetica, sans-serif; font-size: 13px; line-height: 18px; color: {text_color}; background-color: #ffffff;">
  <tr>
    <td style="padding: 16px 20px; border-top: 3px solid {accent}; border-bottom: 1px solid #e5e7eb;">
      <table cellpadding="0" cellspacing="0" border="0" style="width: 100%;">
        <tr>
          <td style="vertical-align: top;">
            <div style="font-size: 18px; font-weight: bold; line-height: 22px; color: {primary}; letter-spacing: 0.2px;">[NAME]</div>
            <div style="font-size: 13px; font-weight: 600; line-height: 18px; color: {accent}; margin-top: 2px;">[ROLE]</div>
          </td>
        </tr>
        <tr>
          <td style="padding-top: 10px; font-size: 12px; line-height: 18px; color: {text_color};">
            <table cellpadding="0" cellspacing="0" border="0" style="font-size: 12px; line-height: 18px; color: {text_color};">
              <tr>
                <td style="padding-right: 14px; white-space: nowrap;">
                  <strong style="color: {primary};">Phone:</strong> <a href="tel:[PHONE]" style="color: {text_color}; text-decoration: none;">[PHONE]</a>
                </td>
                <td style="padding-right: 14px; white-space: nowrap;">
                  <strong style="color: {primary};">Email:</strong> <a href="mailto:[EMAIL]" style="color: {accent}; text-decoration: none;">[EMAIL]</a>
                </td>
                <td style="white-space: nowrap;">
                  <strong style="color: {primary};">Web:</strong> <a href="https://[WEBSITE]" style="color: {accent}; text-decoration: none;" target="_blank">[WEBSITE]</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def generate_template_2(spec: dict | None = None) -> str:
    """Template 2: Executive Split

    Two-column design with left logo/avatar placeholder box and right structured contact lines.
    """
    spec = spec or {}
    primary = spec.get("primary_color", "#111827")
    accent = spec.get("accent_color", "#0d9488")
    bg_color = spec.get("bg_color", "#f9fafb")
    text_color = spec.get("text_color", "#374151")

    return f"""<!-- Template 2: Executive Split (Width: 540px, Web-safe Trebuchet MS/sans-serif) -->
<table cellpadding="0" cellspacing="0" border="0" style="width: 540px; max-width: 540px; font-family: 'Trebuchet MS', Helvetica, sans-serif; font-size: 13px; line-height: 18px; color: {text_color}; background-color: {bg_color}; border: 1px solid #e5e7eb;">
  <tr>
    <td style="width: 110px; padding: 18px 12px 18px 18px; vertical-align: middle; text-align: center; border-right: 2px solid {accent};">
      <!-- Optional Placeholder Logo Slot -->
      <table cellpadding="0" cellspacing="0" border="0" align="center" style="width: 76px; height: 76px; background-color: #ffffff; border: 2px dashed {accent}; border-radius: 4px;">
        <tr>
          <td align="center" valign="middle" style="font-family: Arial, sans-serif; font-size: 11px; font-weight: bold; color: {accent}; line-height: 14px; text-transform: uppercase;">
            LOGO /<br>PHOTO
          </td>
        </tr>
      </table>
    </td>
    <td style="padding: 16px 20px; vertical-align: top;">
      <table cellpadding="0" cellspacing="0" border="0" style="width: 100%;">
        <tr>
          <td style="padding-bottom: 6px;">
            <span style="font-size: 17px; font-weight: bold; color: {primary};">[NAME]</span>
            <span style="font-size: 12px; color: #9ca3af; padding: 0 6px;">|</span>
            <span style="font-size: 13px; font-weight: 600; color: {accent};">[ROLE]</span>
          </td>
        </tr>
        <tr>
          <td style="font-size: 12px; line-height: 20px; color: {text_color};">
            <div><strong style="color: {primary};">M:</strong> <a href="tel:[PHONE]" style="color: {text_color}; text-decoration: none;">[PHONE]</a></div>
            <div><strong style="color: {primary};">E:</strong> <a href="mailto:[EMAIL]" style="color: {accent}; text-decoration: none;">[EMAIL]</a></div>
            <div><strong style="color: {primary};">W:</strong> <a href="https://[WEBSITE]" style="color: {accent}; text-decoration: none;" target="_blank">[WEBSITE]</a></div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def generate_template_3(spec: dict | None = None) -> str:
    """Template 3: Modern Corporate

    Header accent line, boxed name pill, and organized tabular contact details.
    """
    spec = spec or {}
    primary = spec.get("primary_color", "#0f172a")
    accent = spec.get("accent_color", "#3b82f6")
    text_color = spec.get("text_color", "#334155")

    return f"""<!-- Template 3: Modern Corporate (Width: 550px, Web-safe Verdana/sans-serif) -->
<table cellpadding="0" cellspacing="0" border="0" style="width: 550px; max-width: 550px; font-family: Verdana, Geneva, sans-serif; font-size: 12px; line-height: 17px; color: {text_color}; background-color: #ffffff; border: 1px solid #cbd5e1;">
  <tr>
    <td style="height: 4px; background-color: {accent}; font-size: 1px; line-height: 1px;">&nbsp;</td>
  </tr>
  <tr>
    <td style="padding: 16px 20px;">
      <table cellpadding="0" cellspacing="0" border="0" style="width: 100%;">
        <tr>
          <td style="vertical-align: top; padding-bottom: 10px;">
            <span style="font-size: 16px; font-weight: bold; color: {primary}; letter-spacing: 0.3px;">[NAME]</span>
            <div style="font-size: 12px; font-weight: bold; color: {accent}; margin-top: 3px; text-transform: uppercase;">[ROLE]</div>
          </td>
        </tr>
        <tr>
          <td style="border-top: 1px solid #e2e8f0; padding-top: 10px;">
            <table cellpadding="0" cellspacing="0" border="0" style="font-size: 11px; line-height: 18px; color: {text_color};">
              <tr>
                <td style="padding-right: 18px; white-space: nowrap;">
                  <strong style="color: {primary};">&#9742;</strong> <a href="tel:[PHONE]" style="color: {text_color}; text-decoration: none;">[PHONE]</a>
                </td>
                <td style="padding-right: 18px; white-space: nowrap;">
                  <strong style="color: {primary};">&#9993;</strong> <a href="mailto:[EMAIL]" style="color: {accent}; text-decoration: none;">[EMAIL]</a>
                </td>
                <td style="white-space: nowrap;">
                  <strong style="color: {primary};">&#127760;</strong> <a href="https://[WEBSITE]" style="color: {accent}; text-decoration: none;" target="_blank">[WEBSITE]</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def generate_template_4(spec: dict | None = None) -> str:
    """Template 4: Creative Left-Border

    Thick colorful vertical left border and prominent typographic hierarchy.
    """
    spec = spec or {}
    primary = spec.get("primary_color", "#18181b")
    accent = spec.get("accent_color", "#e11d48")
    text_color = spec.get("text_color", "#52525b")

    return f"""<!-- Template 4: Creative Left Border (Width: 500px, Web-safe Georgia/serif) -->
<table cellpadding="0" cellspacing="0" border="0" style="width: 500px; max-width: 500px; font-family: Georgia, 'Times New Roman', serif; font-size: 13px; line-height: 19px; color: {text_color}; background-color: #ffffff;">
  <tr>
    <td style="width: 5px; background-color: {accent}; font-size: 1px; line-height: 1px;">&nbsp;</td>
    <td style="padding: 14px 18px;">
      <table cellpadding="0" cellspacing="0" border="0" style="width: 100%; font-family: Arial, Helvetica, sans-serif;">
        <tr>
          <td style="padding-bottom: 8px;">
            <div style="font-family: Georgia, serif; font-size: 19px; font-weight: bold; color: {primary};">[NAME]</div>
            <div style="font-family: Arial, sans-serif; font-size: 12px; font-weight: 600; color: {accent}; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px;">[ROLE]</div>
          </td>
        </tr>
        <tr>
          <td style="font-size: 12px; line-height: 18px; color: {text_color}; border-top: 1px dashed #d4d4d8; padding-top: 8px;">
            <div>Phone: <a href="tel:[PHONE]" style="color: {text_color}; text-decoration: none;">[PHONE]</a></div>
            <div>Email: <a href="mailto:[EMAIL]" style="color: {accent}; text-decoration: none;">[EMAIL]</a></div>
            <div>Website: <a href="https://[WEBSITE]" style="color: {accent}; text-decoration: none;" target="_blank">[WEBSITE]</a></div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def generate_template_5(spec: dict | None = None) -> str:
    """Template 5: Compact Horizontal

    Compact single-line / two-line layout designed for quick reply threads.
    """
    spec = spec or {}
    primary = spec.get("primary_color", "#1e293b")
    accent = spec.get("accent_color", "#6366f1")
    text_color = spec.get("text_color", "#64748b")

    return f"""<!-- Template 5: Compact Horizontal (Width: 560px, Web-safe Tahoma/sans-serif) -->
<table cellpadding="0" cellspacing="0" border="0" style="width: 560px; max-width: 560px; font-family: Tahoma, Verdana, sans-serif; font-size: 12px; line-height: 16px; color: {text_color}; background-color: #ffffff; border-top: 2px solid {accent}; border-bottom: 2px solid {accent};">
  <tr>
    <td style="padding: 10px 14px;">
      <table cellpadding="0" cellspacing="0" border="0" style="width: 100%;">
        <tr>
          <td style="white-space: nowrap;">
            <strong style="font-size: 14px; color: {primary};">[NAME]</strong>
            <span style="color: #cbd5e1; padding: 0 6px;">|</span>
            <span style="font-weight: 600; color: {accent};">[ROLE]</span>
          </td>
          <td align="right" style="font-size: 11px; white-space: nowrap; color: {text_color};">
            <a href="tel:[PHONE]" style="color: {text_color}; text-decoration: none;">[PHONE]</a>
            <span style="color: #cbd5e1; padding: 0 4px;">&bull;</span>
            <a href="mailto:[EMAIL]" style="color: {accent}; text-decoration: none;">[EMAIL]</a>
            <span style="color: #cbd5e1; padding: 0 4px;">&bull;</span>
            <a href="https://[WEBSITE]" style="color: {accent}; text-decoration: none;" target="_blank">[WEBSITE]</a>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


def generate_template_6(spec: dict | None = None) -> str:
    """Template 6: Friendly Card

    Soft card container with avatar slot, rounded corners, and organized contact badges.
    """
    spec = spec or {}
    primary = spec.get("primary_color", "#1e1e24")
    accent = spec.get("accent_color", "#059669")
    bg_color = spec.get("bg_color", "#f8fafc")
    text_color = spec.get("text_color", "#475569")

    return f"""<!-- Template 6: Friendly Card (Width: 520px, Web-safe Arial/sans-serif) -->
<table cellpadding="0" cellspacing="0" border="0" style="width: 520px; max-width: 520px; font-family: Arial, Helvetica, sans-serif; font-size: 12px; line-height: 17px; color: {text_color}; background-color: {bg_color}; border: 1px solid #e2e8f0; border-radius: 6px;">
  <tr>
    <td style="padding: 16px 18px;">
      <table cellpadding="0" cellspacing="0" border="0" style="width: 100%;">
        <tr>
          <td style="width: 64px; vertical-align: top; padding-right: 14px;">
            <!-- Optional Avatar / Logo Box -->
            <table cellpadding="0" cellspacing="0" border="0" style="width: 60px; height: 60px; background-color: #ffffff; border: 2px solid {accent}; border-radius: 50%;">
              <tr>
                <td align="center" valign="middle" style="font-family: Arial, sans-serif; font-size: 10px; font-weight: bold; color: {accent}; line-height: 12px;">
                  AVATAR
                </td>
              </tr>
            </table>
          </td>
          <td style="vertical-align: top;">
            <div style="font-size: 16px; font-weight: bold; color: {primary}; line-height: 20px;">[NAME]</div>
            <div style="font-size: 12px; font-weight: 600; color: {accent}; margin-top: 1px;">[ROLE]</div>
            <table cellpadding="0" cellspacing="0" border="0" style="margin-top: 8px; font-size: 11px; line-height: 16px; color: {text_color};">
              <tr>
                <td style="padding-right: 12px; white-space: nowrap;">
                  Tel: <a href="tel:[PHONE]" style="color: {text_color}; text-decoration: none;">[PHONE]</a>
                </td>
                <td style="padding-right: 12px; white-space: nowrap;">
                  Email: <a href="mailto:[EMAIL]" style="color: {accent}; text-decoration: none;">[EMAIL]</a>
                </td>
                <td style="white-space: nowrap;">
                  Web: <a href="https://[WEBSITE]" style="color: {accent}; text-decoration: none;" target="_blank">[WEBSITE]</a>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>"""


TEMPLATE_GENERATORS = [
    generate_template_1,
    generate_template_2,
    generate_template_3,
    generate_template_4,
    generate_template_5,
    generate_template_6,
]


def render_template_html(index: int, spec: dict | None = None) -> str:
    """Renders the HTML content for template index (0 to 5)."""
    if not (0 <= index < len(TEMPLATE_GENERATORS)):
        raise IndexError(f"Template index out of range: {index}")
    return TEMPLATE_GENERATORS[index](spec)


def get_readme_content() -> str:
    """Returns the content for README-HOW-TO-INSTALL.txt in Tenglish house style."""
    return """EMAIL SIGNATURE TEMPLATES — HOW TO INSTALL GUIDE
=================================================
Mee email signature templates ni Gmail, Outlook, mariyu Apple Mail lo setup cheyadaniki kindi steps follow avvandi.

HOUSE STYLE & RULES:
- Anni templates pure table-based HTML mariyu inline CSS tho tayaru cheyabaddayi.
- Max width <= 600px undatam valla desktop mariyu mobile lo layout break avvadu.
- Placeholders replace cheyandi:
    [NAME]    -> Mee full name
    [ROLE]    -> Mee job title / designation
    [PHONE]   -> Mee phone number
    [EMAIL]   -> Mee email address
    [WEBSITE] -> Mee website URL

-------------------------------------------------
1. GMAIL INSTALLATION (WEB BROWSER)
-------------------------------------------------
Step 1: HTML file ni meeku istamaina browser (Chrome, Edge, Firefox) lo open cheyandi.
Step 2: Signature design mothanni select chesi copy cheyandi (Ctrl+A, tarvata Ctrl+C; Mac lo Cmd+A, Cmd+C).
Step 3: Gmail open chesi, top-right lo unna Gear icon (Settings) click cheyandi.
Step 4: 'See all settings' click chesi, 'General' tab lo undandi.
Step 5: Settings lo Signature section open cheyandi.
Step 6: '+ Create new' click chesi mee signature ki name ivvandi.
Step 7: Signature text box lo copy chesina template ni paste cheyandi (Ctrl+V leda Cmd+V).
Step 8: Placeholders [NAME], [ROLE], [PHONE], [EMAIL], [WEBSITE] ni mee details tho replace cheyandi.
Step 9: Page bottom lo 'Save Changes' button click cheyandi.

-------------------------------------------------
2. MICROSOFT OUTLOOK INSTALLATION (WINDOWS / MAC / WEB)
-------------------------------------------------
Step 1: HTML file browser lo open chesi design copy cheyandi.
Step 2: Outlook open chesi, Settings (Gear icon) leda 'File > Options > Mail' loki vellandi.
Step 3: Settings lo Signature section open cheyandi.
Step 4: 'New Signature' click chesi name enter cheyandi.
Step 5: Edit box lo template paste chesi placeholders edit cheyandi.
Step 6: 'Save' click chesi, default signature ga select cheyandi.

-------------------------------------------------
3. APPLE MAIL INSTALLATION (MACOS / IOS)
-------------------------------------------------
Step 1: HTML file browser lo open chesi copy cheyandi.
Step 2: Apple Mail open chesi 'Mail > Settings' (leda Preferences) open cheyandi.
Step 3: Settings lo Signature section open cheyandi.
Step 4: Mee account select chesi '+' button click cheyandi.
Step 5: 'Always match my default message font' uncheck cheyandi.
Step 6: Signature box lo paste chesi mee personal details fill cheyandi.

-------------------------------------------------
NOTE:
- External images avasaram ledu. Placeholder logo box unte mee logo URL add chesukovachhu.
- Edaina help kavali ante templates README chudandi.
"""


def _get_font(size: int, bold: bool = False):
    """Loads Inter font from repo if available, else PIL default font."""
    if not HAS_PILLOW:
        return None
    base_dir = os.path.dirname(os.path.abspath(__file__))
    font_name = "Inter-Bold.ttf" if bold else "Inter-Regular.ttf"
    font_path = os.path.join(base_dir, "fonts", font_name)
    if os.path.isfile(font_path):
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def render_preview_png(template_index: int, out_path: str, spec: dict | None = None) -> str:
    """Draws a visual layout preview PNG for a signature template using Pillow.

    (Not a browser screenshot; pure Pillow canvas drawing).
    """
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is not available to render preview PNG")

    spec = spec or {}
    tdef = TEMPLATE_DEFS[template_index]
    width, height = 600, 240

    img = Image.new("RGB", (width, height), color="#f1f5f9")
    draw = ImageDraw.Draw(img)

    # Card background
    card_x0, card_y0 = 20, 20
    card_x1, card_y1 = width - 20, height - 20
    draw.rectangle([card_x0, card_y0, card_x1, card_y1], fill=tdef["bg_color"], outline="#cbd5e1", width=1)

    primary_hex = spec.get("primary_color", tdef["primary_color"])
    accent_hex = spec.get("accent_color", tdef["accent_color"])
    text_hex = spec.get("text_color", tdef["text_color"])

    font_title = _get_font(18, bold=True)
    font_sub = _get_font(13, bold=True)
    font_body = _get_font(12, bold=False)
    font_small = _get_font(10, bold=False)

    if template_index == 0:  # Minimal Clean
        # Top accent bar
        draw.rectangle([card_x0, card_y0, card_x1, card_y0 + 4], fill=accent_hex)
        draw.text((card_x0 + 20, card_y0 + 24), "[NAME]  Alex Morgan", fill=primary_hex, font=font_title)
        draw.text((card_x0 + 20, card_y0 + 52), "[ROLE]  Product Operations Specialist", fill=accent_hex, font=font_sub)
        draw.line([card_x0 + 20, card_y0 + 82, card_x1 - 20, card_y0 + 82], fill="#e2e8f0", width=1)
        draw.text((card_x0 + 20, card_y0 + 96), "Phone: [PHONE]  +1 (555) 019-2834", fill=text_hex, font=font_body)
        draw.text((card_x0 + 20, card_y0 + 120), "Email: [EMAIL]  alex.morgan@example.com", fill=accent_hex, font=font_body)
        draw.text((card_x0 + 20, card_y0 + 144), "Web: [WEBSITE]  www.companybrand.example", fill=accent_hex, font=font_body)

    elif template_index == 1:  # Executive Split
        # Left logo container
        logo_box = [card_x0 + 20, card_y0 + 35, card_x0 + 105, card_y0 + 120]
        draw.rectangle(logo_box, fill="#ffffff", outline=accent_hex, width=2)
        draw.text((card_x0 + 32, card_y0 + 60), "LOGO /\nPHOTO", fill=accent_hex, font=font_small)
        # Vertical divider
        draw.line([card_x0 + 125, card_y0 + 25, card_x0 + 125, card_y1 - 25], fill=accent_hex, width=2)
        # Right details
        draw.text((card_x0 + 145, card_y0 + 30), "[NAME]  Sarah Jenkins", fill=primary_hex, font=font_title)
        draw.text((card_x0 + 145, card_y0 + 58), "[ROLE]  Managing Director", fill=accent_hex, font=font_sub)
        draw.text((card_x0 + 145, card_y0 + 95), "M: [PHONE]  +1 555-0144", fill=text_hex, font=font_body)
        draw.text((card_x0 + 145, card_y0 + 118), "E: [EMAIL]  sarah@example.com", fill=accent_hex, font=font_body)
        draw.text((card_x0 + 145, card_y0 + 141), "W: [WEBSITE]  executive.example", fill=accent_hex, font=font_body)

    elif template_index == 2:  # Modern Corporate
        # Header bar
        draw.rectangle([card_x0, card_y0, card_x1, card_y0 + 6], fill=accent_hex)
        draw.text((card_x0 + 24, card_y0 + 26), "[NAME]  David Chen", fill=primary_hex, font=font_title)
        draw.text((card_x0 + 24, card_y0 + 52), "[ROLE]  SENIOR ENTERPRISE ARCHITECT", fill=accent_hex, font=font_sub)
        draw.line([card_x0 + 24, card_y0 + 82, card_x1 - 24, card_y0 + 82], fill="#cbd5e1", width=1)
        draw.text((card_x0 + 24, card_y0 + 98), "Tel: [PHONE]  (555) 782-9012", fill=text_hex, font=font_body)
        draw.text((card_x0 + 24, card_y0 + 122), "Email: [EMAIL]  dchen@example.com", fill=accent_hex, font=font_body)
        draw.text((card_x0 + 24, card_y0 + 146), "Web: [WEBSITE]  www.enterprise.example", fill=accent_hex, font=font_body)

    elif template_index == 3:  # Creative Left Border
        # Thick left accent border
        draw.rectangle([card_x0, card_y0, card_x0 + 8, card_y1], fill=accent_hex)
        draw.text((card_x0 + 28, card_y0 + 26), "[NAME]  Maya Lin", fill=primary_hex, font=font_title)
        draw.text((card_x0 + 28, card_y0 + 54), "[ROLE]  CREATIVE DIRECTOR", fill=accent_hex, font=font_sub)
        draw.line([card_x0 + 28, card_y0 + 84, card_x1 - 24, card_y0 + 84], fill="#e4e4e7", width=1)
        draw.text((card_x0 + 28, card_y0 + 100), "Phone: [PHONE]  +1 555-8900", fill=text_hex, font=font_body)
        draw.text((card_x0 + 28, card_y0 + 124), "Email: [EMAIL]  maya@example.com", fill=accent_hex, font=font_body)
        draw.text((card_x0 + 28, card_y0 + 148), "Website: [WEBSITE]  creativestudio.example", fill=accent_hex, font=font_body)

    elif template_index == 4:  # Compact Horizontal
        draw.rectangle([card_x0, card_y0, card_x1, card_y0 + 3], fill=accent_hex)
        draw.rectangle([card_x0, card_y1 - 3, card_x1, card_y1], fill=accent_hex)
        draw.text((card_x0 + 16, card_y0 + 36), "[NAME]  Jordan Vance", fill=primary_hex, font=font_title)
        draw.text((card_x0 + 16, card_y0 + 64), "[ROLE]  Support Lead", fill=accent_hex, font=font_sub)
        draw.line([card_x0 + 16, card_y0 + 94, card_x1 - 16, card_y0 + 94], fill="#e2e8f0", width=1)
        row_str = "T: [PHONE] +1-555-0100   |   E: [EMAIL] jv@example.com   |   W: [WEBSITE] brand.example"
        draw.text((card_x0 + 16, card_y0 + 115), row_str, fill=text_hex, font=font_body)

    elif template_index == 5:  # Friendly Card
        # Avatar circle
        draw.ellipse([card_x0 + 22, card_y0 + 36, card_x0 + 86, card_y0 + 100], fill="#ffffff", outline=accent_hex, width=2)
        draw.text((card_x0 + 32, card_y0 + 60), "AVATAR", fill=accent_hex, font=font_small)
        # Content on right
        draw.text((card_x0 + 106, card_y0 + 32), "[NAME]  Taylor Reed", fill=primary_hex, font=font_title)
        draw.text((card_x0 + 106, card_y0 + 58), "[ROLE]  Community Manager", fill=accent_hex, font=font_sub)
        draw.text((card_x0 + 106, card_y0 + 92), "Tel: [PHONE]  (555) 234-5678", fill=text_hex, font=font_body)
        draw.text((card_x0 + 106, card_y0 + 114), "Email: [EMAIL]  taylor@example.com", fill=accent_hex, font=font_body)
        draw.text((card_x0 + 106, card_y0 + 136), "Web: [WEBSITE]  community.example", fill=accent_hex, font=font_body)

    # Atomic write of the image
    dir_name = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(dir_name, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="tmp_img_", suffix=".png")
    os.close(temp_fd)
    img.save(temp_path, format="PNG")
    os.replace(temp_path, out_path)
    return out_path


def substitute_placeholders(html_text: str, mapping: dict[str, str]) -> str:
    """Safely substitutes placeholders in HTML content while escaping user values."""
    res = html_text
    for placeholder, val in mapping.items():
        if placeholder in res:
            res = res.replace(placeholder, html.escape(str(val)))
    return res


def atomic_write(filepath: str, content: str) -> None:
    """Writes a text file atomically using a temp file and replace."""
    dir_name = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(dir_name, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="tmp_sig_", suffix=".tmp")
    with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(temp_path, filepath)


def build(spec: dict | None = None, out_dir: str | None = None) -> dict:
    """Builds the Email Signature pack:

    - 6 HTML email signature templates
    - 6 preview PNGs
    - README-HOW-TO-INSTALL.txt
    Writes atomically to out_dir and returns build manifest.
    """
    spec = spec or {}
    if not out_dir:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exported_content", "signatures")

    os.makedirs(out_dir, exist_ok=True)

    manifest = {
        "format": FORMAT,
        "keywords": list(KEYWORDS),
        "templates": [],
        "previews": [],
        "readme": "README-HOW-TO-INSTALL.txt",
        "placeholders": list(PLACEHOLDERS),
        "out_dir": out_dir,
    }

    # 1. Write the 6 HTML templates
    for i, tdef in enumerate(TEMPLATE_DEFS):
        html_content = render_template_html(i, spec)
        html_path = os.path.join(out_dir, tdef["filename"])
        atomic_write(html_path, html_content)
        manifest["templates"].append({
            "id": tdef["id"],
            "name": tdef["name"],
            "filename": tdef["filename"],
            "path": html_path,
        })

    # 2. Write the 6 preview PNGs
    if HAS_PILLOW:
        for i, tdef in enumerate(TEMPLATE_DEFS):
            png_path = os.path.join(out_dir, tdef["preview"])
            render_preview_png(i, png_path, spec)
            manifest["previews"].append({
                "id": tdef["id"],
                "filename": tdef["preview"],
                "path": png_path,
            })

    # 3. Write README-HOW-TO-INSTALL.txt
    readme_path = os.path.join(out_dir, "README-HOW-TO-INSTALL.txt")
    atomic_write(readme_path, get_readme_content())
    manifest["readme_path"] = readme_path

    return manifest
