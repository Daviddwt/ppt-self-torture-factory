#!/usr/bin/env python3
"""Run offline package checks; no model, private PPT runtime or Office invocation."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def structure():
    expected = {"ppt-storyboard", "ppt-image-deck", "ppt-editable-rebuild"}
    skills = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}
    if skills != expected:
        raise ValueError("Expected exactly the three published skills")
    for name in sorted(skills):
        text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError(f"Invalid skill frontmatter: {name}")
        front = text.split("---", 2)[1]
        if not re.search(rf"^name: {re.escape(name)}$", front, re.M):
            raise ValueError(f"Skill name mismatch: {name}")
        if not re.search(r"^description: .+", front, re.M):
            raise ValueError(f"Missing skill description: {name}")
    for path in ROOT.rglob("*.json"):
        if ".git" not in path.parts:
            json.loads(path.read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    overlay = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    for key in ("name", "version", "license"):
        if manifest[key] != overlay[key]:
            raise ValueError(f"Plugin manifest mismatch: {key}")
    for path in ROOT.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)), "exec")
    # Check explicit relative Markdown links. Remote URLs and page anchors stay remote.
    for path in ROOT.rglob("*.md"):
        for target in re.findall(r"\]\(([^\s)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#") or target.startswith("mailto:"):
                continue
            link = unquote(target.split("#", 1)[0])
            if link and not (path.parent / link).exists():
                raise ValueError(f"Broken link in {path.relative_to(ROOT)}: {target}")
    print("PASS: package structure, Python syntax, JSON and local documentation links", flush=True)


def run(command):
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        raise RuntimeError(f"Check failed: {' '.join(str(p) for p in command)}")


def main():
    try:
        structure()
        run([sys.executable, "scripts/audit_public.py"])
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        for folder in sorted((ROOT / "skills").glob("*/tests")):
            if list(folder.glob("test_*.py")):
                run([sys.executable, "-m", "unittest", "discover", "-s", str(folder.relative_to(ROOT)), "-v"])
        run([sys.executable, "skills/ppt-storyboard/scripts/validate_script.py",
             "skills/ppt-storyboard/examples/fictional-script.json"])
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js 20+ is required for the offline JavaScript checks")
        for path in sorted(ROOT.rglob("*.mjs")):
            run([node, "--check", str(path.relative_to(ROOT))])
        tests = sorted((ROOT / "skills").glob("*/tests/*.test.mjs"))
        if not tests:
            raise RuntimeError("Missing image-deck offline tests")
        run([node, "--test", *(str(p.relative_to(ROOT)) for p in tests)])
        print("PASS: offline release checks. Image generation, real PPT runtime and PowerPoint remain unverified.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
