"""JIRA API client wrapper for fetching issues and history."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from jira import JIRA, JIRAError

logger = logging.getLogger(__name__)


class JiraClient:
    """Wrapper for JIRA API operations."""

    def __init__(self, base_url: str, email: str, api_token: str):
        """Initialize JIRA client.

        Args:
            base_url: JIRA instance base URL
            email: User email for authentication
            api_token: API token for authentication
        """
        try:
            self.client = JIRA(
                server=base_url,
                basic_auth=(email, api_token),
                options={"verify": True},
            )
            logger.info(f"Connected to JIRA at {base_url}")
        except JIRAError as e:
            raise ConnectionError(f"Failed to connect to JIRA: {e}") from e

    def get_issues(self, project, fields: Optional[List[str]] = None, max_results: int = 100, sprint: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch all issues from a project or multiple projects.

        Args:
            project: Project key (string) or list of project keys
            fields: List of fields to fetch
            max_results: Maximum results per page
            sprint: Sprint name to filter by

        Returns:
            List of issue dictionaries
        """
        if fields is None:
            fields = [
                "key",
                "summary",
                "status",
                "assignee",
                "created",
                "updated",
                "*all",  # Fetch all fields to find custom fields
            ]
        elif "*all" not in fields:
            # Add *all if not present to ensure we get all custom fields
            fields.append("*all")

        # Build JQL query - support single project or multiple projects
        if isinstance(project, list):
            project_list = ", ".join(f'"{p}"' for p in project)
            jql = f"project in ({project_list})"
        else:
            jql = f"project = {project}"

        if sprint:
            # Support both single sprint and multiple sprints (comma-separated)
            if "," in sprint:
                jql += f' AND sprint in ({sprint})'
            else:
                jql += f' AND sprint = "{sprint}"'

        logger.info(f"Fetching issues with JQL: {jql}")
        logger.info(f"Requesting fields: {fields}")

        try:
            issues = []
            next_page_token = None

            while True:
                result = self.client.enhanced_search_issues(
                    jql_str=jql,
                    nextPageToken=next_page_token,
                    maxResults=max_results,
                    fields=fields,
                )
                logger.info(f"Retrieved {len(result)} issues")
                issues.extend(result.iterable)

                # Check if there are more pages
                next_page_token = result.nextPageToken
                if not next_page_token:
                    break

            logger.info(f"Retrieved {len(issues)} issues")
            return issues

        except JIRAError as e:
            logger.error(f"Failed to fetch issues: {e}")
            raise

    def get_issue_history(self, issue_key: str) -> List[Dict[str, Any]]:
        """Get status transition history for an issue.

        Args:
            issue_key: Issue key

        Returns:
            List of history entries with timestamp and status changes
        """
        try:
            issue = self.client.issue(issue_key, expand="changelog")
            history = []

            for item in issue.changelog.histories:
                created = datetime.fromisoformat(item.created.replace("Z", "+00:00"))

                for field in item.items:
                    if field.field == "status":
                        history.append(
                            {
                                "key": issue_key,
                                "timestamp": created,
                                "from": field.fromString,
                                "to": field.toString,
                                "name": item.author.displayName,
                            }
                        )

            return sorted(history, key=lambda x: x["timestamp"])

        except JIRAError as e:
            logger.error(f"Failed to fetch history for {issue_key}: {e}")
            return []

    def test_connection(self) -> bool:
        """Test connection to JIRA.

        Returns:
            True if connection successful
        """
        try:
            self.client.session()
            return True
        except JIRAError:
            return False

    def discover_fields(self, project: str, limit: int = 1) -> Dict[str, str]:
        """Discover all available fields in a project.

        Args:
            project: Project key
            limit: Number of issues to inspect (default: 1)

        Returns:
            Dictionary mapping field IDs to display names
        """
        logger.info(f"Discovering fields for project: {project}")

        try:
            fix_project = project
            if len(project) > 0:
                fix_project = project[0]

            # Fetch a single issue with all fields
            issues = self.client.search_issues(
                f"project={fix_project}",
                maxResults=limit,
                fields=["*all"],
            )

            if not issues:
                return {}

            issue = issues[0]
            fields = {}

            # Get field metadata
            for field in self.client.fields():
                field_id = field["id"]
                field_name = field["name"]
                fields[field_id] = field_name

                # Check if this field has a value on the sample issue
                if hasattr(issue.fields, field_id):
                    value = getattr(issue.fields, field_id, None)
                    if value is not None:
                        logger.debug(f"  {field_id} ({field_name}): {type(value).__name__}")

            logger.info(f"Discovered {len(fields)} fields")
            return fields

        except JIRAError as e:
            logger.error(f"Failed to discover fields: {e}")
            return {}

    def discover_field_id(self, field_name: str) -> Optional[str]:
        """Find the custom field ID for a given field display name.

        Args:
            field_name: Display name (e.g., "Bug Label", "Severity")

        Returns:
            Field ID (e.g., "customfield_10020") or None
        """
        logger.info(f"Searching for field: '{field_name}'")

        try:
            all_fields = self.client.fields()
            for field in all_fields:
                if field["name"].lower() == field_name.lower():
                    logger.info(f"Found field '{field_name}': ID = {field['id']}")
                    return field["id"]
            logger.warning(f"Field '{field_name}' not found")
            return None

        except JIRAError as e:
            logger.error(f"Failed to search for field '{field_name}': {e}")
            return None

    def get_outward_links_with_details(
        self,
        issue_key: str,
        link_types: List[str] = None,
        bug_label_field: Optional[str] = None,
        severity_field: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get linked bugs with their field details.

        Handles both outward links (e.g., "relates to") and inward links (e.g., "blocks").

        For "relates to": Bug is in outwardIssue (Task relates to Bug)
        For "blocks": Bug is in inwardIssue (Bug blocks Task, so Task is "blocked by" Bug)

        Args:
            issue_key: The issue to query (typically a Task)
            link_types: List of link type names (default: ["Relates", "Blocks"])
            bug_label_field: Custom field ID for Bug Label
            severity_field: Custom field ID for Severity

        Returns:
            List of dicts: [{key, summary, status, bug_label, severity}, ...]
        """
        if link_types is None:
            link_types = ["Relates", "Blocks"]

        try:
            issue = self.client.issue(issue_key, expand="issuelinks")
            linked_bugs = []
            seen_bug_keys = set()  # Track to avoid duplicates

            # Build fields list for bug fetching
            bug_fields = ["issuetype", "summary", "status"]
            if bug_label_field:
                bug_fields.append(bug_label_field)
            if severity_field:
                bug_fields.append(severity_field)

            # Normalize link types to lowercase for comparison
            link_types_lower = [lt.lower() for lt in link_types]

            for link in getattr(issue.fields, "issuelinks", []):
                if not hasattr(link, "type"):
                    continue

                link_name = link.type.name.lower()
                if link_name not in link_types_lower:
                    continue

                # Check outward issue (for "relates to" type links)
                if hasattr(link, "outwardIssue"):
                    bug_key = link.outwardIssue.key
                    if bug_key in seen_bug_keys:
                        continue

                    try:
                        bug = self.client.issue(bug_key, fields=bug_fields)

                        # Only include if it's actually a Bug
                        if hasattr(bug.fields, "issuetype") and bug.fields.issuetype.name == "Bug":
                            bug_data = {
                                "key": bug_key,
                                "summary": getattr(bug.fields, "summary", ""),
                                "status": getattr(bug.fields.status, "name", ""),
                            }

                            # Get custom field values
                            bug_data["bug_label"] = self._get_field_value(bug, bug_label_field) if bug_label_field else None
                            bug_data["severity"] = self._get_field_value(bug, severity_field) if severity_field else None

                            linked_bugs.append(bug_data)
                            seen_bug_keys.add(bug_key)
                            logger.debug(f"Found outward bug: {bug_key} via {link.type.name}")

                    except JIRAError as e:
                        logger.warning(f"Failed to fetch details for bug {bug_key}: {e}")
                        continue

                # Check inward issue (for "blocks" type links - Bug blocks Task)
                if hasattr(link, "inwardIssue"):
                    bug_key = link.inwardIssue.key
                    if bug_key in seen_bug_keys:
                        continue

                    try:
                        bug = self.client.issue(bug_key, fields=bug_fields)

                        # Only include if it's actually a Bug
                        if hasattr(bug.fields, "issuetype") and bug.fields.issuetype.name == "Bug":
                            bug_data = {
                                "key": bug_key,
                                "summary": getattr(bug.fields, "summary", ""),
                                "status": getattr(bug.fields.status, "name", ""),
                            }

                            # Get custom field values
                            bug_data["bug_label"] = self._get_field_value(bug, bug_label_field) if bug_label_field else None
                            bug_data["severity"] = self._get_field_value(bug, severity_field) if severity_field else None

                            linked_bugs.append(bug_data)
                            seen_bug_keys.add(bug_key)
                            logger.debug(f"Found inward bug: {bug_key} via {link.type.name}")

                    except JIRAError as e:
                        logger.warning(f"Failed to fetch details for bug {bug_key}: {e}")
                        continue

            logger.info(f"Found {len(linked_bugs)} bugs for {issue_key}")
            return linked_bugs

        except JIRAError as e:
            logger.error(f"Failed to fetch links for {issue_key}: {e}")
            return []

    def _get_field_value(self, issue, field_id: str) -> Any:
        """Get value of a field from an issue.

        Args:
            issue: JIRA issue object
            field_id: Field ID to retrieve

        Returns:
            Field value or None
        """
        try:
            value = getattr(issue.fields, field_id, None)
            if value is None:
                return None

            # Handle different field value types
            if hasattr(value, "name"):
                return value.name
            elif hasattr(value, "value"):
                return value.value
            elif isinstance(value, list):
                return ", ".join(str(v) for v in value)
            else:
                return str(value) if value != "" else None

        except Exception as e:
            logger.debug(f"Error getting field {field_id}: {e}")
            return None

    @staticmethod
    def extract_sprint_info(sprint_field: Any) -> Optional[str]:
        """Extract sprint name from JIRA sprint field.

        Args:
            sprint_field: The sprint field value (can be complex structure)

        Returns:
            Sprint name or None if no sprint found
        """
        if sprint_field is None:
            return None

        # JIRA sprint field can be:
        # 1. A single sprint object (dict-like with attributes)
        # 2. A list of sprints (historical)
        # 3. A string (sprint name)

        try:
            # If it's a list, get the most recent (last) sprint
            if isinstance(sprint_field, list):
                if not sprint_field:
                    return None
                # Get the last sprint (most recently assigned)
                sprint_field = sprint_field[-1]

            # Try to get name attribute (JIRA Resource object)
            if hasattr(sprint_field, 'name'):
                return sprint_field.name

            # Try dict access
            if isinstance(sprint_field, dict):
                return sprint_field.get('name')

            # Try string representation
            if isinstance(sprint_field, str):
                return sprint_field

            logger.debug(f"Unable to extract sprint name from: {type(sprint_field)}")
            return None

        except Exception as e:
            logger.debug(f"Error extracting sprint info: {e}")
            return None

    def get_sprints(self, project: str) -> List[Dict[str, Any]]:
        """Get all sprints for a project.

        Args:
            project: Project key

        Returns:
            List of sprint information dictionaries
        """
        logger.info(f"Fetching sprints for project: {project}")

        try:
            # Get all boards for the project
            boards = self.client.boards(projectKeyOrID=project)

            sprints = []
            for board in boards:
                try:
                    board_sprints = self.client.sprints(board.id, state='active,closed,future')
                    for sprint in board_sprints:
                        sprints.append({
                            'id': sprint.id,
                            'name': sprint.name,
                            'state': sprint.state,
                            'start_date': getattr(sprint, 'startDate', None),
                            'end_date': getattr(sprint, 'endDate', None),
                        })
                except JIRAError:
                    # Some boards might not have sprints
                    continue

            # Remove duplicates by name
            seen = set()
            unique_sprints = []
            for sprint in sprints:
                if sprint['name'] not in seen:
                    seen.add(sprint['name'])
                    unique_sprints.append(sprint)

            logger.info(f"Found {len(unique_sprints)} sprints")
            return unique_sprints

        except JIRAError as e:
            logger.error(f"Failed to fetch sprints: {e}")
            return []

    def filter_sprints_by_date_range(
        self, project: str, start_date: str, end_date: str
    ) -> List[str]:
        """Get sprint names that start within the specified date range.

        Args:
            project: Project key
            start_date: Start date (YYYY-MM-DD format)
            end_date: End date (YYYY-MM-DD format)

        Returns:
            List of sprint names that start within the date range
        """
        from datetime import datetime as dt

        logger.info(f"Filtering sprints from {start_date} to {end_date}")

        try:
            start_dt = dt.strptime(start_date, "%Y-%m-%d")
            end_dt = dt.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

            all_sprints = self.get_sprints(project)
            filtered_sprints = []

            for sprint in all_sprints:
                sprint_start = sprint.get('start_date')
                logger.info(f"Processing sprint: {sprint['name']} (starts {sprint_start})")
                if sprint_start:
                    try:
                        # Parse sprint start date (handle ISO format with timezone)
                        if 'T' in sprint_start:
                            sprint_start_dt = dt.fromisoformat(sprint_start.replace('Z', '+00:00'))
                            # Strip timezone info for comparison (compare dates only)
                            sprint_start_dt = sprint_start_dt.replace(tzinfo=None)
                        else:
                            sprint_start_dt = dt.strptime(sprint_start, "%Y-%m-%d")

                        # Check if sprint starts within the date range
                        if start_dt <= sprint_start_dt <= end_dt:
                            filtered_sprints.append(sprint['name'])
                            logger.debug(f"Including sprint: {sprint['name']} (starts {sprint_start})")

                    except ValueError as e:
                        logger.warning(f"Could not parse date '{sprint_start}' for sprint '{sprint['name']}': {e}")
                        continue

            logger.info(f"Found {len(filtered_sprints)} sprints within date range")
            return filtered_sprints

        except ValueError as e:
            logger.error(f"Invalid date format in config: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to filter sprints by date: {e}")
            return []

        except JIRAError as e:
            logger.error(f"Failed to filter sprints: {e}")
            return []
