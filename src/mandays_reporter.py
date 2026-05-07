"""Mandays report generator for JIRA time tracking analysis."""

import csv
import json
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.config_manager import ConfigManager


class MandaysReporter:
    """Generates mandays reports from JIRA analysis results.

    Produces:
      - Per-role manhour actuals (Dev / QA)
      - Queue/wait time breakdown
      - Proposal vs actuals comparison
      - Per-developer and per-QA-tester KPI summaries
      - Sprint-level summary tables
    """

    SEP = "=" * 72
    SEP2 = "-" * 72
    NL = "\n"

    def __init__(self, config: ConfigManager):
        """Initialize mandays reporter.

        Args:
            config: Configuration manager instance
        """
        self.config = config

    def parse_hours(self, value: Any) -> Optional[float]:
        """Return float hours, or None for 'N/A' / blank / unparseable.

        Args:
            value: Value to parse

        Returns:
            Float hours or None
        """
        if value is None:
            return None
        v = str(value).strip()
        if v.lower() in ("n/a", "", "none", "-"):
            return None
        try:
            return float(v)
        except ValueError:
            return None

    def h(self, value: Optional[float], decimals: int = 2) -> str:
        """Format hours for display, '-' when None.

        Args:
            value: Hours value
            decimals: Number of decimal places

        Returns:
            Formatted string
        """
        if value is None:
            return "-"
        return f"{value:.{decimals}f}h"

    def pct(self, part: Optional[float], total: Optional[float]) -> str:
        """Return percentage string, '-' when total is 0 or None.

        Args:
            part: Part value
            total: Total value

        Returns:
            Percentage string or "-"
        """
        if not total or not part:
            return "-"
        return f"{part / total * 100:.1f}%"

    def variance_str(self, actual: Optional[float], proposed: Optional[float]) -> str:
        """Return variance string e.g. '+3.5h (8.8% over)' or '-2.0h (5.0% under)'.

        Args:
            actual: Actual hours
            proposed: Proposed hours

        Returns:
            Variance string
        """
        if actual is None or proposed is None or proposed == 0:
            return "n/a"
        diff = actual - proposed
        sign = "+" if diff >= 0 else ""
        direction = "over" if diff >= 0 else "under"
        return f"{sign}{diff:.1f}h ({abs(diff)/proposed*100:.1f}% {direction})"

    def parse_tickets(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Normalize raw JIRA results into structured ticket dicts.

        Dev mandays  = dev_to_code_review_time (time developer actively coded)
                       code_review_time is attributed to dev role (author responding
                       to review comments), NOT to the reviewer.
        QA mandays   = qa_testing_time + qa_regression_testing_time
        Queue waste  = dev_to_qa_ready_time - dev_to_code_review_time
                       - code_review_time (time ticket sat idle waiting for QA)

        Args:
            results: Raw JIRA analysis results

        Returns:
            List of normalized ticket dictionaries
        """
        tickets = []
        for r in results:
            assignee = r.get("assignee", "").strip() or "Unassigned"
            qa_tester = r.get("qa_tester", "").strip() or None
            sprint = r.get("sprint", "").strip()
            status = r.get("status", "").strip()
            issuetype = r.get("issuetype", "").strip()
            key = r.get("key", "").strip()
            summary = r.get("summary", "").strip()

            sp_total = self.parse_hours(r.get("story_points"))
            sp_dev = self.parse_hours(r.get("story_point_dev"))
            sp_qa = self.parse_hours(r.get("story_point_qa"))

            t_dev = self.parse_hours(r.get("dev_to_code_review_time"))
            t_cr = self.parse_hours(r.get("code_review_time"))
            t_cr_wait = self.parse_hours(r.get("code_review_waiting_time"))
            t_qa_ready = self.parse_hours(r.get("dev_to_qa_ready_time"))
            t_qa = self.parse_hours(r.get("qa_testing_time"))
            t_qa_reg = self.parse_hours(r.get("qa_regression_testing_time"))
            t_done = self.parse_hours(r.get("dev_to_done_time"))

            # dev_manhours: coding time + code-review response time
            dev_coding = t_dev
            dev_review = t_cr
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
                "key": key,
                "summary": summary,
                "sprint": sprint,
                "status": status,
                "issuetype": issuetype,
                "assignee": assignee,
                "qa_tester": qa_tester,
                "sp_total": sp_total,
                "sp_dev": sp_dev,
                "sp_qa": sp_qa,
                "dev_coding": dev_coding,
                "dev_review": dev_review,
                "dev_manhours": dev_manhours,
                "cr_wait": t_cr_wait,
                "queue_waste": queue_waste,
                "qa_manhours": qa_manhours,
                "t_qa": t_qa,
                "t_qa_reg": t_qa_reg,
                "t_done": t_done,
                "effort_ratio": effort_ratio,
                # Bug fields
                "linked_bugs": r.get("linked_bugs", []),
                "bug_count": r.get("bug_count", 0),
                "test_case_bug_count": r.get("test_case_bug_count", 0),
                "blocker_bug_count": r.get("blocker_bug_count", 0),
                "regular_bug_count": r.get("regular_bug_count", 0),
            })

        return tickets

    def sprint_summary(self, tickets: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Calculate sprint-level aggregated metrics.

        Args:
            tickets: List of normalized ticket dictionaries

        Returns:
            Dictionary keyed by sprint name with metrics
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

    def developer_summary(self, tickets: List[Dict[str, Any]]) -> Dict[tuple, Dict[str, Any]]:
        """Calculate per-developer metrics.

        Args:
            tickets: List of normalized ticket dictionaries

        Returns:
            Dictionary keyed by (sprint, assignee) with metrics
        """
        devs = defaultdict(lambda: {
            "tickets": 0, "done": 0,
            "sp_dev": 0.0, "dev_manhours": 0.0,
            "effort_ratios": [],
            "queue_waste": 0.0, "cr_wait": 0.0,
            # Bug tracking
            "bugs_created": 0,
            "test_case_bugs": 0,
            "blocker_bugs": 0,
            "regular_bugs": 0,
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

            # Bug tracking - only count bugs for Task/Story types
            if t.get("issuetype") in ["Task", "Story"]:
                devs[key]["bugs_created"] += t.get("bug_count", 0)
                devs[key]["test_case_bugs"] += t.get("test_case_bug_count", 0)
                devs[key]["blocker_bugs"] += t.get("blocker_bug_count", 0)
                devs[key]["regular_bugs"] += t.get("regular_bug_count", 0)

        result = {}
        for k, v in devs.items():
            ratios = v["effort_ratios"]
            v["avg_effort_ratio"] = sum(ratios) / len(ratios) if ratios else None
            v["completion_rate"] = v["done"] / v["tickets"] if v["tickets"] else 0
            result[k] = v

        return result

    def qa_summary(self, tickets: List[Dict[str, Any]]) -> Dict[tuple, Dict[str, Any]]:
        """Calculate per-QA-tester metrics.

        Args:
            tickets: List of normalized ticket dictionaries

        Returns:
            Dictionary keyed by (sprint, qa_tester) with metrics
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

    def fmt_section(self, title: str) -> str:
        """Format a section header.

        Args:
            title: Section title

        Returns:
            Formatted section header
        """
        return f"\n{self.SEP}\n  {title}\n{self.SEP}\n"

    def fmt_sub(self, title: str) -> str:
        """Format a subsection header.

        Args:
            title: Subsection title

        Returns:
            Formatted subsection header
        """
        return f"\n{self.SEP2}\n  {title}\n{self.SEP2}\n"

    def build_report(
        self,
        tickets: List[Dict[str, Any]],
        proposals: Dict[str, Any],
        sprint_data: Dict[str, Dict[str, Any]],
        dev_data: Dict[tuple, Dict[str, Any]],
        qa_data: Dict[tuple, Dict[str, Any]]
    ) -> str:
        """Build human-readable text report.

        Args:
            tickets: List of normalized ticket dictionaries
            proposals: Proposal data from JSON file
            sprint_data: Sprint-level aggregated metrics
            dev_data: Per-developer metrics
            qa_data: Per-QA metrics

        Returns:
            Formatted report string
        """
        lines = []
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines.append(f"MANDAYS REPORT  |  generated {ts}")
        lines.append(f"Tickets loaded : {len(tickets)}")

        sprints_found = sorted({t['sprint'] for t in tickets})
        lines.append(f"Sprints        : {', '.join(sprints_found)}")
        lines.append(self.SEP)

        # Sprint summaries
        lines.append(self.fmt_section("SPRINT SUMMARY"))

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
            flow_eff = self.pct(total_active, total_elapsed) if total_elapsed > 0 else "-"
            cmpl = self.pct(sd["done"], sd["tickets"])
            lines.append(
                f"{sprint:<{col_w[0]}}"
                f"{sd['tickets']:>{col_w[1]}}"
                f"{sd['done']:>{col_w[2]}}"
                f"{self.h(sd['dev_manhours'] or None):>{col_w[3]}}"
                f"{self.h(sd['qa_manhours'] or None):>{col_w[4]}}"
                f"{self.h(sd['queue_waste'] or None):>{col_w[5]}}"
                f"{flow_eff:>{col_w[6]}}"
                f"{cmpl:>{col_w[7]}}"
            )

        lines.append(self.NL + "Wait = QA queue wait + code review wait (nobody's clock — process idle time)")
        lines.append("Flow eff = active work time ÷ total elapsed time")

        # Proposal vs actuals
        lines.append(self.fmt_section("PROPOSAL vs ACTUALS"))

        if not proposals:
            lines.append("  No proposal file provided. Run with --proposal <file.json> to enable.\n")
        else:
            for sprint, sd in sorted(sprint_data.items()):
                prop = proposals.get("sprints", {}).get(sprint)
                lines.append(self.fmt_sub(f"Sprint: {sprint}"))

                if not prop:
                    lines.append(f"  No proposal data found for sprint '{sprint}'\n")
                    continue

                dev_proposed = (prop.get("backend_hours", 0) +
                                prop.get("frontend_hours", 0) +
                                prop.get("mobile_hours", 0))
                qa_proposed = prop.get("qa_hours", 0)

                dev_actual = sd["dev_manhours"] or 0
                qa_actual = sd["qa_manhours"] or 0
                wait_actual = sd["queue_waste"] + sd["cr_wait"]

                lines.append(f"  {'Role':<18} {'Proposed':>10} {'Actual':>10} {'Variance':>28}")
                lines.append(f"  {'-'*18} {'-'*10} {'-'*10} {'-'*28}")
                lines.append(f"  {'Dev (all roles)':<18} {self.h(dev_proposed):>10} {self.h(dev_actual):>10} {self.variance_str(dev_actual, dev_proposed):>28}")
                if prop.get("backend_hours"):
                    lines.append(f"    {'↳ Backend':<16} {self.h(prop['backend_hours']):>10}")
                if prop.get("frontend_hours"):
                    lines.append(f"    {'↳ Frontend':<16} {self.h(prop['frontend_hours']):>10}")
                if prop.get("mobile_hours"):
                    lines.append(f"    {'↳ Mobile':<16} {self.h(prop['mobile_hours']):>10}")
                lines.append(f"  {'QA':<18} {self.h(qa_proposed):>10} {self.h(qa_actual):>10} {self.variance_str(qa_actual, qa_proposed):>28}")
                lines.append(f"  {'Queue wait':<18} {'—':>10} {self.h(wait_actual):>10} {'(process overhead — not in proposal)':>28}")

                total_proposed = dev_proposed + qa_proposed
                total_actual = dev_actual + qa_actual
                lines.append(f"  {'-'*68}")
                lines.append(f"  {'TOTAL':<18} {self.h(total_proposed):>10} {self.h(total_actual):>10} {self.variance_str(total_actual, total_proposed):>28}")
                lines.append("")

        # Developer KPI
        lines.append(self.fmt_section("DEVELOPER KPI"))

        col_w2 = [22, 10, 7, 7, 9, 8, 6, 3, 3, 3, 9, 9]
        hdr2 = (
            f"  {'Developer':<{col_w2[0]}}"
            f"{'Sprint':<{col_w2[1]}}"
            f"{'SP':>{col_w2[2]}}"
            f"{'Hrs':>{col_w2[3]}}"
            f"{'Eff.ratio':>{col_w2[4]}}"
            f"{'Cmpl%':>{col_w2[5]}}"
            f"{'Bugs':>{col_w2[6]}}"
            f"{'TC':>{col_w2[7]}}"
            f"{'Blk':>{col_w2[8]}}"
            f"{'Reg':>{col_w2[9]}}"
            f"{'QueueWait':>{col_w2[10]}}"
            f"{'CRWait':>{col_w2[11]}}"
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
                f"{self.h(dd['sp_dev'] or None, 1):>{col_w2[2]}}"
                f"{self.h(dd['dev_manhours'] or None, 1):>{col_w2[3]}}"
                f"{ratio_str + flag:>{col_w2[4]+len(flag)}}"
                f"{self.pct(dd['done'], dd['tickets']):>{col_w2[5]}}"
                f"{dd.get('bugs_created', 0):>{col_w2[6]}}"
                f"{dd.get('test_case_bugs', 0):>{col_w2[7]}}"
                f"{dd.get('blocker_bugs', 0):>{col_w2[8]}}"
                f"{dd.get('regular_bugs', 0):>{col_w2[9]}}"
                f"{self.h(dd['queue_waste'] or None, 1):>{col_w2[10]}}"
                f"{self.h(dd['cr_wait'] or None, 1):>{col_w2[11]}}"
            )

        lines.append(self.NL + "  Effort ratio = actual dev hrs / estimated (SP hrs). Ideal = 1.0x")
        lines.append("  ⚠ flagged if ratio > 1.5  |  ↓ flagged if ratio < 0.6")
        lines.append("  Bugs = Total bugs created | TC = Test Case | Blk = Blocker | Reg = Regular")
        lines.append("  QueueWait = idle time the developer's tickets spent waiting for QA")
        lines.append("  CRWait    = idle time waiting for a reviewer to pick up the PR")

        # QA KPI
        lines.append(self.fmt_section("QA TESTER KPI"))

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
            reg_str = self.pct(qd["t_qa_reg"] or 0, qd["qa_manhours"])
            lines.append(
                f"  {tester:<{col_w3[0]}}"
                f"{sprint:<{col_w3[1]}}"
                f"{self.h(qd['sp_qa'] or None, 1):>{col_w3[2]}}"
                f"{self.h(qd['qa_manhours'] or None, 1):>{col_w3[3]}}"
                f"{rate_str:>{col_w3[4]}}"
                f"{reg_str:>{col_w3[5]}}"
            )

        lines.append(self.NL + "  Hrs/SP = QA manhours per story point. Track for consistency over time.")
        lines.append("  Regression% = share of QA time spent on regression testing.")
        lines.append("  Rising regression% over sprints signals accumulating technical debt.")

        # Bug Summary section
        bug_tasks = [t for t in tickets if t.get("issuetype") in ["Task", "Story"] and t.get("bug_count", 0) > 0]
        if bug_tasks:
            lines.append(self.fmt_section("BUG SUMMARY"))

            # By Task table
            lines.append(self.fmt_sub("Bugs by Task"))
            col_w_bug = [14, 18, 12, 8, 8, 8, 8]
            hdr_bug = (
                f"  {'Task':<{col_w_bug[0]}}"
                f"{'Developer':<{col_w_bug[1]}}"
                f"{'Sprint':<{col_w_bug[2]}}"
                f"{'Total':>{col_w_bug[3]}}"
                f"{'TC':>{col_w_bug[4]}}"
                f"{'Blk':>{col_w_bug[5]}}"
                f"{'Reg':>{col_w_bug[6]}}"
            )
            lines.append(hdr_bug)
            lines.append("  " + "-" * (sum(col_w_bug) - 2))

            for t in sorted(bug_tasks, key=lambda x: x.get("bug_count", 0), reverse=True):
                lines.append(
                    f"  {t['key']:<{col_w_bug[0]}}"
                    f"{t['assignee']:<{col_w_bug[1]}}"
                    f"{t['sprint']:<{col_w_bug[2]}}"
                    f"{t.get('bug_count', 0):>{col_w_bug[3]}}"
                    f"{t.get('test_case_bug_count', 0):>{col_w_bug[4]}}"
                    f"{t.get('blocker_bug_count', 0):>{col_w_bug[5]}}"
                    f"{t.get('regular_bug_count', 0):>{col_w_bug[6]}}"
                )

            lines.append("")
            lines.append("  TC = Test Case Bugs (Bug Label = 'Test Case')")
            lines.append("  Blk = Blocker Bugs (Severity = 'Blocker')")
            lines.append("  Reg = Regular Bugs (neither TC nor Blocker)")
            lines.append("")

            # By Sprint summary
            sprint_bug_totals = defaultdict(lambda: {"total": 0, "tc": 0, "blk": 0, "reg": 0})
            for t in bug_tasks:
                sprint = t["sprint"]
                sprint_bug_totals[sprint]["total"] += t.get("bug_count", 0)
                sprint_bug_totals[sprint]["tc"] += t.get("test_case_bug_count", 0)
                sprint_bug_totals[sprint]["blk"] += t.get("blocker_bug_count", 0)
                sprint_bug_totals[sprint]["reg"] += t.get("regular_bug_count", 0)

            lines.append(self.fmt_sub("Bugs by Sprint"))
            col_w_sp = [16, 8, 8, 8, 8]
            hdr_sp = (
                f"  {'Sprint':<{col_w_sp[0]}}"
                f"{'Total':>{col_w_sp[1]}}"
                f"{'TC':>{col_w_sp[2]}}"
                f"{'Blk':>{col_w_sp[3]}}"
                f"{'Reg':>{col_w_sp[4]}}"
            )
            lines.append(hdr_sp)
            lines.append("  " + "-" * sum(col_w_sp))

            for sprint, totals in sorted(sprint_bug_totals.items()):
                lines.append(
                    f"  {sprint:<{col_w_sp[0]}}"
                    f"{totals['total']:>{col_w_sp[1]}}"
                    f"{totals['tc']:>{col_w_sp[2]}}"
                    f"{totals['blk']:>{col_w_sp[3]}}"
                    f"{totals['reg']:>{col_w_sp[4]}}"
                )

            lines.append("")

        # Unassigned tickets alert
        unassigned = [t for t in tickets if t["assignee"] == "Unassigned"]
        if unassigned:
            lines.append(self.fmt_section(f"ALERTS — {len(unassigned)} UNASSIGNED TICKET(S)"))
            for t in unassigned:
                sp_str = f"{t['sp_total']:.0f} SP" if t["sp_total"] else "? SP"
                lines.append(f"  [{t['key']}]  {sp_str}  [{t['status']}]  {t['summary'][:60]}")
            lines.append("")

        lines.append(self.SEP)
        lines.append("END OF REPORT")
        return self.NL.join(lines)

    def build_csv_output(
        self,
        dev_data: Dict[tuple, Dict[str, Any]],
        qa_data: Dict[tuple, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Build machine-readable CSV output rows.

        Args:
            dev_data: Per-developer metrics
            qa_data: Per-QA metrics

        Returns:
            List of dictionaries for CSV output
        """
        rows = []

        for (sprint, person), dd in sorted(dev_data.items()):
            rows.append({
                "sprint": sprint,
                "person": person,
                "role": "Dev",
                "tickets": dd["tickets"],
                "tickets_done": dd["done"],
                "completion_pct": round(dd["completion_rate"] * 100, 1),
                "sp_estimated": round(dd["sp_dev"] or 0, 2),
                "manhours_actual": round(dd["dev_manhours"] or 0, 2),
                "effort_ratio": round(dd["avg_effort_ratio"], 3) if dd["avg_effort_ratio"] else "",
                "bugs_created": dd.get("bugs_created", 0),
                "test_case_bugs": dd.get("test_case_bugs", 0),
                "blocker_bugs": dd.get("blocker_bugs", 0),
                "regular_bugs": dd.get("regular_bugs", 0),
                "queue_wait_h": round(dd["queue_waste"] or 0, 2),
                "cr_wait_h": round(dd["cr_wait"] or 0, 2),
                "qa_manhours": "",
                "qa_hrs_per_sp": "",
                "regression_pct": "",
            })

        for (sprint, person), qd in sorted(qa_data.items()):
            rows.append({
                "sprint": sprint,
                "person": person,
                "role": "QA",
                "tickets": qd["tickets"],
                "tickets_done": "",
                "completion_pct": "",
                "sp_estimated": round(qd["sp_qa"] or 0, 2),
                "manhours_actual": "",
                "effort_ratio": "",
                "queue_wait_h": "",
                "cr_wait_h": "",
                "qa_manhours": round(qd["qa_manhours"] or 0, 2),
                "qa_hrs_per_sp": round(qd["qa_rate"], 3) if qd["qa_rate"] else "",
                "regression_pct": round((qd["t_qa_reg"] or 0) / qd["qa_manhours"] * 100, 1)
                                  if qd["qa_manhours"] else "",
            })

        return rows

    def generate(
        self,
        results: List[Dict[str, Any]],
        proposal_path: Optional[str] = None,
        output_dir: str = "."
    ) -> tuple[str, str]:
        """Generate mandays report from JIRA analysis results.

        Args:
            results: JIRA analysis results from jira_time_tracker
            proposal_path: Optional path to proposal JSON file
            output_dir: Output directory for report files

        Returns:
            Tuple of (txt_report_path, csv_report_path)
        """
        # Load proposal if provided
        proposals = {}
        if proposal_path:
            if not os.path.exists(proposal_path):
                raise FileNotFoundError(f"Proposal file not found: {proposal_path}")
            with open(proposal_path) as f:
                proposals = json.load(f)

        # Parse tickets from results
        tickets = self.parse_tickets(results)

        # Aggregate data
        sprint_data = self.sprint_summary(tickets)
        dev_data = self.developer_summary(tickets)
        qa_data = self.qa_summary(tickets)

        # Build outputs
        report_text = self.build_report(tickets, proposals, sprint_data, dev_data, qa_data)
        csv_rows = self.build_csv_output(dev_data, qa_data)

        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)

        # Write text report
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        txt_path = os.path.join(output_dir, f"mandays_report_{timestamp}.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(report_text)

        # Write CSV report
        csv_path = os.path.join(output_dir, f"mandays_report_{timestamp}.csv")
        fieldnames = [
            "sprint", "person", "role", "tickets", "tickets_done", "completion_pct",
            "sp_estimated", "manhours_actual", "effort_ratio",
            "bugs_created", "test_case_bugs", "blocker_bugs", "regular_bugs",
            "queue_wait_h", "cr_wait_h",
            "qa_manhours", "qa_hrs_per_sp", "regression_pct",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)

        return txt_path, csv_path
