"""Offline tests for the portable storyboard contract."""

import copy
import json
from pathlib import Path
import runpy
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPERS = runpy.run_path(str(ROOT / "scripts" / "validate_script.py"))
validate = HELPERS["validate"]
EXAMPLE = json.loads((ROOT / "examples" / "fictional-script.json").read_text(encoding="utf-8"))


class StoryboardValidationTests(unittest.TestCase):
    def setUp(self):
        self.document = copy.deepcopy(EXAMPLE)

    def test_three_page_example(self):
        self.assertEqual(validate(self.document), [])
        self.assertEqual(len(self.document["slides"]), 3)

    def test_non_object_root_is_rejected(self):
        self.assertTrue(validate([]))

    def test_boolean_dimension_is_rejected(self):
        self.document["canvas"]["width"] = True
        self.assertTrue(validate(self.document))

    def test_invalid_canvas_dimensions_are_rejected(self):
        for field in ("width", "height"):
            for value in (0, -1, 16385, 1.5, "1920"):
                with self.subTest(field=field, value=value):
                    self.document = copy.deepcopy(EXAMPLE)
                    self.document["canvas"][field] = value
                    self.assertTrue(validate(self.document))

    def test_canvas_boundary_dimensions_are_accepted(self):
        for value in (1, 16384):
            with self.subTest(value=value):
                self.document["canvas"] = {"width": value, "height": value}
                self.assertEqual(validate(self.document), [])

    def test_duplicate_page_id_is_rejected(self):
        self.document["slides"][1]["id"] = self.document["slides"][0]["id"]
        self.assertTrue(validate(self.document))

    def test_case_variant_page_id_is_rejected(self):
        self.document["slides"][1]["id"] = self.document["slides"][0]["id"].upper()
        self.assertTrue(validate(self.document))

    def test_unsafe_page_ids_are_rejected(self):
        for identifier in ("../p1", "p1/p2", "p1\\p2", "/p1", ".p1", "p 1", "页1", "p" * 65):
            with self.subTest(identifier=identifier):
                self.document["slides"][0]["id"] = identifier
                self.assertTrue(validate(self.document))

    def test_safe_id_boundaries_are_accepted(self):
        for identifier in ("p", "1", "p-1_2", "p" * 64):
            with self.subTest(identifier=identifier):
                self.document["slides"][0]["id"] = identifier
                self.assertEqual(validate(self.document), [])

    def test_missing_prompt_is_rejected(self):
        del self.document["slides"][0]["image_prompt"]
        self.assertTrue(validate(self.document))

    def test_wrong_source_type_is_rejected(self):
        self.document["slides"][0]["source_refs"] = "source"
        self.assertTrue(validate(self.document))

    def test_empty_slide_array_is_rejected(self):
        self.document["slides"] = []
        self.assertTrue(validate(self.document))

    def test_slide_count_upper_boundary(self):
        self.document["slides"] = []
        for index in range(501):
            slide = copy.deepcopy(EXAMPLE["slides"][0])
            slide["id"] = f"p{index}"
            self.document["slides"].append(slide)
        self.assertTrue(validate(self.document))
        self.document["slides"].pop()
        self.assertEqual(validate(self.document), [])

    def test_single_slide_is_accepted(self):
        self.document["slides"] = self.document["slides"][:1]
        self.assertEqual(validate(self.document), [])

    def test_empty_or_non_string_array_items_are_rejected(self):
        for field in ("on_slide_text", "source_refs"):
            for value in ("", " \n\t", None, 1):
                with self.subTest(field=field, value=value):
                    self.document = copy.deepcopy(EXAMPLE)
                    self.document["slides"][0][field] = [value]
                    self.assertTrue(validate(self.document))

    def test_documented_empty_fields_are_accepted(self):
        slide = self.document["slides"][0]
        slide["speaker_notes"] = ""
        slide["on_slide_text"] = []
        slide["source_refs"] = []
        self.assertEqual(validate(self.document), [])

    def test_extension_fields_are_preserved(self):
        self.document["consumer_extension"] = {"enabled": True}
        self.document["slides"][0]["extra_field"] = [1, 2]
        before = copy.deepcopy(self.document)
        self.assertEqual(validate(self.document), [])
        self.assertEqual(self.document, before)

    def test_duplicate_json_key_is_rejected(self):
        with self.assertRaises(ValueError):
            json.loads('{"a": 1, "a": 2}', object_pairs_hook=HELPERS["unique_object"])

    def test_non_finite_json_numbers_are_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    json.loads('{"a": ' + value + '}', parse_constant=HELPERS["reject_constant"])


if __name__ == "__main__":
    unittest.main()
