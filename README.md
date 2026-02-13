# skill-router

A Claude Code plugin that automatically routes user prompts to the most relevant skill from a cloud registry. Zero-config, zero-latency skill discovery.

## How It Works

```
User prompt → UserPromptSubmit Hook → matcher.py (pure text, no LLM)
  → Scores against 100 skills in index.json
  → Best match injected via systemMessage
  → Claude sees skill context + original prompt
```

## Architecture

- **matcher.py** - Multi-level scoring: trigger keywords (40%) → intent patterns (35%) → tag overlap (15%) → description overlap (10%)
- **cache_manager.py** - Local cache with TTL (index: 24h, skills: 7d), offline fallback
- **registry.py** - Abstracted data layer (GitHub/Local/future API)
- **injector.py** - Formats matched skill content for systemMessage injection
- **router.py** - Main entry point, reads stdin JSON, outputs systemMessage JSON

## Key Design Decisions

| Decision | Reason |
|----------|--------|
| Pure text matching, no LLM | P50 < 10ms latency, deterministic |
| Never exit(2) | Never blocks user input |
| Negative keywords | Prevents cross-skill confusion |
| Bilingual (EN/ZH) | Substring matching for Chinese, word-boundary for English |
| systemMessage injection | Cleanly injects context without modifying prompt |

## Evaluation Results (100 test cases)

| Metric | Score | Target |
|--------|-------|--------|
| Precision | 98.0% | > 90% |
| Recall | 100.0% | > 80% |
| F1 Score | 99.0% | - |
| False Positive Rate | 5.0% | < 5% |
| Boundary Trigger Rate | 66.7% | - |
| P50 Latency | 8.6ms | < 200ms |
| P95 Latency | 43ms | < 1s |

## Installation

```bash
claude --plugin-dir ./skill-router
```

## Development

```bash
# Run evaluation
py eval/run_eval.py --verbose --save

# Validate skills
py ../cloud-skills/scripts/validate_skills.py

# Rebuild index
py ../cloud-skills/scripts/build_index.py
```
