# Pinterest Pin Variants & A/B Title Ideas

This document describes the automated Pinterest pin generation system for upload packs implemented in `pin_variants.py`.

## Overview

For each upload pack, the generator creates 5 distinct 1000x1500 px PNG pins and writes them to:
```
exported_content/marketing/<pack>/pins/
```
The generator strictly treats the upload pack folder as **read-only** and performs all writes outside of the pack directory using atomic writes.

Along with the 5 pin images, two metadata files are generated:
- `ab_titles.txt`: Contains the 5 title variants for human review and manual A/B testing (no automated posting).
- `pins.json`: Structured metadata array with `{file, layout, title, description, alt}` for each pin.

---

## 5 Pin Layouts

1. **Layout A: Big Title Top + Tilted Page**
   - Dominant headline text at the top with category kicker.
   - Realistic page preview tilted with drop shadow.
   - Clean footer displaying the pack name.

2. **Layout B: Split Screen Two Pages**
   - Two-tone split screen background.
   - Dual page previews side-by-side with subtle alternating angles.
   - Balanced headline and category tagline.

3. **Layout C: Checklist Style (3 Benefit Ticks)**
   - Bold headline paired with 3 prominent benefit checkmark bullet points.
   - Clear visual value proposition above a centered preview page.

4. **Layout D: "Free Printable" Badge Style**
   - High-contrast "FREE PRINTABLE" pill badge at top.
   - Prominent headline and download subline.
   - **Condition:** Rendered **only** when the pack listing explicitly indicates the product is free (e.g. `Free`, `freebie`, price zero). If the product is not free, Layout D is skipped and Layout E is used instead.

5. **Layout E: Minimal Photo-Frame**
   - Clean, gallery-style inner photo frame border.
   - Elegant, centered typography.
   - Showcases the product in a minimalist aesthetic.

---

## A/B Title Variations

For each pack, 5 distinct title variants are systematically generated from the pack title:

1. **Benefit-first**: Emphasizes the positive outcome (e.g., `Stay Organized With Daily Planner`).
2. **Number-first**: Numerical hook (e.g., `5 Steps To Master Daily Planner`).
3. **Question**: Engaging query (e.g., `Need A Better Daily Planner?`).
4. **"How To"**: Practical actionable phrasing (e.g., `How To Use A Daily Planner`).
5. **Short**: Concise and punchy title under 40 characters (e.g., `Daily Planner`).

### Title Constraints & Compliance
- **Maximum Length:** <= 60 characters.
- **Uniqueness:** All 5 titles generated for a pack are unique.
- **No Clickbait:** Disallowed words like `viral`, `shocking`, `insane`, `miracle`, `secret`, `magic` are stripped.
- **No Currency / Money:** No currency symbols or pricing phrases (e.g., `$`, `USD`, `INR`, `EUR`, `dollars`).
- **No Disallowed Brand Names:** No mentions of third-party platforms (e.g., `Notion`, `Excel`, `Canva`, `GoodNotes`, `Google Sheets`).

---

## Technical & Quality Standards

- **Pillow Only:** All image manipulation and rendering is performed exclusively with Pillow.
- **Strict Size:** Exactly 1000 x 1500 pixels.
- **File Size:** Optimized PNG compression ensuring every pin is under 1 MB.
- **Safe Margin:** Minimum 60 px safe margin from all edges.
- **Text Wrapping & Shrink:** Titles and body text dynamically wrap and scale down to guarantee no text is ever clipped.
- **Contrast Ratio:** All foreground text and graphical elements maintain WCAG AA contrast ratio >= 4.5:1 against backgrounds.
- **Deterministic:** Generating pins from identical inputs yields byte-identical output.
- **Missing Asset Fallbacks:** If no cover or preview images exist in the pack, a crisp synthetic document template is automatically rendered.
