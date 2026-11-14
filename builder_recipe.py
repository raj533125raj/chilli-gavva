"""builder_recipe.py — Recipe book and meal planner printable pack.

FORMAT = 'recipe_book'
KEYWORDS = ['recipe book', 'recipe cards', 'meal planner', 'grocery list']

Builds a deterministic A4 + US Letter printable pack with recipe cards
(1, 2, or 4 per page), a weekly meal plan, grocery list by aisle, and a
pantry inventory. Indian and global category sets are included with veg and
non-veg tags. Cover and three preview images use Pillow only. No stock images,
no currency amounts, no network.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    HAS_PILLOW = False

import pdf_design

FORMAT = "recipe_book"
KEYWORDS = [
    "recipe book", "recipe cards", "meal planner", "grocery list",
    "printable recipe book", "weekly meal plan",
]

DEFAULT_TITLE = "Everyday Recipe Book & Meal Planner"
DEFAULT_CUISINE = "mixed"
RECIPES_PER_PAGE = (1, 2, 4)

CUISINE_SETS: Dict[str, List[str]] = {
    "indian": ["Breakfast", "Dal & Curry", "Rice & Biryani", "Roti & Bread", "Snacks", "Sweets"],
    "global": ["Breakfast", "Mains", "Salads", "Soups", "Baking", "Drinks"],
    "mixed": [
        "Breakfast", "Dal & Curry", "Rice & Biryani", "Roti & Bread",
        "Mains", "Salads", "Soups", "Baking",
    ],
}

DIET_TAGS = ["Veg", "Non-veg", "Vegan", "Egg"]

DEFAULT_RECIPES: List[Dict[str, Any]] = [
    {
        "title": "Masala Omelette", "category": "Breakfast", "diet": "Non-veg", "prep": "15 min",
        "ingredients": ["2 eggs", "1 small onion", "1 green chilli", "Coriander leaves", "Salt and pepper", "1 tsp oil"],
        "method": ["Whisk the eggs with salt and pepper.", "Fold in onion, chilli, and coriander.",
                   "Heat oil in a pan and pour the mixture.", "Cook both sides on low heat and serve."],
    },
    {
        "title": "Dal Tadka", "category": "Dal & Curry", "diet": "Veg", "prep": "30 min",
        "ingredients": ["1 cup toor dal", "1 tomato", "1 onion", "Cumin seeds", "Turmeric", "Ghee"],
        "method": ["Rinse the dal and pressure cook until soft.", "Saute onion, tomato, and spices.",
                   "Add the cooked dal and simmer.", "Finish with a warm ghee tadka."],
    },
    {
        "title": "Vegetable Pulao", "category": "Rice & Biryani", "diet": "Veg", "prep": "35 min",
        "ingredients": ["1 cup basmati rice", "Mixed vegetables", "Bay leaf", "Cinnamon", "Cumin seeds", "Salt"],
        "method": ["Soak the rice for twenty minutes.", "Saute whole spices and vegetables.",
                   "Add rice, water, and salt.", "Cover and cook until fluffy."],
    },
    {
        "title": "Paneer Bhurji", "category": "Roti & Bread", "diet": "Veg", "prep": "20 min",
        "ingredients": ["200 g paneer", "1 onion", "1 tomato", "Capsicum", "Turmeric", "Coriander"],
        "method": ["Crumble the paneer.", "Saute onion, tomato, and capsicum.",
                   "Add paneer and spices.", "Mix well and garnish with coriander."],
    },
    {
        "title": "Overnight Oats", "category": "Breakfast", "diet": "Vegan", "prep": "10 min",
        "ingredients": ["1 cup oats", "1 cup plant milk", "Chia seeds", "Banana", "Cinnamon", "Maple syrup"],
        "method": ["Mix oats, milk, chia, and cinnamon.", "Cover and chill overnight.",
                   "Top with banana slices.", "Add a little maple syrup before serving."],
    },
    {
        "title": "Grilled Chicken Salad", "category": "Salads", "diet": "Non-veg", "prep": "25 min",
        "ingredients": ["1 chicken breast", "Mixed leaves", "Cherry tomatoes", "Cucumber", "Olive oil", "Lemon"],
        "method": ["Season and grill the chicken.", "Rest for five minutes and slice.",
                   "Toss leaves and vegetables with dressing.", "Top with chicken and lemon."],
    },
    {
        "title": "Tomato Basil Soup", "category": "Soups", "diet": "Veg", "prep": "30 min",
        "ingredients": ["6 tomatoes", "1 onion", "Garlic", "Fresh basil", "Vegetable stock", "Olive oil"],
        "method": ["Roast tomatoes, onion, and garlic.", "Blend with stock until smooth.",
                   "Simmer for ten minutes.", "Finish with basil and olive oil."],
    },
    {
        "title": "Banana Pancakes", "category": "Baking", "diet": "Egg", "prep": "20 min",
        "ingredients": ["1 ripe banana", "1 egg", "1 cup flour", "1 tsp baking powder", "Milk", "Butter"],
        "method": ["Mash the banana and whisk with egg.", "Fold in flour, baking powder, and milk.",
                   "Cook small pancakes on a buttered pan.", "Serve warm with fruit."],
    },
]

DEFAULT_AISLES = ["Produce", "Dairy & Eggs", "Pantry", "Spices", "Protein", "Other"]
WEEK_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MEALS = ["Breakfast", "Lunch", "Dinner"]
PAPER_SIZES = {"a4": "A4", "letter": "Letter"}


def _clean_text(value: Any, fallback: str = "") -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def _clean_list(value: Any, fallback: List[str], limit: int) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return list(fallback)[:limit]
    out: List[str] = []
    for item in value:
        text = _clean_text(item)
        if text:
            out.append(text)
        if len(out) == limit:
            break
    return out or list(fallback)[:limit]


def _normalize_diet(value: Any) -> str:
    text = _clean_text(value, "Veg").lower()
    if text in ("non-veg", "nonveg", "non veg", "chicken", "fish", "meat"):
        return "Non-veg"
    if text in ("vegan", "plant"):
        return "Vegan"
    if text in ("egg", "eggetarian"):
        return "Egg"
    return "Veg"


def _normalize_recipe(item: Any, index: int) -> Dict[str, Any]:
    if isinstance(item, dict):
        title = _clean_text(item.get("title") or item.get("name"), f"Recipe {index + 1}")
        category = _clean_text(item.get("category"), "Mains")
        diet = _normalize_diet(item.get("diet"))
        prep = _clean_text(item.get("prep") or item.get("time"), "25 min")
        ingredients = _clean_list(item.get("ingredients"), ["Ingredient one", "Ingredient two", "Ingredient three"], 8)
        method = _clean_list(item.get("method") or item.get("steps"), ["Prepare the ingredients.", "Cook gently until ready.", "Serve warm."], 6)
    else:
        title = _clean_text(item, f"Recipe {index + 1}")
        category = "Mains"
        diet = "Veg"
        prep = "25 min"
        ingredients = ["Ingredient one", "Ingredient two", "Ingredient three"]
        method = ["Prepare the ingredients.", "Cook gently until ready.", "Serve warm."]
    return {
        "title": title, "category": category, "diet": diet, "prep": prep,
        "ingredients": ingredients, "method": method,
    }


def normalize_spec(spec: Any) -> Dict[str, Any]:
    """Validate the spec and return a deterministic normalized dictionary."""
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dictionary")

    title = _clean_text(spec.get("title"), DEFAULT_TITLE)
    cuisine = _clean_text(spec.get("cuisine"), DEFAULT_CUISINE).lower()
    if cuisine not in CUISINE_SETS:
        cuisine = DEFAULT_CUISINE

    try:
        per_page = int(spec.get("recipes_per_page", 2))
    except (TypeError, ValueError):
        raise ValueError("recipes_per_page must be an integer")
    if per_page not in RECIPES_PER_PAGE:
        raise ValueError("recipes_per_page must be 1, 2, or 4")

    raw_recipes = spec.get("recipes")
    if isinstance(raw_recipes, (list, tuple)) and raw_recipes:
        recipes = [_normalize_recipe(item, i) for i, item in enumerate(raw_recipes[:12])]
    else:
        recipes = [dict(recipe) for recipe in DEFAULT_RECIPES]

    aisles = _clean_list(spec.get("aisles"), DEFAULT_AISLES, 8)
    pantry = _clean_list(
        spec.get("pantry_items"),
        ["Rice", "Flour", "Pasta", " lentils".strip(), "Cooking oil", "Salt", "Sugar", "Tea", "Coffee", "Spices"],
        16,
    )
    return {
        "title": title,
        "cuisine": cuisine,
        "categories": list(CUISINE_SETS[cuisine]),
        "recipes_per_page": per_page,
        "recipes": recipes,
        "aisles": aisles,
        "pantry_items": pantry,
    }


def _font(size: int, bold: bool = False):
    try:
        import mockups
        face = mockups.font("bold" if bold else "regular", size)
        if face is not None:
            return face
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _cover_image(path: Path, norm: Dict[str, Any], preview: int = 0) -> None:
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for recipe book images")
    w, h = 1200, 800
    bg = "#fbf7f0"
    ink = "#2d2621"
    accent = "#a35c3a"
    green = "#58705a"
    im = Image.new("RGB", (w, h), color=bg)
    draw = ImageDraw.Draw(im)
    draw.rectangle([26, 26, w - 26, h - 26], outline=accent, width=3)
    draw.rectangle([26, 26, w - 26, 76], fill=accent)
    draw.text((50, 40), "CHILLI GAVVA · RECIPE & MEAL PLANNER", fill="#ffffff", font=_font(19, True))
    draw.text((60, 120), norm["title"][:40], fill=ink, font=_font(40, True))
    draw.text((60, 176), f"{norm['cuisine'].title()} categories · {len(norm['recipes'])} recipe cards · A4 + US Letter", fill=accent, font=_font(19))

    card_x, card_y = 60, 240
    for row in range(min(3, len(norm["recipes"]))):
        recipe = norm["recipes"][row]
        y = card_y + row * 132
        draw.rounded_rectangle([card_x, y, card_x + 650, y + 112], radius=10, fill="#ffffff", outline="#d8c5b5", width=2)
        draw.text((card_x + 18, y + 14), recipe["title"][:34], fill=ink, font=_font(18, True))
        draw.text((card_x + 18, y + 42), f"{recipe['category']} · {recipe['diet']} · {recipe['prep']}", fill="#7b6b5f", font=_font(13))
        for i, ingredient in enumerate(recipe["ingredients"][:3]):
            draw.text((card_x + 18, y + 68 + i * 14), f"• {ingredient[:48]}", fill=green, font=_font(11))

    draw.rounded_rectangle([760, 240, 1140, 640], radius=14, fill="#ffffff", outline="#d8c5b5", width=2)
    draw.text((786, 264), "WEEKLY MEAL PLAN", fill=accent, font=_font(16, True))
    for i, day in enumerate(WEEK_DAYS):
        draw.text((786, 300 + i * 44), day, fill=ink, font=_font(12, True))
        draw.line([(900, 308 + i * 44), (1114, 308 + i * 44)], fill="#d8c5b5")
    draw.text((60, 680), "Recipe cards · Weekly meal plan · Grocery list by aisle · Pantry inventory", fill="#7b6b5f", font=_font(15))
    draw.text((900, 720), ["Cover", "Recipe Cards", "Meal Plan", "Grocery List"][preview % 4], fill=accent, font=_font(14, True))
    im.save(str(path), format="PNG", optimize=True)


def _heading(pdf: Any, text: str, subtitle: str = "") -> None:
    pdf.text(pdf_design.MARGIN, pdf.h - 62, text, 20, bold=True, color="#2d2621")
    if subtitle:
        pdf.text(pdf_design.MARGIN, pdf.h - 84, subtitle, 10, color="#7b6b5f")
    pdf.line(pdf_design.MARGIN, pdf.h - 96, pdf.w - pdf_design.MARGIN, pdf.h - 96, color="#d8c5b5")


def _title_page(pdf: Any, norm: Dict[str, Any]) -> None:
    lines, size = pdf_design.fit_text(norm["title"], 30, pdf.w - 2 * pdf_design.MARGIN, bold=True, max_lines=3, min_size=16)
    y = pdf.h / 2 + 90
    for line in lines:
        pdf.text_center(pdf.w / 2, y, line, size, bold=True, color="#2d2621")
        y -= size * 1.25
    pdf.text_center(pdf.w / 2, y - 10, f"{norm['cuisine'].title()} recipe collection · {len(norm['recipes'])} cards", 16, color="#a35c3a")
    pdf.text_center(pdf.w / 2, y - 40, "Recipe cards · Weekly meal plan · Grocery list by aisle · Pantry inventory", 10, color="#7b6b5f")
    pdf.line(pdf.w / 2 - 90, y - 68, pdf.w / 2 + 90, y - 68, color="#a35c3a", width=1.2)


def _recipe_card(pdf: Any, x: float, y: float, w: float, h: float, recipe: Dict[str, Any], number: int) -> None:
    pdf.rect(x, y, w, h, color="#d8c5b5", width=0.8)
    pdf.text(x + 10, y + h - 20, recipe["title"][:52], 13, bold=True, color="#2d2621")
    pdf.text(x + 10, y + h - 36, f"{recipe['category']} · {recipe['diet']} · {recipe['prep']}", 7.5, color="#a35c3a")
    pdf.text(x + 10, y + h - 54, "Ingredients", 8, bold=True, color="#58705a")
    line_y = y + h - 68
    for ingredient in recipe["ingredients"][:6]:
        pdf.text(x + 12, line_y, f"• {ingredient[:42]}", 7, color="#2d2621")
        line_y -= 10
    method_y = line_y - 6
    pdf.text(x + 10, method_y, "Method", 8, bold=True, color="#58705a")
    method_y -= 14
    for step in recipe["method"][:5]:
        pdf.text(x + 12, method_y, f"{step[:56]}", 7, color="#2d2621")
        method_y -= 10
    pdf.text(x + w - 42, y + 8, f"No. {number:02d}", 7, color="#7b6b5f")


def _recipe_pages(pdf: Any, norm: Dict[str, Any]) -> None:
    per_page = norm["recipes_per_page"]
    recipes = norm["recipes"]
    if per_page == 1:
        cols, rows = 1, 1
    elif per_page == 2:
        cols, rows = 1, 2
    else:
        cols, rows = 2, 2
    page_size = cols * rows
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    bottom = 70
    gap = 12
    card_w = (right - left - (cols - 1) * gap) / cols
    card_h = (top - bottom - (rows - 1) * gap) / rows
    for page_start in range(0, len(recipes), page_size):
        pdf.new_page()
        chunk = recipes[page_start:page_start + page_size]
        _heading(pdf, f"Recipe Cards {page_start + 1}–{page_start + len(chunk)}", "Write your own notes in the margins.")
        for index, recipe in enumerate(chunk):
            col = index % cols
            row = index // cols
            x = left + col * (card_w + gap)
            y = top - (row + 1) * card_h - row * gap
            _recipe_card(pdf, x, y, card_w, card_h, recipe, page_start + index + 1)


def _meal_plan_page(pdf: Any, norm: Dict[str, Any]) -> None:
    pdf.new_page()
    _heading(pdf, "Weekly Meal Plan", "Plan once, shop once, cook calmly.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    label_w = 88
    col_w = (right - left - label_w) / len(MEALS)
    row_h = min(54.0, (top - 80) / len(WEEK_DAYS))
    pdf.text(left, top + 8, "Day", 8, bold=True, color="#7b6b5f")
    for i, meal in enumerate(MEALS):
        pdf.text_center(left + label_w + i * col_w + col_w / 2, top + 8, meal, 8, bold=True, color="#7b6b5f")
    for r, day in enumerate(WEEK_DAYS):
        y = top - (r + 1) * row_h
        pdf.text(left + 2, y + row_h / 2 - 4, day, 9, bold=True, color="#2d2621")
        for c in range(len(MEALS)):
            pdf.rect(left + label_w + c * col_w, y, col_w, row_h, color="#d8c5b5", width=0.5)
    pdf.text(left, 64, "Tip: repeat one trusted meal each week to save planning time.", 9, color="#7b6b5f")


def _grocery_page(pdf: Any, norm: Dict[str, Any]) -> None:
    pdf.new_page()
    _heading(pdf, "Grocery List by Aisle", "Tick each item as you shop.")
    left = pdf_design.MARGIN
    top = pdf.h - 128
    gap = 12
    cols = 2
    box_w = (pdf.w - 2 * left - gap) / cols
    rows = (len(norm["aisles"]) + cols - 1) // cols
    box_h = min(150.0, (top - 80 - (rows - 1) * gap) / max(1, rows))
    for index, aisle in enumerate(norm["aisles"]):
        col = index % cols
        row = index // cols
        x = left + col * (box_w + gap)
        y = top - (row + 1) * box_h - row * gap
        pdf.rect(x, y, box_w, box_h, color="#d8c5b5", width=0.7)
        pdf.text(x + 8, y + box_h - 16, aisle, 10, bold=True, color="#a35c3a")
        for line in range(max(0, int((box_h - 30) // 15))):
            pdf.line(x + 20, y + box_h - 30 - line * 15, x + box_w - 8, y + box_h - 30 - line * 15, color="#e3d5c8")
            pdf.rect(x + 8, y + box_h - 34 - line * 15, 6, 6, color="#9fb8b5", width=0.4)


def _pantry_page(pdf: Any, norm: Dict[str, Any]) -> None:
    pdf.new_page()
    _heading(pdf, "Pantry Inventory", "Know what you have before the next shop.")
    left = pdf_design.MARGIN
    right = pdf.w - pdf_design.MARGIN
    top = pdf.h - 128
    headers = ["Item", "Quantity", "Use by", "Notes"]
    widths = [0.42, 0.16, 0.18, 0.24]
    x = left
    for header, width in zip(headers, widths):
        pdf.text(x + 2, top + 8, header, 8, bold=True, color="#7b6b5f")
        x += (right - left) * width
    row_h = min(28.0, (top - 80) / 16)
    items = norm["pantry_items"] + [""] * max(0, 16 - len(norm["pantry_items"]))
    for r, item in enumerate(items[:16]):
        y = top - (r + 1) * row_h
        x = left
        for width in widths:
            pdf.rect(x, y, (right - left) * width, row_h, color="#d8c5b5", width=0.4)
            x += (right - left) * width
        if item:
            pdf.text(left + 4, y + row_h / 2 - 4, item[:40], 8, color="#2d2621")


def _build_pdf(path: Path, paper: str, norm: Dict[str, Any]) -> None:
    size = PAPER_SIZES[paper]
    pdf = pdf_design.Pdf(size, footer=f"{norm['title']} · Recipe & Meal Planner", style="A")
    pdf.new_page()
    _title_page(pdf, norm)
    _recipe_pages(pdf, norm)
    _meal_plan_page(pdf, norm)
    _grocery_page(pdf, norm)
    _pantry_page(pdf, norm)
    pdf.save(str(path), title=norm["title"])


def _readme(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"{norm['title']}",
        "=" * 52,
        "",
        "WHAT IS INSIDE",
        f"- {len(norm['recipes'])} recipe cards ({norm['recipes_per_page']} per page)",
        "- A4 PDF and US Letter PDF",
        "- Weekly meal plan page",
        "- Grocery list by aisle",
        "- Pantry inventory page",
        "- cover.png and three preview images",
        "",
        "HOW TO PRINT",
        "1. Open the PDF for your paper size.",
        "2. Print at 100% scale (Actual Size).",
        "3. For durable cards, print on 200-300 gsm card stock.",
        "4. Laminate recipe cards or place them in a binder.",
        "",
        "TIPS",
        "- Plan the week before writing the grocery list.",
        "- Keep the pantry page updated after every shop.",
        "- Add your own family notes beside each recipe.",
        "",
        "LICENSE",
        "Personal use only. Do not resell or redistribute the files.",
    ]) + "\n"


def _license(norm: Dict[str, Any]) -> str:
    return "\n".join([
        f"LICENSE — {norm['title']}",
        "",
        "This printable pack is for personal use only.",
        "You may print copies for yourself and your household.",
        "You may not resell, redistribute, share, or claim the files as your own.",
        "All layouts and text are generated by the Chilli Gavva robot.",
        "",
    ])


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    """Build the recipe book and meal planner pack and return created file names."""
    if not HAS_PILLOW:
        raise RuntimeError("Pillow is required for the recipe book builder")
    norm = normalize_spec(spec)
    out_path = Path(out_dir).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="recipe_build_"))
    names: List[str] = []
    try:
        for paper in PAPER_SIZES:
            name = f"recipe_book_{paper}.pdf"
            _build_pdf(temp_dir / name, paper, norm)
            names.append(name)

        names.append("cover.png")
        _cover_image(temp_dir / "cover.png", norm, preview=0)
        for index in range(1, 4):
            name = f"preview_{index}.png"
            _cover_image(temp_dir / name, norm, preview=index)
            names.append(name)

        names.extend(["README.txt", "LICENSE.txt"])
        (temp_dir / "README.txt").write_text(_readme(norm), encoding="utf-8")
        (temp_dir / "LICENSE.txt").write_text(_license(norm), encoding="utf-8")

        recipe_pages = (len(norm["recipes"]) + norm["recipes_per_page"] - 1) // norm["recipes_per_page"]
        manifest = {
            "format": FORMAT,
            "title": norm["title"],
            "cuisine": norm["cuisine"],
            "categories": norm["categories"],
            "recipes_per_page": norm["recipes_per_page"],
            "recipe_count": len(norm["recipes"]),
            "recipes": [recipe["title"] for recipe in norm["recipes"]],
            "aisles": norm["aisles"],
            "paper_sizes": list(PAPER_SIZES),
            "pages_per_pdf": 1 + recipe_pages + 3,
            "files": sorted(names + ["manifest.json"]),
        }
        (temp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        names.append("manifest.json")

        out_path.mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(temp_dir / name, out_path / name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return sorted(names)


def _content_digest(norm: Dict[str, Any]) -> str:
    payload = json.dumps(norm, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
