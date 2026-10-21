# 🏗️ Chilli-Gavva-Agent Project Architecture
**Last Updated:** 2026-10-02 11:12:09

## 📁 Core Files Location (`/Users/vudathagroup/`)
1. **`chilli_app.py`**
   - **Type:** Python Tkinter GUI Desktop Application.
   - **Title:** `🌶️ Chilli-Gavva-Agent | 24/7 Zero-Cost Earning Dashboard`
   - **Features:**
     - Interactive GUI with custom color palette (Catppuccin theme).
     - Buttons: `🚀 Run Earning Campaign`, `📊 View Earnings & Stats`, `💰 Real Money Guide`.
     - Live Activity Scrollable Log Box.
     - SQLite Database integration (`enterprise_data.db`).

2. **`ai_enterprise_corp.py`**
   - **Type:** Core Backend Enterprise Logic.
   - **Features:**
     - `EnterpriseStorage` class managing SQLite DB (`enterprise_data.db`).
     - Tables: `agent_logs`, `campaigns`, `health_reports`.
     - Multi-agent simulation (CEO, CMO, CTO, CFO agents).

3. **`enterprise_data.db`**
   - **Type:** SQLite Database file.
   - **Schema:**
     - `campaigns`: id, timestamp, niche, content_title, clicks, earnings, status.
     - `agent_logs`: id, timestamp, agent_name, department, status, message.
     - `health_reports`: id, timestamp, component, error_type, details, resolution_status.

## 💡 How It Works (Zero-Cost Affiliate Model)
- The agent simulates automated content creation and affiliate link click generation.
- Generates simulated revenue (private, see the office per campaign) and clicks (165 clicks).
- Real money integration plan: Replace simulated links with free affiliate networks (Amazon Associates, ClickBank, Digistore24) and connect Groq API for real-time article generation.
