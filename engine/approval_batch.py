"""Pending approvals and batch signing (unit S1, method 1).

A pending item is one missing or stale approval of one unit's workspace (skill_eval.pending_items builds them):
unit, kind, unit_dir, workspace, gate, status, summary, sha256 (None when the gated file is missing: listed, never
signed), ruleset_sha256, spend_usd, estimate_usd, needs_reason, show and review. `pending` prints them and signs
nothing; `approve --batch` runs run_batch on an Approver sitting, so presence and approver-check ran once before the
list was shown. ASCII only.
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
             "  sha256 %s" % (item["sha256"] or "none (the gated file is missing; this item cannot be signed yet)"),
             "  ruleset_sha256 %s" % item["ruleset_sha256"],
             "  spend so far %s, plan estimate %s of the next live run" % (money(item["spend_usd"]),
                                                                          money(item["estimate_usd"])),
             "  workspace %s" % item["workspace"]]
    return [approvals.printable(x) for x in lines]


def pending_text(items):
    out = []
    for item in items: out += item_lines(item)
    out.append("%d pending approval(s); nothing was signed" % len(items))
    return "\n".join(out)


def draft(item, reason):
    return approvals.Draft("approve", item["gate"], item["unit"], item["sha256"], item["ruleset_sha256"],
                           reason if item["needs_reason"] else None)


def run_batch(sitting, items, sign_item, errors):
    """approve --batch on one sitting: the list, one typed reason per waiver item, one challenge, the passphrase once
    (Sitting.unlock), then sign_item(item, sitting) per item. A failing item does not stop the rest. Returns
    [Outcome]; a challenge mismatch or a refused reason raises before anything is signed."""
    ap = sitting.approver
    signable = [i for i in items if i["sha256"]]
    for item in items:
        for line in item_lines(item): ap.write(line)
    drafts = []
    for item in signable:
        reason = None
        if item["needs_reason"]:
            ap.write(approvals.printable("reason for %s of %s:" % (item["gate"], item["unit"])))
            reason = (ap.read_line(approvals.REASON_PROMPT) or "").strip()
            problem = approvals.reason_problem(reason)
            if problem: raise sitting.refuse("reason refused for %s of %s: %s; nothing was signed"
                                             % (item["gate"], item["unit"], problem))
        drafts.append(draft(item, reason))
    if not drafts: return [Outcome(False, "nothing to sign: no listed item has its gated files")]
    code = sitting.challenge()
    sitting.confirm(drafts, ap.read_line("type %s to sign %d item(s) (anything else cancels): " % (code, len(drafts))))
    sitting.unlock()
    out = [Outcome(False, "not signed %s %s: the gated file is missing" % (i["unit"], i["gate"]))
           for i in items if not i["sha256"]]
    for item in signable:
        try:
            rec = sign_item(item, sitting)
            out.append(Outcome(True, "signed %s %s sha256 %s by %s" % (item["unit"], item["gate"], rec["sha256"][:12],
                                                                       rec["principal"])))
        except errors as ex:
            out.append(Outcome(False, "not signed %s %s: %s" % (item["unit"], item["gate"], ex)))
    return out
