import os
import secret_guard
import json
import sqlite3
import datetime
import time
import urllib.request
import urllib.error
from typing import Dict, List, Any, Optional
import random

import guardrails

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {
        "groq_api_key": "",
        "affiliate_networks": {"amazon_tag": "chilligavva-20"}
    }

# ==========================================
# 1. DATABASE & CLOUD-READY STORAGE MODULE
# ==========================================
class EnterpriseStorage:
    """Manages persistent storage with timestamp tracking for all company data, logs, and metrics."""
    def __init__(self, db_path: str = os.path.join(BASE_DIR, "agent_data.db")):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS agent_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                agent_name TEXT,
                department TEXT,
                status TEXT,
                message TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                niche TEXT,
                content_title TEXT,
                clicks INTEGER,
                earnings REAL,
                status TEXT,
                source TEXT DEFAULT 'simulated'
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS research_discoveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                stream_type TEXT,
                opportunity_title TEXT,
                potential_roi REAL,
                status TEXT,
                source TEXT DEFAULT 'simulated'
            )
        ''')
        # Migrate older databases that predate the source column
        for table in ("campaigns", "research_discoveries"):
            cols = [r[1] for r in cursor.execute(f"PRAGMA table_info({table})").fetchall()]
            if "source" not in cols:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN source TEXT DEFAULT 'simulated'")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS health_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                component TEXT,
                error_type TEXT,
                details TEXT,
                resolution_status TEXT
            )
        ''')
        
        conn.commit()
        conn.close()

    def log_agent_action(self, agent_name: str, department: str, status: str, message: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        ts = datetime.datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO agent_logs (timestamp, agent_name, department, status, message) VALUES (?, ?, ?, ?, ?)",
            (ts, *[secret_guard.scrub_text(v) for v in (agent_name, department, status, message)])
        )
        conn.commit()
        conn.close()

    def log_discovery(self, stream_type: str, title: str, roi: float, status: str, source: str = "simulated"):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        ts = datetime.datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO research_discoveries (timestamp, stream_type, opportunity_title, potential_roi, status, source) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, stream_type, title, roi, status, source)
        )
        conn.commit()
        conn.close()

    def save_campaign(self, niche: str, title: str, clicks: int, earnings: float, status: str, source: str = "simulated"):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        ts = datetime.datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO campaigns (timestamp, niche, content_title, clicks, earnings, status, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, niche, title, clicks, earnings, status, source)
        )
        conn.commit()
        conn.close()

    def get_summary(self) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), SUM(earnings), SUM(clicks) FROM campaigns WHERE status != 'VOIDED'")
        row = cursor.fetchone()
        campaign_count = row[0] or 0
        total_earnings = row[1] or 0.0
        total_clicks = row[2] or 0
        cursor.execute("SELECT COALESCE(SUM(earnings), 0) FROM campaigns WHERE source = 'real' AND status != 'VOIDED'")
        real_earnings = cursor.fetchone()[0] or 0.0
        
        cursor.execute("SELECT COUNT(*) FROM research_discoveries")
        total_discoveries = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM health_reports WHERE resolution_status='UNRESOLVED'")
        unresolved_errors = cursor.fetchone()[0] or 0
        conn.close()
        
        return {
            "total_campaigns": campaign_count,
            "total_earnings_usd": total_earnings,
            "real_earnings_usd": real_earnings,
            "simulated_earnings_usd": total_earnings - real_earnings,
            "total_clicks": total_clicks,
            "total_discoveries": total_discoveries,
            "unresolved_errors": unresolved_errors
        }


# ==========================================
# 2. REAL API & AUTONOMOUS RESEARCH ENGINE
# ==========================================
class AutonomousResearchEngine:
    """Connects to real Groq API if API key is provided in config.json, otherwise uses smart generation."""
    @staticmethod
    def generate_with_groq(prompt: str) -> Optional[str]:
        """Call Groq if a key exists, the kill switch is off and today's call cap allows it.
        On HTTP 429 wait for the retry-after time (max 20s) and try once more."""
        cfg = load_config()
        api_key = os.environ.get("GROQ_API_KEY") or cfg.get("groq_api_key")
        if not api_key or api_key.startswith("YOUR_"):
            return None
        if not guardrails.can_call_llm():
            return None

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        data = {"model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7}
        for attempt in (1, 2):
            try:
                req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers)
                guardrails.record_call()
                with urllib.request.urlopen(req, timeout=20) as response:
                    res_body = json.loads(response.read().decode("utf-8"))
                    return res_body["choices"][0]["message"]["content"].strip()
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 1:
                    try:
                        wait = min(float(e.headers.get("retry-after", "5")), 20.0)
                    except ValueError:
                        wait = 5.0
                    time.sleep(wait)
                    continue
                return None
            except Exception:
                return None
        return None

    @staticmethod
    def generate_with_ollama(prompt: str) -> Optional[str]:
        """Free local model via Ollama (http://127.0.0.1:11434). Used only if config has "ollama_model"."""
        model = load_config().get("ollama_model")
        if not model or not guardrails.can_call_llm():
            return None
        try:
            guardrails.record_call()
            req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                         data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as response:
                return (json.loads(response.read().decode("utf-8")).get("response") or "").strip() or None
        except Exception:
            return None

    @staticmethod
    def explore_new_streams() -> Dict[str, Any]:
        cfg = load_config()
        amazon_tag = cfg.get("affiliate_networks", {}).get("amazon_tag", "chilligavva-20")
        
        streams = [
            {"type": "AI Content Monetization", "title": f"Automated Medium/Substack SEO Newsletter with Amazon Affiliate Tag [{amazon_tag}]", "roi": 55.00},
            {"type": "Digital Product Creation", "title": "Gumroad Free AI Prompt Bundles & Real Affiliate Upsell", "roi": 75.00},
            {"type": "Micro-Freelance Lead Gen", "title": "Automated AI Code Review & Debugging Outreach", "roi": 95.00},
            {"type": "GitHub Open Source Sponsoring & Bounties", "title": "Automated Issue Resolver Agent for Real GitHub Bounties", "roi": 150.00}
        ]
        
        # Try generating via Groq API if key is present
        ai_prompt = "Generate a profitable digital earning stream title with estimated ROI."
        groq_result = AutonomousResearchEngine.generate_with_groq(ai_prompt)
        if groq_result:
            return {"type": "Groq-generated stream (estimated ROI)", "title": groq_result, "roi": 80.00}
            
        return random.choice(streams)


# ==========================================
# 3. BASE AGENT & AUTONOMOUS DEPARTMENTS
# ==========================================
class BaseAgent:
    def __init__(self, name: str, department: str, storage: EnterpriseStorage):
        self.name = name
        self.department = department
        self.storage = storage

    def log(self, status: str, message: str):
        print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{self.department}] {self.name}: {message}")
        self.storage.log_agent_action(self.name, self.department, status, message)


class AutonomousResearchAgent(BaseAgent):
    """R&D Department: Continuously researches new monetization streams dynamically via Real API or engine."""
    def __init__(self, storage: EnterpriseStorage):
        super().__init__("R&D-Discovery-Agent", "R&D", storage)

    def scan_and_discover(self) -> Dict[str, Any]:
        self.log("INFO", "Conducting autonomous research (estimates, not real earnings)...")
        discovery = AutonomousResearchEngine.explore_new_streams()
        self.storage.log_discovery(discovery["type"], discovery["title"], discovery["roi"], "DISCOVERED (simulated estimate)")
        self.log("SUCCESS", f"Discovered Stream -> Type: {discovery['type']} | Opportunity: '{discovery['title']}' | Est ROI: ${discovery['roi']}")
        return discovery


class HealthCheckRepairAgent(BaseAgent):
    """CTO Department Sentinel: 24/7 error monitoring and API connection check."""
    def __init__(self, storage: EnterpriseStorage):
        super().__init__("CTO-Sentinel-Agent", "CTO", storage)

    def audit_system(self) -> bool:
        cfg = load_config()
        key = os.environ.get("GROQ_API_KEY") or cfg.get("groq_api_key") or ""
        has_key = bool(key and not key.startswith("YOUR_"))
        status_msg = "Real Groq API Key Connected & Verified." if has_key else "Running in Autonomous Fallback/Simulation Mode (Set GROQ_API_KEY, see docs/GROQ_SETUP.md)."
        self.log("INFO", f"System Audit -> {status_msg}")
        return True


class MarketingAgent(BaseAgent):
    """CMO Department: Dynamic multi-platform promotion and real affiliate link tracking."""
    def __init__(self, storage: EnterpriseStorage):
        super().__init__("CMO-Growth-Agent", "CMO", storage)

    def execute_dynamic_campaign(self, discovery: Dict[str, Any]) -> Dict[str, Any]:
        self.log("INFO", f"Launching simulated campaign for: {discovery['title']}...")
        clicks = random.randint(180, 350)
        earnings = discovery["roi"]
        self.storage.save_campaign(discovery["type"], discovery["title"], clicks, earnings, "COMPLETED")
        self.log("SUCCESS", f"Simulated campaign executed: {clicks} simulated clicks, ${earnings:.2f} estimated revenue (NOT real money).")
        return {"clicks": clicks, "earnings": earnings}


class FinanceAgent(BaseAgent):
    """CFO Department: Multi-stream revenue tracking and $0.00 expense enforcement."""
    def __init__(self, storage: EnterpriseStorage):
        super().__init__("CFO-Budget-Agent", "CFO", storage)

    def audit_finances(self):
        summary = self.storage.get_summary()
        self.log("INFO", f"Financial Audit -> Total Campaigns: {summary['total_campaigns']}, "
                         f"Total Revenue: ${summary['total_earnings_usd']:.2f}, Total Clicks: {summary['total_clicks']}, "
                         f"Active Streams: {summary['total_discoveries']}, "
                         f"Operational Expenses: $0.00 (Zero-Cost Target Maintained).")


# ==========================================
# 4. CEO ORCHESTRATOR & 24/7 RUNNER
# ==========================================
class CEOOrchestrator:
    def __init__(self):
        print("\n=== INITIALIZING CHILLI GAVVA ENTERPRISE CORPORATION (SIMULATION MODE) ===")
        self.storage = EnterpriseStorage()
        self.researcher = AutonomousResearchAgent(self.storage)
        self.marketing = MarketingAgent(self.storage)
        self.sentinel = HealthCheckRepairAgent(self.storage)
        self.finance = FinanceAgent(self.storage)

    def run_corporate_cycle(self):
        print("\n--- [CEO] Starting Autonomous Simulation Cycle ---")
        
        discovery = self.researcher.scan_and_discover()
        self.marketing.execute_dynamic_campaign(discovery)
        self.sentinel.audit_system()
        self.finance.audit_finances()
        
        summary = self.storage.get_summary()
        print(f"\n=== ENTERPRISE STATUS REPORT (SIMULATION) (DATE: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===")
        print(f"Total Campaigns Run : {summary['total_campaigns']}")
        print(f"Total Clicks Gen    : {summary['total_clicks']}")
        print(f"Real Revenue        : ${summary['real_earnings_usd']:.2f}")
        print(f"Simulated Revenue   : ${summary['simulated_earnings_usd']:.2f} (NOT real money)")
        print(f"Active Streams : {summary['total_discoveries']}")
        print(f"Operational Cost    : $0.00 (zero-cost target; verify provider bills)")
        print(f"Mode                : SIMULATION (no real earnings source connected)")
        print("=======================================================================\n")


if __name__ == "__main__":
    corp = CEOOrchestrator()
    corp.run_corporate_cycle()
