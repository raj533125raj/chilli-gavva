"""Debt snowball / avalanche payoff tracker (.xlsx + CSV): payoff order, months left, progress."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

import money_sheets as ms

FORMAT = "debt_payoff"
KEYWORDS = [
    "debt snowball tracker", "debt snowball spreadsheet", "debt avalanche tracker", "debt snowball", "debt avalanche", "debt payoff tracker", "debt payoff spreadsheet",
    "debt payoff planner", "debt tracker spreadsheet", "snowball method tracker", "avalanche method tracker",
    "pay off debt tracker", "debt free tracker", "credit card payoff tracker", "loan payoff tracker",
]
DEFAULT_TITLE = "Debt Snowball and Avalanche Payoff Tracker"
DEBTS, PAYMENTS = 20, 300


def normalize_spec(spec: Any) -> Dict[str, Any]:
    return {"title": ms.normalize_title(spec, DEFAULT_TITLE)}


def design(spec: Any = None) -> Dict[str, Any]:
    title = normalize_spec(spec)["title"]
    payments = ms.table_tab("Payments", [
        ms.col("Date", ms.DATE, 14), ms.col("Debt Name", ms.TEXT, 26), ms.col("Amount Paid", ms.MONEY, 16),
        ms.col("Note", ms.TEXT, 32)], PAYMENTS)
    pay_name, pay_amount = ms.rng(payments, "Debt Name"), ms.rng(payments, "Amount Paid")
    n = DEBTS + 1

    def paid(r):
        return f'=IF(A{r}="","",SUMIF({pay_name},A{r},{pay_amount}))'

    def left(r):
        return f'=IF(A{r}="","",MAX(0,N(B{r})-N(F{r})))'

    def progress(r):
        return f'=IF(OR(A{r}="",N(B{r})=0),"",ROUND(MIN(1,N(F{r})/B{r})*100,0))'

    def snowball(r):   # smallest balance left first; paid-off debts drop out; ties keep row order
        g = f"$G$2:$G${n}"
        return (f'=IF(OR(A{r}="",N(G{r})<=0),"",SUMPRODUCT(ISNUMBER({g})*({g}>0)*({g}<G{r}))'
                f'+SUMPRODUCT(ISNUMBER($G$2:G{r})*($G$2:G{r}>0)*($G$2:G{r}=G{r})))')

    def avalanche(r):  # highest interest rate first (an empty rate counts as 0); ties keep row order
        g, c = f"$G$2:$G${n}", f"$C$2:$C${n}"
        return (f'=IF(OR(A{r}="",N(G{r})<=0),"",SUMPRODUCT(ISNUMBER({g})*({g}>0)*(({c}+0)>N(C{r})))'
                f'+SUMPRODUCT(ISNUMBER($G$2:G{r})*($G$2:G{r}>0)*(($C$2:C{r}+0)=N(C{r}))))')

    def months(r):     # months left paying only the minimum (NPER), or a clear note
        return (f'=IF(OR(A{r}="",N(G{r})<=0),"",IF(N(D{r})<=0,"add a minimum payment",'
                f'IF(N(C{r})=0,ROUNDUP(G{r}/D{r},0),IFERROR(ROUNDUP(NPER(C{r}/1200,-D{r},G{r}),0),'
                f'"payment below interest"))))')

    debts = ms.table_tab("Debts", [
        ms.col("Debt Name", ms.TEXT, 26), ms.col("Starting Balance", ms.MONEY, 16),
        ms.col("Interest Rate % (APR)", ms.NUMBER, 14), ms.col("Minimum Payment", ms.MONEY, 16),
        ms.col("Due Day", ms.NUMBER, 10), ms.col("Paid So Far", ms.MONEY, 14, paid),
        ms.col("Balance Left", ms.MONEY, 14, left), ms.col("Progress %", ms.NUMBER, 11, progress),
        ms.col("Snowball Order", ms.NUMBER, 11, snowball), ms.col("Avalanche Order", ms.NUMBER, 11, avalanche),
        ms.col("Months Left (minimum only)", ms.NUMBER, 16, months)], DEBTS)

    # Payoff plan: order 1..20 for the chosen method; the extra payment goes to order 1.
    plan_lines = [("Payoff Plan", "title"), ("", "blank"),
                  ("Method (Snowball or Avalanche)", "input", ["Snowball", "Avalanche"]),
                  ("Extra payment each month (on top of minimums)", "input"), ("", "blank"),
                  ("Order", "head", ["Debt", "Balance Left", "Minimum Payment", "Pay This Month",
                                     "Months Left at This Payment"])]
    first = len(plan_lines) + 1
    for k in range(1, DEBTS + 1):
        r = first + k - 1
        match = (f'IF($B$3="Avalanche",MATCH({k},Debts!$J$2:$J${n},0),MATCH({k},Debts!$I$2:$I${n},0))')
        pick = lambda column: f'IFERROR(INDEX(Debts!${column}$2:${column}${n},{match}),"")'
        plan_lines.append((f"{k}", "row", [
            (ms.TEXT, f"={pick('A')}"), (ms.MONEY, f"={pick('G')}"), (ms.MONEY, f"={pick('D')}"),
            (ms.MONEY, f'=IF(B{r}="","",N(D{r})+IF({k}=1,N($B$4),0))'),
            (ms.NUMBER, f'=IF(OR(B{r}="",N(E{r})<=0),"",IFERROR(ROUNDUP(NPER(N(INDEX(Debts!$C$2:$C${n},{match}))'
                        f'/1200,-E{r},C{r}),0),"payment below interest"))')]))
    plan_lines += [("", "blank"),
                   ("When order 1 is paid off it drops out and the next debt moves up: add its old minimum "
                    "to your extra payment (the snowball).", "text")]
    plan = ms.form_tab("Payoff Plan", plan_lines, widths=(12, 28, 16, 16, 16, 24))

    bal, left_r = f"Debts!$B$2:$B${n}", f"Debts!$G$2:$G${n}"
    summary_lines = [("Summary", "title"), ("", "blank"), ("Totals", "head", ["Value"]),
                     ("Total starting debt", "row", [(ms.MONEY, f"=SUM({bal})")]),
                     ("Total paid so far", "row", [(ms.MONEY, f"=SUM({pay_amount})")]),
                     ("Total balance left", "row", [(ms.MONEY, f"=SUM({left_r})")]),
                     ("Progress % (paid / starting debt)", "row",
                      [(ms.NUMBER, f'=IF(SUM({bal})=0,"",ROUND(MIN(1,SUM(Debts!$F$2:$F${n})/SUM({bal}))*100,0))')]),
                     ("Progress bar", "row", [(ms.TEXT, ms.progress_bar("B7"))]),
                     ("Debts paid off", "row", [(ms.NUMBER, f'=COUNTIFS({bal},">0",{left_r},0)')]),
                     ("Debts still open", "row", [(ms.NUMBER, f'=COUNTIF({left_r},">0")')]),
                     ("Total minimum payments each month", "row",
                      [(ms.MONEY, f'=SUMIF({left_r},">0",Debts!$D$2:$D${n})')]),
                     ("Longest months left (minimum only)", "row",
                      [(ms.NUMBER, f'=IF(COUNT(Debts!$K$2:$K${n})=0,"",MAX(Debts!$K$2:$K${n}))')]),
                     ("Next debt to focus on (your method)", "row",
                      [(ms.TEXT, "='Payoff Plan'!B7")]),
                     ("", "blank"), ("Paid per month", "head", ["Paid"])]
    year = 2027
    for m, month in enumerate(ms.MONTHS, 1):
        summary_lines.append((month, "row", [(ms.MONEY, "=" + ms.sum_in_month(
            pay_amount, ms.rng(payments, "Date"), year, m))]))
    summary_lines.append(("Change the year in these formulas to reuse the sheet next year.", "text"))
    summary = ms.form_tab("Summary", summary_lines, widths=(40, 26))

    start = ms.start_here(title, [
        "Debts tab: one row per debt with its name, starting balance, interest rate and minimum payment.",
        "Payments tab: every time you pay, add the date, the exact debt name and the amount.",
        "Payoff Plan tab: pick Snowball (smallest balance first) or Avalanche (highest rate first) and type "
        "any extra amount you can add each month.",
        "Summary tab: totals, progress % and a progress bar update by themselves.",
        "Pay the minimum on every debt and put the extra on order 1 until it is gone, then roll it to the next."])
    return {"format": FORMAT, "title": title, "accent": "#2f6f62", "band": "DEBT PAYOFF TRACKER · EXCEL + GOOGLE SHEETS",
            "tagline": "Snowball or avalanche order, months left and progress in one workbook",
            "tabs": [start, debts, payments, plan, summary],
            "previews": [{"tab": "Debts", "caption": "Debts tab: payoff order, balance left and months left are formulas"},
                         {"tab": "Payoff Plan", "caption": "Payoff Plan: choose Snowball or Avalanche, the order updates"},
                         {"tab": "Summary", "caption": "Summary: totals, progress % and a progress bar"}],
            "features": ["Paid So Far and Balance Left per debt from your Payments tab (SUMIF)",
                         "Snowball order (smallest balance first) and Avalanche order (highest rate first)",
                         "Months left at the minimum payment (NPER), with a clear note if a payment is too low",
                         "Payoff Plan: the chosen method's order, this month's payment and months left",
                         "Summary: total debt, total paid, balance left, progress % and a progress bar"]}


def build(spec: Dict[str, Any], out_dir: Union[str, Path]) -> List[str]:
    return ms.build(design(spec), out_dir)
