import io
from contextlib import redirect_stdout

from app.cli_view import print_answer_result, print_final_answer
from app.ollama.prompts import answerer_prompt


def test_result_block_does_not_duplicate_final_answer():
    out = io.StringIO()
    with redirect_stdout(out):
        print_answer_result({
            "status": "answer",
            "answer": "Enable two-factor authentication",
            "confidence": 0.95,
            "model": "qwen3:8b",
            "reason": "The visible action matches the requested objective.",
        })
        print_final_answer("Enable two-factor authentication")
    rendered = out.getvalue()
    assert rendered.count("Enable two-factor authentication") == 1
    assert "[RESULT]" in rendered
    assert "[FINAL ANSWER]" in rendered


def test_answer_prompt_requires_concise_distinguishing_content():
    prompt = answerer_prompt().lower()
    assert "be concise" in prompt
    assert "do not restate the question" in prompt
    assert "main information that distinguishes" in prompt
    assert "must not repeat the answer" in prompt
