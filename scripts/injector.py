"""
Formats matched skill content for injection via systemMessage.
"""

import sys
from typing import Optional, Tuple

import cache_manager
from registry import SkillRegistry, GitHubRegistry, LocalRegistry
from config import MAX_SKILL_CONTENT_CHARS, DEBUG, LOCAL_CLOUD_SKILLS_DIR


def load_skill_content(
    skill: dict,
    registry: Optional[SkillRegistry] = None,
) -> Optional[str]:
    """
    Load SKILL.md content for a matched skill.
    Uses cache with hash validation, falls back to registry fetch.
    """
    skill_name = skill["name"]
    skill_path = skill.get("path", "")
    content_hash = skill.get("content_hash", "")

    # Try cache (hash-validated)
    cached = cache_manager.get_cached_skill(skill_name, content_hash)
    if cached is not None:
        if DEBUG:
            print(f"[skill-router][debug] skill '{skill_name}' loaded from cache", file=sys.stderr)
        return cached

    # Fetch from registry
    if registry is None:
        registry = _default_registry()

    try:
        content = registry.fetch_skill_content(skill_path)
        if content is not None:
            cache_manager.save_skill_cache(skill_name, content, content_hash)
            if DEBUG:
                print(f"[skill-router][debug] skill '{skill_name}' fetched and cached", file=sys.stderr)
            return content
    except Exception as e:
        if DEBUG:
            print(f"[skill-router][debug] skill fetch failed: {e}", file=sys.stderr)

    # Fallback: expired cache
    fallback = cache_manager.get_cached_skill_fallback(skill_name)
    if fallback is not None:
        if DEBUG:
            print(f"[skill-router][debug] skill '{skill_name}' loaded from expired cache", file=sys.stderr)
        return fallback

    return None


def format_injection(
    skill: dict,
    content: str,
    score: float,
    is_ambiguous: bool = False,
    runner_up: Optional[Tuple[dict, float]] = None,
) -> str:
    """
    Format the systemMessage injection text.
    """
    display_name = skill.get("display_name", skill["name"])
    category = skill.get("category", "unknown")

    # Truncate content if too long
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        content = content[:MAX_SKILL_CONTENT_CHARS] + "\n\n[... content truncated ...]"

    lines = []
    lines.append(
        f"[skill-router] Automatically loaded skill: **{display_name}** "
        f"(category: {category}, score: {score:.0f})"
    )

    if is_ambiguous and runner_up:
        ru_skill, ru_score = runner_up
        ru_name = ru_skill.get("display_name", ru_skill["name"])
        lines.append(
            f"[skill-router] Note: also considered **{ru_name}** (score: {ru_score:.0f}). "
            f"If the loaded skill seems wrong, the user may have meant the other one."
        )

    lines.append("")
    lines.append("--- BEGIN SKILL INSTRUCTIONS ---")
    lines.append(content)
    lines.append("--- END SKILL INSTRUCTIONS ---")
    lines.append("")
    lines.append(
        "[skill-router] Apply these skill instructions to the user's request. "
        "If the skill doesn't seem relevant, ignore these instructions and respond normally."
    )

    return "\n".join(lines)


def _default_registry() -> SkillRegistry:
    if LOCAL_CLOUD_SKILLS_DIR.exists() and (LOCAL_CLOUD_SKILLS_DIR / "index.json").exists():
        return LocalRegistry()
    return GitHubRegistry()
