#!/usr/bin/env python3
"""Bounded release-content audit. Findings show rules and files, never secret values."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", "__pycache__", ".DS_Store"}
FORBIDDEN = {"node_modules", ".env", ".venv", "outputs", "private", "work"}
SUFFIXES = {".md", ".py", ".json", ".mjs", ".yaml", ".yml", ".txt"}
NAMES = {"LICENSE", ".gitignore"}
RULES = {
    "personal_absolute_path": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
    "windows_user_path": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9_.-]+"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    "api_secret": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "private_network": re.compile(r"\b(?:10|192\.168|172\.(?:1[6-9]|2[0-9]|3[01]))(?:\.\d{1,3}){2,3}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\b"),
}


def audit(root, tracked=False, deny_terms=()):
    root = Path(root).resolve()
    if tracked:
        listing = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True).stdout
        files = [root / name.decode("utf-8") for name in listing.split(b"\0") if name]
    else:
        files = [p for p in root.rglob("*") if p.is_file() or p.is_symlink()]
    findings, count, size = [], 0, 0
    for path in sorted(files):
        rel = path.relative_to(root)
        if any(part in IGNORED for part in rel.parts):
            continue
        count += 1
        if path.is_symlink():
            findings.append({"file": str(rel), "rule": "symlink"})
            continue
        if any(part in FORBIDDEN or part.startswith(".env.") for part in rel.parts):
            findings.append({"file": str(rel), "rule": "private_or_dependency_directory"})
        if path.suffix not in SUFFIXES and path.name not in NAMES:
            findings.append({"file": str(rel), "rule": "unreviewed_file_type"})
            continue
        try:
            data = path.read_bytes()
            size += len(data)
            if len(data) > 1_000_000:
                findings.append({"file": str(rel), "rule": "unexpected_large_file"})
            body = data.decode("utf-8")
        except (OSError, UnicodeError):
            findings.append({"file": str(rel), "rule": "unreadable_text"})
            continue
        for rule, pattern in RULES.items():
            if pattern.search(body):
                findings.append({"file": str(rel), "rule": rule})
        for index, term in enumerate(deny_terms):
            if term and (term.casefold() in body.casefold() or term.casefold() in str(rel).casefold()):
                findings.append({"file": str(rel), "rule": f"private_term_{index + 1}"})
    return {"status": "pass" if not findings and count else "fail", "files": count,
            "bytes": size, "findings": findings,
            "scope": "Text and path patterns only; manual content and rights review still required."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tracked", action="store_true")
    parser.add_argument("--deny-terms-file", type=Path)
    args = parser.parse_args()
    terms = args.deny_terms_file.read_text(encoding="utf-8").splitlines() if args.deny_terms_file else []
    report = audit(args.root, args.tracked, terms)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
