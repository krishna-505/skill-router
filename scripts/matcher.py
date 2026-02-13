"""
Multi-level matching algorithm for skill routing.
Pure text processing, no LLM calls. Bilingual (EN/ZH) support.
"""

import re
from typing import Dict, List, Optional, Tuple

from config import (
    WEIGHT_TRIGGER_KEYWORDS,
    WEIGHT_INTENT_PATTERNS,
    WEIGHT_TAG_OVERLAP,
    WEIGHT_DESCRIPTION_OVERLAP,
    TRIGGER_THRESHOLD,
    AMBIGUITY_GAP,
)


def detect_language(text: str) -> str:
    """Detect if text contains Chinese characters. Returns 'zh', 'en', or 'both'."""
    has_zh = False
    has_en = False
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff':
            has_zh = True
        elif ch.isascii() and ch.isalpha():
            has_en = True
        if has_zh and has_en:
            return "both"
    if has_zh:
        return "zh"
    return "en"


def tokenize_en(text: str) -> List[str]:
    """Tokenize English text into lowercase words."""
    return re.findall(r'[a-z][a-z0-9\-]*', text.lower())


def normalize(text: str) -> str:
    """Lowercase and strip extra whitespace."""
    return re.sub(r'\s+', ' ', text.lower().strip())


def _stem_match(word: str, keyword: str) -> bool:
    """Simple prefix-based stem matching. 'accessible' matches 'accessibility' etc."""
    if len(word) < 5 or len(keyword) < 5:
        return word == keyword
    # Both must be at least 6 chars and share 80% prefix of the shorter word
    min_len = min(len(word), len(keyword))
    prefix_len = max(5, min_len * 4 // 5)  # 80%, min 5
    if prefix_len > min_len:
        return False
    return word[:prefix_len] == keyword[:prefix_len]


# ---------- Level 1: Negative Keyword Exclusion ----------

def check_negative_keywords(prompt: str, skill: dict, lang: str) -> bool:
    """
    Return True if skill should be EXCLUDED.
    Requires 2+ negative keyword hits for single-word keywords,
    or 1 hit for multi-word negative keywords (more specific = stronger signal).
    """
    neg = skill.get("negative_keywords", {})
    prompt_lower = prompt.lower()

    hits_single = 0
    hits_multi = 0

    langs_to_check = _langs_for(lang)
    for lng in langs_to_check:
        for kw in neg.get(lng, []):
            kw_lower = kw.lower()
            if kw_lower in prompt_lower:
                word_count = len(kw_lower.split())
                if word_count >= 2:
                    hits_multi += 1
                else:
                    hits_single += 1

    # Multi-word negative keyword is a strong signal (1 hit enough)
    if hits_multi >= 1:
        return True
    # Single-word negative keywords need 2+ hits to exclude
    if hits_single >= 2:
        return True
    return False


# ---------- Level 2: Trigger Keyword Matching (40%) ----------

def score_trigger_keywords(prompt: str, skill: dict, lang: str) -> float:
    """
    Score based on trigger_keywords presence in prompt.
    Returns 0-100 raw score (will be weighted later).

    Key insight: even 1 specific keyword match is a strong signal.
    Scoring: first match gives 40 base, each additional adds 15.
    """
    trigger_kws = skill.get("trigger_keywords", {})
    prompt_lower = prompt.lower()
    prompt_words = set(tokenize_en(prompt))

    matched = 0
    best_bonus = 0.0

    langs_to_check = _langs_for(lang)
    for lng in langs_to_check:
        kws = trigger_kws.get(lng, [])
        for kw in kws:
            kw_lower = kw.lower()
            if lng == "zh":
                # Chinese: direct substring match
                if kw_lower in prompt_lower:
                    matched += 1
                    best_bonus = max(best_bonus, min(len(kw_lower) / 3, 15))
            else:
                # English: word-boundary aware matching
                kw_words = set(tokenize_en(kw_lower))
                if kw_words and kw_words.issubset(prompt_words):
                    # All keyword words appear as whole words → strong match
                    matched += 1
                    best_bonus = max(best_bonus, 10)
                elif kw_lower in prompt_lower and len(kw_lower) >= 5:
                    # Substring match only for longer keywords (avoid "aria" in "variable")
                    matched += 0.7
                else:
                    # Try stem matching for single-word keywords (6+ chars)
                    kw_toks = tokenize_en(kw_lower)
                    if len(kw_toks) == 1 and len(kw_toks[0]) >= 6:
                        for pw in prompt_words:
                            if len(pw) >= 6 and _stem_match(pw, kw_toks[0]):
                                matched += 0.5
                                break

    if matched == 0:
        return 0.0

    # First match gives 40 base, each additional adds 15, cap at 100
    base = 40 + (matched - 1) * 15
    return min(base + best_bonus, 100.0)


# ---------- Level 3: Intent Pattern Matching (35%) ----------

def score_intent_patterns(prompt: str, skill: dict, lang: str) -> float:
    """
    Score based on regex intent_patterns matching.
    Any match gives high score; more matches = higher.
    Returns 0-100 raw score.
    """
    patterns = skill.get("intent_patterns", {})
    prompt_lower = prompt.lower()

    matched = 0
    total = 0

    langs_to_check = _langs_for(lang)
    for lng in langs_to_check:
        pats = patterns.get(lng, [])
        for pat in pats:
            total += 1
            try:
                if re.search(pat, prompt_lower, re.IGNORECASE):
                    matched += 1
            except re.error:
                continue

    if total == 0:
        return 0.0
    if matched == 0:
        return 0.0
    # First match gives 70, additional matches add up to 30 more
    return min(70 + (matched - 1) * 15, 100.0)


# ---------- Level 4: Tag Overlap (15%) ----------

def score_tag_overlap(prompt: str, skill: dict) -> float:
    """
    Score based on overlap between prompt words and skill tags.
    Returns 0-100 raw score.
    """
    tags = skill.get("tags", [])
    if not tags:
        return 0.0

    prompt_words = set(tokenize_en(prompt))
    prompt_lower = prompt.lower()

    matched = 0
    for tag in tags:
        tag_lower = tag.lower()
        # Check as substring (only for tags >= 5 chars to avoid false matches)
        if tag_lower in prompt_lower and len(tag_lower) >= 5:
            matched += 1
        else:
            # Check individual words of the tag
            tag_words = set(tokenize_en(tag_lower))
            if tag_words and tag_words.issubset(prompt_words):
                matched += 0.7
            else:
                # Stem matching for tags (conservative: only long words)
                for tw in tokenize_en(tag_lower):
                    if len(tw) < 6:
                        continue
                    for pw in prompt_words:
                        if len(pw) >= 6 and _stem_match(pw, tw):
                            matched += 0.3
                            break

    # Any tag match is meaningful
    if matched == 0:
        return 0.0
    return min((matched / len(tags)) * 120, 100.0)


# ---------- Level 5: Description Word Overlap (10%) ----------

def score_description_overlap(prompt: str, skill: dict) -> float:
    """
    Score based on word overlap between prompt and short_description.
    Returns 0-100 raw score.
    """
    desc = skill.get("short_description", "")
    if not desc:
        return 0.0

    prompt_words = set(tokenize_en(prompt))
    desc_words = set(tokenize_en(desc))

    # Remove common stop words
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "and",
        "but", "or", "nor", "not", "so", "yet", "both", "either",
        "neither", "each", "every", "all", "any", "few", "more",
        "most", "other", "some", "such", "no", "only", "own", "same",
        "than", "too", "very", "just", "that", "this", "it", "its",
    }

    prompt_words -= stop_words
    desc_words -= stop_words

    if not desc_words:
        return 0.0

    # Exact + stem overlap (conservative stems)
    overlap = 0
    for dw in desc_words:
        if dw in prompt_words:
            overlap += 1
        elif len(dw) >= 6:
            for pw in prompt_words:
                if len(pw) >= 6 and _stem_match(pw, dw):
                    overlap += 0.5
                    break

    return min((overlap / len(desc_words)) * 100, 100.0)


# ---------- Main Matching Function ----------

def compute_score(prompt: str, skill: dict, lang: str) -> float:
    """
    Compute total weighted score for a skill against a prompt.
    Returns 0-100 score.
    """
    # Level 1: Negative keyword exclusion
    if check_negative_keywords(prompt, skill, lang):
        return -1.0

    # Levels 2-5: Weighted scoring
    s_trigger = score_trigger_keywords(prompt, skill, lang)
    s_intent = score_intent_patterns(prompt, skill, lang)
    s_tags = score_tag_overlap(prompt, skill)
    s_desc = score_description_overlap(prompt, skill)

    total = (
        s_trigger * WEIGHT_TRIGGER_KEYWORDS +
        s_intent * WEIGHT_INTENT_PATTERNS +
        s_tags * WEIGHT_TAG_OVERLAP +
        s_desc * WEIGHT_DESCRIPTION_OVERLAP
    )
    return total


def match_skills(prompt: str, skills: List[dict]) -> List[Tuple[dict, float]]:
    """
    Match prompt against all skills, return sorted list of (skill, score).
    Only includes skills above TRIGGER_THRESHOLD.
    """
    lang = detect_language(prompt)
    results = []

    for skill in skills:
        score = compute_score(prompt, skill, lang)
        if score >= TRIGGER_THRESHOLD:
            results.append((skill, score))

    # Sort by score descending
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def select_best(
    ranked: List[Tuple[dict, float]]
) -> Optional[Tuple[dict, float, bool]]:
    """
    Select the best matching skill from ranked results.
    Returns (skill, score, is_ambiguous) or None.
    """
    if not ranked:
        return None

    best_skill, best_score = ranked[0]

    is_ambiguous = False
    if len(ranked) > 1:
        second_score = ranked[1][1]
        if best_score - second_score < AMBIGUITY_GAP:
            is_ambiguous = True

    return (best_skill, best_score, is_ambiguous)


# ---------- Helper ----------

def _langs_for(lang: str) -> List[str]:
    """Return list of language keys to check based on detected language."""
    if lang == "both":
        return ["en", "zh"]
    elif lang == "zh":
        return ["zh", "en"]  # check zh first, fallback to en
    else:
        return ["en"]
