---------------------------- MODULE SpendLedger ----------------------------
(***************************************************************************)
(* The spend ledger <workspace>/spend.jsonl across live commands.          *)
(*                                                                         *)
(* NOT RUN. No Java runtime and no TLC exist on the machine that wrote     *)
(* this, so neither SANY nor TLC ever read this file. ledger_search.py     *)
(* holds the same actions under the same names and that search is what    *)
(* ran. Money is in whole cents.                                           *)
(*                                                                         *)
(* Two invariants differ from the search. Readable marks corrupt on the    *)
(* first row glued to a torn tail. _scan accepts a non-JSON last line as   *)
(* torn, so the search reads that state as readable and fails one row      *)
(* later. Exclusive covers the whole flock span from reread on. Python's   *)
(* holding in ledger_search.py starts only after Ledger.open returns, so   *)
(* the .tla invariant is the stronger of the two. PreviewMatches uses      *)
(* Opened, the states after Ledger.open returns, as the search does.       *)
(***************************************************************************)
EXTENDS Integers, Sequences, FiniteSets

CONSTANTS Cmds,       \* the live commands on one workspace
          NoCmd,      \* no command holds the flock
          Calls,      \* paid calls one command tries
          Cap,        \* --budget-usd
          Bound,      \* the fixed per-call bound of the API-key stress case
          Estimate,   \* the estimate the budget gate compares with what is left
          SeedCalls,  \* crashed calls an earlier command left in the ledger
          SeedTorn,   \* the ledger starts with a torn last line
          ApiKey      \* TRUE for profile company, FALSE for personal-mac

VARIABLES rows,       \* the complete lines of spend.jsonl
          tornTail,
          corrupt,
          holder,     \* who holds the flock of Ledger.open
          pc,
          preview,    \* the call ids spend.preview returned
          recovered,
          spent,      \* budget.spent
          settledCalls,
          bound,
          counted,    \* recovered cents plus admitted bounds, history only
          started     \* call ids whose CLI started, history only

vars == <<rows, tornTail, corrupt, holder, pc, preview, recovered, spent, settledCalls, bound, counted, started>>

Id(c, k) == <<c, k>>

RECURSIVE SumUsd(_)
SumUsd(S) == IF S = {} THEN 0
             ELSE LET r == CHOOSE x \in S : TRUE IN r.usd + SumUsd(S \ {r})

RowSet == {rows[i] : i \in 1..Len(rows)}

Settled == {r.id : r \in {x \in RowSet : x.t \in {"result", "recovered"}}}

\* engine/spend.py:126 dangling
Dangling == {r \in RowSet : r.t = "intent" /\ r.id \notin Settled}

DanglingIds == {r.id : r \in Dangling}

\* One os.write of one row, engine/spend.py:243. Behind a partial line the new row glues onto it.
AddRow(r) == /\ rows' = Append(rows, r)
             /\ corrupt' = (corrupt \/ tornTail)

Init ==
    /\ rows = [i \in 1..SeedCalls |-> [t |-> "intent", id |-> Id("c0", i), usd |-> Bound]]
    /\ tornTail = SeedTorn
    /\ corrupt = FALSE
    /\ holder = NoCmd
    /\ pc = [c \in Cmds |-> "preview"]
    /\ preview = [c \in Cmds |-> {}]
    /\ recovered = [c \in Cmds |-> 0]
    /\ spent = [c \in Cmds |-> 0]
    /\ settledCalls = [c \in Cmds |-> 0]
    /\ bound = [c \in Cmds |-> 0]
    /\ counted = [c \in Cmds |-> 0]
    /\ started = {}

\* skill_eval.py:6735, engine/spend.py:131. No lock. One read of the file.
Preview(c) ==
    /\ pc[c] = "preview"
    /\ preview' = [preview EXCEPT ![c] = DanglingIds]
    /\ pc' = [pc EXCEPT ![c] = "gate"]
    /\ UNCHANGED <<rows, tornTail, corrupt, holder, recovered, spent, settledCalls, bound, counted, started>>

\* skill_eval.py:6736 subtracts. _live_estimate at 6129 compares, through check_live_gates at 6741.
Gate(c) ==
    /\ pc[c] = "gate"
    /\ LET left == Cap - SumUsd({r \in RowSet : r.t = "intent" /\ r.id \in preview[c]}) IN
       pc' = [pc EXCEPT ![c] = IF Estimate <= left THEN "trylock" ELSE "done"]
    /\ UNCHANGED <<rows, tornTail, corrupt, holder, preview, recovered, spent, settledCalls, bound, counted, started>>

\* engine/spend.py:170, flock with LOCK_NB. The kernel makes this one step.
TryLock(c) ==
    /\ pc[c] = "trylock"
    /\ IF holder = NoCmd
       THEN /\ holder' = c
            /\ pc' = [pc EXCEPT ![c] = "reread"]
       ELSE /\ holder' = holder
            /\ pc' = [pc EXCEPT ![c] = "done"]
    /\ UNCHANGED <<rows, tornTail, corrupt, preview, recovered, spent, settledCalls, bound, counted, started>>

AfterOpen(rs) == IF rs = {} THEN "admit" ELSE "recover"

\* engine/spend.py:175-180, under the flock.
Reread(c) ==
    /\ pc[c] = "reread"
    /\ IF DanglingIds = preview[c]
       THEN /\ pc' = [pc EXCEPT ![c] = IF tornTail THEN "cut" ELSE AfterOpen(DanglingIds)]
            /\ holder' = holder
       ELSE /\ pc' = [pc EXCEPT ![c] = "done"]
            /\ holder' = NoCmd
    /\ UNCHANGED <<rows, tornTail, corrupt, preview, recovered, spent, settledCalls, bound, counted, started>>

\* engine/spend.py:182, under the flock.
CutTorn(c) ==
    /\ pc[c] = "cut"
    /\ tornTail' = FALSE
    /\ pc' = [pc EXCEPT ![c] = AfterOpen(DanglingIds)]
    /\ UNCHANGED <<rows, corrupt, holder, preview, recovered, spent, settledCalls, bound, counted, started>>

\* engine/spend.py:188-192. One row per step. The flock keeps other writers out but a Preview may land between two rows
\* and a crash may land there too, so the loop is not one step.
AppendRecovered(c) ==
    /\ pc[c] = "recover"
    /\ \E r \in Dangling :
         /\ AddRow([t |-> "recovered", id |-> r.id, usd |-> r.usd])
         /\ recovered' = [recovered EXCEPT ![c] = @ + r.usd]
         /\ spent' = [spent EXCEPT ![c] = @ + r.usd]
         /\ counted' = [counted EXCEPT ![c] = @ + r.usd]
         /\ pc' = [pc EXCEPT ![c] = AfterOpen(DanglingIds \ {r.id})]
    /\ UNCHANGED <<tornTail, holder, preview, settledCalls, bound, started>>

\* engine/spend.py:207-226, skill_eval.py:2694 guard_call and 2710 spend_admit. Memory only, no row yet. ApiKey is the
\* stress case of ledger_search.py with a fixed Bound. The real guard_call order is searched there and not stated here.
Admit(c) ==
    /\ pc[c] = "admit"
    /\ LET b == IF ApiKey THEN Bound ELSE (Cap - spent[c]) \div 2
           no == IF ApiKey THEN spent[c] + b > Cap ELSE b < 1
       IN /\ bound' = [bound EXCEPT ![c] = b]
          /\ pc' = [pc EXCEPT ![c] = IF no THEN "refuse" ELSE "intent"]
          /\ counted' = [counted EXCEPT ![c] = IF no THEN @ ELSE @ + b]
    /\ UNCHANGED <<rows, tornTail, corrupt, holder, preview, recovered, spent, settledCalls, started>>

\* engine/spend.py:225.
AppendRefused(c) ==
    /\ pc[c] = "refuse"
    /\ AddRow([t |-> "refused", id |-> Id(c, settledCalls[c] + 1), usd |-> 0])
    /\ pc' = [pc EXCEPT ![c] = "release"]
    /\ UNCHANGED <<tornTail, holder, preview, recovered, spent, settledCalls, bound, counted, started>>

\* engine/spend.py:273.
AppendIntent(c) ==
    /\ pc[c] = "intent"
    /\ AddRow([t |-> "intent", id |-> Id(c, settledCalls[c] + 1), usd |-> bound[c]])
    /\ pc' = [pc EXCEPT ![c] = "start"]
    /\ UNCHANGED <<tornTail, holder, preview, recovered, spent, settledCalls, bound, counted, started>>

\* skill_eval.py:434 consume, 440 subprocess.run.
StartCli(c) ==
    /\ pc[c] = "start"
    /\ started' = started \cup {Id(c, settledCalls[c] + 1)}
    /\ pc' = [pc EXCEPT ![c] = "result"]
    /\ UNCHANGED <<rows, tornTail, corrupt, holder, preview, recovered, spent, settledCalls, bound, counted>>

\* engine/spend.py:303, skill_eval.py:2913-2917. The model charges the call its bound.
AppendResult(c) ==
    /\ pc[c] = "result"
    /\ AddRow([t |-> "result", id |-> Id(c, settledCalls[c] + 1), usd |-> bound[c]])
    /\ spent' = [spent EXCEPT ![c] = @ + bound[c]]
    /\ settledCalls' = [settledCalls EXCEPT ![c] = @ + 1]
    /\ pc' = [pc EXCEPT ![c] = IF settledCalls[c] + 1 = Calls THEN "release" ELSE "admit"]
    /\ UNCHANGED <<tornTail, holder, preview, recovered, bound, counted, started>>

\* engine/spend.py:198, skill_eval.py:6610.
Release(c) ==
    /\ pc[c] = "release"
    /\ holder' = NoCmd
    /\ pc' = [pc EXCEPT ![c] = "done"]
    /\ UNCHANGED <<rows, tornTail, corrupt, preview, recovered, spent, settledCalls, bound, counted, started>>

\* The process dies. The kernel drops its flock. No line of the code.
Crash(c) ==
    /\ pc[c] # "done"
    /\ pc' = [pc EXCEPT ![c] = "done"]
    /\ holder' = IF holder = c THEN NoCmd ELSE holder
    /\ UNCHANGED <<rows, tornTail, corrupt, preview, recovered, spent, settledCalls, bound, counted, started>>

\* The process dies inside one os.write, at engine/spend.py:243, and leaves a partial line.
CrashTorn(c) ==
    /\ pc[c] \in {"recover", "refuse", "intent", "result"}
    /\ tornTail' = TRUE
    /\ pc' = [pc EXCEPT ![c] = "done"]
    /\ holder' = IF holder = c THEN NoCmd ELSE holder
    /\ UNCHANGED <<rows, corrupt, preview, recovered, spent, settledCalls, bound, counted, started>>

Next ==
    \E c \in Cmds :
        \/ Preview(c)
        \/ Gate(c)
        \/ TryLock(c)
        \/ Reread(c)
        \/ CutTorn(c)
        \/ AppendRecovered(c)
        \/ Admit(c)
        \/ AppendRefused(c)
        \/ AppendIntent(c)
        \/ StartCli(c)
        \/ AppendResult(c)
        \/ Release(c)
        \/ Crash(c)
        \/ CrashTorn(c)

Spec == Init /\ [][Next]_vars

\* The flock, engine/spend.py:170 to 198.
Held == {"reread", "cut", "recover", "admit", "refuse", "intent", "start", "result", "release"}

\* Recovery has finished, so recovered can equal the previewed sum. Before that it is still counting up.
Opened == Held \ {"reread", "cut", "recover"}

\* L-exclusive
Exclusive == \A c, d \in Cmds : (pc[c] \in Held /\ pc[d] \in Held) => c = d

\* L-preview-matches
PreviewMatches ==
    \A c \in Cmds : pc[c] \in Opened
        => recovered[c] = SumUsd({r \in RowSet : r.t = "intent" /\ r.id \in preview[c]})

\* L-counted-once. A command's own call dangles from AppendIntent to AppendResult, so the second clause names preview.
CountedOnce ==
    /\ \A i, j \in 1..Len(rows) :
           (i # j /\ rows[i].t \in {"result", "recovered"} /\ rows[j].t \in {"result", "recovered"}) => rows[i].id # rows[j].id
    /\ \A c \in Cmds : pc[c] \in Opened => preview[c] \subseteq Settled

\* L-intent-first
IntentFirst == started \subseteq {r.id : r \in {x \in RowSet : x.t = "intent"}}

\* L-hard-cap
HardCap == ApiKey => \A c \in Cmds : counted[c] <= Cap

\* L-readable
Readable == ~corrupt

=============================================================================
