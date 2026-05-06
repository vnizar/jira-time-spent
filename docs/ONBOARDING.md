# JIRA Time Tracker - Onboarding Guide

## Project Overview

**JIRA Time Tracker** is a Python CLI tool that analyzes time spent on JIRA tickets with status transition tracking and business hours calculation.

- **Languages**: Python, YAML, JSON, Markdown
- **Frameworks**: JIRA Cloud API
- **Purpose**: Track and report how long tickets spend in each status, calculating only business hours (excluding weekends and holidays)

## Architecture Layers

### 1. Configuration Layer
Handles YAML configs and environment variables.
- `config/default.yaml` - Main configuration (status transitions, working hours, JIRA fields)
- `config/holidays.yaml` - Company holidays to exclude from business hours
- `src/config_manager.py` - ConfigManager class that loads configs and env variables

### 2. JIRA Integration Layer
Wraps the JIRA Cloud API with pagination and authentication.
- `src/jira_client.py` - JiraClient class with methods for fetching issues, history, and sprints

### 3. Business Logic Layer
Core analysis and time calculation engine.
- `src/time_calculator.py` - TimeCalculator class for business hours computation
- `src/jira_time_tracker.py` - JiraTimeTracker orchestration class

### 4. Data Export Layer
CSV export functionality.
- `src/csv_exporter.py` - CsvExporter class for writing results to files

### 5. Documentation Layer
Project documentation.
- `README.md` - Comprehensive user guide

## Key Concepts

### Status Transition Tracking
The tool tracks how long issues spend in each status by analyzing JIRA changelog history. Status transitions are configured in `config/default.yaml`:

```yaml
status_transitions:
  - name: "In Progress"
    start_status: "In Progress"
    end_status: ["Done", "Closed", "Rejected"]
```

### Business Hours Calculation
TimeCalculator computes elapsed business hours only during working hours (default 08:00-17:00 Jakarta time), excluding:
- Weekends (Saturday, Sunday)
- Holidays from `config/holidays.yaml`

### Regression Task Separation
When using `-g` (group-by-sprint), issues with type "QA Regression Task" are automatically separated into a single `regression_TIMESTAMP.csv` file, while regular issues are grouped by sprint.

## Guided Tour

### Step 1: Project Overview
Start with `README.md` to understand the tool's purpose, installation, and usage.

### Step 2: Configuration Setup
Review `config/default.yaml` to understand:
- Status transitions to track
- Working hours configuration
- JIRA field mappings
- Additional custom fields

Then examine `src/config_manager.py` to see how configs are loaded.

### Step 3: JIRA API Integration
Explore `src/jira_client.py`:
- `get_issues()` - Fetches tickets with pagination
- `get_issue_history()` - Retrieves status transitions
- Sprint management functions

### Step 4: Time Calculation Engine
Study `src/time_calculator.py`:
- `calculate()` - Main method for computing business hours
- Handles status entry/exit tracking
- Excludes weekends and holidays

### Step 5: Main Analysis Orchestration
Examine `src/jira_time_tracker.py`:
- `JiraTimeTracker.analyze()` - Core orchestration logic
- Coordinates JiraClient, TimeCalculator, and CsvExporter
- Handles regression task separation

### Step 6: Data Export
Review `src/csv_exporter.py` to see how results are written to CSV files.

## File Map

### Configuration
| File | Purpose |
|------|---------|
| `config/default.yaml` | Status transitions, working hours, JIRA fields, analysis settings |
| `config/holidays.yaml` | Holiday dates to exclude from business hours |
| `src/config_manager.py` | Loads YAML configs and environment variables |

### JIRA Integration
| File | Purpose |
|------|---------|
| `src/jira_client.py` | JIRA API wrapper with pagination support |

### Business Logic
| File | Purpose |
|------|---------|
| `src/time_calculator.py` | Business hours calculator |
| `src/jira_time_tracker.py` | Main CLI orchestration |

### Data Export
| File | Purpose |
|------|---------|
| `src/csv_exporter.py` | CSV export functionality |

### Documentation
| File | Purpose |
|------|---------|
| `README.md` | User guide with installation and usage |

## Complexity Hotspots

Based on the codebase analysis:

| Module | Complexity | Notes |
|--------|------------|-------|
| `src/jira_client.py` | Moderate | Handles pagination, token-based API calls, and sprint parsing |
| `src/time_calculator.py` | Moderate | Business hours logic with timezone handling |
| `src/jira_time_tracker.py` | Moderate | Orchestrates multiple components and handles regression separation |

## Quick Start

### Installation
```bash
# Clone the repository
git clone <repository-url>
cd JIRA-time-spent

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your JIRA credentials
```

### Configuration
Edit `config/default.yaml`:
- Set working hours for your timezone
- Configure status transitions to track
- Add custom fields as needed

Add holidays to `config/holidays.yaml`.

### Usage
```bash
# Analyze all issues in a project
python -m src.jira_time_tracker --project MYPROJECT

# Group by sprint
python -m src.jira_time_tracker --project MYPROJECT --group-by-sprint

# Filter by sprint
python -m src.jira_time_tracker --project MYPROJECT --sprint "Sprint 1"

# Date range filtering
python -m src.jira_time_tracker --project MYPROJECT --after 2025-01-01 --before 2025-12-31
```

### Output Files
Results are saved to `outputs/` directory:
- `jira_analysis_TIMESTAMP.csv` - Default output (all issues)
- `sprint_NAME_TIMESTAMP.csv` - Per-sprint files when using `--group-by-sprint`
- `regression_TIMESTAMP.csv` - Regression tasks when using `--group-by-sprint`

## Development Workflow

1. **Adding New Fields**
   - Add field names to `base_fields` or `additional_fields` in `src/jira_time_tracker.py`
   - Update field mapping in extraction logic

2. **Modifying Status Transitions**
   - Edit `status_transitions` in `config/default.yaml`

3. **Changing Working Hours**
   - Modify `working_hours` in `config/default.yaml`
   - Adjust timezone if needed

4. **Testing**
   - Ensure JIRA credentials are valid
   - Test with a small date range first
   - Verify output CSV structure

## Troubleshooting

See `README.md` for common issues and solutions.
