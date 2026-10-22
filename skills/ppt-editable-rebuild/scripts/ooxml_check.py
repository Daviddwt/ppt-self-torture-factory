#!/usr/bin/env python3
"""Bounded, read-only PPTX structure inspection. Never grants release approval."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import sys
import zipfile
import zlib
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
}
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_PART = 64 * 1024 * 1024
MAX_ENTRIES = 20_000
REL_BASE = NS["r"] + "/"


class InspectionError(ValueError):
    """Unsupported or invalid input, without a broader OOXML validity claim."""


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def check_member_name(name: str) -> None:
    # Refuse ambiguous encoded names rather than attempting to extract/normalize them.
    if not name or "\\" in name or "\x00" in name or unquote(name) != name:
        raise InspectionError("unsafe_or_encoded_zip_path")
    bare = name[:-1] if name.endswith("/") else name
    if bare.startswith("/") or ":" in bare or any(x in ("", ".", "..") for x in bare.split("/")):
        raise InspectionError("unsafe_zip_path")


def parse_xml(data: bytes) -> ET.Element:
    # Also reject UTF-16/32 declarations by removing NUL bytes before scanning.
    probe = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in probe or b"<!ENTITY" in probe:
        raise InspectionError("xml_dtd_or_entity_forbidden")
    return ET.fromstring(data)


def relationship_owner(part: str) -> str:
    if part == "_rels/.rels":
        return ""
    match = re.fullmatch(r"(.*/)?_rels/([^/]+)\.rels", part)
    if not match:
        raise InspectionError("invalid_relationship_part_path")
    return (match.group(1) or "") + match.group(2)


def resolve_target(owner: str, target: str) -> str:
    decoded = unquote(target)
    parsed = urlsplit(decoded)
    if not decoded or "\\" in decoded or "\x00" in decoded or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise InspectionError("unsafe_or_unsupported_relationship_target")
    if unquote(decoded) != decoded:
        raise InspectionError("nested_encoded_relationship_target")
    # Package-root paths are legal relationship targets; archive entry paths are relative.
    combined = decoded.lstrip("/") if decoded.startswith("/") else posixpath.join(posixpath.dirname(owner), decoded)
    normalized = posixpath.normpath(combined)
    if normalized in (".", "..") or normalized.startswith("../") or ":" in normalized:
        raise InspectionError("relationship_escapes_package")
    check_member_name(normalized)
    return normalized


def union_area(rectangles: list[tuple[int, int, int, int]]) -> int:
    """Exact union of axis-aligned rectangles; not visible pixel coverage."""
    xs = sorted({x for r in rectangles for x in (r[0], r[2])})
    area = 0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((y1, y2) for x1, y1, x2, y2 in rectangles if x1 < right and x2 > left)
        height, last = 0, None
        for low, high in intervals:
            start = max(low, last) if last is not None else low
            height += max(0, high - start)
            last = max(last, high) if last is not None else high
        area += (right - left) * height
    return area


def slide_stats(root: ET.Element, width: int, height: int) -> dict:
    tree = root.find("p:cSld/p:spTree", NS)
    if tree is None:
        raise InspectionError("slide_shape_tree_missing")
    pictures = root.findall(".//p:pic", NS)
    direct_ids = {id(node) for node in tree.findall("p:pic", NS)}
    rectangles = []
    unknown = 0
    bad_extents = 0

    # Inspect explicit object extents, not the spTree's default group transform.
    # Lines have length but can have zero width OR height. Other objects need area.
    for tag, extent_path in (
        ("p:sp", "p:spPr/a:xfrm/a:ext"),
        ("p:cxnSp", "p:spPr/a:xfrm/a:ext"),
        ("p:pic", "p:spPr/a:xfrm/a:ext"),
        ("p:graphicFrame", "p:xfrm/a:ext"),
    ):
        for obj in root.findall(".//" + tag, NS):
            ext = obj.find(extent_path, NS)
            if ext is None:
                continue
            preset = obj.find("p:spPr/a:prstGeom", NS)
            is_line = tag == "p:cxnSp" or (
                tag == "p:sp" and preset is not None and preset.get("prst") == "line"
            )
            try:
                cx, cy = int(ext.attrib["cx"]), int(ext.attrib["cy"])
                invalid = cx < 0 or cy < 0 or (cx == 0 and cy == 0)
                if not is_line:
                    invalid = invalid or cx == 0 or cy == 0
                bad_extents += int(invalid)
            except (ValueError, KeyError):
                bad_extents += 1

    # Only check explicit group dimensions for numeric, nonnegative values.
    # Zero-dimensional groups may contain lines. Composed geometry is not assessed.
    groups = root.findall(".//p:grpSp", NS)
    for group in groups:
        for extent_tag in ("a:ext", "a:chExt"):
            ext = group.find("p:grpSpPr/a:xfrm/" + extent_tag, NS)
            if ext is None:
                continue
            try:
                bad_extents += int(int(ext.attrib["cx"]) < 0 or int(ext.attrib["cy"]) < 0)
            except (ValueError, KeyError):
                bad_extents += 1
    for pic in pictures:
        xf = pic.find("p:spPr/a:xfrm", NS)
        if id(pic) not in direct_ids or xf is None or xf.get("rot", "0") != "0":
            unknown += 1
            continue
        offset, extent = xf.find("a:off", NS), xf.find("a:ext", NS)
        if offset is None or extent is None:
            unknown += 1
            continue
        try:
            x, y = int(offset.attrib["x"]), int(offset.attrib["y"])
            w, h = int(extent.attrib["cx"]), int(extent.attrib["cy"])
        except (ValueError, KeyError):
            unknown += 1
            continue
        if w <= 0 or h <= 0:
            unknown += 1
            continue
        clipped = (max(x, 0), max(y, 0), min(x + w, width), min(y + h, height))
        if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
            rectangles.append(clipped)
    shapes = root.findall(".//p:sp", NS)
    return {
        "native_shapes": len(shapes),
        "shapes_with_text": sum(bool(n.findall("p:txBody/a:p/a:r/a:t", NS)) for n in shapes),
        "text_runs": len(root.findall(".//a:t", NS)),
        "tables": len(root.findall(".//a:tbl", NS)),
        "table_cells": len(root.findall(".//a:tc", NS)),
        "connectors": len(root.findall(".//p:cxnSp", NS)),
        "pictures": len(pictures),
        "picture_fills": len(root.findall(".//p:sp/p:spPr/a:blipFill", NS)),
        "unmeasured_pictures": unknown,
        "unmeasured_groups": len(groups),
        "bad_extents": bad_extents,
        "known_picture_rect_union_ratio": round(union_area(rectangles) / (width * height), 6),
        "editability_status": "not_assessed",
    }


def inspect(path: Path, sources: list[Path] | None = None, expected_sha256: str | None = None) -> dict:
    path = Path(path)
    report = {
        "schema_version": "1.0",
        "inspection_scope": "bounded_ooxml_structure_only",
        "candidate": {"file": path.name, "sha256": None},
        "input_files": [],
        "structural_status": "fail",
        "visual_status": "not_assessed",
        "editability_status": "not_assessed",
        "independent_review_status": "not_assessed",
        "powerpoint_status": "not_assessed",
        "release_status": "not_assessed",
        "slides": [],
        "issues": [],
        "limitations": [
            "No semantic, visual, independent-role or PowerPoint validation.",
            "Picture ratios include only known top-level unrotated rectangles, not visible pixels.",
            "Master/layout backgrounds, fills, groups, rotations and occlusion require separate review.",
            "This is not complete OOXML schema validation or a security scanner.",
        ],
    }

    def issue(severity: str, code: str, **fields: object) -> None:
        report["issues"].append({"severity": severity, "code": code, **fields})

    try:
        if path.stat().st_size > MAX_ARCHIVE:
            raise InspectionError("archive_size_limit")
        initial_hash = digest(path)
        report["candidate"]["sha256"] = initial_hash
        if expected_sha256 is not None:
            if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
                raise InspectionError("invalid_expected_sha256")
            if initial_hash != expected_sha256.lower():
                raise InspectionError("candidate_hash_mismatch")
        for src in sources or []:
            src = Path(src)
            report["input_files"].append({"file": src.name, "sha256": digest(src)})
        if not sources:
            issue("review", "source_hash_binding_missing")
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise InspectionError("entry_count_limit")
            seen, total, names = set(), 0, set()
            for info in infos:
                check_member_name(info.filename)
                key = info.filename.rstrip("/").casefold()
                if key in seen:
                    raise InspectionError("duplicate_or_case_ambiguous_zip_path")
                seen.add(key)
                if info.flag_bits & 1:
                    raise InspectionError("encrypted_zip_entry")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise InspectionError("unsupported_zip_compression")
                if info.file_size > MAX_PART:
                    raise InspectionError("part_size_limit")
                total += info.file_size
                if total > MAX_TOTAL:
                    raise InspectionError("expanded_size_limit")
                if not info.is_dir():
                    names.add(info.filename)
            required = {"[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"}
            if not required <= names:
                raise InspectionError("required_part_missing")
            xml = {}
            for info in infos:
                if info.is_dir():
                    continue
                # Read each bounded entry once, validating CRC without extracting files.
                data = archive.read(info)
                if info.filename.endswith((".xml", ".rels")):
                    xml[info.filename] = parse_xml(data)
            ct = xml["[Content_Types].xml"]
            if ct.tag != "{" + NS["ct"] + "}Types":
                raise InspectionError("content_types_root_invalid")
            for override in ct.findall("ct:Override", NS):
                target = resolve_target("", override.get("PartName", ""))
                if target not in names:
                    raise InspectionError("content_type_part_missing")
            rels = {}
            external_count = 0
            for name, root in xml.items():
                if not name.endswith(".rels"):
                    continue
                owner = relationship_owner(name)
                if owner and owner not in names:
                    raise InspectionError("relationship_owner_missing")
                if root.tag != "{" + NS["rel"] + "}Relationships":
                    raise InspectionError("relationship_root_invalid")
                mapping = {}
                for rel in root.findall("rel:Relationship", NS):
                    rid = rel.get("Id", "")
                    if not rid or rid in mapping:
                        raise InspectionError("relationship_id_missing_or_duplicate")
                    if rel.get("TargetMode") not in (None, "Internal", "External"):
                        raise InspectionError("relationship_mode_invalid")
                    external = rel.get("TargetMode") == "External"
                    if not rel.get("Type") or not rel.get("Target"):
                        raise InspectionError("relationship_field_missing")
                    target = rel.get("Target") if external else resolve_target(owner, rel.get("Target", ""))
                    if not external and target not in names:
                        raise InspectionError("relationship_target_missing")
                    external_count += int(external)
                    mapping[rid] = {"target": target, "type": rel.get("Type"), "external": external}
                rels[owner] = mapping
            if external_count:
                issue("review", "external_relationships_not_fetched", count=external_count)
            offices = [r for r in rels[""].values() if r["type"] == REL_BASE + "officeDocument"]
            if len(offices) != 1 or offices[0]["external"] or offices[0]["target"] != "ppt/presentation.xml":
                raise InspectionError("unsupported_or_missing_presentation_entrypoint")
            pres = xml["ppt/presentation.xml"]
            if pres.tag != "{" + NS["p"] + "}presentation":
                raise InspectionError("unsupported_presentation_namespace")
            size = pres.find("p:sldSz", NS)
            if size is None:
                raise InspectionError("slide_size_missing")
            width, height = int(size.get("cx", "0")), int(size.get("cy", "0"))
            if width <= 0 or height <= 0:
                raise InspectionError("slide_size_invalid")
            report["slide_size_emu"] = {"width": width, "height": height}
            slide_ids = pres.findall("p:sldIdLst/p:sldId", NS)
            if not slide_ids:
                raise InspectionError("no_presentation_slides")
            presented, numeric_ids = set(), set()
            for number, node in enumerate(slide_ids, 1):
                if not node.get("id") or node.get("id") in numeric_ids:
                    raise InspectionError("slide_id_missing_or_duplicate")
                numeric_ids.add(node.get("id"))
                rel = rels["ppt/presentation.xml"].get(node.get("{" + NS["r"] + "}id", ""))
                if rel is None or rel["external"] or rel["type"] != REL_BASE + "slide":
                    raise InspectionError("slide_relationship_invalid")
                part = rel["target"]
                if part in presented:
                    raise InspectionError("duplicate_presented_slide")
                presented.add(part)
                root = xml.get(part)
                if root is None or root.tag != "{" + NS["p"] + "}sld":
                    raise InspectionError("slide_root_invalid")
                stats = slide_stats(root, width, height)
                stats.update({"slide": number, "part": part})
                report["slides"].append(stats)
                if stats["bad_extents"]:
                    issue("error", "nonpositive_or_invalid_object_extent", slide=number)
                if stats["known_picture_rect_union_ratio"] > 0.4:
                    issue("review", "large_known_picture_area_requires_source_review", slide=number)
                if stats["unmeasured_pictures"] or stats["picture_fills"]:
                    issue("review", "image_geometry_incomplete", slide=number)
                if stats["unmeasured_groups"]:
                    issue("review", "group_geometry_not_assessed", slide=number)
                # Verify relationship references in each slide, including embedded images.
                for element in root.iter():
                    for attr, rid in element.attrib.items():
                        if attr.startswith("{" + NS["r"] + "}") and rid not in rels.get(part, {}):
                            raise InspectionError("slide_relationship_reference_missing")
            loose = [n for n in names if re.fullmatch(r"ppt/slides/[^/]+\.xml", n) and n not in presented]
            if loose:
                issue("review", "unreferenced_slide_parts", count=len(loose))
            report["slide_count"] = len(report["slides"])
            report["media_count"] = sum(n.startswith("ppt/media/") for n in names)
        if digest(path) != initial_hash:
            raise InspectionError("candidate_changed_during_inspection")
        report["structural_status"] = "fail" if any(x["severity"] == "error" for x in report["issues"]) else "pass"
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        issue("error", "inspection_failed", detail=str(exc))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--source", action="append", type=Path, default=[])
    parser.add_argument("--expected-sha256")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    report = inspect(args.pptx, args.source, args.expected_sha256)
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json_out:
        try:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            with args.json_out.open("x", encoding="utf-8") as stream:
                stream.write(output)
        except OSError as exc:
            print(f"Report write failed: {exc}", file=sys.stderr)
            return 2
    print(output, end="")
    return 0 if report["structural_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
