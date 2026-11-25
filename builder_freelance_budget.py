"""Freelancer irregular-income budget (.xlsx + CSV): tax set-aside, buffer fund, steady monthly pay."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

import money_sheets as ms

FORMAT = "freelance_budget"
KEYWORDS = [
    "irregular income budget", "irregular income budget spreadsheet", "irregular income tracker", "irregular income", "variable income budget", "freelancer budget", "freelance budget spreadsheet",
    "freelancer budget spreadsheet", "self employed budget", "gig worker budget", "irregular paycheck budget",
    "freelancer income and expense tracker", "freelance tax set aside", "uneven income budget",
]
DEFAULT_TITLE = "Freelancer Irregular Income Budget Spreadsheet"
ENTRIES = 300
EXPENSE_TYPES = ["Business", "Personal"]


def normalize_spec(spec: Any) -> Dict[str, Any]:
    return {"title": ms.normalize_title(spec, DEFAULT_TITLE)}


def design(spec: Any = None) -> Dict[str, Any]:
    title = normalize_spec(spec)["title"]
    settings = ms.form_tab("Settings", [
        ("Settings", "title"), ("", "blank"), ("Your numbers (type in column B)", "head", ["Value"]),
        ("Tax set-aside % (ask a local tax adviser for your rate)", "input"),
        ("Steady monthly pay you give yourself", "input"),
        ("Buffer goal in months of steady pay", "input"),
        ("", "blank"),
        ("Leave a cell empty until you know the number; the formulas wait for it.", "text")],
        widths=(58, 20))
    tax_pct = ms.form_ref(settings, "Tax set-aside % (ask a local tax adviser for your rate)")
    pay = ms.form_ref(settings, "Steady monthly pay you give yourself")
    goal_months = ms.form_ref(settings, "Buffer goal in months of steady pay")

    income = ms.table_tab("Income Log", [
        ms.col("Date Received", ms.DATE, 14), ms.col("Client", ms.TEXT, 22), ms.col("Project", ms.TEXT, 24),
        ms.col("Amount Received", ms.MONEY, 16),
        ms.col("Set Aside for Tax", ms.MONEY, 16, lambda r: f'=IF(D{r}="","",ROUND(N(D{r})*N({tax_pct})/100,2))'),
        ms.col("After Tax", ms.MONEY, 14, lambda r: f'=IF(D{r}="","",N(D{r})-N(E{r}))')], ENTRIES)
    expenses = ms.table_tab("Expenses", [
        ms.col("Date", ms.DATE, 14), ms.col("Type", ms.TEXT, 12, options=EXPENSE_TYPES),
        ms.col("Category", ms.TEXT, 20), ms.col("Description", ms.TEXT, 28), ms.col("Amount", ms.MONEY, 14)],
        ENTRIES)
    buffer = ms.table_tab("Buffer Fund", [
        ms.col("Date", ms.DATE, 14), ms.col("Added", ms.MONEY, 14), ms.col("Taken Out", ms.MONEY, 14),
        ms.col("Note", ms.TEXT, 30),
        ms.col("Balance", ms.MONEY, 14,
               lambda r: f'=IF(AND(B{r}="",C{r}=""),"",SUM($B$2:B{r})-SUM($C$2:C{r}))')], 200)

    inc_amt, inc_date = ms.rng(income, "Amount Received"), ms.rng(income, "Date Received")
    inc_tax, inc_after = ms.rng(income, "Set Aside for Tax"), ms.rng(income, "After Tax")
    exp_amt, exp_date, exp_type = ms.rng(expenses, "Amount"), ms.rng(expenses, "Date"), ms.rng(expenses, "Type")

    month_lines = [("Monthly", "title"), ("", "blank"),
                   ("Month (2027)", "head", ["Income", "Tax Set Aside", "Business Costs", "Personal Spending",
                                             "Left Over"])]
    first = len(month_lines) + 1
    for m, month in enumerate(ms.MONTHS, 1):
        r = first + m - 1
        month_lines.append((month, "row", [
            (ms.MONEY, "=" + ms.sum_in_month(inc_amt, inc_date, 2027, m)),
            (ms.MONEY, "=" + ms.sum_in_month(inc_tax, inc_date, 2027, m)),
            (ms.MONEY, "=" + ms.sum_in_month(exp_amt, exp_date, 2027, m, f',{exp_type},"Business"')),
            (ms.MONEY, "=" + ms.sum_in_month(exp_amt, exp_date, 2027, m, f',{exp_type},"Personal"')),
            (ms.MONEY, f"=B{r}-C{r}-D{r}-E{r}")]))
    last = first + 11
    month_lines.append(("Change 2027 in these formulas to reuse the sheet next year.", "text"))
    monthly = ms.form_tab("Monthly", month_lines, widths=(18, 16, 16, 16, 18, 16))
    inc_months = f"Monthly!$B${first}:$B${last}"

    lines = [("Summary", "title"), ("", "blank"), ("This year", "head", ["Value"]),
             ("Total income", "row", [(ms.MONEY, f"=SUM({inc_amt})")]),
             ("Total set aside for tax", "row", [(ms.MONEY, f"=SUM({inc_tax})")]),
             ("Total after tax", "row", [(ms.MONEY, f"=SUM({inc_after})")]),
             ("Business costs", "row", [(ms.MONEY, f'=SUMIF({exp_type},"Business",{exp_amt})')]),
             ("Personal spending", "row", [(ms.MONEY, f'=SUMIF({exp_type},"Personal",{exp_amt})')]),
             ("", "blank"), ("Uneven months", "head", ["Value"]),
             ("Months with income", "row", [(ms.NUMBER, f'=COUNTIF({inc_months},">0")')]),
             ("Average month (months with income)", "row",
              [(ms.MONEY, f'=IFERROR(AVERAGEIF({inc_months},">0"),"")')]),
             ("Highest month", "row", [(ms.MONEY, f"=MAX({inc_months})")]),
             ("Lowest month with income", "row", [(ms.MONEY, f'=IFERROR(SMALL({inc_months},COUNTIF({inc_months},0)+1),"")')]),
             ("", "blank"), ("Buffer fund", "head", ["Value"]),
             ("Buffer balance", "row", [(ms.MONEY, "=SUM('Buffer Fund'!$B$2:$B$201)-SUM('Buffer Fund'!$C$2:$C$201)")]),
             ("Months of steady pay covered", "row", [(ms.NUMBER, f'=IF(N({pay})=0,"",ROUND(B17/{pay},1))')]),
             ("Buffer goal", "row", [(ms.MONEY, f'=IF(OR(N({pay})=0,N({goal_months})=0),"",{pay}*{goal_months})')]),
             ("Buffer progress %", "row", [(ms.NUMBER, '=IF(OR(B19="",N(B19)=0),"",ROUND(MIN(1,B17/B19)*100,0))')]),
             ("Buffer progress bar", "row", [(ms.TEXT, ms.progress_bar("B20"))])]
    summary = ms.form_tab("Summary", lines, widths=(46, 22))

    start = ms.start_here(title, [
        "Settings tab: type your tax set-aside %, the steady monthly pay you give yourself and your buffer "
        "goal in months (leave any you do not know yet empty).",
        "Income Log tab: add every payment when it arrives. Tax to set aside and after-tax amount fill in.",
        "Expenses tab: add costs and mark each one Business or Personal.",
        "Buffer Fund tab: record money you move into or out of your buffer. The balance runs by itself.",
        "Monthly and Summary tabs: see good and slow months, your average month and buffer progress."],
        ["Idea: in a good month, top up the buffer; in a slow month, pay yourself the steady amount from it."])
    return {"format": FORMAT, "title": title, "accent": "#3b5f8f",
            "band": "FREELANCER IRREGULAR INCOME BUDGET · EXCEL + GOOGLE SHEETS",
            "tagline": "Tax set-aside, a buffer fund and a steady monthly pay for uneven income",
            "tabs": [start, settings, income, expenses, buffer, monthly, summary],
            "previews": [{"tab": "Income Log", "caption": "Income Log: tax set-aside and after-tax amount are formulas"},
                         {"tab": "Monthly", "caption": "Monthly: income, tax, business costs, spending and left over"},
                         {"tab": "Summary", "caption": "Summary: average month, lowest month and buffer progress"}],
            "features": ["Tax set-aside per payment from your own % (Settings tab)",
                         "Running buffer fund balance and months of steady pay it covers",
                         "Monthly income, tax, business costs, personal spending and left over",
                         "Average month, highest month and lowest month with income",
                         "Buffer progress % toward your goal with a text progress bar"]}


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    return ms.build(design(spec), out_dir)
