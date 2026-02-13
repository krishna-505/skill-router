"""
Evaluation runner for skill-router matching accuracy.
Computes precision, recall, false positive rate, confusion rate, and latency.

Usage:
    python run_eval.py [--index PATH] [--verbose]
"""

import json
import os
import sys
import time
import argparse
from pathlib import Path
from collections import defaultdict

# Add scripts dir to path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from config import TRIGGER_THRESHOLD
from matcher import match_skills, select_best, detect_language

EVAL_DIR = Path(__file__).resolve().parent
TEST_CASES_PATH = EVAL_DIR / "test_cases.json"
RESULTS_DIR = EVAL_DIR / "results"

# Default cloud-skills index
DEFAULT_INDEX = Path(__file__).resolve().parent.parent.parent / "cloud-skills" / "index.json"


def load_test_cases() -> list:
    return json.loads(TEST_CASES_PATH.read_text(encoding="utf-8"))["test_cases"]


def load_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_single(prompt: str, skills: list) -> dict:
    """Run matching for a single prompt, return result with timing."""
    start = time.perf_counter()
    ranked = match_skills(prompt, skills)
    result = select_best(ranked)
    elapsed_ms = (time.perf_counter() - start) * 1000

    if result is None:
        return {
            "matched": None,
            "score": 0,
            "is_ambiguous": False,
            "runner_up": None,
            "runner_up_score": 0,
            "all_matches": [],
            "latency_ms": elapsed_ms,
        }

    best_skill, score, is_ambiguous = result
    runner_up = ranked[1] if len(ranked) > 1 else None

    return {
        "matched": best_skill["name"],
        "score": score,
        "is_ambiguous": is_ambiguous,
        "runner_up": runner_up[0]["name"] if runner_up else None,
        "runner_up_score": runner_up[1] if runner_up else 0,
        "all_matches": [(s["name"], sc) for s, sc in ranked[:5]],
        "latency_ms": elapsed_ms,
    }


def evaluate(test_cases: list, skills: list, verbose: bool = False) -> dict:
    """Run all test cases and compute metrics."""
    results = []
    latencies = []

    # Counters
    tp = 0  # true positive: expected match, correct match
    fp = 0  # false positive: expected no match, but matched something
    fn = 0  # false negative: expected match, but no match
    tn = 0  # true negative: expected no match, no match

    confusion_correct = 0  # confusion cases where primary expected matched
    confusion_alt = 0      # confusion cases where alt expected matched
    confusion_wrong = 0    # confusion cases where wrong skill matched
    confusion_none = 0     # confusion cases where nothing matched

    boundary_correct = 0
    boundary_wrong = 0
    boundary_none = 0

    category_results = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})

    for tc in test_cases:
        tc_id = tc["id"]
        prompt = tc["prompt"]
        expected = tc.get("expected")
        expected_alt = tc.get("expected_alt")
        tc_type = tc["type"]

        result = run_single(prompt, skills)
        matched = result["matched"]
        latencies.append(result["latency_ms"])

        # Determine correctness
        if tc_type == "positive":
            if matched == expected:
                tp += 1
                correct = True
                category_results[tc.get("category", "unknown")]["tp"] += 1
            elif matched is None:
                fn += 1
                correct = False
                category_results[tc.get("category", "unknown")]["fn"] += 1
            else:
                fp += 1
                correct = False
                category_results[tc.get("category", "unknown")]["fp"] += 1

        elif tc_type == "negative":
            if matched is None:
                tn += 1
                correct = True
            else:
                fp += 1
                correct = False

        elif tc_type == "confusion":
            if matched == expected:
                confusion_correct += 1
                correct = True
            elif matched == expected_alt:
                confusion_alt += 1
                correct = True  # acceptable
            elif matched is None:
                confusion_none += 1
                correct = False
            else:
                confusion_wrong += 1
                correct = False

        elif tc_type == "boundary":
            if matched == expected:
                boundary_correct += 1
                correct = True
            elif matched is None:
                boundary_none += 1
                correct = False
            else:
                boundary_wrong += 1
                correct = False

        else:
            correct = matched == expected

        entry = {
            "id": tc_id,
            "type": tc_type,
            "prompt": prompt[:80],
            "expected": expected,
            "matched": matched,
            "score": round(result["score"], 1),
            "correct": correct,
            "is_ambiguous": result["is_ambiguous"],
            "runner_up": result["runner_up"],
            "latency_ms": round(result["latency_ms"], 1),
            "notes": tc.get("notes", ""),
        }
        results.append(entry)

        if verbose:
            status = "OK" if correct else "FAIL"
            print(f"  [{status}] #{tc_id} ({tc_type}) \"{prompt[:50]}...\"")
            if not correct:
                print(f"       expected={expected}, got={matched} (score={result['score']:.1f})")
                if result["all_matches"]:
                    top3 = result["all_matches"][:3]
                    print(f"       top matches: {top3}")

    # Compute metrics
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)

    # Positive test metrics
    positive_count = sum(1 for tc in test_cases if tc["type"] == "positive")
    negative_count = sum(1 for tc in test_cases if tc["type"] == "negative")
    confusion_count = sum(1 for tc in test_cases if tc["type"] == "confusion")
    boundary_count = sum(1 for tc in test_cases if tc["type"] == "boundary")

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    false_positive_rate = fp / negative_count if negative_count > 0 else 0
    confusion_rate = confusion_wrong / confusion_count if confusion_count > 0 else 0

    metrics = {
        "summary": {
            "total_cases": len(test_cases),
            "positive_cases": positive_count,
            "negative_cases": negative_count,
            "confusion_cases": confusion_count,
            "boundary_cases": boundary_count,
        },
        "accuracy": {
            "precision": round(precision * 100, 1),
            "recall": round(recall * 100, 1),
            "f1_score": round(f1 * 100, 1),
            "false_positive_rate": round(false_positive_rate * 100, 1),
            "confusion_rate": round(confusion_rate * 100, 1),
        },
        "positive_results": {
            "true_positive": tp,
            "false_negative": fn,
            "wrong_match": fp - (sum(1 for tc in test_cases if tc["type"] == "negative") - tn),
        },
        "negative_results": {
            "true_negative": tn,
            "false_positive": sum(1 for r in results if r["type"] == "negative" and not r["correct"]),
        },
        "confusion_results": {
            "correct_primary": confusion_correct,
            "correct_alt": confusion_alt,
            "wrong_match": confusion_wrong,
            "no_match": confusion_none,
            "acceptable_rate": round(
                (confusion_correct + confusion_alt) / confusion_count * 100, 1
            ) if confusion_count > 0 else 0,
        },
        "boundary_results": {
            "correct": boundary_correct,
            "wrong_match": boundary_wrong,
            "no_match": boundary_none,
            "trigger_rate": round(
                boundary_correct / boundary_count * 100, 1
            ) if boundary_count > 0 else 0,
        },
        "latency": {
            "p50_ms": round(latencies_sorted[n // 2], 1) if n > 0 else 0,
            "p95_ms": round(latencies_sorted[int(n * 0.95)] if n > 0 else 0, 1),
            "p99_ms": round(latencies_sorted[int(n * 0.99)] if n > 0 else 0, 1),
            "max_ms": round(max(latencies) if latencies else 0, 1),
            "mean_ms": round(sum(latencies) / n if n > 0 else 0, 1),
        },
        "per_category": {
            cat: {
                "precision": round(v["tp"] / (v["tp"] + v["fp"]) * 100, 1) if (v["tp"] + v["fp"]) > 0 else 0,
                "recall": round(v["tp"] / (v["tp"] + v["fn"]) * 100, 1) if (v["tp"] + v["fn"]) > 0 else 0,
            }
            for cat, v in sorted(category_results.items())
        },
        "threshold_used": TRIGGER_THRESHOLD,
    }

    return {"metrics": metrics, "results": results}


def print_report(metrics: dict):
    """Print a formatted evaluation report."""
    m = metrics
    print("\n" + "=" * 60)
    print("  SKILL-ROUTER EVALUATION REPORT")
    print("=" * 60)

    print(f"\nTest Cases: {m['summary']['total_cases']}")
    print(f"  Positive: {m['summary']['positive_cases']} | "
          f"Negative: {m['summary']['negative_cases']} | "
          f"Confusion: {m['summary']['confusion_cases']} | "
          f"Boundary: {m['summary']['boundary_cases']}")

    print(f"\n--- Core Metrics ---")
    print(f"  Precision:          {m['accuracy']['precision']}%")
    print(f"  Recall:             {m['accuracy']['recall']}%")
    print(f"  F1 Score:           {m['accuracy']['f1_score']}%")
    print(f"  False Positive Rate:{m['accuracy']['false_positive_rate']}%")
    print(f"  Confusion Rate:     {m['accuracy']['confusion_rate']}%")

    print(f"\n--- Positive Cases (should trigger correct skill) ---")
    p = m["positive_results"]
    print(f"  True Positive:  {p['true_positive']}")
    print(f"  False Negative: {p['false_negative']} (missed)")
    print(f"  Wrong Match:    {p['wrong_match']}")

    print(f"\n--- Negative Cases (should NOT trigger) ---")
    n = m["negative_results"]
    print(f"  True Negative:  {n['true_negative']}")
    print(f"  False Positive: {n['false_positive']} (wrongly triggered)")

    print(f"\n--- Confusion Cases (ambiguous prompts) ---")
    c = m["confusion_results"]
    print(f"  Correct (primary): {c['correct_primary']}")
    print(f"  Correct (alt):     {c['correct_alt']}")
    print(f"  Wrong:             {c['wrong_match']}")
    print(f"  No match:          {c['no_match']}")
    print(f"  Acceptable Rate:   {c['acceptable_rate']}%")

    print(f"\n--- Boundary Cases (hard-to-trigger) ---")
    b = m["boundary_results"]
    print(f"  Correct:   {b['correct']}")
    print(f"  Wrong:     {b['wrong_match']}")
    print(f"  No match:  {b['no_match']}")
    print(f"  Trigger Rate: {b['trigger_rate']}%")

    print(f"\n--- Latency ---")
    l = m["latency"]
    print(f"  P50:  {l['p50_ms']}ms")
    print(f"  P95:  {l['p95_ms']}ms")
    print(f"  P99:  {l['p99_ms']}ms")
    print(f"  Max:  {l['max_ms']}ms")
    print(f"  Mean: {l['mean_ms']}ms")

    if m.get("per_category"):
        print(f"\n--- Per Category ---")
        for cat, vals in m["per_category"].items():
            print(f"  {cat:12s}: precision={vals['precision']}%, recall={vals['recall']}%")

    print("\n" + "=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Run skill-router evaluation")
    parser.add_argument("--index", type=str, default=str(DEFAULT_INDEX), help="Path to index.json")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each test case result")
    parser.add_argument("--save", "-s", action="store_true", help="Save results to file")
    args = parser.parse_args()

    print("Loading test cases...")
    test_cases = load_test_cases()
    print(f"  {len(test_cases)} test cases loaded")

    print("Loading index...")
    index = load_index(Path(args.index))
    skills = index.get("skills", [])
    print(f"  {len(skills)} skills in index")

    print(f"\nRunning evaluation (threshold={TRIGGER_THRESHOLD})...")
    output = evaluate(test_cases, skills, verbose=args.verbose)

    print_report(output["metrics"])

    if args.save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        result_path = RESULTS_DIR / f"eval_{timestamp}.json"
        result_path.write_text(
            json.dumps(output, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nResults saved to {result_path}")

    # Print failures for debugging
    failures = [r for r in output["results"] if not r["correct"]]
    if failures:
        print(f"\n--- {len(failures)} Failed Cases ---")
        for f in failures:
            print(f"  #{f['id']} ({f['type']}): \"{f['prompt']}\"")
            print(f"    expected={f['expected']}, got={f['matched']} (score={f['score']})")


if __name__ == "__main__":
    main()
