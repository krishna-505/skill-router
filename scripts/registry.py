"""
Data abstraction layer for skill registries.
Provides GitHubRegistry (Phase 1) and LocalRegistry (dev/offline).
"""

import json
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import Optional

from config import (
    GITHUB_RAW_BASE,
    LOCAL_CLOUD_SKILLS_DIR,
    DEBUG,
)


class SkillRegistry(ABC):
    """Abstract base for skill data sources."""

    @abstractmethod
    def fetch_index(self) -> Optional[dict]:
        """Fetch the skill index. Returns parsed JSON or None."""
        ...

    @abstractmethod
    def fetch_skill_content(self, skill_path: str) -> Optional[str]:
        """Fetch SKILL.md content by path. Returns content string or None."""
        ...


class GitHubRegistry(SkillRegistry):
    """
    Fetches skills from a GitHub public repository via raw.githubusercontent.com.
    No authentication required.
    """

    def __init__(self, base_url: str = GITHUB_RAW_BASE, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def fetch_index(self) -> Optional[dict]:
        url = f"{self.base_url}/index.json"
        content = self._fetch_url(url)
        if content is None:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    def fetch_skill_content(self, skill_path: str) -> Optional[str]:
        url = f"{self.base_url}/{skill_path}"
        return self._fetch_url(url)

    def _fetch_url(self, url: str) -> Optional[str]:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "skill-router/1.0"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
            if DEBUG:
                import sys
                print(f"[skill-router][debug] fetch failed: {url} -> {e}", file=sys.stderr)
            return None


class LocalRegistry(SkillRegistry):
    """
    Reads skills from a local cloud-skills directory.
    Used for development and offline fallback.
    """

    def __init__(self, root=None):
        self.root = root or LOCAL_CLOUD_SKILLS_DIR

    def fetch_index(self) -> Optional[dict]:
        index_path = self.root / "index.json"
        if not index_path.exists():
            return None
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def fetch_skill_content(self, skill_path: str) -> Optional[str]:
        full_path = self.root / skill_path
        if not full_path.exists():
            return None
        try:
            return full_path.read_text(encoding="utf-8")
        except OSError:
            return None


class APIRegistry(SkillRegistry):
    """
    Placeholder for future API-based registry.
    For when skills scale to 10000+.
    """

    def fetch_index(self) -> Optional[dict]:
        raise NotImplementedError("APIRegistry not yet implemented")

    def fetch_skill_content(self, skill_path: str) -> Optional[str]:
        raise NotImplementedError("APIRegistry not yet implemented")
