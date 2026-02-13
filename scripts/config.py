"""
Skill Router configuration constants.
"""

import os
import pathlib

# --- Paths ---
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE_DIR = PLUGIN_ROOT / "data" / "cache"
SKILLS_CACHE_DIR = CACHE_DIR / "skills"
INDEX_CACHE_PATH = CACHE_DIR / "index.json"
CACHE_META_PATH = CACHE_DIR / "cache-meta.json"

# --- GitHub Registry ---
GITHUB_OWNER = "krishna-505"
GITHUB_REPO = "cloud-skills"
GITHUB_BRANCH = "main"
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}"

# --- Local fallback (for development / offline) ---
LOCAL_CLOUD_SKILLS_DIR = PLUGIN_ROOT.parent / "cloud-skills"

# --- Matching Thresholds (score out of 100) ---
TRIGGER_THRESHOLD = 18       # below this, don't inject any skill
AMBIGUITY_GAP = 10           # if top1 - top2 < this, mark as ambiguous

# --- Scoring Weights (must sum to 1.0) ---
WEIGHT_TRIGGER_KEYWORDS = 0.40
WEIGHT_INTENT_PATTERNS = 0.35
WEIGHT_TAG_OVERLAP = 0.15
WEIGHT_DESCRIPTION_OVERLAP = 0.10

# --- Cache TTL (seconds) ---
INDEX_TTL = 24 * 60 * 60     # 24 hours
SKILL_TTL = 7 * 24 * 60 * 60 # 7 days

# --- Injection Limits ---
MAX_SKILL_CONTENT_CHARS = 8000  # truncate SKILL.md beyond this

# --- Debug ---
DEBUG = os.environ.get("SKILL_ROUTER_DEBUG", "").lower() in ("1", "true", "yes")
