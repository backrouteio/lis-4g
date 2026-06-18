"""
LEA Receiver Server — India 4G LTE LIS (FTP Version)
Runs on the LEA machine. Provides:
  1. FTP server on port 21     — receives HI3 CC content from LIS (plaintext, capturable)
  2. HTTP server on port 8443  — receives HI2 IRI events pushed by LIS
  3. HTTP server on port 8080  — serves hi1_lea.html portal

Usage:
    python lea_ftp_server.py

    Optional:
    python lea_ftp_server.py --ftp-port 21 --hi2-port 8443 --portal-port 8080

Directory layout (created automatically):
    cc_received/     — received HI3 CC files from LIS (via FTP)
    hi2_events/      — received HI2 IRI events as JSON files
"""
import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from ftplib import FTP_TLS, FTP
import io

logging.basicConfig(level=logging.INFO, format="%(asctime)s [LEA] %(message)s")
logger = logging.getLogger("LEA")

# ── Shared in-memory stores (for portal polling) ──────────────

_lock = threading.Lock()
_hi2_events:   list[dict] = []   # IRI events received via HI2
_cc_files:     list[dict] = []   # CC files received via FTP
_ftp_clients:  list[str]  = []   # Connected FTP clients log
_start_time    = time.time()


# ═══════════════════════════════════════════════════════════════
#  FTP SERVER  (built-in pyftpdlib or custom)
# ═══════════════════════════════════════════════════════════════

def _run_ftp_server(port: int, cc_dir: str):
    """
    Embedded FTP server using pyftpdlib.
    Accepts any username/password for the simulator (testing mode).
    Stores uploaded files in cc_dir.
    Plaintext FTP allows packet capture with tcpdump/Wireshark.
    """
    try:
        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.servers import FTPServer as PyFTPServer
    except ImportError:
        logger.error("pyftpdlib not installed. Run: pip install pyftpdlib --break-system-packages")
        return

    os.makedirs(cc_dir, exist_ok=True)

    class LEAFTPHandler(FTPHandler):
        def on_file_received(self, file):
            """Called when a file upload completes."""
            fname = os.path.basename(file)
            size = os.path.getsize(file) if os.path.exists(file) else -1
            logger.info("HI3 FTP: file received complete: %s size=%dB", fname, size)
            with _lock:
                for rec in _cc_files:
                    if rec.get("path") == file and rec.get("size") == "—":
                        rec["size"] = f"{size}B"
                        break

        def on_login(self, username):
            """Called when client logs in."""
            logger.info("HI3 FTP: auth user=%s (accepted — simulator mode)", username)
            with _lock:
                _ftp_clients.append(f"{username} @ {datetime.utcnow().isoformat()[:19]}")

        def on_disconnect(self, username):
            """Called when client disconnects."""
            logger.info("HI3 FTP: disconnected user=%s", username)

    # Allow any username/password
    authorizer = DummyAuthorizer()
    authorizer.add_user("lea", "lea", cc_dir, perm="elradfmw")  # All permissions
    authorizer.add_user("admin", "admin", cc_dir, perm="elradfmw")

    handler = LEAFTPHandler
    handler.authorizer = authorizer
    handler.permit_foreign_addresses = True

    try:
        server = PyFTPServer(("0.0.0.0", port), handler)
        logger.info("FTP server listening on 0.0.0.0:%d (cc_received: %s)", port, cc_dir)
        server.serve_forever()
    except Exception as e:
        logger.error("Cannot start FTP server on port %d: %s", port, e)
        return


# ═══════════════════════════════════════════════════════════════
#  HI2 HTTP RECEIVER  (receives IRI events from LIS IRI-MF)
# ═══════════════════════════════════════════════════════════════

def _run_hi2_server(port: int, hi2_dir: str):
    os.makedirs(hi2_dir, exist_ok=True)
    hi2_seq = [0]

    class HI2Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            logger.debug("HI2 HTTP: %s", format % args)

        def do_POST(self):
            client_ip = self.client_address[0]
            if self.path in ("/hi2/iri", "/hi2"):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    body   = self.rfile.read(length)
                    logger.debug("HI2 POST from %s: %d bytes raw=%s", client_ip, length, body[:500])
                    event  = json.loads(body)
                    ts     = datetime.utcnow().isoformat()
                    event.setdefault("received_at", ts)
                    event.setdefault("hi2_seq", hi2_seq[0])
                    hi2_seq[0] += 1
                    with _lock:
                        _hi2_events.append(event)
                    # Save to disk
                    fname  = f"hi2_{event.get('liid','unknown')}_{hi2_seq[0]:05d}_{ts[:10]}.json"
                    fpath  = os.path.join(hi2_dir, fname)
                    with open(fpath, "w") as f:
                        json.dump(event, f, indent=2)
                    logger.info("HI2: received IRI event from=%s LIID=%s type=%s seq=%s ne_source=%s saved=%s",
                                client_ip, event.get("liid","?"), event.get("event_type","?"),
                                event.get("seq_no", event.get("hi2_seq")), event.get("ne_source","?"), fname)
                    self.send_response(200)
                    self.send_header("Content-Type","application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok":True,"seq":hi2_seq[0]}).encode())
                except json.JSONDecodeError as e:
                    logger.warning("HI2 handler: invalid JSON from %s: %s", client_ip, e)
                    self.send_response(400); self.end_headers()
                except Exception as e:
                    logger.error("HI2 handler error from %s: %s", client_ip, e)
                    self.send_response(500)
                    self.end_headers()
            else:
                logger.warning("HI2 HTTP: unknown POST path=%s from=%s", self.path, client_ip)
                self.send_response(404); self.end_headers()

        def do_GET(self):
            client_ip = self.client_address[0]
            if self.path in ("/hi2/log", "/hi2/events"):
                with _lock:
                    data = list(reversed(_hi2_events[-200:]))
                logger.debug("HI2 HTTP: %s polled /hi2/log → %d event(s)", client_ip, len(data))
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Access-Control-Allow-Origin","*")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            elif self.path == "/health":
                data = {"status":"ok","uptime":int(time.time()-_start_time),
                        "hi2_events":len(_hi2_events),"cc_files":len(_cc_files)}
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Access-Control-Allow-Origin","*")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())
            else:
                logger.warning("HI2 HTTP: unknown GET path=%s from=%s", self.path, client_ip)
                self.send_response(404); self.end_headers()

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin","*")
            self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers","Content-Type")
            self.end_headers()

    server = HTTPServer(("0.0.0.0", port), HI2Handler)
    logger.info("HI2 receiver listening on 0.0.0.0:%d", port)
    server.serve_forever()


# ═══════════════════════════════════════════════════════════════
#  PORTAL HTTP SERVER  (serves hi1_lea.html)
# ═══════════════════════════════════════════════════════════════

def _run_portal_server(port: int, portal_dir: str = "portal"):

    class PortalHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            logger.debug("Portal HTTP: %s - %s", self.client_address[0], format % args)

        def do_GET(self):
            client_ip = self.client_address[0]
            if self.path == "/" or self.path == "/index.html":
                logger.debug("Portal: %s GET / (serving hi1_lea.html)", client_ip)
                self._serve_file(os.path.join(portal_dir, "hi1_lea.html"), "text/html")
            elif self.path.endswith(".html"):
                fname = self.path.lstrip("/")
                logger.debug("Portal: %s GET %s", client_ip, fname)
                self._serve_file(os.path.join(portal_dir, fname), "text/html")
            elif self.path == "/hi2/log":
                with _lock:
                    data = list(reversed(_hi2_events[-200:]))
                logger.debug("Portal: %s polled /hi2/log → %d event(s)", client_ip, len(data))
                self._json(data)
            elif self.path == "/hi3/files":
                with _lock:
                    files = list(_cc_files)
                logger.debug("Portal: %s polled /hi3/files → %d file(s)", client_ip, len(files))
                self._json({"files": files})
            elif self.path == "/health":
                self._json({"status":"ok","uptime":int(time.time()-_start_time),
                            "hi2_events":len(_hi2_events),"cc_files":len(_cc_files),
                            "ftp_clients":_ftp_clients[-5:]})
            else:
                logger.warning("Portal: %s requested unknown path=%s", client_ip, self.path)
                self.send_response(404); self.end_headers()

        def _serve_file(self, path, ctype):
            try:
                with open(path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                logger.debug("Portal: served %s (%d bytes)", path, len(data))
            except FileNotFoundError:
                logger.warning("Portal: file not found: %s", path)
                self.send_response(404); self.end_headers()

        def _json(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin","*")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin","*")
            self.send_header("Access-Control-Allow-Methods","GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers","Content-Type")
            self.end_headers()

    server = HTTPServer(("0.0.0.0", port), PortalHandler)
    logger.info("LEA portal server on 0.0.0.0:%d  →  http://<LEA-IP>:%d/", port, port)
    server.serve_forever()


# ═══════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LEA Receiver — India 4G LTE LIS (FTP Mode)")
    parser.add_argument("--ftp-port",     type=int, default=21,     help="FTP server port (HI3, plaintext)")
    parser.add_argument("--hi2-port",     type=int, default=8443,   help="HI2 IRI receiver port")
    parser.add_argument("--portal-port",  type=int, default=8080,   help="LEA portal (hi1_lea.html) port")
    parser.add_argument("--cc-dir",       default="cc_received",    help="Directory for received CC files")
    parser.add_argument("--hi2-dir",      default="hi2_events",     help="Directory for received IRI event files")
    parser.add_argument("--portal-dir",   default="portal",         help="Path to portal/ directory")
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║    LEA Receiver Server — India 4G LTE LIS (FTP Mode)        ║
╠══════════════════════════════════════════════════════════════╣
║  FTP (HI3):    0.0.0.0:{args.ftp_port:<5}  ← CC content (plaintext)  ║
║  HI2:          0.0.0.0:{args.hi2_port:<5}  ← IRI events from LIS    ║
║  Portal:       0.0.0.0:{args.portal_port:<5}  → hi1_lea.html          ║
╠══════════════════════════════════════════════════════════════╣
║  CC files:     {args.cc_dir:<46}║
║  IRI events:   {args.hi2_dir:<46}║
║  Capture:      tcpdump -i any -n 'port 21' -w ftp.pcap       ║
╚══════════════════════════════════════════════════════════════╝
    """)

    threads = [
        threading.Thread(target=_run_ftp_server,     args=(args.ftp_port,     args.cc_dir),        daemon=True, name="FTP"),
        threading.Thread(target=_run_hi2_server,     args=(args.hi2_port,     args.hi2_dir),       daemon=True, name="HI2"),
        threading.Thread(target=_run_portal_server,  args=(args.portal_port,  args.portal_dir),    daemon=True, name="Portal"),
    ]
    for t in threads:
        t.start()
        logger.info("Started thread: %s", t.name)

    try:
        while True:
            time.sleep(10)
            logger.info("LEA status: HI2 events=%d  CC files=%d  FTP clients=%d",
                        len(_hi2_events), len(_cc_files), len(_ftp_clients))
    except KeyboardInterrupt:
        print("\nLEA receiver stopped.")
