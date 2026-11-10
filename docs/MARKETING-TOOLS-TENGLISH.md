# Marketing Tools Guide

Ee page Chilli Gavva robot kosam ready ga unna marketing tools ni chusukondi. Prati tool ante: eedi okay chepthadu, okka command, mariyu output files emlevu.

> **Owner note:** Ee tools anni draft files chepthayi. Kani manual ga approve, post, publish cheyadam kuda kavala. Robot emainaa auto-post cheyadu.

## 1. Cover Variants

**What it makes:** Ready pack kosam 3 cover designs (PNG, 1280x720).

**One command:**

```
python3 cover_variants.py --all
```

**Output folder:** `exported_content/marketing/<pack>/covers/`

## 2. Pin Boards

**What it makes:** Pinterest board plan (keyword-first boards + pin allocation).

**One command:**

```
python3 pin_boards.py
```

**Output folder:** `exported_content/marketing/` (boards.json mariyu boards.md)

## 3. Newsletter

**What it makes:** Weekly newsletter draft (Markdown + plain text).

**One command:**

```
python3 newsletter.py
```

**Output folder:** `exported_content/marketing/newsletter/`

## 4. Shorts Script

**What it makes:** YouTube Shorts script per pack (title options, description, hashtags, frame VO).

**One command:**

```
python3 shorts_script.py --all
```

**Output folder:** `exported_content/marketing/<pack>/reel/` mariyu `exported_content/marketing/<pack>/shorts.md`

## 5. Landing Page

**What it makes:** Self-contained landing page (HTML) per pack.

**One command:**

```
python3 landing_page.py --all
```

**Output folder:** `exported_content/marketing/<pack>/landing/`

## 6. Sitemap Builder

**What it makes:** sitemap.xml, robots.txt, and landing pages index.html.

**One command:**

```
python3 sitemap_builder.py
```

**Output folder:** `exported_content/marketing/site/`

## 7. Review Request

**What it makes:** Buyer review request templates (English + Tenglish) per pack.

**One command:**

```
python3 review_request.py --all
```

**Output folder:** `exported_content/marketing/<pack>/review_request.md`

## 8. Marketing Index

**What it makes:** One browse page for all marketing files (index.html).

**One command:**

```
python3 marketing_index.py
```

**Output folder:** `exported_content/marketing/index.html`

## 9. Calendar Export

**What it makes:** Post plan ku .ics mariyu .csv files.

**One command:**

```
python3 calendar_export.py
```

**Output folder:** `exported_content/marketing/` (post_plan.ics mariyu post_plan.csv)

## 10. Catalog Sheet

**What it makes:** Printable catalog pages (A4 PNG) for ready packs.

**One command:**

```
python3 catalog_sheet.py
```

**Output folder:** `exported_content/marketing/catalog/`

## 11. Gift Guide

**What it makes:** Seasonal collections / gift guides from tags and keywords.

**One command:**

```
python3 gift_guide.py
```

**Output folder:** `exported_content/marketing/collections/`

---

**Quick reminder:** Prati tool ante output `exported_content/marketing/` ku southlo undi. Pack folders ni read-only ga treat cheyali. Money amounts ledu, brand names ledu, auto-post ledu.
