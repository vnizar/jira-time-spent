"""Configuration manager for loading and validating settings."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv


class ConfigManager:
    """Manages configuration from YAML files and environment variables."""

    def __init__(self, config_path: Optional[str] = None, env_path: Optional[str] = None):
        """Initialize configuration manager.

        Args:
            config_path: Path to default configuration file
            env_path: Path to .env file
        """
        self.project_root = Path(__file__).parent.parent

        if config_path is None:
            config_path = self.project_root / "config" / "default.yaml"
        if env_path is None:
            env_path = self.project_root / ".env"

        self.config = self._load_config(config_path)
        self._load_env(env_path)

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load YAML configuration file."""
        with open(config_path, "r") as f:
            return yaml.safe_load(f)

    def _load_env(self, env_path: str):
        """Load environment variables from .env file."""
        if os.path.exists(env_path):
            load_dotenv(env_path)

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key (supports nested keys with dot notation)."""
        keys = key.split(".")
        value = self.config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default

        return value if value is not None else default

    @property
    def jira_base_url(self) -> str:
        """Get JIRA base URL from environment."""
        return os.getenv("JIRA_BASE_URL", "")

    @property
    def jira_email(self) -> str:
        """Get JIRA email from environment."""
        return os.getenv("JIRA_EMAIL", "")

    @property
    def jira_api_token(self) -> str:
        """Get JIRA API token from environment."""
        return os.getenv("JIRA_API_TOKEN", "")

    @property
    def jira_project(self) -> str:
        """Get JIRA project key(s) from environment.

        Returns a comma-separated string that can be split into a list.
        Example: "DEA" or "DEA,DEV,QA"
        """
        return os.getenv("JIRA_PROJECT", "")

    def validate(self) -> bool:
        """Validate required configuration is present."""
        errors = []

        if not self.jira_base_url:
            errors.append("JIRA_BASE_URL not set in .env")
        if not self.jira_email:
            errors.append("JIRA_EMAIL not set in .env")
        if not self.jira_api_token:
            errors.append("JIRA_API_TOKEN not set in .env")
        if not self.jira_project:
            errors.append("JIRA_PROJECT not set in .env")

        if errors:
            raise ValueError("Configuration errors:\n" + "\n".join(f"- {e}" for e in errors))

        return True
