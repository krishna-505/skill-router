"""
Comparison evaluation: skill-router (方案A) vs full local install baseline (方案B).

Runs the same 100 test cases through both approaches and generates a comparison
report showing coverage, precision, recall, confusion rate, and token costs.

方案B simulation strategy:
- Layer 1 (default): Description-based word overlap matching, limited to skills
  within the 2% context budget. No trigger_keywords, no intent_patterns, no
  negative_keywords — simulating what Claude sees in the native skill system.
- Layer 2 (--llm): Optional LLM-based matching via Claude API.

Usage:
    python compare.py [--index PATH] [--verbose] [--save]
"""

import json
import re
import sys
import time
import argparse
from pathlib import Path
from collections import defaultdict

# Add scripts dir to path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from config import TRIGGER_THRESHOLD
from matcher import match_skills, select_best, detect_language, tokenize_en

EVAL_DIR = Path(__file__).resolve().parent
TEST_CASES_PATH = EVAL_DIR / "test_cases.json"
RESULTS_DIR = EVAL_DIR / "results"
DEFAULT_INDEX = Path(__file__).resolve().parent.parent.parent / "cloud-skills" / "index.json"

CONTEXT_WINDOW = 128000
BUDGET_CHARS = int(CONTEXT_WINDOW * 0.02)


# ──────────────────────────────────────────────
#  方案B: Baseline matching (description only)
# ──────────────────────────────────────────────

def select_within_budget(skills: list, budget_chars: int = BUDGET_CHARS) -> list:
    """Select skills that fit within the description budget, shortest first."""
    def desc_line(s):
        name = s.get("display_name", s["name"])
        desc = s.get("short_description", "")
        return f"{name}: {desc}"

    entries = [(s, desc_line(s)) for s in skills]
    entries.sort(key=lambda e: len(e[1]))

    visible = []
    cumulative = 0
    for skill, line in entries:
        cumulative += len(line)
        if cumulative <= budget_chars:
            visible.append(skill)
        # Once over budget, all remaining are hidden

    return visible


def baseline_word_overlap(prompt: str, name: str, description: str) -> float:
    """
    Score a skill against a prompt using only name + description word overlap.
    This simulates Claude's best-case matching with limited information.
    No trigger_keywords, no intent_patterns, no negative_keywords.
    Returns 0-100 score.
    """
    prompt_lower = prompt.lower()
    prompt_words = set(tokenize_en(prompt))

    # Also extract Chinese characters as substrings
    zh_chars_in_prompt = re.findall(r'[\u4e00-\u9fff]+', prompt_lower)

    # Build searchable text from name + description
    search_text = f"{name} {description}".lower()
    search_words = set(tokenize_en(search_text))
    zh_chars_in_search = re.findall(r'[\u4e00-\u9fff]+', search_text)

    # Stop words to ignore
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "and",
        "but", "or", "not", "so", "both", "either", "each", "every",
        "all", "any", "few", "more", "most", "other", "some", "such",
        "no", "only", "own", "same", "than", "too", "very", "just",
        "that", "this", "it", "its", "how", "what", "which", "my",
        "your", "our", "their", "you", "me", "us", "them", "i", "we",
    }

    prompt_words -= stop_words
    search_words -= stop_words

    if not search_words and not zh_chars_in_search:
        return 0.0

    # English word overlap
    overlap = 0
    for sw in search_words:
        if sw in prompt_words:
            overlap += 1
        elif len(sw) >= 6:
            # Simple prefix match
            for pw in prompt_words:
                if len(pw) >= 6 and sw[:5] == pw[:5]:
                    overlap += 0.5
                    break

    # Chinese substring overlap (check if Chinese in name/desc appears in prompt)
    zh_overlap = 0
    for zh in zh_chars_in_search:
        if zh in prompt_lower:
            zh_overlap += 1

    total_searchable = len(search_words) + len(zh_chars_in_search)
    total_overlap = overlap + zh_overlap

    if total_searchable == 0:
        return 0.0

    # Normalize: more overlap = higher score
    ratio = total_overlap / total_searchable
    # Boost if skill name directly appears in prompt
    name_words = set(tokenize_en(name))
    name_words -= stop_words
    if name_words and name_words.issubset(prompt_words):
        ratio = min(ratio + 0.4, 1.0)
    elif name.lower().replace("-", " ") in prompt_lower:
        ratio = min(ratio + 0.3, 1.0)
    elif name.lower().replace("-", "") in prompt_lower.replace(" ", ""):
        ratio = min(ratio + 0.2, 1.0)

    return ratio * 100


def baseline_match(prompt: str, skills: list, budget_chars: int = BUDGET_CHARS) -> dict:
    """
    方案B matching: description-based word overlap, budget-limited.
    Returns dict with matched skill name, score, and visibility info.
    """
    visible_skills = select_within_budget(skills, budget_chars)
    visible_names = {s["name"] for s in visible_skills}

    best_name = None
    best_score = 0.0

    for skill in visible_skills:
        name = skill["name"]
        display_name = skill.get("display_name", name)
        desc = skill.get("short_description", "")
        score = baseline_word_overlap(prompt, name, desc)

        if score > best_score:
            best_score = score
            best_name = name

    # Require a minimum threshold to avoid matching everything
    baseline_threshold = 15.0
    if best_score < baseline_threshold:
        best_name = None
        best_score = 0.0

    return {
        "matched": best_name,
        "score": round(best_score, 1),
        "visible_count": len(visible_skills),
        "total_count": len(skills),
        "visible_names": visible_names,
    }


# ──────────────────────────────────────────────
#  方案A: Skill-router matching (reuse existing)
# ──────────────────────────────────────────────

def plan_a_match(prompt: str, skills: list) -> dict:
    """方案A matching: full skill-router with all features."""
    start = time.perf_counter()
    ranked = match_skills(prompt, skills)
    result = select_best(ranked)
    elapsed_ms = (time.perf_counter() - start) * 1000

    if result is None:
        return {
            "matched": None,
            "score": 0,
            "latency_ms": elapsed_ms,
        }

    skill, score, is_ambiguous = result
    return {
        "matched": skill["name"],
        "score": round(score, 1),
        "latency_ms": elapsed_ms,
    }


# ──────────────────────────────────────────────
#  Evaluation logic
# ──────────────────────────────────────────────

def evaluate_comparison(test_cases: list, skills: list, verbose: bool = False) -> dict:
    """Run both approaches on all test cases and compute comparison metrics."""
    results = []

    # 方案A counters
    a_tp = a_fp = a_fn = a_tn = 0
    a_confusion_correct = a_confusion_alt = a_confusion_wrong = a_confusion_none = 0

    # 方案B counters
    b_tp = b_fp = b_fn = b_tn = 0
    b_confusion_correct = b_confusion_alt = b_confusion_wrong = b_confusion_none = 0
    b_invisible_miss = 0  # expected skill was hidden (outside budget)

    # Pre-compute visible skills for 方案B
    visible_skills = select_within_budget(skills)
    visible_names = {s["name"] for s in visible_skills}

    latencies_a = []

    for tc in test_cases:
        tc_id = tc["id"]
        prompt = tc["prompt"]
        expected = tc.get("expected")
        expected_alt = tc.get("expected_alt")
        tc_type = tc["type"]

        # Run both
        ra = plan_a_match(prompt, skills)
        rb = baseline_match(prompt, skills)
        latencies_a.append(ra["latency_ms"])

        # Check if expected skill is invisible in 方案B
        is_invisible = expected is not None and expected not in visible_names

        # --- 方案A correctness ---
        a_correct = False
        if tc_type == "positive":
            if ra["matched"] == expected:
                a_tp += 1; a_correct = True
            elif ra["matched"] is None:
                a_fn += 1
            else:
                a_fp += 1
        elif tc_type == "negative":
            if ra["matched"] is None:
                a_tn += 1; a_correct = True
            else:
                a_fp += 1
        elif tc_type == "confusion":
            if ra["matched"] == expected:
                a_confusion_correct += 1; a_correct = True
            elif ra["matched"] == expected_alt:
                a_confusion_alt += 1; a_correct = True
            elif ra["matched"] is None:
                a_confusion_none += 1
            else:
                a_confusion_wrong += 1
        elif tc_type == "boundary":
            if ra["matched"] == expected:
                a_correct = True

        # --- 方案B correctness ---
        b_correct = False
        if tc_type == "positive":
            if is_invisible:
                b_invisible_miss += 1
                b_fn += 1
            elif rb["matched"] == expected:
                b_tp += 1; b_correct = True
            elif rb["matched"] is None:
                b_fn += 1
            else:
                b_fp += 1
        elif tc_type == "negative":
            if rb["matched"] is None:
                b_tn += 1; b_correct = True
            else:
                b_fp += 1
        elif tc_type == "confusion":
            if is_invisible:
                b_invisible_miss += 1
                b_confusion_none += 1
            elif rb["matched"] == expected:
                b_confusion_correct += 1; b_correct = True
            elif rb["matched"] == expected_alt:
                b_confusion_alt += 1; b_correct = True
            elif rb["matched"] is None:
                b_confusion_none += 1
            else:
                b_confusion_wrong += 1
        elif tc_type == "boundary":
            if is_invisible:
                b_invisible_miss += 1
            elif rb["matched"] == expected:
                b_correct = True

        # Determine status label for display
        if a_correct and b_correct:
            status = "BOTH OK"
        elif a_correct and not b_correct:
            status = "A WINS"
        elif not a_correct and b_correct:
            status = "B WINS"
        else:
            status = "BOTH FAIL"

        entry = {
            "id": tc_id,
            "type": tc_type,
            "prompt": prompt,
            "expected": expected,
            "expected_alt": expected_alt,
            "plan_a": {"matched": ra["matched"], "score": ra["score"], "correct": a_correct},
            "plan_b": {"matched": rb["matched"], "score": rb["score"], "correct": b_correct,
                       "invisible": is_invisible},
            "status": status,
            "notes": tc.get("notes", ""),
        }
        results.append(entry)

        if verbose:
            prompt_short = prompt[:50] + ("..." if len(prompt) > 50 else "")
            print(f"  [{status:9s}] #{tc_id:3d} ({tc_type:10s}) \"{prompt_short}\"")
            if status != "BOTH OK":
                print(f"       A: {ra['matched']} (score={ra['score']})")
                b_note = " [INVISIBLE]" if is_invisible else ""
                print(f"       B: {rb['matched']} (score={rb['score']}){b_note}")

    # Compute metrics
    positive_count = sum(1 for tc in test_cases if tc["type"] == "positive")
    negative_count = sum(1 for tc in test_cases if tc["type"] == "negative")
    confusion_count = sum(1 for tc in test_cases if tc["type"] == "confusion")
    boundary_count = sum(1 for tc in test_cases if tc["type"] == "boundary")

    # 方案A metrics
    a_precision = a_tp / (a_tp + a_fp) * 100 if (a_tp + a_fp) > 0 else 0
    a_recall = a_tp / (a_tp + a_fn) * 100 if (a_tp + a_fn) > 0 else 0
    a_confusion_rate = a_confusion_wrong / confusion_count * 100 if confusion_count > 0 else 0

    # 方案B metrics
    b_precision = b_tp / (b_tp + b_fp) * 100 if (b_tp + b_fp) > 0 else 0
    b_recall = b_tp / (b_tp + b_fn) * 100 if (b_tp + b_fn) > 0 else 0
    b_confusion_rate = b_confusion_wrong / confusion_count * 100 if confusion_count > 0 else 0

    # Invisible miss rate (only for 方案B)
    should_match_count = positive_count + confusion_count + boundary_count
    invisible_miss_rate = b_invisible_miss / should_match_count * 100 if should_match_count > 0 else 0

    # Token cost estimation
    avg_a_tokens = 487  # Average injection cost from token_analysis
    b_idle_tokens = sum(
        len(f"{s.get('display_name', s['name'])}: {s.get('short_description', '')}") // 4
        for s in skills
    )

    n = len(latencies_a)
    latencies_sorted = sorted(latencies_a)

    metrics = {
        "plan_a": {
            "precision": round(a_precision, 1),
            "recall": round(a_recall, 1),
            "f1": round(2 * a_precision * a_recall / (a_precision + a_recall), 1) if (a_precision + a_recall) > 0 else 0,
            "confusion_rate": round(a_confusion_rate, 1),
            "invisible_miss_rate": 0,
            "coverage": len(skills),
            "avg_token_cost": avg_a_tokens,
            "tp": a_tp, "fp": a_fp, "fn": a_fn, "tn": a_tn,
            "confusion": {
                "correct": a_confusion_correct,
                "alt": a_confusion_alt,
                "wrong": a_confusion_wrong,
                "none": a_confusion_none,
            },
            "latency_p50": round(latencies_sorted[n // 2], 1) if n > 0 else 0,
        },
        "plan_b": {
            "precision": round(b_precision, 1),
            "recall": round(b_recall, 1),
            "f1": round(2 * b_precision * b_recall / (b_precision + b_recall), 1) if (b_precision + b_recall) > 0 else 0,
            "confusion_rate": round(b_confusion_rate, 1),
            "invisible_miss_rate": round(invisible_miss_rate, 1),
            "coverage": len(visible_skills),
            "avg_token_cost": b_idle_tokens,
            "tp": b_tp, "fp": b_fp, "fn": b_fn, "tn": b_tn,
            "confusion": {
                "correct": b_confusion_correct,
                "alt": b_confusion_alt,
                "wrong": b_confusion_wrong,
                "none": b_confusion_none,
            },
            "invisible_misses": b_invisible_miss,
        },
        "summary": {
            "total_cases": len(test_cases),
            "positive": positive_count,
            "negative": negative_count,
            "confusion": confusion_count,
            "boundary": boundary_count,
        },
        "win_counts": {
            "a_wins": sum(1 for r in results if r["status"] == "A WINS"),
            "b_wins": sum(1 for r in results if r["status"] == "B WINS"),
            "both_ok": sum(1 for r in results if r["status"] == "BOTH OK"),
            "both_fail": sum(1 for r in results if r["status"] == "BOTH FAIL"),
        },
    }

    return {"metrics": metrics, "results": results}


def print_comparison_report(data: dict):
    """Print formatted Markdown comparison report."""
    m = data["metrics"]
    results = data["results"]
    a = m["plan_a"]
    b = m["plan_b"]
    w = m["win_counts"]

    print()
    print("=" * 64)
    print("  SKILL-ROUTER vs BASELINE COMPARISON REPORT")
    print("=" * 64)

    print(f"\n  Test Cases: {m['summary']['total_cases']}")
    print(f"    Positive: {m['summary']['positive']} | Negative: {m['summary']['negative']} "
          f"| Confusion: {m['summary']['confusion']} | Boundary: {m['summary']['boundary']}")

    # Main comparison table
    print(f"\n{'─' * 64}")
    print(f"  {'Metric':30s} {'方案A':>10s}  {'方案B':>10s}  {'差异':>10s}")
    print(f"  {'':30s} {'(router)':>10s}  {'(baseline)':>10s}  {'':>10s}")
    print(f"{'─' * 64}")

    def diff_str(va, vb, suffix="", higher_better=True):
        d = va - vb
        sign = "+" if d > 0 else ""
        return f"{sign}{d:.1f}{suffix}" if d != 0 else "="

    print(f"  {'Coverage (routable)':30s} {a['coverage']:>10d}  {b['coverage']:>10d}  {'+' + str(a['coverage'] - b['coverage']):>10s}")
    print(f"  {'Precision':30s} {a['precision']:>9.1f}%  {b['precision']:>9.1f}%  {diff_str(a['precision'], b['precision'], '%'):>10s}")
    print(f"  {'Recall':30s} {a['recall']:>9.1f}%  {b['recall']:>9.1f}%  {diff_str(a['recall'], b['recall'], '%'):>10s}")
    print(f"  {'F1 Score':30s} {a['f1']:>9.1f}%  {b['f1']:>9.1f}%  {diff_str(a['f1'], b['f1'], '%'):>10s}")
    print(f"  {'Confusion Rate':30s} {a['confusion_rate']:>9.1f}%  {b['confusion_rate']:>9.1f}%  {diff_str(a['confusion_rate'], b['confusion_rate'], '%', higher_better=False):>10s}")
    print(f"  {'Invisible Miss Rate':30s} {a['invisible_miss_rate']:>9.1f}%  {b['invisible_miss_rate']:>9.1f}%  {diff_str(a['invisible_miss_rate'], b['invisible_miss_rate'], '%', higher_better=False):>10s}")
    print(f"  {'Avg Token Cost':30s} {a['avg_token_cost']:>10d}  {b['avg_token_cost']:>10d}  {'-' + str(b['avg_token_cost'] - a['avg_token_cost']):>10s}")

    # Win/loss summary
    print(f"\n{'─' * 64}")
    print(f"  Win/Loss Summary")
    print(f"{'─' * 64}")
    print(f"  方案A wins:     {w['a_wins']:3d} cases")
    print(f"  方案B wins:     {w['b_wins']:3d} cases")
    print(f"  Both correct:   {w['both_ok']:3d} cases")
    print(f"  Both wrong:     {w['both_fail']:3d} cases")

    # Detailed confusion analysis
    print(f"\n{'─' * 64}")
    print(f"  Confusion Case Breakdown")
    print(f"{'─' * 64}")
    print(f"  {'':20s} {'方案A':>8s}  {'方案B':>8s}")
    print(f"  {'Correct (primary)':20s} {a['confusion']['correct']:>8d}  {b['confusion']['correct']:>8d}")
    print(f"  {'Correct (alt)':20s} {a['confusion']['alt']:>8d}  {b['confusion']['alt']:>8d}")
    print(f"  {'Wrong match':20s} {a['confusion']['wrong']:>8d}  {b['confusion']['wrong']:>8d}")
    print(f"  {'No match':20s} {a['confusion']['none']:>8d}  {b['confusion']['none']:>8d}")

    # Per-case comparison (differences only, or all if verbose)
    diff_results = [r for r in results if r["status"] in ("A WINS", "B WINS")]
    print(f"\n{'─' * 64}")
    print(f"  Case-by-Case Differences ({len(diff_results)} cases)")
    print(f"{'─' * 64}")

    for r in diff_results:
        prompt_short = r["prompt"][:55] + ("..." if len(r["prompt"]) > 55 else "")
        print(f"\n  #{r['id']:3d} [{r['status']}] ({r['type']})")
        print(f"       \"{prompt_short}\"")
        print(f"       Expected: {r['expected']}")

        ra = r["plan_a"]
        rb = r["plan_b"]
        a_mark = "OK" if ra["correct"] else "FAIL"
        b_mark = "OK" if rb["correct"] else "FAIL"
        inv = " [INVISIBLE]" if rb.get("invisible") else ""

        print(f"       方案A: {ra['matched'] or '(none)':25s} (score={ra['score']:5.1f}) [{a_mark}]")
        print(f"       方案B: {(rb['matched'] or '(none)'):25s} (score={rb['score']:5.1f}) [{b_mark}]{inv}")

    # Summary verdict
    print(f"\n{'=' * 64}")
    if w['a_wins'] > w['b_wins']:
        advantage = w['a_wins'] - w['b_wins']
        print(f"  VERDICT: 方案A (skill-router) wins by {advantage} cases")
        print(f"  Key advantages:")
        print(f"    - {a['coverage'] - b['coverage']} more skills routable (no budget limit)")
        print(f"    - {b['invisible_miss_rate']:.1f}% invisible miss rate eliminated")
        print(f"    - {b['avg_token_cost'] - a['avg_token_cost']:,} fewer tokens per turn")
        if a['precision'] > b['precision']:
            print(f"    - {a['precision'] - b['precision']:.1f}% higher precision (negative keywords)")
    elif w['b_wins'] > w['a_wins']:
        print(f"  VERDICT: 方案B (baseline) wins by {w['b_wins'] - w['a_wins']} cases")
    else:
        print(f"  VERDICT: Both approaches tie on case wins")
    print("=" * 64)


def main():
    parser = argparse.ArgumentParser(description="Compare skill-router vs baseline")
    parser.add_argument("--index", type=str, default=str(DEFAULT_INDEX), help="Path to index.json")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each test case")
    parser.add_argument("--save", "-s", action="store_true", help="Save results to JSON")
    args = parser.parse_args()

    print("Loading test cases...")
    test_cases = json.loads(TEST_CASES_PATH.read_text(encoding="utf-8"))["test_cases"]
    print(f"  {len(test_cases)} test cases loaded")

    print("Loading index...")
    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    skills = index.get("skills", [])
    print(f"  {len(skills)} skills in index")

    # Show budget info
    visible = select_within_budget(skills)
    print(f"\n  方案B budget: {BUDGET_CHARS:,} chars ({len(visible)}/{len(skills)} skills visible)")

    print(f"\nRunning comparison evaluation...")
    data = evaluate_comparison(test_cases, skills, verbose=args.verbose)

    print_comparison_report(data)

    if args.save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        result_path = RESULTS_DIR / f"comparison_{timestamp}.json"
        result_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nResults saved to {result_path}")


if __name__ == "__main__":
    main()
