"""Pending approvals and batch recording (unit S1, method 1; unsigned since unit N1).

A pending item is one missing or stale approval of one unit's workspace (skill_eval.pending_items builds them):
unit, kind, unit_dir, workspace, gate, status, summary, sha256 (None when the gated file is missing: listed, never
recorded), ruleset_sha256, spend_usd, estimate_usd, needs_reason, show and review. `pending` prints them and records
nothing; `approve --batch` runs run_batch, which records each item like a single approve. ASCII only.
"""
import collections

from engine import approvals

Outcome = collections.namedtuple("Outcome", "ok line")


def money(x):
    return "unknown" if x is None else "$%.2f" % x


def item_lines(item):
    """The text lines of one item, every character that is not printable escaped (approvals.printable)."""
    lines = ["unit %s gate %s status %s" % (item["unit"], item["gate"], item["status"]),
             "  summary %s" % item["summary"],
             "  sha256 %s" % (item["sha256"] or "none (the gated file is missing; this item cannot be recorded yet)"),
             "  ruleset_sha256 %s" % item["ruleset_sha256"],
             "  spend so far %s, plan estimate %s of the next live run" % (money(item["spend_usd"]),
                                                                          money(item["estimate_usd"])),
             "  workspace %s" % item["workspace"]]
    return [approvals.printable(x) for x in lines]


def pending_text(items):
    out = []
    for item in items: out += item_lines(item)
    out.append("%d pending approval(s); nothing was recorded" % len(items))
    return "\n".join(out)


def run_batch(ap, items, record_item, errors):
    """approve --batch: the list, one reason line per coverage waiver item read with ap.read_line (a pipe is fine),
    then record_item(item, reason) per item. A refused reason raises before anything is recorded; a failing item does
    not stop the rest. Returns [Outcome]."""
    what = "approve --batch"
    recordable = [i for i in items if i["sha256"]]
    for item in items:
        for line in item_lines(item): ap.write(line)
    reasons = []
    for item in recordable:
        reason = None
        if item["needs_reason"]:
            ap.write(approvals.printable("reason for %s of %s:" % (item["gate"], item["unit"])))
            reason = (ap.read_line(approvals.REASON_PROMPT) or "").strip()
            problem = approvals.reason_problem(reason)
            if problem: raise approvals.Refused(what, ["%s: reason refused for %s of %s: %s; nothing was recorded"
                                                       % (what, item["gate"], item["unit"], problem)])
        reasons.append(reason)
    if not recordable: return [Outcome(False, "nothing to record: no listed item has its gated files")]
    out = [Outcome(False, "not recorded %s %s: the gated file is missing" % (i["unit"], i["gate"]))
           for i in items if not i["sha256"]]
    for item, reason in zip(recordable, reasons):
        try:
            rec = record_item(item, reason)
            out.append(Outcome(True, "recorded %s %s sha256 %s by %s" % (item["unit"], item["gate"], rec["sha256"][:12],
                                                                         approvals.decided_by(rec))))
        except errors as ex:
            out.append(Outcome(False, "not recorded %s %s: %s" % (item["unit"], item["gate"], ex)))
    return out
