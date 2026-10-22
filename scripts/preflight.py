#!/usr/bin/env python3
"""Read-only baseline checks. Never calls a model, imports a runtime or installs."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys


def inspect(stage, runtime_entry=None):
    checks = [{"name": "python", "status": "pass" if sys.version_info >= (3, 10) else "fail",
               "detail": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"}]
    manual = ["Codex can read the authorized input and write a separate output directory."]
    if stage in ("2", "3", "all"):
        node = shutil.which("node")
        version = None
        if node:
            try:
                version = subprocess.run([node, "--version"], check=True, capture_output=True,
                                         text=True, timeout=5).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                version = None
        try:
            node_ok = int((version or "").lstrip("v").split(".")[0]) >= 20
        except ValueError:
            node_ok = False
        checks.append({"name": "node", "status": "pass" if node_ok else "fail", "detail": version})
        if runtime_entry:
            checks.append({"name": "runtime_entry_file", "status": "pass" if Path(runtime_entry).is_file() else "fail",
                           "detail": "File existence only; import, API and license remain unchecked."})
        manual.extend([
            "Locate the PPT runtime supplied by the current environment; verify permission for this use.",
            "Within permitted use, create/save/read a small probe using the real runtime API.",
            "Confirm image viewing and required fonts in this Codex session.",
        ])
    if stage in ("2", "all"):
        manual.append("Confirm a real built-in image generation tool and successful image file capture.")
    if stage in ("3", "all"):
        manual.extend([
            "Confirm seven distinct agent identities can be scheduled in stages.",
            "Confirm Microsoft PowerPoint open/edit/save/close/reopen evidence can be collected.",
        ])
    return {"status": "blocked" if any(c["status"] == "fail" for c in checks) else "manual_verification_required",
            "production_ready": False, "stage": stage, "checks": checks, "manual_checks": manual,
            "note": "This report does not certify tool access, runtime authorization or end-to-end operation."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("1", "2", "3", "all"), default="all")
    parser.add_argument("--runtime-entry", type=Path)
    args = parser.parse_args()
    report = inspect(args.stage, args.runtime_entry)
    print(json.dumps(report, indent=2))
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
