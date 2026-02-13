"""
Local cache management for skill index and SKILL.md files.
Handles TTL-based expiration, hash-based content validation, and offline fallback.
"""

import json
import os
import time
from typing import Optional

from config import (
    CACHE_DIR,
    SKILLS_CACHE_DIR,
    INDEX_CACHE_PATH,
    CACHE_META_PATH,
    INDEX_TTL,
    SKILL_TTL,
)


def ensure_cache_dirs():
    """Create cache directories if they don't exist."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _load_meta() -> dict:
    """Load cache metadata file."""
    if CACHE_META_PATH.exists():
        try:
            return json.loads(CACHE_META_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"index": {}, "skills": {}}


def _save_meta(meta: dict):
    """Save cache metadata file."""
    try:
        CACHE_META_PATH.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


# ---------- Index Cache ----------

def get_cached_index() -> Optional[dict]:
    """
    Load cached index.json if it exists and is not expired.
    Returns the parsed JSON or None.
    """
    if not INDEX_CACHE_PATH.exists():
        return None

    meta = _load_meta()
    cached_at = meta.get("index", {}).get("cached_at", 0)

    if time.time() - cached_at > INDEX_TTL:
        return None  # expired

    try:
        return json.loads(INDEX_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def get_cached_index_fallback() -> Optional[dict]:
    """
    Load cached index.json regardless of TTL (for offline fallback).
    """
    if not INDEX_CACHE_PATH.exists():
        return None
    try:
        return json.loads(INDEX_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_index_cache(data: dict):
    """Save index.json to cache with timestamp."""
    ensure_cache_dirs()
    try:
        INDEX_CACHE_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        meta = _load_meta()
        meta["index"] = {
            "cached_at": time.time(),
            "version": data.get("version", "unknown"),
            "skills_count": data.get("skills_count", 0),
        }
        _save_meta(meta)
    except OSError:
        pass


# ---------- Skill Content Cache ----------

def get_cached_skill(skill_name: str, expected_hash: Optional[str] = None) -> Optional[str]:
    """
    Load cached SKILL.md content for a given skill name.
    If expected_hash is provided, validate against stored hash.
    Returns content string or None.
    """
    skill_dir = SKILLS_CACHE_DIR / skill_name
    skill_path = skill_dir / "SKILL.md"

    if not skill_path.exists():
        return None

    meta = _load_meta()
    skill_meta = meta.get("skills", {}).get(skill_name, {})

    # Check TTL
    cached_at = skill_meta.get("cached_at", 0)
    if time.time() - cached_at > SKILL_TTL:
        # Expired, but still usable as fallback
        pass

    # Check hash if provided
    if expected_hash and skill_meta.get("content_hash") != expected_hash:
        return None  # hash mismatch, need re-download

    try:
        return skill_path.read_text(encoding="utf-8")
    except OSError:
        return None


def get_cached_skill_fallback(skill_name: str) -> Optional[str]:
    """Load cached SKILL.md regardless of TTL/hash (offline fallback)."""
    skill_path = SKILLS_CACHE_DIR / skill_name / "SKILL.md"
    if not skill_path.exists():
        return None
    try:
        return skill_path.read_text(encoding="utf-8")
    except OSError:
        return None


def save_skill_cache(skill_name: str, content: str, content_hash: str = ""):
    """Save SKILL.md content to cache."""
    ensure_cache_dirs()
    skill_dir = SKILLS_CACHE_DIR / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    try:
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        meta = _load_meta()
        if "skills" not in meta:
            meta["skills"] = {}
        meta["skills"][skill_name] = {
            "cached_at": time.time(),
            "content_hash": content_hash,
        }
        _save_meta(meta)
    except OSError:
        pass


# ---------- Cache Stats ----------

def get_cache_stats() -> dict:
    """Return cache statistics for debugging."""
    meta = _load_meta()
    index_info = meta.get("index", {})
    skills_info = meta.get("skills", {})

    return {
        "index_cached": INDEX_CACHE_PATH.exists(),
        "index_cached_at": index_info.get("cached_at"),
        "index_version": index_info.get("version"),
        "cached_skills_count": len(skills_info),
        "cached_skill_names": list(skills_info.keys()),
    }
