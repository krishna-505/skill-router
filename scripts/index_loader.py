"""
Index loading with cache-first strategy and offline fallback.
"""

import sys
from typing import Optional, List

import cache_manager
from registry import SkillRegistry, GitHubRegistry, LocalRegistry
from config import DEBUG, LOCAL_CLOUD_SKILLS_DIR


def load_index(registry: Optional[SkillRegistry] = None) -> Optional[dict]:
    """
    Load skill index with the following priority:
    1. Valid (non-expired) local cache
    2. Fetch from registry (GitHub or local)
    3. Expired local cache (offline fallback)

    Returns parsed index dict or None.
    """
    # Step 1: Try valid cache
    cached = cache_manager.get_cached_index()
    if cached is not None:
        if DEBUG:
            print("[skill-router][debug] index loaded from valid cache", file=sys.stderr)
        return cached

    # Step 2: Try fetching from registry
    if registry is None:
        registry = _default_registry()

    try:
        fresh = registry.fetch_index()
        if fresh is not None:
            cache_manager.save_index_cache(fresh)
            if DEBUG:
                print("[skill-router][debug] index fetched from registry and cached", file=sys.stderr)
            return fresh
    except Exception as e:
        if DEBUG:
            print(f"[skill-router][debug] registry fetch failed: {e}", file=sys.stderr)

    # Step 3: Offline fallback - use expired cache
    fallback = cache_manager.get_cached_index_fallback()
    if fallback is not None:
        if DEBUG:
            print("[skill-router][debug] index loaded from expired cache (offline fallback)", file=sys.stderr)
        return fallback

    if DEBUG:
        print("[skill-router][debug] no index available", file=sys.stderr)
    return None


def get_skills_list(index: dict) -> List[dict]:
    """Extract the skills list from an index dict."""
    return index.get("skills", [])


def _default_registry() -> SkillRegistry:
    """
    Return the best available registry.
    Prefer local if cloud-skills dir exists (dev mode), otherwise GitHub.
    """
    if LOCAL_CLOUD_SKILLS_DIR.exists() and (LOCAL_CLOUD_SKILLS_DIR / "index.json").exists():
        return LocalRegistry()
    return GitHubRegistry()
