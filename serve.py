#!/usr/bin/env python3
"""
Liten lokal server för dashboarden så att manuella taggändringar sparas
AUTOMATISKT till overrides.csv (ingen nedladdning behövs).

    python analyze.py      # bygg rapporten först
    python serve.py        # starta servern
    # öppna http://localhost:8000

Varje ändring du gör i dashboarden skrivs direkt till
iq_tiktok_data/overrides.csv. Kör analyze.py igen när du vill bygga om
rapporten (t.ex. efter nytt skrap/analys) – dina rättelser tillämpas alltid.

Servern lyssnar bara på localhost och serverar enbart iq_tiktok_data/.
"""

import csv
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJECT_DIR, "iq_tiktok_data")
OVERRIDES = os.path.join(DATA, "overrides.csv")
PORT = int(os.environ.get("IQ_PORT", "8000") or 8000)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DATA, **k)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.path = "/iq_analys.html"
        return super().do_GET()

    def do_POST(self):
        if self.path.rstrip("/") != "/__overrides":
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            assert isinstance(data, dict)
        except Exception:
            self.send_error(400, "bad json")
            return
        # data = {video_id: {field: value}}  ->  overrides.csv
        tmp = OVERRIDES + ".tmp"
        rows = 0
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["video_id", "field", "value"])
            for vid, fields in data.items():
                if not isinstance(fields, dict):
                    continue
                for fld, val in fields.items():
                    w.writerow([vid, fld, val])
                    rows += 1
        os.replace(tmp, OVERRIDES)
        body = json.dumps({"ok": True, "rows": rows}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    if not os.path.isfile(os.path.join(DATA, "iq_analys.html")):
        print("Hittar inte iq_tiktok_data/iq_analys.html – kör analyze.py först.")
        return
    print(f"Dashboard:  http://localhost:{PORT}")
    print(f"Taggändringar sparas automatiskt till {OVERRIDES}")
    print("Ctrl+C för att stoppa.")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
