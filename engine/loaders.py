"""Frontmatter, YAML-typing and flat TOML readers, moved unchanged from eva.py (plan.md:556, unit A1).

eva.py imports every name below, so eva.<name> callers keep working until A6. Standard library only; no import of eva
or skill_eval, because engine modules are imported by the hook (engine/writable.py)."""
import re

def _quoted_end(v):
    """Index of the quote closing the quoted scalar that starts at v[0], or -1 if unterminated.
    YAML: in "..." a backslash escapes the next character; in '...' a quote is escaped by doubling it ('')."""
    q, i = v[0], 1
    while i < len(v):
        if q == '"' and v[i] == "\\":
            i += 2; continue
        if v[i] == q:
            if q == "'" and v[i + 1:i + 2] == "'":
                i += 2; continue
            return i
        i += 1
    return -1

def _strip_comment(v):
    """Drop a YAML comment: a '#' at the start of the value or preceded by whitespace (outside quotes)
    starts a comment, so 'k: #x' is an empty (null) value, as in YAML."""
    v = v.strip()
    if v and v[0] in "\"'":
        end = _quoted_end(v)
        if end > 0 and re.match(r"^\s+#", v[end + 1:]):
            return v[:end + 1]
        return v
    if v.startswith("#"):
        return ""
    m = re.search(r"\s#", v)
    return v[:m.start()].rstrip() if m else v

# YAML 1.1 double-quoted escapes (PyYAML ScalarScanner.ESCAPE_REPLACEMENTS / ESCAPE_CODES)
_DQ_ESCAPES = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
               "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0",
               "L": " ", "P": " "}
_DQ_HEX = {"x": 2, "u": 4, "U": 8}

def _unquote(v):
    """(value, error) of a complete quoted scalar v (as returned by _strip_comment)."""
    if v[0] == "'":
        return v[1:-1].replace("''", "'"), None
    out, i, s = [], 0, v[1:-1]
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c); i += 1; continue
        e = s[i + 1:i + 2]
        if e in _DQ_ESCAPES:
            out.append(_DQ_ESCAPES[e]); i += 2
        elif e in _DQ_HEX and re.match(r"^[0-9A-Fa-f]{%d}$" % _DQ_HEX[e], s[i + 2:i + 2 + _DQ_HEX[e]]):
            code = int(s[i + 2:i + 2 + _DQ_HEX[e]], 16)
            if code > 0x10FFFF:  # chr() would raise (PyYAML itself crashes with OverflowError/ValueError here)
                return None, "escape '\\%s%s' is beyond U+10FFFF" % (e, s[i + 2:i + 2 + _DQ_HEX[e]])
            out.append(chr(code)); i += 2 + _DQ_HEX[e]
        else:
            return None, "invalid escape '\\%s' in double-quoted value" % e
    return "".join(out), None

def _scalar(v):
    """Python value of one scalar: None for empty/comment-only, unquoted string, inline list, or plain string."""
    v = _strip_comment(v)
    if not v:
        return None
    if v[0] in "\"'" and _quoted_end(v) == len(v) - 1:
        return _unquote(v)[0]
    if v.startswith("[") and v.endswith("]"):
        return [_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    return v

def _plain_scalar_error(v):
    """Why an unquoted value is not a valid YAML plain scalar for this subset (None if fine).
    A real YAML loader would reject or reinterpret these, so the frontmatter must quote them."""
    v = _strip_comment(v)
    if not v:
        return None
    if v[0] in "\"'":
        end = _quoted_end(v)
        if end < 0:
            return "unterminated %s-quoted value (multi-line quoted values are not supported here)" % v[0]
        if end != len(v) - 1:
            return "unexpected text after the closing %s quote" % v[0]
        return _unquote(v)[1]
    if v.startswith("[") and v.endswith("]"):
        return None
    if ": " in v or v.endswith(":"):
        return "':' inside an unquoted value is a YAML mapping indicator; quote the value"
    if v[0] in "@`":
        return "'%s' is a reserved YAML indicator; quote the value" % v[0]
    if v[0] in "&*!%{|>" or v == "-" or v.startswith("- ") or v.startswith("["):
        return "value starts with YAML syntax '%s' not supported here; quote the value" % v[0]
    return None

def _block_scalar(style, lines, eof=False):
    """Value of a '>'/'|' block scalar (style '>', '|', '>-', '|-') from its raw lines, as PyYAML scans it
    (ScalarScanner.scan_block_scalar): indentation from the first non-empty line, '#' lines are content,
    folding joins adjacent non-indented lines with a space, clip chomping keeps one final newline, '-' strips it.
    eof: the last line ends the frontmatter text. skill-creator's quick_validate loads the text between '---\n' and
    '\n---', so a block that is the last line has no line break and clip adds none. Returns (value, error)."""
    no_break = eof and bool(lines) and bool(lines[-1].strip())
    while lines and not lines[-1].strip():
        lines = lines[:-1]  # trailing empty lines only matter for '+' (keep), which is not supported
    first = next((l for l in lines if l.strip()), None)
    if first is None:
        return "", None
    indent = len(first) - len(first.lstrip(" "))
    chunks, breaks, line_break, prev_text = [], [], "", None
    for ln in lines:
        if len(ln) - len(ln.lstrip(" ")) >= indent and ln.strip():
            text = ln[indent:]
            if prev_text is not None:
                if style[0] == ">" and prev_text[:1] not in (" ", "\t") and text[:1] not in (" ", "\t"):
                    if not breaks: chunks.append(" ")
                else:
                    chunks.append(line_break)
            chunks.extend(breaks); breaks = []
            chunks.append(text); line_break = "\n"; prev_text = text
        elif not ln.strip():
            if prev_text is None: chunks.append("\n")  # leading empty lines are kept
            else: breaks.append("\n")
        else:
            return None, "line less indented than the block scalar's first line"
    if not style.endswith("-") and not no_break:
        chunks.append(line_break)
    return "".join(chunks), None

def parse_frontmatter_ex(text):
    """Returns (meta dict or None, body, error). Supports scalars, inline lists, '- item' lists,
    one level of 'key: value' nesting, '>'/'|' blocks. Opening and closing lines must be exactly '---';
    every top-level line must be 'key: value' (a mapping) with unique keys. Indentation must use spaces
    (YAML forbids tabs) and unquoted values must be valid YAML plain scalars (see _plain_scalar_error).
    ' #' starts a comment, as in YAML; inside a '>'/'|' block an indented '#' line is content."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return None, text, "first line must be exactly '---'"
    end = None
    for i in range(1, len(lines)):
        if lines[i] == "---":
            end = i; break
    if end is None:
        return None, text, "closing '---' line not found"
    meta, key, block, block_lines, block_start, closed = {}, None, None, [], 0, False
    def err(n, msg): return None, text, "frontmatter line %d: %s" % (n, msg)
    def finish_block(eof=False):
        v, e = _block_scalar(block, block_lines, eof)
        if e: return err(block_start, e)
        meta[key] = v
        return None
    for n, ln in enumerate(lines[1:end], 2):
        if block is not None and (not ln.strip() or ln[0] == " "):
            block_lines.append(ln); continue  # block content, '#' lines included
        if block is not None:
            bad = finish_block()
            if bad: return bad
            block, closed = None, True  # an indented line after this would continue nothing
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[0] in " \t":
            if key is None:
                return err(n, "indented line without a parent key")
            if closed:
                return err(n, "indented line after the block scalar of '%s' ended" % key)
            if "\t" in ln[:len(ln) - len(ln.lstrip())]:
                return err(n, "tab in indentation (YAML allows spaces only)")
            s = ln.strip()
            if meta[key] is not None and not isinstance(meta[key], (list, dict)):
                return err(n, "key '%s' already has a scalar value; nested lines not allowed" % key)
            elif s.startswith("- "):
                if isinstance(meta[key], dict):
                    return err(n, "key '%s' mixes '- item' list and 'key: value' mapping" % key)
                pe = _plain_scalar_error(s[2:])
                if pe: return err(n, pe)
                if meta[key] is None: meta[key] = []
                meta[key].append(_scalar(s[2:]))
            elif re.match(r"^[A-Za-z_][\w./-]*:\s+\S", s):
                if isinstance(meta[key], list):
                    return err(n, "key '%s' mixes '- item' list and 'key: value' mapping" % key)
                if meta[key] is None: meta[key] = {}
                k2, v2 = s.split(":", 1)
                if k2.strip() in meta[key]:
                    return err(n, "duplicate key '%s.%s'" % (key, k2.strip()))
                pe = _plain_scalar_error(v2)
                if pe: return err(n, pe)
                meta[key][k2.strip()] = _scalar(v2)
            else:
                return err(n, "expected '- item' or 'key: value' under '%s'" % key)
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$", ln)
        if not m:
            return err(n, "expected 'key: value' (frontmatter must be a mapping)")
        key, val = m.group(1), _strip_comment(m.group(2) or "")
        if key in meta:
            return err(n, "duplicate key '%s'" % key)
        closed = False
        if val in (">", "|", ">-", "|-"):
            meta[key] = ""; block, block_lines, block_start = val, [], n
        elif val == "":
            meta[key] = None
        else:
            pe = _plain_scalar_error(val)
            if pe: return err(n, pe)
            meta[key] = _scalar(val)
    if block is not None:
        bad = finish_block(eof=True)
        if bad: return bad
    return meta, "\n".join(lines[end + 1:]), None

# YAML 1.1 implicit types (what PyYAML's safe_load resolves): an unquoted value matching these is
# loaded as bool/null/int/float/date, not a string.
_YAML_TYPED_RE = re.compile(
    r"^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF"
    r"|~|null|Null|NULL"
    r"|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0[0-7_]+|[-+]?0x[0-9a-fA-F_]+|[-+]?0b[01_]+"
    r"|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?"
    r"|[-+]?[0-9][0-9_]*\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9_]+(?:[eE][-+][0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
    r"|\d{4}-\d\d-\d\d|\d{4}-\d\d?-\d\d?(?:[Tt]|[ \t]+)\d\d?:\d\d:\d\d.*)$")

def yaml_typed_keys(text):
    """Dotted keys ('name', 'metadata.version') whose unquoted value a YAML loader types as non-string."""
    lines = text.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return []
    out, parent, block = [], None, False
    for ln in lines[1:lines.index("---", 1)]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln[0] == " ":
            m = re.match(r"^\s+(?:-\s+(.*)|([A-Za-z_][\w./-]*):\s+(.*))$", ln)
            if not block and parent and m:
                v = m.group(1) if m.group(1) is not None else m.group(3)
                if _YAML_TYPED_RE.match(_strip_comment(v)):
                    out.append(parent + ("[]" if m.group(1) is not None else "." + m.group(2)))
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):(?:\s+(.*))?$", ln)
        if m:
            parent, v = m.group(1), _strip_comment(m.group(2) or "")
            block = v in (">", "|", ">-", "|-")
            if v and _YAML_TYPED_RE.match(v):
                out.append(parent)
    return out

class TomlError(ValueError):
    pass

_TOML_BARE_KEY_RE = re.compile(r"[A-Za-z0-9_-]+")
_TOML_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
_TOML_INT_RE = re.compile(r"[+-]?(?:0|[1-9](?:_?[0-9])*)$")
_TOML_FLOAT_RE = re.compile(r"[+-]?(?:0|[1-9](?:_?[0-9])*)(?:\.[0-9](?:_?[0-9])*)?(?:[eE][+-]?[0-9](?:_?[0-9])*)?$")

class _TomlReader:
    """Strict reader for the flat TOML subset a wrapper needs (see parse_flat_toml)."""
    def __init__(self, text):
        self.s, self.i, self.line = text, 0, 1
    def fail(self, msg):
        raise TomlError("line %d: %s" % (self.line, msg))
    def peek(self, n=1):
        return self.s[self.i:self.i + n]
    def skip_blank(self):
        while self.i < len(self.s) and self.s[self.i] in " \t": self.i += 1
    def newline(self):
        if self.peek(2) == "\r\n": self.i += 2
        elif self.peek() == "\n": self.i += 1
        else: self.fail("expected end of line")
        self.line += 1
    def at_eol(self):
        return self.i >= len(self.s) or self.peek() == "\n" or self.peek(2) == "\r\n"
    def comment(self):
        """Consume an optional comment up to (not including) the end of line."""
        if self.peek() == "#":
            while not self.at_eol():
                c = self.s[self.i]
                if c == "\r" or (ord(c) < 0x20 and c != "\t") or ord(c) == 0x7F: self.fail("control character in comment")
                self.i += 1
    def parse(self):
        out = {}
        while self.i < len(self.s):
            self.skip_blank()
            if self.i >= len(self.s): break
            c = self.peek()
            if c == "#": self.comment()
            if self.at_eol():
                if self.i < len(self.s): self.newline()
                continue
            if c == "[": self.fail("tables are not supported (flat key = value files only)")
            m = _TOML_BARE_KEY_RE.match(self.s, self.i)
            if not m:
                self.fail("quoted keys are not supported" if c in "\"'" else "expected a bare key, got %r" % c)
            key = m.group(0); self.i = m.end(); self.skip_blank()
            if self.peek() == ".": self.fail("dotted key '%s.' is not supported" % key)
            if self.peek() != "=": self.fail("expected '=' after key '%s'" % key)
            self.i += 1; self.skip_blank()
            if key in out: self.fail("duplicate key '%s'" % key)
            out[key] = self.value()
            self.skip_blank(); self.comment()
            if not self.at_eol(): self.fail("unexpected text after the value of '%s'" % key)
            if self.i < len(self.s): self.newline()
        return out
    def value(self):
        c, three = self.peek(), self.peek(3)
        if three == '"""': return self.ml_basic()
        if three == "'''": return self.ml_literal()
        if c == '"': return self.basic()
        if c == "'": return self.literal()
        if c == "[": return self.array()
        if c == "{": self.fail("inline tables are not supported")
        m = re.match(r"[^\s,#\]\[{}]+", self.s[self.i:])
        if not m: self.fail("missing value")
        tok = m.group(0); self.i += len(tok)
        if tok == "true": return True
        if tok == "false": return False
        if _TOML_INT_RE.match(tok): return int(tok.replace("_", ""))
        if _TOML_FLOAT_RE.match(tok): return float(tok.replace("_", ""))
        self.fail("unsupported value %r (strings must be quoted; dates, times, hex, inf and nan are not supported)" % tok[:20])
    def escape(self):
        e = self.peek()
        if e in _TOML_ESCAPES:
            self.i += 1; return _TOML_ESCAPES[e]
        if e in ("u", "U"):
            n = 4 if e == "u" else 8
            hexs = self.s[self.i + 1:self.i + 1 + n]
            if not re.match(r"^[0-9A-Fa-f]{%d}$" % n, hexs): self.fail("invalid \\%s escape" % e)
            code = int(hexs, 16)
            if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF: self.fail("\\%s%s is not a Unicode scalar value" % (e, hexs))
            self.i += 1 + n; return chr(code)
        self.fail("invalid escape '\\%s'" % e)
    def ctl(self, c, multiline):
        if c == "\r" and multiline and self.peek(2) == "\r\n": return False
        if (ord(c) < 0x20 and c != "\t" and not (multiline and c == "\n")) or ord(c) == 0x7F:
            self.fail("control character U+%04X in string" % ord(c))
        return True
    def basic(self):
        self.i += 1; out = []
        while True:
            if self.i >= len(self.s) or self.s[self.i] in "\n": self.fail("unterminated string")
            c = self.s[self.i]
            if c == '"': self.i += 1; return "".join(out)
            self.ctl(c, False)
            self.i += 1
            out.append(self.escape() if c == "\\" else c)
    def literal(self):
        self.i += 1; j = self.s.find("'", self.i)
        body = self.s[self.i:j] if j >= 0 else None
        if body is None or "\n" in body: self.fail("unterminated literal string")
        for c in body: self.ctl(c, False)
        self.i = j + 1; return body
    def _trim_first_newline(self):
        if self.peek(2) == "\r\n": self.i += 2; self.line += 1
        elif self.peek() == "\n": self.i += 1; self.line += 1
    def ml_basic(self):
        self.i += 3; self._trim_first_newline(); out = []
        while True:
            if self.i >= len(self.s): self.fail("unterminated multi-line string")
            if self.peek(3) == '"""':
                n = 0
                while self.peek() == '"': n += 1; self.i += 1
                if n > 5: self.fail("too many quotes at the end of a multi-line string")
                return "".join(out) + '"' * (n - 3)
            c = self.s[self.i]
            if c == "\r" and self.peek(2) == "\r\n": self.i += 1; continue
            if c == "\r": self.fail("lone carriage return in string")
            self.ctl(c, True); self.i += 1
            if c == "\n": self.line += 1
            if c != "\\": out.append(c); continue
            j = self.i
            while j < len(self.s) and self.s[j] in " \t": j += 1
            if self.s[j:j + 1] == "\n" or self.s[j:j + 2] == "\r\n":  # line-ending backslash trims the whitespace after it
                while j < len(self.s) and self.s[j] in " \t\r\n":
                    if self.s[j] == "\n": self.line += 1
                    j += 1
                self.i = j; continue
            out.append(self.escape())
    def ml_literal(self):
        self.i += 3; self._trim_first_newline(); out = []
        while True:
            if self.i >= len(self.s): self.fail("unterminated multi-line literal string")
            if self.peek(3) == "'''":
                n = 0
                while self.peek() == "'": n += 1; self.i += 1
                if n > 5: self.fail("too many quotes at the end of a multi-line string")
                return "".join(out) + "'" * (n - 3)
            c = self.s[self.i]
            if c == "\r" and self.peek(2) == "\r\n": self.i += 1; continue
            if c == "\r": self.fail("lone carriage return in string")
            self.ctl(c, True); self.i += 1
            if c == "\n": self.line += 1
            out.append(c)
    def array(self):
        self.i += 1; out = []
        while True:
            while True:
                self.skip_blank()
                if self.peek() == "#": self.comment()
                if self.peek(2) == "\r\n" or self.peek() == "\n": self.newline(); continue
                break
            if self.i >= len(self.s): self.fail("unterminated array")
            if self.peek() == "]": self.i += 1; return out
            out.append(self.value())
            while True:
                self.skip_blank()
                if self.peek() == "#": self.comment()
                if self.peek(2) == "\r\n" or self.peek() == "\n": self.newline(); continue
                break
            if self.i >= len(self.s): self.fail("unterminated array")
            if self.peek() == ",": self.i += 1
            elif self.peek() != "]": self.fail("expected ',' or ']' in array")

class _TableTomlReader(_TomlReader):
    """The flat reader plus [table] headers (bare dotted names) and dotted bare keys, read into nested dicts (unit A3,
    plan.md:674). Values and strings are read by the flat reader's methods."""
    def __init__(self, text):
        from engine.schema import MAX_DEPTH
        _TomlReader.__init__(self, text)
        # tables: path -> (how it was made, header section): "header", "implicit" (a parent of a header) or "dotted"
        self.root, self.tables, self.values, self.section, self.max_depth = {}, {}, set(), 0, MAX_DEPTH
    def dotted(self, what):
        parts = []
        while True:
            self.skip_blank()
            c = self.peek()
            m = _TOML_BARE_KEY_RE.match(self.s, self.i)
            if not m:
                if c in "\"'": self.fail("quoted keys are not supported")
                self.fail("expected a bare %s, got %r" % (what, c) if c else "expected a bare %s" % what)
            parts.append(m.group(0)); self.i = m.end(); self.skip_blank()
            if self.peek() != ".": return parts
            self.i += 1; self.skip_blank()
            if not _TOML_BARE_KEY_RE.match(self.s, self.i) and self.peek() not in "\"'":
                self.fail("dotted key '%s.' has an empty part" % ".".join(parts))
    def table(self, parts, upto, dotted_from=None):
        """The table at parts[:upto], made on the way. Tables from dotted_from on are dotted-key tables: TOML 1.0 lets a
        dotted key make or extend only tables that dotted keys of the same section made."""
        if len(parts) > self.max_depth: self.fail("tables nest deeper than %d levels" % self.max_depth)
        node = self.root
        for n in range(1, upto + 1):
            path = tuple(parts[:n])
            if path in self.values: self.fail("key '%s' is used both as a value and a table" % ".".join(path))
            made = self.tables.get(path)
            if dotted_from is not None and n > dotted_from:
                if made is None: self.tables[path] = ("dotted", self.section)
                elif made != ("dotted", self.section): self.fail("table '%s' is defined twice" % ".".join(path))
            elif made is None:
                self.tables[path] = ("implicit", self.section)
            node = node.setdefault(parts[n - 1], {})
        return node
    def parse(self):
        node, prefix = self.root, ()
        while self.i < len(self.s):
            self.skip_blank()
            if self.i >= len(self.s): break
            c = self.peek()
            if c == "#": self.comment()
            if self.at_eol():
                if self.i < len(self.s): self.newline()
                continue
            if c == "[":
                if self.peek(2) == "[[": self.fail("arrays of tables are not supported")
                self.i += 1
                parts = self.dotted("table header name")
                if self.peek() != "]": self.fail("expected ']' to close the table header")
                self.i += 1
                prefix = tuple(parts)
                if self.tables.get(prefix, ("implicit",))[0] != "implicit":
                    self.fail("table '%s' is defined twice" % ".".join(prefix))
                self.section += 1
                node = self.table(parts, len(parts))
                self.tables[prefix] = ("header", self.section)
            else:
                parts = self.dotted("key")
                if self.peek() != "=": self.fail("expected '=' after key '%s'" % ".".join(parts))
                self.i += 1; self.skip_blank()
                parent = self.table(list(prefix) + parts, len(prefix) + len(parts) - 1, len(prefix))
                full = prefix + tuple(parts)
                if parts[-1] in parent:
                    if isinstance(parent[parts[-1]], dict) and full not in self.values:
                        self.fail("key '%s' is used both as a value and a table" % ".".join(full))
                    self.fail("duplicate key '%s'" % parts[-1])
                parent[parts[-1]] = self.value()
                self.values.add(full)
            self.skip_blank(); self.comment()
            if not self.at_eol(): self.fail("unexpected text after %s" % ("the table header" if c == "[" else
                                                                            "the value of '%s'" % ".".join(parts)))
            if self.i < len(self.s): self.newline()
        return self.root

def parse_toml(text):
    """parse_flat_toml plus [table] headers (dotted names too) and dotted bare keys, returned as nested dicts. Still
    refused: arrays of tables [[...]], quoted keys, inline tables, a key or table defined twice, a key used both as a
    value and as a table, and everything parse_flat_toml refuses. A file without tables or dotted keys reads exactly as
    parse_flat_toml reads it."""
    if text.startswith("\ufeff"): raise TomlError("line 1: a byte order mark is not allowed")
    return _TableTomlReader(text).parse()

def parse_flat_toml(text):
    """Strict flat-TOML reader (stdlib only; Python 3.9 has no tomllib). Returns {key: value}; raises TomlError on anything
    it cannot parse. Supported: comments, bare keys, basic and literal strings (single and multi-line, all TOML escapes),
    integers, floats, booleans, arrays of those. Rejected: tables, arrays of tables, dotted or quoted keys, inline tables,
    dates and times, duplicate keys, control characters, invalid or surrogate escapes, trailing text, a BOM, a lone CR."""
    if text.startswith("\ufeff"): raise TomlError("line 1: a byte order mark is not allowed")
    return _TomlReader(text).parse()
