"""
Baseline installer: converts 100 cloud-skills into Claude Code native format.

Claude Code's native skill system uses SKILL.md files with YAML frontmatter
containing `name` and `description`. The system has a description budget of
~2% of the context window — skills exceeding the budget are invisible to Claude.

This script:
1. Reads cloud-skills metadata.yaml + SKILL.md for each skill
2. Generates Claude Code native format files (YAML frontmatter .md)
3. Writes to a target directory (default: ./baseline-skills/)
4. Outputs budget analysis showing visible vs hidden skills

Usage:
    python setup_baseline.py [--dry-run] [--install] [--output-dir PATH]
"""

import json
import sys
import argparse
import shutil
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

EVAL_DIR = Path(__file__).resolve().parent
CLOUD_SKILLS_DIR = EVAL_DIR.parent.parent / "cloud-skills"
DEFAULT_OUTPUT_DIR = EVAL_DIR / "baseline-skills"
DEFAULT_INDEX = CLOUD_SKILLS_DIR / "index.json"
CLAUDE_SKILLS_DIR = Path.home() / ".claude" / "skills"

CONTEXT_WINDOW = 128000
BUDGET_RATIO = 0.02
BUDGET_CHARS = int(CONTEXT_WINDOW * BUDGET_RATIO)


def load_metadata_yaml(skill_dir: Path) -> dict:
    """Load metadata.yaml from a skill directory."""
    meta_path = skill_dir / "metadata.yaml"
    if not meta_path.exists():
        return {}
    text = meta_path.read_text(encoding="utf-8")
    if yaml:
        return yaml.safe_load(text) or {}
    # Fallback: simple key-value parsing for the fields we need
    result = {}
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("display_name:"):
            result["display_name"] = line.split(":", 1)[1].strip().strip('"')
        elif line.startswith("short_description:"):
            result["short_description"] = line.split(":", 1)[1].strip().strip('"')
    return result


def load_skill_content(skill_dir: Path) -> str:
    """Load SKILL.md content."""
    skill_path = skill_dir / "SKILL.md"
    if skill_path.exists():
        return skill_path.read_text(encoding="utf-8")
    return ""


def generate_native_skill(name: str, display_name: str, description: str, content: str) -> str:
    """Generate Claude Code native skill format: YAML frontmatter + content."""
    lines = [
        "---",
        f"name: \"{display_name}\"",
        f"description: \"{description}\"",
        "---",
        "",
        content,
    ]
    return "\n".join(lines)


def discover_skills(cloud_skills_dir: Path) -> list:
    """Discover all skill directories under categories/."""
    categories_dir = cloud_skills_dir / "categories"
    if not categories_dir.exists():
        return []
    skills = []
    for cat_dir in sorted(categories_dir.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith("."):
            continue
        for skill_dir in sorted(cat_dir.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name.startswith("."):
                continue
            if (skill_dir / "SKILL.md").exists():
                skills.append({
                    "dir": skill_dir,
                    "name": skill_dir.name,
                    "category": cat_dir.name,
                })
    return skills


def analyze_budget(descriptions: list) -> dict:
    """Analyze which skills fit within the 2% description budget."""
    # Sort by description length ascending (shorter first = Claude Code packing order)
    sorted_descs = sorted(descriptions, key=lambda d: len(d["description_line"]))

    total_chars = sum(len(d["description_line"]) for d in sorted_descs)
    cumulative = 0
    visible = []
    hidden = []

    for d in sorted_descs:
        cumulative += len(d["description_line"])
        if cumulative <= BUDGET_CHARS:
            visible.append(d)
        else:
            hidden.append(d)

    return {
        "total_chars": total_chars,
        "budget_chars": BUDGET_CHARS,
        "overflow_ratio": round(total_chars / BUDGET_CHARS, 1),
        "visible": visible,
        "hidden": hidden,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate Claude Code baseline skills")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    parser.add_argument("--install", action="store_true", help="Install to ~/.claude/skills/")
    parser.add_argument("--output-dir", type=str, default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--index", type=str, default=str(DEFAULT_INDEX), help="Path to index.json (for metadata fallback)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if args.install:
        output_dir = CLAUDE_SKILLS_DIR

    # Load index as fallback for metadata
    index_skills = {}
    if Path(args.index).exists():
        index_data = json.loads(Path(args.index).read_text(encoding="utf-8"))
        for s in index_data.get("skills", []):
            index_skills[s["name"]] = s

    # Discover skills
    print("Discovering skills...")
    skill_dirs = discover_skills(CLOUD_SKILLS_DIR)
    print(f"  Found {len(skill_dirs)} skills in {CLOUD_SKILLS_DIR}")

    if not skill_dirs:
        print("ERROR: No skills found. Check cloud-skills directory path.")
        sys.exit(1)

    # Process each skill
    generated = []
    descriptions = []
    for sd in skill_dirs:
        name = sd["name"]
        category = sd["category"]
        skill_dir = sd["dir"]

        # Load metadata (prefer yaml, fallback to index.json)
        meta = load_metadata_yaml(skill_dir)
        idx_meta = index_skills.get(name, {})

        display_name = meta.get("display_name") or idx_meta.get("display_name", name)
        description = meta.get("short_description") or idx_meta.get("short_description", "")
        content = load_skill_content(skill_dir)

        native_content = generate_native_skill(name, display_name, description, content)
        description_line = f"{display_name}: {description}"

        generated.append({
            "name": name,
            "category": category,
            "display_name": display_name,
            "description": description,
            "description_line": description_line,
            "native_content": native_content,
            "content_chars": len(native_content),
        })

        descriptions.append({
            "name": name,
            "category": category,
            "description_line": description_line,
        })

    # Budget analysis
    budget = analyze_budget(descriptions)

    # Print summary
    print(f"\n{'=' * 60}")
    print(f"  BASELINE SKILL GENERATION REPORT")
    print(f"{'=' * 60}")
    print(f"\n  Generated: {len(generated)} skills in Claude Code native format")
    print(f"  Total description chars: {budget['total_chars']:,}")
    print(f"  Budget at 128K context:  {budget['budget_chars']:,} chars (2%)")
    print(f"  Overflow:                {budget['overflow_ratio']}x budget")
    print(f"  Skills within budget:    {len(budget['visible'])}/{len(generated)} ({len(budget['visible'])*100//len(generated)}%)")
    print(f"  Skills EXCLUDED:         {len(budget['hidden'])}/{len(generated)} ({len(budget['hidden'])*100//len(generated)}%)")

    print(f"\n  Visible skills ({len(budget['visible'])}):")
    for d in sorted(budget['visible'], key=lambda x: x['name']):
        print(f"    + {d['name']:30s} ({len(d['description_line']):3d} chars)")

    print(f"\n  Hidden skills ({len(budget['hidden'])}):")
    for d in sorted(budget['hidden'], key=lambda x: x['name']):
        print(f"    - {d['name']:30s} ({len(d['description_line']):3d} chars)")

    # Write files
    if args.dry_run:
        print(f"\n  [DRY RUN] Would write {len(generated)} files to: {output_dir}")
        print(f"\n  Sample output ({generated[0]['name']}):")
        print(f"  {'─' * 50}")
        # Show first 15 lines of first skill
        lines = generated[0]['native_content'].split('\n')[:15]
        for line in lines:
            print(f"    {line}")
        print(f"    ...")
    else:
        print(f"\n  Writing {len(generated)} files to: {output_dir}")
        output_dir.mkdir(parents=True, exist_ok=True)

        for g in generated:
            file_path = output_dir / f"{g['name']}.md"
            file_path.write_text(g['native_content'], encoding="utf-8")

        print(f"  Done! {len(generated)} skill files written.")

        if args.install:
            print(f"\n  Skills installed to: {CLAUDE_SKILLS_DIR}")
            print(f"  To uninstall: delete {CLAUDE_SKILLS_DIR}")
        else:
            print(f"\n  To install into Claude Code:")
            print(f"    python setup_baseline.py --install")
            print(f"  Or manually copy to: {CLAUDE_SKILLS_DIR}")

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    main()
