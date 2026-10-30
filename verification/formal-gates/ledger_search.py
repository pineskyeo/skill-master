"""A thread cannot be copied, so a transition replays its whole schedule from the seed ledger."""
import builtins, collections, fcntl, json, os, threading, types

CAP, BOUND, ESTIMATE = 0.05, 0.02, 0.01
ENGINE = "capped-stub"
STUB = types.SimpleNamespace(name=ENGINE, model="m", has_call_cap=True)

Config = collections.namedtuple("Config", "name billing profile dangling torn commands calls stress", defaults=(False,))
Search = collections.namedtuple("Search", "states transitions actions violations configs")
Action = collections.namedtuple("Action", "path line text", defaults=(None, None, None))

CONFIGS = (Config("api-key stress, empty ledger, 3 calls", "api_key", "company", 0, False, 2, 3, True),
           Config("api-key stress, one crashed call", "api_key", "company", 1, False, 2, 2, True),
           Config("api-key stress, two crashed calls", "api_key", "company", 2, False, 2, 1, True),
           Config("api-key stress, three crashed calls, the gate refuses", "api_key", "company", 3, False, 2, 1, True),
           Config("api-key stress, one crashed call and a torn tail", "api_key", "company", 1, True, 2, 1, True),
           Config("subscription, empty ledger", "subscription", "personal-mac", 0, False, 2, 2),
           Config("subscription, one crashed call", "subscription", "personal-mac", 1, False, 2, 1),
           Config("api-key stress, three commands, one crashed call", "api_key", "company", 1, False, 3, 1, True),
           Config("api-key real order, empty ledger, 4 calls", "api_key", "company", 0, False, 2, 4),
           Config("api-key real order, one crashed call", "api_key", "company", 1, False, 2, 3))
REAL_ORDER = tuple(c for c in CONFIGS if c.billing == "api_key" and not c.stress)
OPEN_REFUSALS = ("another live command is using", "changed since the preview")

ACTIONS = {"Preview": Action("skill_eval.py", 6735, "dangling = spend.preview(ws)"),
           "Gate": Action("skill_eval.py", 6129, 'if est["total_usd"] > g.budget_left:'),
           "TryLock": Action("engine/spend.py", 170, "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)"),
           "Reread": Action("engine/spend.py", 175, 'with open(self.path, "rb") as f: data = f.read()'),
           "CutTorn": Action("engine/spend.py", 182, "os.ftruncate(fd, keep)"),
           "AppendRecovered": Action("engine/spend.py", 192, "self._append(row)"),
           "Admit": Action("engine/spend.py", 220, "if reason is None and round(spent + bound, 6) > round(self.cap, 6):"),
           "AppendRefused": Action("engine/spend.py", 225, "if reason: self._append(t.intent_row())"),
           "AppendIntent": Action("engine/spend.py", 273, "self.ledger._append(self.intent_row())"),
           "StartCli": Action("skill_eval.py", 434, '_LIVE["ledger"].consume()'),
           "AppendResult": Action("engine/spend.py", 303, "led._append(row)"),
           "Release": Action("engine/spend.py", 198, "os.close(self.fd)"),
           "Crash": Action(),
           "CrashTorn": Action()}


class Killed(BaseException):
    pass


class Probe:
    def __init__(self):
        self.world = None

    def __getattr__(self, name):
        return getattr(os, name)

    def fsync(self, fd):
        return None

    def write(self, fd, data):
        row = json.loads(data)
        action = ("AppendRecovered" if row["type"] == "recovered" else "AppendResult" if row["type"] == "result" else
                  "AppendIntent" if row["decision"] == "allowed" else "AppendRefused")
        if self.world.pause(action) == "torn":
            os.write(fd, data[:len(data) // 2])
            raise Killed()
        return os.write(fd, data)

    def ftruncate(self, fd, length):
        self.world.pause("CutTorn")
        return os.ftruncate(fd, length)

    def flock(self, fd, op):
        self.world.pause("TryLock")
        return fcntl.flock(fd, op)

    def read_open(self, path, *args, **kw):
        world = self.world
        world.pause("Reread" if world.opening.get(world.me()) else "Preview")
        return builtins.open(path, *args, **kw)


class World:
    def __init__(self, mods, cfg, ws, seed, probe):
        self.se, self.spend, self.cfg, self.ws, self.probe = mods.se, mods.spend, cfg, ws, probe
        self.path = os.path.join(ws, self.spend.FILE)
        self.procs = ["c%d" % (i + 1) for i in range(cfg.commands)]
        self.at, self.names, self.fates, self.opening = {}, {}, {}, {}
        self.ledgers, self.budgets, self.expected, self.calls = {}, {}, {}, {}
        self.counted, self.holding, self.bad, self.error, self.stray = {}, set(), {}, None, None
        self.back = threading.Lock()
        self.back.acquire()
        self.gates = {p: threading.Lock() for p in self.procs}
        for lock in self.gates.values(): lock.acquire()
        if os.path.lexists(self.path): os.unlink(self.path)
        if seed:
            with open(self.path, "wb") as f: f.write(seed)
        probe.world = self
        self.threads = [threading.Thread(target=self.run, args=(p,), daemon=True) for p in self.procs]
        for t in self.threads:
            t.start()
            self.back.acquire()

    def me(self):
        return self.names[threading.get_ident()]

    def pause(self, action):
        p = self.me()
        self.at[p] = action
        self.back.release()
        self.gates[p].acquire()
        fate = self.fates.pop(p)
        if fate in ("crash", "abort"): raise Killed()
        return fate

    def advance(self, p, fate):
        self.fates[p] = fate
        self.gates[p].release()
        self.back.acquire()
        if self.error is not None: raise self.error

    def run(self, p):
        self.names[threading.get_ident()] = p
        try:
            self.command(p)
        except Killed:
            self.holding.discard(p)
        except self.spend.SpendError as ex:
            if not any(x in str(ex) for x in OPEN_REFUSALS): self.stray = ex
        except BaseException as ex:
            self.error = ex
        finally:
            led = self.ledgers.get(p)
            if led is not None: led.close()
            self.at[p] = None
            self.back.release()

    def rows(self):
        try:
            with open(self.path, "rb") as f: return self.spend._scan(self.path, f.read())[0]
        except FileNotFoundError:
            return []

    def command(self, me):
        """skill_eval.py:6735-6765 and run_iteration.one, 2898-2917, in that order. Gate restates the subtraction at 6736
        and the compare at 6129, the settle restates 2913-2917, and launch then consume restates Engine.run at 1255. All
        three sit in code that must not run here. A stress configuration replaces guard_call at 2899 with a fixed bound to
        load Ledger.admit, an order the code never takes. Every other configuration calls the real guard_call."""
        se, spend, cfg = self.se, self.spend, self.cfg
        dangling = spend.preview(self.ws)
        previewed = sum(r["bound_usd"] or 0.0 for r in dangling)
        self.expected[me] = tuple(sorted(r["call_id"] for r in dangling))
        self.pause("Gate")
        if ESTIMATE > CAP - previewed: return
        led = spend.Ledger(self.ws, "run", cfg.profile, cfg.billing, CAP)
        led.run_id = me
        self.ledgers[me] = led
        budget = se.Budget(CAP)
        budget.ledger = led
        self.budgets[me] = budget
        self.opening[me] = True
        budget.spent = led.open([r["call_id"] for r in dangling])
        self.holding.add(me)
        rows = self.rows()
        recovered = sum(r["counted_usd"] for r in rows if r["type"] == "recovered" and r["run_id"] == me)
        self.counted[me] = recovered
        if round(recovered, 6) != round(previewed, 6):
            self.bad.setdefault("L-preview-matches", "%s recovered $%.2f, its budget gate subtracted $%.2f"
                                % (me, recovered, previewed))
        left = spend.dangling(rows)
        if left: self.bad.setdefault("L-counted-once", "%s finished Ledger.open and %s still dangle"
                                     % (me, [r["call_id"] for r in left]))
        for k in range(cfg.calls):
            self.calls[me] = k
            self.pause("Admit")
            cap, fallback, stop = (BOUND, BOUND, None) if cfg.stress else se.guard_call(STUB, "exec", budget, {}, None)
            ticket, stop = se.spend_admit(budget, "exec", ENGINE, "m", 1, cap, fallback, True, stop)
            if stop: break
            self.counted[me] += fallback
            if round(self.counted[me], 6) > CAP:
                self.bad.setdefault("L-hard-cap", "%s admitted a call with $%.2f counted against the cap $%.2f"
                                    % (me, self.counted[me], CAP))
            ticket.launch()
            self.pause("StartCli")
            allowed = [r for r in self.rows() if r["type"] == "intent" and r["decision"] == "allowed"
                       and r["call_id"] == ticket.call_id]
            if not (led.consume() and allowed):
                self.bad.setdefault("L-intent-first", "%s started call %s with no allowed intent row on disk"
                                    % (me, ticket.call_id))
            budget.charge(fallback)
            se.spend_settle(ticket, budget, "ok", fallback, 0, 0, (None, None, None), fallback, "cli_reported")
        self.calls[me] = cfg.calls
        self.pause("Release")
        led.close()
        self.holding.discard(me)

    def state_checks(self):
        if len(self.holding) > 1:
            self.bad.setdefault("L-exclusive", "%s are past Ledger.open at once" % " and ".join(sorted(self.holding)))
        try:
            rows = self.rows()
        except self.spend.SpendError as ex:
            self.bad.setdefault("L-readable", str(ex).replace(self.ws + os.sep, ""))
            return
        settled = collections.Counter([r["call_id"] for r in rows if r["type"] == "result"]
                                      + [r["recovers"] for r in rows if r["type"] == "recovered"])
        twice = sorted(c for c, n in settled.items() if n > 1)
        if twice: self.bad.setdefault("L-counted-once", "%s settled twice" % ", ".join(twice))
        # A corrupt ledger ends a command with a SpendError too, and the return above reports that as L-readable.
        if self.stray is not None: raise self.stray

    def key(self):
        try:
            with open(self.path, "rb") as f: data = f.read()
        except FileNotFoundError:
            data = None
        locs = []
        for p in self.procs:
            if self.at[p] is None:
                locs.append(None)
                continue
            led, budget = self.ledgers.get(p), self.budgets.get(p)
            locs.append((self.at[p], self.expected.get(p), self.calls.get(p), p in self.holding,
                         led and led.seq, led and tuple(sorted(led._open)), budget and round(budget.spent, 6)))
        return data, tuple(locs)

    def moves(self):
        out = []
        for p in self.procs:
            action = self.at[p]
            if action is None: continue
            out += [(p, "step", action), (p, "crash", "Crash")]
            if action.startswith("Append"): out.append((p, "torn", "CrashTorn"))
        return out

    def finish(self):
        for p in self.procs:
            if self.at[p] is not None:
                self.fates[p] = "abort"
                self.gates[p].release()
                self.back.acquire()
        for t in self.threads: t.join()


def real_offers(se):
    budget, offers = se.Budget(CAP), []
    while True:
        cap, fallback, stop = se.guard_call(STUB, "exec", budget, {}, None)
        if stop: return offers
        offers.append(cap)
        budget.charge(fallback)


def seed_bytes(mods, cfg, ws):
    spend = mods.spend
    path = os.path.join(ws, spend.FILE)
    if os.path.lexists(path): os.unlink(path)
    if not cfg.dangling and not cfg.torn: return b""
    led = spend.Ledger(ws, "run", cfg.profile, cfg.billing, CAP)
    led.run_id = "c0"
    led.open([])
    for _ in range(cfg.dangling):
        led.admit("exec", ENGINE, "m", 1, 0.0, BOUND, BOUND, True).launch()
    led.close()
    with open(path, "rb") as f: data = f.read()
    return data + (b'{"v": 1, "type": "inte' if cfg.torn else b"")


def replay(mods, cfg, ws, seed, probe, schedule):
    world = World(mods, cfg, ws, seed, probe)
    try:
        for p, fate, _ in schedule:
            world.advance(p, fate)
        world.state_checks()
        return world.key(), world.moves(), dict(world.bad)
    finally:
        world.finish()


def search_config(mods, cfg, ws, seed, probe, stats, stop_on):
    key, moves, bad = replay(mods, cfg, ws, seed, probe, ())
    seen, queue = {key}, collections.deque([((), moves)])
    while queue:
        schedule, moves = queue.popleft()
        for move in moves:
            path = schedule + (move,)
            key, next_moves, bad = replay(mods, cfg, ws, seed, probe, path)
            stats["transitions"] += 1
            stats["actions"][move[2]] += 1
            for pid, message in bad.items():
                stats["violations"].setdefault(pid, (cfg.name, [(p, a) for p, _, a in path], message))
            if stop_on in stats["violations"]: return
            if bad or key in seen: continue
            seen.add(key)
            queue.append((path, next_moves))
    stats["states"] += len(seen)


def search(mods, ws, stop_on=None):
    spend, probe = mods.spend, Probe()
    saved = {k: getattr(spend, k) for k in ("os", "fcntl", "_now")}
    stats = {"states": 0, "transitions": 0, "actions": collections.Counter(), "violations": {}}
    spend._now = lambda: "2026-10-30T00:00:00.000000Z"
    try:
        seeds = [seed_bytes(mods, cfg, ws) for cfg in CONFIGS]
        spend.os, spend.open = probe, probe.read_open
        spend.fcntl = types.SimpleNamespace(flock=probe.flock, LOCK_EX=fcntl.LOCK_EX, LOCK_SH=fcntl.LOCK_SH, LOCK_NB=fcntl.LOCK_NB)
        for cfg, seed in zip(CONFIGS, seeds):
            search_config(mods, cfg, ws, seed, probe, stats, stop_on)
            if stop_on in stats["violations"]: break
    finally:
        for k, v in saved.items(): setattr(spend, k, v)
        if "open" in vars(spend): del spend.open
    return Search(stats["states"], stats["transitions"], stats["actions"], stats["violations"], len(CONFIGS))
