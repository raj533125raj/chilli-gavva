"""One command to start everything on this computer: the worker AND the dashboard.

  python3 start.py              # worker in the background + dashboard at http://127.0.0.1:8787
  python3 start.py --no-browser
Press Ctrl+C to stop both.
"""
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer

import serve

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    worker = subprocess.Popen([sys.executable, os.path.join(BASE_DIR, "worker_daemon.py")], cwd=BASE_DIR)
    time.sleep(1.0)
    if worker.poll() is not None:
        print("(Worker was not started here: another worker is probably already running. Dashboard only.)")
    server = ThreadingHTTPServer(("127.0.0.1", serve.PORT), serve.Handler)
    url = f"http://127.0.0.1:{serve.PORT}"
    print(f"Dashboard: {url}   (Ctrl+C stops the dashboard and the worker)")
    if "--no-browser" not in argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()


if __name__ == "__main__":
    main(sys.argv[1:])
