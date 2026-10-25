"""H3: the two named auth profiles of policy/auth-profiles.json, picked only by --auth-profile.

personal-mac keeps the A5 subscription rules byte for byte; company allows the listed API-key variables and gateways,
needs a hard --budget-usd and refuses every engine without a per-call cap. Every CLI here is a fake: subprocess is
patched for the whole module, live_exec is replaced or runs a patched subprocess.run, and every key is the throwaway
value below in a scrubbed temp environment. The ruleset root is a temp copy of the bundle wherever a test edits a
policy file. This file is ASCII only."""
import contextlib, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
from test_skill_eval import se, evals_doc, make_skill, write, wjson, rjson, quiet, claude_stream, FakeGraderExec
from engine import approvals as A, spend

ROOT = T.ROOT
KEY = "sk-test-not-a-key"
GATEWAY = "https://gw.unlisted.example"
LIVE_HEADER = "live gates (policy/gates.json, checked as for run --live, nothing executed):"
PERSONAL_LINE = ("auth profile: personal-mac (default: no --auth-profile given; a live start requires "
                 "--auth-profile)")
NEEDS_FLAG = ("a live run requires --auth-profile (personal-mac or company); the engine never detects the "
              "profile")
CAP_REQUIRED = "a hard cap is required in API-key mode: pass a finite --budget-usd > 0 (auth profile company)"
CODEX_REFUSED = "codex: no enforceable per-call cap in API-key mode (auth profile company); refusing it"
CODEX_GRADER_REFUSED = ("codex grader: no enforceable per-call cap in API-key mode (its budget is a soft limit); "
                        "refusing it")
CLAUDE_TAIL = " refused in API-key mode (auth profile company): --max-budget-usd is not an enforceable per-call cap; refusing it"
CLAUDE_REFUSED = "claude:" + CLAUDE_TAIL
CLAUDE_GRADER_REFUSED = "claude grader:" + CLAUDE_TAIL
CLAUDE_BUILDER_REFUSED = "claude builder:" + CLAUDE_TAIL
CLAUDE_REASONS = {"exec": CLAUDE_REFUSED, "trigger": CLAUDE_REFUSED, "grader": CLAUDE_GRADER_REFUSED,
                  "pairwise": CLAUDE_GRADER_REFUSED, "calibrate": CLAUDE_GRADER_REFUSED, "builder": CLAUDE_BUILDER_REFUSED}
TINY = ("--tokens", "exec=100,10", "--tokens", "trigger=100,10", "--tokens", "grader=100,10", "--tokens", "pairwise=100,10",
        "--tokens", "builder=100,10")
NEEDS_KEY = "claude: auth profile company needs one of ANTHROPIC_API_KEY set (value not shown)"
GATEWAY_REFUSED = ("ANTHROPIC_BASE_URL is set to a gateway that auth profile company does not list (value not shown); "
                   "unset it or list it in policy/auth-profiles.json")
PERSONAL_KEY_REFUSED = ("ANTHROPIC_API_KEY is set (value not shown): API-key auth is not allowed, eval runs use "
                        "subscription auth only; unset it")
API_KEY_TOTAL = "(API-key mode, auth profile company: billed per token against the hard cap --budget-usd)"
NO_REOPEN = " (--no-strict-budget does not reopen it)"
API_KEY_TRAILER = ("DRY RUN: nothing was executed. Real runs need --live --auth-profile company --budget-usd X (a hard "
                   "cap in USD: API-key calls are billed per token, and each call starts only when the spend so far plus "
                   "its per-call cap fits), no claude call (the claude engine, the claude grader and the builder are "
                   "refused in API-key mode: --max-budget-usd is not an enforceable per-call cap), no engine or grader "
                   "without a per-call cap (codex and the codex grader: no "
                   "enforceable per-call cap in API-key mode; --no-strict-budget does not reopen them), approved "
                   "requirements/eval_set, no validation or preflight problems, and an estimate within budget.")
A5_TRAILER = ("DRY RUN: nothing was executed. Real runs need --live --budget-usd X (API-equivalent USD), subscription "
              "auth only, approved requirements/eval_set, no validation or preflight problems, and an estimate within "
              "budget.")
BUNDLE_DIRS = ("rules", "schemas", "targets", "policy")
SHA = "ab" * 32

_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


def shipped():
    return rjson(os.path.join(ROOT, "policy", "auth-profiles.json"))


def file_sha(path):
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def states(out):
    lines = out.splitlines()
    i = lines.index(LIVE_HEADER)
    got = {}
    for x in lines[i + 1:]:
        m = re.match(r"  (\d+)\. ([a-z-]+): (.*)$", x)
        if not m: break
        got[m.group(2)] = m.group(3)
    return got


class Profiled(T.Base):
    """T.Base (temp home, fake auth status, the test approver) plus a temp ruleset root holding a copy of the bundle and
    of policy/auth-profiles.json, an approved skill and a workspace."""
    def setUp(self):
        T.Base.setUp(self)
        self.root = os.path.join(self.tmp, "bundle")
        for d in BUNDLE_DIRS:
            shutil.copytree(os.path.join(ROOT, d), os.path.join(self.root, d), ignore=shutil.ignore_patterns("__pycache__"))
        pt = mock.patch.object(se, "RULESET_ROOT", self.root); pt.start(); self.addCleanup(pt.stop)
        self.profiles = os.path.join(self.root, "policy", "auth-profiles.json")
        au = mock.patch.dict(se.AUTH, {"profile": None}); au.start(); self.addCleanup(au.stop)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def approved_skill(self, **kw):
        d = make_skill(self.tmp, **kw)
        os.makedirs(self.ws, exist_ok=True); write(os.path.join(self.ws, "requirements.md"), "goal")
        for g in ("requirements", "eval_set"): self.cli("approve", d, "--workspace", self.ws, "--gate", g)
        return d

    def edit_profiles(self, change):
        doc = rjson(self.profiles); change(doc); wjson(self.profiles, doc)

    def activate(self, name):
        se.AUTH["profile"] = se.load_auth_profiles()[name]

    def plan(self, *extra):
        rc, out, err = self.cli("plan", self.d, "--workspace", self.ws, *extra)
        self.assertEqual(rc, 0, err)
        return out


class TestLoader(Profiled):
    def refused(self, change, needle=""):
        self.edit_profiles(change)
        with self.assertRaises(se.EvalError) as cm: se.load_auth_profiles()
        self.assertTrue(str(cm.exception).startswith("policy/auth-profiles.json: "), cm.exception)
        self.assertIn(needle, str(cm.exception))

    def test_both_shipped_profiles_load(self):
        p = se.load_auth_profiles()
        self.assertEqual(sorted(p), ["company", "personal-mac"])
        pm, co = p["personal-mac"], p["company"]
        self.assertEqual((pm.billing, pm.account_shim, pm.subscription_checks), ("subscription", True, True))
        self.assertEqual(pm.engines, {"claude": {"api_key_env": (), "gateways": {}},
                                      "codex": {"api_key_env": (), "gateways": {}}})
        self.assertEqual((co.billing, co.account_shim, co.subscription_checks), ("api_key", False, False))
        self.assertEqual(co.engines, {"claude": {"api_key_env": ("ANTHROPIC_API_KEY",), "gateways": {}},
                                      "codex": {"api_key_env": (), "gateways": {}}})
        doc = shipped()
        self.assertEqual(sorted(doc), ["$comment", "policy", "profiles", "source", "version"])
        self.assertEqual((doc["policy"], doc["version"]), ("auth-profiles", "1.0.0"))

    def test_a_missing_or_non_strict_file_is_refused(self):
        os.remove(self.profiles)
        with self.assertRaises(se.EvalError) as cm: se.load_auth_profiles()
        self.assertTrue(str(cm.exception).startswith("policy/auth-profiles.json: "), cm.exception)
        for text in ("{", '{"policy": "auth-profiles", "policy": "auth-profiles"}', '{"x": NaN}'):
            write(self.profiles, text)
            with self.assertRaises(se.EvalError, msg=text) as cm: se.load_auth_profiles()
            self.assertTrue(str(cm.exception).startswith("policy/auth-profiles.json: "), cm.exception)

    def test_extra_or_missing_profiles_are_refused(self):
        self.refused(lambda d: d["profiles"].update(other=d["profiles"]["company"]), "profiles")
        self.refused(lambda d: d["profiles"].pop("other") and d["profiles"].pop("company"), "profiles")

    def test_unknown_or_missing_keys_are_refused(self):
        base = shipped()
        for change in (lambda d: d.update(extra=1), lambda d: d.pop("source"),
                       lambda d: d["profiles"]["company"].update(extra=1),
                       lambda d: d["profiles"]["company"].pop("label"),
                       lambda d: d["profiles"]["company"]["engines"].update(opencode={"api_key_env": [], "gateways": {}}),
                       lambda d: d["profiles"]["company"]["engines"]["claude"].update(extra=[]),
                       lambda d: d["profiles"]["company"]["engines"]["codex"].pop("gateways"),
                       lambda d: d["profiles"]["company"].update(billing="metered"),
                       lambda d: d["profiles"]["company"].update(account_shim=0)):
            wjson(self.profiles, base)
            self.refused(change)

    def test_a_relaxed_personal_mac_is_refused(self):
        base = shipped()
        for change in (lambda d: d["profiles"]["personal-mac"]["engines"]["claude"]["api_key_env"].append("ANTHROPIC_API_KEY"),
                       lambda d: d["profiles"]["personal-mac"].update(account_shim=False),
                       lambda d: d["profiles"]["personal-mac"].update(subscription_checks=False),
                       lambda d: d["profiles"]["personal-mac"].update(billing="api_key"),
                       lambda d: d["profiles"]["personal-mac"].update(label="relaxed")):
            wjson(self.profiles, base)
            self.refused(change, "personal-mac")

    def test_api_key_billing_with_the_shim_or_subscription_checks_is_refused(self):
        base = shipped()
        for change in (lambda d: d["profiles"]["company"].update(account_shim=True),
                       lambda d: d["profiles"]["company"].update(subscription_checks=True)):
            wjson(self.profiles, base)
            self.refused(change, "company")

    def test_a_bad_key_name_is_refused(self):
        base = shipped()
        for bad in ("MY_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN", 5):
            wjson(self.profiles, base)
            self.refused(lambda d: d["profiles"]["company"]["engines"]["claude"]["api_key_env"].append(bad), "api_key_env")
        wjson(self.profiles, base)
        self.refused(lambda d: d["profiles"]["company"]["engines"]["claude"]["api_key_env"].append("ANTHROPIC_API_KEY"),
                     "api_key_env")

    def test_a_bad_gateway_is_refused(self):
        base = shipped()
        gw = lambda var, urls: (lambda d: d["profiles"]["company"]["engines"]["claude"]["gateways"].update({var: urls}))
        for change in (gw("MY_BASE_URL", ["https://gw.example.com"]), gw("CLAUDE_CODE_USE_BEDROCK", ["https://gw.example.com"]),
                       gw("ANTHROPIC_BASE_URL", ["http://gw.example.com"]), gw("ANTHROPIC_BASE_URL", ["https://u:p@gw.example.com"]),
                       gw("ANTHROPIC_BASE_URL", ["https://gw.example.com/?a=1"]),
                       gw("ANTHROPIC_BASE_URL", ["https://gw.example.com/#x"]), gw("ANTHROPIC_BASE_URL", []),
                       gw("ANTHROPIC_BASE_URL", "https://gw.example.com"), gw("ANTHROPIC_BASE_URL", ["https://"])):
            wjson(self.profiles, base)
            self.refused(change, "gateway")
        wjson(self.profiles, base)
        self.edit_profiles(gw("ANTHROPIC_BASE_URL", ["https://gw.example.com:8443/v1"]))
        self.assertEqual(se.load_auth_profiles()["company"].engines["claude"]["gateways"],
                         {"ANTHROPIC_BASE_URL": ("https://gw.example.com:8443/v1",)})

    def test_only_commands_that_take_a_profile_load_the_file(self):
        self.assertNotIn("auth-profiles.json", se.POLICY_FILES)
        self.assertIsNone(se.AUTH["profile"])
        self.d = make_skill(self.tmp)
        write(self.profiles, "{}")
        rc, out, err = self.cli("validate", self.d)
        self.assertEqual(rc, 0, err); self.assertNotIn("auth-profiles", out + err)
        rc, _, err = self.cli("plan", self.d, "--workspace", self.ws)
        self.assertEqual(rc, 2); self.assertIn("error: policy/auth-profiles.json: ", err)


class TestSelection(Profiled):
    def setUp(self):
        Profiled.setUp(self)
        self.d = self.approved_skill(evals=evals_doc(human=False))

    def test_a_live_start_without_the_flag_is_refused_before_signing(self):
        shown = []
        T.install_test_approver(self, write=shown.append)
        gp = os.path.join(self.tmp, "golden.json")
        wjson(gp, {"examples": [{"prompt": "p", "output": "o", "item": "i", "label": True}]})
        se.run_iteration(se.load_eval_set(self.d)[0], self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=T.FakeClaude())],
                         runs=1, log=quiet)
        with mock.patch.object(se, "live_exec", T._forbidden):
            for argv in (["run"], ["grade"], ["calibrate", "--golden", gp], ["loop"], ["run", "--dry-run"],
                         ["grade", "--dry-run"]):
                rc, out, err = self.cli(argv[0], self.d, "--workspace", self.ws, "--live", "--budget-usd", "100",
                                        "--engines", "claude", *argv[1:])
                self.assertEqual(rc, 2, argv); self.assertIn("error: " + NEEDS_FLAG, err)
        self.assertEqual(shown, [])
        self.assertFalse(se._LIVE["on"])

    def test_the_budget_check_comes_first_and_company_names_the_hard_cap(self):
        for extra, want in (([], se.NO_BUDGET), (["--auth-profile", "personal-mac"], se.NO_BUDGET),
                            (["--auth-profile", "company"], CAP_REQUIRED)):
            rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--engines", "claude", *extra)
            self.assertEqual((rc, err.strip()), (2, "error: " + want), extra)
        rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--budget-usd", "inf", "--engines", "claude",
                              "--auth-profile", "company")
        self.assertEqual((rc, err.strip()), (2, "error: " + CAP_REQUIRED))

    def test_an_unknown_name_lists_both_profiles(self):
        want = "error: unknown --auth-profile 'nope': the profiles are personal-mac, company"
        for argv in (["plan"], ["estimate"], ["run", "--live", "--budget-usd", "1"]):
            rc, _, err = self.cli(argv[0], self.d, "--workspace", self.ws, "--auth-profile", "nope", *argv[1:])
            self.assertEqual((rc, err.strip()), (2, want), argv)

    def test_the_environment_never_picks_a_profile(self):
        with mock.patch.dict(os.environ, {"SKILL_EVAL_AUTH_PROFILE": "company", "AUTH_PROFILE": "company",
                                          "ANTHROPIC_API_KEY": KEY}):
            out = self.plan("--engines", "claude")
        self.assertIn(PERSONAL_LINE, out.splitlines())
        self.assertNotIn(KEY, out)

    def test_plan_without_the_flag_is_the_a5_plan_plus_one_line(self):
        rc, out, err = self.cli("plan", self.d, "--workspace", self.ws)
        self.assertEqual(rc, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[lines.index(PERSONAL_LINE) + 1][:6], "auth: ")
        a = se.build_parser().parse_args(["plan", self.d, "--workspace", self.ws])
        a.argv, a.grader_model = [], se.DEFAULT_GRADER_MODEL
        spec, problems = se.load_eval_set(self.d)
        engines = se.make_engines(a, spec)
        est = se.estimate(*se.spec_counts(spec, a), a.runs, [(e.name, e.model) for e in engines], a.baseline,
                          a.grader_model, se.DEFAULT_TOKENS, {}, grader_engine=a.grader_engine)
        self.assertIsNone(se.AUTH["profile"])
        a5 = se.plan_text(spec, problems, a, engines, os.path.realpath(self.ws), est, se.DEFAULT_TOKENS, {}).splitlines()
        self.assertEqual([x for x in lines if x != PERSONAL_LINE][:len(a5)], a5)
        self.assertEqual(len(states(out)), 11)
        self.assertEqual(states(out), states("\n".join(a5)))
        rc, out2, _ = self.cli("plan", self.d, "--workspace", self.ws, "--auth-profile", "personal-mac")
        self.assertIn("auth profile: personal-mac (billing subscription)", out2.splitlines())
        self.assertEqual(states(out2), states(out))


class TestPersonalMac(Profiled):
    def setUp(self):
        Profiled.setUp(self)
        self.d = self.approved_skill(evals=evals_doc(human=False))

    def test_an_api_key_is_refused_with_the_a5_text_in_plan(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}):
            for extra in ([], ["--auth-profile", "personal-mac"]):
                out = self.plan("--engines", "claude", *extra)
                self.assertEqual(states(out)["preflight"], "fail: preflight: " + PERSONAL_KEY_REFUSED)
                self.assertIn("  - " + PERSONAL_KEY_REFUSED, out.splitlines())
                self.assertNotIn(KEY, out)

    def test_an_api_key_is_refused_at_a_live_start(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch.object(se, "live_exec", T._forbidden):
            rc, out, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac",
                                    "--budget-usd", "100", "--engines", "claude", "--runs", "1")
        self.assertEqual(rc, 2); self.assertIn("preflight: " + PERSONAL_KEY_REFUSED, err)
        self.assertNotIn(KEY, out + err)

    def test_a_personal_live_run_keeps_the_subscription_check_and_the_shim(self):
        seen = []
        def fake_live(cmd, cwd, timeout, env=None):
            seen.append(se.with_account_marker({}, cmd))
            return T.FakeClaude()(cmd, cwd, timeout, env)
        with mock.patch.object(se, "live_exec", fake_live):
            rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "personal-mac",
                                  "--budget-usd", "100", "--engines", "claude", "--runs", "1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.status_calls, [se.CLAUDE_AUTH_STATUS_CMD])
        self.assertTrue(seen and all(e.get("CLAUDE_AS_CALLER") == "skill_eval" for e in seen))
        plan = rjson(os.path.join(self.ws, "iteration-1", "plan.json"))
        self.assertEqual((plan["auth_profile"], plan["billing"]), ("personal-mac", "subscription"))


class TestCompanyGatewaysAndKeys(Profiled):
    def setUp(self):
        Profiled.setUp(self)
        self.d = self.approved_skill(evals=evals_doc(human=False))

    def company_plan(self, env, *extra):
        with mock.patch.dict(os.environ, env):
            return self.plan("--engines", "claude", "--auth-profile", "company", "--budget-usd", "1", *extra)

    def test_an_unlisted_gateway_is_refused_in_plan(self):
        out = self.company_plan({"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_BASE_URL": GATEWAY})
        st = states(out)
        self.assertEqual(st["subscription-auth"], "fail: auth profile company: " + GATEWAY_REFUSED)
        self.assertEqual(st["preflight"], "fail: preflight: " + GATEWAY_REFUSED)
        self.assertIn("  - " + GATEWAY_REFUSED, out.splitlines())
        self.assertNotIn("gw.unlisted", out); self.assertNotIn(KEY, out)

    def live_start(self, env):
        """A company run start, refused first on Claude (R19-f1), and its --dry-run, which lists every live gate's
        refusal."""
        argv = ["run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "company", "--budget-usd", "1",
                "--engines", "claude", "--runs", "1"]
        with mock.patch.dict(os.environ, env), mock.patch.object(se, "live_exec", T._forbidden):
            rc, out, err = self.cli(*argv)
            self.assertEqual((rc, err.strip()), (2, "error: " + CLAUDE_REFUSED))
            rc, dry, _ = self.cli(*argv + ["--dry-run"])
        self.assertEqual(rc, 3)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "spend.jsonl")))
        return out + err, dry

    def test_an_unlisted_gateway_is_refused_at_a_live_start(self):
        shown, dry = self.live_start({"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_BASE_URL": GATEWAY})
        self.assertIn(GATEWAY_REFUSED, dry)
        self.assertNotIn("gw.unlisted", shown + dry)

    def test_a_listed_gateway_is_accepted_only_with_its_listed_url(self):
        self.edit_profiles(lambda d: d["profiles"]["company"]["engines"]["claude"]["gateways"].update(
            ANTHROPIC_BASE_URL=["https://gw.example.com/v1"]))
        out = self.company_plan({"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_BASE_URL": "https://gw.example.com/v1/"})
        self.assertEqual((states(out)["subscription-auth"], states(out)["preflight"]), ("pass", "pass"))
        self.assertNotIn("gw.example.com", out)
        out = self.company_plan({"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_BASE_URL": "https://gw.example.com/v2"})
        self.assertEqual(states(out)["preflight"], "fail: preflight: " + GATEWAY_REFUSED)
        out = self.company_plan({"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_FOUNDRY_BASE_URL": "https://gw.example.com/v1"})
        self.assertIn("ANTHROPIC_FOUNDRY_BASE_URL is set to a gateway that auth profile company does not list",
                      states(out)["preflight"])

    def test_an_unlisted_key_or_provider_routing_is_refused(self):
        out = self.company_plan({"ANTHROPIC_API_KEY": KEY, "OPENAI_API_KEY": KEY})
        self.assertEqual(states(out)["preflight"], "fail: preflight: OPENAI_API_KEY is set (value not shown): auth "
                         "profile company does not list it; unset it")
        out = self.company_plan({"ANTHROPIC_API_KEY": KEY, "CLAUDE_CODE_USE_BEDROCK": "1"})
        self.assertEqual(states(out)["preflight"], "fail: preflight: CLAUDE_CODE_USE_BEDROCK is set (value not shown): "
                         "provider routing that auth profile company does not list; unset it")
        self.assertNotIn(KEY, out)

    def test_the_listed_key_is_required(self):
        out = self.company_plan({})
        self.assertEqual(states(out)["subscription-auth"], "fail: auth profile company: " + NEEDS_KEY)
        self.assertEqual(states(out)["preflight"], "pass")
        out = self.company_plan({"ANTHROPIC_API_KEY": KEY})
        self.assertEqual(states(out)["subscription-auth"], "pass")
        self.assertIn(NEEDS_KEY, self.live_start({})[1])

    def test_company_runs_no_subscription_check(self):
        out = self.company_plan({"ANTHROPIC_API_KEY": KEY})
        self.assertNotIn(se.AUTH_STATUS_SKIPPED, out)
        self.assertIn("auth profile: company (billing api_key)", out.splitlines())
        os.remove(self.auth)  # no ChatGPT login: not a company problem
        self.activate("company")
        self.assertEqual(se.preflight(se.load_eval_set(self.d)[0], ["claude", "codex"], self.runs), [])
        self.assertEqual(self.status_calls, [])


class TestCompanyChildren(Profiled):
    ENV = {"ANTHROPIC_API_KEY": KEY, "OPENAI_API_KEY": KEY, "CODEX_API_KEY": KEY, "ANTHROPIC_BASE_URL": GATEWAY}

    def run_children(self, profile):
        """The child env of a Claude and a Codex live_exec by argv[0]; under company the Claude child is refused
        (R19-f1) and has no entry."""
        self.activate(profile)
        seen = {}
        def fake_run(cmd, **kw):
            seen[cmd[0]] = kw["env"]; return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.dict(os.environ, self.ENV), mock.patch.object(subprocess, "run", fake_run), \
                mock.patch.dict(se._LIVE, {"on": True}):
            if se.api_key_mode():
                with self.assertRaises(se.LiveDisabled): se.live_exec(["claude", "-p", "x"], self.tmp, 1)
            else:
                se.live_exec(["claude", "-p", "x"], self.tmp, 1)
            se.live_exec(["/opt/bin/codex", "exec", "x"], self.tmp, 1, env=dict(os.environ))
        return seen

    def listed(self, cmd):
        with mock.patch.dict(os.environ, self.ENV):
            return se.listed_child_env(cmd)

    def test_the_listed_key_reaches_a_claude_child_and_never_a_codex_child(self):
        seen = self.run_children("company")
        self.assertNotIn("claude", seen)
        assert_key_and_scrub(self, self.listed(["claude", "-p", "x"]))
        self.assertEqual(self.listed(["/opt/bin/codex", "exec", "x"]), {})
        self.assertFalse(set(se.NON_SUBSCRIPTION_ENV) & set(seen["/opt/bin/codex"]))
        seen = self.run_children("personal-mac")
        self.assertFalse(set(se.NON_SUBSCRIPTION_ENV) & (set(seen["claude"]) | set(seen["/opt/bin/codex"])))

    def test_a_listed_gateway_reaches_only_its_engine(self):
        self.edit_profiles(lambda d: d["profiles"]["company"]["engines"]["claude"]["gateways"].update(
            ANTHROPIC_BASE_URL=[GATEWAY]))
        seen = self.run_children("company")
        self.assertEqual(self.listed(["claude", "-p", "x"]).get("ANTHROPIC_BASE_URL"), GATEWAY)
        self.assertNotIn("ANTHROPIC_BASE_URL", self.listed(["/opt/bin/codex", "exec", "x"]))
        self.assertNotIn("ANTHROPIC_BASE_URL", seen["/opt/bin/codex"])

    def test_no_account_marker_under_company(self):
        os.makedirs(os.path.join(self.home, ".claude-acct1"))
        self.activate("personal-mac")
        self.assertEqual(self.run_children("personal-mac")["claude"]["CLAUDE_AS_CALLER"], "skill_eval")
        self.assertEqual(se.switch_account_config_dirs(), [os.path.join(self.home, ".claude-acct1")])
        self.run_children("company")
        for k in ("CLAUDE_AS_CALLER", "CLAUDE_AS_AUTO_SWITCH"):
            self.assertNotIn(k, se.with_account_marker({}, ["claude", "-p", "x"]))
            self.assertNotIn(k, se.claude_child_env())
        self.assertEqual(se.switch_account_config_dirs(), [])
        self.assertNotIn("CLAUDE_AS_CALLER", se.with_account_marker({"CLAUDE_AS_CALLER": "x"}, ["claude", "-p"]))

    def test_no_account_marker_on_the_builder_child_under_company(self):
        os.makedirs(os.path.join(self.home, ".claude-acct1"))
        cmd = se.ClaudeBuilder("Fix it.", "claude-sonnet-5-5", None, {}, skill_name="branch-setup").command(
            "Fix it.", 0.5, self.tmp)
        self.assertIn("--permission-mode", cmd)
        for profile in ("personal-mac", "company"):
            self.activate(profile)
            seen = []
            def fake_run(argv, **kw):
                seen.append(kw["env"]); return mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), \
                    mock.patch.object(subprocess, "run", fake_run), mock.patch.dict(se._LIVE, {"on": True}):
                if profile == "personal-mac":
                    se.live_exec(cmd, self.tmp, 1)
                else:
                    with self.assertRaises(se.LiveDisabled): se.live_exec(cmd, self.tmp, 1)
            if profile == "personal-mac":
                self.assertEqual(seen[0].get("CLAUDE_AS_CALLER"), "skill_eval")
            else:
                self.assertEqual(seen, [])
                for k in ("CLAUDE_AS_CALLER", "CLAUDE_AS_AUTO_SWITCH"):
                    self.assertNotIn(k, se.with_account_marker({}, cmd), k)

    def test_api_key_source_is_checked(self):
        eng = se.ClaudeCodeEngine("claude-opus-5-5")
        res = lambda src: {"init": {"skills": [], "plugins": [], "apiKeySource": src}}
        self.activate("company")
        self.assertEqual(eng.isolation_problems(res("ANTHROPIC_API_KEY"), False, "x", []), [])
        for src in ("none", "ANTHROPIC_AUTH_TOKEN", None):
            probs = eng.isolation_problems(res(src), False, "x", [])
            self.assertEqual(len(probs), 1, src); self.assertIn("apiKeySource", probs[0])
            self.assertIn("expected one of ANTHROPIC_API_KEY (auth profile company", probs[0])
        self.assertIn("'none'", eng.isolation_problems(res("none"), False, "x", [])[0])
        self.activate("personal-mac")
        self.assertEqual(eng.isolation_problems(res("none"), False, "x", []), [])
        self.assertEqual(len(eng.isolation_problems(res("ANTHROPIC_API_KEY"), False, "x", [])), 1)


class TestCompanyCodex(Profiled):
    def setUp(self):
        Profiled.setUp(self)
        self.d = self.approved_skill(evals=evals_doc(human=False))

    def test_the_you_see_command_refuses_codex(self):
        out = self.plan("--auth-profile", "company", "--engines", "claude,codex", "--budget-usd", "1")
        self.assertEqual(states(out)["strict-budget"], "fail: " + CODEX_REFUSED)
        line = next(x for x in out.splitlines() if x.startswith("budget enforcement: "))
        self.assertIn(CODEX_REFUSED, line[len("budget enforcement: "):].split(" | "))
        self.assertIn("no enforceable per-call cap in API-key mode", out)
        out = self.plan("--auth-profile", "company", "--engines", "claude,codex", "--budget-usd", "1", "--no-strict-budget")
        self.assertEqual(states(out)["strict-budget"], "fail: " + CODEX_REFUSED)

    def test_the_lane_3_phrase(self):
        st = states(self.plan("--auth-profile", "company", "--engines", "claude"))
        self.assertEqual((st["per-call-bound"], st["estimate-within-budget"]), ("fail: " + CAP_REQUIRED,) * 2)
        st = states(self.plan("--engines", "claude"))
        self.assertEqual(st["per-call-bound"], "fail: " + se.NO_BUDGET)

    def test_the_codex_engine_is_refused_at_a_live_start_even_with_no_strict(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch.object(se, "live_exec", T._forbidden):
            for extra in ([], ["--no-strict-budget"]):
                rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "company",
                                      "--budget-usd", "1", "--engines", "claude,codex", "--price", "codex=1,1", *extra)
                self.assertEqual(rc, 2, extra); self.assertIn(CODEX_REFUSED, err)

    def test_the_codex_grader_is_refused_in_the_gate_state_and_at_a_live_start(self):
        se.run_iteration(se.load_eval_set(self.d)[0], self.ws, [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=T.FakeClaude())],
                         runs=1, log=quiet)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch.object(se, "live_exec", T._forbidden):
            rc, out, _ = self.cli("grade", self.d, "--workspace", self.ws, "--live", "--dry-run", "--auth-profile",
                                  "company", "--budget-usd", "5", "--grader-engine", "codex")
            self.assertEqual(rc, 3); self.assertIn("  - " + CODEX_GRADER_REFUSED, out.splitlines())
            for extra in ([], ["--no-strict-budget"]):
                rc, _, err = self.cli("grade", self.d, "--workspace", self.ws, "--live", "--auth-profile", "company",
                                      "--budget-usd", "5", "--grader-engine", "codex", *extra)
                self.assertEqual(rc, 2, extra); self.assertIn(CODEX_GRADER_REFUSED, err)
        a = se.build_parser().parse_args(["grade", self.d, "--grader-engine", "codex", "--budget-usd", "5"])
        self.activate("company")
        report = []
        se._live_strict_budget(se._LiveGates(a, None, [], self.ws, 1, [], {}, {}, 5.0, None, report))
        self.assertEqual(report, [CODEX_GRADER_REFUSED])
        with self.assertRaises(se.EvalError) as cm:
            se._live_strict_budget(se._LiveGates(a, None, [], self.ws, 1, [], {}, {}, 5.0, None, None))
        self.assertEqual(str(cm.exception), CODEX_GRADER_REFUSED)

    def test_spend_refuses_codex_calls_that_skip_the_gate(self):
        from engine import spend
        import test_round10
        self.activate("company")
        b = se.Budget(5.0)
        b.ledger = spend.Ledger(self.ws, "grade", "company", "api_key", 5.0)
        b.ledger.open([])
        self.addCleanup(b.ledger.close)
        fx = test_round10.FakeCodexGrader()
        with self.assertRaises(se.EvalError) as cm:
            se.CodexGrader(exec_fn=fx, budget=b).grade(test_round10.CASE, {"final": "x", "tool_calls": []},
                                                        test_round10.ITEMS)
        self.assertEqual(str(cm.exception), CODEX_GRADER_REFUSED)
        self.assertEqual(fx.calls, [])
        fc = T.FakeCodex()
        n = se.run_iteration(se.load_eval_set(self.d)[0], self.ws, [se.CodexEngine(exec_fn=fc)], runs=1, budget=b,
                             prices={"codex": (1.0, 1.0)}, log=quiet)
        self.assertEqual(rjson(os.path.join(self.ws, "iteration-%d" % n, "plan.json"))["stopped"], CODEX_REFUSED)
        self.assertEqual(fc.calls, [])
        rows = spend.read_rows(os.path.join(self.ws, "spend.jsonl"))[0]
        self.assertEqual([(r["type"], r["decision"], r["engine"], r["reason"]) for r in rows],
                         [("intent", "refused", "codex", CODEX_GRADER_REFUSED), ("intent", "refused", "codex", CODEX_REFUSED)])

    def test_the_codex_grader_is_refused_under_loop_at_the_gate_and_in_spend(self):
        from engine import spend
        import test_round10
        self.activate("company")
        for extra in ([], ["--no-strict-budget"]):
            a = se.build_parser().parse_args(["loop", self.d, "--grader-engine", "codex", "--budget-usd", "5",
                                              "--engines", "claude", *extra])
            report = []
            se._live_strict_budget(se._LiveGates(a, None, [], self.ws, 1, [], {}, {}, 5.0, None, report))
            self.assertEqual(report, [CODEX_GRADER_REFUSED], extra)
        b = se.Budget(5.0)
        b.ledger = spend.Ledger(self.ws, "loop", "company", "api_key", 5.0)
        b.ledger.open([])
        self.addCleanup(b.ledger.close)
        fx = test_round10.FakeCodexGrader()
        for kind in ("grader", "pairwise", "calibrate"):
            g = se.CodexGrader(exec_fn=fx, budget=b)
            with self.assertRaises(se.EvalError) as cm, se.spend_scope(g, kind=kind, iteration=1):
                g.grade(test_round10.CASE, {"final": "x", "tool_calls": []}, test_round10.ITEMS)
            self.assertEqual(str(cm.exception), CODEX_GRADER_REFUSED, kind)
        self.assertEqual(fx.calls, [])
        rows = spend.read_rows(os.path.join(self.ws, "spend.jsonl"))[0]
        self.assertEqual([(r["command"], r["kind"], r["decision"], r["reason"]) for r in rows],
                         [("loop", k, "refused", CODEX_GRADER_REFUSED) for k in ("grader", "pairwise", "calibrate")])


class TestCompanyText(Profiled):
    """Under company the plan, estimate and dry-run text state the API-key facts; personal-mac keeps the A5 text."""
    def setUp(self):
        Profiled.setUp(self)
        self.d = self.approved_skill(evals=evals_doc(human=False))

    def trailer(self, out):
        return next(x for x in out.splitlines() if x.startswith("DRY RUN: "))

    def total(self, out):
        return next(x for x in out.splitlines() if x.startswith("total calls "))

    def test_the_company_plan_states_billing_the_cap_and_the_codex_refusal(self):
        out = self.plan("--auth-profile", "company", "--engines", "claude,codex", "--budget-usd", "1")
        self.assertIn(API_KEY_TOTAL, self.total(out))
        self.assertIn("budget: " + CODEX_REFUSED + NO_REOPEN, out.splitlines())
        self.assertNotIn("--no-strict-budget allows it", out)
        self.assertEqual(self.trailer(out), API_KEY_TRAILER)
        self.assertNotIn("subscription", self.total(out) + self.trailer(out))

    def test_the_company_plan_states_the_codex_grader_refusal(self):
        out = self.plan("--auth-profile", "company", "--engines", "claude", "--grader-engine", "codex", "--budget-usd", "1")
        lines = out.splitlines()
        self.assertIn("grader budget: " + CODEX_GRADER_REFUSED + NO_REOPEN, lines)
        self.assertIn("grader isolation: none: the codex grader never starts in API-key mode", lines)
        self.assertNotIn("SOFT LIMIT", out)
        self.assertNotIn(se.CODEX_GRADER_NOTE, out)

    def test_company_estimate_and_dry_run_text(self):
        rc, out, err = self.cli("estimate", self.d, "--workspace", self.ws, "--auth-profile", "company",
                                "--engines", "claude,codex")
        self.assertEqual(rc, 0, err)
        self.assertIn(API_KEY_TOTAL, self.total(out))
        self.assertIn("budget: " + CODEX_REFUSED + NO_REOPEN, out.splitlines())
        rc, out, err = self.cli("run", self.d, "--workspace", self.ws, "--auth-profile", "company", "--engines", "claude",
                                "--budget-usd", "1")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.trailer(out), API_KEY_TRAILER)

    def test_personal_mac_keeps_the_a5_text(self):
        for extra in ([], ["--auth-profile", "personal-mac"]):
            out = self.plan("--engines", "claude,codex", *extra)
            self.assertIn("(subscription: not billed per token)", self.total(out))
            self.assertIn("budget: " + se.SOFT_LIMIT_NOTE % "codex", out.splitlines())
            self.assertEqual(self.trailer(out), A5_TRAILER)
            out = self.plan("--engines", "claude", "--grader-engine", "codex", *extra)
            self.assertIn("grader isolation: " + se.CODEX_GRADER_NOTE, out.splitlines())
            self.assertTrue(any(x.startswith("grader budget: SOFT LIMIT (codex has no per-call cap)")
                                for x in out.splitlines()))


class TestBindingAndHashes(Profiled):
    def setUp(self):
        Profiled.setUp(self)
        self.d = self.approved_skill(evals=evals_doc(human=False))

    def test_the_profile_file_does_not_move_the_ruleset_and_approvals_stay_valid(self):
        with_file = se.ruleset_sha256()
        spec = se.load_eval_set(self.d)[0]
        self.assertTrue(se.approval_ok("eval_set", spec, self.ws))
        self.edit_profiles(lambda d: d["profiles"]["company"].update(label="Company, edited"))
        self.assertEqual(se.ruleset_sha256(), with_file)
        self.assertTrue(se.approval_ok("eval_set", spec, self.ws))
        os.remove(self.profiles)
        self.assertEqual(se.ruleset_sha256(), with_file)
        with mock.patch.object(se, "RULESET_ROOT", ROOT):
            self.assertEqual(se.ruleset_sha256(), with_file)

    def test_plan_json_records_the_profile_and_its_hash(self):
        """spend.API_KEY_REFUSED is patched to () so the stub Claude stands in for a capped engine: plan.json of a company
        iteration has no route but a CLI run."""
        with mock.patch.object(spend, "API_KEY_REFUSED", ()), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), \
                mock.patch.object(se, "live_exec", lambda cmd, cwd, timeout, env=None: (
                    0, claude_stream("feat/x", usage=(10, 1, 0), cost=0.0001, api_key_source="ANTHROPIC_API_KEY",
                                     skills=["branch-setup"] if "--disable-slash-commands" not in cmd else []), "")):
            rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "company",
                                  "--budget-usd", "1", "--engines", "claude", "--runs", "1", "--tokens", "exec=100,10",
                                  "--tokens", "trigger=100,10")
        self.assertEqual(rc, 0, err)
        plan = rjson(os.path.join(self.ws, "iteration-1", "plan.json"))
        self.assertEqual((plan["auth_profile"], plan["billing"]), ("company", "api_key"))
        self.assertEqual(plan["policy_sha256"]["auth_profiles"], file_sha(self.profiles))
        case = se.load_eval_set(self.d)[0]["cases"][0]
        timing = rjson(os.path.join(self.ws, "iteration-1", "claude", se.eval_dir_name(case), "with_skill", "run-1",
                                    "timing.json"))
        self.assertEqual(timing["billing"], "api_key")

    def test_live_plan_hash_moves_with_the_profile_file(self):
        a = se.build_parser().parse_args(["run", self.d, "--live", "--budget-usd", "1", "--auth-profile", "company"])
        a.argv = ["run"]
        spec = se.load_eval_set(self.d)[0]
        before = se.live_plan_hash(a, spec, self.ws)
        self.edit_profiles(lambda d: d["profiles"]["company"].update(label="Company, edited"))
        self.assertNotEqual(se.live_plan_hash(a, spec, self.ws), before)

    def test_a_profile_edit_between_signing_and_start_refuses(self):
        """spend.API_KEY_REFUSED is patched to () so the stub Claude stands in for a capped engine: a company start that
        gets signed has no route but the CLI."""
        def edit_then_type(prompt):
            self.edit_profiles(lambda d: d["profiles"]["company"].update(label="Company, edited"))
            return T.type_challenge(prompt)
        T.install_test_approver(self, read_line=edit_then_type)
        with mock.patch.object(spend, "API_KEY_REFUSED", ()), mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), \
                mock.patch.object(se, "live_exec", T._forbidden):
            rc, _, err = self.cli("run", self.d, "--workspace", self.ws, "--live", "--auth-profile", "company",
                                  "--budget-usd", "1", "--engines", "claude", "--runs", "1", "--tokens", "exec=100,10",
                                  "--tokens", "trigger=100,10")
        self.assertEqual(rc, 2); self.assertIn("inputs changed after signing", err)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "spend.jsonl")))


SCRUB = "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"


def assert_key_and_scrub(case, env, name="claude"):
    """env is exactly the listed key plus SCRUB=1; a failure names the variables, never the key value."""
    case.assertTrue(env == {"ANTHROPIC_API_KEY": KEY, SCRUB: "1"},
                    "%s: want ANTHROPIC_API_KEY and %s=1, got %s" % (name, SCRUB, sorted(env)))


class TestCompanyChildScrub(Profiled):
    """R18-f1, kept as defence in depth: in API-key mode listed_child_env gives a Claude argv the listed key together
    with CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1, which the parent environment cannot turn off. No Claude child starts under
    company since R19-f1 (TestCompanyRefusesClaude.test_c4_guard_exec)."""
    def listed(self, profile, cmd, parent=None):
        if profile: self.activate(profile)
        env = dict({"HOME": self.home, "PATH": os.path.join(self.tmp, "bin"), "ANTHROPIC_API_KEY": KEY}, **(parent or {}))
        with mock.patch.dict(os.environ, env, clear=True):
            return se.listed_child_env(cmd)

    def child_env(self, profile, cmd):
        if profile: self.activate(profile)
        bin_dir = os.path.join(self.tmp, "bin"); os.makedirs(bin_dir, exist_ok=True)
        seen = []
        def fake_run(argv, **kw):
            seen.append(kw["env"]); return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.dict(os.environ, {"HOME": self.home, "PATH": bin_dir, "ANTHROPIC_API_KEY": KEY}, clear=True), \
                mock.patch.object(se.subprocess, "run", fake_run), mock.patch.dict(se._LIVE, {"on": True, "gated": False}):
            se.live_exec(cmd, self.tmp, 1)
        self.assertEqual(len(seen), 1)
        return seen[0]

    def executor_argv(self):
        cmd = se.ClaudeCodeEngine("claude-opus-5-5").command("p", True)
        self.assertFalse(se.claude_bash_disabled(cmd))
        return cmd

    def test_f1_scrub_exec_argv_gets_key_and_scrub(self):
        assert_key_and_scrub(self, self.listed("company", self.executor_argv()))

    def test_f1_scrub_parent_zero_is_overridden(self):
        assert_key_and_scrub(self, self.listed("company", self.executor_argv(), {SCRUB: "0"}))

    def test_f1_scrub_tools_free_argvs_keep_key_and_scrub(self):
        grader = se.LLMGrader("claude-sonnet-5-5").command("p", {"type": "object"}, 0.5)
        builder = se.ClaudeBuilder("Fix it.", "claude-sonnet-5-5", None, {}, skill_name="branch-setup").command(
            "Fix it.", 0.5, self.tmp)
        for name, cmd in (("grader", grader), ("builder", builder)):
            self.assertTrue(se.claude_bash_disabled(cmd), name)
            assert_key_and_scrub(self, self.listed("company", cmd), name)

    def test_f1_scrub_pin_unchanged_paths(self):
        for profile in ("personal-mac", None):
            se.AUTH["profile"] = None
            self.assertEqual(self.listed(profile, self.executor_argv()), {}, profile)
            env = self.child_env(profile, self.executor_argv())
            self.assertNotIn("ANTHROPIC_API_KEY", env, profile)
            self.assertNotIn(SCRUB, env, profile)
        self.assertEqual(self.listed("company", ["/opt/bin/codex", "exec", "x"]), {})
        env = self.child_env("company", ["/opt/bin/codex", "exec", "x"])
        self.assertFalse(set(se.NON_SUBSCRIPTION_ENV) & set(env))
        self.assertNotIn(SCRUB, env)


class TestCompanyRefusesClaude(Profiled):
    """R19-f1 option (c): in API-key mode every Claude call (exec, trigger, grader, pairwise, calibrate, builder) is
    refused before it starts and before any spend (plan.md:902). The CLI cases run in a scrubbed environment with the
    real live_exec and a recorder in place of subprocess.run."""
    def setUp(self):
        Profiled.setUp(self)
        self.d = self.approved_skill(evals=evals_doc(human=False))
        self.gp = os.path.join(self.tmp, "golden.json")
        wjson(self.gp, {"examples": [{"prompt": "p", "output": "feat/x", "item": "names a branch", "label": True}]})
        self.bp = os.path.join(self.tmp, "builder.md")
        write(self.bp, "Fix the skill.")
        self.started = []

    def record(self, argv, **kw):
        self.started.append(argv)
        return mock.Mock(returncode=0, stdout="", stderr="")

    @contextlib.contextmanager
    def scrubbed(self):
        bin_dir = os.path.join(self.tmp, "bin"); os.makedirs(bin_dir, exist_ok=True)
        with mock.patch.dict(os.environ, {"HOME": self.home, "PATH": bin_dir, "ANTHROPIC_API_KEY": KEY}, clear=True), \
                mock.patch.object(se.subprocess, "run", self.record):
            yield

    def workspace_files(self):
        return sorted(os.path.relpath(os.path.join(dp, f), self.ws) for dp, _, fs in os.walk(self.ws) for f in fs)

    def subscription_iteration(self):
        se.run_iteration(se.load_eval_set(self.d)[0], self.ws,
                         [se.ClaudeCodeEngine("claude-opus-5-5", exec_fn=T.FakeClaude())], runs=1, log=quiet)

    def assert_start_refused(self, reason, cmd, *extra):
        before = self.workspace_files()
        with self.scrubbed():
            rc, out, err = self.cli(cmd, self.d, "--workspace", self.ws, "--live", "--auth-profile", "company",
                                    "--budget-usd", "0.05", *extra)
        self.assertEqual(self.started, [], "a Claude call started")
        self.assertFalse(os.path.exists(os.path.join(self.ws, "spend.jsonl")), "a Claude call was admitted")
        self.assertEqual((rc, err.strip()), (2, "error: " + reason))
        self.assertEqual(self.workspace_files(), before)
        self.assertFalse(se._LIVE["on"])
        self.assertNotIn(KEY, out + err)

    def test_c4_cli_run_refused(self):
        self.assert_start_refused(CLAUDE_REFUSED, "run", "--engines", "claude", "--runs", "1", *TINY)

    def test_c4_cli_grade_refused(self):
        self.subscription_iteration()
        self.assert_start_refused(CLAUDE_GRADER_REFUSED, "grade", "--engines", "claude", *TINY)

    def test_c4_cli_calibrate_refused(self):
        self.subscription_iteration()
        self.assert_start_refused(CLAUDE_GRADER_REFUSED, "calibrate", "--engines", "claude", "--golden", self.gp, *TINY)

    def test_c4_cli_loop_refused(self):
        self.assert_start_refused(CLAUDE_REFUSED, "loop", "--engines", "claude", "--runs", "1", "--builder-prompt", self.bp,
                                  "--golden", self.gp, "--max-iterations", "2", "--run-root", self.runs, *TINY)

    def test_c4_guard_exec(self):
        self.activate("company")
        argvs = (("exec", se.ClaudeCodeEngine("claude-opus-5-5").command("p", True)),
                 ("grader", se.LLMGrader("claude-sonnet-5-5").command("p", {"type": "object"}, 0.5)),
                 ("builder", se.ClaudeBuilder("Fix it.", "claude-sonnet-5-5", None, {}, skill_name="branch-setup").command(
                     "Fix it.", 0.5, self.tmp)))
        with self.scrubbed(), mock.patch.dict(se._LIVE, {"on": True, "gated": False}):
            for name, cmd in argvs:
                with self.assertRaises(se.LiveDisabled, msg=name) as cm: se.live_exec(cmd, self.tmp, 1)
                self.assertIn(CLAUDE_REFUSED, str(cm.exception), name)
            self.assertEqual(self.started, [], "a Claude child started")
            se.live_exec(["/opt/bin/codex", "exec", "x"], self.tmp, 1)
        self.assertEqual([argv[0] for argv in self.started], ["/opt/bin/codex"])

    def test_c4_plan_lists(self):
        golden = os.path.join(ROOT, "golden", "skills", "csv-cleaner")
        rc, out, err = self.cli("plan", golden, "--auth-profile", "company", "--engines", "claude,codex", "--budget-usd", "1")
        self.assertEqual(rc, 0, err)
        line = next(x for x in out.splitlines() if x.startswith("budget enforcement: "))
        self.assertEqual(line[len("budget enforcement: "):].split(" | "),
                         [CODEX_REFUSED, CLAUDE_REFUSED, CLAUDE_GRADER_REFUSED])
        self.assertEqual(states(out)["strict-budget"], "fail: " + CODEX_REFUSED)
        hint = next(x for x in out.splitlines() if x.startswith("DRY RUN: "))
        self.assertIn("--max-budget-usd is not an enforceable per-call cap", hint)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch.object(se, "live_exec", T._forbidden):
            rc, out, _ = self.cli("run", self.d, "--workspace", self.ws, "--live", "--dry-run", "--auth-profile", "company",
                                  "--engines", "claude,codex", "--budget-usd", "1")
            self.assertEqual(rc, 3)
            self.assertIn("  - " + CLAUDE_REFUSED, out.splitlines())
            rc, out, _ = self.cli("loop", self.d, "--workspace", self.ws, "--live", "--dry-run", "--auth-profile", "company",
                                  "--engines", "claude", "--budget-usd", "1", "--builder-prompt", self.bp)
            self.assertEqual(rc, 3)
            for reason in (CLAUDE_REFUSED, CLAUDE_GRADER_REFUSED, CLAUDE_BUILDER_REFUSED):
                self.assertIn("  - " + reason, out.splitlines())
        self.assertFalse(os.path.exists(os.path.join(self.ws, "spend.jsonl")))

    def test_c4_pin_soft_texts(self):
        from engine import spend
        self.assertEqual((spend.no_cap_reason("exec", "codex", "company"), spend.no_cap_reason("grader", "codex", "company")),
                         (CODEX_REFUSED, CODEX_GRADER_REFUSED))
        self.activate("company")
        self.assertEqual(se.api_key_soft_refusals([se.CodexEngine()], "codex", True, True),
                         [CODEX_REFUSED, CODEX_GRADER_REFUSED])
        led = spend.Ledger(self.ws, "run", "company", "api_key", 1.0); led.open([]); self.addCleanup(led.close)
        t = led.admit("exec", "codex", None, 1, 0.0, None, 0.02, False)
        self.assertEqual((t.decision, t.reason), ("refused", CODEX_REFUSED))
        self.assertEqual([(r["type"], r["decision"], r["engine"], r["reason"], r["call_id"])
                          for r in spend.read_rows(led.path)[0]],
                         [("intent", "refused", "codex", CODEX_REFUSED, led.run_id + ":1")])

    def test_c4_pin_listed_env(self):
        cmd = se.ClaudeCodeEngine("claude-opus-5-5").command("p", True)
        with self.scrubbed():
            for profile in ("personal-mac", None):
                se.AUTH["profile"] = None
                if profile: self.activate(profile)
                self.assertEqual(se.listed_child_env(cmd), {}, profile)


if __name__ == "__main__":
    unittest.main()
