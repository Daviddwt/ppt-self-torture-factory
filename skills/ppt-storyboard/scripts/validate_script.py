#!/usr/bin/env python3
"""Validate the portable storyboard 1.0 contract without external dependencies."""

import argparse
import json
import re
import sys
from pathlib import Path

ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def validate(data):
    """Return field errors; extra fields are preserved and ignored."""
    errors = []

    def nonempty(value, location):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{location}: expected a non-empty string")
            return False
        return True

    def strings(value, location):
        if not isinstance(value, list):
            errors.append(f"{location}: expected an array of strings")
            return
        for index, item in enumerate(value):
            nonempty(item, f"{location}[{index}]")

    if not isinstance(data, dict):
        return ["root: expected an object"]
    if data.get("schema_version") != "1.0":
        errors.append('schema_version: expected "1.0"')
    nonempty(data.get("title"), "title")
    canvas = data.get("canvas")
    if not isinstance(canvas, dict):
        errors.append("canvas: expected an object")
    else:
        for dimension in ("width", "height"):
            value = canvas.get(dimension)
            if type(value) is not int or not 1 <= value <= 16384:
                errors.append(f"canvas.{dimension}: expected an integer from 1 to 16384")
    slides = data.get("slides")
    if not isinstance(slides, list) or not 1 <= len(slides) <= 500:
        errors.append("slides: expected an array containing 1 to 500 slides")
        return errors
    seen = set()
    for index, slide in enumerate(slides):
        location = f"slides[{index}]"
        if not isinstance(slide, dict):
            errors.append(f"{location}: expected an object")
            continue
        identifier = slide.get("id")
        if nonempty(identifier, f"{location}.id"):
            if not ID_PATTERN.fullmatch(identifier):
                errors.append(f"{location}.id: use 1-64 ASCII letters, digits, "
                              "hyphens or underscores; start with a letter or digit")
            folded = identifier.casefold()
            if folded in seen:
                errors.append(f"{location}.id: duplicate page id (case-insensitive)")
            seen.add(folded)
        for field in ("title", "key_message", "image_prompt"):
            nonempty(slide.get(field), f"{location}.{field}")
        if not isinstance(slide.get("speaker_notes"), str):
            errors.append(f"{location}.speaker_notes: expected a string")
        for field in ("on_slide_text", "source_refs"):
            strings(slide.get(field), f"{location}.{field}")
    return errors


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains a duplicate object key")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("JSON contains a non-finite number")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script", type=Path, help="Path to script.json")
    args = parser.parse_args()
    try:
        with args.script.open(encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=unique_object,
                             parse_constant=reject_constant)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f"ERROR: cannot read valid JSON ({type(exc).__name__})", file=sys.stderr)
        return 1
    errors = validate(data)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"PASS: storyboard 1.0 structure; {len(data['slides'])} slides. "
          "Content and user approval require separate review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
