"""B5: `python3 skill_eval.py hub build [ROOT] --out DIR` writes a static site (index.html, registry.json, style.css and
one page per unit with four install snippets) whose badges come from the records and checks, scanned English-only
before anything is written, with relative links only.

The golden units are read from temp copies. Signing uses only the throwaway test key of tests/test_skill_eval.py;
subprocess is patched for the whole module. ASCII only: non-ASCII text is built from code points."""
import atexit, contextlib, importlib, io, json, os, posixpath, re, shutil, socket, subprocess, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_skill_eval as T
import test_registry as TR
import test_multi_tool_parity as P
import test_eva as E
from test_skill_eval import se, eva, write, read, rjson

ROOT = T.ROOT
TEMPLATES = os.path.join(ROOT, "engine", "hub_templates")
KOREAN = "".join(map(chr, (0xD55C, 0xAD6D, 0xC5B4)))
GOLDEN_UNITS = ("agents/code-reviewer", "agents/research-assistant", "skills/csv-cleaner", "skills/korean-proofreader",
                "skills/pdf-summarizer")
TOOLS = ("claude", "codex", "opencode", "hermes")
LABELS = {"claude": "Claude Code", "codex": "Codex CLI", "opencode": "OpenCode", "hermes": "Hermes Agent"}
LINK_RE = re.compile(r"""(?:href|src)\s*=\s*["']([^"']*)["']""", re.I)
_patches = []


def setUpModule():
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        pt = mock.patch.object(subprocess, name, T._forbidden); pt.start(); _patches.append(pt)


def tearDownModule():
    for pt in _patches: pt.stop()


def build(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = se.main(["hub", "build"] + list(argv))
    return rc, out.getvalue(), err.getvalue()


def site(out):
    files = {}
    for dp, dn, fn in os.walk(out):
        for n in fn:
            fp = os.path.join(dp, n)
            with open(fp, "rb") as f: files[os.path.relpath(fp, out).replace(os.sep, "/")] = f.read()
    return files


def slug(rel):
    return rel.replace("/", "--")


def badge(page, key):
    m = re.search(r'data-badge="%s">([^<]*)<' % re.escape(key), page)
    return m.group(1) if m else None


def blocks(page):
    return re.findall(r'<div class="snippet" data-tool="([a-z]+)">(.*?)</div>', page, re.S)


_GOLDEN = {}
REAL_TMP = tempfile.gettempdir()


def golden_site():
    """One hub build of a temp golden copy for the read-only golden tests: (root, out, rc, stdout, stderr)."""
    if not _GOLDEN:
        d = tempfile.mkdtemp(prefix="hub-golden-", dir=REAL_TMP)
        atexit.register(shutil.rmtree, d, True)
        root, out = os.path.join(d, "golden"), os.path.join(d, "site")
        shutil.copytree(os.path.join(ROOT, "golden"), root)
        _GOLDEN.update(root=root, out=out, result=build(root, "--out", out))
    return _GOLDEN


class Golden(T.Base):
    def setUp(self):
        T.Base.setUp(self)
        self.g = golden_site()
        self.assertEqual(self.g["result"][0], 0, self.g["result"])
        self.files = site(self.g["out"])
        self.text = {k: v.decode("ascii") for k, v in self.files.items()}

    def page(self, rel):
        return self.text["units/%s.html" % slug(rel)]


class TestGoldenSite(Golden):
    def test_the_site_holds_the_index_the_registry_the_style_and_one_page_per_unit(self):
        self.assertEqual(sorted(self.files), sorted(["index.html", "registry.json", "style.css"] +
                                                    ["units/%s.html" % slug(u) for u in GOLDEN_UNITS]))
        self.assertEqual(json.loads(self.text["registry.json"]), {"registry_format": 1, "units": []})
        self.assertIn("5 units", self.g["result"][1])

    def test_the_index_lists_five_units_and_csv_cleaner_is_stale(self):
        rows = re.findall(r'<tr class="unit">(.*?)</tr>', self.text["index.html"], re.S)
        self.assertEqual(len(rows), 5)
        for rel in GOLDEN_UNITS:
            row = next(r for r in rows if 'href="units/%s.html"' % slug(rel) in r)
            want = "stale (legacy-absolute)" if rel == "skills/csv-cleaner" else "unregistered"
            self.assertEqual(badge(row, "registration"), want, rel)
            for tool in TOOLS:
                self.assertIsNotNone(badge(row, "parity-" + tool)); self.assertIsNotNone(badge(row, "coverage-" + tool))

    def test_every_badge_comes_from_the_checks_and_records(self):
        for rel in GOLDEN_UNITS:
            u = os.path.join(self.g["root"], *rel.split("/"))
            page, r = self.page(rel), eva.check(u)
            self.assertEqual(badge(page, "verdict"), "%s %.2f" % (r.verdict, r.score), rel)
            self.assertEqual(badge(page, "english"), "English %s" % eva.english_state(r), rel)
            for tool, st in eva.parity_status(u).items():
                self.assertEqual(badge(page, "parity-" + tool), "%s parity: %s" % (LABELS[tool], st["status"]), rel)
            self.assertIn('data-hash="tree_sha256"><code>%s</code>' % se.skill_tree_hash(u), page)
        self.assertEqual(badge(self.page("skills/csv-cleaner"), "scripts"), "scripts")
        self.assertEqual(badge(self.page("agents/code-reviewer"), "scripts"), "no scripts")
        self.assertEqual(badge(self.page("skills/pdf-summarizer"), "version"), "version 1.0.0")

    def test_the_hermes_coverage_badge_reads_not_run_with_the_reason(self):
        csv = self.page("skills/csv-cleaner")
        self.assertEqual(badge(csv, "coverage-hermes"), "Hermes Agent coverage: not-run: engine not available")
        self.assertEqual(badge(csv, "coverage-opencode"), "OpenCode coverage: not-run: engine not available")
        legacy = rjson(os.path.join(self.g["root"], "skills", "csv-cleaner-workspace", "registration.json"))
        self.assertIn("recomputed from the runs of iteration-%d." % legacy["iteration"], csv)
        self.assertIn('data-hash="skill_sha256"><code>%s</code>' % legacy["skill_sha256"], csv)
        for tool in TOOLS:
            self.assertEqual(badge(self.page("skills/pdf-summarizer"), "coverage-" + tool),
                             "%s coverage: not-run: no eval iteration" % LABELS[tool])
            self.assertEqual(badge(self.page("agents/code-reviewer"), "coverage-" + tool),
                             "%s coverage: not-run: no agent registration (gates.md G48)" % LABELS[tool])

    def test_every_page_has_four_project_scope_snippet_blocks(self):
        for rel in GOLDEN_UNITS:
            got = blocks(self.page(rel))
            self.assertEqual([t for t, _ in got], list(TOOLS), rel)
            for tool, body in got:
                self.assertIn("<h3>%s</h3>" % LABELS[tool], body)
                for word in ("~/", "--user", "install", "push", "curl", "wget", "http", "/Users/"):
                    self.assertNotIn(word, body.lower(), (rel, tool, word))
                if tool in ("opencode", "hermes"):
                    self.assertIn("UNVERIFIED: no %s engine here (gates.md G33)" % LABELS[tool], body)
                else:
                    self.assertNotIn("UNVERIFIED", body)
        csv = dict(blocks(self.page("skills/csv-cleaner")))
        self.assertIn("mkdir -p .claude/skills\ncp -R &lt;clone&gt;/skills/csv-cleaner .claude/skills/csv-cleaner", csv["claude"])
        self.assertIn("mkdir -p .agents/skills\ncp -R &lt;clone&gt;/skills/csv-cleaner .agents/skills/csv-cleaner", csv["codex"])
        self.assertIn("cp -R &lt;clone&gt;/skills/csv-cleaner .opencode/skills/csv-cleaner", csv["opencode"])
        self.assertIn("cp -R &lt;clone&gt;/skills/csv-cleaner .hermes/skills/csv-cleaner", csv["hermes"])
        for tool, body in blocks(self.page("agents/code-reviewer")):
            self.assertIn("no generated wrapper: run python3 eva.py generate agents/code-reviewer", body)

    def test_the_korean_description_is_withheld_and_the_site_is_ascii(self):
        self.assertIn("(description withheld: not English)", self.page("skills/korean-proofreader"))
        for rel, data in self.files.items():
            self.assertTrue(data.isascii(), rel)
            self.assertNotIn(b"&#x", data, rel)

    def test_every_link_is_relative_and_resolves_to_a_file_of_the_site(self):
        seen = 0
        for rel, text in self.text.items():
            for link in LINK_RE.findall(text):
                seen += 1
                self.assertFalse(link.startswith("/") or ":" in link or "\\" in link, (rel, link))
                target = posixpath.normpath(posixpath.join(posixpath.dirname(rel), link))
                self.assertIn(target, self.files, (rel, link))
        self.assertGreaterEqual(seen, 5 + 5 * 2)

    def test_no_local_path_no_url_and_no_build_time(self):
        for rel, data in self.files.items():
            for word in (b"/Users/", b"http", b"/private/", self.g["root"].encode(), os.path.expanduser("~").encode()):
                self.assertNotIn(word, data, (rel, word))
            self.assertIsNone(re.search(rb"20[0-9]{2}-[0-9]{2}-[0-9]{2}T", data), rel)


class TestBuildRefusals(T.Base):
    def setUp(self):
        T.Base.setUp(self)
        self.root = os.path.join(self.tmp, "g")
        os.makedirs(os.path.join(self.root, "skills"))
        shutil.copytree(os.path.join(ROOT, "golden", "skills", "pdf-summarizer"),
                        os.path.join(self.root, "skills", "pdf-summarizer"))
        self.out = os.path.join(self.tmp, "site")

    def plant(self, name, transform):
        t = os.path.join(self.tmp, "templates")
        if os.path.isdir(t): shutil.rmtree(t)
        shutil.copytree(TEMPLATES, t)
        write(os.path.join(t, name), transform(read(os.path.join(t, name))))
        return t

    def test_a_hangul_word_in_a_template_copy_fails_the_english_scan_and_writes_nothing(self):
        t = self.plant("index.html", lambda s: s.replace("</body>", "<p>%s</p></body>" % KOREAN))
        rc, out, err = build(self.root, "--out", self.out, "--templates", t)
        self.assertEqual(rc, 1, (out, err))
        self.assertIn("index.html", err); self.assertIn("Hangul", err)
        self.assertFalse(os.path.exists(self.out))

    def test_a_character_reference_in_a_template_copy_fails_and_writes_nothing(self):
        for ref in ("&#xD55C;", "&#54620;"):
            with self.subTest(ref):
                t = self.plant("unit.html", lambda s: s.replace("</body>", "<p>%s</p></body>" % ref))
                rc, out, err = build(self.root, "--out", self.out, "--templates", t)
                self.assertEqual(rc, 1, (out, err))
                self.assertIn("units/skills--pdf-summarizer.html", err); self.assertIn(ref[:5], err)
                self.assertFalse(os.path.exists(self.out))

    def test_a_css_escape_a_script_and_an_absolute_link_fail_the_scan(self):
        cases = (("style.css", lambda s: s + "\nh1::after { content: \"\\D55C\"; }\n", "style.css"),
                 ("index.html", lambda s: s.replace("</body>", "<script>x()</script></body>"), "script"),
                 ("index.html", lambda s: s.replace("</body>", '<a href="/etc/x">x</a></body>'), "/etc/x"),
                 ("index.html", lambda s: s.replace("</body>", '<a href="missing.html">x</a></body>'), "missing.html"),
                 ("unit.html", lambda s: s.replace("</body>", "<p>caf&eacute;</p></body>"), "&eacute;"),
                 ("index.html", lambda s: s.replace("</body>", '<img srcset="//cdn.example/a.png 1x"></body>'), "//"),
                 ("index.html", lambda s: s.replace("</body>", '<a href="index.html"onclick=x()>x</a></body>'), "script"),
                 ("index.html", lambda s: s.replace("</body>", '<svg/onload=x()></body>'), "script"),
                 ("style.css", lambda s: s + "\nbody { background: url(/etc/x); }\n", "url("),
                 ("index.html", lambda s: s.replace("</body>", "<p>/private/var/x</p></body>"), "/private/"))
        for name, transform, word in cases:
            with self.subTest(word):
                t = self.plant(name, transform)
                rc, out, err = build(self.root, "--out", self.out, "--templates", t)
                self.assertEqual(rc, 1, (out, err)); self.assertIn(word, err)
                self.assertFalse(os.path.exists(self.out))

    def test_the_scan_reads_escapes_in_registry_json(self):
        H = importlib.import_module("engine.hub")
        self.assertEqual(H.scan({"registry.json": '{"units": []}\n'}), [])
        found = H.scan({"registry.json": '{"name": "\\ud55c"}\n'})
        self.assertTrue(found and "registry.json" in found[0], found)
        found = H.scan({"registry.json": '{"path": "skills/caf\\u00e9"}\n'})
        self.assertTrue(found and "registry.json" in found[0], found)

    def test_out_must_be_absent_or_an_empty_folder(self):
        os.makedirs(self.out); write(os.path.join(self.out, "keep.txt"), "keep")
        rc, out, err = build(self.root, "--out", self.out)
        self.assertEqual(rc, 2, (out, err)); self.assertIn("--out", err)
        self.assertEqual(os.listdir(self.out), ["keep.txt"])
        afile = os.path.join(self.tmp, "afile")
        write(afile, "x")
        self.assertEqual(build(self.root, "--out", afile)[0], 2)
        self.assertEqual(build(self.root, "--out", os.path.join(afile, "site"))[0], 2)
        self.assertEqual(read(afile), "x")
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.assertEqual(build(self.root, "--out", empty)[0], 0)
        self.assertIn("units/skills--pdf-summarizer.html", site(empty))

    def test_two_units_with_one_slug_refuse_the_build(self):
        for name in ("a_b", "a-b"):
            shutil.copytree(os.path.join(self.root, "skills", "pdf-summarizer"), os.path.join(self.root, "skills", name))
        rc, out, err = build(self.root, "--out", self.out)
        self.assertEqual(rc, 2, (out, err)); self.assertIn("skills--a-b", err)
        self.assertFalse(os.path.exists(self.out))

    def test_root_defaults_to_the_current_folder(self):
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(self.root)
        rc, out, err = build("--out", self.out)
        self.assertEqual(rc, 0, err)
        self.assertIn("units/skills--pdf-summarizer.html", site(self.out))

    def test_a_non_english_name_is_withheld(self):
        d = os.path.join(self.root, "skills", "ko-name")
        shutil.copytree(os.path.join(self.root, "skills", "pdf-summarizer"), d)
        fp = os.path.join(d, "SKILL.md")
        write(fp, read(fp).replace("name: pdf-summarizer", "name: " + KOREAN, 1))
        rc, out, err = build(self.root, "--out", self.out)
        self.assertEqual(rc, 0, err)
        page = site(self.out)["units/skills--ko-name.html"].decode("ascii")
        self.assertIn("(name withheld: not English)", page)

    def test_the_build_starts_no_process_and_opens_no_socket(self):
        boom = AssertionError("no process or socket in hub build")
        with mock.patch.object(socket, "socket", side_effect=boom), \
                mock.patch.object(socket, "create_connection", side_effect=boom), \
                mock.patch.object(os, "system", side_effect=boom), mock.patch.object(os, "posix_spawn", side_effect=boom), \
                mock.patch.object(os, "fork", side_effect=boom):
            rc, out, err = build(self.root, "--out", self.out)
        self.assertEqual(rc, 0, err)


class TestRegisteredSite(TR.Reg):
    def build(self, out):
        return build(self.repo, "--out", out)

    def test_a_registered_unit_shows_its_record_and_the_stand_in_for_lanes_7_and_8(self):
        rec = self.registered()
        calls = []
        T.install_test_approver(self, run=T.keygen_runner(T.shared_keys()["dir"], calls))
        out = os.path.join(self.tmp, "site")
        with mock.patch.object(socket, "socket", side_effect=AssertionError("socket")):
            rc, stdout, err = self.build(out)
        self.assertEqual(rc, 0, err)
        self.assertTrue(calls and all(c[:3] == ["/usr/bin/ssh-keygen", "-Y", "verify"] for c in calls), calls)
        files = site(out)
        page = files["units/skills--branch-setup.html"].decode("ascii")
        self.assertEqual(badge(page, "registration"), "registered 1.0.0 by %s" % T.TEST_PRINCIPAL)
        self.assertEqual(badge(page, "version"), "version 1.0.0")
        for field in ("benchmark_sha256", "skill_sha256", "eval_set_sha256", "skill_md_sha256", "ruleset_sha256"):
            self.assertIn('data-hash="%s"><code>%s</code>' % (field, rec[field]), page)
        self.assertEqual([badge(page, "coverage-" + t) for t in TOOLS],
                         ["Claude Code coverage: run", "Codex CLI coverage: run",
                          "OpenCode coverage: not-run: engine not available",
                          "Hermes Agent coverage: not-run: engine not available"])
        self.assertIn("coverage_waiver:hermes", page); self.assertIn(TR.C.REASON, page)
        index = files["index.html"].decode("ascii")
        self.assertIn("registered 1.0.0", index)
        doc = json.loads(files["registry.json"])
        self.assertEqual([(u["name"], u["version"]) for u in doc["units"]], [("branch-setup", "1.0.0")])
        rc, _, text, _ = self.index()
        self.assertEqual(text.encode("ascii"), files["registry.json"], "byte-equal to eva.py index")
        write(os.path.join(self.d, "notes.md"), "one more file\n")
        out2 = os.path.join(self.tmp, "site2")
        rc, stdout, err = self.build(out2)
        self.assertEqual(rc, 0, err)
        files = site(out2)
        page = files["units/skills--branch-setup.html"].decode("ascii")
        self.assertEqual(badge(page, "registration"), "stale (skill_sha256 changed)")
        self.assertEqual(json.loads(files["registry.json"])["units"], [])

    def test_coverage_is_recomputed_from_the_runs_not_the_stored_field(self):
        self.registered()
        bp = os.path.join(self.ws, "iteration-1", "benchmark.json")
        bench = rjson(bp)
        bench["tool_coverage"]["hermes"] = {"status": "run", "engine": "hermes", "graded_runs": 1, "pending_runs": 0}
        T.wjson(bp, bench)
        rc, stdout, err = self.build(os.path.join(self.tmp, "site"))
        self.assertEqual(rc, 0, err)
        page = site(os.path.join(self.tmp, "site"))["units/skills--branch-setup.html"].decode("ascii")
        self.assertEqual(badge(page, "coverage-hermes"), "Hermes Agent coverage: not-run: engine not available")
        self.assertEqual(badge(page, "registration"), "stale (benchmark_sha256 changed)")

    def test_an_agent_with_wrappers_gets_copy_snippets_for_claude_and_codex(self):
        a = os.path.join(self.repo, "agents", "code-scout")
        os.makedirs(os.path.join(a, "scenarios"))
        write(os.path.join(a, "AGENT.md"), P.AGENT_MD)
        for n, s in (("01", P.S1), ("02", P.S2)): write(os.path.join(a, "scenarios", n + ".json"), json.dumps(s))
        P.write_wrappers(a)
        out = os.path.join(self.tmp, "site")
        rc, stdout, err = self.build(out)
        self.assertEqual(rc, 0, err)
        got = dict(blocks(site(out)["units/agents--code-scout.html"].decode("ascii")))
        self.assertIn("mkdir -p .claude/agents\ncp &lt;clone&gt;/agents/code-scout/wrappers/claude/code-scout.md "
                      ".claude/agents/code-scout.md", got["claude"])
        self.assertIn("mkdir -p .codex/agents\ncp &lt;clone&gt;/agents/code-scout/wrappers/codex/code-scout.toml "
                      ".codex/agents/code-scout.toml", got["codex"])
        self.assertIn("&lt;clone&gt;/agents/code-scout/wrappers/hermes/SOUL.md", got["hermes"])
        self.assertIn("UNVERIFIED", got["hermes"]); self.assertNotIn(".hermes", got["hermes"])


if __name__ == "__main__":
    unittest.main()
