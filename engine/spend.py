"""The spend ledger of skill_eval.py live commands: <workspace>/spend.jsonl (plan.md:903-904, references/auth-and-cost.md).

Every paid call gets an intent row right before it starts and a result row where its cost is charged. An allowed intent
with neither a result nor a recovered row naming it is dangling (the harness died mid-call): the next live command on
the workspace writes one recovered row for it and starts with its worst-case bound counted as spent. Under API-key
billing a call is admitted only when the spend so far plus its worst-case bound fits the hard cap; under subscription
billing the caller's own check decides and the row records that decision.

One JSON object per line, USD rounded to 6 decimals, each row appended with one write and fsynced. The live command
holds an exclusive flock on the file while it runs. Standard library only; imports neither skill_eval nor eva."""
import datetime, errno, fcntl, json, math, os, uuid
from engine import writable

FILE = "spend.jsonl"
VERSION = 1
MIN_BOUND = 0.01
KINDS = ("exec", "trigger", "grader", "pairwise", "calibrate", "builder")
GRADER_KINDS = ("grader", "pairwise", "calibrate")
OUTCOMES = ("ok", "error", "launch_failed", "unparsed", "not_launched")
COMMON = ("v", "type", "call_id", "run_id", "pid", "at", "command", "kind", "engine", "model", "profile", "billing",
          "iteration", "cap_usd", "remaining_usd")
FIELDS = {"intent": COMMON + ("decision", "reason", "spent_before_usd", "call_cap_usd", "bound_usd"),
          "result": COMMON + ("outcome", "reported_cost_usd", "input_tokens", "output_tokens", "computed_cost_usd", "price",
                              "price_source", "price_date", "charged_usd", "charged_source", "spent_after_usd",
                              "overshoot_usd"),
          "recovered": COMMON + ("recovers", "counted_usd")}
DECISIONS = ("allowed", "refused")
IDS = {"intent": ("call_id",), "result": ("call_id",), "recovered": ("call_id", "recovers")}
# The amounts the ledger sums into spend: each must be null or a finite number >= 0, so a forged or corrupt row cannot
# lift the remainder above the cap. remaining_usd is left out: it is negative after an overshoot.
AMOUNTS = {"intent": ("bound_usd", "spent_before_usd", "call_cap_usd"),
           "result": ("reported_cost_usd", "charged_usd"),
           "recovered": ("counted_usd",)}


class SpendError(Exception):
    pass


def usd(x):
    return None if x is None else round(float(x), 6)


def money(x):
    """USD for messages and reports: at least two decimals, up to six, no trailing zeros beyond two."""
    s = ("%.6f" % x).rstrip("0")
    return s if len(s.split(".")[1]) >= 2 else "%.2f" % x


def no_cap_reason(kind, engine, profile):
    if kind in GRADER_KINDS:
        return "%s grader: no enforceable per-call cap in API-key mode (its budget is a soft limit); refusing it" % engine
    return "%s: no enforceable per-call cap in API-key mode (auth profile %s); refusing it" % (engine, profile)


# Refused under API-key billing whatever their per-call cap: Claude's --max-budget-usd is not an enforceable maximum
# (plan.md:902; operator lane 8b reported $0.161712 against a $0.01 per-call cap).
API_KEY_REFUSED = ("claude",)


def api_key_refusal(kind, engine, profile, has_call_cap):
    """The refusal of one call under API-key billing, or None when it may be admitted against the hard cap."""
    if engine in API_KEY_REFUSED:
        role = engine + (" grader" if kind in GRADER_KINDS else " builder" if kind == "builder" else "")
        return ("%s: refused in API-key mode (auth profile %s): --max-budget-usd is not an enforceable per-call cap; "
                "refusing it" % (role, profile))
    return None if has_call_cap else no_cap_reason(kind, engine, profile)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row_problem(row):
    if not isinstance(row, dict): return "not a JSON object"
    if row.get("type") not in FIELDS: return "unknown row type %s" % json.dumps(row.get("type"))[:40]
    if row.get("v") != VERSION: return "row version must be %d" % VERSION
    t = row["type"]
    missing = [k for k in FIELDS[t] if k not in row]
    if missing: return "%s row misses %s" % (t, ", ".join(missing))
    for k in IDS[t]:
        if not (isinstance(row[k], str) and row[k]): return "%s row: %s must be a non-empty string" % (t, k)
    if t == "intent" and row["decision"] not in DECISIONS: return 'intent row: decision must be "allowed" or "refused"'
    for k in AMOUNTS[t]:
        if not _amount_ok(row[k]): return "%s row: %s must be null or a finite number >= 0" % (t, k)
    return None


def _amount_ok(x):
    return x is None or (isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0)


def _scan(path, data):
    """(rows, torn, keep): keep is the byte length of the complete rows. The last line is torn when it has no trailing
    newline or is not JSON; it is ignored. Any other bad line fails closed."""
    lines = data.split(b"\n")
    complete, tail = lines[:-1], lines[-1]
    rows, torn, keep, pos = [], None, 0, 0
    for i, raw in enumerate(complete, 1):
        pos += len(raw) + 1
        try:
            row = json.loads(raw.decode("utf-8"))
        except ValueError as ex:
            if i == len(complete) and not tail:
                torn = "line %d: not JSON (%s)" % (i, str(ex)[:80])
                break
            raise SpendError("%s line %d: not JSON (%s)" % (path, i, str(ex)[:80]))
        bad = _row_problem(row)
        if bad: raise SpendError("%s line %d: %s" % (path, i, bad))
        rows.append(row); keep = pos
    if tail: torn = "line %d: no trailing newline (a write that did not finish)" % (len(complete) + 1)
    return rows, torn, keep


def read_rows(path):
    """(rows, torn): every complete row, and None or a note on a torn last line. A missing file has no rows."""
    try:
        with open(path, "rb") as f: data = f.read()
    except FileNotFoundError:
        return [], None
    except OSError as ex:
        raise SpendError("%s: cannot read (%s)" % (path, ex.strerror or ex))
    return _scan(path, data)[:2]


def dangling(rows):
    done = {r["call_id"] for r in rows if r["type"] == "result"} | {r["recovers"] for r in rows if r["type"] == "recovered"}
    return [r for r in rows if r["type"] == "intent" and r["decision"] == "allowed" and r["call_id"] not in done]


def preview(ws):
    """The dangling intents of the workspace ledger, read only: nothing is created, locked or written."""
    return dangling(read_rows(os.path.join(ws, FILE))[0])


def preview_line(rows):
    if not rows: return None
    return ("spend ledger: %d dangling intent(s) from crashed calls, $%s counted as spent at the next live start"
            % (len(rows), money(sum(r["bound_usd"] or 0.0 for r in rows))))


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class Ledger(object):
    """The ledger of one live command. open() takes the lock and recovers dangling intents; admit() returns a Ticket."""
    def __init__(self, ws, command, profile, billing, cap_usd):
        self.ws, self.path = ws, os.path.join(ws, FILE)
        self.command, self.profile, self.billing, self.cap = command, profile, billing, float(cap_usd)
        self.run_id, self.seq = uuid.uuid4().hex, 0
        self.fd, self.torn, self.halt, self.bracket = None, None, None, None
        self.recovered_usd = 0.0
        self._open = set()

    def api_key(self):
        return self.billing == "api_key"

    def open(self, expected):
        """Locks the ledger for this command, checks that the dangling intents are the previewed ones (call ids), drops a
        torn last line, writes one recovered row per dangling intent and returns the USD they count."""
        os.makedirs(self.ws, exist_ok=True)
        existed = os.path.lexists(self.path)
        fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            if not existed: _fsync_dir(self.ws)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as ex:
                if ex.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise SpendError("another live command is using %s" % self.path)
                raise SpendError("%s: cannot lock (%s)" % (self.path, ex.strerror or ex))
            with open(self.path, "rb") as f: data = f.read()
            rows, self.torn, keep = _scan(self.path, data)
            now = dangling(rows)
            if sorted(r["call_id"] for r in now) != sorted(expected):
                raise SpendError("%s changed since the preview (dangling intents now %s, previewed %s); nothing was run, "
                                 "start again" % (self.path, sorted(r["call_id"] for r in now), sorted(expected)))
            if self.torn:
                os.ftruncate(fd, keep); os.fsync(fd)
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd
        total = 0.0
        for r in now:
            total += r["bound_usd"] or 0.0
            row = self._common("recovered", r["kind"], r["engine"], r["model"], None, self.cap - total)
            row.update(recovers=r["call_id"], counted_usd=usd(r["bound_usd"] or 0.0))
            self._append(row)
        self.recovered_usd = usd(total)
        return self.recovered_usd

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def consume(self):
        """live_exec's check: True (and the intent is used up) when an admitted call of this process has started."""
        if not self._open: return False
        self._open.pop()
        return True

    def admit(self, kind, engine, model, iteration, spent, call_cap, bound, has_call_cap, refusal=None):
        """A Ticket for one call. refusal: the caller's own check (None when it passed). Under API-key billing an engine
        of API_KEY_REFUSED raises SpendError before any row, and the ledger refuses on its own too: after a stop, for an
        engine without a per-call cap, for a bound below MIN_BOUND, and when spent + bound exceeds the cap. A refused
        ticket's intent row is written at once."""
        reason = refusal
        if self.api_key():
            if engine in API_KEY_REFUSED:
                raise SpendError(api_key_refusal(kind, engine, self.profile, has_call_cap))
            reason = self.halt or api_key_refusal(kind, engine, self.profile, has_call_cap) or refusal
            if reason is None and (bound is None or bound < MIN_BOUND):
                reason = ("worst-case bound %s is below $%s: no room for a capped call"
                          % ("unknown" if bound is None else "$" + money(bound), money(MIN_BOUND)))
            if reason is None and round(spent + bound, 6) > round(self.cap, 6):
                reason = ("spend so far $%s + worst-case bound $%s exceeds the hard cap $%s"
                          % (money(spent), money(bound), money(self.cap)))
        self.seq += 1
        t = Ticket(self, "%s:%d" % (self.run_id, self.seq), kind, engine, model, iteration, spent, call_cap, bound, reason)
        if reason: self._append(t.intent_row())
        return t

    def _common(self, type_, kind, engine, model, iteration, remaining, call_id=None):
        if call_id is None:
            self.seq += 1
            call_id = "%s:%d" % (self.run_id, self.seq)
        return {"v": VERSION, "type": type_, "call_id": call_id, "run_id": self.run_id, "pid": os.getpid(), "at": _now(),
                "command": self.command, "kind": kind, "engine": engine, "model": model, "profile": self.profile,
                "billing": self.billing, "iteration": iteration, "cap_usd": usd(self.cap), "remaining_usd": usd(remaining)}

    def _append(self, row):
        if self.fd is None: raise SpendError("%s is not open for this command" % self.path)
        data = (json.dumps(row) + "\n").encode("ascii")
        read = writable.entry(self.path) if self.bracket is not None else None
        created = not os.path.lexists(self.path)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            n = os.write(fd, data)
            if n != len(data): raise SpendError("%s: short write (%d of %d bytes)" % (self.path, n, len(data)))
            os.fsync(fd)
        except OSError as ex:
            raise SpendError("%s: cannot append (%s)" % (self.path, ex.strerror or ex))
        finally:
            os.close(fd)
        if created: _fsync_dir(self.ws)
        if self.bracket is not None: self.bracket.append((self.path, read, writable.entry(self.path)))


class Ticket(object):
    """One call: refused (reason set, its intent row already written) or allowed. launch() writes the allowed intent
    right before the CLI starts; result() or not_launched() writes its one result row."""
    def __init__(self, ledger, call_id, kind, engine, model, iteration, spent, call_cap, bound, reason):
        self.ledger, self.call_id, self.kind, self.engine, self.model = ledger, call_id, kind, engine, model
        self.iteration, self.spent, self.call_cap, self.bound, self.reason = iteration, spent, call_cap, bound, reason
        self.decision = "refused" if reason else "allowed"
        self.launched = self.settled = False

    def intent_row(self):
        led = self.ledger
        row = led._common("intent", self.kind, self.engine, self.model, self.iteration, led.cap - self.spent, self.call_id)
        row.update(decision=self.decision, reason=self.reason, spent_before_usd=usd(self.spent),
                   call_cap_usd=usd(self.call_cap), bound_usd=usd(self.bound))
        return row

    def launch(self):
        if self.decision != "allowed" or self.launched:
            raise SpendError("call %s was not admitted or has already started" % self.call_id)
        self.ledger._append(self.intent_row())
        self.launched = True
        self.ledger._open.add(self.call_id)

    def result(self, outcome, reported, tin, tout, computed, price, price_source, price_date, charged, charged_source,
               spent_after):
        """Writes the result row and returns the USD it charged on top of `charged`: under API-key billing a launched
        call with no valid reported cost is charged at least its per-call cap (its real cost is unknown) and stops the
        command. The caller adds that extra to its budget."""
        if not self.launched or self.settled: raise SpendError("call %s has no open intent" % self.call_id)
        if outcome not in OUTCOMES: raise SpendError("unknown outcome %r" % (outcome,))
        led = self.ledger
        led._open.discard(self.call_id)
        rep = float(reported) if isinstance(reported, (int, float)) and not isinstance(reported, bool) \
            and math.isfinite(reported) and reported >= 0 else None
        extra = 0.0
        if led.api_key() and rep is None and outcome != "not_launched":
            floor = self.call_cap if self.call_cap is not None else (self.bound or 0.0)
            if (charged or 0.0) < floor:
                extra = floor - (charged or 0.0)
                charged, charged_source, spent_after = floor, "cap_charged_unreported", spent_after + extra
            if led.halt is None:
                led.halt = ("stopped: call %s reported no valid total_cost_usd in API-key mode (its real cost is unknown); "
                            "charged $%s, at least its per-call cap $%s" % (self.call_id, money(charged), money(floor)))
        over = None if self.call_cap is None or rep is None else usd(max(0.0, rep - self.call_cap))
        row = led._common("result", self.kind, self.engine, self.model, self.iteration, led.cap - spent_after, self.call_id)
        row.update(outcome=outcome, reported_cost_usd=usd(rep), input_tokens=int(tin or 0), output_tokens=int(tout or 0),
                   computed_cost_usd=usd(computed), price=None if price is None else [float(price[0]), float(price[1])],
                   price_source=price_source, price_date=price_date, charged_usd=usd(charged),
                   charged_source=charged_source, spent_after_usd=usd(spent_after), overshoot_usd=over)
        led._append(row)
        self.settled = True
        if led.api_key() and led.halt is None:
            if over:
                led.halt = ("stopped: call %s overshot its per-call cap $%s by $%s (reported $%s)"
                            % (self.call_id, money(self.call_cap), money(over), money(rep)))
            elif round(spent_after, 6) > round(led.cap, 6):
                led.halt = "stopped: spend $%s is above the hard cap $%s" % (money(spent_after), money(led.cap))
        return extra

    def not_launched(self, spent):
        """The call raised between its intent and the CLI start: one result row, charged 0. No-op otherwise."""
        if self.launched and not self.settled:
            self.result("not_launched", None, 0, 0, None, None, None, None, 0.0, "unknown", spent)
