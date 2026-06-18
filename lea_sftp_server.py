"""
LEA Receiver Server — India 4G LTE LIS
Runs on the LEA machine. Provides:
  1. SFTP server on port 2222   — receives HI3 CC content from LIS
  2. HTTP server on port 8443   — receives HI2 IRI events pushed by LIS
  3. HTTP server on port 8080   — serves hi1_lea.html portal

Usage:
    pip install paramiko --break-system-packages
    python lea_sftp_server.py

    Optional:
    python lea_sftp_server.py --sftp-port 2222 --hi2-port 8443 --portal-port 8080

Directory layout (created automatically):
    cc_received/     — received HI3 CC files from LIS
    hi2_events/      — received HI2 IRI events as JSON files
    keys/            — SSH host key for SFTP server
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [LEA] %(message)s")
logger = logging.getLogger("LEA")

# ── Shared in-memory stores (for portal polling) ──────────────

_lock = threading.Lock()
_hi2_events:   list[dict] = []   # IRI events received via HI2
_cc_files:     list[dict] = []   # CC files received via SFTP
_sftp_clients: list[str]  = []   # Connected SFTP clients log
_start_time    = time.time()


# ═══════════════════════════════════════════════════════════════
#  SFTP SERVER  (paramiko)
# ═══════════════════════════════════════════════════════════════

def _run_sftp_server(port: int, cc_dir: str):
    """
    Embedded SFTP server using paramiko's Transport + ServerInterface.
    Accepts any username/password for the simulator.
    Stores uploaded files in cc_dir.
    """
    try:
        import paramiko
    except ImportError:
        logger.error("paramiko not installed. Run: pip install paramiko --break-system-packages")
        return

    os.makedirs(cc_dir, exist_ok=True)
    os.makedirs("keys", exist_ok=True)

    # Generate or load host key
    key_path = "keys/sftp_host_rsa.key"
    if not os.path.exists(key_path):
        host_key = paramiko.RSAKey.generate(2048)
        host_key.write_private_key_file(key_path)
        logger.info("Generated SSH host key: %s", key_path)
    else:
        host_key = paramiko.RSAKey(filename=key_path)

    class SFTPServerInterface(paramiko.SFTPServerInterface):
        ROOT = os.path.abspath(cc_dir)

        def _realpath(self, path):
            root = self.ROOT
            joined = os.path.join(root, path.lstrip("/"))
            real = os.path.realpath(joined)
            if not real.startswith(root):
                return root  # jail to ROOT
            return real

        def list_folder(self, path):
            real = self._realpath(path)
            try:
                entries = []
                for fname in os.listdir(real):
                    fpath = os.path.join(real, fname)
                    stat = os.stat(fpath)
                    attr = paramiko.SFTPAttributes.from_stat(stat)
                    attr.filename = fname
                    entries.append(attr)
                logger.debug("SFTP: list_folder(%s) → %d entries", path, len(entries))
                return entries
            except OSError as e:
                logger.warning("SFTP: list_folder(%s) failed: %s", path, e)
                return paramiko.SFTP_PERMISSION_DENIED

        def stat(self, path):
            real = self._realpath(path)
            try:
                return paramiko.SFTPAttributes.from_stat(os.stat(real))
            except OSError as e:
                logger.debug("SFTP: stat(%s) — not found: %s", path, e)
                return paramiko.SFTP_NO_SUCH_FILE

        def lstat(self, path):
            return self.stat(path)

        def open(self, path, flags, attr):
            real = self._realpath(path)
            os.makedirs(os.path.dirname(real), exist_ok=True)
            try:
                if flags & os.O_WRONLY or flags & os.O_RDWR:
                    f = open(real, "wb")
                    obj = paramiko.SFTPHandle(flags)
                    obj.readfile = f
                    obj.writefile = f
                    # Track received file
                    fname = os.path.basename(real)
                    with _lock:
                        _cc_files.append({
                            "name": fname,
                            "path": real,
                            "ts": datetime.utcnow().isoformat(),
                            "size": "—"
                        })
                    logger.info("HI3 SFTP: receiving file → %s (path=%s)", fname, real)
                    # Log completion + final size when the upload finishes
                    _orig_close = obj.close
                    def _logged_close():
                        try:
                            size = os.path.getsize(real)
                        except OSError:
                            size = -1
                        with _lock:
                            for rec in _cc_files:
                                if rec.get("path") == real and rec.get("size") == "—":
                                    rec["size"] = f"{size}B"
                                    break
                        logger.info("HI3 SFTP: file received complete: %s size=%dB", fname, size)
                        _orig_close()
                    obj.close = _logged_close
                    return obj
                else:
                    f = open(real, "rb")
                    obj = paramiko.SFTPHandle(flags)
                    obj.readfile = f
                    logger.debug("SFTP: read-open %s", real)
                    return obj
            except Exception as e:
                logger.error("SFTP open error for path=%s flags=%s: %s", path, flags, e)
                return paramiko.SFTP_FAILURE

        def mkdir(self, path, attr):
            real = self._realpath(path)
            try:
                os.makedirs(real, exist_ok=True)
                logger.debug("SFTP: mkdir %s", real)
                return paramiko.SFTP_OK
            except Exception as e:
                logger.warning("SFTP: mkdir %s failed: %s", real, e)
                return paramiko.SFTP_FAILURE

        def remove(self, path):
            logger.debug("SFTP: remove(%s) — unsupported (simulator is append-only)", path)
            return paramiko.SFTP_OP_UNSUPPORTED

        def rename(self, oldpath, newpath):
            logger.debug("SFTP: rename(%s → %s) — unsupported", oldpath, newpath)
            return paramiko.SFTP_OP_UNSUPPORTED

        def rmdir(self, path):
            logger.debug("SFTP: rmdir(%s) — unsupported", path)
            return paramiko.SFTP_OP_UNSUPPORTED

        def chattr(self, path, attr):
            return paramiko.SFTP_OK

        def canonicalize(self, path):
            if not path or path == ".":
                return "/"
            if not path.startswith("/"):
                path = "/" + path
            return path

    class LEAServerInterface(paramiko.ServerInterface):
        def check_channel_request(self, kind, chanid):
            logger.debug("SFTP: channel request kind=%s chanid=%s", kind, chanid)
            if kind == "session":
                return paramiko.OPEN_SUCCEEDED
            logger.warning("SFTP: channel request denied — unsupported kind=%s", kind)
            return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_auth_password(self, username, password):
            # Accept any credentials in simulator mode
            logger.info("SFTP: auth user=%s (accepted — simulator mode, password not validated)", username)
            with _lock:
                _sftp_clients.append(f"{username} @ {datetime.utcnow().isoformat()[:19]}")
            return paramiko.AUTH_SUCCESSFUL

        def check_auth_publickey(self, username, key):
            logger.info("SFTP: auth user=%s via publickey (accepted — simulator mode)", username)
            return paramiko.AUTH_SUCCESSFUL

        def get_allowed_auths(self, username):
            return "password,publickey"

        def check_channel_subsystem_request(self, channel, name):
            logger.debug("SFTP: subsystem request name=%s", name)
            if name != "sftp":
                logger.warning("SFTP: rejected unsupported subsystem request: %s", name)
            return name == "sftp"

    class SFTPHandle(paramiko.SFTPHandle):
        pass

    # Listen for connections
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        logger.error("Cannot bind SFTP port %d: %s", port, e)
        return
    sock.listen(5)
    logger.info("SFTP server listening on 0.0.0.0:%d (cc_received: %s)", port, cc_dir)

    while True:
        try:
            client_sock, client_addr = sock.accept()
            logger.info("HI3 SFTP: connection accepted from %s:%d", *client_addr)

            t = paramiko.Transport(client_sock)
            t.add_server_key(host_key)
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, SFTPServerInterface)

            server = LEAServerInterface()
            try:
                t.start_server(server=server)
                logger.info("HI3 SFTP: transport/SSH handshake established with %s:%d", *client_addr)
            except Exception as e:
                logger.warning("HI3 SFTP: transport handshake FAILED from %s:%d — %s", client_addr[0], client_addr[1], e)
                continue

            chan = t.accept(20)
            if chan is None:
                logger.warning("HI3 SFTP: no channel opened by client %s:%d (timeout)", *client_addr)
            else:
                logger.info("HI3 SFTP: channel opened by %s:%d", *client_addr)
        except Exception as e:
            logger.error("HI3 SFTP accept error: %s", e)
            time.sleep(1)


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
                            "sftp_clients":_sftp_clients[-5:]})
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
    parser = argparse.ArgumentParser(description="LEA Receiver — India 4G LTE LIS")
    parser.add_argument("--sftp-port",   type=int, default=2222,  help="SFTP server port (HI3)")
    parser.add_argument("--hi2-port",    type=int, default=8443,  help="HI2 IRI receiver port")
    parser.add_argument("--portal-port", type=int, default=8080,  help="LEA portal (hi1_lea.html) port")
    parser.add_argument("--cc-dir",      default="cc_received",   help="Directory for received CC files")
    parser.add_argument("--hi2-dir",     default="hi2_events",    help="Directory for received IRI event files")
    parser.add_argument("--portal-dir",  default="portal",        help="Path to portal/ directory")
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║        LEA Receiver Server — India 4G LTE LIS               ║
╠══════════════════════════════════════════════════════════════╣
║  SFTP (HI3):   0.0.0.0:{args.sftp_port:<5}  ← CC content from LIS    ║
║  HI2:          0.0.0.0:{args.hi2_port:<5}  ← IRI events from LIS    ║
║  Portal:       0.0.0.0:{args.portal_port:<5}  → hi1_lea.html          ║
╠══════════════════════════════════════════════════════════════╣
║  CC files:     {args.cc_dir:<46}║
║  IRI events:   {args.hi2_dir:<46}║
╚══════════════════════════════════════════════════════════════╝
    """)

    threads = [
        threading.Thread(target=_run_sftp_server,   args=(args.sftp_port,   args.cc_dir),  daemon=True, name="SFTP"),
        threading.Thread(target=_run_hi2_server,    args=(args.hi2_port,    args.hi2_dir), daemon=True, name="HI2"),
        threading.Thread(target=_run_portal_server, args=(args.portal_port, args.portal_dir), daemon=True, name="Portal"),
    ]
    for t in threads:
        t.start()
        logger.info("Started thread: %s", t.name)

    try:
        while True:
            time.sleep(10)
            logger.info("LEA status: HI2 events=%d  CC files=%d  SFTP clients=%d",
                        len(_hi2_events), len(_cc_files), len(_sftp_clients))
    except KeyboardInterrupt:
        print("\nLEA receiver stopped.")
