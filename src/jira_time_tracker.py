"""Main orchestration for JIRA time tracking analysis."""

import argparse
import logging
import sys
from typing import Any, Dict, List

import numpy as np

from src.config_manager import ConfigManager
from src.csv_exporter import CsvExporter
from src.jira_client import JiraClient
from src.mandays_reporter import MandaysReporter
from src.time_calculator import TimeCalculator

logger = logging.getLogger(__name__)


class JiraTimeTracker:
    """Main application for analyzing JIRA ticket time spent."""

    def __init__(self, config_path: str = None):
        """Initialize time tracker.

        Args:
            config_path: Path to configuration file
        """
        self.config = ConfigManager(config_path)
        self.config.validate()

        self.jira = JiraClient(
            base_url=self.config.jira_base_url,
            email=self.config.jira_email,
            api_token=self.config.jira_api_token,
        )

        self.calculator = TimeCalculator(
            working_start=self.config.get("working_hours.start", "08:00"),
            working_end=self.config.get("working_hours.end", "17:00"),
            timezone=self.config.get("working_hours.timezone", "Asia/Jakarta"),
            weekends=self.config.get("working_hours.weekends", ["Saturday", "Sunday"]),
            holidays=self.config.get("holidays", []),
        )

        self.exporter = CsvExporter(config=self.config)
        self.mandays_reporter = MandaysReporter(self.config)

    def analyze(self, project=None, sprint: str = None, include_bugs: bool = False) -> List[Dict[str, Any]]:
        """Analyze time spent on tickets in a project or multiple projects.

        Args:
            project: Project key (string), list of project keys, or None (default from env)
            sprint: Sprint name to filter by

        Returns:
            List of analysis results
        """
        if project is None:
            project = self.config.jira_project

        # Convert single project to list for consistent handling
        if isinstance(project, str):
            # Split comma-separated values (e.g., "DEA,KFCAR" or "DEA, KFCAR")
            if "," in project:
                projects = [p.strip() for p in project.split(",")]
            else:
                projects = [project]
        else:
            projects = project

        logger.info(f"Analyzing projects: {projects}")
        if sprint:
            logger.info(f"Filtering by sprint: {sprint}")

        # Check for date range filtering
        date_range_config = self.config.get("date_range", {})
        if date_range_config.get("enabled", False) and not sprint:
            # No specific sprint requested, use date range filtering
            start_date = date_range_config.get("start_date")
            end_date = date_range_config.get("end_date")

            # For multiple projects, filter sprints for each project separately
            all_filtered_sprints = []
            for proj in projects:
                logger.info(f"Date range filtering enabled for {proj}: {start_date} to {end_date}")
                filtered_sprints = self.jira.filter_sprints_by_date_range(
                    proj, start_date, end_date
                )
                all_filtered_sprints.extend(filtered_sprints)

            if all_filtered_sprints:
                sprint = ",".join(f'"{s}"' for s in all_filtered_sprints)
                logger.info(f"Found sprints in date range: {sprint}")
            else:
                logger.warning(f"No sprints found in date range {start_date} to {end_date}")
                # Return empty results if no sprints match
                return []

        # Get status transitions to track
        transitions = self.config.get("status_transitions", [])
        story_points_field = self.config.get("story_points_field", "customfield_10016")
        sprint_field = self.config.get("sprint_field", "customfield_10020")

        # Get additional fields from config
        additional_fields = self.config.get("additional_fields", {})
        additional_field_ids = [fid for fid in additional_fields.values() if fid is not None]

        # Build fields list for JIRA API
        base_fields = ["key", "summary", "status", "assignee", "created", "updated", "issuetype"]
        if story_points_field:
            base_fields.append(story_points_field)
        if sprint_field:
            base_fields.append(sprint_field)
        base_fields.extend(additional_field_ids)

        # Fetch issues with specific fields
        issues = self.jira.get_issues(projects, fields=base_fields, sprint=sprint)
        results = []

        for issue in issues:
            # Extract project from issue key (e.g., "DEA-123" -> "DEA")
            issue_project = issue.key.split("-")[0]

            result = {
                "key": issue.key,
                "project": issue_project,
                "summary": issue.fields.summary,
                "status": issue.fields.status.name,
                "issuetype": issue.fields.issuetype.name if hasattr(issue.fields, 'issuetype') and issue.fields.issuetype else "Unknown",
            }

            # Get assignee
            assignee = getattr(issue.fields, "assignee", None)
            result["assignee"] = assignee.displayName if assignee else "Unassigned"

            # Get story points
            story_points = getattr(issue.fields, story_points_field, None)
            result["story_points"] = story_points if story_points else "N/A"

            # Get sprint information
            if sprint_field:
                sprint_value = getattr(issue.fields, sprint_field, None)
                sprint_name = self.jira.extract_sprint_info(sprint_value)
                result["sprint"] = sprint_name if sprint_name else "No Sprint"

            # Get additional fields
            for field_name, field_id in additional_fields.items():
                if field_id is None:
                    result[field_name] = "N/A"
                    continue

                value = getattr(issue.fields, field_id, None)

                # Handle user objects (e.g., QA Tester)
                if hasattr(value, "displayName"):
                    result[field_name] = value.displayName
                elif hasattr(value, "name"):
                    result[field_name] = value.name
                elif value is None or value == "":
                    result[field_name] = "N/A"
                else:
                    result[field_name] = value

            # Debug: Log first issue details
            if len(results) == 0:
                logger.debug(f"First issue: {issue.key}")
                logger.debug(f"Story points field: {story_points_field}")
                logger.debug(f"Story points value: {story_points}")
                logger.debug(f"Additional fields: {additional_fields}")

            # Get transition history
            history = self.jira.get_issue_history(issue.key)

            # Apply qa_tester fallback if needed (using already fetched history)
            if result.get("qa_tester") == "N/A":
                fallback_config = self.config.get("qa_tester_fallback", {})
                if fallback_config.get("enabled", False):
                    detect_status = fallback_config.get("detect_status", "QA IN PROGRESS")
                    # Look for user who moved ticket TO the target status
                    for entry in reversed(history):  # Most recent first
                        if entry.get("to").lower() == detect_status.lower():
                            result["qa_tester"] = entry.get("name", "N/A")
                            break

            # Calculate times for each transition
            # Get paused statuses from config
            paused_statuses = self.config.get("time_tracking.paused_statuses", [])
            use_pause_tracking = paused_statuses and isinstance(paused_statuses, list)

            for transition in transitions:
                # New format: start_status and end_statuses
                if "start_status" in transition:
                    start_status = transition["start_status"]
                    end_statuses = transition.get("end_statuses", [])

                    # Use pause/resume calculation if configured, otherwise use standard calculation
                    if use_pause_tracking:
                        hours = self.calculator.calculate_transition_time_with_pause(
                            history,
                            start_statuses=[start_status],
                            end_statuses=end_statuses,
                            paused_statuses=paused_statuses,
                        )
                    else:
                        hours = self.calculator.calculate_transition_time(
                            history, start_status=start_status, end_statuses=end_statuses
                        )

                    column_name = transition.get("name", f"{start_status}_to_completion")
                    result[column_name] = f"{hours}" if hours else "N/A"

                # Legacy format support
                elif "from" in transition and "to" in transition:
                    from_status = transition["from"]
                    to_statuses = (
                        [transition["to"]]
                        if isinstance(transition["to"], str)
                        else transition.get("to_statuses", [transition["to"]])
                    )

                    for to_status in to_statuses:
                        if use_pause_tracking:
                            hours = self.calculator.calculate_transition_time_with_pause(
                                history,
                                start_statuses=[from_status],
                                end_statuses=[to_status],
                                paused_statuses=paused_statuses,
                            )
                        else:
                            hours = self.calculator.calculate_transition_time(
                                history, from_status=from_status, to_status=to_status
                            )

                        column_name = transition.get("name", f"{from_status}_to_{to_status}")
                        result[column_name] = f"{hours}" if hours else "N/A"

            # Bug counting (if enabled)
            if include_bugs and result.get("issuetype") in ["Task", "Story"]:
                # Get field IDs from config
                bug_label_field = self.config.get("bug_tracking.bug_label_field")
                severity_field = self.config.get("bug_tracking.severity_field")

                # Get link types from config (should be a list)
                link_types = self.config.get("bug_tracking.link_type", ["Relates", "Blocks"])
                if isinstance(link_types, str):
                    link_types = [link_types]

                # Fetch linked bugs with details
                linked_bugs = self.jira.get_outward_links_with_details(
                    result["key"],
                    link_types=link_types,
                    bug_label_field=bug_label_field,
                    severity_field=severity_field,
                )

                # Get filter values from config
                test_case_label = self.config.get("bug_tracking.test_case_label", "Test Case")
                blocker_severity = self.config.get("bug_tracking.blocker_severity", "Blocker")
                # logger.info(f"Linked Bugs: {linked_bugs}")
                # Categorize bugs
                test_case_bugs = [
                    b for b in linked_bugs
                    if b.get("bug_label") == test_case_label
                ]
                blocker_bugs = [
                    b for b in linked_bugs
                    if b.get("severity") == blocker_severity
                ]

                # Regular bugs are those that are neither test case nor blocker
                # Note: A bug could be both test case AND blocker - it counts in both categories
                # Regular bugs = total - (test case only + blocker only + both)
                # Actually, let's be more careful: we want to avoid double counting
                # A bug is "regular" if it doesn't match either category
                test_case_keys = {b["key"] for b in test_case_bugs}
                blocker_keys = {b["key"] for b in blocker_bugs}
                regular_bugs = [
                    b for b in linked_bugs
                    if b["key"] not in test_case_keys and b["key"] not in blocker_keys
                ]

                result["linked_bugs"] = [b["key"] for b in linked_bugs]
                result["bug_count"] = len(linked_bugs)
                result["test_case_bug_count"] = len(test_case_bugs)
                result["blocker_bug_count"] = len(blocker_bugs)
                result["regular_bug_count"] = len(regular_bugs)
            else:
                result["linked_bugs"] = []
                result["bug_count"] = 0
                result["test_case_bug_count"] = 0
                result["blocker_bug_count"] = 0
                result["regular_bug_count"] = 0

            results.append(result)

        logger.info(f"Analyzed {len(results)} tickets")
        return results

    def export_results(self, results: List[Dict[str, Any]], filename: str = None) -> str:
        """Export results to CSV.

        Args:
            results: Analysis results
            filename: Output filename

        Returns:
            Path to output file
        """
        return self.exporter.export(results, filename)

    def detect_anomalies(
        self, results: List[Dict[str, Any]], time_column: str = "completion_time"
    ) -> List[Dict[str, Any]]:
        """Detect anomalous tickets using IQR (Interquartile Range) method.

        Args:
            results: Analysis results with time columns
            time_column: Column name to analyze for anomalies

        Returns:
            List of anomalies with metadata
        """
        # Extract valid numeric values
        times = []
        for r in results:
            val = r.get(time_column, "N/A")
            if isinstance(val, str) and val.endswith("h"):
                try:
                    times.append(float(val.replace("h", "")))
                except ValueError:
                    continue
            elif isinstance(val, (int, float)):
                times.append(float(val))

        if not times:
            return []

        # Calculate IQR
        q1 = np.percentile(times, 25)
        q3 = np.percentile(times, 75)
        iqr = q3 - q1

        # Define bounds (1.5 * IQR is standard)
        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr

        logger.info(f"Anomaly detection - Q1: {q1:.2f}h, Q3: {q3:.2f}h, IQR: {iqr:.2f}h")
        logger.info(f"Bounds: < {lower_bound:.2f}h or > {upper_bound:.2f}h")

        # Find anomalies
        anomalies = []
        for r in results:
            val = r.get(time_column, "N/A")
            if isinstance(val, str) and val.endswith("h"):
                try:
                    hours = float(val.replace("h", ""))
                    if hours < lower_bound or hours > upper_bound:
                        anomalies.append({
                            **r,
                            "anomaly_type": "too_fast" if hours < lower_bound else "too_slow",
                            "anomaly_deviation": f"{abs(hours - q2 if (q2 := np.median(times)) > 0 else 0):.2f}h from median",
                        })
                except ValueError:
                    continue
            elif isinstance(val, (int, float)):
                hours = float(val)
                if hours < lower_bound or hours > upper_bound:
                    anomalies.append({
                        **r,
                        "anomaly_type": "too_fast" if hours < lower_bound else "too_slow",
                        "anomaly_deviation": f"{abs(hours - (q2 := np.median(times))):.2f}h from median",
                    })

        logger.info(f"Found {len(anomalies)} anomalous tickets")
        return anomalies


def print_summary(results: List[Dict[str, Any]]) -> None:
    """Print summary statistics for analysis results.

    Args:
        results: Analysis results
    """
    if not results:
        return

    print(f"\n{'='*60}")
    print("SUMMARY STATISTICS")
    print(f"{'='*60}")

    # Basic counts
    total = len(results)
    assignees = set(r.get("assignee") for r in results if r.get("assignee") != "Unassigned")
    statuses = set(r.get("status") for r in results)

    print(f"Total tickets analyzed: {total}")
    print(f"Unique assignees: {len(assignees)}")
    print(f"Statuses: {', '.join(sorted(statuses))}")

    # Story points
    story_points = [r.get("story_points") for r in results if r.get("story_points") not in ("N/A", None, "")]
    if story_points:
        try:
            sp_values = [float(sp) for sp in story_points]
            print("\nStory Points:")
            print(f"  - Total: {sum(sp_values)}")
            print(f"  - Average: {sum(sp_values) / len(sp_values):.1f}")
            print(f"  - Min/Max: {min(sp_values)} / {max(sp_values)}")
        except (ValueError, TypeError):
            pass

    # Completion time
    completion_times = []
    for r in results:
        ct = r.get("completion_time", "N/A")
        if isinstance(ct, str) and ct.endswith("h") and ct != "N/A":
            try:
                completion_times.append(float(ct.replace("h", "")))
            except ValueError:
                pass

    if completion_times:
        print("\nCompletion Time (hours):")
        print(f"  - Average: {sum(completion_times) / len(completion_times):.1f}h")
        print(f"  - Median: {sorted(completion_times)[len(completion_times) // 2]:.1f}h")
        print(f"  - Min/Max: {min(completion_times):.1f}h / {max(completion_times):.1f}h")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="jira-time-tracker",
        description="Analyze time spent on JIRA tickets with business hours calculation",
        epilog="""
Examples:
  %(prog)s                              # Use default project from env
  %(prog)s -p MYPROJECT                 # Analyze specific project
  %(prog)s -p PROJ1 PROJ2 PROJ3        # Analyze multiple projects
  %(prog)s --list-sprints               # List all available sprints
  %(prog)s -s "Sprint 1"                # Analyze specific sprint
  %(prog)s --group-by-sprint            # Create separate CSV per sprint
  %(prog)s --group-by-project           # Create separate CSV per project
  %(prog)s --detect-anomalies           # Find anomalous tickets
  %(prog)s -a -o report.csv             # Combine all analyses
  %(prog)s --discover-fields            # List all available JIRA fields
  %(prog)s --mandays-report             # Generate mandays report (proposal vs actuals)
  %(prog)s -m --proposal proposal.json  # Compare actuals with proposal
  %(prog)s -p MYPROJ -s "Sprint 1" -m   # Generate report for specific sprint

Environment Variables:
  JIRA_BASE_URL    JIRA instance URL
  JIRA_EMAIL       User email for authentication
  JIRA_API_TOKEN   API token for authentication
  JIRA_PROJECT     Default project key
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--project",
        "-p",
        metavar="KEY",
        nargs="*",
        help="Project key(s) - can specify multiple (default: from JIRA_PROJECT env var)",
    )
    parser.add_argument(
        "--sprint",
        "-s",
        metavar="NAME",
        help="Sprint name to filter by",
    )
    parser.add_argument(
        "--list-sprints",
        "-l",
        action="store_true",
        help="List all available sprints for the project",
    )
    parser.add_argument(
        "--group-by-sprint",
        "-g",
        action="store_true",
        help="Create separate CSV files per sprint",
    )
    parser.add_argument(
        "--group-by-project",
        action="store_true",
        help="Create separate CSV files per project",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="Output filename (default: auto-generated timestamp)",
    )
    parser.add_argument(
        "--config",
        "-c",
        metavar="PATH",
        help="Path to config file (default: config/default.yaml)",
    )
    parser.add_argument(
        "--detect-anomalies",
        "-a",
        action="store_true",
        help="Detect and report anomalous tickets using IQR method",
    )
    parser.add_argument(
        "--anomaly-column",
        default="completion_time",
        metavar="COL",
        help="Column to analyze for anomalies (default: completion_time)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress output (only show errors)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--discover-fields",
        "-d",
        action="store_true",
        help="Discover all available JIRA fields and their IDs",
    )
    parser.add_argument(
        "--mandays-report",
        "-m",
        action="store_true",
        help="Generate mandays report (proposal vs actuals, KPIs, summaries)",
    )
    parser.add_argument(
        "--proposal",
        metavar="FILE",
        help="JSON file with proposed hours per sprint/role (for mandays report)",
    )
    parser.add_argument(
        "--report-dir",
        default="outputs",
        help="Output directory for mandays report (default: outputs/)",
    )
    parser.add_argument(
        "--discover-bug-fields",
        action="store_true",
        help="Discover field IDs for Bug Label and Severity fields",
    )
    parser.add_argument(
        "--include-bugs",
        action="store_true",
        help="Fetch and count related bugs for each Task (uses issue links API)",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.WARNING if args.quiet else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s" if args.verbose else "%(levelname)s: %(message)s",
    )

    # Print banner
    if not args.quiet:
        print(f"\n{'='*60}")
        print("JIRA Time Tracker")
        print(f"{'='*60}\n")

    # List sprints mode
    if args.list_sprints:
        if not args.quiet:
            print("Fetching sprints...")

        tracker = JiraTimeTracker(config_path=args.config)

        if not tracker.jira.test_connection():
            print("Error: Failed to connect to JIRA. Check your credentials.", file=sys.stderr)
            sys.exit(1)

        project = args.project or tracker.config.jira_project

        # Split comma-separated values for multiple projects
        if isinstance(project, str) and "," in project:
            projects = [p.strip() for p in project.split(",")]
        else:
            projects = [project] if project else []

        # Handle single or multiple projects
        if len(projects) == 1:
            sprints = tracker.jira.get_sprints(projects[0])
            print(f"\n{'='*60}")
            print(f"SPRINTS IN PROJECT: {projects[0]}")
            print(f"{'='*60}\n")
        else:
            # Multiple projects - fetch sprints for each
            all_sprints = []
            for proj in projects:
                proj_sprints = tracker.jira.get_sprints(proj)
                for sprint in proj_sprints:
                    sprint['project'] = proj  # Track which project sprint belongs to
                all_sprints.extend(proj_sprints)

            # Remove duplicates by name across projects
            seen = set()
            sprints = []
            for sprint in all_sprints:
                if sprint['name'] not in seen:
                    seen.add(sprint['name'])
                    sprints.append(sprint)

            print(f"\n{'='*60}")
            print(f"SPRINTS IN PROJECTS: {', '.join(projects)}")
            print(f"{'='*60}\n")

        if not sprints:
            print("No sprints found.")
        else:
            for sprint in sprints:
                state_icon = {"active": "🔄", "closed": "✅", "future": "📅"}.get(sprint.get('state', ''), "•")
                print(f"{state_icon} {sprint['name']}")
                print(f"   State: {sprint.get('state', 'Unknown')}")
                if sprint.get('start_date'):
                    print(f"   Start: {sprint['start_date']}")
                if sprint.get('end_date'):
                    print(f"   End: {sprint['end_date']}")
                print()

        print("Usage:")
        print(f"  python -m src.jira_time_tracker --project {project} --sprint \"<Sprint Name>\"")
        print()

        sys.exit(0)

    # Discover fields mode
    if args.discover_fields:
        if not args.quiet:
            print("Discovering JIRA fields...")

        tracker = JiraTimeTracker(config_path=args.config)

        if not tracker.jira.test_connection():
            print("Error: Failed to connect to JIRA. Check your credentials.", file=sys.stderr)
            sys.exit(1)

        project = args.project or tracker.config.jira_project
        fields = tracker.jira.discover_fields(project)

        print(f"\n{'='*60}")
        print(f"AVAILABLE FIELDS IN PROJECT: {project}")
        print(f"{'='*60}\n")

        # Group fields by type
        custom_fields = {k: v for k, v in fields.items() if k.startswith("customfield_")}
        standard_fields = {k: v for k, v in fields.items() if not k.startswith("customfield_")}

        print("STANDARD FIELDS:")
        for field_id, field_name in sorted(standard_fields.items()):
            print(f"  {field_id:30} : {field_name}")

        print(f"\nCUSTOM FIELDS ({len(custom_fields)} total):")
        for field_id, field_name in sorted(custom_fields.items()):
            print(f"  {field_id:30} : {field_name}")

        # Search for story points
        print(f"\n{'='*60}")
        print("POSSIBLE STORY POINTS FIELDS:")
        print(f"{'='*60}")
        for field_id, field_name in fields.items():
            if "story" in field_name.lower() or "point" in field_name.lower():
                print(f"  {field_id:30} : {field_name}")

        print("\nUpdate config/default.yaml with:")
        print(f'  story_points_field: "<field_id>"')
        print()

        sys.exit(0)

    # Discover bug fields mode
    if args.discover_bug_fields:
        if not args.quiet:
            print("Discovering Bug Label and Severity field IDs...")

        tracker = JiraTimeTracker(config_path=args.config)

        if not tracker.jira.test_connection():
            print("Error: Failed to connect to JIRA. Check your credentials.", file=sys.stderr)
            sys.exit(1)

        print(f"\n{'='*60}")
        print("BUG FIELD DISCOVERY")
        print(f"{'='*60}\n")

        # Search for Bug Label field
        print("Searching for 'Bug Label' field...")
        bug_label_id = tracker.jira.discover_field_id("Bug Label")
        if bug_label_id:
            print(f"  ✓ Found: Bug Label -> {bug_label_id}")
        else:
            print("  ✗ Not found. This field may not exist in your JIRA.")

        # Search for Severity field
        print("\nSearching for 'Severity' field...")
        severity_id = tracker.jira.discover_field_id("Severity")
        if severity_id:
            print(f"  ✓ Found: Severity -> {severity_id}")
        else:
            print("  ✗ Not found. This field may not exist in your JIRA.")

        print(f"\n{'='*60}")
        print("CONFIGURATION")
        print(f"{'='*60}\n")

        if bug_label_id or severity_id:
            print("Add the following to config/default.yaml:")
            print("\nbug_tracking:")
            if bug_label_id:
                print(f'  bug_label_field: "{bug_label_id}"')
            if severity_id:
                print(f'  severity_field: "{severity_id}"')
            print()
        else:
            print("No Bug Label or Severity fields found.")
            print("Your JIRA may use different field names.")
            print("\nTo find all custom fields, run:")
            print("  python -m src.jira_time_tracker --discover-fields")
            print()

        sys.exit(0)

    try:
        # Initialize tracker
        if not args.quiet:
            print("Initializing JIRA connection...")
        tracker = JiraTimeTracker(config_path=args.config)

        # Test connection
        if not tracker.jira.test_connection():
            print("Error: Failed to connect to JIRA. Check your credentials.", file=sys.stderr)
            sys.exit(1)

        if not args.quiet:
            print("Connection successful.")

        # Analyze project(s)
        if args.project:
            projects = args.project
        else:
            # Parse from environment - support comma-separated values
            env_project = tracker.config.jira_project
            if "," in env_project:
                projects = [p.strip() for p in env_project.split(",")]
            else:
                projects = [env_project]

        sprint = args.sprint

        if not args.quiet:
            if len(projects) == 1:
                print(f"\nAnalyzing project: {projects[0]}")
            else:
                print(f"\nAnalyzing projects: {', '.join(projects)}")
            if sprint:
                print(f"Sprint: {sprint}")
            print("Fetching issues...")

        results = tracker.analyze(project=projects, sprint=sprint, include_bugs=args.include_bugs)

        if not args.quiet:
            print(f"Retrieved {len(results)} issues.")
            print("Processing transitions...")

        # Export results
        if not args.quiet:
            print("\nExporting results...")

        # Group by project if requested
        if args.group_by_project:
            # Group results by project and export separately
            project_groups = {}
            for r in results:
                proj_name = r.get("project", "Unknown")
                if proj_name not in project_groups:
                    project_groups[proj_name] = []
                project_groups[proj_name].append(r)

            output_paths = []
            for proj_name, group_results in project_groups.items():
                if args.output:
                    filename = args.output.replace(".csv", f"_{proj_name}.csv")
                else:
                    from datetime import datetime
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"jira_{proj_name}_{timestamp}.csv"

                path = tracker.export_results(group_results, filename=filename)
                output_paths.append(path)

            if not args.quiet:
                print(f"\n{'='*60}")
                print(f"Results saved to {len(output_paths)} files:")
                for path in output_paths:
                    print(f"  - {path}")
                print(f"{'='*60}")

                # Print summary for all groups
                print_summary(results)

        # Group by sprint if requested
        elif args.group_by_sprint and not sprint:
            # Group results by sprint and separate regression tasks
            sprint_groups = {}
            regression_tasks = []

            for r in results:
                issue_type = r.get("issuetype", "")

                # Separate regression tasks from regular issues
                if issue_type == "QA Regression Task":
                    regression_tasks.append(r)
                else:
                    sprint_name = r.get("sprint", "No Sprint")
                    if sprint_name not in sprint_groups:
                        sprint_groups[sprint_name] = []
                    sprint_groups[sprint_name].append(r)

            output_paths = []

            # Export regression tasks to a single file
            if regression_tasks:
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                if args.output:
                    filename = args.output.replace(".csv", "_regression.csv")
                else:
                    filename = f"regression_{timestamp}.csv"

                path = tracker.export_results(regression_tasks, filename=filename)
                output_paths.append(path)

            # Export regular issues per sprint
            for sprint_name, group_results in sprint_groups.items():
                # Sanitize sprint name for filename
                safe_sprint_name = sprint_name.replace(" ", "_").replace("/", "-")
                if args.output:
                    filename = args.output.replace(".csv", f"_{safe_sprint_name}.csv")
                else:
                    from datetime import datetime
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"jira_{safe_sprint_name}_{timestamp}.csv"

                path = tracker.export_results(group_results, filename=filename)
                output_paths.append(path)

            if not args.quiet:
                print(f"\n{'='*60}")
                print(f"Results saved to {len(output_paths)} files:")
                for path in output_paths:
                    print(f"  - {path}")
                print(f"{'='*60}")

                # Print summary for all groups
                print_summary(results)
        else:
            # Single export
            output_path = tracker.export_results(results, filename=args.output)

            if not args.quiet:
                print(f"\n{'='*60}")
                print(f"Results saved to: {output_path}")
                print(f"{'='*60}")

                # Print summary statistics
                print_summary(results)

        # Anomaly detection
        if args.detect_anomalies:
            if not args.quiet:
                print("\nRunning anomaly detection...")

            anomalies = tracker.detect_anomalies(results, args.anomaly_column)

            if anomalies:
                # Export anomalies to separate file
                anomaly_filename = args.output.replace(".csv", "_anomalies.csv") if args.output else None
                anomaly_path = tracker.export_results(anomalies, filename=anomaly_filename)

                print(f"\n{'='*60}")
                print(f"ANOMALY DETECTION ({args.anomaly_column})")
                print(f"{'='*60}")
                print(f"Found {len(anomalies)} anomalous tickets:")

                too_fast = [a for a in anomalies if a.get("anomaly_type") == "too_fast"]
                too_slow = [a for a in anomalies if a.get("anomaly_type") == "too_slow"]

                print(f"  - Too fast: {len(too_fast)} tickets")
                print(f"  - Too slow: {len(too_slow)} tickets")
                print(f"\nAnomalies saved to: {anomaly_path}")

                # Show top 5 anomalies
                print("\nTop 5 slowest tickets:")
                for a in sorted(too_slow, key=lambda x: float(x[args.anomaly_column].replace("h", "")), reverse=True)[:5]:
                    print(f"  - {a['key']}: {a[args.anomaly_column]} ({a.get('anomaly_deviation', 'N/A')})")

                if too_fast:
                    print("\nTop 5 fastest tickets:")
                    for a in sorted(too_fast, key=lambda x: float(x[args.anomaly_column].replace("h", "")))[:5]:
                        print(f"  - {a['key']}: {a[args.anomaly_column]} ({a.get('anomaly_deviation', 'N/A')})")
            else:
                print("\nNo anomalies detected.")

        # Mandays report generation
        if args.mandays_report:
            if not args.quiet:
                print("\nGenerating mandays report...")

            try:
                txt_path, csv_path = tracker.mandays_reporter.generate(
                    results=results,
                    proposal_path=args.proposal,
                    output_dir=args.report_dir
                )

                if not args.quiet:
                    print(f"\n{'='*60}")
                    print(f"MANDAYS REPORT GENERATED")
                    print(f"{'='*60}")
                    print(f"Text report: {txt_path}")
                    print(f"CSV summary: {csv_path}")

                    # Show preview of the report
                    with open(txt_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        preview_lines = content.split("\n")[:50]
                        print(f"\n{'='*60}")
                        print("REPORT PREVIEW (first 50 lines)")
                        print(f"{'='*60}")
                        print("\n".join(preview_lines))
                        content_lines = content.split("\n")
                        if len(content_lines) > 50:
                            print(f"\n... ({len(content_lines) - 50} more lines)")
                        print(f"{'='*60}\n")

            except FileNotFoundError as e:
                print(f"\nError: {e}", file=sys.stderr)
                sys.exit(1)
            except Exception as e:
                print(f"\nError generating mandays report: {e}", file=sys.stderr)
                if args.verbose:
                    import traceback
                    traceback.print_exc()
                sys.exit(1)

        if not args.quiet:
            print(f"\n{'='*60}")
            print("Analysis complete!")
            print(f"{'='*60}\n")

    except KeyboardInterrupt:
        print("\nAnalysis interrupted by user.", file=sys.stderr)
        sys.exit(130)
    except ConnectionError as e:
        print(f"\nConnection error: {e}", file=sys.stderr)
        print("Please check your JIRA credentials and network connection.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        else:
            print(f"\nError: {e}", file=sys.stderr)
            print("Use --verbose for more details.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
