import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.answering.deterministic import solve_deterministic_task
from app.pipeline import Pipeline
from app.window.extractor import WindowExtractor
from app.window.rules import load_rules
from app.downloads.rules import load_download_rules
REPLACEMENT_HTML = '''
<div class="assessment-container">
  <article class="task-item" data-task-id="c1">
    <div class="task-question">
    <div class="question-instructions">
      <div class="question-text">
        <p>Remplacez le mot sed par le mot mais dans tout le texte.</p>
        <p>Combien de caractères contient le document suite à cette modification (en incluant les espaces) ?</p>
      </div>
    </div>
    <form><fieldset class="task-response"><legend>Réponse</legend></fieldset></form>
    </div>
  </article>
</div>
'''


def _make_raw(base: Path) -> Path:
    raw = base / "data" / "raw_pages" / "20260930T200000_000000Z.html"
    raw.parent.mkdir(parents=True)
    raw.write_text(REPLACEMENT_HTML, encoding="utf-8")
    return raw


class DeterministicSolverTests(unittest.TestCase):
    def test_replace_and_count_characters_french(self):
        result = solve_deterministic_task(
            REPLACEMENT_HTML,
            filename="page.html",
            attachment_text="sed sed\nhello world",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.answer, str(len("mais mais\nhello world")))
        self.assertEqual(result.solver, "text_replace_and_count")
        self.assertEqual(result.details["replacement_count"], 2)

    def test_replace_and_count_excludes_spaces_when_explicit(self):
        html = REPLACEMENT_HTML.replace("en incluant les espaces", "sans les espaces")
        result = solve_deterministic_task(
            html,
            filename="page.html",
            attachment_text="sed sed hi",
        )
        self.assertIsNotNone(result)
        expected = len("mais mais hi".replace(" ", ""))
        self.assertEqual(result.answer, str(expected))

    def test_replace_and_count_does_not_match_when_old_word_absent(self):
        result = solve_deterministic_task(
            REPLACEMENT_HTML,
            filename="page.html",
            attachment_text="hello world",
        )
        self.assertIsNone(result)

    def test_plain_character_count_is_deterministic(self):
        html = REPLACEMENT_HTML.replace(
            "Remplacez le mot sed par le mot mais dans tout le texte.",
            "Combien de caractères contient le document ?",
        )
        result = solve_deterministic_task(
            html,
            filename="page.html",
            attachment_text="hello world",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.answer, str(len("hello world")))
        self.assertEqual(result.solver, "character_counter")

    def test_unsupported_reasoning_task_returns_none(self):
        html = REPLACEMENT_HTML.replace(
            "Remplacez le mot sed par le mot mais dans tout le texte.",
            "Quelle est la meilleure réponse ?",
        ).replace(
            "Combien de caractères contient le document suite à cette modification (en incluant les espaces) ?",
            "Choisissez la réponse correcte.",
        )
        result = solve_deterministic_task(
            html,
            filename="page.html",
            attachment_text="hello world",
        )
        self.assertIsNone(result)


class DeterministicPipelineTests(unittest.TestCase):
    def test_deterministic_solver_bypasses_ollama_and_prints_answer_only_at_end(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = _make_raw(base)
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=None,
                window_model="missing",
                answer_model="missing",
                use_window_llm=False,
                use_answer_llm=True,
                download_rules={**load_download_rules()},
            )
            attachment = base / "source-document.txt"
            attachment.write_text("sed sed\nhello world", encoding="utf-8")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = pipeline.process_raw_file(raw, attachment_path=attachment)

            self.assertEqual(result["status"], "answer")
            self.assertEqual(result["decision"]["solver"], "text_replace_and_count")
            lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
            self.assertEqual(lines[-1], str(len("mais mais\nhello world")))
            self.assertNotIn("reason:", lines[-1].lower())
            self.assertFalse(any("[OLLAMA] Preparing answer input" in line for line in lines))
            processed_files = list((base / "data" / "downloads" / "processed").glob("*"))
            self.assertTrue(all(path.suffix == ".json" for path in processed_files))

    def test_deterministic_confidence_is_high_enough_for_download_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = _make_raw(base)
            pipeline = Pipeline(
                base_dir=base,
                extractor=WindowExtractor(load_rules()),
                ollama=None,
                window_model="small",
                answer_model="large",
                use_window_llm=False,
                use_answer_llm=False,
                download_rules={**load_download_rules()},
            )
            attachment = base / "source-document.txt"
            attachment.write_text("sed sed", encoding="utf-8")
            result = pipeline.process_raw_file(raw, attachment_path=attachment)
            self.assertEqual(result["status"], "clean_ready")


if __name__ == "__main__":
    unittest.main()
