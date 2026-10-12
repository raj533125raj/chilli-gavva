"""Chilli Gavva Agent - desktop dashboard.

Connected to engine.py: the Run button runs the real engine cycle and the
stats come from the same database. Every number is labelled real or simulated.
"""
import os
import sys
import sqlite3
import datetime
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import engine  # noqa: E402  (backend lives next to this file)

DB_PATH = os.path.join(BASE_DIR, "agent_data.db")


class ChilliGavvaApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Chilli-Gavva-Agent | Dashboard (SIMULATION MODE)")
        self.root.geometry("680x580")
        self.root.config(bg="#1e1e2e")

        tk.Label(root, text="CHILLI GAVVA AGENT DASHBOARD", font=("Arial", 16, "bold"),
                 fg="#fab387", bg="#1e1e2e").pack(pady=(15, 2))
        tk.Label(root, text="Real money earned so far: $0.00  |  Simulated numbers are NOT real income",
                 font=("Arial", 10), fg="#f38ba8", bg="#1e1e2e").pack(pady=(0, 10))

        btns = tk.Frame(root, bg="#1e1e2e")
        btns.pack(pady=10)
        self._button(btns, "Run Engine Cycle", "#a6e3a1", self.run_cycle, 0)
        self._button(btns, "View Stats", "#89b4fa", self.view_stats, 1)
        self._button(btns, "Real Money Guide", "#f9e2af", self.show_guide, 2)

        tk.Label(root, text="Agent activity log (latest first):", font=("Arial", 10, "bold"),
                 fg="#94e2d5", bg="#1e1e2e").pack(anchor="w", padx=25, pady=(8, 0))
        self.log_box = scrolledtext.ScrolledText(root, width=80, height=20, font=("Consolas", 9),
                                                 bg="#11111b", fg="#a6e3a1")
        self.log_box.pack(padx=20, pady=10)
        self.refresh_log()

    def _button(self, parent, text, color, command, column):
        tk.Button(parent, text=text, font=("Arial", 11, "bold"), bg=color, fg="#11111b",
                  relief="flat", padx=10, pady=8, command=command).grid(row=0, column=column, padx=10)

    def refresh_log(self):
        self.log_box.delete("1.0", tk.END)
        if not os.path.exists(DB_PATH):
            self.log_box.insert(tk.END, "No database yet. Run an engine cycle first.\n")
            return
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT timestamp, agent_name, status, message FROM agent_logs ORDER BY id DESC LIMIT 100"
        ).fetchall()
        conn.close()
        for ts, agent, status, msg in rows:
            self.log_box.insert(tk.END, f"[{ts[:19]}] {agent} [{status}] {msg}\n")

    def run_cycle(self):
        if getattr(self, "_running", False):
            return
        self._running = True
        self.log_box.insert(tk.END, f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] Running engine cycle in the background...\n")

        def work():
            error = None
            try:
                engine.CEOOrchestrator().run_corporate_cycle()
            except Exception as e:  # report the failure instead of crashing the window
                error = str(e)
            self.root.after(0, lambda: self._cycle_done(error))

        threading.Thread(target=work, daemon=True).start()

    def _cycle_done(self, error):
        self._running = False
        if error:
            messagebox.showerror("Engine error", error)
            return
        self.refresh_log()
        self.view_stats(show_popup=False)

    def view_stats(self, show_popup=True):
        summary = engine.EnterpriseStorage(DB_PATH).get_summary()
        text = (
            f"Campaigns run: {summary['total_campaigns']}\n"
            f"Clicks (simulated): {summary['total_clicks']}\n"
            f"REAL revenue: ${summary['real_earnings_usd']:.2f}\n"
            f"Simulated revenue (NOT real money): ${summary['simulated_earnings_usd']:.2f}\n"
            f"Operational cost: $0.00 (zero-cost target)"
        )
        if show_popup:
            messagebox.showinfo("Stats", text)
        else:
            self.log_box.insert(tk.END, f"[stats] real=${summary['real_earnings_usd']:.2f} "
                                        f"simulated=${summary['simulated_earnings_usd']:.2f}\n")

    def show_guide(self):
        messagebox.showinfo("Real Money Guide", (
            "Steps to real income (none are done yet):\n\n"
            "1. Pick ONE income stream and validate it with real visitors/clients.\n"
            "2. Connect a real earnings source (affiliate dashboard, invoices, payments).\n"
            "3. Record real earnings with source = 'real' in the database.\n"
            "4. Only then trust the revenue numbers in this dashboard."
        ))


if __name__ == "__main__":
    root = tk.Tk()
    ChilliGavvaApp(root)
    root.mainloop()
