"""Run with: python -B -m unittest discover -s tests -p report_regression.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_report_html import build_compact_payload


class ReportRegression(unittest.TestCase):
    def payload(self, **fields):
        question = {
            "question_id": "q1", "question_number": "1",
            "analysis_status": "completed", "relevance": "relevant",
            "topic_ids": ["TREE"], "source_refs": [{"exam_name": "other"}],
            **fields,
        }
        return build_compact_payload(
            Path.cwd(), Path.cwd() / "report.md", "",
            {"target_exam": "wanted", "unique_related_questions": 0},
            {"unique_related_questions": 1},
            {"study_subject": "資料結構", "topics": [{"topic_id": "TREE", "label": "樹"}],
             "questions": [question]}, None,
        )

    def test_zero_matches_stays_empty(self):
        data = self.payload()
        self.assertEqual(data["target_unique_count"], 0)
        self.assertFalse(data["questions"][0]["target"])
        self.assertEqual(data["topics"][0]["target_total"], 0)

    def test_numeric_question_is_not_assumed_mcq(self):
        data = self.payload()
        self.assertEqual(data["questions"][0]["type"], "unknown")
        self.assertEqual(data["topics"][0]["all_mcq"], 0)
        self.assertEqual(data["topics"][0]["all_essay"], 0)

    def test_explicit_essay_and_missing_quiz_data(self):
        data = self.payload(question_type="essay")
        self.assertEqual(data["questions"][0]["type"], "essay")
        self.assertEqual(data["topics"][0]["all_essay"], 1)
        self.assertFalse(data["questions"][0]["quiz_ready"])

    def test_unreviewed_questions_are_not_used_in_topic_fallback(self):
        data = self.payload(analysis_status="needs_review", summary="UNREVIEWED_SENTINEL")
        self.assertNotIn("UNREVIEWED_SENTINEL", data["topics"][0]["html"])
        self.assertNotIn("法制", data["topics"][0]["km_module_name"])


if __name__ == "__main__":
    unittest.main()
