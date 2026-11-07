# Instagram Carousel Maker

`carousel_maker.py` generates ready-to-post, 6-slide Instagram carousels (1080x1350 PNG) for ready upload packs. All output files are written outside the pack to `exported_content/marketing/<pack>/carousel/`.

The owner reviews the generated slides and posts them manually by hand on Instagram.

## Overview

For every upload pack located in `exported_content/products/upload/<pack>/`, `carousel_maker.py` creates:
- `slide-1.png`: Hook slide (pack title, large typography, max 2 lines, overview).
- `slide-2.png` to `slide-5.png`: Feature & benefit highlights from "What you get" with cropped/fitted preview images (never stretched; falls back to cover image if preview is missing).
- `slide-6.png`: Call to action ("Link in bio" + pack short name).
- `caption.txt`: Clean Instagram caption (<= 2,000 characters, no brand names, no money amounts, with 8 curated hashtags).
- `alt.txt`: Accessibility alt text for all 6 slides (one line per slide).

## Design Rules

1. **Dimensions**: Exactly 1080x1350 px (Instagram standard 4:5 vertical carousel portrait format).
2. **Safe Margins**: 80 px padding on all sides ensuring no text or critical elements are occluded by Instagram UI controls.
3. **Typography**:
   - Uses Inter font from the repository (`fonts/Inter-*.ttf`), accessed via PIL.
   - Text never cut or truncated abruptly: auto-shrinks down to minimum font size and wraps cleanly at word boundaries.
   - Title blocks constrained to at most 2 lines; benefit text blocks constrained to at most 3 lines.
4. **Color Themes & Accessibility**:
   - 2 complementary color themes extracted from the pack's `cover.png`.
   - Contrast ratios computed using WCAG 2.1 relative luminance formulas (`(L1 + 0.05) / (L2 + 0.05)`).
   - Strict adherence to contrast ratio >= 4.5:1 for all text and accent elements against slide backgrounds.
5. **Asset Fitting**:
   - Previews and cover images are fitted using aspect-ratio-preserving center crops.
   - Images are never stretched or distorted.
6. **File Size**:
   - Each slide PNG is optimized and kept under 1.5 MB.
7. **Purity & Safety**:
   - Pillow-only image generation (no heavy or undeclared dependencies).
   - No brand names of third-party platforms.
   - No money amounts, prices, or currency figures.
8. **Idempotence & Atomic Writes**:
   - Completely idempotent: running the command multiple times produces consistent output.
   - Atomic directory creation via staging and renaming: unfinished or failed runs leave no corrupted files in destination directories.
   - Pack directory is read-only and never modified.

## CLI Usage

### Generate for a specific pack
```bash
python3 carousel_maker.py --pack <pack_id>
```

### Generate for all ready packs
```bash
python3 carousel_maker.py --all
```

### Custom Directories
```bash
python3 carousel_maker.py --pack <pack_id> --products-dir /path/to/products --marketing-dir /path/to/marketing
```

## Running Tests

Tests are executed with standard Python `unittest`:

```bash
python3 -m unittest tests.test_kj_carousel
```
