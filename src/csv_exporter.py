"""CSV export functionality for time tracking results."""

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class CsvExporter:
    """Export time tracking results to CSV."""

    def __init__(self, output_dir: str = None):
        """Initialize CSV exporter.

        Args:
            output_dir: Directory for output files
        """
        if output_dir is None:
            output_dir = Path(__file__).parent.parent / "outputs"

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export(
        self,
        results: List[Dict[str, Any]],
        filename: str = None,
    ) -> str:
        """Export results to CSV file.

        Args:
            results: List of result dictionaries
            filename: Output filename (default: timestamp-based)

        Returns:
            Path to created file
        """
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"jira_time_tracking_{timestamp}.csv"

        filepath = self.output_dir / filename

        if not results:
            logger.warning("No results to export")
            return str(filepath)

        # Get all possible keys from results
        fieldnames_set = set()
        for result in results:
            fieldnames_set.update(result.keys())
        fieldnames = sorted(fieldnames_set)

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

        logger.info(f"Exported {len(results)} results to {filepath}")
        return str(filepath)
