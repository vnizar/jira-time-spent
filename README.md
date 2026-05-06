# JIRA Time Tracker

A Python CLI tool to analyze time spent on JIRA tickets, tracking status transitions with business hours calculation.

## Features

- **Status Transition Tracking**: Track time between any two statuses (e.g., "DEV IN PROGRESS" → "DONE")
- **Business Hours Calculation**: Calculate working hours only (08:00-17:00 Jakarta time, excludes weekends/holidays)
- **Assignee Tracking**: See who was assigned to each ticket
- **Anomaly Detection**: Identify tickets that were unusually fast or slow using statistical methods
- **CSV Export**: Export results for further analysis in Excel or other tools

## Installation

1. Clone this repository
2. Install dependencies:

```bash
pip install -r requirements.txt
```

Or use a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Installation

1. Clone this repository
2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

1. Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

2. Edit `.env` with your JIRA credentials:

```env
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=your-email@example.com
JIRA_API_TOKEN=your-api-token-here
JIRA_PROJECT=YOUR_PROJECT_KEY
```

For multiple projects, use comma-separated values:

```env
JIRA_PROJECT=DEA,DEV,QA
```

3. (Optional) Configure status transitions and working hours in `config/default.yaml`

## Usage

Run analysis on your project:

```bash
python -m src.jira_time_tracker
```

Or with specific project:

```bash
python -m src.jira_time_tracker --project MYPROJECT
```

Analyze multiple projects:

```bash
python -m src.jira_time_tracker --project PROJ1 PROJ2 PROJ3
```

Group by project (separate CSV files):

```bash
python -m src.jira_time_tracker --project PROJ1 PROJ2 --group-by-project
```

Combine multiple projects with sprint filtering:

```bash
python -m src.jira_time_tracker --project DEA DEV --sprint "Sprint 1"
```

Specify output filename:

```bash
python -m src.jira_time_tracker --output my_report.csv
```

List all available sprints:

```bash
python -m src.jira_time_tracker --list-sprints
```

Filter by specific sprint:

```bash
python -m src.jira_time_tracker --sprint "Sprint 1"
```

Group output by sprint (separate CSV per sprint):

```bash
python -m src.jira_time_tracker --group-by-sprint
```

Enable anomaly detection:

Enable anomaly detection:

```bash
python -m src.jira_time_tracker --detect-anomalies
```

Analyze specific column for anomalies:

```bash
python -m src.jira_time_tracker --detect-anomalies --anomaly-column qa_ready_time
```

Combine multiple analyses:

```bash
python -m src.jira_time_tracker --detect-anomalies --output my_report.csv
```

Quiet mode (minimal output):

```bash
python -m src.jira_time_tracker -q
```

Verbose mode (detailed logging):

```bash
python -m src.jira_time_tracker -v
```

## Configuration Options

### Status Transitions

Configure which transitions to track in `config/default.yaml`:

```yaml
status_transitions:
  - start_status: "DEV IN PROGRESS"
    end_statuses:
      - "QA PASS"
      - "DONE"
    name: "completion_time"
  - start_status: "DEV IN PROGRESS"
    end_statuses:
      - "READY TO QA"
    name: "qa_ready_time"
  - start_status: "CODE REVIEW IN PROGRESS"
    end_statuses:
      - "READY TO QA"
      - "CODE REVIEW DONE"
    name: "code_review_time"
  - start_status: "READY TO CODE REVIEW"
    end_statuses:
      - "CODE REVIEW IN PROGRESS"
    name: "code_review_waiting_time"
```

**How it works:**
- Timer **starts** when ticket ENTERS `start_status` (e.g., first time it becomes "DEV IN PROGRESS")
- Timer **stops** when ticket ENTERS any of `end_statuses` (e.g., becomes "QA PASS" or "DONE")
- Timer continues (doesn't reset) if status changes back and forth between start and end

### Sprint Configuration

Configure the sprint field in `config/default.yaml`:

```yaml
sprint_field: "customfield_10020"  # Your JIRA sprint field ID
```

**Finding Your Sprint Field ID:**

Run the discover command to find your sprint field:

```bash
python -m src.jira_time_tracker --discover-fields
```

Look for fields with "sprint" or "Sprint" in the name.

**Sprint Filtering:**

```bash
# List all available sprints
python -m src.jira_time_tracker --list-sprints

# Analyze a specific sprint
python -m src.jira_time_tracker --sprint "Sprint 1"

# Create separate CSV files per sprint
python -m src.jira_time_tracker --group-by-sprint
```

### Working Hours

Configure business hours in `config/default.yaml`:

```yaml
working_hours:
  start: "08:00"
  end: "17:00"
  timezone: "Asia/Jakarta"
  weekends: [Saturday, Sunday]
```

### Additional Custom Fields

Add custom fields to fetch in `config/default.yaml`:

```yaml
additional_fields:
  story_point_dev: "customfield_10143"  # Development story points
  story_point_qa: "customfield_10144"   # QA story points
  qa_tester: "customfield_10362"        # QA tester assigned

# QA Tester fallback configuration
qa_tester_fallback:
  enabled: true
  detect_status: "QA PASS"  # Look for user who moved ticket TO this status
```

**Finding Field IDs:**

Run the discover command to find the correct field IDs:

```bash
python -m src.jira_time_tracker --discover-fields
```

This will list all available fields. Look for your custom fields and copy their IDs (e.g., `customfield_10016`).

**Handling Different Field Types:**

- **Number fields** (e.g., story points): Displayed as-is
- **User fields** (e.g., assignee, tester): Displays the user's display name
- **Empty fields**: Show as "N/A"

**QA Tester Fallback:**

If the `qa_tester` field is often empty, you can enable fallback to detect the QA tester from the ticket's status transition history:

```yaml
qa_tester_fallback:
  enabled: true
  detect_status: "QA IN PROGRESS"  # Look for user who moved ticket TO this status
```

When enabled:
- If `qa_tester` has a value → use that value
- If `qa_tester` is empty → find the user who moved the ticket to "QA IN PROGRESS"
- If no matching transition found → show "N/A"

### Date Range Filtering

Filter analysis to only include sprints that start within a specific date range:

```yaml
date_range:
  enabled: true   # Set to true to enable date range filtering
  start_date: "2025-01-01"  # Only include sprints starting on or after this date
  end_date: "2025-01-31"    # Only include sprints starting on or before this date
```

**How it works:**
- When enabled, the tool fetches all sprints for the project
- Filters sprints by their `start_date` field
- Only includes tickets from sprints that START within the specified date range
- Useful for analyzing specific time periods (e.g., Q1, Q2, monthly reports)

**Example usage:**

```bash
# Analyze Q1 2025 sprints
# 1. Enable date_range in config/default.yaml
# 2. Set start_date: "2025-01-01" and end_date: "2025-03-31"
python -m src.jira_time_tracker
```

**Notes:**
- Date range filtering is applied before fetching tickets
- Overrides `--sprint` CLI argument if both are used
- Requires sprints to have `startDate` field set in JIRA

### Holidays

Add company holidays in `config/holidays.yaml`:

```yaml
holidays:
  - date: "2025-01-01"
    name: "New Year's Day"
  - date: "2025-12-25"
    name: "Christmas Day"
```

## Output

Results are saved to `outputs/` directory with columns:

- `key`: JIRA ticket key
- `summary`: Ticket summary
- `status`: Current status
- `assignee`: Assigned user (or "Unassigned")
- `story_points`: Story points estimate
- `completion_time`: Hours from DEV IN PROGRESS to DONE/QA PASS
- `qa_ready_time`: Hours from DEV IN PROGRESS to READY TO QA
- `sprint`: Sprint name (or "No Sprint" if unassigned)

## Understanding the Output

### Main Output Columns

| Column | Description |
|--------|-------------|
| `key` | JIRA ticket identifier (e.g., DEA-123) |
| `project` | Project key (extracted from ticket key) |
| `summary` | Ticket title/summary |
| `status` | Current status of the ticket |
| `assignee` | Assigned user's display name (or "Unassigned") |
| `sprint` | Sprint name (or "No Sprint" if unassigned) |
| `story_points` | Story point estimate from JIRA |
| `story_point_dev` | Development story points (custom field) |
| `story_point_qa` | QA story points (custom field) |
| `qa_tester` | QA tester assigned to the ticket (custom field) |
| `completion_time` | Business hours from DEV IN PROGRESS to QA PASS/DONE |
| `qa_ready_time` | Business hours from DEV IN PROGRESS to READY TO QA |
| `code_review_time` | Business hours from CODE REVIEW IN PROGRESS to READY TO QA/CODE REVIEW DONE |
| `code_review_waiting_time` | Business hours from READY TO CODE REVIEW to CODE REVIEW IN PROGRESS |

## Troubleshooting

### "Failed to connect to JIRA"
- Verify your credentials in `.env`
- Check that JIRA_BASE_URL is correct (include https://)
- Ensure your API token is valid (regenerate in JIRA if needed)

### "No module named 'yaml'" or similar errors
- Make sure you installed the requirements: `pip install -r requirements.txt`
- If using a virtual environment, ensure it's activated

### "The search API is deprecated" error
- This tool uses the latest `enhanced_search_issues` API
- Ensure you have `jira>=3.8.0` installed

### Empty or incomplete results
- Check that the project key is correct
- Verify you have permission to view the project
- Some tickets may not have the required status transitions

## How Business Hours Are Calculated

The tool calculates only business hours between status transitions:

1. **Working Hours**: 08:00 - 17:00 (configurable in `config/default.yaml`)
2. **Timezone**: Asia/Jakarta (configurable)
3. **Weekends**: Saturday and Sunday are excluded
4. **Holidays**: Dates in `config/holidays.yaml` are excluded

Example: If a ticket moves from "DEV IN PROGRESS" at 16:00 on Friday to "DONE" at 09:00 on Monday:
- Friday: 1 hour (16:00-17:00)
- Saturday: 0 hours (weekend)
- Sunday: 0 hours (weekend)
- Monday: 1 hour (08:00-09:00)
- **Total**: 2 business hours

## Workflow Example

Your typical workflow might be:
```
TO DO → DEV IN PROGRESS → READY TO CODE REVIEW → CODE REVIEW IN PROGRESS
     → READY TO QA → QA IN PROGRESS → QA PASS / QA FAILED → REOPEN → DONE
```

With the default configuration:
- **completion_time**: DEV IN PROGRESS → QA PASS or DONE
- **qa_ready_time**: DEV IN PROGRESS → READY TO QA
- **code_review_time**: CODE REVIEW IN PROGRESS → READY TO QA or CODE REVIEW DONE
- **code_review_waiting_time**: READY TO CODE REVIEW → CODE REVIEW IN PROGRESS

The timer starts when the ticket first enters the start status and stops when it first reaches the end status. If a ticket goes back and forth (e.g., QA FAILED → REOPEN → DEV IN PROGRESS → QA PASS), the original start time is preserved.

## Configuration

### Status Transitions

Edit `config/default.yaml` to track different transitions:

```yaml
status_transitions:
  - from: "TODO"
    to: "IN PROGRESS"
    name: "start_time"
  - from: "IN PROGRESS"
    to: "DONE"
    name: "completion_time"
```

### Working Hours

Edit in `config/default.yaml`:

```yaml
working_hours:
  start: "09:00"    # Start of business day
  end: "18:00"      # End of business day
  timezone: "UTC"   # Timezone for calculations
  weekends: [Saturday, Sunday]
```

### Additional Custom Fields

Add custom fields to fetch in `config/default.yaml`:

```yaml
additional_fields:
  story_point_dev: "customfield_10143"  # Development story points
  story_point_qa: "customfield_10144"   # QA story points
  qa_tester: "customfield_10362"        # QA tester assigned

# QA Tester fallback configuration
qa_tester_fallback:
  enabled: true
  detect_status: "QA PASS"  # Look for user who moved ticket TO this status
```

**Finding Field IDs:**

Run the discover command to find the correct field IDs:

```bash
python -m src.jira_time_tracker --discover-fields
```

This will list all available fields. Look for your custom fields and copy their IDs (e.g., `customfield_10016`).

**Handling Different Field Types:**

- **Number fields** (e.g., story points): Displayed as-is
- **User fields** (e.g., assignee, tester): Displays the user's display name
- **Empty fields**: Show as "N/A"

**QA Tester Fallback:**

If the `qa_tester` field is often empty, you can enable fallback to detect the QA tester from the ticket's status transition history:

```yaml
qa_tester_fallback:
  enabled: true
  detect_status: "QA IN PROGRESS"  # Look for user who moved ticket TO this status
```

When enabled:
- If `qa_tester` has a value → use that value
- If `qa_tester` is empty → find the user who moved the ticket to "QA IN PROGRESS"
- If no matching transition found → show "N/A"

### Holidays

Add company holidays in `config/holidays.yaml`:

```yaml
holidays:
  - date: "2025-01-01"
    name: "New Year's Day"
  - date: "2025-12-25"
    name: "Christmas Day"
```

## Requirements

- Python 3.9+
- JIRA API token
- Project access in JIRA

## License

MIT
