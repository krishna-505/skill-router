"""
Token cost comparison between skill-router (方案A) and full local install (方案B).

Analyzes:
- 方案B idle cost: all 100 skill descriptions always in context
- 方案B budget truncation: which skills fit within 2% context budget
- 方案A per-trigger cost: injection cost when a skill is matched
- Cumulative session cost comparison

Usage:
    python token_analysis.py [--index PATH] [--context-window 128000]
"""

import json
import sys
import argparse
from pathlib import Path
from collections import defaultdict

# Add scripts dir to path
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from config import MAX_SKILL_CONTENT_CHARS
from matcher import match_skills, select_best

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_INDEX = Path(__file__).resolve().parent.parent.parent / "cloud-skills" / "index.json"
TEST_CASES_PATH = EVAL_DIR / "test_cases.json"


def estimate_tokens(text: str) -> int:
    """Estimate token count. ~4 chars per token for English, ~2 for Chinese."""
    en_chars = sum(1 for c in text if c.isascii())
    zh_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - en_chars - zh_chars
    return int(en_chars / 4 + zh_chars / 1.5 + other_chars / 3)


def build_description_entry(skill: dict) -> str:
    """Build the description string as Claude Code would see it for a skill."""
    name = skill.get("display_name", skill["name"])
    desc = skill.get("short_description", "")
    return f"{name}: {desc}"


def analyze_budget(skills: list, context_window: int) -> dict:
    """Analyze which skills fit within Claude Code's 2% description budget."""
    budget_chars = int(context_window * 0.02)

    # Build description entries and sort by length (shorter first = more can fit)
    entries = []
    for skill in skills:
        entry_text = build_description_entry(skill)
        entries.append({
            "name": skill["name"],
            "category": skill.get("category", "unknown"),
            "text": entry_text,
            "chars": len(entry_text),
            "tokens": estimate_tokens(entry_text),
        })

    # Sort by char length ascending (Claude Code fills budget with shorter descriptions first)
    entries.sort(key=lambda e: e["chars"])

    total_chars = sum(e["chars"] for e in entries)
    total_tokens = sum(e["tokens"] for e in entries)

    # Determine which fit within budget
    cumulative = 0
    visible = []
    hidden = []
    for e in entries:
        cumulative += e["chars"]
        if cumulative <= budget_chars:
            visible.append(e)
        else:
            hidden.append(e)

    # Category breakdown of hidden skills
    hidden_by_category = defaultdict(int)
    for e in hidden:
        hidden_by_category[e["category"]] += 1

    visible_by_category = defaultdict(int)
    for e in visible:
        visible_by_category[e["category"]] += 1

    return {
        "budget_chars": budget_chars,
        "total_chars": total_chars,
        "total_tokens": total_tokens,
        "total_skills": len(entries),
        "visible_count": len(visible),
        "hidden_count": len(hidden),
        "visible_skills": [e["name"] for e in visible],
        "hidden_skills": [e["name"] for e in hidden],
        "visible_by_category": dict(visible_by_category),
        "hidden_by_category": dict(hidden_by_category),
        "overflow_ratio": total_chars / budget_chars if budget_chars > 0 else 0,
    }


def analyze_plan_a(skills: list, test_cases: list) -> dict:
    """Analyze 方案A (skill-router) token costs."""
    injection_tokens = []
    triggered_count = 0

    for tc in test_cases:
        prompt = tc["prompt"]
        ranked = match_skills(prompt, skills)
        result = select_best(ranked)

        if result is not None:
            triggered_count += 1
            skill, score, _ = result
            # Injection = skill content (SKILL.md) capped at MAX_SKILL_CONTENT_CHARS
            content_size = skill.get("content_size_bytes", 500)
            # Approximate: content_size_bytes ≈ char count for UTF-8 English text
            injection_chars = min(content_size, MAX_SKILL_CONTENT_CHARS)
            injection_tokens.append(estimate_tokens("x" * injection_chars))
        else:
            injection_tokens.append(0)

    avg_injection = sum(injection_tokens) / len(injection_tokens) if injection_tokens else 0
    trigger_rate = triggered_count / len(test_cases) if test_cases else 0

    return {
        "idle_cost_tokens": 0,
        "avg_injection_tokens": round(avg_injection),
        "max_injection_tokens": max(injection_tokens) if injection_tokens else 0,
        "trigger_rate": round(trigger_rate * 100, 1),
        "triggered_count": triggered_count,
        "total_cases": len(test_cases),
    }


def print_report(budget_analysis: dict, plan_a: dict, context_window: int, session_turns: int):
    """Print formatted comparison report."""
    print()
    print("=" * 64)
    print("  TOKEN COST COMPARISON: skill-router vs Full Local Install")
    print("=" * 64)

    print(f"\n  Context Window: {context_window:,} tokens")
    print(f"  Description Budget (2%): {budget_analysis['budget_chars']:,} chars")
    print(f"  Session Length: {session_turns} turns")

    # 方案B
    print(f"\n{'─' * 64}")
    print(f"  方案B (全部本地安装 / Full Local Install)")
    print(f"{'─' * 64}")
    print(f"  Total skills:                    {budget_analysis['total_skills']}")
    print(f"  Total description chars:         {budget_analysis['total_chars']:,}")
    print(f"  Total description tokens:        {budget_analysis['total_tokens']:,}")
    print(f"  Budget capacity:                 {budget_analysis['budget_chars']:,} chars")
    print(f"  Overflow ratio:                  {budget_analysis['overflow_ratio']:.1f}x")
    print(f"  Skills VISIBLE within budget:    {budget_analysis['visible_count']}/{budget_analysis['total_skills']} ({budget_analysis['visible_count']*100//budget_analysis['total_skills']}%)")
    print(f"  Skills HIDDEN (over budget):     {budget_analysis['hidden_count']}/{budget_analysis['total_skills']} ({budget_analysis['hidden_count']*100//budget_analysis['total_skills']}%)")
    print(f"  Per-turn cost (always present):  {budget_analysis['total_tokens']:,} tokens")
    b_session_cost = budget_analysis['total_tokens'] * session_turns
    print(f"  Per-session cost ({session_turns} turns):      {b_session_cost:,} tokens")

    print(f"\n  Hidden skills by category:")
    for cat, count in sorted(budget_analysis['hidden_by_category'].items()):
        total_in_cat = budget_analysis['visible_by_category'].get(cat, 0) + count
        print(f"    {cat:15s}: {count}/{total_in_cat} hidden")

    # 方案A
    print(f"\n{'─' * 64}")
    print(f"  方案A (skill-router / On-Demand Injection)")
    print(f"{'─' * 64}")
    print(f"  Idle cost:                       {plan_a['idle_cost_tokens']} tokens")
    print(f"  Skills routable:                 {budget_analysis['total_skills']}/100 (100%)")
    print(f"  Trigger rate on test set:        {plan_a['trigger_rate']}%")
    print(f"  Avg injection cost (triggered):  {plan_a['avg_injection_tokens']:,} tokens")
    print(f"  Max injection cost:              {plan_a['max_injection_tokens']:,} tokens")
    avg_per_turn = round(plan_a['avg_injection_tokens'] * plan_a['trigger_rate'] / 100)
    print(f"  Avg per-turn cost:               {avg_per_turn:,} tokens")
    a_session_cost = avg_per_turn * session_turns
    print(f"  Per-session cost ({session_turns} turns):      {a_session_cost:,} tokens")

    # Comparison
    print(f"\n{'─' * 64}")
    print(f"  COMPARISON SUMMARY")
    print(f"{'─' * 64}")
    savings = (1 - a_session_cost / b_session_cost) * 100 if b_session_cost > 0 else 0
    print(f"  {'':30s} {'方案A':>10s}  {'方案B':>10s}  {'差异':>10s}")
    print(f"  {'Routable skills':30s} {'100':>10s}  {budget_analysis['visible_count']:>10d}  {'+' + str(budget_analysis['hidden_count']):>10s}")
    print(f"  {'Idle cost (tokens)':30s} {'0':>10s}  {budget_analysis['total_tokens']:>10,}  {'-' + str(budget_analysis['total_tokens']):>10s}")
    print(f"  {'Avg per-turn cost':30s} {avg_per_turn:>10,}  {budget_analysis['total_tokens']:>10,}  {'-' + str(budget_analysis['total_tokens'] - avg_per_turn):>10s}")
    print(f"  {'Session cost (' + str(session_turns) + ' turns)':30s} {a_session_cost:>10,}  {b_session_cost:>10,}  {'-' + str(b_session_cost - a_session_cost):>10s}")
    print(f"  {'Token savings':30s} {'':>10s}  {'':>10s}  {savings:>9.1f}%")

    print(f"\n  方案A saves {savings:.1f}% tokens vs 方案B")
    print("=" * 64)


def main():
    parser = argparse.ArgumentParser(description="Token cost comparison analysis")
    parser.add_argument("--index", type=str, default=str(DEFAULT_INDEX), help="Path to index.json")
    parser.add_argument("--context-window", type=int, default=128000, help="Context window size in tokens")
    parser.add_argument("--session-turns", type=int, default=30, help="Assumed turns per session")
    args = parser.parse_args()

    print("Loading index...")
    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    skills = index.get("skills", [])
    print(f"  {len(skills)} skills loaded")

    print("Loading test cases...")
    test_cases = json.loads(TEST_CASES_PATH.read_text(encoding="utf-8"))["test_cases"]
    print(f"  {len(test_cases)} test cases loaded")

    print("\nAnalyzing 方案B budget...")
    budget_analysis = analyze_budget(skills, args.context_window)

    print("Analyzing 方案A injection costs...")
    plan_a = analyze_plan_a(skills, test_cases)

    print_report(budget_analysis, plan_a, args.context_window, args.session_turns)

    # Print visible/hidden skill lists
    print(f"\n--- Visible Skills ({budget_analysis['visible_count']}) ---")
    for name in sorted(budget_analysis['visible_skills']):
        print(f"  + {name}")

    print(f"\n--- Hidden Skills ({budget_analysis['hidden_count']}) ---")
    for name in sorted(budget_analysis['hidden_skills']):
        print(f"  - {name}")


if __name__ == "__main__":
    main()
