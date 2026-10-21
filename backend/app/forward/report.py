"""The report, and the go-live checklist it answers.

`docs/07-risk-limits.md` ends with a checklist for going live. Most of its
items are things a person confirms, but several are facts the system
already knows: whether the kill switches have been drilled, whether the
paper run is long enough, whether the books reconcile, whether every
position carried a stop.

Those are checked here rather than ticked by hand, because a checklist
ticked from memory is a checklist that passes.

The report never says "ready to go live". It reports what is true and what
is still open; the decision is the account owner's, and a system that
grades its own homework is not evidence.
"""
from __future__ import annotations

from typing import Any

from .. import config
from ..clock import now_ist
from ..risk import killswitch
from ..store import audit
from . import attribution, compare, journal

# The roadmap asks for 2-3 months. Indian markets run roughly 21 sessions a
# month, so two months is about 40.
MIN_SESSIONS = 40
MIN_UPTIME_PCT = 95.0
MAX_REJECT_RATE_PCT = 5.0


def collect(limit: int = 1000) -> dict[str, Any]:
    """Everything, in one object. Read-only."""
    sessions = journal.sessions()
    paper = journal.summary(sessions)
    return {
        "at": now_ist().isoformat(timespec="seconds"),
        "paper": paper,
        "versus_backtest": compare.compare(paper),
        "agents": attribution.report(limit),
        "checklist": checklist(paper),
        "switches": killswitch.status(),
    }


def checklist(paper: dict[str, Any] | None = None) -> dict[str, Any]:
    """The machine-checkable half of the go-live list.

    Each item is a fact the system can establish. The rest of the list --
    "chhoti capital se shuru karein" and the like -- is a decision, and is
    listed separately so it is not mistaken for something already handled.
    """
    paper = paper if paper is not None else journal.summary()
    items: list[dict[str, Any]] = []

    def check(key: str, ok: bool, detail: str, blocking: bool = True) -> None:
        items.append({"check": key, "passed": bool(ok), "detail": detail,
                      "blocking": blocking})

    sessions = paper.get("sessions", 0)
    check("paper_run_long_enough", sessions >= MIN_SESSIONS,
          f"{sessions} sessions record hue, {MIN_SESSIONS} chahiye "
          f"(~2 mahine)")

    uptime = paper.get("uptime_pct", 0)
    check("uptime", uptime >= MIN_UPTIME_PCT,
          f"uptime {uptime}% (kam se kam {MIN_UPTIME_PCT}%) -- strategy chalna "
          f"aur process chalna do alag daave hain")

    reject = paper.get("reject_rate_pct", 0)
    check("orders_filling", reject <= MAX_REJECT_RATE_PCT,
          f"{reject}% orders reject hue (limit {MAX_REJECT_RATE_PCT}%)")

    # Config inside its hard ceilings. The app refuses to start otherwise,
    # so this should always pass -- which is exactly why it is worth
    # asserting rather than assuming.
    problems = config.validate()
    check("config_within_ceilings", not problems,
          "; ".join(problems) if problems else "saari limits hard ceiling ke andar")

    check("capital_configured", config.TRADING_CAPITAL > 0,
          f"TRADING_CAPITAL = {config.TRADING_CAPITAL:,.0f}"
          if config.TRADING_CAPITAL > 0
          else "TRADING_CAPITAL set nahi hai -- risk engine size karne se "
               "mana kar dega")

    # Kill switches drilled. The drill writes nothing, so this looks for
    # switches having actually fired at some point in the audit log.
    fired = {row["outcome"] for row in audit.recent(limit=2000, kind="kill_switch")}
    drilled = bool(fired)
    check("kill_switches_exercised", drilled,
          f"audit me ye switch fire hue: {', '.join(sorted(fired))}" if drilled
          else "kisi kill switch ko abhi tak trigger karke dekha nahi gaya -- "
               "`python tools/killswitch_drill.py` chalayein",
          blocking=False)

    # No position ever left without a stop.
    unprotected = [row for row in audit.recent(limit=2000)
                   if row["kind"] in ("stop_failed", "stop_missing")]
    check("every_position_had_a_stop", not unprotected,
          f"{len(unprotected)} baar position bina stop ke rahi" if unprotected
          else "har filled position par stop laga")

    # Books agreed.
    mismatches = [row for row in audit.recent(limit=2000, kind="reconcile_mismatch")]
    check("books_reconciled", not mismatches,
          f"{len(mismatches)} baar broker se mismatch hua" if mismatches
          else "koi reconciliation mismatch nahi")

    # The audit log itself has to be trustworthy for any of the above to be.
    chain = audit.verify()
    check("audit_log_intact", chain.get("intact", False),
          f"{chain.get('rows', 0)} rows, chain {'theek' if chain.get('intact') else 'TOOTI HUI'}")

    # Paper close enough to the backtest.
    versus = compare.compare(paper)
    if versus.get("ready"):
        check("matches_backtest", versus["worst_severity"] in ("none", "warning"),
              versus["verdict"])
    else:
        check("matches_backtest", False, versus.get("note", "compare nahi ho saka"))

    # Agents earning their place.
    agent_report = attribution.report()
    if agent_report.get("ready"):
        costly = agent_report["summary"]["costs_money"]
        check("no_agent_costing_money", not costly,
              f"ye agents trades ko bigaad rahe hain: {', '.join(costly)}"
              if costly else "koi agent nuksan nahi kar raha")
    else:
        check("no_agent_costing_money", False, agent_report.get("note", ""))

    blocking_failed = [i for i in items if i["blocking"] and not i["passed"]]
    return {
        "items": items,
        "passed": sum(1 for i in items if i["passed"]),
        "total": len(items),
        "blocking_failures": [i["check"] for i in blocking_failed],
        "machine_checks_clear": not blocking_failed,
        # Deliberately not "ready to go live". The system does not get to
        # certify itself.
        "still_your_call": [
            "Paper results aapne khud padhe hain",
            "Chhoti capital se shuru karna hai, poori nahi",
            "Static IP aur VPS setup ho chuka hai",
            "Broker side par limits aur alerts set hain",
        ],
    }


def text(data: dict[str, Any] | None = None) -> str:
    """The report as plain text, for a terminal or a daily email."""
    data = data or collect()
    paper = data["paper"]
    lines: list[str] = []

    def head(title: str) -> None:
        lines.extend(["", "=" * 66, title, "=" * 66])

    head("PAPER FORWARD TEST")
    if not paper.get("sessions"):
        lines.append("  Abhi koi session record nahi hua.")
        return "\n".join(lines)

    lines += [
        f"  sessions        : {paper['sessions']}  "
        f"({paper['first_day']} se {paper['last_day']})",
        f"  return          : {paper['total_return_pct']:+.2f}%",
        f"  max drawdown    : {paper['max_drawdown_pct']:.2f}%",
        f"  trades          : {paper['trades']}  "
        f"({paper['wins']}W / {paper['losses']}L"
        + (f", {paper['win_rate_pct']:.0f}% win rate)" if paper['win_rate_pct'] is not None else ")"),
        f"  orders          : {paper['orders_sent']} sent, "
        f"{paper['orders_rejected']} rejected ({paper['reject_rate_pct']}%)",
        f"  uptime          : {paper['uptime_pct']}%",
        f"  halt days       : {paper['days_with_halts']}",
        f"  flat days       : {paper['flat_days']}",
    ]

    head("VERSUS BACKTEST")
    versus = data["versus_backtest"]
    if not versus.get("ready"):
        lines.append(f"  {versus.get('note', '')}")
    else:
        for f in versus["findings"]:
            lines.append(f"  [{f['severity'].upper()}] {f['check']}: {f['detail']}")
            lines.append(f"      likely: {f['likely_cause']}")
        if not versus["findings"]:
            lines.append("  Koi bada gap nahi mila.")
        lines += ["", f"  {versus['verdict']}"]

    head("AGENTS")
    agents = data["agents"]
    if not agents.get("ready"):
        lines.append(f"  {agents.get('note', '')}")
    else:
        lines.append(f"  base rate: {agents['base_rate_pct']:+.3f}% per trade "
                     f"over {agents['trades']} trades")
        lines.append("")
        for s in agents["agents"]:
            edge = s.get("edge_vs_base_pct")
            edge_text = f"{edge:+.3f}%" if edge is not None else "   —   "
            lines.append(f"  {s['agent']:<30} {s['verdict']:<22} edge {edge_text}")

    head("GO-LIVE CHECKLIST (jo system khud check kar sakta hai)")
    for item in data["checklist"]["items"]:
        mark = "ok  " if item["passed"] else "FAIL"
        block = "" if item["blocking"] else "  (non-blocking)"
        lines.append(f"  [{mark}] {item['check']}{block}")
        lines.append(f"         {item['detail']}")

    lines += ["", "  Aapke faisle (system check nahi kar sakta):"]
    for item in data["checklist"]["still_your_call"]:
        lines.append(f"    - {item}")

    return "\n".join(lines)
