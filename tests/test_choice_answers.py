import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.answering.choice_resolver import resolve_choice_answer
from app.answer_representation import build_compact_representation
from app.pipeline import Pipeline
from app.window.extractor import WindowExtractor
from app.window.rules import load_rules

GENERIC_QUESTION_HTML = '''
<div class="assessment-container">
  <article class="task-item" data-task-id="c1">
    <div class="task-question">
      <div class="question-instructions">
        <div class="question-text">
          <p>Jordan wants to protect an online account.</p>
          <p>A password manager can help store distinct credentials.</p>
          <p>Which action best improves account security?</p>
        </div>
      </div>
    </div>
    <form>
      <fieldset class="task-response">
        <legend>Choose one answer.</legend>
        <div class="task-options">
          <p class="answer-row"><div class="answer-option"><label for="a1"><input id="a1" name="radio" type="radio" value="1"><span>Use a unique password</span></label></div></p>
          <p class="answer-row"><div class="answer-option"><label for="a2"><input id="a2" name="radio" type="radio" value="2"><span>Reuse the same password</span></label></div></p>
          <p class="answer-row"><div class="answer-option"><label for="a3"><input id="a3" name="radio" type="radio" value="3"><span>Enable two-factor authentication</span></label></div></p>
          <p class="answer-row"><div class="answer-option"><label for="a4"><input id="a4" name="radio" type="radio" value="4"><span>Disable security updates</span></label></div></p>
        </div>
      </fieldset>
    </form>
  </article>
</div>
'''


class FakeOllama:
    def __init__(self, answer):
        self.answer = answer
        self.last_metrics = {}

    def chat_json(self, model, system_prompt, user_content):
        return self.answer


class ChoiceAnswerTests(unittest.TestCase):
    def test_representation_marks_choice_mode(self):
        data = build_compact_representation(GENERIC_QUESTION_HTML, filename="page.html")
        self.assertEqual(data["task"]["response_mode"], "choice")
        self.assertEqual(
            data["task"]["answer_output_rule"],
            "Return the exact visible sentence/text of the selected choice, not its numeric value or position.",
        )

    def test_numeric_value_resolves_to_visible_choice_text(self):
        choices = [
            {"value": "1", "text": "Use a unique password"},
            {"value": "2", "text": "Reuse the same password"},
            {"value": "3", "text": "Enable two-factor authentication"},
            {"value": "4", "text": "Disable security updates"},
        ]
        result = resolve_choice_answer("3", choices)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["answer"], "Enable two-factor authentication")
        self.assertEqual(result["source"], "choice_value")

    def test_choice_phrase_resolves_to_visible_choice_text(self):
        choices = [
            {"value": "1", "text": "A"},
            {"value": "2", "text": "B"},
            {"value": "3", "text": "C"},
        ]
        result = resolve_choice_answer("choice 3", choices)
        self.assertEqual(result["answer"], "C")

    def test_existing_choice_text_is_left_untouched(self):
        choices = [{"value": "3", "text": "Enable two-factor authentication"}]
        result = resolve_choice_answer("Enable two-factor authentication", choices)
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["answer"], "Enable two-factor authentication")

    def test_unmatched_numeric_choice_is_invalid(self):
        choices = [{"value": "1", "text": "A"}, {"value": "2", "text": "B"}]
        result = resolve_choice_answer("7", choices)
        self.assertEqual(result["status"], "invalid")
        self.assertIsNone(result["answer"])

    def test_pipeline_converts_model_number_to_visible_choice_and_prints_it(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "20260930T210000_000000Z.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=FakeOllama({
                    "status": "answer",
                    "confidence": 0.95,
                    "answer": "3",
                    "reason": "Selected choice 3.",
                }),
                window_model="small",
                answer_model="qwen3:8b",
                use_window_llm=False,
                use_answer_llm=True,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = pipeline.process_raw_file(raw)

            self.assertEqual(result["status"], "answer")
            self.assertEqual(result["decision"]["answer"], "Enable two-factor authentication")
            self.assertEqual(result["decision"]["model_answer"], "3")
            self.assertEqual(result["decision"]["answer_normalization"]["choice_number"], 3)
            self.assertEqual(output.getvalue().splitlines()[-1].strip(), "Enable two-factor authentication")

            answer_json = base / "data" / "answer" / raw.name.replace(".html", ".json")
            payload = json.loads(answer_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["answer"], "Enable two-factor authentication")

    def test_pipeline_rejects_unmatched_numeric_choice(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = base / "data" / "raw_pages" / "20260930T210001_000000Z.html"
            raw.parent.mkdir(parents=True)
            raw.write_text(GENERIC_QUESTION_HTML, encoding="utf-8")
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=FakeOllama({
                    "status": "answer",
                    "confidence": 0.95,
                    "answer": "9",
                    "reason": "Selected choice 9.",
                }),
                window_model="small",
                answer_model="qwen3:8b",
                use_window_llm=False,
                use_answer_llm=True,
            )
            result = pipeline.process_raw_file(raw)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["code"], "MODEL_INVALID_CHOICE_RESPONSE")


if __name__ == "__main__":
    unittest.main()
