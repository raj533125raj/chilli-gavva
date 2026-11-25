"""Couples shared budget (.xlsx + CSV): two incomes, shared bills, fair split and who-owes-whom."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

import money_sheets as ms

FORMAT = "couples_budget"
KEYWORDS = [
    "couples budget", "couples budget spreadsheet", "couples budget tracker", "shared budget tracker", "couples budget planner", "shared budget spreadsheet",
    "joint budget spreadsheet", "budget for couples", "household budget for two", "split expenses tracker",
    "shared expenses tracker", "couple finance tracker", "joint finances spreadsheet",
]
DEFAULT_TITLE = "Couples Shared Budget Spreadsheet"
ENTRIES = 300
CATEGORIES = ["Rent or Mortgage", "Utilities", "Groceries", "Transport", "Insurance", "Phone and Internet",
              "Eating Out", "Household", "Health", "Gifts", "Travel", "Savings", "Other"]
WHO = ["Partner A", "Partner B"]
SPLITS = ["50/50", "By income", "Only Partner A", "Only Partner B"]


def normalize_spec(spec: Any) -> Dict[str, Any]:
    return {"title": ms.normalize_title(spec, DEFAULT_TITLE)}


def design(spec: Any = None) -> Dict[str, Any]:
    title = normalize_spec(spec)["title"]
    income = ms.table_tab("Income", [
        ms.col("Date", ms.DATE, 14), ms.col("Who", ms.TEXT, 14, options=WHO), ms.col("Source", ms.TEXT, 28),
        ms.col("Amount", ms.MONEY, 16), ms.col("Note", ms.TEXT, 28)], ENTRIES)
    inc_who, inc_amt, inc_date = ms.rng(income, "Who"), ms.rng(income, "Amount"), ms.rng(income, "Date")

    share_a = "Summary!$B$7"   # Partner A share of total income (0-1), see summary lines below

    def a_share(r):
        return (f'=IF(D{r}="","",IF(F{r}="Only Partner A",N(D{r}),IF(F{r}="Only Partner B",0,'
                f'IF(AND(F{r}="By income",ISNUMBER({share_a})),N(D{r})*{share_a},N(D{r})/2))))')

    def b_share(r):
        return f'=IF(D{r}="","",N(D{r})-N(G{r}))'

    shared = ms.table_tab("Shared Expenses", [
        ms.col("Date", ms.DATE, 14), ms.col("Category", ms.TEXT, 20, options=CATEGORIES),
        ms.col("Description", ms.TEXT, 26), ms.col("Amount", ms.MONEY, 14),
        ms.col("Paid By", ms.TEXT, 13, options=WHO), ms.col("Split", ms.TEXT, 15, options=SPLITS),
        ms.col("Partner A Share", ms.MONEY, 15, a_share), ms.col("Partner B Share", ms.MONEY, 15, b_share)],
        ENTRIES)
    amt, paid_by, cat = ms.rng(shared, "Amount"), ms.rng(shared, "Paid By"), ms.rng(shared, "Category")
    a_col, b_col, date_col = ms.rng(shared, "Partner A Share"), ms.rng(shared, "Partner B Share"), ms.rng(shared, "Date")

    lines = [("Summary", "title"), ("", "blank"), ("Income", "head", ["Value"]),
             ("Partner A income", "row", [(ms.MONEY, f'=SUMIF({inc_who},"Partner A",{inc_amt})')]),
             ("Partner B income", "row", [(ms.MONEY, f'=SUMIF({inc_who},"Partner B",{inc_amt})')]),
             ("Total income", "row", [(ms.MONEY, "=B4+B5")]),
             ("Partner A share of income (0-1, used by the 'By income' split)", "row",
              [(ms.NUMBER, '=IF(B6=0,"",ROUND(B4/B6,4))')]),
             ("", "blank"), ("Shared spending", "head", ["Value"]),
             ("Total shared spending", "row", [(ms.MONEY, f"=SUM({amt})")]),
             ("Paid by Partner A", "row", [(ms.MONEY, f'=SUMIF({paid_by},"Partner A",{amt})')]),
             ("Paid by Partner B", "row", [(ms.MONEY, f'=SUMIF({paid_by},"Partner B",{amt})')]),
             ("Partner A fair share", "row", [(ms.MONEY, f"=SUM({a_col})")]),
             ("Partner B fair share", "row", [(ms.MONEY, f"=SUM({b_col})")]),
             ("Settle up", "row", [(ms.TEXT, '=IF(ROUND(B11-B13,2)=0,"All even",IF(B11>B13,'
                                            '"Partner B owes Partner A "&TEXT(B11-B13,"0.00"),'
                                            '"Partner A owes Partner B "&TEXT(B13-B11,"0.00")))')]),
             ("Left after shared spending", "row", [(ms.MONEY, "=B6-B10")]),
             ("Spent % of income", "row", [(ms.NUMBER, '=IF(B6=0,"",ROUND(B10/B6*100,0))')]),
             ("Spending bar", "row", [(ms.TEXT, ms.progress_bar("B17"))]),
             ("", "blank"), ("Category", "head", ["Total", "% of spending"])]
    first = len(lines) + 1
    for i, name in enumerate(CATEGORIES):
        r = first + i
        lines.append((name, "row", [(ms.MONEY, f'=SUMIF({cat},A{r},{amt})'),
                                    (ms.NUMBER, f'=IF($B$10=0,"",ROUND(B{r}/$B$10*100,0))')]))
    summary = ms.form_tab("Summary", lines, widths=(52, 22, 16))

    month_lines = [("Monthly", "title"), ("", "blank"),
                   ("Month (2027)", "head", ["Income", "Shared Spending", "Left Over", "Partner A Paid",
                                             "Partner B Paid"])]
    first = len(month_lines) + 1
    for m, month in enumerate(ms.MONTHS, 1):
        r = first + m - 1
        month_lines.append((month, "row", [
            (ms.MONEY, "=" + ms.sum_in_month(inc_amt, inc_date, 2027, m)),
            (ms.MONEY, "=" + ms.sum_in_month(amt, date_col, 2027, m)),
            (ms.MONEY, f"=B{r}-C{r}"),
            (ms.MONEY, "=" + ms.sum_in_month(amt, date_col, 2027, m, f',{paid_by},"Partner A"')),
            (ms.MONEY, "=" + ms.sum_in_month(amt, date_col, 2027, m, f',{paid_by},"Partner B"'))]))
    month_lines.append(("Change 2027 in these formulas to reuse the sheet next year.", "text"))
    monthly = ms.form_tab("Monthly", month_lines, widths=(18, 16, 18, 16, 16, 16))

    start = ms.start_here(title, [
        "Income tab: each partner adds their income with the date, Who (Partner A or Partner B) and amount.",
        "Shared Expenses tab: add every shared bill with its category, amount, who paid and the split.",
        "Split choices: 50/50, By income (each pays the same share of their income), Only Partner A, "
        "Only Partner B.",
        "Summary tab: totals, each partner's fair share and one 'Settle up' line say who owes whom.",
        "Monthly tab: income, shared spending and left over for each month of the year."],
        ["Tip: agree on the split for each category together before you start."])
    return {"format": FORMAT, "title": title, "accent": "#8a4f7d",
            "band": "COUPLES SHARED BUDGET · EXCEL + GOOGLE SHEETS",
            "tagline": "Two incomes, shared bills, a fair split and one clear settle-up line",
            "tabs": [start, income, shared, summary, monthly],
            "previews": [{"tab": "Shared Expenses", "caption": "Shared Expenses: pick who paid and the split, shares are formulas"},
                         {"tab": "Summary", "caption": "Summary: fair shares, settle-up line and category totals"},
                         {"tab": "Monthly", "caption": "Monthly: income, shared spending and left over per month"}],
            "features": ["Each partner's share of every shared bill: 50/50, by income or only one partner",
                         "Settle up: one line says who owes whom (fair share minus what each paid)",
                         "Totals per category with % of spending",
                         "Monthly income, shared spending and left over (SUMIFS by date)",
                         "Spent % of income with a text progress bar"]}


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    return ms.build(design(spec), out_dir)
