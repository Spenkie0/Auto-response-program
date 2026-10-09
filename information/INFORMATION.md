# Scrapper Ollama — Complete Information

This document consolidates the documentation that was previously spread across multiple files in the project root.

## Contents

- [Project Overview](#project-overview)
- [Installation and Dependency Bootstrap](#installation-and-dependency-bootstrap)
- [Security Model](#security-model)
- [File Evidence Architecture](#file-evidence-architecture)
- [Screenshot OCR and Visual Analysis](#screenshot-ocr-and-visual-analysis)
- [Task Modes](#task-modes)
- [Secure Pipeline State and Error Model](#secure-pipeline-state-and-error-model)
- [Test Report](#test-report)
- [Firefox Extension and Native Messaging Installation](#firefox-extension-and-native-messaging-installation)

---

## Project Overview


## What the project does

This project captures the **currently active Firefox tab** through a Firefox WebExtension, saves the live DOM locally, extracts a target question/window from the HTML, and optionally sends the cleaned page to Ollama.

The browser-capture mechanism is now **Firefox Native Messaging**. The extension is a toolbar trigger rather than a continuously polling bridge.

## Architecture

```text
Firefox tab
    │
    │ click Scrapper Ollama toolbar button
    ▼
Firefox WebExtension
    │
    │ activeTab + one-shot DOM read
    ▼
Native Messaging
    │
    ▼
Python native host
    │
    ├── save timestamped raw HTML
    └── start local pipeline in a Windows console
             │
             ▼
      data/raw_pages/
             │
             ▼
      deterministic HTML extractor
             │
        ┌────┴────┐
        │         │
    confident   uncertain
        │         │
        │         ▼
        │    small Ollama verifier
        │         │
        └────┬────┘
             ▼
   data/clean_window_pages/
             │
             ▼
       cleaned task window
             │
        task classifier
      ┌──────┼─────────┐
      ▼      ▼         ▼
    basic  file_task  sandbox
      │      │         │
      │      ▼         │
      │ Docker       frame-aware
      │ quarantine   interaction (planned)
      └──────┼─────────┘
             ▼
       answer / action
             │
             ▼
      main Ollama model
          /          \
      certain      uncertain
         │             │
         ▼             ▼
 data/answer/   data/answer_not_found/
```

### Browser communication

There is **no localhost HTTP server or listening scraper port** anymore. Firefox launches the native host through its native-messaging mechanism and exchanges JSON over the native application's standard input/output. The native-host manifest is registered in Windows for the current user.

## Automatic dependency installation

The project includes a Windows setup/bootstrap path so the application does not assume Python, Docker Desktop, Tesseract, Ollama, Firefox, or the host Python libraries are already installed.

First install:

```bat
setup.bat
```

From an existing Python installation:

```bat
python scripts/setup_windows.py
```

Or through the normal CLI:

```bat
python main.py --setup-dependencies
```

The setup verifies the Docker daemon and builds the reusable quarantine image, installs configured Ollama models, verifies Tesseract language data, and registers the Firefox Native Messaging host. See this document for the complete bootstrap flow and Windows prerequisites.

## Firefox trigger

The extension has two important permissions:

- `activeTab` — the extension acts on the tab the user explicitly triggers from.
- `nativeMessaging` — Firefox can launch the local Python native host.

The toolbar click determines the target tab. There is no 5-second countdown anymore because the tab being captured is the tab associated with the click itself.

The extension executes a one-shot DOM read on that tab and sends the captured `{url, title, html}` object to the native host.

The extension does not call `reload()`, navigate the page, submit forms, or start a polling loop.

## Native Messaging installation

Run from the project root on Windows:

```powershell
python native_messaging/install_native_host.py
```

The installer:

1. creates a Windows `.bat` launcher using the Python interpreter that ran the installer;
2. writes the Firefox native-host manifest;
3. registers the manifest under the current user's Firefox native-messaging registry key.

The native manifest allows only the project's explicit extension ID:

`active-firefox-scraper@example.local`

Uninstall later with:

```powershell
python native_messaging/uninstall_native_host.py
```

After changing the extension manifest or native-host registration, restart/reload Firefox as appropriate.

## Native host responsibilities

`native_messaging/native_host.py` is intentionally small. It:

1. reads one framed JSON message from Firefox;
2. validates the capture request;
3. saves the raw HTML using a UTC timestamp filename;
4. starts `main.py --process-existing --file <raw-file>` in a new Windows console;
5. sends a small success/error response back to Firefox.

The native host does not run the heavy Ollama inference itself. The separately launched Python pipeline owns the interactive terminal, so missing-model installation prompts still work normally.

## Answer-model output discipline

The main answer model is called once for the final reasoning stage. The CLI diagnostic block does not print the answer; the user-facing answer is emitted exactly once under `[FINAL ANSWER]`. The answer prompt requires concise output containing only the main information that distinguishes the correct answer from alternatives, with no question restatement or long explanation. The stored decision JSON keeps the model reason separately for diagnostics.

## Secure download and file-processing pipeline

Downloaded content is treated as **untrusted raw bytes**. The production path has no host-side parser fallback.

```text
CAPTURE
  ↓
TASK_DETECTION
  ↓
DOWNLOAD_DISCOVERY
  ↓
URL / SCHEME / DNS POLICY
  ↓
DOWNLOAD
  ↓
QUARANTINE ARTIFACT FINALIZATION
  ↓
HOST SHA-256
  ↓
METADATA
  ↓
TYPE DETECTION
  ↓
DISPOSABLE DOCKER INSPECTION
  ↓
SECURITY VALIDATION
  ↓
URL/TYPE CONSISTENCY
  ↓
FORMAT / CONTAINER VALIDATION
  ↓
RECURSIVE MEMBER INSPECTION
  ↓
FORMAT PROCESSING
  ├── image → decode → dimensions/metadata → OCR → sanitized PNG
  ├── document → text/relationships → embedded images → image pipeline
  └── text → bounded deterministic text processing
  ↓
EVIDENCE / PROVENANCE
  ↓
TASK SOLVING
  ↓
ANSWER VALIDATION
  ↓
FINAL ANSWER
  ↓
CLEANUP
```

### Network and download boundary

The downloader accepts only configured schemes, currently `http` and `https`. It validates the initial URL and every redirect against the same origin/explicit host allow-list and IP destination policy. Private, loopback, link-local, multicast, reserved, and unspecified destinations are rejected by default. An optional configured allow-list can explicitly permit ranges when required.

DNS answers are pinned for the request/redirect chain when `download_dns_pin` is enabled, reducing the time-of-check/time-of-use window between DNS validation and the actual socket connection. Redirects are counted and recorded in the redirect chain. URL length, response header size, response size, and content type are bounded.

Download metadata records:

- source URL
- final URL
- redirect chain
- URL extension (`null` when the URL path has no extension)
- Content-Type
- Content-Disposition
- filename
- byte size
- SHA-256

A URL extension is **only a consistency signal**. If it is absent, no URL-extension comparison is performed. If it is present and disagrees with the byte-detected canonical type, the file is rejected.

### Quarantine and integrity

The host establishes the exact download bytes and SHA-256, then passes the same bytes to a fresh Docker container over stdin. The worker independently hashes the received bytes and sends a receipt before deeper processing. The host verifies both the receipt hash and the final worker-result hash.

For host-side transient files, deletion is allowed only after the worker acknowledges matching bytes. Quarantine artifacts are retained by hash and accompanied by provenance metadata until cleanup policy permits deletion. User-supplied attachments are not automatically deleted.

There is no production `local` parser backend. Tests use an explicit in-process Docker-worker double so the production security boundary cannot silently be bypassed by a test fixture.

### Docker isolation

The default container runtime uses:

- network disabled;
- read-only root filesystem;
- all capabilities dropped;
- `no-new-privileges`;
- fixed non-root UID/GID;
- CPU, memory, PID, timeout and bounded temporary-storage limits;
- no project/home/Docker-socket mounts;
- `--pull=never` so the already-installed image is used;
- `--rm` disposable containers;
- immutable local image-ID recording for provenance.

`quarantine_prefer_rootless` records whether the Docker daemon reports rootless support. The application does not claim rootless isolation when the runtime does not provide it.

### Archive and container security

ZIP-based containers such as DOCX and ODT are inspected member-by-member without blindly extracting the whole archive to the host filesystem.

The configured limits cover archive size, total decompressed size, individual member size, member count, compression ratio, recursion depth, nested archive count, total recursive members, XML size, processing time, embedded-image count, and image decoded pixels.

Archive names are checked for:

- path traversal;
- absolute and Windows drive paths;
- symlinks;
- duplicate canonical names;
- control characters;
- Windows reserved device names;
- Windows-illegal filename characters;
- excessive member-name length.

Every non-directory member receives its own SHA-256, byte-derived type, canonical extension, MIME observation, parent archive hash, member path, depth, security findings, and processing status. Nested ZIP members recursively follow the same policy under strict depth/count limits.

DOCX/ODT processing also checks package structure, relationships, external targets, macros/VBA, ActiveX, scripts, executable members, and embedded active content. External document resources are never fetched; the default policy rejects external relationships.

### Image processing boundary

Accepted images are parsed only inside the Docker worker. The image path validates the format and decoded dimensions, bounds pixel count, records metadata/selected EXIF fields, verifies image integrity, then re-encodes the decoded pixels to a bounded normalized PNG.

Tesseract OCR runs only against the normalized PNG, never the original untrusted image bytes. The worker returns the normalized image bytes only as bounded, hashable evidence artifacts. Optional vision reasoning runs outside the container only against those sanitized PNGs; the original attachment is never sent to the vision model.

### Document processing boundary

DOCX and ODT are validated as containers first. Only after the security gate succeeds are text, relationships, embedded images, OCR evidence, and other structured content extracted. Embedded images use the same image-validation path as downloaded image files.

The parser layer is therefore downstream of the security decision and remains inside the Docker trust boundary.

### Identity versus security

File identity and security are represented separately. The evidence records both what the bytes are and what policy findings were observed.

Typical identity evidence includes:

```text
detected_kind
canonical_extension
signature_match
container_match
mime_type
validation_strength
```

Typical security evidence includes:

```text
security_status
security_findings
url_extension
url_match
mime_match
active-content findings
archive findings
```

A technically valid but unsupported format is not silently treated as safe or rejected as malware; it receives an explicit `UNSUPPORTED`/processing result.

### Final processing states

The pipeline distinguishes:

```text
ACCEPTED
REJECTED
UNSUPPORTED
INCONCLUSIVE
ERROR
```

Timeouts and cancellations are explicit statuses in the pipeline state/error model. A prerequisite failure stops downstream work and preserves the first meaningful failure.

### Cleanup

Successful processed downloads are eligible for deletion only when the final validated answer confidence is **strictly greater than 0.90**. `0.90` is not sufficient.

Rejected, error, inconclusive, security-failure, and dependency-failure artifacts are retained according to configured retention policy so they remain diagnosable.

## Data layout

Runtime and evidence data stays under `data/`:

```text
data/
├── raw_pages/               # raw Firefox DOM captures
├── clean_window_pages/      # cleaned task windows
├── answer/                  # successful answer JSON
├── answer_not_found/        # explicit no-answer JSON
├── review/                  # extraction ambiguity/review data
├── errors/                  # machine-readable processing errors
├── downloads/
│   ├── quarantine/          # hash-addressed retained artifacts
│   ├── evidence/            # canonical post-validation evidence
│   └── metadata/            # concise processing metadata
├── dependency_report.json   # latest setup dependency health report
└── debug/ollama/            # inference timing/token diagnostics
```

Quarantine artifacts are addressed by SHA-256 rather than user-controlled filenames. Original filenames remain metadata only.

## Unified file evidence

After a download has passed the Docker quarantine validation, the file is processed into one stable evidence object. This is the handoff boundary between secure file processing and the thinking model.

```text
Download
   ↓
Docker quarantine
   ↓
SHA-256 + actual type + structural/security validation
   ↓
Evidence stages
   ├── metadata
   ├── structure
   ├── text
   ├── tables
   ├── embedded content
   ├── images
   ├── OCR
   └── visual analysis
   ↓
Fixed evidence JSON
   ↓
Thinking model + webpage task
```

The evidence schema always contains `file`, `metadata`, `structure`, `text`, `tables`, `images`, `ocr`, `visual_analysis`, `embedded_content`, and `security`. When a category genuinely does not exist, it is represented as `status: "non-existent"` and its value fields are `null`. The schema is therefore stable across TXT, CSV, DOCX, ODT, images, and future formats.

The canonical evidence JSON is stored under `data/downloads/evidence/`. It contains structured information rather than a second copy of the original untrusted file.

The canonical evidence schema and responsibility split are documented in this section and implemented by `app/downloads/evidence.py`.

## Task modes

After the clean question/window has been extracted, the pipeline deterministically classifies it into one of three execution modes:

```text
clean page
   │
   ├── BASIC
   ├── FILE_TASK
   └── SANDBOX
```

`BASIC` covers ordinary questions and visible answer controls. `FILE_TASK` covers tasks that require a file or downloadable resource; those bytes continue through the Docker quarantine before they are parsed. `SANDBOX` covers stateful interactive simulators, including cross-origin embedded frames.

The precedence is `SANDBOX > FILE_TASK > BASIC`, so an embedded simulator remains a sandbox even when the surrounding page also exposes a download link. The classifier also records whether the expected response surface is a choice, text field, or unknown.

The sandbox interaction executor is intentionally a separate future layer. Its contract is documented in the Task Modes section below: observe the parent and sandbox frame, check completion, choose one action, execute it, and then re-observe from scratch. No multi-action plan is executed against a stale DOM snapshot.

Task-mode and iframe behavior is documented in the Task Modes section below and implemented by `app/task/`.

For downloaded-file evidence, see the File Evidence Architecture section below.

## Screenshot OCR and visual analysis

Firefox captures the visible tab alongside the live DOM. The screenshot is always retained for the capture, while post-capture analysis is configurable. The default OCR path uses Tesseract; optional visual analysis uses a configured vision-capable Ollama model. The thinking model remains the configured answer model and receives the structured screenshot evidence as supporting data.

```text
Firefox screenshot
      ↓
screenshot evidence builder
      ├── OCR (Tesseract)
      └── visual analysis (optional vision model)
      ↓
fixed screenshot evidence JSON
      ↓
answer model / thinking model
```

Screenshot evidence is stored under `data/screenshots/evidence/` and keeps a stable schema with `screenshot`, `ocr`, and `visual_analysis`. A stage that does not exist or is not configured is represented with a status and null value fields instead of changing the JSON shape.

Configuration lives in `config/vision_settings.json`:

```json
{
  "ocr_enabled": true,
  "ocr_executable": "tesseract",
  "ocr_language": "fra+eng",
  "visual_analysis_enabled": true
}
```

On Windows, install Tesseract separately and either put `tesseract.exe` on `PATH` or set `ocr_executable` to its full path. Missing language models are **not** written into `C:\Program Files\Tesseract-OCR\tessdata`, because that directory commonly requires elevation. Setup instead keeps a project-managed, user-writable cache under `data/tesseract/tessdata` and invokes Tesseract with `--tessdata-dir` when the requested languages are not already available in the system installation. The cache contains only the configured/requested language data. A vision model is optional; configure `vision_model` in `config/model_settings.json` only when a vision-capable Ollama model is installed.

Useful CLI controls are `--no-ocr`, `--ocr-language`, `--no-visual-analysis`, and `--no-screenshot`.

Screenshot OCR and vision behavior is documented in this section and implemented by `app/vision.py`.

## HTML window extraction

The extractor is rule-first. CSS selectors and required child selectors are configurable in `config/window_rules.json`; the built-in examples use generic task, question, and response containers:

```html
<div class="assessment-container">
  <article class="task-item" data-task-id="example-1">
    <section class="task-question">
      <p>Which action best improves account security?</p>
    </section>
    <form>
      <fieldset class="task-response">
        <legend>Choose one answer.</legend>
        <label><input type="radio" name="answer" value="1"> Use a unique password</label>
        <label><input type="radio" name="answer" value="2"> Reuse the same password</label>
      </fieldset>
    </form>
  </article>
  <aside class="task-feedback">Optional feedback outside the task</aside>
</div>
```

The extractor targets the configured task container and can leave sibling navigation, progress, or feedback elements outside the cleaned window. When no explicit selector matches, deterministic candidate scoring ranks plausible elements; the small Ollama verifier may adjudicate genuinely ambiguous candidates.

Selectors are examples, not universal rules. Different sites use different markup, so adapt `config/window_rules.json` and inspect cleaned output when onboarding another site.

## Ollama responsibilities

There are two intentionally separate model roles.

### Small model — window verifier

It does **not** answer the question.

It only evaluates ambiguous extraction candidates and returns a structured accept/reject decision plus a candidate index and confidence.

### Main model — answerer

It reads the clean question window and returns either:

```json
{
  "status": "answer",
  "confidence": 0.95,
  "answer": "...",
  "reason": "..."
}
```

or:

```json
{
  "status": "answer_not_found",
  "confidence": 0.42,
  "answer": null,
  "reason": "..."
}
```

Python performs the filesystem routing. The model does not execute file operations.

## Compact answer representation

The full cleaned HTML stays in `data/clean_window_pages/`.

By default, the answer model receives a compact semantic representation containing question text, instructions, answer choices/controls, relevant visible text, and useful image metadata instead of unnecessary HTML markup.

### Choice-answer normalization

For multiple-choice questions, the final answer is the **visible text of the selected choice**, not the HTML value or numeric position. The prompt explicitly requests this format, and Python performs a second deterministic normalization step. For example, a model response of `3` is converted to the third visible choice, such as `Enable two-factor authentication`. If a numeric choice cannot be matched to a real visible choice, the response is treated as invalid rather than guessed.

For comparison/debugging:

```powershell
python main.py --process-existing --answer-input-mode full
```

The compact representation significantly reduces prompt size on complex pages.

## Ollama performance debugging

The pipeline records model metrics when available, including model-load time, prompt evaluation time, generation time, token counts, total time and generation speed.

Inspect:

`data/debug/ollama/`

You can use these measurements to distinguish:

- model loading overhead;
- large prompt/context overhead;
- slow token generation;
- or failures before inference.

## Missing-model behavior

The application does not silently download models.

Run:

```powershell
python main.py --check-models
```

to inspect configured model availability.

During normal processing, if a model is missing the running console asks whether it should install it with `ollama pull`.

```text
Install this model now with 'ollama pull'? [y/N]:
```

- `N` / Enter → stop safely; the relevant raw/clean page is preserved.
- `Y` → install the model, then restart processing for the exact triggering raw page.
- failed installation → stop safely and record the error.

Because processing runs in the separate Windows console started by the native host, this interactive behavior remains available even when the extension itself launched the workflow.

## Useful commands

Check models:

```powershell
python main.py --check-models
```

Process existing raw captures:

```powershell
python main.py --process-existing
```

Process one raw capture:

```powershell
python main.py --process-existing --file 20260929T193745_123456Z.html
```

Use the complete clean HTML as answer-model input:

```powershell
python main.py --process-existing --answer-input-mode full
```

Run tests:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

## Project structure

```text
scrapper_active_firefox/
├── app/
│   ├── answer_representation.py
│   ├── answering/
│   │   ├── deterministic.py
│   │   └── choice_resolver.py
│   ├── downloads/
│   │   ├── detector.py        # byte signatures / actual format detection
│   │   ├── rules.py           # config-driven policy
│   │   ├── security.py        # validation gate
│   │   ├── docker_quarantine.py
│   │   ├── evidence.py        # fixed evidence JSON contract
│   │   ├── extract.py         # safe extraction coordinator
│   │   ├── workflow.py        # fetch → quarantine → evidence → cleanup
│   │   ├── downloader.py
│   │   ├── hints.py
│   │   └── models.py
│   ├── native_capture.py
│   ├── pipeline.py
│   ├── cli.py
│   ├── cli_view.py
│   ├── vision.py          # screenshot OCR + optional visual analysis
│   ├── debug/
│   ├── errors/
│   ├── ollama/
│   ├── parsing/
│   ├── storage/
│   ├── task/
│   └── window/
├── config/
│   ├── model_settings.json
│   ├── vision_settings.json
│   ├── download_rules.json
│   └── window_rules.json
├── data/
│   └── downloads/evidence/  # canonical post-validation evidence JSON
├── firefox_extension/
│   ├── background.js
│   └── manifest.json
├── native_messaging/
│   ├── native_host.py
│   ├── install_native_host.py
│   ├── uninstall_native_host.py
│   └── ...generated host files...
├── quarantine/
│   ├── Dockerfile
│   ├── worker_entry.py
│   └── requirements.txt
├── scripts/
│   └── build_quarantine_image.py
├── setup.bat
├── prompts/
├── information/
│   └── INFORMATION.md
├── CHANGES/
│   ├── 00-change-history.md
│   └── ...
├── tests/
├── main.py
└── setup.bat
```

The file subsystem has one clear direction of travel:

```text
fetch / receive
     ↓
Docker quarantine
     ↓
actual type + security validation
     ↓
post-validation evidence stages
     ↓
canonical evidence JSON
     ↓
thinking model
```

`detector.py` identifies what the bytes actually are. `rules.py` says what the configuration permits. `security.py` decides whether processing may continue. `evidence.py` does not make security decisions; it only builds the stable evidence interface after validation.

## Privacy / connection model

The scraper capture path is local after the page has already been loaded in Firefox:

```text
Website → Firefox
              │
              │ read existing DOM
              ▼
        Firefox extension
              │
              │ native messaging
              ▼
        local Python host
              │
              ▼
        local filesystem
              │
              ▼
          local Ollama
```

The project no longer creates a localhost HTTP listener for capture. The extension does not reload the active page as part of capture.

## CLI observability

The visible processing console now reports the capture, deterministic task classification, sandbox frame details, screenshot/OCR/vision stages, answer-model payload summary, Ollama timings, and final result. The detailed view is intended to make failures diagnosable without opening debug JSON files first.

A typical run is organized as:

```text
[CAPTURE]
[TASK]
[SANDBOX]        # only when a simulator is detected
[CLEAN]
[SCREENSHOT]
[DOWNLOAD]        # only when a file task needs one
[ANSWER INPUT]
[OLLAMA]
[RESULT]
```

The model profile JSON files remain available under `data/debug/ollama/` for deeper inspection.

## Answer strategy

The answer stage is now two-tiered. Before Ollama is called, `app/answering/deterministic.py` checks whether the task is a small, exact operation that Python can solve safely and repeatably, such as replacing text and counting characters/words. These tasks bypass Ollama and receive a deterministic confidence of `1.0`.

Tasks that are not explicitly supported by the deterministic solver continue to the configured answer model. This keeps the 8B model focused on interpretation and reasoning rather than mechanical calculations.

The final user-facing line in the processing console is only the answer text. Reasoning, confidence, solver/model metadata, and diagnostics remain in JSON/debug output.

This is a reduction in unnecessary local network surface, not a guarantee about what a website can infer from its own normal browser/network activity.

## Ollama model configuration

The selected Ollama models are configured in `config/model_settings.json`:

```json
{
  "window_verifier_model": "qwen3:4b",
  "answer_model": "qwen3:8b"
}
```

Change this file to select the models used by default. The command-line options `--window-model` and `--answer-model` override these values for a single run.

The answer model defaults to `qwen3:8b` because it is lighter than the previous `qwen3:14b` default.
## Model installation restart behavior

When a configured Ollama model is missing and the user accepts installation, the program runs `ollama pull`, starts a fresh pipeline process for the same raw capture, and exits the stale process. On Windows the visible pipeline console is started with `cmd /d /k` so it remains open during and after the resumed run. If the replacement process cannot be started, the current process reports the restart error and stops without deleting the raw/clean page.

---

## Installation and Dependency Bootstrap


The project targets Windows and can install the runtime stack it needs instead of assuming the machine is already prepared.

## What the setup manages

The setup checks and, after confirmation, can install:

- Python 3.13+ runtime
- Host Python libraries (`beautifulsoup4` and `defusedxml>=0.7.1`), installed directly by `setup.bat`
- Firefox
- Docker Desktop and a ready Docker daemon
- Tesseract OCR
- English and French Tesseract language data when missing
- Ollama
- Every Ollama model named in `config/model_settings.json`, including the optional vision model when configured
- The reusable Docker quarantine image
- Firefox Native Messaging registration

WinGet is available as an installation backend for Windows applications, but `setup.bat` does not invoke direct application installs. `scripts/setup_windows.py` first detects an installed application and installs it only when it is missing. WinGet requests use `--no-upgrade` as an additional guard against replacing/upgrading an existing package. Microsoft documents exact-ID installation and the `--no-upgrade` option. citeturn921542search1
Dependency diagnostics also verify that the configured Docker quarantine image exists locally when the Docker daemon is ready; this check does not install or modify Docker images.

Python's current Windows distribution provides the Python install manager, which can be installed via WinGet and can install Python 3.13 with `py install 3.13`.

Docker Desktop is installed through its Windows installer/WinGet package and must have a working daemon before the quarantine image can be built. Docker's documentation also documents command-line installation. citeturn349093search12

Tesseract's Windows documentation points to the UB Mannheim Windows installers; this project uses the corresponding WinGet package when available and then verifies the requested language data. citeturn349093search0turn349093search5

Ollama's official Windows installation is available for Windows 10+ through Ollama's Windows installer. The project only invokes an Ollama installer when no existing Ollama executable is detected; an existing installation is preserved and its detected version is reported. citeturn921542search0

## First install

Run:

```bat
setup.bat
```

The batch bootstrap first ensures Python is available, then launches the Python dependency manager.

Once Python already exists, the same process can be started with:

```bat
python scripts/setup_windows.py
```

`setup.bat` is the normal one-step installation entry point. It installs the Windows applications, host Python libraries, required Tesseract language data, configured Ollama models, the Docker quarantine image, and the Firefox Native Messaging registration.

## Command-line setup

The normal application can also run the dependency manager with:

```bat
python main.py --setup-dependencies
```

This verifies the same dependency stack and registers the native Firefox host when setup completes successfully.

## Python libraries

Host-side libraries are installed using:

```bat
setup.bat installs `beautifulsoup4` and `defusedxml>=0.7.1` directly with pip.
```

The quarantine image installs its own Python dependencies from `quarantine/requirements.txt` during the image build, so host Python packages and quarantine packages remain separate.

## Docker

The installer starts Docker Desktop when it is installed but the daemon is not ready. If Windows virtualization/WSL requirements prevent Docker from starting, setup stops and reports the problem rather than pretending the quarantine is available.

The quarantine image is built once:

```bat
python scripts/build_quarantine_image.py
```

Each downloaded file then uses a disposable container created from that image.

## Tesseract

Screenshot OCR is only attempted after the screenshot exists. When OCR is enabled and Tesseract is missing, the live pipeline can ask whether to install it. Setup and the live pipeline both verify the configured language data (`eng` and `fra` by default).

Missing language models are installed into the project-managed user-writable cache `data/tesseract/tessdata`; the Windows system Tesseract installation is left unchanged. This avoids requiring administrator rights merely to add a `.traineddata` file beneath `Program Files`. When the cache is used, the runtime passes `--tessdata-dir` to Tesseract so the OCR operation uses the verified cache explicitly.

The setup therefore distinguishes the Tesseract executable from its language data: an installed `tesseract.exe` is not considered fully ready until the configured OCR languages can be verified either in the system installation or in the managed cache.

Tesseract is not required for the core HTML capture pipeline when screenshot OCR is disabled.

## Firefox

The project depends on Firefox for the extension-based capture. The installer can install Firefox, but Firefox WebExtension installation remains a browser-side action; the project does not silently force-install an unsigned extension.

Native Messaging registration is automated with:

```bat
python native_messaging/install_native_host.py
```

## WinGet bootstrap limitation

WinGet/App Installer is a Windows platform component. If `winget.exe` is not available, `setup.bat` reports this instead of downloading an arbitrary package-manager executable from the Internet. Install Microsoft App Installer/WinGet once, then rerun the bootstrap.

## Verification

A successful setup finishes with a summary for every dependency. Any missing dependency is explicitly marked `MISSING`; the program never claims setup is complete while a required component is absent.

---

## Security Model

The project assumes every downloaded file can be malicious or malformed, regardless of URL, extension, MIME type, filename, or server reputation.

## Production security boundary

Windows owns browser interaction, bounded downloading, hashing, policy orchestration, and cleanup. Untrusted format parsing is performed inside the disposable Linux Docker worker.

```text
Windows host
  ├── Firefox / Native Messaging
  ├── bounded URL fetch
  ├── SHA-256 / provenance
  ├── antimalware integration
  └── Docker orchestration
          │ stdin: exact bytes
          ▼
Disposable Docker worker
  ├── signature/type detection
  ├── archive/member recursion
  ├── container validation
  ├── document/image parsing
  ├── OCR
  └── structured JSON evidence
```

The host does not open downloads in Word, LibreOffice, a browser, an image viewer, a shell, or an arbitrary external application.

## Parser trust boundary

The security design intentionally separates orchestration from untrusted-format parsing. Signature detection and byte hashing on the host are integrity/orchestration operations. Container parsing, image decoding, XML processing, document extraction, archive recursion, and OCR remain inside Docker.

Optional visual reasoning is a sanitized-data handoff: the Docker worker emits a bounded normalized PNG only after image validation, and the host-side vision client sees that PNG rather than the original attachment.

## External resources

Document relationships and similar references are parsed without fetching their targets. The worker runs with no network access, and the default policy rejects external relationships/resources. This applies even when a document contains an otherwise valid supported format.

## Machine-readable errors and state

Every major stage emits a structured record containing the pipeline stage, operation, status, error code, message, exception type, input/output summaries, timestamp, duration, previous/next stage where known, file hash, and request ID.

The top-level state machine is monotonic: stages cannot transition backwards. The first meaningful failure is retained, and downstream stages are not entered after a failed prerequisite.

Error-code families are centralized in `app/downloads/error_codes.py`:

```text
DOWNLOAD_*      QUARANTINE_*     HASH_*          METADATA_*
TYPE_*          ARCHIVE_*        SECURITY_*      ANTIMALWARE_*
IMAGE_*         OCR_*            DOCUMENT_*      VISION_*
TASK_*          MODEL_*          ANSWER_*        CLEANUP_*
DEPENDENCY_*
```

## Evidence and AI handoff

Evidence is lineage-aware. Archive members carry their own SHA-256 and parent archive hash; image evidence records the hash of the normalized PNG used for OCR/vision.

The answer model receives an allow-listed semantic context containing task/question/instructions/choices, relevant page context, document text, OCR, visual evidence, and selected file metadata. Host paths, quarantine locations, internal debug fields, archive extraction paths, and raw provenance hashes are intentionally filtered out of the AI prompt.

## Antimalware

Windows Defender/Microsoft Defender is an additional security layer, not a mathematical proof of safety. `antimalware.mode` supports `off`, `auto`, and `required`. In `required` mode, scan unavailability/failure prevents acceptance. Findings are stored with the affected artifact hash.

## Dependency diagnostics

Windows setup writes `data/dependency_report.json`, a machine-readable health report containing detected versions/paths, required versions or configured requirements, affected pipeline stages, and a remediation hint for missing dependencies.

## Security language

The project intentionally avoids claims such as "bulletproof", "100% safe", or "malware-proof". Acceptance means only that the artifact passed the configured validation and security policy.

## File Evidence Architecture


## Purpose

`app/downloads/` is responsible for handling untrusted files and producing a stable, structured evidence package for the thinking model.

The file bytes are validated **first** inside the configured quarantine backend. Only after validation succeeds do evidence-producing stages run.

```text
Firefox / direct URL
        |
        v
   quarantine handoff
        |
        v
 hash + actual type + structure + security validation
        |
        +---- rejected ----> stop / retain according to policy
        |
        v
   EVIDENCE EXTRACTION
        |
        +--> metadata
        +--> structure
        +--> text
        +--> tables
        +--> embedded content
        +--> images
        +--> OCR
        +--> visual analysis
        |
        v
   fixed evidence JSON
        |
        v
 thinking model
        |
        v
      answer
```

## Canonical JSON contract

The evidence object always contains the same top-level sections:

- `file`
- `metadata`
- `structure`
- `text`
- `tables`
- `images`
- `ocr`
- `visual_analysis`
- `embedded_content`
- `security`

A section that genuinely does not exist uses:

```json
{
  "status": "non-existent",
  "value_1": null,
  "value_2": null
}
```

The shape does not disappear just because a file type does not support that category.

A present section uses `status: "present"` and populated values. Unknown or unavailable individual values remain `null`; they are not guessed.

## Responsibilities

### `detector.py`

Determines actual file type from bytes and safe container structure. Filename extensions are only hints.

### `rules.py`

Loads `config/download_rules.json`. This is the source of truth for `accepted_extensions` and quarantine limits.

### `security.py`

Applies the configured policy after byte/structure detection and performs unconditional structural safety checks.

### `docker_quarantine.py`

Creates one disposable Docker worker for each untrusted document. It enforces the isolation boundary and hash handoff.

### `extract.py`

Coordinates safe extraction for formats that currently have direct text parsers. It invokes `evidence.py` only after validation succeeds.

### `evidence.py`

Owns the stable JSON schema and converts validated extraction results into the canonical evidence interface. It must not decide whether a download is allowed.

### `workflow.py`

Owns the download lifecycle: fetch, quarantine, verify, extract, retain/delete, and metadata/evidence persistence.

## Image-capable documents

The design deliberately separates:

1. **image existence** — the file or container actually contains image data;
2. **OCR** — readable text recovered from image pixels;
3. **visual analysis** — semantic information obtained from the pixels.

A DOCX, ODT, PDF, spreadsheet, presentation, archive, or other structured format should be inspected for embedded images whenever its validated structure can contain them.

The thinking model receives the combined evidence package rather than raw bytes. The byte parsing, metadata extraction, image decoding and OCR remain in the evidence pipeline; a vision-capable Ollama stage may add `visual_analysis` from sanitized image data. Page screenshots have a separate stable evidence contract documented in the Screenshot OCR and Visual Analysis section above.

## Persistence

The canonical evidence is saved under:

```text
data/downloads/evidence/<job>.json
```

This is structured evidence, not a copy of the original downloaded bytes.

The original downloaded file remains subject to the configured quarantine lifecycle and high-confidence cleanup policy.

## Model handoff

The answerer receives:

```text
page task
+ page context
+ attachment evidence JSON
+ extracted attachment text
```

The task remains primary. The evidence is supporting material. Instructions found inside a downloaded file are treated as untrusted data and never override the page task.

---

## Screenshot OCR and Visual Analysis


## Purpose

Every Firefox capture contains two complementary representations of the active tab:

1. the live DOM;
2. a PNG screenshot of the visible tab.

The screenshot is useful when information is visible to a human but is absent, fragmented, canvas-rendered, or poorly represented in the DOM.

## Processing contract

```text
Firefox
  ↓
PNG screenshot
  ↓
screenshot evidence builder
  ├── dimensions / capture metadata
  ├── OCR
  └── optional visual analysis
  ↓
stable screenshot evidence JSON
  ↓
thinking model
```

The screenshot is never treated as an answer by itself. OCR and visual analysis provide evidence; the configured answer model reasons over that evidence together with the page task.

## Stable JSON schema

Every screenshot evidence object contains these sections:

- `screenshot`
- `ocr`
- `visual_analysis`

A stage that does not exist uses `status: "non-existent"` and null values. A configured stage that cannot run uses `status: "unavailable"`; an execution failure uses `status: "error"`. The JSON shape is stable in all cases.

Example:

```json
{
  "schema_version": "1.0",
  "screenshot": {
    "status": "present",
    "path": "20261001T195700_004358Z.png",
    "width": 1920,
    "height": 1080
  },
  "ocr": {
    "status": "present",
    "engine": "tesseract",
    "language": "fra+eng",
    "text": "...",
    "character_count": 1234
  },
  "visual_analysis": {
    "status": "present",
    "model": "vision-model",
    "summary": "...",
    "observations": ["..."],
    "confidence": 0.92
  }
}
```

## OCR

The default OCR engine is Tesseract. The language list is configurable, for example `fra+eng`. If Tesseract is not installed or the configured languages are unavailable, OCR is marked `unavailable`; the rest of the pipeline can continue.

On Windows, `ocr_executable` may be a bare `tesseract` command when it is on `PATH`, or a full path to `tesseract.exe`.

## Visual analysis

Visual analysis is optional. When `vision_model` is configured and installed, the screenshot is sent to that model before the answer model runs. The vision model is asked only for visual evidence: layout, visible labels, controls, charts, objects, and similar observations. It does not answer the page task.

The answer model remains the configured `answer_model` and receives the `screenshot_evidence` object alongside the normal page task and any file evidence. This keeps the vision model as an evidence producer rather than replacing the reasoning model.

## Storage

Screenshots live in:

```text
data/screenshots/
```

The structured screenshot analysis is saved in:

```text
data/screenshots/evidence/<screenshot>.json
```

The evidence JSON contains no raw copy of a second screenshot; the PNG remains the single image artifact.

## CLI

Useful switches:

- `--no-ocr` disables OCR for a run;
- `--ocr-language fra+eng` selects Tesseract languages;
- `--no-visual-analysis` disables the optional vision-model stage;
- `--no-screenshot` disables screenshot processing by the Python pipeline for that run.

The processing console displays a dedicated `[SCREENSHOT]` section with the screenshot path, dimensions, OCR status/preview, visual-analysis status, and evidence path.

---

## Task Modes


The pipeline now classifies each cleaned page into one of three top-level task modes before answering:

```text
Captured page
    ↓
Deterministic task classifier
    ├── BASIC
    ├── FILE_TASK
    └── SANDBOX
```

The classifier is deliberately rule-first. It does not ask Ollama which mode the page belongs to.

## 1. BASIC

A normal question where the page itself contains the information needed to answer.

Typical response surfaces are:

- multiple choice / radio buttons / checkboxes / select controls;
- an ordinary text input or textarea;
- a question with no visible answer control yet (for example, a page that only needs an answer representation before a later interaction step).

Examples:

```text
"Quelle est la capitale de l'Italie ?"
```

or a set of visible radio-button choices.

## 2. FILE_TASK

A page whose task requires a local file or downloadable resource as supporting material.

The existing Docker quarantine pipeline remains responsible for the untrusted bytes:

```text
page
  ↓
find/identify download
  ↓
Docker quarantine
  ↓
extract safe plain text
  ↓
solve the requested operation
```

The classifier marks the page as `file_task` when it detects a strong file-task signal such as a download link, configured download URL attribute, explicit download wording, or a local file input. These signals are distinct: a local `input[type=file]` is an upload/local-file control and is **not** treated as evidence that a download exists.

Multiple candidate URLs are not silently guessed. The classification reports `ambiguous_download=true` when the download signal exists but the candidate selector cannot safely choose one.

## 3. SANDBOX

A page containing a stateful interactive simulator, game-like task, or similar embedded environment.

A sandbox commonly lives in an iframe. The parent document and the embedded document are separate interaction surfaces, and the parent's HTML capture generally does not contain the iframe's inner DOM.

For example, a generic page may embed an interactive environment like this:

```html
<div class="interactive-sandbox-container">
    <iframe
        class="sandbox-frame"
        src="https://sandbox.example.test/interactive/demo"
        title="Interactive simulator">
    </iframe>
</div>
```

The parent page can change when the user starts or acknowledges an activity even when the iframe URL remains the same. Therefore, a sandbox should be treated as a separate frame-level state machine.

## Sandbox observation loop

The intended future interaction loop is one action per reasoning cycle:

```text
OBSERVE parent + sandbox frame
        ↓
CHECK completion
        ├── DONE → stop
        └── NOT_DONE / UNKNOWN
                 ↓
        choose exactly ONE action
                 ↓
        validate action against current state
                 ↓
        execute action
                 ↓
        wait for the expected UI update
                 ↓
        OBSERVE again from scratch
```

The page is the source of truth after every action. A previously captured DOM/state snapshot must never be reused to issue a sequence of actions because the action may mutate the page or the embedded application.

Completion detection should be deterministic when the sandbox exposes a reliable signal (status element, success class/state, completed objective, etc.) and can fall back to AI interpretation when the DOM does not expose an explicit completion condition.

## Frame handling

`TaskClassification.sandbox_frames` records the sandbox iframe candidates and whether the frame is same-origin with the parent page when the origin can be determined.

The classifier deliberately does **not** treat every iframe as a sandbox. A plain video/map/advertising iframe is not enough. Strong signals currently include:

- sandbox/simulator wording in ancestor CSS classes;
- sandbox/simulator wording in the iframe title;
- sandbox/simulator wording in the iframe CSS classes.

When more than one iframe exists, only the iframe(s) meeting the sandbox threshold are reported.

## Mode precedence

When signals overlap, the deterministic precedence is:

```text
SANDBOX > FILE_TASK > BASIC
```

This avoids reducing a stateful embedded task to a normal file or question task merely because the surrounding page also contains a download signal.

The classifier still exposes overlapping signals. For example, a sandbox page that also contains a download link is classified as `sandbox`, while `download_candidate_count` remains available for the future controller.

## Current scope

This change adds the three-way task classification, sandbox-frame detection, response-surface classification, pipeline logging, JSON metadata, tests, and documentation.

It does **not** yet implement the page-interaction executor itself. That remains a separate layer so the final interaction mechanism can be tested independently of task classification and Docker file processing.

## Download-detection false-positive guard

Generic mentions of files or documents are not download signals. For example, a question that says it has "two files" in XLSX and ODS formats, followed by a text answer field, remains a `basic` task.

Download detection looks for explicit download affordances or wording (`download`, `télécharger`, download URL attributes, or a URL/link whose path clearly indicates a download). This separation prevents ordinary data-processing questions from entering the download-ingestion pipeline.

## File-task evidence handoff

`FILE_TASK` uses the same secure quarantine lifecycle regardless of whether the answer will ultimately use text, metadata, tables, embedded images, OCR, or visual analysis. The sequence is:

```text
validate inside quarantine
        ↓
extract evidence
        ↓
fixed evidence JSON
        ↓
thinking model
```

The thinking model receives the page task first and treats file evidence as supporting material. The canonical schema is described in the File Evidence Architecture section above.


## Screenshot evidence in all task modes

The page screenshot is a supporting evidence source for BASIC, FILE_TASK, and SANDBOX. The capture is made alongside the DOM. OCR and optional visual analysis are performed before the thinking model receives the final page/task context, so the model can use information that is visible but not represented reliably in HTML.

For SANDBOX specifically, screenshot evidence is supplementary to the live-frame state. It does not replace frame observation and should never be treated as proof that the sandbox is complete.

---

## Secure Pipeline State and Error Model

The file subsystem has its own detailed worker stages, while the top-level pipeline records the browser/task/download/answer lifecycle. Both are machine-readable.

The top-level stages are:

```text
CAPTURE
→ TASK_DETECTION
→ DOWNLOAD_DISCOVERY
→ DOWNLOAD
→ QUARANTINE
→ HASH
→ METADATA
→ TYPE_DETECTION
→ DOCKER_INSPECTION
→ SECURITY_VALIDATION
→ TYPE_CONSISTENCY
→ FORMAT_PROCESSING
→ IMAGE_OCR_VISION / DOCUMENT_TEXT
→ EVIDENCE_ASSEMBLY
→ TASK_SOLVING
→ ANSWER_VALIDATION
→ FINAL_ANSWER
→ CLEANUP
```

Each stage has an explicit terminal status. Failures carry a machine-readable code and the state preserves the first meaningful failure. Illegal backwards transitions are rejected by `app/pipeline_state.py`.

The Docker worker returns a versioned JSON schema containing the final status, exact SHA-256, validation, metadata, evidence, and its stage report. The host rejects missing/invalid schema data rather than trusting an arbitrary worker response.

The answer stage separately validates model confidence, optional `choice_index`, visible-choice mapping, and the normalized visible answer text. Cleanup uses `validated_confidence` and the strict configured condition `confidence > 0.90`.

## Test Report

## Scope

The regression suite covers the browser/native-messaging path, deterministic task solving, answer validation, the Docker quarantine contract, recursive archive security, URL/DNS policy, image/document evidence, dependency diagnostics, state/error propagation, cleanup policy, and configuration-driven security limits.

## Result

**181 automated tests passed, with 43 subtests passed and no test failures.**

The suite includes negative/security cases for:

- fake/wrong URL extensions and extensionless URLs;
- archive traversal, Windows-specific filenames, duplicates, malformed containers, excessive size/count/ratio, recursion depth and nested archives;
- per-member SHA-256/type/provenance;
- macro/VBA/ActiveX/script/executable/active-content findings;
- external document relationships;
- valid/corrupt/oversized images and sanitized-image handoff;
- OCR availability/error behavior and optional visual-analysis behavior;
- Docker-unavailable, worker-failure, malformed-result, schema-mismatch and hash-mismatch cases;
- host-source deletion only after verified byte handoff;
- stage failure propagation and backwards-transition prevention;
- invalid model choice indexes, unmatched choices, and validated confidence;
- strict cleanup threshold behavior (`> 0.90`, not `>= 0.90`);
- AI evidence filtering so internal paths/provenance/debug fields are not sent to the answer model;
- dependency health diagnostics.

## Actual Docker/runtime limitation

An actual production Docker daemon is not available in this test environment, so the suite does not claim a live Windows Docker Desktop integration test. The quarantine worker itself is executed directly as a subprocess, while Docker command construction, isolation flags, image identity checks, output-schema validation, and hash handoff are unit-tested with explicit test doubles/mocks.

The production project does **not** use the test double or host-side parsing backend.

## Windows-only limitation

The current test environment is not Windows, so the real Windows `cmd.exe` console lifecycle, registry-backed Native Messaging installation, Defender executable, and WinGet installers are verified by structural/unit tests rather than live installation claims.

## Latest verification

```text
python -m pytest -q
181 passed, 43 subtests passed
```

Additional verification performed for the packaged project includes Python bytecode compilation of changed modules and a direct subprocess smoke test of `quarantine/worker_entry.py` receipt/final JSON behavior.

## Firefox Extension and Native Messaging Installation

CURRENT INSTALLATION (Firefox + Native Messaging)

1. Run the single installer from the project root:
   setup.bat

2. The installer also registers the Firefox native host:
   python native_messaging/install_native_host.py

   This writes the native-host manifest and registers it under the current
   user's Firefox native-messaging registry key. No localhost port is opened.

3. In Firefox, open:
   about:debugging#/runtime/this-firefox

4. Click "Load Temporary Add-on..." and select:
   firefox_extension/manifest.json

5. Pin the "Scrapper Ollama Trigger" extension if desired.

6. Open the Firefox page you want to capture and click the extension toolbar
   button. The extension captures that exact active tab, passes the DOM through
   Firefox native messaging, and starts a new Windows console running the local
   Python pipeline for that timestamped raw file.

7. Remove the native-host registration later with:
   python native_messaging/uninstall_native_host.py

No HTTP bridge, localhost listening port, Selenium, Marionette, or Firefox
restart is required.

---

## Host Python dependencies

The single Windows installer installs these host-side Python packages directly:

```text
beautifulsoup4
defusedxml>=0.7.1
```

The Docker quarantine image has its own isolated dependency file at `quarantine/requirements.txt`; it is installed inside the image and is not installed into the host Python environment.
