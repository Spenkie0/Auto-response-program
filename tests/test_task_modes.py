from __future__ import annotations

from app.task import TaskMode, TaskResponseMode, classify_task


BASIC_QUESTION = """
<article class="task-item">
  <div class="question-text">
    <p>Quelle est la capitale de l'Italie ?</p>
  </div>
  <fieldset>
    <label><input type="radio" name="answer" value="Paris">Paris</label>
    <label><input type="radio" name="answer" value="Rome">Rome</label>
    <label><input type="radio" name="answer" value="Madrid">Madrid</label>
  </fieldset>
</article>
"""

TEXT_QUESTION = """
<article class="task-item">
  <div class="question-text">
    <p>Enter your answer.</p>
  </div>
  <textarea aria-label="Réponse"></textarea>
</article>
"""

FILE_DIRECT = """
<article class="task-item">
  <div class="question-text">
    <p>Téléchargez le fichier puis calculez le nombre de mots.</p>
  </div>
  <a download href="https://example.test/document.docx">Télécharger</a>
</article>
"""

FILE_GENERIC = """
<article class="task-item">
  <div class="question-text">
    <p>Open the supplied file and identify its author.</p>
  </div>
  <input type="file" aria-label="Fichier">
</article>
"""

FILE_AMBIGUOUS = """
<article class="task-item">
  <p>Téléchargez le document.</p>
  <a href="https://example.test/a.docx">Download document A</a>
  <a href="https://example.test/b.docx">Download document B</a>
</article>
"""

SANDBOX_EXAMPLE = """
<article class="task-item" data-task-id="task-123">
  <div class="question-text">
    <p>Use the embedded workspace to create a new item.</p>
    <p>Add two participants to the workspace.</p>
  </div>
  <div class="interactive-sandbox-container">
    <div class="sandbox-wrapper">
      <div class="start-overlay">
        <button type="button">Commencer</button>
      </div>
      <div class="sandbox-content blurred">
        <iframe
          class="sandbox-frame"
          src="https://sandbox.example.test/interactive/simulator?mode=demo&amp;lang=en"
          title="Interactive simulator"></iframe>
      </div>
    </div>
  </div>
</article>
"""

SANDBOX_AFTER_START = """
<article class="task-item">
  <div class="question-text">
    <p>Use the embedded environment to create a new item.</p>
  </div>
  <div class="interactive-sandbox-container">
    <div class="sandbox-content">
      <iframe
        class="sandbox-frame"
        src="https://sandbox.example.test/interactive/simulator?mode=demo&amp;lang=en"
        title="Interactive simulator"></iframe>
    </div>
  </div>
</article>
"""

SANDBOX_DOWNLOAD_TOO = """
<article class="task-item">
  <p>Use the interactive task below, then download the report.</p>
  <div class="interactive-sandbox-container">
    <iframe class="sandbox-frame" src="https://example.test/sandbox" title="Interactive simulator"></iframe>
  </div>
  <a download href="https://example.test/report.docx">Download the report</a>
</article>
"""

NON_SANDBOX_IFRAME = """
<article class="task-item">
  <p>Regardez la vidéo puis répondez.</p>
  <iframe src="https://video.example.test/watch/123" title="Vidéo"></iframe>
  <input type="text" aria-label="Réponse">
</article>
"""

MULTIPLE_IFRAMES_ONE_SANDBOX = """
<article class="task-item">
  <p>Interact with the embedded simulator.</p>
  <iframe src="https://example.test/video" title="Vidéo"></iframe>
  <section class="interactive-sandbox-container">
    <iframe class="sandbox-frame" src="/simulator" title="Simulateur"></iframe>
  </section>
</article>
"""



def test_basic_multiple_choice_classification():
    result = classify_task(BASIC_QUESTION, page_url="https://example.test/q")
    assert result.mode is TaskMode.BASIC
    assert result.response_mode is TaskResponseMode.CHOICE
    assert result.has_sandbox is False
    assert result.download_candidate_count == 0



def test_basic_text_input_classification():
    result = classify_task(TEXT_QUESTION, page_url="https://example.test/q")
    assert result.mode is TaskMode.BASIC
    assert result.response_mode is TaskResponseMode.TEXT



def test_empty_page_is_basic_unknown():
    result = classify_task("", page_url="https://example.test/q")
    assert result.mode is TaskMode.BASIC
    assert result.response_mode is TaskResponseMode.UNKNOWN
    assert result.confidence == 0.9



def test_malformed_html_still_classifies_safely():
    result = classify_task(
        "<article><p>Téléchargez le fichier</p><a href='/x.txt'>Télécharger",
        page_url="https://example.test/q",
    )
    assert result.mode is TaskMode.FILE_TASK
    assert result.download_candidate_count == 1



def test_direct_file_task_is_file_mode():
    result = classify_task(FILE_DIRECT, page_url="https://example.test/q")
    assert result.mode is TaskMode.FILE_TASK
    assert result.response_mode is TaskResponseMode.UNKNOWN
    assert result.download_candidate_count == 1
    assert result.ambiguous_download is False
    assert "https://example.test/document.docx" in result.download_candidate_urls



def test_file_input_is_file_mode_without_download_url():
    result = classify_task(FILE_GENERIC, page_url="https://example.test/q")
    assert result.mode is TaskMode.FILE_TASK
    assert result.response_mode is TaskResponseMode.UNKNOWN
    assert result.download_candidate_count == 0
    assert "local-file-input-detected" in result.reasons



def test_ambiguous_download_remains_file_mode_but_is_marked_ambiguous():
    result = classify_task(FILE_AMBIGUOUS, page_url="https://example.test/q")
    assert result.mode is TaskMode.FILE_TASK
    assert result.ambiguous_download is True
    assert result.download_candidate_count == 2
    assert len(result.download_candidate_urls) == 2



def test_download_candidate_can_be_relative_to_page_url():
    html = '<p>Télécharger</p><a download href="/document.txt">Télécharger</a>'
    result = classify_task(html, page_url="https://example.test/tasks/one")
    assert result.mode is TaskMode.FILE_TASK
    assert result.download_candidate_urls == ("https://example.test/document.txt",)



def test_interactive_sandbox_first_state_is_sandbox():
    result = classify_task(SANDBOX_EXAMPLE, page_url="https://app.example.test/epreuve")
    assert result.mode is TaskMode.SANDBOX
    assert result.has_sandbox is True
    assert len(result.sandbox_frames) == 1
    frame = result.sandbox_frames[0]
    assert frame.title == "Interactive simulator"
    assert frame.same_origin is False
    assert "sandbox-like-ancestor-class" in frame.reason_codes
    assert "sandbox-like-frame-class" in frame.reason_codes
    assert "sandbox-like-title" in frame.reason_codes



def test_interactive_sandbox_after_start_stays_sandbox():
    result = classify_task(SANDBOX_AFTER_START, page_url="https://app.example.test/epreuve")
    assert result.mode is TaskMode.SANDBOX
    assert result.has_sandbox is True
    assert result.sandbox_frames[0].same_origin is False



def test_sandbox_takes_priority_over_file_signal():
    result = classify_task(SANDBOX_DOWNLOAD_TOO, page_url="https://app.example.test/q")
    assert result.mode is TaskMode.SANDBOX
    assert result.has_sandbox is True
    assert result.download_candidate_count == 1
    assert "file-task-signal-also-present" in result.reasons



def test_plain_iframe_does_not_become_sandbox():
    result = classify_task(NON_SANDBOX_IFRAME, page_url="https://app.example.test/q")
    assert result.mode is TaskMode.BASIC
    assert result.has_sandbox is False
    assert result.response_mode is TaskResponseMode.TEXT



def test_only_the_sandbox_frame_is_selected_among_multiple_iframes():
    result = classify_task(MULTIPLE_IFRAMES_ONE_SANDBOX, page_url="https://app.example.test/q")
    assert result.mode is TaskMode.SANDBOX
    assert len(result.sandbox_frames) == 1
    assert result.sandbox_frames[0].src == "https://app.example.test/simulator"



def test_sandbox_same_origin_is_reported():
    html = """
    <div class="interactive-sandbox-container">
      <iframe class="sandbox-frame" src="/simulator" title="Simulateur"></iframe>
    </div>
    """
    result = classify_task(html, page_url="https://example.test/path/page")
    assert result.mode is TaskMode.SANDBOX
    assert result.sandbox_frames[0].same_origin is True



def test_non_http_frame_origin_is_unknown():
    html = """
    <div class="interactive-sandbox-container">
      <iframe class="sandbox-frame" src="about:blank" title="Simulateur"></iframe>
    </div>
    """
    result = classify_task(html, page_url="https://example.test/q")
    assert result.mode is TaskMode.SANDBOX
    assert result.sandbox_frames[0].same_origin is None



def test_sandbox_title_alone_is_enough_when_other_strong_signal_is_present():
    html = '<iframe src="https://example.test/app" title="Interactive simulator"></iframe>'
    result = classify_task(html, page_url="https://example.test/q")
    assert result.mode is TaskMode.SANDBOX
    assert result.sandbox_frames[0].sandbox_score >= 70



def test_sandbox_class_alone_is_enough_when_strong():
    html = '<iframe class="custom-sandbox-frame" src="https://example.test/app"></iframe>'
    result = classify_task(html, page_url="https://example.test/q")
    assert result.mode is TaskMode.SANDBOX



def test_button_proposals_are_choice_response_mode():
    html = """
    <div class="task-options">
      <button type="button">Option A</button>
      <button type="button">Option B</button>
    </div>
    """
    result = classify_task(html, page_url="https://example.test/q")
    assert result.mode is TaskMode.BASIC
    assert result.response_mode is TaskResponseMode.CHOICE



def test_select_is_choice_response_mode():
    html = '<select><option>A</option><option>B</option></select>'
    result = classify_task(html, page_url="https://example.test/q")
    assert result.response_mode is TaskResponseMode.CHOICE



def test_search_input_is_text_response_mode():
    html = '<input type="search" aria-label="Recherche">'
    result = classify_task(html, page_url="https://example.test/q")
    assert result.response_mode is TaskResponseMode.TEXT



def test_textarea_is_text_response_mode():
    html = '<textarea aria-label="Réponse"></textarea>'
    result = classify_task(html, page_url="https://example.test/q")
    assert result.response_mode is TaskResponseMode.TEXT



def test_choice_response_takes_precedence_over_text_input_when_both_exist():
    html = """
    <input type="radio" name="answer" value="a">
    <input type="text" aria-label="Commentaire">
    """
    result = classify_task(html, page_url="https://example.test/q")
    assert result.response_mode is TaskResponseMode.CHOICE



def test_custom_download_extension_list_is_used():
    html = '<a download href="https://example.test/task.custom">Télécharger</a>'
    result = classify_task(
        html,
        page_url="https://example.test/q",
        accepted_extensions=[".custom"],
    )
    assert result.mode is TaskMode.FILE_TASK
    assert result.download_candidate_count == 1



def test_download_url_can_be_detected_without_file_extension():
    html = '<a download href="https://example.test/download?id=123">Télécharger</a>'
    result = classify_task(html, page_url="https://example.test/q")
    assert result.mode is TaskMode.FILE_TASK
    assert result.download_candidate_count == 1



def test_data_download_url_is_file_task():
    html = '<button data-download-url="https://example.test/file.docx">Télécharger</button>'
    result = classify_task(html, page_url="https://example.test/q")
    assert result.mode is TaskMode.FILE_TASK
    assert result.download_candidate_count == 1



def test_choice_plus_file_is_file_task():
    html = """
    <p>Téléchargez le document.</p>
    <a download href="https://example.test/data.csv">Télécharger</a>
    <label><input type="radio" name="answer" value="a">A</label>
    """
    result = classify_task(html, page_url="https://example.test/q")
    assert result.mode is TaskMode.FILE_TASK
    assert result.response_mode is TaskResponseMode.CHOICE



def test_sandbox_plus_choice_is_sandbox():
    html = """
    <div class="interactive-sandbox-container">
      <iframe class="sandbox-frame" src="/sandbox" title="Simulateur"></iframe>
    </div>
    <label><input type="radio" name="answer" value="done">Terminé</label>
    """
    result = classify_task(html, page_url="https://example.test/q")
    assert result.mode is TaskMode.SANDBOX
    assert result.response_mode is TaskResponseMode.CHOICE



def test_serialization_contains_enough_information_for_logging():
    result = classify_task(SANDBOX_EXAMPLE, page_url="https://app.example.test/epreuve")
    payload = result.as_dict()
    assert payload["mode"] == "sandbox"
    assert payload["response_mode"] == "unknown"
    assert isinstance(payload["sandbox_frames"], list)
    assert payload["sandbox_frames"][0]["same_origin"] is False



def test_all_three_top_level_modes_are_reachable():
    results = [
        classify_task(BASIC_QUESTION),
        classify_task(FILE_DIRECT),
        classify_task(SANDBOX_EXAMPLE, page_url="https://app.example.test/q"),
    ]
    assert {result.mode for result in results} == {
        TaskMode.BASIC,
        TaskMode.FILE_TASK,
        TaskMode.SANDBOX,
    }


FILE_MENTION_QUESTION = """
<article class="task-item">
  <div class="question-text">
    <p>Jérôme recense régulièrement les myriapodes qui vivent près de son village.</p>
    <p>Il a un tableau contenant les nombres de myriapodes de chaque espèce, comptés chaque mois depuis 5 ans.</p>
    <p>Il souhaite le partager en <em>open data</em> (données ouvertes). Il a déjà préparé deux fichiers : l’un au format xlsx, l’autre au format ods.</p>
    <p>Quel autre format peut-on lui conseiller pour la diffusion de ces données ?</p>
  </div>
  <label for="qroc_input">Format : </label>
  <input id="qroc_input" type="text" name="f32201603">
</article>
"""


def test_file_mentions_without_download_are_basic():
    result = classify_task(FILE_MENTION_QUESTION, page_url="https://app.example.test/epreuve")
    assert result.mode is TaskMode.BASIC
    assert result.response_mode is TaskResponseMode.TEXT
    assert result.download_candidate_count == 0
    assert result.ambiguous_download is False



def test_default_download_extensions_are_loaded_from_project_config(monkeypatch):
    from app.task import classifier as classifier_module

    monkeypatch.setattr(
        classifier_module,
        "load_download_rules",
        lambda: {"accepted_extensions": [".custom"]},
    )
    html = '<a download href="https://example.test/task.custom">Télécharger</a>'
    result = classify_task(html, page_url="https://example.test/q")
    assert result.mode is TaskMode.FILE_TASK
    assert result.download_candidate_count == 1
