"""
Main entry point for the skill-router hook.
Reads UserPromptSubmit JSON from stdin, runs matching, outputs systemMessage JSON.

Exit codes:
  0 - Success (with or without injection)
  Never exits with 2 (never blocks user input)
"""

import json
import sys
import os
import time

# Add scripts dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DEBUG
from index_loader import load_index, get_skills_list
from matcher import match_skills, select_best
from injector import load_skill_content, format_injection


def main():
    start_time = time.time()

    try:
        # Read hook input from stdin (force UTF-8 for Windows compatibility)
        raw = sys.stdin.buffer.read().decode("utf-8")
        if not raw.strip():
            sys.exit(0)

        hook_input = json.loads(raw)
        prompt = hook_input.get("prompt", "")

        if not prompt or not prompt.strip():
            sys.exit(0)

        # Skip very short prompts (likely just commands or typos)
        if len(prompt.strip()) < 5:
            sys.exit(0)

        # Load index
        index = load_index()
        if index is None:
            if DEBUG:
                print("[skill-router][debug] no index available, skipping", file=sys.stderr)
            sys.exit(0)

        skills = get_skills_list(index)
        if not skills:
            sys.exit(0)

        # Match
        ranked = match_skills(prompt, skills)
        result = select_best(ranked)

        if result is None:
            if DEBUG:
                elapsed = (time.time() - start_time) * 1000
                print(f"[skill-router][debug] no match above threshold ({elapsed:.0f}ms)", file=sys.stderr)
            sys.exit(0)

        best_skill, score, is_ambiguous = result

        # Load skill content
        content = load_skill_content(best_skill)
        if content is None:
            if DEBUG:
                print(f"[skill-router][debug] could not load content for '{best_skill['name']}'", file=sys.stderr)
            sys.exit(0)

        # Format injection
        runner_up = None
        if is_ambiguous and len(ranked) > 1:
            runner_up = (ranked[1][0], ranked[1][1])

        injection = format_injection(
            skill=best_skill,
            content=content,
            score=score,
            is_ambiguous=is_ambiguous,
            runner_up=runner_up,
        )

        # Output the systemMessage (force UTF-8 for Windows compatibility)
        output = {"systemMessage": injection}
        sys.stdout.buffer.write(json.dumps(output, ensure_ascii=False).encode("utf-8"))

        if DEBUG:
            elapsed = (time.time() - start_time) * 1000
            print(
                f"[skill-router][debug] injected '{best_skill['name']}' "
                f"(score={score:.1f}, ambiguous={is_ambiguous}, {elapsed:.0f}ms)",
                file=sys.stderr,
            )

    except Exception as e:
        # Never crash, never block user input
        if DEBUG:
            print(f"[skill-router][debug] error: {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
