"""
mandays_report.py
=================

DEPRECATED: This functionality is now integrated into jira_time_tracker.py.
Use: python -m src.jira_time_tracker --mandays-report

This standalone script is kept for backward compatibility.

Reads one or more JIRA CSV exports (same column schema) and produces:
  1. Per-role manhour actuals  (Dev / QA)
  2. Queue/wait time breakdown (nobody's clock — team process metric)
  3. Proposal vs actuals comparison
  4. Per-developer and per-QA-tester KPI summary
  5. Sprint-level summary table

Usage
-----
    python mandays_report.py --files sprint1.csv sprint2.csv --proposal proposal.json

Proposal JSON format (all values in hours):
    {
        "sprints": {
            "Ph 2.6.1": {
                "backend_hours":  40,
                "frontend_hours": 32,
                "mobile_hours":   24,
                "qa_hours":       20
            }
        }
    }

If no --proposal is given the script still runs and reports actuals only.

Outputs
-------
  - mandays_report.txt   human-readable summary
  - mandays_report.csv   machine-readable, one row per developer per sprint
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime


# ─── helpers ─────────────────────────────────────────────────────────────────

def parse_hours(value):
    """Return float hours, or None for 'N/A' / blank / unparseable."""
    if value is None:
        return None
    v = str(value).strip()
    if v.lower() in ("n/a", "", "none", "-"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def h(value, decimals=2):
    """Format hours for display, '-' when None."""
    if value is None:
        return "-"
    return f"{value:.{decimals}f}h"


def pct(part, total):
    """Return percentage string, '-' when total is 0 or None."""
    if not total or not part:
        return "-"
    return f"{part / total * 100:.1f}%"


def variance_str(actual, proposed):
    """Return e.g. '+3.5h (8.8% over)' or '-2.0h (5.0% under)'."""
    if actual is None or proposed is None or proposed == 0:
        return "n/a"
    diff = actual - proposed
    sign = "+" if diff >= 0 else ""
    direction = "over" if diff >= 0 else "under"
    return f"{sign}{diff:.1f}h ({abs(diff)/proposed*100:.1f}% {direction})"


# ─── load & parse CSVs ───────────────────────────────────────────────────────

def load_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def parse_tickets(rows):
    """
    Normalise raw CSV rows into structured ticket dicts.

    Dev mandays  = dev_to_code_review_time  (time developer actively coded)
                   code_review_time is attributed to dev role (author responding
                   to review comments), NOT to the reviewer.
    QA mandays   = qa_testing_time + qa_regression_testing_time
    Queue waste  = dev_to_qa_ready_time - dev_to_code_review_time
                   - code_review_time  (time ticket sat idle waiting for QA)
    """
    tickets = []
    for r in rows:
        assignee    = r.get("assignee", "").strip() or "Unassigned"
        qa_tester   = r.get("qa_tester", "").strip() or None
        sprint      = r.get("sprint", "").strip()
        status      = r.get("status", "").strip()
        issuetype   = r.get("issuetype", "").strip()
        key         = r.get("key", "").strip()
        summary     = r.get("summary", "").strip()

        sp_total    = parse_hours(r.get("story_points"))
        sp_dev      = parse_hours(r.get("story_point_dev"))
        sp_qa       = parse_hours(r.get("story_point_qa"))

        t_dev       = parse_hours(r.get("dev_to_code_review_time"))   # dev active coding
        t_cr        = parse_hours(r.get("code_review_time"))           # in review (attr to dev)
        t_cr_wait   = parse_hours(r.get("code_review_waiting_time"))   # wait for reviewer
        t_qa_ready  = parse_hours(r.get("dev_to_qa_ready_time"))       # dev+review end
        t_qa        = parse_hours(r.get("qa_testing_time"))
        t_qa_reg    = parse_hours(r.get("qa_regression_testing_time"))
        t_done      = parse_hours(r.get("dev_to_done_time"))

        # ── manhour calculations ────────────────────────────────────────────
        #
        # dev_manhours: coding time + code-review response time
        dev_coding   = t_dev
        dev_review   = t_cr   # time ticket spent in review = dev's response overhead
        dev_manhours = None
        if dev_coding is not None and dev_review is not None:
            dev_manhours = dev_coding + dev_review
        elif dev_coding is not None:
            dev_manhours = dev_coding
        elif dev_review is not None:
            dev_manhours = dev_review

        # qa_manhours: active testing + regression
        qa_manhours = None
        if t_qa is not None and t_qa_reg is not None:
            qa_manhours = t_qa + t_qa_reg
        elif t_qa is not None:
            qa_manhours = t_qa
        elif t_qa_reg is not None:
            qa_manhours = t_qa_reg

        # queue_waste: time ticket sat idle between code-review-done and QA start
        queue_waste = None
        if t_qa_ready is not None and t_dev is not None:
            cr_done = t_dev + (t_cr or 0)
            queue_waste = max(0.0, t_qa_ready - cr_done)

        # effort_ratio: actual dev time vs estimated (1 SP = 1 hour)
        effort_ratio = None
        if dev_manhours is not None and sp_dev and sp_dev > 0:
            effort_ratio = dev_manhours / sp_dev

        tickets.append({
            "key":          key,
            "summary":      summary,
            "sprint":       sprint,
            "status":       status,
            "issuetype":    issuetype,
            "assignee":     assignee,
            "qa_tester":    qa_tester,
            "sp_total":     sp_total,
            "sp_dev":       sp_dev,
            "sp_qa":        sp_qa,
            "dev_coding":   dev_coding,
            "dev_review":   dev_review,
            "dev_manhours": dev_manhours,
            "cr_wait":      t_cr_wait,
            "queue_waste":  queue_waste,
            "qa_manhours":  qa_manhours,
            "t_qa":         t_qa,
            "t_qa_reg":     t_qa_reg,
            "t_done":       t_done,
            "effort_ratio": effort_ratio,
        })

    return tickets


# ─── aggregations ────────────────────────────────────────────────────────────

def sprint_summary(tickets):
    """
    Returns dict keyed by sprint name with aggregated metrics.
    """
    sprints = defaultdict(lambda: {
        "tickets": 0, "done": 0,
        "sp_total": 0.0, "sp_dev": 0.0, "sp_qa": 0.0,
        "dev_manhours": 0.0, "qa_manhours": 0.0,
        "queue_waste": 0.0, "cr_wait": 0.0,
        "dev_coding": 0.0, "dev_review": 0.0,
        "t_done_sum": 0.0, "t_done_count": 0,
    })

    for t in tickets:
        s = t["sprint"]
        sprints[s]["tickets"] += 1
        if t["status"] == "Done":
            sprints[s]["done"] += 1
        if t["sp_total"]:
            sprints[s]["sp_total"] += t["sp_total"]
        if t["sp_dev"]:
            sprints[s]["sp_dev"] += t["sp_dev"]
        if t["sp_qa"]:
            sprints[s]["sp_qa"] += t["sp_qa"]
        if t["dev_manhours"]:
            sprints[s]["dev_manhours"] += t["dev_manhours"]
        if t["dev_coding"]:
            sprints[s]["dev_coding"] += t["dev_coding"]
        if t["dev_review"]:
            sprints[s]["dev_review"] += t["dev_review"]
        if t["qa_manhours"]:
            sprints[s]["qa_manhours"] += t["qa_manhours"]
        if t["queue_waste"]:
            sprints[s]["queue_waste"] += t["queue_waste"]
        if t["cr_wait"]:
            sprints[s]["cr_wait"] += t["cr_wait"]
        if t["t_done"]:
            sprints[s]["t_done_sum"] += t["t_done"]
            sprints[s]["t_done_count"] += 1

    return dict(sprints)


def developer_summary(tickets):
    """
    Returns dict keyed by (sprint, assignee) with per-dev metrics.
    """
    devs = defaultdict(lambda: {
        "tickets": 0, "done": 0,
        "sp_dev": 0.0, "dev_manhours": 0.0,
        "effort_ratios": [],
        "queue_waste": 0.0, "cr_wait": 0.0,
    })

    for t in tickets:
        if t["assignee"] == "Unassigned":
            continue
        key = (t["sprint"], t["assignee"])
        devs[key]["tickets"] += 1
        if t["status"] == "Done":
            devs[key]["done"] += 1
        if t["sp_dev"]:
            devs[key]["sp_dev"] += t["sp_dev"]
        if t["dev_manhours"]:
            devs[key]["dev_manhours"] += t["dev_manhours"]
        if t["effort_ratio"]:
            devs[key]["effort_ratios"].append(t["effort_ratio"])
        if t["queue_waste"]:
            devs[key]["queue_waste"] += t["queue_waste"]
        if t["cr_wait"]:
            devs[key]["cr_wait"] += t["cr_wait"]

    # compute avg effort ratio
    result = {}
    for k, v in devs.items():
        ratios = v["effort_ratios"]
        v["avg_effort_ratio"] = sum(ratios) / len(ratios) if ratios else None
        v["completion_rate"] = v["done"] / v["tickets"] if v["tickets"] else 0
        result[k] = v

    return result


def qa_summary(tickets):
    """
    Returns dict keyed by (sprint, qa_tester) with QA metrics.
    """
    testers = defaultdict(lambda: {
        "tickets": 0, "sp_qa": 0.0,
        "qa_manhours": 0.0, "t_qa": 0.0, "t_qa_reg": 0.0,
    })

    for t in tickets:
        if not t["qa_tester"]:
            continue
        key = (t["sprint"], t["qa_tester"])
        testers[key]["tickets"] += 1
        if t["sp_qa"]:
            testers[key]["sp_qa"] += t["sp_qa"]
        if t["qa_manhours"]:
            testers[key]["qa_manhours"] += t["qa_manhours"]
        if t["t_qa"]:
            testers[key]["t_qa"] += t["t_qa"]
        if t["t_qa_reg"]:
            testers[key]["t_qa_reg"] += t["t_qa_reg"]

    result = {}
    for k, v in testers.items():
        v["qa_rate"] = (v["qa_manhours"] / v["sp_qa"]
                        if v["sp_qa"] and v["sp_qa"] > 0 else None)
        v["regression_ratio"] = (v["t_qa_reg"] / v["qa_manhours"]
                                  if v["qa_manhours"] and v["qa_manhours"] > 0 else None)
        result[k] = v

    return result


# ─── report formatting ────────────────────────────────────────────────────────

SEP  = "=" * 72
SEP2 = "-" * 72
NL   = "\n"


def fmt_section(title):
    return f"\n{SEP}\n  {title}\n{SEP}\n"


def fmt_sub(title):
    return f"\n{SEP2}\n  {title}\n{SEP2}\n"


def build_report(tickets, proposals, sprint_data, dev_data, qa_data):
    lines = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"MANDAYS REPORT  |  generated {ts}")
    lines.append(f"Tickets loaded : {len(tickets)}")

    sprints_found = sorted({t['sprint'] for t in tickets})
    lines.append(f"Sprints        : {', '.join(sprints_found)}")
    lines.append(SEP)

    # ── sprint summaries ──────────────────────────────────────────────────
    lines.append(fmt_section("SPRINT SUMMARY"))

    col_w = [22, 8, 8, 12, 12, 12, 12, 10]
    hdr = (
        f"{'Sprint':<{col_w[0]}}"
        f"{'Tkts':>{col_w[1]}}"
        f"{'Done':>{col_w[2]}}"
        f"{'Dev hrs':>{col_w[3]}}"
        f"{'QA hrs':>{col_w[4]}}"
        f"{'Wait':>{col_w[5]}}"
        f"{'Flow eff':>{col_w[6]}}"
        f"{'Cmpl%':>{col_w[7]}}"
    )
    lines.append(hdr)
    lines.append("-" * sum(col_w))

    for sprint, sd in sorted(sprint_data.items()):
        total_active = sd["dev_manhours"] + sd["qa_manhours"]
        total_elapsed = total_active + sd["queue_waste"] + sd["cr_wait"]
        flow_eff = pct(total_active, total_elapsed) if total_elapsed > 0 else "-"
        cmpl = pct(sd["done"], sd["tickets"])
        lines.append(
            f"{sprint:<{col_w[0]}}"
            f"{sd['tickets']:>{col_w[1]}}"
            f"{sd['done']:>{col_w[2]}}"
            f"{h(sd['dev_manhours'] or None):>{col_w[3]}}"
            f"{h(sd['qa_manhours'] or None):>{col_w[4]}}"
            f"{h(sd['queue_waste'] or None):>{col_w[5]}}"
            f"{flow_eff:>{col_w[6]}}"
            f"{cmpl:>{col_w[7]}}"
        )

    lines.append(NL + "Wait = QA queue wait + code review wait (nobody's clock — process idle time)")
    lines.append("Flow eff = active work time ÷ total elapsed time")

    # ── proposal vs actuals ──────────────────────────────────────────────
    lines.append(fmt_section("PROPOSAL vs ACTUALS"))

    if not proposals:
        lines.append("  No proposal file provided. Run with --proposal <file.json> to enable.\n")
    else:
        for sprint, sd in sorted(sprint_data.items()):
            prop = proposals.get("sprints", {}).get(sprint)
            lines.append(fmt_sub(f"Sprint: {sprint}"))

            if not prop:
                lines.append(f"  No proposal data found for sprint '{sprint}'\n")
                continue

            # infer role breakdown from story_point_dev / qa totals
            # (user may track backend/frontend/mobile separately via labels)
            dev_proposed = (prop.get("backend_hours", 0) +
                            prop.get("frontend_hours", 0) +
                            prop.get("mobile_hours", 0))
            qa_proposed  = prop.get("qa_hours", 0)

            dev_actual = sd["dev_manhours"] or 0
            qa_actual  = sd["qa_manhours"] or 0
            wait_actual = sd["queue_waste"] + sd["cr_wait"]

            lines.append(f"  {'Role':<18} {'Proposed':>10} {'Actual':>10} {'Variance':>28}")
            lines.append(f"  {'-'*18} {'-'*10} {'-'*10} {'-'*28}")
            lines.append(f"  {'Dev (all roles)':<18} {h(dev_proposed):>10} {h(dev_actual):>10} {variance_str(dev_actual, dev_proposed):>28}")
            if prop.get("backend_hours"):
                lines.append(f"    {'↳ Backend':<16} {h(prop['backend_hours']):>10}")
            if prop.get("frontend_hours"):
                lines.append(f"    {'↳ Frontend':<16} {h(prop['frontend_hours']):>10}")
            if prop.get("mobile_hours"):
                lines.append(f"    {'↳ Mobile':<16} {h(prop['mobile_hours']):>10}")
            lines.append(f"  {'QA':<18} {h(qa_proposed):>10} {h(qa_actual):>10} {variance_str(qa_actual, qa_proposed):>28}")
            lines.append(f"  {'Queue wait':<18} {'—':>10} {h(wait_actual):>10} {'(process overhead — not in proposal)':>28}")

            total_proposed = dev_proposed + qa_proposed
            total_actual   = dev_actual + qa_actual
            lines.append(f"  {'-'*68}")
            lines.append(f"  {'TOTAL':<18} {h(total_proposed):>10} {h(total_actual):>10} {variance_str(total_actual, total_proposed):>28}")
            lines.append("")

    # ── developer KPI ────────────────────────────────────────────────────
    lines.append(fmt_section("DEVELOPER KPI"))

    col_w2 = [24, 10, 8, 8, 10, 10, 10, 10]
    hdr2 = (
        f"  {'Developer':<{col_w2[0]}}"
        f"{'Sprint':<{col_w2[1]}}"
        f"{'SP':>{col_w2[2]}}"
        f"{'Hrs':>{col_w2[3]}}"
        f"{'Eff.ratio':>{col_w2[4]}}"
        f"{'Cmpl%':>{col_w2[5]}}"
        f"{'QueueWait':>{col_w2[6]}}"
        f"{'CRWait':>{col_w2[7]}}"
    )
    lines.append(hdr2)
    lines.append("  " + "-" * (sum(col_w2) - 2))

    for (sprint, dev), dd in sorted(dev_data.items()):
        ratio = dd["avg_effort_ratio"]
        ratio_str = f"{ratio:.2f}x" if ratio else "-"
        flag = ""
        if ratio and ratio > 1.5:
            flag = " ⚠ over"
        elif ratio and ratio < 0.6:
            flag = " ↓ under"

        lines.append(
            f"  {dev:<{col_w2[0]}}"
            f"{sprint:<{col_w2[1]}}"
            f"{h(dd['sp_dev'] or None, 1):>{col_w2[2]}}"
            f"{h(dd['dev_manhours'] or None, 1):>{col_w2[3]}}"
            f"{ratio_str + flag:>{col_w2[4]+len(flag)}}"
            f"{pct(dd['done'], dd['tickets']):>{col_w2[5]}}"
            f"{h(dd['queue_waste'] or None, 1):>{col_w2[6]}}"
            f"{h(dd['cr_wait'] or None, 1):>{col_w2[7]}}"
        )

    lines.append(NL + "  Effort ratio = actual dev hrs / estimated (SP hrs). Ideal = 1.0x")
    lines.append("  ⚠ flagged if ratio > 1.5  |  ↓ flagged if ratio < 0.6")
    lines.append("  QueueWait = idle time the developer's tickets spent waiting for QA")
    lines.append("  CRWait    = idle time waiting for a reviewer to pick up the PR")

    # ── QA KPI ──────────────────────────────────────────────────────────
    lines.append(fmt_section("QA TESTER KPI"))

    col_w3 = [24, 10, 8, 10, 10, 12]
    hdr3 = (
        f"  {'QA Tester':<{col_w3[0]}}"
        f"{'Sprint':<{col_w3[1]}}"
        f"{'SP':>{col_w3[2]}}"
        f"{'QA hrs':>{col_w3[3]}}"
        f"{'Hrs/SP':>{col_w3[4]}}"
        f"{'Regression%':>{col_w3[5]}}"
    )
    lines.append(hdr3)
    lines.append("  " + "-" * (sum(col_w3) - 2))

    for (sprint, tester), qd in sorted(qa_data.items()):
        rate_str = f"{qd['qa_rate']:.2f}h" if qd["qa_rate"] else "-"
        reg_str  = pct(qd["t_qa_reg"] or 0, qd["qa_manhours"])
        lines.append(
            f"  {tester:<{col_w3[0]}}"
            f"{sprint:<{col_w3[1]}}"
            f"{h(qd['sp_qa'] or None, 1):>{col_w3[2]}}"
            f"{h(qd['qa_manhours'] or None, 1):>{col_w3[3]}}"
            f"{rate_str:>{col_w3[4]}}"
            f"{reg_str:>{col_w3[5]}}"
        )

    lines.append(NL + "  Hrs/SP = QA manhours per story point. Track for consistency over time.")
    lines.append("  Regression% = share of QA time spent on regression testing.")
    lines.append("  Rising regression% over sprints signals accumulating technical debt.")

    # ── unassigned tickets alert ─────────────────────────────────────────
    unassigned = [t for t in tickets if t["assignee"] == "Unassigned"]
    if unassigned:
        lines.append(fmt_section(f"ALERTS — {len(unassigned)} UNASSIGNED TICKET(S)"))
        for t in unassigned:
            sp_str = f"{t['sp_total']:.0f} SP" if t["sp_total"] else "? SP"
            lines.append(f"  [{t['key']}]  {sp_str}  [{t['status']}]  {t['summary'][:60]}")
        lines.append("")

    lines.append(SEP)
    lines.append("END OF REPORT")
    return NL.join(lines)


def build_csv_output(dev_data, qa_data):
    rows = []

    for (sprint, person), dd in sorted(dev_data.items()):
        rows.append({
            "sprint":        sprint,
            "person":        person,
            "role":          "Dev",
            "tickets":       dd["tickets"],
            "tickets_done":  dd["done"],
            "completion_pct":round(dd["completion_rate"] * 100, 1),
            "sp_estimated":  round(dd["sp_dev"] or 0, 2),
            "manhours_actual": round(dd["dev_manhours"] or 0, 2),
            "effort_ratio":  round(dd["avg_effort_ratio"], 3) if dd["avg_effort_ratio"] else "",
            "queue_wait_h":  round(dd["queue_waste"] or 0, 2),
            "cr_wait_h":     round(dd["cr_wait"] or 0, 2),
            "qa_manhours":   "",
            "qa_hrs_per_sp": "",
            "regression_pct":"",
        })

    for (sprint, person), qd in sorted(qa_data.items()):
        rows.append({
            "sprint":        sprint,
            "person":        person,
            "role":          "QA",
            "tickets":       qd["tickets"],
            "tickets_done":  "",
            "completion_pct":"",
            "sp_estimated":  round(qd["sp_qa"] or 0, 2),
            "manhours_actual":"",
            "effort_ratio":  "",
            "queue_wait_h":  "",
            "cr_wait_h":     "",
            "qa_manhours":   round(qd["qa_manhours"] or 0, 2),
            "qa_hrs_per_sp": round(qd["qa_rate"], 3) if qd["qa_rate"] else "",
            "regression_pct":round((qd["t_qa_reg"] or 0) / qd["qa_manhours"] * 100, 1)
                              if qd["qa_manhours"] else "",
        })

    return rows


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="JIRA mandays report — proposal vs actuals")
    parser.add_argument("--files",    nargs="+", required=True,
                        help="One or more JIRA CSV export files")
    parser.add_argument("--proposal", default=None,
                        help="JSON file with proposed hours per sprint/role")
    parser.add_argument("--out-dir",  default=".",
                        help="Output directory (default: current directory)")
    args = parser.parse_args()

    # load tickets
    all_rows = []
    for path in args.files:
        if not os.path.exists(path):
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        rows = load_csv(path)
        all_rows.extend(rows)
        print(f"Loaded {len(rows)} rows from {path}")

    tickets = parse_tickets(all_rows)
    print(f"Parsed {len(tickets)} tickets across {len({t['sprint'] for t in tickets})} sprint(s)")

    # load proposal
    proposals = {}
    if args.proposal:
        with open(args.proposal) as f:
            proposals = json.load(f)
        print(f"Loaded proposal from {args.proposal}")

    # aggregate
    sprint_data = sprint_summary(tickets)
    dev_data    = developer_summary(tickets)
    qa_data     = qa_summary(tickets)

    # build outputs
    report_text = build_report(tickets, proposals, sprint_data, dev_data, qa_data)
    csv_rows    = build_csv_output(dev_data, qa_data)

    os.makedirs(args.out_dir, exist_ok=True)
    txt_path = os.path.join(args.out_dir, "mandays_report.txt")
    csv_path = os.path.join(args.out_dir, "mandays_report.csv")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    fieldnames = [
        "sprint","person","role","tickets","tickets_done","completion_pct",
        "sp_estimated","manhours_actual","effort_ratio",
        "queue_wait_h","cr_wait_h",
        "qa_manhours","qa_hrs_per_sp","regression_pct",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)

    print(f"\nOutputs written:")
    print(f"  {txt_path}")
    print(f"  {csv_path}")
    print("\n--- REPORT PREVIEW ---\n")
    print(report_text)


if __name__ == "__main__":
    main()