"""Synthetic fixtures test mechanical boundaries, not real PowerPoint compatibility."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import struct
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ooxml_check.py"
SPEC = importlib.util.spec_from_file_location("ooxml_check", SCRIPT)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
P, A, R = checker.NS["p"], checker.NS["a"], checker.NS["r"]
REL = checker.NS["rel"]


def rels(*items):
    return f'<Relationships xmlns="{REL}">' + "".join(
        f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{target}"{mode}/>'
        for rid, kind, target, mode in items
    ) + '</Relationships>'


def picture(x=0, y=0, w=100, h=100, rotation="0"):
    return f'''<p:pic><p:blipFill><a:blip r:embed="img"/></p:blipFill>
    <p:spPr><a:xfrm rot="{rotation}"><a:off x="{x}" y="{y}"/>
    <a:ext cx="{w}" cy="{h}"/></a:xfrm></p:spPr></p:pic>'''


def shape(w=100, h=100, preset="rect", connector=False):
    tag = "cxnSp" if connector else "sp"
    nonvisual = (
        '<p:nvCxnSpPr><p:cNvPr id="2" name="Connector"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>'
        if connector else
        '<p:nvSpPr><p:cNvPr id="2" name="Shape"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
    )
    return f'''<p:{tag}>{nonvisual}<p:spPr><a:xfrm><a:off x="0" y="0"/>
    <a:ext cx="{w}" cy="{h}"/></a:xfrm><a:prstGeom prst="{preset}"><a:avLst/></a:prstGeom>
    </p:spPr></p:{tag}>'''


def group_transform(w=0, h=0, child_w=0, child_h=0):
    return f'''<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{w}" cy="{h}"/>
    <a:chOff x="0" y="0"/><a:chExt cx="{child_w}" cy="{child_h}"/></a:xfrm></p:grpSpPr>'''


def table_frame(w=100, h=100):
    return f'''<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="3" name="Table"/>
    <p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>
    <p:xfrm><a:off x="0" y="0"/><a:ext cx="{w}" cy="{h}"/></p:xfrm>
    <a:graphic><a:graphicData uri="{A}/table"><a:tbl><a:tblPr/>
    <a:tblGrid><a:gridCol w="100"/></a:tblGrid><a:tr h="100"><a:tc>
    <a:txBody><a:bodyPr/><a:lstStyle/><a:p/></a:txBody><a:tcPr/>
    </a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>'''


def slide(body=""):
    return f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>'


def fixture(body="", order=(1,)):
    # These fixtures intentionally contain only parts required by this inspector.
    entries = {
        "[Content_Types].xml": f'<Types xmlns="{checker.NS["ct"]}"/>',
        "_rels/.rels": rels(("office", "officeDocument", "ppt/presentation.xml", "")),
        "ppt/presentation.xml": f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>' + ''.join(
            f'<p:sldId id="{255 + number}" r:id="s{number}"/>' for number in order
        ) + '</p:sldIdLst><p:sldSz cx="100" cy="100"/></p:presentation>',
        "ppt/_rels/presentation.xml.rels": rels(*[(f"s{n}", "slide", f"slides/slide{n}.xml", "") for n in order]),
        "ppt/media/pixel.png": b"fixture-only-not-a-decodable-image",
    }
    for n in order:
        entries[f"ppt/slides/slide{n}.xml"] = slide(body)
        entries[f"ppt/slides/_rels/slide{n}.xml.rels"] = rels(("img", "image", "../media/pixel.png", ""))
    return entries


class StructureChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "candidate.pptx"

    def write(self, entries, extra=()):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, content in [*entries.items(), *extra]:
                    archive.writestr(name, content)

    def checked(self, entries, extra=()):
        self.write(entries, extra)
        return checker.inspect(self.path)

    def assertFailed(self, result, reason):
        self.assertEqual(result["structural_status"], "fail")
        self.assertIn(reason, " ".join(str(x) for x in result["issues"]))
        self.assertEqual(result["release_status"], "not_assessed")

    def test_valid_structure_never_certifies_editability_or_release(self):
        body = '<p:sp><p:txBody><a:p><a:r><a:t>Hello</a:t></a:r></a:p></p:txBody></p:sp>'
        result = self.checked(fixture(body))
        self.assertEqual(result["structural_status"], "pass")
        self.assertEqual(result["slides"][0]["text_runs"], 1)
        for key in ("visual_status", "editability_status", "independent_review_status", "powerpoint_status", "release_status"):
            self.assertEqual(result[key], "not_assessed")

    def test_actual_presentation_order_is_preserved(self):
        result = self.checked(fixture(order=(9, 2)))
        self.assertEqual([s["part"] for s in result["slides"]], ["ppt/slides/slide9.xml", "ppt/slides/slide2.xml"])

    def test_unsafe_zip_paths_fail_without_extraction(self):
        for name in ("../escape", "/tmp/escape", "C:/escape", "ppt\\escape", "ppt/%2e%2e/escape", "ppt/./escape", "ppt//escape"):
            with self.subTest(name=name):
                self.assertFailed(self.checked(fixture(), [(name, b"x")]), "unsafe")
        self.assertFalse((self.root / "escape").exists())

    def test_duplicate_or_case_conflicting_parts_fail(self):
        for name in ("ppt/presentation.xml", "PPT/presentation.xml"):
            with self.subTest(name=name):
                self.assertFailed(self.checked(fixture(), [(name, "duplicate")]), "duplicate_or_case")

    def test_dtd_and_utf16_entity_declarations_fail(self):
        payload = '<!DOCTYPE a [<!ENTITY x "value">]><a>&x;</a>'
        for data in (payload.encode(), payload.encode("utf-16")):
            with self.subTest(encoding=data[:2]):
                self.assertFailed(self.checked(fixture(), [("extra.xml", data)]), "dtd_or_entity")

    def test_missing_relationship_target_fails(self):
        entries = fixture()
        del entries["ppt/media/pixel.png"]
        self.assertFailed(self.checked(entries), "relationship_target_missing")

    def test_relationship_traversal_fails_but_parent_relative_media_works(self):
        self.assertEqual(self.checked(fixture())["structural_status"], "pass")
        entries = fixture()
        entries["ppt/slides/_rels/slide1.xml.rels"] = rels(("img", "image", "../../../escape.png", ""))
        self.assertFailed(self.checked(entries), "relationship_escapes_package")

    def test_missing_picture_relationship_reference_fails(self):
        entries = fixture(picture().replace('r:embed="img"', 'r:embed="missing"'))
        self.assertFailed(self.checked(entries), "slide_relationship_reference_missing")

    def test_overlapping_images_use_union_area_and_still_need_review(self):
        result = self.checked(fixture(picture() + picture()))
        self.assertEqual(result["slides"][0]["known_picture_rect_union_ratio"], 1)
        self.assertEqual(result["slides"][0]["pictures"], 2)
        self.assertEqual(result["editability_status"], "not_assessed")
        self.assertIn("large_known_picture_area_requires_source_review", [x["code"] for x in result["issues"]])

    def test_tiled_images_do_not_evade_union_area(self):
        result = self.checked(fixture(''.join(picture(x=x, w=25) for x in (0, 25, 50, 75))))
        self.assertEqual(result["slides"][0]["known_picture_rect_union_ratio"], 1)

    def test_grouped_rotated_and_small_pictures_cannot_certify_editability(self):
        for body in (f'<p:grpSp>{picture()}</p:grpSp>', picture(rotation="5400000"), picture(w=10, h=10)):
            with self.subTest(body=body):
                result = self.checked(fixture(body))
                self.assertEqual(result["editability_status"], "not_assessed")
                if "grpSp" in body or "5400000" in body:
                    self.assertEqual(result["slides"][0]["unmeasured_pictures"], 1)
                    self.assertEqual(result["slides"][0]["known_picture_rect_union_ratio"], 0)

    def test_off_canvas_picture_area_is_clipped(self):
        result = self.checked(fixture(picture(x=-50, y=-50)))
        self.assertEqual(result["slides"][0]["known_picture_rect_union_ratio"], 0.25)

    def test_bad_extent_fails(self):
        self.assertFailed(self.checked(fixture(picture(w=0))), "nonpositive_or_invalid_object_extent")

    def test_horizontal_and_vertical_connectors_have_valid_extents(self):
        for w, h in ((100, 0), (0, 100)):
            with self.subTest(w=w, h=h):
                result = self.checked(fixture(shape(w, h, preset="straightConnector1", connector=True)))
                self.assertEqual(result["structural_status"], "pass")
                self.assertEqual(result["slides"][0]["connectors"], 1)
                self.assertEqual(result["slides"][0]["bad_extents"], 0)

    def test_horizontal_and_vertical_line_shapes_have_valid_extents(self):
        for w, h in ((100, 0), (0, 100)):
            with self.subTest(w=w, h=h):
                result = self.checked(fixture(shape(w, h, preset="line")))
                self.assertEqual(result["structural_status"], "pass")
                self.assertEqual(result["slides"][0]["native_shapes"], 1)

    def test_root_group_default_zero_transform_does_not_fail(self):
        result = self.checked(fixture(group_transform() + shape()))
        self.assertEqual(result["structural_status"], "pass")
        self.assertEqual(result["slides"][0]["bad_extents"], 0)
        self.assertEqual(result["slides"][0]["unmeasured_groups"], 0)

    def test_lines_and_connectors_reject_negative_or_two_zero_axes(self):
        for connector in (False, True):
            for w, h in ((0, 0), (-1, 0), (0, -1), (-1, 100), (100, -1)):
                with self.subTest(connector=connector, w=w, h=h):
                    self.assertFailed(self.checked(fixture(shape(w, h, preset="line", connector=connector))), "nonpositive_or_invalid_object_extent")

    def test_regular_shapes_need_two_positive_dimensions(self):
        for w, h in ((0, 100), (100, 0), (0, 0), (-1, 100), (100, -1)):
            with self.subTest(w=w, h=h):
                # Include actual text to exercise an ordinary editable text shape.
                body = shape(w, h).replace('</p:sp>', '<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Text</a:t></a:r></a:p></p:txBody></p:sp>')
                self.assertFailed(self.checked(fixture(body)), "nonpositive_or_invalid_object_extent")

    def test_pictures_reject_negative_and_degenerate_extents(self):
        for w, h in ((-1, 100), (100, -1), (0, 100), (100, 0), (0, 0)):
            with self.subTest(w=w, h=h):
                self.assertFailed(self.checked(fixture(picture(w=w, h=h))), "nonpositive_or_invalid_object_extent")

    def test_table_graphic_frames_require_area(self):
        valid = self.checked(fixture(table_frame()))
        self.assertEqual(valid["structural_status"], "pass")
        self.assertEqual(valid["slides"][0]["tables"], 1)
        for w, h in ((0, 100), (100, 0), (-1, 100), (100, -1)):
            with self.subTest(w=w, h=h):
                self.assertFailed(self.checked(fixture(table_frame(w, h))), "nonpositive_or_invalid_object_extent")

    def test_group_geometry_is_limited_and_reported_for_review(self):
        body = '<p:grpSp>' + group_transform(100, 0, 100, 0) + shape(100, 0, "line") + '</p:grpSp>'
        result = self.checked(fixture(body))
        self.assertEqual(result["structural_status"], "pass")
        self.assertEqual(result["slides"][0]["unmeasured_groups"], 1)
        self.assertIn("group_geometry_not_assessed", [x["code"] for x in result["issues"]])
        for transform in (group_transform(-1, 100, 100, 100), group_transform(100, 100, 100, -1)):
            self.assertFailed(self.checked(fixture('<p:grpSp>' + transform + shape() + '</p:grpSp>')), "nonpositive_or_invalid_object_extent")

    def test_source_and_candidate_hashes_bind_actual_bytes(self):
        self.write(fixture())
        source = self.root / "source.md"
        source.write_bytes(b"approved source")
        before = self.path.read_bytes()
        result = checker.inspect(self.path, [source], hashlib.sha256(before).hexdigest())
        self.assertEqual(result["structural_status"], "pass")
        self.assertEqual(result["candidate"]["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(result["input_files"][0]["sha256"], hashlib.sha256(b"approved source").hexdigest())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFailed(checker.inspect(self.path, expected_sha256="0" * 64), "candidate_hash_mismatch")

    def test_missing_source_is_not_silently_accepted(self):
        self.write(fixture())
        self.assertEqual(checker.inspect(self.path, [self.root / "missing.md"])["structural_status"], "fail")

    def test_size_limits_stop_before_parsing(self):
        self.write(fixture())
        for constant in ("MAX_ARCHIVE", "MAX_TOTAL", "MAX_PART", "MAX_ENTRIES"):
            with self.subTest(constant=constant), patch.object(checker, constant, 1):
                self.assertEqual(checker.inspect(self.path)["structural_status"], "fail")

    def test_report_cannot_overwrite_candidate_or_existing_report(self):
        self.write(fixture())
        original = self.path.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checker.main([str(self.path), "--json-out", str(self.path)]), 2)
        self.assertEqual(self.path.read_bytes(), original)

    def test_external_targets_are_recorded_without_fetching(self):
        entries = fixture()
        entries["ppt/slides/_rels/slide1.xml.rels"] = rels(("link", "hyperlink", "https://example.invalid/page", ' TargetMode="External"'))
        result = self.checked(entries)
        self.assertEqual(result["structural_status"], "pass")
        self.assertIn("external_relationships_not_fetched", [x["code"] for x in result["issues"]])

    def test_malformed_xml_fails(self):
        entries = fixture()
        entries["ppt/slides/slide1.xml"] = "<unclosed>"
        self.assertEqual(self.checked(entries)["structural_status"], "fail")

    def test_non_zip_input_fails(self):
        self.path.write_bytes(b"not a zip")
        self.assertEqual(checker.inspect(self.path)["structural_status"], "fail")

    def test_corrupt_compressed_payload_reports_failure(self):
        self.write(fixture())
        with zipfile.ZipFile(self.path) as archive:
            info = archive.getinfo("ppt/slides/slide1.xml")
        content = bytearray(self.path.read_bytes())
        name_length, extra_length = struct.unpack_from("<HH", content, info.header_offset + 26)
        payload_start = info.header_offset + 30 + name_length + extra_length
        content[payload_start:payload_start + info.compress_size] = b"\xff" * info.compress_size
        self.path.write_bytes(content)
        result = checker.inspect(self.path)
        self.assertEqual(result["structural_status"], "fail")
        self.assertEqual(result["release_status"], "not_assessed")


if __name__ == "__main__":
    unittest.main()
