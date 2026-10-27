"""The local approval page (unit S1, method 5).

Every page decision is in Core: admit(peer) holds the tailnet whois check, which the Handler asks before it reads a
request byte; handle(Request) -> Response holds Host, the one-time URL token that becomes an HttpOnly SameSite=Strict
cookie, Origin on POST, the body limit, and the batch form whose challenge the sitting checks (skill_eval builds
`submit`). Core.finished(now) holds the idle and batch exits. skill_eval.approve_ui
runs presence and approver-check before any socket opens and prints the URL and the batch challenge to the launching
terminal only. ASCII only.
"""
import collections, hmac, html, http.server, io, ipaddress, json, os, socket, time, urllib.parse

from engine import approvals

IDLE_SECONDS = 600
BODY_MAX = 65536
MISMATCH_MAX = 3
TAILNET = ipaddress.ip_network("100.64.0.0/10")
WHOIS_LOGIN_FIELD = ("UserProfile", "LoginName")  # verified 2026-10-27, Homebrew tailscale 1.96.3 `whois --json`
# The standalone CLIs come first: under run_process's stripped env the App bundle binary prints "The Tailscale GUI
# failed to start" instead of answering (seen 2026-10-27 over SSH).
TAILSCALE_PATHS = ("/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
                   "/Applications/Tailscale.app/Contents/MacOS/Tailscale")
HEADERS = [("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
                                       "frame-ancestors 'none'; base-uri 'none'"),
           ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "DENY"), ("Cache-Control", "no-store"),
           ("Referrer-Policy", "same-origin")]  # under no-referrer a browser sends `Origin: null` on the form POST

Request = collections.namedtuple("Request", "method path query headers body peer")
Response = collections.namedtuple("Response", "status headers body")


class Mismatch(Exception):
    """The typed batch challenge did not match; nothing was signed."""


class Blocked(Exception):
    """The batch may not be signed now (the launching terminal is gone); nothing was signed."""


def bind_address(tailnet, ip_output):
    """(host, 0): 127.0.0.1, or the one IPv4 in 100.64.0.0/10 that `tailscale ip -4` printed. Never 0.0.0.0."""
    if not tailnet: return "127.0.0.1", 0
    lines = [x.strip() for x in (ip_output or "").splitlines() if x.strip()]
    if len(lines) != 1: raise ValueError("tailscale ip -4 printed %d addresses, not one" % len(lines))
    try:
        ip = ipaddress.IPv4Address(lines[0])
    except ValueError:
        raise ValueError("tailscale ip -4 printed %r, not an IPv4 address" % lines[0][:40])
    if ip not in TAILNET: raise ValueError("%s is not a Tailscale address (100.64.0.0/10)" % ip)
    return str(ip), 0


def whois_login(rc, out, err=None):
    """The login of a `tailscale whois --json` answer, or None when the command failed, the JSON does not parse or the
    field (WHOIS_LOGIN_FIELD) is missing."""
    if rc != 0: return None
    try:
        value = json.loads(out)
        for key in WHOIS_LOGIN_FIELD: value = value[key]
    except (ValueError, KeyError, TypeError):
        return None
    return value if isinstance(value, str) and value else None


def tailscale_binary(exists=os.path.exists):
    return next((p for p in TAILSCALE_PATHS if exists(p)), None)


def same(a, b):
    return isinstance(a, str) and isinstance(b, str) and hmac.compare_digest(a.encode("utf-8", "replace"),
                                                                           b.encode("utf-8", "replace"))


def text(value):
    return html.escape(approvals.printable(str(value)), quote=True)


def pre(value):
    """value in a <pre> block: line breaks and tabs stay real, every other control is still escaped as in text(). HTML
    drops one newline right after <pre>, so one is added for a body that starts with a blank line."""
    return "<pre>\n%s</pre>" % "\n".join("\t".join(text(part) for part in line.split("\t"))
                                         for line in str(value).split("\n"))


def page(title, rows):
    return ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><title>%s</title>"
            "<style>body{font-family:sans-serif;max-width:60em;margin:1em auto}pre{white-space:pre-wrap;"
            "background:#f4f4f4;padding:.5em;max-height:20em;overflow:auto}fieldset{margin:1em 0}</style></head>"
            "<body><h1>%s</h1>\n%s\n</body></html>\n" % (text(title), text(title), "\n".join(rows))).encode("utf-8")


def list_page(items):
    rows = ['<form method="post" action="/decide">']
    for i, it in enumerate(items):
        rows += ['<fieldset><legend>%s: %s (%s)</legend>' % (text(it["unit"]), text(it["gate"]), text(it["status"])),
                 "<p>summary: %s</p>" % text(it["summary"]),
                 "<p>sha256 %s<br>ruleset_sha256 %s<br>spend so far %s, plan estimate %s</p>" % (
                     text(it["sha256"] or "none: cannot be signed yet"), text(it["ruleset_sha256"]),
                     text("$%.2f" % it["spend_usd"]),
                     text("unknown" if it["estimate_usd"] is None else "$%.2f" % it["estimate_usd"]))]
        rows += ["<p>%s</p>%s" % (text(label), pre(body)) for label, body in it["review"]]
        rows += ['<p><label><input type="radio" name="d%d" value="approve"> approve</label> '
                 '<label><input type="radio" name="d%d" value="reject"> reject</label> '
                 '<label><input type="radio" name="d%d" value="leave" checked> leave pending</label></p>' % (i, i, i),
                 '<p><label>reason%s <input type="text" name="r%d" maxlength="200" size="60"></label></p></fieldset>'
                 % (" (required to approve this waiver)" if it["needs_reason"] else "", i)]
    rows += ['<p><label>challenge from the launching terminal <input type="text" name="challenge" maxlength="6" '
             'autocomplete="off"></label></p><p><button type="submit">submit this batch</button></p></form>']
    return page("Pending approvals (%d)" % len(items), rows)


def respond(status, body=b"", extra=()):
    return Response(status, list(extra) + HEADERS + [("Content-Type", "text/html; charset=utf-8")], body)


class Core(object):
    """Every page decision. hosts: the accepted Host values; origins: the accepted Origin values on POST; whois(peer):
    the tailnet login of a peer or None (tailnet mode only); submit(decisions, typed): outcome lines, raising Mismatch
    or Blocked. The token and the cookie are compared in constant time and never written to a response body."""
    def __init__(self, items, token, cookie, hosts, origins, clock, submit, whois=None, owner=None, port=0):
        self.items, self.token, self.cookie, self.hosts, self.origins = items, token, cookie, set(hosts), set(origins)
        self.clock, self.submit, self.whois, self.owner = clock, submit, whois, owner
        self.cookie_name = "sm_%d" % port
        self.last, self.done, self.mismatches, self.exit_reason, self.ok = clock(), False, 0, None, False

    def finished(self, now):
        if not self.done and now - self.last >= IDLE_SECONDS:
            self.done, self.exit_reason = True, "no request for 10 minutes: the page closed and nothing more is signed"
        return self.done

    def idle_left(self):
        return self.last + IDLE_SECONDS - self.clock()

    def admit(self, peer):
        """None when this transport peer may send a request; a 403 otherwise. The Handler asks it before reading any
        request byte, so a tailnet peer that is not the owner cannot hold the connection open."""
        if self.whois is None or same(self.whois(peer), self.owner): return None
        return respond(403, page("Forbidden", ["<p>this tailnet peer is not the owner</p>"]))

    def handle(self, req):
        if self.done: return respond(410, page("This page has closed", []))
        if req.headers.get("host") not in self.hosts: return respond(403, page("Forbidden", ["<p>wrong Host</p>"]))
        jar = parse_cookies(req.headers.get("cookie", ""))
        if not same(jar.get(self.cookie_name), self.cookie):
            if req.method == "GET" and req.path == "/" and self.token is not None and same(req.query.get("t"), self.token):
                self.token, self.last = None, self.clock()
                return respond(303, b"", [("Location", "/"), ("Set-Cookie", "%s=%s; HttpOnly; SameSite=Strict; Path=/"
                                                                 % (self.cookie_name, self.cookie))])
            return respond(403, page("Forbidden", ["<p>open the URL printed in the launching terminal</p>"]))
        self.last = self.clock()
        if req.method == "GET" and req.path == "/": return respond(200, list_page(self.items))
        if not (req.method == "POST" and req.path == "/decide"): return respond(404, page("Not found", []))
        if req.headers.get("origin") not in self.origins: return respond(403, page("Forbidden", ["<p>Origin</p>"]))
        if len(req.body) > BODY_MAX: return respond(413, page("Too large", []))
        try:
            decisions, typed = self.parse(req.body)
        except ValueError as ex:
            return respond(400, page("Bad request", ["<p>%s</p>" % text(ex)]))
        try:
            lines = self.submit(decisions, typed)
        except Mismatch:
            self.mismatches += 1
            if self.mismatches >= MISMATCH_MAX:
                self.done, self.exit_reason = True, "%d challenge mismatches: the page closed" % MISMATCH_MAX
            return respond(403, page("Challenge mismatch", ["<p>nothing was signed; a new challenge is in the "
                                                                "launching terminal</p>"]))
        except Blocked as ex:
            return respond(403, page("Not signed", ["<p>%s</p>" % text(ex)]))
        self.done, self.exit_reason = True, "the batch is done: the page closed"
        self.ok = all(not x.startswith("not signed") for x in lines)
        return respond(200, page("Batch done", ["<p>%s</p>" % text(x) for x in lines] +
                                 ["<p>the server has exited; this page will not load again</p>"]))

    def parse(self, body):
        try:
            fields = urllib.parse.parse_qs(body.decode("ascii"), keep_blank_values=True, strict_parsing=bool(body))
        except (UnicodeDecodeError, ValueError):
            raise ValueError("the form does not parse")
        decisions = []
        for name, values in fields.items():
            if name == "challenge" or (name[:1] == "r" and name[1:].isdigit()): continue
            if not (name[:1] == "d" and name[1:].isdigit() and int(name[1:]) < len(self.items)) or len(values) != 1 \
                    or values[0] not in ("approve", "reject", "leave"):
                raise ValueError("unknown field %s" % name[:20])
            i = int(name[1:])
            decisions.append((i, values[0], (fields.get("r%d" % i) or [""])[0].strip()))
        return sorted(decisions), (fields.get("challenge") or [""])[0]


def parse_cookies(header):
    out = {}
    for part in (header or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out.setdefault(k.strip(), v.strip())
    return out


class DeadlineReader(io.RawIOBase):
    """Socket reads that share one wall-clock deadline, so a peer that trickles bytes cannot stretch a request past
    it."""
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def readable(self):
        return True

    def readinto(self, buf):
        left = self.deadline - time.monotonic()
        if left <= 0: raise socket.timeout("the request took too long")
        self.sock.settimeout(left)
        return self.sock.recv_into(buf)


class Handler(http.server.BaseHTTPRequestHandler):
    """Prints nothing, so the URL token never reaches a log. One connection ends by `timeout` seconds after it was
    accepted, or at the idle exit when that comes first."""
    timeout = 10
    server_version = "skill-master-approve-ui"
    sys_version = ""

    def setup(self):
        http.server.BaseHTTPRequestHandler.setup(self)
        budget = min(self.timeout, self.server.core.idle_left())
        self.rfile.close()
        self.rfile = io.BufferedReader(DeadlineReader(self.connection, time.monotonic() + budget))

    def handle(self):
        refused = self.server.core.admit(self.client_address[0])
        if refused is None: return http.server.BaseHTTPRequestHandler.handle(self)
        self.requestline, self.request_version, self.close_connection = "", self.protocol_version, True
        self.send(refused)

    def log_message(self, format, *args):
        pass

    def answer(self, body):
        target = urllib.parse.urlsplit(self.path)
        req = Request(self.command, target.path, dict(urllib.parse.parse_qsl(target.query)),
                      {k.lower(): v for k, v in self.headers.items()}, body, self.client_address[0])
        self.send(self.server.core.handle(req))

    def send(self, resp):
        self.send_response(resp.status)
        for k, v in resp.headers: self.send_header(k, v)
        self.send_header("Content-Length", str(len(resp.body)))
        self.end_headers()
        self.wfile.write(resp.body)

    def do_GET(self):
        self.answer(b"")

    def do_POST(self):
        try: n = int(self.headers.get("Content-Length") or 0)
        except ValueError: n = -1
        if not 0 <= n <= BODY_MAX:
            self.send_response(413); self.send_header("Content-Length", "0"); self.end_headers(); return
        self.answer(self.rfile.read(n))


def make_server(host, core):
    """handle_request() returns after 1 s without a request."""
    server = http.server.HTTPServer((host, 0), Handler)
    server.timeout, server.core = 1, core
    server.host, server.port = host, server.server_address[1]
    return server


def serve(server, core, clock):
    try:
        while not core.finished(clock()):
            server.handle_request()
    finally:
        server.server_close()
