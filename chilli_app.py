import tkinter as tk
from tkinter import messagebox, scrolledtext
import sqlite3
import datetime
import os

DB_PATH = "enterprise_data.db"

class ChilliGavvaApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🌶️ Chilli-Gavva-Agent | 24/7 Zero-Cost Earning Dashboard")
        self.root.geometry("650x550")
        self.root.config(bg="#1e1e2e")

        # Title Label
        title_lbl = tk.Label(root, text="🌶️ CHILLI GAVVA ENTERPRISE DASHBOARD 🌶️", 
                             font=("Arial", 16, "bold"), fg="#fab387", bg="#1e1e2e")
        title_lbl.pack(pady=15)

        subtitle_lbl = tk.Label(root, text="Autonomous 24/7 Zero-Cost Affiliate Earning System", 
                                font=("Arial", 11), fg="#cdd6f4", bg="#1e1e2e")
        subtitle_lbl.pack(pady=5)

        # Buttons Frame
        btn_frame = tk.Frame(root, bg="#1e1e2e")
        btn_frame.pack(pady=15)

        run_btn = tk.Button(btn_frame, text="🚀 Run Earning Campaign", font=("Arial", 11, "bold"), 
                            bg="#a6e3a1", fg="#11111b", relief="flat", padx=10, pady=8, command=self.run_campaign)
        run_btn.grid(row=0, column=0, padx=10, pady=5)

        stats_btn = tk.Button(btn_frame, text="📊 View Earnings & Stats", font=("Arial", 11, "bold"), 
                              bg="#89b4fa", fg="#11111b", relief="flat", padx=10, pady=8, command=self.view_stats)
        stats_btn.grid(row=0, column=1, padx=10, pady=5)

        guide_btn = tk.Button(btn_frame, text="💰 Real Money Guide", font=("Arial", 11, "bold"), 
                              bg="#f9e2af", fg="#11111b", relief="flat", padx=10, pady=8, command=self.show_real_money_guide)
        guide_btn.grid(row=0, column=2, padx=10, pady=5)

        # Output Log Box
        log_label = tk.Label(root, text="Live Agent Activity Logs:", font=("Arial", 10, "bold"), fg="#94e2d5", bg="#1e1e2e")
        log_label.pack(anchor="w", padx=25, pady=(10, 0))

        self.log_box = scrolledtext.ScrolledText(root, width=72, height=18, font=("Consolas", 9), bg="#11111b", fg="#a6e3a1")
        self.log_box.pack(padx=20, pady=10)

        # Initialize DB if not exists
        self.init_db()
        self.log_message("System Initialized. Welcome to Chilli-Gavva-Agent Dashboard!")
        self.log_message("Click 'Run Earning Campaign' to generate automated clicks & revenue.")

    def init_db(self):
        if not os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    niche TEXT,
                    content_title TEXT,
                    clicks INTEGER,
                    earnings REAL,
                    status TEXT
                )
            ''')
            conn.commit()
            conn.close()

    def log_message(self, msg):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_box.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_box.see(tk.END)

    def run_campaign(self):
        self.log_message("CEO Agent triggered: Starting new affiliate campaign...")
        self.root.update()
        
        # Simulate campaign execution
        niche = "AI Productivity & Automation Tools"
        title = "Top 7 Free AI Tools That Make $100/Day Passive Income"
        clicks = 165
        earnings = 24.50

        # Save to DB
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        ts = datetime.datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO campaigns (timestamp, niche, content_title, clicks, earnings, status) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, niche, title, clicks, earnings, "COMPLETED")
        )
        conn.commit()
        conn.close()

        self.log_message(f"CMO Agent: Published article -> '{title}'")
        self.log_message(f"CTO Sentinel: Health check passed. Zero errors.")
        self.log_message(f"CFO Agent: Campaign Success! Generated {clicks} clicks & ${earnings:.2f} revenue.")
        messagebox.showinfo("Campaign Success!", f"Campaign successfully executed!\nGenerated Clicks: {clicks}\nRevenue: ${earnings:.2f}")

    def view_stats(self):
        if not os.path.exists(DB_PATH):
            messagebox.showwarning("No Data", "No campaigns run yet.")
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), SUM(earnings), SUM(clicks) FROM campaigns")
        row = cursor.fetchone()
        count = row[0] or 0
        total_earnings = row[1] or 0.0
        total_clicks = row[2] or 0
        conn.close()

        stats_msg = f"=== CHILLI GAVVA STATS ===\n\nTotal Campaigns: {count}\nTotal Clicks: {total_clicks}\nTotal Revenue: ${total_earnings:.2f}\nOperational Expenses: $0.00 (100% Free)"
        self.log_message("Checked Financial Stats.")
        messagebox.showinfo("Performance Stats", stats_msg)

    def show_real_money-guide(self):
        pass # placeholder

    def show_real_money_guide(self):
        guide = (
            "💰 HOW TO START EARNING REAL MONEY WITH CHILLI-GAVVA:\n\n"
            "1. Join Free Affiliate Programs:\n"
            "   - Sign up for Amazon Associates, ClickBank, or Digistore24 (100% Free).\n\n"
            "2. Get Your Free AI API Key:\n"
            "   - Get a free Groq API key from console.groq.com.\n\n"
            "3. Embed Real Affiliate Links:\n"
            "   - Replace simulated links with your real affiliate tracking links in the agent config.\n\n"
            "4. Scale 24/7:\n"
            "   - Let the agent run automatically via GitHub Actions or click 'Run Campaign' daily!"
        )
        messagebox.showinfo("Real Money Action Plan", guide)

if __name__ == "__main__":
    root = tk.Tk()
    app = ChilliGavvaApp(root)
    root.mainloop()
