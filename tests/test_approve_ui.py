"""S1 method 5: the local approval page. Every page decision lives in engine/approve_ui.Core, which these cases drive with
fake requests, a fake clock, a fake server and a fake tailscale; signing goes through the same _approve_with and
store_approval as a single approve, with the throwaway test key of tests/test_skill_eval.py in the temp HOME of its
Base. Cases that need a real socket or a pty skip only on the bind or openpty OSError (gates.md G73); a skip is not a
pass. No case starts tailscale, a browser or the real approver. ASCII only."""
import contextlib, io, json, os, re, select, socket, subprocess, sys, threading, time, unittest, urllib.parse
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_approval_batch as B
import test_human_gate as H
from test_skill_eval import se, write, wjson, rjson
from engine import approvals as A, approve_ui as U

COMBINED = "requirements_eval_set"
PORT = 4321
OWNER = "pinesky@example.com"
TAILNET_IP = "100.84.56.12"
MAGICDNS = "mac-mini.tail0000.ts.net"
PEER = "100.101.102.103"
REAL_RUN = subprocess.run
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


def whois_json(login=OWNER):
    """The login is read from UserProfile.LoginName, the field a real `tailscale whois --json` answer carried (captured
    2026-10-27 with Homebrew tailscale 1.96.3; no session may run tailscale)."""
    return json.dumps({"Node": {"Name": "phone"}, "UserProfile": {"LoginName": login, "DisplayName": "x"}})


def form_post_origin(referrer_policy, page_origin):
    """The Origin header a browser sends on the page's own form POST, per the Fetch standard's "append a request
    `Origin` header" step: a POST that is not CORS gets `null` under no-referrer, under same-origin when the request is
    cross-origin, and under the strict policies on an https-to-http downgrade. The form posts to its own http origin,
    so only no-referrer serializes it as `null`. The last token of the header is the policy in force."""
    policy = referrer_policy.split(",")[-1].strip().lower()
    return "null" if policy == "no-referrer" else page_origin


class Clock(object):
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class FakeServer(object):
    def __init__(self, host, clock, script):
        self.host, self.port, self.clock, self.script = host, PORT, clock, script
        self.core, self.responses, self.closed = None, [], False

    def handle_request(self):
        if self.script:
            self.responses.append(self.script.pop(0)(self))
        else:
            self.clock.t += 1

    def server_close(self):
        self.closed = True


class Page(B.Batch):
    def setUp(self):
        B.Batch.setUp(self)
        self.clock = Clock()
        self.script, self.servers, self.tailscale_calls = [], [], []
        self.tailscale = {("ip", "-4"): (0, TAILNET_IP + "\n", ""), ("whois", "--json", PEER): (0, whois_json(), "")}
        def factory(host):
            s = FakeServer(host, self.clock, self.script); self.servers.append(s); return s
        def ts(argv):
            self.tailscale_calls.append(list(argv))
            return self.tailscale.get(tuple(argv), (1, "", "not running"))
        pt = mock.patch.dict(se.UI, {"server": factory, "clock": self.clock, "tailscale": ts})
        pt.start(); self.addCleanup(pt.stop)
        self.config = os.path.join(self.home, ".config", "skill-master", "approve-ui.json")

    def pairs(self, names=("alpha", "beta", "gamma")):
        return se.unit_pairs([self.units[n][0] for n in names], [self.units[n][1] for n in names])

    def terminal(self):
        return "\n".join(self.shown)

    def token(self):
        return re.search(r"http://[^/\s]+/\?t=([A-Za-z0-9_-]+)", self.terminal()).group(1)

    def challenge(self):
        return re.findall(r"type ([A-Z0-9]{6}) into the page", self.terminal())[-1]

    def host(self, server):
        return "%s:%d" % (server.host, server.port) if server.host != "127.0.0.1" else "127.0.0.1:%d" % PORT

    def request(self, method, path, headers=None, body=b"", peer="127.0.0.1"):
        def step(server):
            refused = server.core.admit(peer)
            if refused is not None: return refused
            target = urllib.parse.urlsplit(path)
            h = {"host": self.host(server)}
            h.update(headers or {})
            return server.core.handle(U.Request(method, target.path, dict(urllib.parse.parse_qsl(target.query)),
                                                {k.lower(): v for k, v in h.items()}, body, peer))
        return step

    def open_url(self, **kw):
        return lambda server: self.request("GET", "/?t=" + self.token(), **kw)(server)

    def cookie(self, server):
        return [v for k, v in server.responses[0].headers if k == "Set-Cookie"][0].split(";")[0]

    def browser_origin(self, server):
        """The Origin a browser puts on the form POST, from the Referrer-Policy of the last page the server sent."""
        policy = [v for r in server.responses for k, v in r.headers if k == "Referrer-Policy"][-1]
        return form_post_origin(policy, "http://" + self.host(server))

    def with_cookie(self, method, path, body=b"", origin=True, **kw):
        def step(server):
            headers = {"Cookie": self.cookie(server)}
            if origin: headers["Origin"] = self.browser_origin(server)
            headers.update(kw.pop("headers", {}))
            return self.request(method, path, headers=headers, body=body, **kw)(server)
        return step

    def form(self, decisions, challenge=None):
        def body():
            fields = [("challenge", self.challenge() if challenge is None else challenge)]
            for i, (d, r) in decisions.items(): fields += [("d%d" % i, d), ("r%d" % i, r)]
            return urllib.parse.urlencode(fields).encode()
        return body

    def submit(self, decisions, challenge=None, **kw):
        make = self.form(decisions, challenge)
        return lambda server: self.with_cookie("POST", "/decide", body=make(), **kw)(server)

    def run_page(self, names=("alpha", "beta", "gamma"), tailscale=False):
        return se.approve_ui(self.pairs(names), tailscale=tailscale)

    def statuses(self):
        return [r.status for r in self.servers[0].responses]

    def bodies(self):
        return b"".join(r.body for s in self.servers for r in s.responses)


class TestStart(Page):
    def refused(self, want, **kw):
        if kw: self.install(**kw)
        with self.assertRaises(A.Refused) as cm: self.run_page()
        self.assertIn(want, str(cm.exception))
        self.assertEqual(self.servers, [], "a socket opened before presence passed")
        self.assertEqual(self.secrets, [])

    def test_a_non_tty_stdin_is_refused_with_the_h1_text_and_no_socket(self):
        with mock.patch.object(A, "is_tty", lambda fd: fd != 0):
            self.refused("approve-ui needs a human at an interactive terminal (stdin is not a TTY)")

    def test_an_agent_marker_is_refused(self):
        self.refused("approve-ui needs a human at an interactive terminal (agent environment variables are set: "
                     "CLAUDECODE)", environ=dict(T.AGENT_ENV, CLAUDECODE="1"))

    def test_an_agent_ancestor_from_a_fake_ps_is_refused(self):
        table = {4242: (77, "Python", "/usr/bin/python3"), 77: (88, "zsh", "/bin/zsh"),
                 88: (1, "claude.exe", "/opt/claude.exe"), 1: (0, "launchd", "/sbin/launchd")}
        self.refused("agent process in the parent chain: claude.exe (pid 88)", read_proc=A.table_reader(table))

    def test_approver_check_runs_before_any_socket(self):
        loaded = (0, (T.shared_keys()["pub"] + " approver\n").encode(), b"")
        self.refused("approve-ui: approver-check: the approver key is loaded in ssh-agent", ssh_add=lambda: loaded)

    def test_the_local_bind_address_is_127_0_0_1_on_an_os_picked_port(self):
        self.assertEqual(U.bind_address(False, None), ("127.0.0.1", 0))
        self.run_page()
        self.assertEqual(self.servers[0].host, "127.0.0.1")
        self.assertIn("http://127.0.0.1:%d/?t=" % PORT, self.terminal())

    def test_a_real_listening_socket_is_on_127_0_0_1_only(self):
        core = U.Core(items=[], token="t" * 43, cookie="c" * 43, hosts={"127.0.0.1:0"}, origins=set(),
                      clock=time.monotonic, submit=None)
        try:
            server = U.make_server("127.0.0.1", core)
        except OSError as ex:
            self.skipTest("cannot bind a socket here (gates.md G73): %s" % ex)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        self.assertNotEqual(server.server_address[1], 0)

    def test_nothing_pending_opens_no_socket(self):
        for n in ("alpha", "beta", "gamma"): se.approve(COMBINED, self.spec(n), self.wsp(n))
        self.assertEqual(self.run_page(), 0)
        self.assertEqual(self.servers, [])
        self.assertIn("nothing is pending", self.terminal())


class TestRequests(Page):
    def test_no_token_wrong_token_reused_token_wrong_host_and_foreign_origin_get_403(self):
        self.script += [self.open_url(),
                        self.request("GET", "/"),
                        self.request("GET", "/?t=" + "x" * 43),
                        lambda s: self.request("GET", "/?t=" + self.token())(s),
                        self.with_cookie("GET", "/", headers={"Host": "localhost:%d" % PORT}),
                        self.with_cookie("GET", "/", headers={"Host": "127.0.0.1:%d" % (PORT + 1)}),
                        self.submit({0: ("approve", "")}, headers={"Origin": "http://evil.example"}),
                        self.submit({0: ("approve", "")}, headers={"Origin": "null"}),
                        self.submit({0: ("approve", "")}, origin=False),
                        self.with_cookie("GET", "/")]
        self.run_page()
        self.assertEqual(self.statuses(), [303, 403, 403, 403, 403, 403, 403, 403, 403, 200])
        for n in ("alpha", "beta", "gamma"): self.assertIsNone(B.state(os.path.join(self.wsp(n), "approvals.json")))

    def test_the_cookie_flags_and_the_security_headers(self):
        self.script += [self.open_url(), self.with_cookie("GET", "/")]
        self.run_page()
        first, page = self.servers[0].responses[:2]
        cookie = [v for k, v in first.headers if k == "Set-Cookie"][0]
        self.assertTrue(cookie.startswith("sm_%d=" % PORT), cookie)
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"): self.assertIn(flag, cookie)
        self.assertEqual(dict(first.headers)["Location"], "/")
        headers = dict(page.headers)
        self.assertEqual(page.status, 200)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual((headers["X-Frame-Options"], headers["Cache-Control"], headers["X-Content-Type-Options"]),
                         ("DENY", "no-store", "nosniff"))
        self.assertIn(headers["Referrer-Policy"], ("no-referrer", "same-origin"), "no Referer to another origin")
        self.assertNotIn(b"<script", page.body.lower())

    def test_a_browser_form_post_carries_an_origin_the_page_accepts(self):
        self.script += [self.open_url(), self.with_cookie("GET", "/"), self.submit({0: ("approve", "")})]
        self.assertEqual(self.run_page(("alpha",)), 0)
        self.assertEqual(self.statuses(), [303, 200, 200])
        self.assertEqual(se.approval_verdict(COMBINED, self.spec("alpha"), self.wsp("alpha")).status, "approved")

    def test_the_page_lists_every_pending_item_with_its_fields(self):
        self.script += [self.open_url(), self.with_cookie("GET", "/")]
        self.run_page()
        body = self.servers[0].responses[1].body.decode()
        for n in ("alpha", "beta", "gamma"):
            spec = self.spec(n)
            self.assertIn(n, body)
            self.assertIn(se.gate_hash(COMBINED, spec, self.wsp(n)), body)
        for word in (COMBINED, se.ruleset_sha256(), "spend so far", "plan estimate", "goal"):
            self.assertIn(word, body)

    def review_page(self, text):
        write(os.path.join(self.wsp("alpha"), "requirements.md"), text)
        self.script += [self.open_url(), self.with_cookie("GET", "/")]
        self.run_page(("alpha",))
        return self.servers[0].responses[1].body.decode()

    def test_agent_written_text_is_escaped(self):
        body = self.review_page("<script>steal()</script> \"><img src=x> & 'q'\n\x1b[2J\rX\x00\x7f\x85" + chr(0x202E)
                                + chr(0x2066) + chr(0x200B) + chr(0x2028) + "end\n")
        for raw in ("<script>steal", "<img src=x>", "\x1b", "\r", "\x00", "\x7f", "\x85", chr(0x202E), chr(0x2066),
                    chr(0x200B), chr(0x2028)):
            self.assertNotIn(raw, body)
        self.assertIn("&lt;script&gt;steal()&lt;/script&gt; &quot;&gt;&lt;img src=x&gt; &amp; &#x27;q&#x27;\n"
                      "\\x1b[2J\\rX\\x00\\x7f\\x85\\u202e\\u2066\\u200b\\u2028end\n", body)

    def test_a_multi_line_body_keeps_its_line_breaks_and_tabs(self):
        body = self.review_page("# Goal\n\nCount words.\n- case\tone\n- case\ttwo\n")
        self.assertIn("<pre>\n# Goal\n\nCount words.\n- case\tone\n- case\ttwo\n</pre>", body)

    def test_a_wrong_batch_challenge_signs_nothing_and_three_end_the_page(self):
        self.script += [self.open_url()] + [self.submit({0: ("approve", "")}, challenge="AAAAAA")] * 3
        rc = self.run_page()
        self.assertEqual(self.statuses(), [303, 403, 403, 403])
        self.assertNotEqual(rc, 0)
        for n in ("alpha", "beta", "gamma"): self.assertIsNone(B.state(os.path.join(self.wsp(n), "approvals.json")))
        self.assertIn("challenge mismatch", self.terminal())

    def test_the_idle_exit_with_a_fake_clock_and_no_request(self):
        rc = self.run_page()
        self.assertEqual(self.servers[0].responses, [])
        self.assertTrue(self.servers[0].closed)
        self.assertGreaterEqual(self.clock.t - 1000.0, 600)
        self.assertLess(self.clock.t - 1000.0, 602)
        self.assertIn("no request for 10 minutes", self.terminal())

    def test_403_requests_do_not_reset_the_idle_clock(self):
        def later(server):
            self.clock.t += 140
            return self.request("GET", "/")(server)
        self.script += [later] * 4
        self.run_page()
        self.assertEqual(self.statuses(), [403] * 4)
        self.assertEqual(self.clock.t - 1000.0, 600)
        self.assertIn("no request for 10 minutes", self.terminal())

    def test_the_batch_signs_approves_logs_every_decision_and_exits(self):
        self.script += [self.open_url(), self.with_cookie("GET", "/"),
                        self.submit({0: ("approve", "looks right"), 1: ("approve", "also right"),
                                     2: ("reject", "eval set misses a case")})]
        rc = self.run_page()
        self.assertEqual(rc, 0)
        self.assertEqual(self.statuses(), [303, 200, 200])
        self.assertTrue(self.servers[0].closed)
        for n in ("alpha", "beta"):
            self.assertEqual(se.approval_verdict(COMBINED, self.spec(n), self.wsp(n)).status, "approved")
        self.assertIsNone(B.state(os.path.join(self.wsp("gamma"), "approvals.json")))
        log = [json.loads(x) for x in T.read(os.path.join(self.home, ".config", "skill-master", "approvals.log"))
               .splitlines()]
        self.assertEqual([(x["unit"], x["gate"], x["decision"]) for x in log],
                         [("alpha", COMBINED, "approved"), ("beta", COMBINED, "approved"), ("gamma", COMBINED, "rejected")])
        for x in log:
            self.assertEqual(sorted(x), ["at", "decision", "gate", "principal", "reason", "sha256", "unit"])
            self.assertEqual(x["principal"], T.TEST_PRINCIPAL)
        self.assertEqual(log[2]["reason"], "eval set misses a case")
        self.assertEqual(len(self.secrets), 1)
        items = se.pending_items(self.pairs())
        self.assertEqual([i["unit"] for i in items], ["gamma"])

    def test_a_lost_terminal_refuses_the_batch(self):
        tty = {"ok": True}
        def submit(server):
            tty["ok"] = False
            return self.submit({0: ("approve", "")})(server)
        self.script += [self.open_url(), submit]
        with mock.patch.object(A, "is_tty", lambda fd: tty["ok"]):
            self.run_page(("alpha",))
        self.assertEqual(self.statuses()[-1], 403)
        self.assertIsNone(B.state(os.path.join(self.wsp("alpha"), "approvals.json")))

    def test_no_response_body_log_line_or_file_holds_the_passphrase_or_the_token(self):
        self.script += [self.open_url(), self.with_cookie("GET", "/"),
                        self.submit({0: ("approve", "ok"), 1: ("reject", "no")})]
        self.run_page()
        token, challenge = self.token(), self.challenge()
        secrets = [T.TEST_PASSPHRASE.encode(), token.encode(), self.cookie(self.servers[0]).split("=", 1)[1].encode()]
        body = self.bodies()
        for s in secrets + [challenge.encode()]: self.assertNotIn(s, body)
        for root, _, files in os.walk(self.home):
            for f in files:
                with open(os.path.join(root, f), "rb") as fh: data = fh.read()
                for s in secrets: self.assertNotIn(s, data, os.path.join(root, f))
        for line in self.shown:
            if token in line: self.assertTrue(line.strip().startswith("http://127.0.0.1:"), line)

    def test_the_request_log_prints_nothing(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            U.Handler.log_message(object.__new__(U.Handler), '"%s" %s %s', "GET /?t=secret-token HTTP/1.1", "303", "-")
        self.assertEqual(err.getvalue(), "")

    def test_a_page_record_verifies_exactly_like_a_single_record(self):
        twin = B.make_unit(os.path.join(self.tmp, "twin"), "alpha")
        single = se.approve(COMBINED, self.spec("alpha"), self.wsp("alpha"))
        self.script += [self.open_url(), self.submit({0: ("approve", "")})]
        self.assertEqual(se.approve_ui(se.unit_pairs([twin[0]], [twin[1]])), 0)
        page = rjson(os.path.join(twin[1], "approvals.json"))[COMBINED]
        self.assertEqual({k: v for k, v in single.items() if k not in ("at", "signature")},
                         {k: v for k, v in page.items() if k not in ("at", "signature")})
        self.assertEqual(B.payload_lines(single, COMBINED, "alpha"), B.payload_lines(page, COMBINED, "alpha"))
        self.assertEqual(se.approval_verdict(COMBINED, se.load_eval_set(twin[0])[0], twin[1]).status, "approved")


class TestTailnet(Page):
    def configure(self, login=OWNER, magic=MAGICDNS):
        wjson(self.config, {"tailnet_owner_login": login, "magicdns_name": magic})

    def tailnet_request(self, path, host=None, peer=PEER, **kw):
        def step(server):
            h = dict(kw.pop("headers", {}), Host=host or "%s:%d" % (TAILNET_IP, PORT))
            return self.request("GET", path, headers=h, peer=peer)(server)
        return step

    def test_down_or_no_address_or_a_non_tailnet_address_refuses_the_start(self):
        self.configure()
        for out in ((1, "", "Tailscale is stopped."), (0, "", ""), (0, "192.168.1.5\n", ""), (0, "0.0.0.0\n", ""),
                    (0, "100.84.56.12\n100.84.56.13\n", "")):
            with self.subTest(out):
                self.tailscale[("ip", "-4")] = out
                with self.assertRaises(se.EvalError) as cm: self.run_page(tailscale=True)
                self.assertIn("--tailscale", str(cm.exception))
                self.assertEqual(self.servers, [])

    def test_a_missing_owner_login_refuses_the_start(self):
        with self.assertRaises(se.EvalError) as cm: self.run_page(tailscale=True)
        self.assertIn("tailnet_owner_login", str(cm.exception))
        self.assertEqual(self.servers, [])

    def test_the_tailnet_mode_binds_the_tailscale_address_and_is_loud(self):
        self.configure()
        self.run_page(tailscale=True)
        self.assertEqual(self.servers[0].host, TAILNET_IP)
        self.assertIn("http://%s:%d/?t=" % (TAILNET_IP, PORT), self.terminal())
        self.assertIn("TAILNET MODE", self.terminal())

    def test_whois_must_name_the_owner_login(self):
        self.configure()
        self.tailscale[("whois", "--json", "100.64.0.9")] = (0, whois_json("someone@example.com"), "")
        self.tailscale[("whois", "--json", "100.64.0.10")] = (0, json.dumps({"UserProfile": {}}), "")
        self.tailscale[("whois", "--json", "100.64.0.11")] = (0, "not json", "")
        self.script += [self.tailnet_request("/?t=" + "x"),
                        lambda s: self.tailnet_request("/?t=" + self.token(), peer="100.64.0.9")(s),
                        lambda s: self.tailnet_request("/?t=" + self.token(), peer="100.64.0.10")(s),
                        lambda s: self.tailnet_request("/?t=" + self.token(), peer="100.64.0.11")(s),
                        lambda s: self.tailnet_request("/?t=" + self.token(), peer="100.64.0.12")(s),
                        lambda s: self.tailnet_request("/?t=" + self.token(), host="evil.example:%d" % PORT)(s),
                        lambda s: self.tailnet_request("/?t=" + self.token(), host="%s:%d" % (MAGICDNS, PORT))(s)]
        self.run_page(tailscale=True)
        self.assertEqual(self.statuses(), [403, 403, 403, 403, 403, 403, 303])
        self.assertIn(["whois", "--json", "100.64.0.9"], self.tailscale_calls)

    def test_an_owner_peer_signs_one_item_with_the_terminal_challenge_on_the_server(self):
        self.configure()
        self.script += [self.open_url(peer=PEER),
                        self.submit({0: ("approve", "")}, challenge="AAAAAA", peer=PEER),
                        self.submit({0: ("approve", "")}, peer=PEER)]
        self.assertEqual(self.run_page(("alpha",), tailscale=True), 0)
        self.assertEqual(self.servers[0].host, TAILNET_IP)
        self.assertEqual(self.statuses(), [303, 403, 200])
        self.assertEqual(se.approval_verdict(COMBINED, self.spec("alpha"), self.wsp("alpha")).status, "approved")
        self.assertEqual(len(self.secrets), 1)
        self.assertEqual(len(re.findall(r"type ([A-Z0-9]{6}) into the page", self.terminal())), 2)
        body = self.bodies()
        for challenge in re.findall(r"type ([A-Z0-9]{6}) into the page", self.terminal()):
            self.assertNotIn(challenge.encode(), body)
        self.assertNotIn(T.TEST_PASSPHRASE.encode(), body)
        self.assertIn("the batch is done", self.terminal())
        self.assertTrue(self.servers[0].closed)

    def test_the_idle_exit_in_tailnet_mode(self):
        self.configure()
        self.tailscale[("whois", "--json", "100.64.0.9")] = (0, whois_json("someone@example.com"), "")
        def stranger(server):
            self.clock.t += 300
            return self.tailnet_request("/?t=" + self.token(), peer="100.64.0.9")(server)
        self.script.append(stranger)
        self.run_page(tailscale=True)
        self.assertEqual(self.statuses(), [403])
        self.assertTrue(self.servers[0].closed)
        self.assertEqual(self.clock.t - 1000.0, 600)
        self.assertIn("no request for 10 minutes", self.terminal())
        self.assertIsNone(B.state(os.path.join(self.wsp("alpha"), "approvals.json")))

    def test_the_owner_login_is_not_a_code_literal(self):
        src = T.read(os.path.join(T.ROOT, "engine", "approve_ui.py")) + T.read(os.path.join(T.ROOT, "skill_eval.py"))
        self.assertNotIn("@", "".join(re.findall(r"tailnet_owner_login[^\n]*", src)).replace("tailnet_owner_login", ""))
        self.assertEqual(U.WHOIS_LOGIN_FIELD, ("UserProfile", "LoginName"))

    def test_a_standalone_cli_runs_before_the_app_bundle(self):
        """The App bundle binary fails as a CLI under run_process's stripped env (2026-10-27), so it runs only when no
        standalone CLI exists."""
        app, brew, local = ("/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/opt/homebrew/bin/tailscale",
                            "/usr/local/bin/tailscale")
        for present, chosen in (({app, brew, local}, brew), ({app, local}, local), ({app}, app), (set(), None)):
            with self.subTest(sorted(present)):
                self.assertEqual(U.tailscale_binary(exists=present.__contains__), chosen)

    def test_bind_address_accepts_one_tailnet_ipv4_only(self):
        self.assertEqual(U.bind_address(True, TAILNET_IP + "\n"), (TAILNET_IP, 0))
        for bad in ("", "0.0.0.0", "127.0.0.1", "192.168.1.5", "100.128.0.1", "fd7a:115c::1", "x"):
            with self.subTest(bad):
                with self.assertRaises(ValueError): U.bind_address(True, bad)


HOST = "127.0.0.1:%d" % PORT
TOKEN, COOKIE = "T" * 43, "C" * 43


class Wire(object):
    """Stands in for HTTPServer: the real Handler gets one end of a socket.socketpair() (binds fail here, G73)."""
    def __init__(self, core, conns=()):
        self.core, self.conns, self.closed = core, list(conns), False

    def handle_request(self):
        if not self.conns:
            time.sleep(0.02)
            return
        sock, peer = self.conns.pop(0)
        try:
            U.Handler(sock, (peer, 40000), self)
        finally:
            sock.close()

    def server_close(self):
        self.closed = True


def http_request(method, path, host=HOST, cookie=None, origin=None, body=b"", length=None):
    lines = ["%s %s HTTP/1.1" % (method, path)]
    if host is not None: lines.append("Host: " + host)
    if cookie: lines.append("Cookie: " + cookie)
    if origin: lines.append("Origin: " + origin)
    if method == "POST": lines.append("Content-Length: %s" % (len(body) if length is None else length))
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body


def page_core(**kw):
    args = dict(items=[], token=TOKEN, cookie=COOKIE, hosts={HOST}, origins={"http://" + HOST}, clock=time.monotonic,
                submit=lambda decisions, typed: [], port=PORT)
    args.update(kw)
    return U.Core(**args)


class Client(threading.Thread):
    """Sends `chunks` with `gap` seconds between them, then reads the answer until the server closes."""
    def __init__(self, sock, chunks, gap=0.0):
        threading.Thread.__init__(self, daemon=True)
        self.sock, self.chunks, self.gap, self.answer, self.sent = sock, chunks, gap, b"", []

    def run(self):
        try:
            for c in self.chunks:
                self.sock.sendall(c)
                self.sent.append(time.monotonic())
                if self.gap: time.sleep(self.gap)
        except OSError:
            pass
        while True:
            try: data = self.sock.recv(65536)
            except OSError: break
            if not data: break
            self.answer += data

    def status(self):
        head = self.answer.split(b"\r\n", 1)[0].split()
        return int(head[1]) if len(head) > 1 else None

    def headers(self):
        head = self.answer.partition(b"\r\n\r\n")[0].decode("latin-1").split("\r\n")[1:]
        return [tuple(x.split(": ", 1)) for x in head if ": " in x]


def exchange(core, raw, peer="127.0.0.1"):
    server_end, client_end = socket.socketpair()
    client = Client(client_end, [raw])
    client.start()
    Wire(core, [(server_end, peer)]).handle_request()
    client.join(10)
    client_end.close()
    return client


class TestHandler(unittest.TestCase):
    """The HTTP layer a browser and a tailnet peer reach: raw bytes through the real Handler and Core."""
    def setUp(self):
        self.core = page_core()

    def send(self, raw, peer="127.0.0.1"):
        return exchange(self.core, raw, peer)

    def jar(self):
        first = self.send(http_request("GET", "/?t=" + TOKEN))
        self.assertEqual(first.status(), 303)
        return [v for k, v in first.headers() if k.lower() == "set-cookie"][0].split(";")[0]

    def test_the_first_token_request_gets_303_with_the_cookie_flags(self):
        first = self.send(http_request("GET", "/?t=" + TOKEN))
        self.assertEqual(first.status(), 303)
        headers = first.headers()
        cookie = [v for k, v in headers if k.lower() == "set-cookie"]
        self.assertEqual(len(cookie), 1)
        self.assertTrue(cookie[0].startswith("sm_%d=%s;" % (PORT, COOKIE)), cookie)
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"): self.assertIn(flag, cookie[0])
        self.assertEqual(dict(headers)["Location"], "/")

    def test_403_for_a_reused_token_no_token_a_wrong_token_and_a_wrong_or_missing_host(self):
        jar = self.jar()
        cases = [("reused token", http_request("GET", "/?t=" + TOKEN)),
                 ("no token", http_request("GET", "/")),
                 ("wrong token", http_request("GET", "/?t=" + "x" * 43)),
                 ("Host localhost", http_request("GET", "/", host="localhost:%d" % PORT, cookie=jar)),
                 ("Host other port", http_request("GET", "/", host="127.0.0.1:%d" % (PORT + 1), cookie=jar)),
                 ("no Host", http_request("GET", "/", host=None, cookie=jar))]
        self.assertEqual([(label, self.send(raw).status()) for label, raw in cases], [(label, 403) for label, _ in cases])
        self.assertEqual(self.send(http_request("GET", "/", cookie=jar)).status(), 200)

    def test_header_names_are_matched_in_any_case(self):
        jar = self.jar()
        raw = ("GET / HTTP/1.1\r\nhOsT: %s\r\ncOOKIE: %s\r\n\r\n" % (HOST, jar)).encode("ascii")
        self.assertEqual(self.send(raw).status(), 200)

    def test_403_for_a_post_with_a_foreign_or_missing_origin_or_no_cookie(self):
        jar = self.jar()
        form = b"challenge=AAAAAA"
        cases = [("foreign Origin", http_request("POST", "/decide", cookie=jar, origin="http://evil.example", body=form)),
                 ("null Origin", http_request("POST", "/decide", cookie=jar, origin="null", body=form)),
                 ("no Origin", http_request("POST", "/decide", cookie=jar, body=form)),
                 ("no cookie", http_request("POST", "/decide", origin="http://" + HOST, body=form))]
        self.assertEqual([(label, self.send(raw).status()) for label, raw in cases], [(label, 403) for label, _ in cases])

    def test_the_form_post_a_browser_sends_from_the_served_page_is_accepted(self):
        jar = self.jar()
        page = self.send(http_request("GET", "/", cookie=jar))
        self.assertEqual(page.status(), 200)
        origin = form_post_origin(dict(page.headers())["Referrer-Policy"], "http://" + HOST)
        self.assertEqual(self.send(http_request("POST", "/decide", cookie=jar, origin=origin,
                                                body=b"challenge=AAAAAA")).status(), 200)

    def test_413_for_an_oversized_or_unreadable_content_length_and_400_for_a_bad_form(self):
        jar = self.jar()
        origin = "http://" + HOST
        self.assertEqual(self.send(http_request("POST", "/decide", cookie=jar, origin=origin,
                                                length=U.BODY_MAX + 1)).status(), 413)
        self.assertEqual(self.send(http_request("POST", "/decide", cookie=jar, origin=origin, length="abc")).status(), 413)
        self.assertEqual(self.send(http_request("POST", "/decide", cookie=jar, origin=origin, body=b"zz")).status(), 400)
        self.assertEqual(self.send(http_request("POST", "/decide", cookie=jar, origin=origin,
                                                body=b"d99=approve&challenge=AAAAAA")).status(), 400)

    def test_core_answers_413_for_a_body_over_the_limit(self):
        req =U.Request("POST", "/decide", {}, {"host": HOST, "cookie": "sm_%d=%s" % (PORT, COOKIE),
                                                "origin": "http://" + HOST}, b"x" * (U.BODY_MAX + 1), "127.0.0.1")
        self.assertEqual(self.core.handle(req).status, 413)

    def test_the_whois_peer_is_the_transport_address(self):
        asked = []
        def whois(peer):
            asked.append(peer)
            return {"100.101.102.103": OWNER}.get(peer, "someone@example.com")
        self.core = page_core(whois=whois, owner=OWNER)
        self.assertEqual(self.send(http_request("GET", "/?t=" + TOKEN), peer="100.64.0.9").status(), 403)
        self.assertEqual(self.send(http_request("GET", "/?t=" + TOKEN), peer=PEER).status(), 303)
        self.assertEqual(asked, ["100.64.0.9", PEER])

    def test_no_answer_holds_the_token_or_the_cookie_and_nothing_is_printed(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            answers = [self.send(http_request("GET", "/?t=" + TOKEN)), self.send(http_request("GET", "/"))]
            answers.append(self.send(http_request("GET", "/", cookie="sm_%d=%s" % (PORT, COOKIE))))
        bodies = b"".join(a.answer.partition(b"\r\n\r\n")[2] for a in answers)
        self.assertNotIn(TOKEN.encode(), bodies); self.assertNotIn(COOKIE.encode(), bodies)
        self.assertEqual(err.getvalue(), "")


class TestConnectionDeadline(unittest.TestCase):
    """No connection holds serve() past the idle exit: a tailnet peer that is not the owner is answered before any
    request byte is read, and every other connection shares one wall-clock deadline (S1-r1-8)."""
    IDLE = 1.0

    def setUp(self):
        pt = mock.patch.object(U, "IDLE_SECONDS", self.IDLE)
        pt.start(); self.addCleanup(pt.stop)

    def test_a_tailnet_peer_that_is_not_the_owner_is_answered_before_it_sends_a_byte(self):
        asked = []
        core = page_core(hosts={"%s:%d" % (TAILNET_IP, PORT)}, whois=lambda p: asked.append(p) or "someone@example.com",
                         owner=OWNER)
        server_end, client_end = socket.socketpair()
        client = Client(client_end, [])
        client.start()
        start = time.monotonic()
        with mock.patch.object(U.Handler, "timeout", 3):
            Wire(core, [(server_end, "100.64.0.9")]).handle_request()
        took = time.monotonic() - start
        client.join(5)
        client_end.close()
        self.assertEqual(asked, ["100.64.0.9"])
        self.assertEqual(client.status(), 403)
        self.assertLess(took, 1.0)

    def test_a_trickling_peer_cannot_hold_serve_past_the_idle_exit(self):
        request = http_request("GET", "/?t=" + TOKEN, host="%s:%d" % (TAILNET_IP, PORT))
        for mode, extra in (("local", {}), ("tailnet owner", {"whois": lambda p: OWNER, "owner": OWNER})):
            with self.subTest(mode):
                core = page_core(hosts={"%s:%d" % (TAILNET_IP, PORT)}, **extra)
                server_end, client_end = socket.socketpair()
                client = Client(client_end, [request[i:i + 1] for i in range(len(request))], gap=0.1)
                client.start()
                start = time.monotonic()
                U.serve(Wire(core, [(server_end, PEER)]), core, time.monotonic)
                took = time.monotonic() - start
                client_end.close()
                client.join(10)
                self.assertTrue(core.done)
                self.assertIn("no request for 10 minutes", core.exit_reason)
                self.assertLess(took, self.IDLE + 0.5, "serve() held %.1f s against a %.1f s idle exit" % (took, self.IDLE))
                self.assertLess(len(client.sent), len(request))


class TestLaunchScript(unittest.TestCase):
    def test_the_script_changes_to_the_repository_and_passes_the_arguments(self):
        path = os.path.join(T.ROOT, "scripts", "approve-ui.sh")
        text = T.read(path)
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn('here=$(pwd)', text)
        self.assertIn('cd "$(dirname "$0")/.." || exit 1', text)
        self.assertIn('exec python3 skill_eval.py approve-ui --cwd "$here" "$@"', text)
        self.assertTrue(text.isascii())

    def test_cwd_resolves_relative_units(self):
        d = os.path.join("/tmp", "x-unit")
        self.assertEqual(se.resolve_under("/base", "rel/unit"), "/base/rel/unit")
        self.assertEqual(se.resolve_under("/base", d), d)


class TestUnderPty(H.GateFixture):
    """Lane 3: the page start under a pty whose real ancestor is this session's agent is refused by the ancestry
    check before any socket opens."""
    def test_the_page_start_under_a_pty_is_refused_by_the_ancestry_check(self):
        try:
            import pty
            master, slave = pty.openpty()
        except OSError as ex:
            self.skipTest("no pty device (%s)" % ex)
        self.addCleanup(os.close, master)
        H.require_agent_ancestor(self)
        p = T.REAL_POPEN([sys.executable, "-B", "skill_eval.py", "approve-ui", "--unit", self.d, "--workspace", self.ws],
                         cwd=T.ROOT, stdin=slave, stdout=slave, stderr=slave, env=self.child_env(()),
                         start_new_session=True)
        os.close(slave)
        out, deadline = b"", time.time() + 60
        while time.time() < deadline:
            r, _, _ = select.select([master], [], [], 0.5)
            if r:
                try: chunk = os.read(master, 4096)
                except OSError: break
                if not chunk: break
                out += chunk
            elif p.poll() is not None:
                break
        text = out.decode("utf-8", "replace")
        self.assertEqual(p.wait(timeout=60), 2, text)
        self.assertIn("approve-ui " + H.ANCESTOR, text)
        self.assertNotIn("http://", text)
        self.assert_nothing_started()


class TestNonTtyCli(H.GateFixture):
    """Lane 2 through the real command line: stdin is /dev/null, so the start is refused with the H1 text."""
    def test_the_cli_start_with_a_non_tty_stdin(self):
        with mock.patch.object(subprocess, "run", REAL_RUN), mock.patch.object(subprocess, "Popen", T.REAL_POPEN):
            rc, out, err = self.run_cli("approve-ui", "--unit", self.d, "--workspace", self.ws)
        self.assertEqual(rc, 2, err)
        self.assertIn("approve-ui needs a human at an interactive terminal (stdin is not a TTY)", err)
        self.assertNotIn("http://", out + err)
        self.assert_nothing_started()


if __name__ == "__main__":
    unittest.main()
