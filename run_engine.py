#!/usr/bin/env python3
import sys
import os

# Ensure current directory is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine

if __name__ == "__main__":
    print("🌶️ STARTING CHILLI GAVVA AUTONOMOUS AI ENTERPRISE...")
    corp = engine.CEOOrchestrator()
    corp.run_corporate_cycle()
    print("✨ Corporate Cycle Completed Successfully!")
