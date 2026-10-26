"""Unit B6: `skill_eval.py install` (engine/install.py, policy/install.json) and the agent records it relies on.

A project install writes a unit's generated wrappers (or a skill folder) under --target after every check passed, with a
dry run, collision refusal, a backup before overwrite and no change on a repeat run. A user-scope install writes only
$HOME/.claude/agents/<name>.md for a registered agent, after a signature of the manifest hash. Every HOME here is a temp
folder, every signature uses the throwaway test key of tests/test_skill_eval.py, subprocess is patched for the whole
module, and every record fixture is built by test code in a temp folder. ASCII only."""
import contextlib, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_agent_eval as AE
import test_human_gate as H
from test_skill_eval import se, eva, write, wjson, rjson
from test_agent_eval import read

ROOT = T.ROOT
NAME = AE.NAME
AGENT_DESTS = (".claude/agents/%s.md" % NAME, ".codex/agents/%s.toml" % NAME, ".opencode/agents/%s.md" % NAME,
               ".hermes/profiles/%s/SOUL.md" % NAME)
WRAPPERS = ("wrappers/claude/%s.md" % NAME, "wrappers/codex/%s.toml" % NAME, "wrappers/opencode/%s.md" % NAME,
            "wrappers/hermes/SOUL.md")
HERMES_NOTE = "hermes: config.yaml UNVERIFIED, not written (gates.md G4)"
STAMP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z(-[0-9]+)?$")
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


def install_module():
    from engine import install
    return install


def cli(*argv):
    return AE.cli(*argv)


def agent_copy(parent, version="1.0.0", name=NAME):
    def edit(dst):
        if version is None: return
        p = os.path.join(dst, "AGENT.md")
        write(p, read(p).replace("targets: [", "version: %s\ntargets: [" % version, 1))
    return AE.generated_copy(parent, edit, name)


def tree(root):
    out = {}
    if not os.path.lexists(root): return out
    for dp, dn, fn in os.walk(root):
        for n in dn + fn:
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if os.path.islink(p): out[rel] = ("link", os.readlink(p))
            elif os.path.isfile(p):
                with open(p, "rb") as f: out[rel] = (f.read(), os.stat(p).st_mtime_ns)
    return out


def fake_iteration(agent_dir, ws, run_root):
    from engine import agent_eval
    agent = se.Agent(agent_dir)
    eng = se.AgentClaudeEngine(model=se.DEFAULT_EXECUTOR_MODELS["claude"], exec_fn=AE.FakeAgentExec())
    n = se.agent_run_iteration(agent, None, ws, [eng], se.agent_cells(agent, [eng]), 1, se.Budget(5.0), se.PRICES,
                               se.DEFAULT_TOKENS, run_root, log=T.quiet)
    agent_eval.grade_runs(os.path.join(ws, "iteration-%d" % n), agent.dir, agent.scenarios,
                          lambda d, p: eva.grade_transcript(d, p).checks, log=T.quiet)
    return n


class Base(T.Base):
    def setUp(self):
        T.Base.setUp(self)
        self.agent = agent_copy(os.path.join(self.tmp, "agents"))
        self.ws = os.path.join(self.tmp, "agents", NAME + "-workspace")
        self.target = os.path.join(self.tmp, "proj"); os.makedirs(self.target)

    def install(self, *extra, unit=None, target=True):
        args = ["install", unit or self.agent] + (["--target", self.target] if target else []) + list(extra)
        return cli(*args)

    def wrapper(self, i):
        return read(os.path.join(self.agent, *WRAPPERS[i].split("/")), "rb")

    def register_agent(self):
        register_agent_at(self, self.agent, self.ws, self.runs)


def register_agent_at(case, agent, ws, runs):
    """A fake claude iteration, signed waivers for the other three tools, the registration gate and the record, all
    signed by the case's test approver."""
    fake_iteration(agent, ws, runs)
    for tool in ("codex", "opencode", "hermes"):
        rc, out, err = cli("approve", agent, "--gate", "coverage_waiver:" + tool)
        case.assertEqual(rc, 0, out + err)
    rc, out, err = cli("agent", "report", agent)
    case.assertEqual(rc, 3, out + err)
    case.assertNotIn("coverage-missing", out)
    rc, out, err = cli("approve", agent, "--gate", "registration")
    case.assertEqual(rc, 0, out + err)
    rc, out, err = cli("register", agent)
    case.assertEqual(rc, 0, out + err)
    case.assertEqual(se.registration_state(agent, ws)[:2], ("registered", ""))


def badge(page, key):
    m = re.search(r'data-badge="%s">([^<]*)<' % re.escape(key), page)
    return m.group(1) if m else None


class TestProjectInstall(Base):
    def test_dry_run_prints_four_creates_and_writes_nothing(self):
        rc, out, err = self.install("--dry-run")
        self.assertEqual(rc, 0, out + err)
        lines = out.splitlines()
        for rel in AGENT_DESTS:
            self.assertEqual(len([ln for ln in lines if ln.startswith("create " + rel)]), 1, (rel, out))
        hermes = next(ln for ln in lines if ln.startswith("create " + AGENT_DESTS[3]))
        self.assertIn("UNVERIFIED", hermes)
        self.assertNotIn("UNVERIFIED", next(ln for ln in lines if ln.startswith("create " + AGENT_DESTS[0])))
        self.assertIn(HERMES_NOTE, lines)
        self.assertEqual(lines[-1], "dry run: install %s (agent) -> %s: 4 create, 0 overwrite, 0 unchanged"
                         % (NAME, os.path.realpath(self.target)))
        self.assertEqual(os.listdir(self.target), [])

    def test_install_then_a_repeat_run_changes_nothing(self):
        rc, out, err = self.install()
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(out.splitlines()[-1].endswith(": 4 create, 0 overwrite, 0 unchanged"), out)
        for i, rel in enumerate(AGENT_DESTS):
            self.assertEqual(read(os.path.join(self.target, *rel.split("/")), "rb"), self.wrapper(i))
        before = tree(self.target)
        self.assertEqual(sorted(before), sorted(AGENT_DESTS))
        rc, out, err = self.install()
        self.assertEqual(rc, 0, out + err)
        for rel in AGENT_DESTS:
            self.assertIn("unchanged " + rel, out)
        self.assertIn("no change", out.splitlines())
        self.assertTrue(any(ln.endswith(": 0 create, 0 overwrite, 4 unchanged") for ln in out.splitlines()), out)
        self.assertEqual(tree(self.target), before)

    def test_a_collision_refuses_without_force_and_backs_up_before_overwrite_with_force(self):
        dest = os.path.join(self.target, *AGENT_DESTS[0].split("/"))
        os.makedirs(os.path.dirname(dest))
        old = b"---\nname: %s\ndescription: an older hand-written agent\n---\nOld body.\n" % NAME.encode()
        with open(dest, "wb") as f: f.write(old)
        before = tree(self.target)
        rc, out, err = self.install()
        self.assertEqual(rc, 2, out + err)
        self.assertIn(AGENT_DESTS[0], err)
        self.assertIn("--force", err)
        self.assertEqual(tree(self.target), before)
        rc, out, err = self.install("--force", "--dry-run")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("overwrite " + AGENT_DESTS[0], out)
        self.assertIn("-Old body.", out)
        self.assertTrue(any(ln.startswith("+++ ") for ln in out.splitlines()), out)
        self.assertEqual(tree(self.target), before)
        rc, out, err = self.install("--force")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(out.splitlines()[-1].endswith(": 3 create, 1 overwrite, 0 unchanged"), out)
        self.assertEqual(read(dest, "rb"), self.wrapper(0))
        backups = os.path.join(self.target, ".unit-install-backup")
        stamps = os.listdir(backups)
        self.assertEqual(len(stamps), 1)
        self.assertRegex(stamps[0], STAMP_RE)
        self.assertEqual(read(os.path.join(backups, stamps[0], *AGENT_DESTS[0].split("/")), "rb"), old)
        self.assertEqual(sorted(tree(os.path.join(backups, stamps[0]))), [AGENT_DESTS[0]])

    def test_a_second_backup_in_the_same_second_gets_a_suffix(self):
        dest = os.path.join(self.target, *AGENT_DESTS[0].split("/"))
        os.makedirs(os.path.dirname(dest))
        write(dest, "first\n")
        with mock.patch.object(se, "install_stamp", lambda: "20261026T120000Z"):
            os.makedirs(os.path.join(self.target, ".unit-install-backup", "20261026T120000Z"))
            rc, out, err = self.install("--force", "--tools", "claude")
        self.assertEqual(rc, 0, out + err)
        got = os.path.join(self.target, ".unit-install-backup", "20261026T120000Z-2", *AGENT_DESTS[0].split("/"))
        self.assertEqual(read(got), "first\n")

    def test_a_same_named_agent_in_another_file_refuses_even_with_force(self):
        for rel, text in ((".claude/agents/other.md", "---\nname: %s\ndescription: x\n---\nbody\n" % NAME),
                          (".codex/agents/other.toml", 'name = "%s"\ndescription = "x"\n' % NAME)):
            with self.subTest(rel):
                p = os.path.join(self.target, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                write(p, text)
                before = tree(self.target)
                rc, out, err = self.install("--force")
                self.assertEqual(rc, 2, out + err)
                self.assertIn(rel, err)
                self.assertIn("same agent name", err)
                self.assertEqual(tree(self.target), before)
                os.remove(p)

    def test_a_same_named_agent_with_a_trailing_comment_refuses_even_with_force(self):
        for i, (rel, text) in enumerate((
                (".claude/agents/other.md", "---\nname: %s # note\ndescription: x\n---\nbody\n" % NAME),
                (".claude/agents/other.md", "---\nname: '%s'  # note\ndescription: x\n---\nbody\n" % NAME),
                (".codex/agents/other.toml", 'name = "%s" # c\ndescription = "x"\n' % NAME))):
            with self.subTest(i):
                p = os.path.join(self.target, *rel.split("/"))
                os.makedirs(os.path.dirname(p), exist_ok=True)
                write(p, text)
                before = tree(self.target)
                rc, out, err = self.install("--force")
                self.assertEqual(rc, 2, out + err)
                self.assertIn(rel, err)
                self.assertIn("same agent name", err)
                self.assertEqual(tree(self.target), before)
                os.remove(p)

    def test_a_stale_or_missing_wrapper_refuses_and_names_generate(self):
        p = os.path.join(self.agent, "AGENT.md")
        good = read(p)
        write(p, good.replace("plain Korean", "plain English"))
        rc, out, err = self.install("--dry-run")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("python3 eva.py generate", err)
        write(p, good)
        os.remove(os.path.join(self.agent, *WRAPPERS[1].split("/")))
        rc, out, err = self.install("--tools", "codex")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("python3 eva.py generate", err)
        self.assertEqual(os.listdir(self.target), [])

    def test_hermes_installs_soul_md_only_and_marks_config_yaml(self):
        rc, out, err = self.install("--tools", "hermes")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(HERMES_NOTE, out.splitlines())
        self.assertEqual(sorted(k for k, v in tree(self.target).items() if isinstance(v[0], bytes)), [AGENT_DESTS[3]])
        self.assertEqual(read(os.path.join(self.target, *AGENT_DESTS[3].split("/")), "rb"), self.wrapper(3))

    def test_tools_and_scope_options(self):
        rc, out, err = self.install("--tools", "claude,cursor")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("cursor", err)
        rc, out, err = self.install("--tools", "codex", "--dry-run")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual([ln.split()[1] for ln in out.splitlines() if ln.startswith("create ")], [AGENT_DESTS[1]])
        rc, out, err = self.install("--dry-run", target=False)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("--target", err)

    def test_a_skill_installs_every_regular_file_and_refuses_a_link(self):
        d = T.make_skill(os.path.join(self.tmp, "skills"))
        rc, out, err = self.install("--tools", "claude,codex", unit=d)
        self.assertEqual(rc, 0, out + err)
        files = sorted(rel for rel, v in tree(d).items() if isinstance(v[0], bytes))
        for base in (".claude/skills/branch-setup/", ".agents/skills/branch-setup/"):
            for rel in files:
                self.assertEqual(read(os.path.join(self.target, *(base + rel).split("/")), "rb"),
                                 read(os.path.join(d, *rel.split("/")), "rb"))
        self.assertTrue(out.splitlines()[-1].startswith("install branch-setup (skill) -> "), out)
        rc, out, err = self.install("--tools", "opencode,hermes", "--dry-run", unit=d)
        self.assertEqual(rc, 0, out + err)
        for base in (".opencode/skills/branch-setup/SKILL.md", ".hermes/skills/branch-setup/SKILL.md"):
            self.assertIn("UNVERIFIED", next(ln for ln in out.splitlines() if ln.startswith("create " + base)))
        os.symlink(os.path.join(self.tmp, "home"), os.path.join(d, "outside"))
        before = tree(self.target)
        rc, out, err = self.install("--tools", "claude", "--force", unit=d)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("outside", err)
        self.assertEqual(tree(self.target), before)


class TestDestinationSafety(Base):
    def home_dirs(self):
        return {d: tree(os.path.join(self.home, d)) for d in (".claude", ".codex", ".config/opencode", ".hermes", ".agents")}

    def test_a_target_equal_to_home_refuses_and_writes_nothing(self):
        before = self.home_dirs()
        rc, out, err = cli("install", self.agent, "--target", self.home)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("home folder", err)
        self.assertEqual(self.home_dirs(), before)
        rc, out, err = cli("install", self.agent, "--target", os.path.dirname(self.home), "--dry-run")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("ancestor of the home folder", err)

    def test_a_target_that_resolves_into_a_user_scope_folder_refuses(self):
        for d in (".claude", ".codex", ".config/opencode", ".hermes", ".agents"):
            with self.subTest(d):
                real = os.path.join(self.home, *d.split("/"))
                os.makedirs(real, exist_ok=True)
                link = os.path.join(self.tmp, "link-" + d.replace("/", "-"))
                os.symlink(real, link)
                before = self.home_dirs()
                rc, out, err = cli("install", self.agent, "--target", link, "--force")
                self.assertEqual(rc, 2, out + err)
                self.assertIn("forbidden", err)
                self.assertEqual(self.home_dirs(), before)

    def test_a_destination_link_to_a_forbidden_file_refuses_even_with_force(self):
        settings = os.path.join(self.home, ".claude", "settings.json")
        os.makedirs(os.path.dirname(settings)); write(settings, '{"keep": true}\n')
        dest = os.path.join(self.target, *AGENT_DESTS[0].split("/"))
        os.makedirs(os.path.dirname(dest))
        os.symlink(settings, dest)
        before, home_before = tree(self.target), self.home_dirs()
        for extra in ((), ("--force",)):
            rc, out, err = self.install(*extra)
            self.assertEqual(rc, 2, out + err)
            self.assertIn(AGENT_DESTS[0], err)
            self.assertIn("link", err)
        self.assertEqual(read(settings), '{"keep": true}\n')
        self.assertEqual((tree(self.target), self.home_dirs()), (before, home_before))

    def test_a_link_inside_the_target_that_leads_out_refuses_and_writes_nothing(self):
        outside = os.path.join(self.tmp, "elsewhere"); os.makedirs(outside)
        os.symlink(outside, os.path.join(self.target, ".codex"))
        rc, out, err = self.install("--force")
        self.assertEqual(rc, 2, out + err)
        self.assertIn(AGENT_DESTS[1], err)
        self.assertIn("outside the target", err)
        self.assertEqual(os.listdir(outside), [])
        self.assertEqual(sorted(os.listdir(self.target)), [".codex"], "no folder and no backup was created")

    def test_a_parent_that_is_a_file_refuses_before_any_write(self):
        write(os.path.join(self.target, ".codex"), "a file, not a folder\n")
        rc, out, err = self.install("--force")
        self.assertEqual(rc, 2, out + err)
        self.assertIn(AGENT_DESTS[1], err)
        self.assertEqual(sorted(os.listdir(self.target)), [".codex"], "no other destination was written")

    def test_a_link_inside_the_target_that_stays_inside_is_followed(self):
        inner = os.path.join(self.target, "real-codex"); os.makedirs(inner)
        os.symlink(inner, os.path.join(self.target, ".codex"))
        rc, out, err = self.install("--tools", "codex")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(read(os.path.join(inner, "agents", NAME + ".toml"), "rb"), self.wrapper(1))

    def test_the_target_checks_cover_both_homes(self):
        I = install_module()
        pol = I.load_policy(os.path.join(ROOT, "policy", "install.json"))
        a, b = os.path.join(self.tmp, "home-a"), os.path.join(self.tmp, "home-b")
        homes = (os.path.realpath(a), os.path.realpath(b))
        for t, word in ((a, "is the home folder"), (b, "is the home folder"), (self.tmp, "ancestor"),
                        (os.path.join(b, ".codex", "x"), "forbidden"), (os.path.join(a, "Library", "Keychains"),
                                                                        "forbidden"),
                        (os.path.join(a, ".ssh"), "forbidden")):
            with self.subTest(t):
                probs = I.target_problems(os.path.realpath(t), homes, pol)
                self.assertTrue(probs and word in probs[0], probs)
        self.assertEqual(I.target_problems(os.path.realpath(os.path.join(a, "work", "proj")), homes, pol), [])

    def assert_refused(self, target):
        before = tree(self.home)
        rc, out, err = cli("install", self.agent, "--target", target, "--force")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("nothing was written", err)
        self.assertEqual(tree(self.home), before)

    def test_a_target_spelled_in_another_case_refuses_and_writes_nothing(self):
        upper = os.path.join(self.tmp, "HOME")
        if not os.path.isdir(upper): self.skipTest("this volume is case-sensitive, so HOME is another folder")
        os.makedirs(os.path.join(self.home, ".claude", "agents"))
        link = os.path.join(self.tmp, "link-agents")
        os.symlink(os.path.join(self.home, ".Claude", "agents"), link)
        for t in (upper, os.path.join(self.home, ".CLAUDE"), link):
            with self.subTest(t):
                self.assert_refused(t)

    def test_the_home_through_the_data_volume_firmlink_refuses(self):
        real = os.path.realpath(self.home)
        if not real.startswith(("/private/", "/Users/")):
            self.skipTest("the temp home %s is under neither /private nor /Users" % real)
        firm = "/System/Volumes/Data" + real
        if not os.path.isdir(firm): self.skipTest("%s does not exist on this system" % firm)
        os.makedirs(os.path.join(self.home, ".claude", "agents"))
        for t in (firm, os.path.join(firm, ".claude")):
            with self.subTest(t):
                self.assert_refused(t)

    def test_a_destination_inside_the_target_under_a_forbidden_root_refuses(self):
        config = os.path.join(self.home, ".config")
        opencode = os.path.join(config, "opencode"); os.makedirs(opencode)
        os.symlink(opencode, os.path.join(config, ".claude"))
        before = tree(self.home)
        rc, out, err = cli("install", self.agent, "--target", config, "--tools", "claude", "--force")
        self.assertEqual(rc, 2, out + err)
        self.assertIn(AGENT_DESTS[0], err)
        self.assertIn("forbidden", err)
        self.assertEqual(tree(self.home), before)

    def test_a_linked_backup_folder_refuses_before_any_write(self):
        dest = os.path.join(self.target, *AGENT_DESTS[0].split("/"))
        os.makedirs(os.path.dirname(dest)); write(dest, "older\n")
        outside = os.path.join(self.tmp, "outside-backup"); os.makedirs(outside)
        inside = os.path.join(self.target, "kept"); os.makedirs(inside)
        backup = os.path.join(self.target, ".unit-install-backup")
        for where in (outside, inside):
            with self.subTest(where):
                os.symlink(where, backup)
                before = (tree(self.target), tree(outside))
                rc, out, err = self.install("--force", "--tools", "claude")
                self.assertEqual(rc, 2, out + err)
                self.assertIn("backup folder", err)
                self.assertEqual((tree(self.target), tree(outside)), before)
                os.remove(backup)

    def test_the_loader_fixes_the_user_scope_destination(self):
        I = install_module()
        src = rjson(os.path.join(ROOT, "policy", "install.json"))
        p = os.path.join(self.tmp, "install.json")
        for path in (".claude/hooks/{name}.md", ".codex/agents/{name}.md", "agents/{name}.md"):
            with self.subTest(path):
                doc = json.loads(json.dumps(src))
                doc["user_scope"]["agent"]["claude"]["path"] = path
                wjson(p, doc)
                with self.assertRaises(I.InstallError) as cm: I.load_policy(p)
                self.assertIn("user_scope.agent.claude", str(cm.exception))

    def test_the_user_scope_check_refuses_a_forbidden_file(self):
        I = install_module()
        pol = I.load_policy(os.path.join(ROOT, "policy", "install.json"))
        home = os.path.realpath(self.home)
        check = I.user_check(home, pol, (home,))
        for rel in (".claude/hooks/x.md", ".claude/settings.json"):
            with self.subTest(rel):
                p = os.path.join(home, *rel.split("/"))
                self.assertIn("forbidden", " ".join(check(rel, p, p)))
        p = os.path.join(home, ".claude", "agents", "x.md")
        self.assertEqual(check("x.md", p, p), [])

    def test_the_policy_floor_refuses_a_policy_that_drops_a_named_root_or_file(self):
        I = install_module()
        src = rjson(os.path.join(ROOT, "policy", "install.json"))
        for key, item in (("forbidden_roots", ".claude"), ("forbidden_roots", ".codex"),
                          ("forbidden_roots", ".config/opencode"), ("forbidden_roots", ".hermes"),
                          ("forbidden_roots", ".agents"), ("forbidden_roots", "Library/Keychains"),
                          ("forbidden_files", ".claude/settings.json"), ("forbidden_files", ".claude/settings.local.json"),
                          ("forbidden_files", ".claude/hooks"), ("forbidden_files", ".codex/config.toml"),
                          ("forbidden_files", ".codex/auth.json")):
            with self.subTest(item):
                doc = json.loads(json.dumps(src))
                self.assertIn(item, doc[key])
                doc[key] = [x for x in doc[key] if x != item]
                p = os.path.join(self.tmp, "install.json")
                wjson(p, doc)
                with self.assertRaises(I.InstallError) as cm: I.load_policy(p)
                self.assertIn(item, str(cm.exception))
        doc = json.loads(json.dumps(src)); doc["user_scope"]["agent"]["codex"] = dict(doc["user_scope"]["agent"]["claude"])
        wjson(p, doc)
        with self.assertRaises(I.InstallError) as cm: I.load_policy(p)
        self.assertIn("Q12", str(cm.exception))

    def test_the_manifest_hash_binds_every_field(self):
        I = install_module()
        entry = {"destination": "/h/.claude/agents/a.md", "source": "/u/a/wrappers/claude/a.md", "source_sha256": "a" * 64}
        base = dict(format=1, scope="user", tool="claude", unit="a", registration_hash="b" * 64, entries=[entry])
        h = I.manifest_hash(base)
        self.assertRegex(h, "^[0-9a-f]{64}$")
        for k, v in (("scope", "project"), ("tool", "codex"), ("unit", "b"), ("registration_hash", "c" * 64),
                     ("format", 2), ("entries", [dict(entry, destination="/h/.claude/agents/b.md")]),
                     ("entries", [dict(entry, source="/u/b")]), ("entries", [dict(entry, source_sha256="d" * 64)]),
                     ("entries", [])):
            with self.subTest(k):
                self.assertNotEqual(I.manifest_hash(dict(base, **{k: v})), h)


class TestUserScope(Base):
    def setUp(self):
        Base.setUp(self)
        self.prompts = []
        def read_line(prompt):
            self.prompts.append(prompt)
            return T.type_challenge(prompt)
        T.install_test_approver(self, read_line=read_line)
        self.user_dest = os.path.join(os.path.realpath(self.home), ".claude", "agents", NAME + ".md")

    def user(self, *extra, tools="claude", unit=None):
        return cli("install", unit or self.agent, "--scope", "user", "--tools", tools, *extra)

    def challenges(self):
        return [p for p in self.prompts if "to sign" in p]

    def test_other_tools_and_skills_are_refused_naming_q12(self):
        for tools in ("codex", "opencode", "hermes", "claude,codex", "claude,codex,opencode,hermes"):
            with self.subTest(tools):
                rc, out, err = self.user(tools=tools)
                self.assertEqual(rc, 2, out + err)
                self.assertIn("Q12", err)
        rc, out, err = cli("install", self.agent, "--scope", "user")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("Q12", err)
        d = T.make_skill(os.path.join(self.tmp, "skills"))
        rc, out, err = self.user(unit=d)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("Q12", err)
        rc, out, err = self.user("--target", self.target)
        self.assertEqual(rc, 2, out + err)
        self.assertEqual((self.prompts, os.listdir(self.home)), ([], [".codex"]))

    def test_an_unregistered_unit_refuses_before_the_challenge(self):
        rc, out, err = self.user()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("unregistered", err)
        self.assertEqual(self.challenges(), [])
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".claude")))

    def test_claude_config_dir_and_a_linked_claude_folder_refuse(self):
        self.register_agent()
        del self.prompts[:]
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": os.path.join(self.home, ".claude")}):
            rc, out, err = self.user()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("CLAUDE_CONFIG_DIR", err)
        elsewhere = os.path.join(self.tmp, "elsewhere-claude"); os.makedirs(os.path.join(elsewhere, "agents"))
        os.symlink(elsewhere, os.path.join(self.home, ".claude"))
        rc, out, err = self.user()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("link", err)
        self.assertEqual(os.listdir(os.path.join(elsewhere, "agents")), [])
        self.assertEqual(self.challenges(), [])

    def test_an_unsigned_user_scope_writes_nothing(self):
        self.register_agent()
        del self.prompts[:]
        with mock.patch.dict(se.APPROVER, {"it": T.gate_approver(read_line=lambda p: "WRONG\n")}):
            rc, out, err = self.user()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("challenge mismatch", err)
        self.assertFalse(os.path.lexists(self.user_dest))
        self.assertNotIn("install_user", rjson(os.path.join(self.ws, "approvals.json")))

    def test_a_signed_user_scope_install_lands_in_the_temp_home(self):
        self.register_agent()
        del self.prompts[:]
        rc, out, err = self.user("--dry-run")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.challenges(), [])
        self.assertIn("registration: registered 1.0.0", out)
        self.assertIn("create " + self.user_dest, out)
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".claude")))
        rc, out, err = self.user()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.challenges()), 1)
        self.assertEqual(read(self.user_dest, "rb"), self.wrapper(0))
        self.assertEqual(sorted(tree(os.path.join(self.home, ".claude"))), ["agents/%s.md" % NAME])
        rec = rjson(os.path.join(self.ws, "approvals.json"))["install_user"]
        reg = rjson(os.path.join(self.ws, "registration.json"))
        I = install_module()
        want = I.manifest_hash({"format": 1, "scope": "user", "tool": "claude", "unit": NAME,
                                "registration_hash": se.registration_hash(reg),
                                "entries": [{"destination": self.user_dest,
                                             "source": os.path.join(os.path.realpath(self.agent), *WRAPPERS[0].split("/")),
                                             "source_sha256": hashlib.sha256(self.wrapper(0)).hexdigest()}]})
        self.assertEqual(rec["sha256"], want)
        self.assertEqual(se.APPROVER["it"].verdict(rec, "approve", "install_user", NAME, want, se.ruleset_sha256()).status,
                         "approved")
        self.assertEqual(se.registration_state(self.agent, self.ws)[0], "registered", "the stored record keeps it valid")
        del self.prompts[:]
        rc, out, err = self.user()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("no change", out.splitlines())
        self.assertEqual(self.challenges(), [], "a repeat run writes nothing and signs nothing")

    def test_a_source_changed_after_signing_refuses(self):
        self.register_agent()
        wrapper = os.path.join(self.agent, *WRAPPERS[0].split("/"))
        real = se.APPROVER["it"]

        class Signer(object):
            def __getattr__(self, k): return getattr(real, k)
            def sign(self, kind, gate, prepare, budget_usd=None):
                grant = real.sign(kind, gate, prepare, budget_usd=budget_usd)
                with open(wrapper, "ab") as f: f.write(b"\nplanted\n")
                return grant
        with mock.patch.dict(se.APPROVER, {"it": Signer()}):
            rc, out, err = self.user()
        self.assertEqual(rc, 2, out + err)
        self.assertFalse(os.path.lexists(self.user_dest))

    def test_a_record_changed_after_signing_refuses_and_writes_nothing(self):
        self.register_agent()
        real, test = se.APPROVER["it"], self

        class Signer(object):
            def __getattr__(self, k): return getattr(real, k)
            def sign(self, kind, gate, prepare, budget_usd=None):
                grant = real.sign(kind, gate, prepare, budget_usd=budget_usd)
                if gate == "install_user":
                    with mock.patch.object(se, "now_iso", lambda: "2030-01-01T00:00:00Z"):
                        rc, out, err = cli("register", test.agent)
                    test.assertEqual(rc, 0, out + err)
                    test.assertEqual(se.registration_state(test.agent, test.ws)[:2], ("registered", ""))
                return grant
        with mock.patch.dict(se.APPROVER, {"it": Signer()}):
            rc, out, err = self.user()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("changed after signing", err)
        self.assertFalse(os.path.lexists(self.user_dest))
        self.assertNotIn("install_user", rjson(os.path.join(self.ws, "approvals.json")))

    def test_a_policy_cannot_move_the_user_scope_write(self):
        self.register_agent()
        pol = se.install_policy()
        moved = pol._replace(user_scope={"agent": {"claude": dict(pol.user_scope["agent"]["claude"],
                                                                  path=".claude/hooks/{name}.md")}})
        with mock.patch.object(se, "install_policy", lambda: moved):
            rc, out, err = self.user()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(read(self.user_dest, "rb"), self.wrapper(0))
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".claude", "hooks")))

    def test_a_record_that_no_longer_verifies_refuses(self):
        self.register_agent()
        del self.prompts[:]
        write(os.path.join(self.agent, "notes.md"), "more\n")
        rc, out, err = self.user()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("stale", err)
        self.assertEqual(self.challenges(), [])
        self.assertFalse(os.path.lexists(self.user_dest))


class TestCliHumanGate(Base, H.Cli):
    def setUp(self):
        Base.setUp(self)
        self.cli_setup()

    def test_a_non_tty_subprocess_is_refused_and_writes_nothing(self):
        env = self.child_env(drop=("CLAUDE_CONFIG_DIR",))
        p = T.REAL_POPEN([sys.executable, "-B", "skill_eval.py", "install", self.agent, "--scope", "user", "--tools",
                          "claude"], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=env)
        out, err = p.communicate(timeout=60)
        err = err.decode("utf-8", "replace")
        self.assertEqual(p.returncode, 2, err)
        self.assertEqual(err.splitlines()[0], H.NOT_TTY % "approve", err)
        self.assertEqual(os.listdir(self.chome), [])
        self.assertFalse(os.path.exists(os.path.join(self.ws, "approvals.json")))
        self.assert_nothing_started()

    def test_an_agent_ancestor_is_refused_in_process(self):
        A = H.gate_module()
        H.require_agent_ancestor(self)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(A, "is_tty", lambda fd: True), \
                mock.patch.dict(se.APPROVER, {"it": A.cli_approver()}), \
                mock.patch.object(A.Approver, "key_problems", H.sentinel), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = se.main(["install", self.agent, "--scope", "user", "--tools", "claude"])
        self.assertEqual(rc, 2, err.getvalue())
        self.assertIn("approve " + H.ANCESTOR, err.getvalue())
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".claude")))


class TestAgentRecords(Base):
    def test_an_agent_waiver_clears_exactly_its_tool(self):
        fake_iteration(self.agent, self.ws, self.runs)
        rc, out, err = cli("agent", "report", self.agent)
        self.assertEqual(rc, 3, out + err)
        for tool in ("codex", "opencode", "hermes"):
            self.assertIn("coverage-missing:%s" % tool, out)
        self.assertEqual(cli("approve", self.agent, "--gate", "coverage_waiver:codex")[0], 0)
        rc, out, err = cli("agent", "report", self.agent)
        gate = rjson(os.path.join(self.ws, "iteration-1", "gate.json"))
        self.assertFalse(any(p.startswith("coverage-missing:codex") for p in gate["problems"]), gate)
        self.assertTrue(any(p.startswith("coverage-missing:opencode") for p in gate["problems"]), gate)
        self.assertEqual(gate["coverage"]["codex"], "waived by %s" % T.TEST_PRINCIPAL)
        scen = os.path.join(self.agent, "scenarios", "02-drill.json")
        write(scen, read(scen) + "\n")
        cli("agent", "report", self.agent)
        gate = rjson(os.path.join(self.ws, "iteration-1", "gate.json"))
        self.assertTrue(any(p.startswith("coverage-missing:codex") for p in gate["problems"]),
                        "a scenario edit invalidates the waiver")

    def test_the_agent_eval_set_hash_covers_the_scenarios(self):
        spec = {"skill_name": NAME, "skill_dir": os.path.realpath(self.agent), "kind": "agent"}
        h = se.gate_hash("eval_set", spec, self.ws)
        self.assertRegex(h, "^[0-9a-f]{64}$")
        p = os.path.join(self.agent, "AGENT.md")
        write(p, read(p) + "\n")
        self.assertEqual(se.gate_hash("eval_set", spec, self.ws), h)
        scen = os.path.join(self.agent, "scenarios", "01-overview.json")
        write(scen, read(scen) + "\n")
        self.assertNotEqual(se.gate_hash("eval_set", spec, self.ws), h)

    def test_an_agent_record_is_format_2_kind_agent_and_verifies_now(self):
        self.register_agent()
        rec = rjson(os.path.join(self.ws, "registration.json"))
        self.assertEqual((rec["registration_format"], rec["kind"], rec["skill_name"], rec["version"], rec["skill_path"]),
                         (2, "agent", NAME, "1.0.0", "../" + NAME))
        self.assertEqual(rec["skill_sha256"], se.skill_tree_hash(self.agent))
        self.assertEqual(rec["skill_md_sha256"], se.sha256_files(os.path.join(self.agent, "AGENT.md")))
        spec = {"skill_name": NAME, "skill_dir": os.path.realpath(self.agent), "kind": "agent"}
        self.assertEqual(rec["eval_set_sha256"], se.gate_hash("eval_set", spec, self.ws))
        self.assertEqual(sorted(rec["approvals"]), ["coverage_waiver:codex", "coverage_waiver:hermes",
                                                    "coverage_waiver:opencode", "registration"])
        self.assertEqual(se.registration_schema_errors(rec), [])
        self.assertEqual(se.registration_state(self.agent)[:2], ("registered", ""))
        p = os.path.join(self.agent, "AGENT.md")
        write(p, read(p) + "\nMore.\n")
        self.assertEqual(se.registration_state(self.agent, self.ws)[:2], ("stale", "skill_sha256 changed"))

    def test_an_unbumped_agent_version_names_the_agent_version_field(self):
        from engine import registry
        old = {"registration_format": 2, "version": "1.0.0", "skill_sha256": "a" * 64}
        msg = registry.bump_problem({"kind": "agent", "version": "1.0.0", "skill_sha256": "b" * 64}, old)
        self.assertIn("bump the version in AGENT.md version", msg)
        msg = registry.bump_problem({"kind": "skill", "version": "1.0.0", "skill_sha256": "b" * 64}, old)
        self.assertIn("bump the version in SKILL.md metadata.version", msg)

    def test_an_agent_without_a_version_is_not_recorded(self):
        shutil.rmtree(self.agent)
        self.agent = agent_copy(os.path.join(self.tmp, "agents"), version=None)
        fake_iteration(self.agent, self.ws, self.runs)
        for tool in ("codex", "opencode", "hermes"):
            self.assertEqual(cli("approve", self.agent, "--gate", "coverage_waiver:" + tool)[0], 0)
        cli("agent", "report", self.agent)
        self.assertEqual(cli("approve", self.agent, "--gate", "registration")[0], 0)
        rc, out, err = cli("register", self.agent)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("version", err)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "registration.json")))

    def test_an_agent_with_a_missing_waiver_is_not_recorded(self):
        fake_iteration(self.agent, self.ws, self.runs)
        for tool in ("codex", "opencode"):
            self.assertEqual(cli("approve", self.agent, "--gate", "coverage_waiver:" + tool)[0], 0)
        cli("agent", "report", self.agent)
        self.assertEqual(cli("approve", self.agent, "--gate", "registration")[0], 0)
        rc, out, err = cli("register", self.agent)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("coverage-missing:hermes", err)
        self.assertFalse(os.path.exists(os.path.join(self.ws, "registration.json")))

    def test_the_agent_plan_names_the_commands_that_read_agent_waivers(self):
        rc, out, err = cli("agent", "plan", self.agent, "--runs", "1")
        self.assertEqual(rc, 0, out + err)
        line = next(ln for ln in out.splitlines() if ln.startswith("registration coverage: "))
        self.assertEqual(line, PLAN_COVERAGE % ", ".join(se.COVERAGE_REQUIRED))
        self.assertNotIn("not read yet", out)


PLAN_COVERAGE = ("registration coverage: registration needs a graded run on every tool of coverage_required (%s) or a "
                 "signed coverage_waiver:<tool> (G1, plan.md:1136); `agent report`, `agent loop` and `register` read the "
                 "agent's signed coverage waivers, so a tool that did not run stays coverage-missing unless its waiver "
                 "verifies")
REGISTRY_SCHEMA = os.path.join(ROOT, "schemas", "registry.schema.json")
RECORD_HASHES = ("benchmark_sha256", "skill_sha256", "eval_set_sha256", "skill_md_sha256", "ruleset_sha256")


class TestIndexAndHub(Base):
    """R22-f1: `eva.py index` and `hub build` read an agent record in the workspace of the agent commands, verified now."""
    def setUp(self):
        Base.setUp(self)
        self.root = os.path.join(self.tmp, "agents")

    def index(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = eva.main(["index", self.root])
        self.assertEqual(rc, 0, out.getvalue())
        md = read(os.path.join(self.root, "INDEX.md"))
        row = next(ln for ln in md.splitlines() if ln.startswith("| agent | %s |" % NAME))
        return row, json.loads(read(os.path.join(self.root, "registry.json")))

    def page(self):
        out = tempfile.mkdtemp(dir=self.tmp)
        rc, stdout, err = cli("hub", "build", self.root, "--out", out)
        self.assertEqual(rc, 0, err)
        return read(os.path.join(out, "units", NAME + ".html"))

    def test_a_verifying_agent_record_is_listed_in_the_index_and_the_registry(self):
        from engine import schema
        self.register_agent()
        rec = rjson(os.path.join(self.ws, "registration.json"))
        row, doc = self.index()
        self.assertIn("| registered 1.0.0 |", row)
        want = {"kind": "agent", "name": NAME, "version": "1.0.0", "path": NAME, "workspace": NAME + "-workspace",
                "iteration": rec["iteration"], "registered_at": rec["registered_at"], "registered_by": T.TEST_PRINCIPAL,
                "approvals": [{"gate": "coverage_waiver:%s" % t, "principal": T.TEST_PRINCIPAL, "reason": T.TEST_REASON}
                              for t in ("codex", "hermes", "opencode")] +
                             [{"gate": "registration", "principal": T.TEST_PRINCIPAL}]}
        want.update((k, rec[k]) for k in RECORD_HASHES)
        self.assertEqual(doc["units"], [want])
        self.assertEqual(schema.compile_schema(schema.load(REGISTRY_SCHEMA)).validate(doc), [])

    def test_the_hub_page_shows_the_record_and_the_coverage_of_its_recorded_benchmark(self):
        self.register_agent()
        page = self.page()
        self.assertEqual(badge(page, "registration"), "registered 1.0.0 by %s" % T.TEST_PRINCIPAL)
        self.assertEqual([badge(page, "coverage-" + t) for t in eva.TOOLS],
                         ["Claude Code coverage: run", "Codex CLI coverage: " + T.NOT_SELECTED,
                          "OpenCode coverage: " + T.NOT_AVAILABLE, "Hermes Agent coverage: " + T.NOT_AVAILABLE])
        self.assertIn("recomputed from the runs of iteration-1.", page)
        for gate in ("coverage_waiver:codex", "coverage_waiver:opencode", "coverage_waiver:hermes", "registration"):
            self.assertIn("<li>%s by %s" % (gate, T.TEST_PRINCIPAL), page)
        self.assertIn(T.TEST_REASON, page)
        self.assertNotIn("no agent registration", page)
        bp = os.path.join(self.ws, "iteration-1", "benchmark.json")
        bench = rjson(bp)
        bench["tool_coverage"]["hermes"] = {"status": "run", "engine": "hermes", "graded_runs": 1, "pending_runs": 0}
        wjson(bp, bench)
        page = self.page()
        self.assertEqual(badge(page, "coverage-hermes"), "Hermes Agent coverage: " + T.NOT_AVAILABLE)
        self.assertEqual(badge(page, "registration"), "stale (benchmark_sha256 changed)")

    def test_an_agent_record_that_does_not_verify_now_stays_out_of_the_registry(self):
        from engine import approvals as A
        self.register_agent()
        rp, p = os.path.join(self.ws, "registration.json"), os.path.join(self.agent, "AGENT.md")
        good, agent_md = rjson(rp), read(p)
        k = T.shared_keys()
        other, _ = T.make_key(tempfile.mkdtemp(dir=k["dir"]), "intruder", T.TEST_PASSPHRASE)
        data = A.payload("register", "registration", NAME, se.registration_hash(good), good["signed"]["ruleset_sha256"],
                         good["signed"]["at"])
        foreign = dict(good, signed=dict(good["signed"], signature=A.sign_payload(
            T.keygen_runner(k["dir"]), other, data, passphrase=T.TEST_PASSPHRASE)))
        cases = (("no record", lambda: os.remove(rp), "unregistered"),
                 ("tampered field", lambda: wjson(rp, dict(good, registered_at="2026-01-01T00:00:00Z")),
                  "invalid (the record does not match its signature)"),
                 ("signer not the approver", lambda: wjson(rp, foreign), "invalid (the register signature does not verify)"),
                 ("AGENT.md changed", lambda: write(p, agent_md + "\nMore.\n"), "stale (skill_sha256 changed)"))
        for label, change, want in cases:
            with self.subTest(label):
                change()
                row, doc = self.index()
                self.assertIn("| %s |" % want, row)
                self.assertEqual(doc["units"], [])
                self.assertEqual(badge(self.page(), "registration"), want)
            wjson(rp, good); write(p, agent_md)
        row, doc = self.index()
        self.assertIn("| registered 1.0.0 |", row)
        self.assertEqual(len(doc["units"]), 1)

    def test_the_index_resolves_the_workspace_with_the_rule_of_the_agent_commands(self):
        import argparse
        self.register_agent()
        other = os.path.join(self.tmp, "other"); os.makedirs(other)
        alias = os.path.join(other, "alias"); os.symlink(self.agent, alias)
        rows, _ = eva.build_index(other)
        self.assertEqual([r["path"] for r in rows], ["alias"])
        self.assertEqual(rows[0]["workspace"], se.agent_workspace(argparse.Namespace(workspace=None), se.Agent(alias)))
        self.assertEqual(rows[0]["registration"], "registered")

    def test_an_agent_whose_name_is_not_a_safe_path_component_reads_unregistered(self):
        from engine import registry
        bad = os.path.join(self.root, "Bad Agent")
        os.makedirs(bad); write(os.path.join(bad, "AGENT.md"), "---\nname: Bad Agent\n---\n# Bad\n")
        rows, _ = eva.build_index(self.root)
        row = next(r for r in rows if r["path"] == "Bad Agent")
        self.assertEqual((row["registration"], row["registration_reason"], row["workspace"]),
                         ("unregistered", registry.NO_AGENT_WORKSPACE, None))


if __name__ == "__main__":
    unittest.main()
