#!/usr/bin/env python3
"""Install only this package's three skills. Plan by default; never overwrite."""
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("ppt-storyboard", "ppt-image-deck", "ppt-editable-rebuild")


def install(destination, apply=False, source=ROOT / "skills"):
    destination = Path(destination).expanduser().absolute()
    source = Path(source).resolve()
    for name in NAMES:
        skill = source / name
        if not (skill / "SKILL.md").is_file():
            raise ValueError(f"Missing skill entry: {name}")
        if skill.is_symlink() or any(p.is_symlink() for p in skill.rglob("*")):
            raise ValueError(f"Symlinks are not permitted in skill: {name}")
        target = destination / name
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Existing skill kept unchanged: {target}")
    if not apply:
        return {"status": "plan", "destination": str(destination), "skills": list(NAMES)}
    destination.mkdir(parents=True, exist_ok=True)
    # Stage copies before creating targets. mkdir is exclusive even if a second
    # installer races us; only directories created by this call are rolled back.
    created = []
    with tempfile.TemporaryDirectory(prefix=".ppt-install-", dir=destination) as temp:
        staged = Path(temp)
        for name in NAMES:
            shutil.copytree(source / name, staged / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
            for notice in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
                target_notice = staged / name / notice
                if target_notice.exists():
                    raise ValueError(f"Skill has a conflicting package notice: {name}/{notice}")
                shutil.copyfile(ROOT / notice, target_notice)
        try:
            for name in NAMES:
                target = destination / name
                target.mkdir()
                created.append(target)
                shutil.copytree(staged / name, target, dirs_exist_ok=True)
        except Exception:
            for target in reversed(created):
                shutil.rmtree(target)
            raise
    return {"status": "installed", "destination": str(destination), "skills": list(NAMES)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills"
    parser.add_argument("--destination", type=Path, default=default)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        import json
        print(json.dumps(install(args.destination, args.apply), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
