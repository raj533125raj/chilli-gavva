"""Sinking funds + savings goals tracker (.xlsx + CSV): saved so far, left to save, per-month need."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

import money_sheets as ms

FORMAT = "sinking_funds"
KEYWORDS = [
    "sinking funds", "sinking fund tracker", "sinking funds budget", "sinking funds tracker", "sinking funds spreadsheet",
    "savings goals tracker", "savings goal spreadsheet", "savings tracker spreadsheet", "savings challenge tracker",
    "savings goals planner", "save for goals spreadsheet", "emergency fund tracker",
]
DEFAULT_TITLE = "Sinking Funds and Savings Goals Tracker"
FUNDS, ENTRIES = 15, 300


def normalize_spec(spec: Any) -> Dict[str, Any]:
    return {"title": ms.normalize_title(spec, DEFAULT_TITLE)}


def design(spec: Any = None) -> Dict[str, Any]:
    title = normalize_spec(spec)["title"]
    moves = ms.table_tab("Deposits", [
        ms.col("Date", ms.DATE, 14), ms.col("Fund Name", ms.TEXT, 24), ms.col("Added", ms.MONEY, 14),
        ms.col("Taken Out", ms.MONEY, 14), ms.col("Note", ms.TEXT, 30)], ENTRIES)
    fund_col, add_col, out_col = ms.rng(moves, "Fund Name"), ms.rng(moves, "Added"), ms.rng(moves, "Taken Out")
    n = FUNDS + 1

    def saved(r):
        return f'=IF(A{r}="","",SUMIF({fund_col},A{r},{add_col})-SUMIF({fund_col},A{r},{out_col}))'

    def left(r):
        return f'=IF(OR(A{r}="",B{r}=""),"",MAX(0,N(B{r})-N(D{r})))'

    def progress(r):
        return f'=IF(OR(A{r}="",N(B{r})=0),"",ROUND(MAX(0,MIN(1,N(D{r})/B{r}))*100,0))'

    def months(r):
        return (f'=IF(OR(A{r}="",NOT(ISNUMBER(C{r}))),"",IF(C{r}<=TODAY(),0,'
                f'(YEAR(C{r})-YEAR(TODAY()))*12+MONTH(C{r})-MONTH(TODAY())))')

    def per_month(r):
        return (f'=IF(OR(E{r}="",G{r}=""),"",IF(N(E{r})=0,0,IF(N(G{r})<=0,"date reached",'
                f'ROUNDUP(E{r}/G{r},2))))')

    funds = ms.table_tab("Funds", [
        ms.col("Fund Name", ms.TEXT, 24), ms.col("Goal Amount", ms.MONEY, 14), ms.col("Target Date", ms.DATE, 14),
        ms.col("Saved So Far", ms.MONEY, 14, saved), ms.col("Left to Save", ms.MONEY, 14, left),
        ms.col("Progress %", ms.NUMBER, 11, progress), ms.col("Months Left", ms.NUMBER, 11, months),
        ms.col("Save Each Month", ms.MONEY, 15, per_month),
        ms.col("Progress Bar", ms.TEXT, 24, lambda r: ms.progress_bar(f"F{r}", 12))], FUNDS)

    goal, saved_r, left_r = f"Funds!$B$2:$B${n}", f"Funds!$D$2:$D${n}", f"Funds!$E$2:$E${n}"
    lines = [("Summary", "title"), ("", "blank"), ("All funds", "head", ["Value"]),
             ("Total of all goals", "row", [(ms.MONEY, f"=SUM({goal})")]),
             ("Total saved", "row", [(ms.MONEY, f"=SUM({saved_r})")]),
             ("Total left to save", "row", [(ms.MONEY, f"=SUM({left_r})")]),
             ("Overall progress %", "row", [(ms.NUMBER, '=IF(B4=0,"",ROUND(MIN(1,B5/B4)*100,0))')]),
             ("Overall progress bar", "row", [(ms.TEXT, ms.progress_bar("B7"))]),
             ("Funds reached", "row", [(ms.NUMBER, f'=COUNTIFS({goal},">0",{left_r},0)')]),
             ("Funds in progress", "row", [(ms.NUMBER, f'=COUNTIF({left_r},">0")')]),
             ("Save each month for all funds", "row", [(ms.MONEY, f"=SUM(Funds!$H$2:$H${n})")]),
             ("", "blank"), ("Month (2027)", "head", ["Added", "Taken Out", "Net Saved"])]
    first = len(lines) + 1
    date_col = ms.rng(moves, "Date")
    for m, month in enumerate(ms.MONTHS, 1):
        r = first + m - 1
        lines.append((month, "row", [(ms.MONEY, "=" + ms.sum_in_month(add_col, date_col, 2027, m)),
                                     (ms.MONEY, "=" + ms.sum_in_month(out_col, date_col, 2027, m)),
                                     (ms.MONEY, f"=B{r}-C{r}")]))
    lines.append(("Change 2027 in these formulas to reuse the sheet next year.", "text"))
    summary = ms.form_tab("Summary", lines, widths=(36, 18, 18, 18))

    start = ms.start_here(title, [
        "Funds tab: one row per goal or sinking fund (car repairs, holidays, gifts, a new laptop...) with the "
        "goal amount and the date you want it by.",
        "Deposits tab: each time you move money into or out of a fund, add the date, the exact fund name and "
        "the amount.",
        "Funds tab: saved so far, left to save, progress %, months left and how much to save each month fill "
        "in by themselves.",
        "Summary tab: totals for all funds, overall progress bar and a month-by-month view."],
        ["A sinking fund is money you set aside a little at a time for a cost you know is coming."])
    return {"format": FORMAT, "title": title, "accent": "#a0662a",
            "band": "SINKING FUNDS + SAVINGS GOALS · EXCEL + GOOGLE SHEETS",
            "tagline": "Every goal with saved so far, left to save and what to set aside each month",
            "tabs": [start, funds, moves, summary],
            "previews": [{"tab": "Funds", "caption": "Funds: progress %, months left and save-each-month are formulas"},
                         {"tab": "Deposits", "caption": "Deposits: one line each time you add or take out money"},
                         {"tab": "Summary", "caption": "Summary: total saved, overall progress bar and monthly view"}],
            "features": ["Saved so far per fund from the Deposits tab (added minus taken out)",
                         "Left to save, progress % and a progress bar for every fund",
                         "Months left to the target date and how much to save each month",
                         "Summary: total goals, total saved, overall progress and funds reached",
                         "Month-by-month added, taken out and net saved"]}


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    return ms.build(design(spec), out_dir)
