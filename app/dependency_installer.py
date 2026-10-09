from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import default_base_dir

DEPENDENCY_CONFIG_FILE = "config/dependencies.json"
DEFAULT_CONFIG = {
    "python": {
        "minimum_version": "3.13",
        "winget_id": "Python.Python.3.13",
        "source": "winget",
    },
    "docker_desktop": {
        "winget_id": "Docker.DockerDesktop",
        "source": "winget",
    },
    "firefox": {
        "winget_id": "Mozilla.Firefox",
        "source": "winget",
    },
    "tesseract": {
        "winget_id": "UB-Mannheim.TesseractOCR",
        "source": "winget",
        "languages": ["eng", "fra"],
        "managed_tessdata_dir": "data/tesseract",
    },
    "ollama": {
        "winget_id": "Ollama.Ollama",
        "source": "winget",
        "official_install_ps1": "https://ollama.com/install.ps1",
    },
    "quarantine_image": {
        "image": "scrapper-ollama-quarantine:2.0",
    },
}


@dataclass(frozen=True)
class DependencyStatus:
    key: str
    label: str
    installed: bool
    detail: str


class DependencyInstallError(RuntimeError):
    """Raised when a required dependency cannot be installed or verified."""


def load_dependency_config(base_dir: Path | None = None) -> dict[str, object]:
    """Load dependency settings and merge them over safe project defaults."""
    base = Path(base_dir) if base_dir is not None else default_base_dir()
    path = base / DEPENDENCY_CONFIG_FILE
    result = json.loads(json.dumps(DEFAULT_CONFIG))
    if not path.exists():
        return result
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key].update(value)  # type: ignore[union-attr]
    return result


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in version.strip().split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def python_version_ok(minimum: str) -> bool:
    """Return whether the current interpreter meets the requested Python floor."""
    return _version_tuple(".".join(str(x) for x in sys.version_info[:3])) >= _version_tuple(minimum)


def executable(name: str) -> str | None:
    """Resolve an executable from PATH."""
    return shutil.which(name)


def _candidate_paths(key: str) -> list[Path]:
    """Return common Windows installation paths for dependencies whose PATH may be stale."""
    if os.name != "nt":
        return []
    program_files = Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"))
    candidates: dict[str, list[Path]] = {
        "firefox": [
            program_files / "Mozilla Firefox" / "firefox.exe",
            program_files_x86 / "Mozilla Firefox" / "firefox.exe",
            local_app_data / "Mozilla Firefox" / "firefox.exe",
        ],
        "docker_desktop": [
            program_files / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
            local_app_data / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe",
        ],
        "tesseract": [
            program_files / "Tesseract-OCR" / "tesseract.exe",
            local_app_data / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        ],
        "ollama": [
            local_app_data / "Programs" / "Ollama" / "ollama.exe",
            program_files / "Ollama" / "ollama.exe",
        ],
    }
    return candidates.get(key, [])


def resolve_dependency_executable(key: str, names: Iterable[str]) -> str | None:
    """Resolve an installed dependency from PATH or standard Windows locations."""
    for name in names:
        found = executable(name)
        if found:
            return found
    for candidate in _candidate_paths(key):
        if candidate.exists():
            return str(candidate)
    return None


def command_ok(command: list[str], *, timeout: int = 20) -> tuple[bool, str]:
    """Run a small verification command without raising; return status and combined output."""
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def winget_available() -> bool:
    """Return whether Windows Package Manager is available."""
    return os.name == "nt" and executable("winget") is not None


def run_winget_install(package_id: str, *, source: str = "winget") -> tuple[bool, str]:
    """Install one application using an exact WinGet package ID."""
    if not winget_available():
        return False, "WinGet is not available. Install Microsoft App Installer/WinGet first."
    command = [
        "winget",
        "install",
        "--id",
        package_id,
        "-e",
        "--source",
        source,
        "--no-upgrade",
        "--accept-source-agreements",
        "--accept-package-agreements",
        "--silent",
    ]
    try:
        result = subprocess.run(command, check=False)
    except OSError as exc:
        return False, f"Could not start WinGet: {exc}"
    if result.returncode != 0:
        return False, f"WinGet installation failed with code {result.returncode}."
    return True, f"Installed {package_id}."


def ollama_cli_version(executable_path: str | None = None) -> str | None:
    """Return the installed Ollama client/server version without installing or upgrading anything."""
    path = executable_path or resolve_dependency_executable("ollama", ["ollama"])
    if not path:
        return None
    ok, output = command_ok([path, "--version"], timeout=15)
    if not ok or not output:
        return None
    return output.strip()


def ensure_app(
    key: str,
    label: str,
    config: dict[str, object],
    *,
    prompt: bool = True,
) -> DependencyStatus:
    """Verify an external application and optionally install it through WinGet."""
    package_id = str(config.get("winget_id", ""))
    executable_names = {
        "docker_desktop": ["docker"],
        "firefox": ["firefox"],
        "tesseract": ["tesseract"],
        "ollama": ["ollama"],
    }.get(key, [])
    resolved = resolve_dependency_executable(key, executable_names)
    if resolved:
        if key == "ollama":
            version = ollama_cli_version(resolved)
            detail = f"{resolved}; {version}" if version else resolved
            print(f"[DEPENDENCY] Ollama already installed; preserving existing installation ({detail}).")
            return DependencyStatus(key, label, True, detail)
        return DependencyStatus(key, label, True, resolved)

    if not prompt:
        return DependencyStatus(key, label, False, f"missing ({package_id or 'no package id'})")

    print(f"[DEPENDENCY] {label} is missing.")
    answer = input(f"[DEPENDENCY] Install {label} now? [Y/n]: ").strip().lower()
    if answer not in {"", "y", "yes"}:
        return DependencyStatus(key, label, False, "installation declined")

    if not package_id:
        return DependencyStatus(key, label, False, "no installer package configured")
    ok, message = run_winget_install(package_id, source=str(config.get("source", "winget")))
    if not ok and key == "ollama" and config.get("official_install_ps1"):
        # Fallback to Ollama's official Windows installer when the WinGet package is not available.
        script_url = str(config["official_install_ps1"])
        print("[DEPENDENCY] WinGet package unavailable; using Ollama's official Windows installer.")
        try:
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-Command", f"irm '{script_url}' | iex",
                ],
                check=False,
            )
        except OSError as exc:
            return DependencyStatus(key, label, False, f"Could not start Ollama installer: {exc}")
        if result.returncode == 0:
            ok = True
            message = "Installed Ollama with the official Windows installer."
        else:
            message = f"Ollama official installer exited with code {result.returncode}."
    if not ok:
        return DependencyStatus(key, label, False, message)
    resolved = resolve_dependency_executable(key, executable_names)
    return DependencyStatus(key, label, resolved is not None, message if resolved else "installed but executable not found yet")


def ensure_python_packages(base_dir: Path, *, prompt: bool = True) -> DependencyStatus:
    """Install all host-side Python libraries declared in config/dependencies.json."""
    dep_config = load_dependency_config(base_dir)
    packages = dep_config.get("python_packages", ["beautifulsoup4", "defusedxml>=0.7.1"])
    if not isinstance(packages, list):
        packages = ["beautifulsoup4", "defusedxml>=0.7.1"]
    packages = [str(package).strip() for package in packages if str(package).strip()]

    if not packages:
        return DependencyStatus("python_packages", "Python libraries", True, "no host Python packages configured")

    ok, _ = command_ok([sys.executable, "-m", "pip", "--version"])
    if not ok:
        return DependencyStatus("python_packages", "Python libraries", False, "pip is unavailable")

    if prompt:
        answer = input(
            "[DEPENDENCY] Install/upgrade host Python libraries "
            f"({', '.join(packages)})? [Y/n]: "
        ).strip().lower()
        if answer not in {"", "y", "yes"}:
            return DependencyStatus("python_packages", "Python libraries", False, "installation declined")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *packages],
            check=False,
        )
    except OSError as exc:
        return DependencyStatus("python_packages", "Python libraries", False, str(exc))

    if result.returncode != 0:
        return DependencyStatus(
            "python_packages",
            "Python libraries",
            False,
            f"pip exited with {result.returncode}",
        )

    return DependencyStatus(
        "python_packages",
        "Python libraries",
        True,
        "configured host Python packages installed",
    )


def find_tesseract() -> str | None:
    """Find tesseract.exe through PATH and standard Windows install locations."""
    found = executable("tesseract")
    if found:
        return found
    if os.name != "nt":
        return None
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _normalize_tessdata_base(path: Path) -> Path:
    """Normalize a configured Tesseract data base directory."""
    candidate = Path(path)
    if candidate.name.lower() == "tessdata":
        return candidate.parent
    return candidate


def installed_tesseract_languages(executable_path: str, tessdata_base: Path | str | None = None) -> set[str]:
    """Return available Tesseract language codes, optionally from a custom data base directory."""
    command = [executable_path, "--list-langs"]
    if tessdata_base is not None:
        command.extend(["--tessdata-dir", str(_normalize_tessdata_base(Path(tessdata_base)))])
    ok, output = command_ok(command, timeout=15)
    if not ok:
        return set()
    languages: set[str] = set()
    for line in output.splitlines():
        value = line.strip()
        if not value or value.lower().startswith("list of available languages"):
            continue
        # When --tessdata-dir points at the parent directory, some Tesseract
        # builds report entries as "tessdata/eng" (or "tessdata\\eng")
        # instead of the language code alone. Normalize that presentation.
        value = value.replace("\\", "/")
        if value.lower().startswith("tessdata/"):
            value = value.split("/", 1)[1]
        if value:
            languages.add(value)
    return languages


def _tessdata_dir(executable_path: str) -> Path | None:
    exe = Path(executable_path).resolve()
    candidate = exe.parent / "tessdata"
    return candidate if candidate.exists() else None


def _managed_tessdata_base(base_dir: Path, config: dict[str, object] | None = None) -> Path:
    """Return the user-writable project-managed Tesseract data base directory."""
    cfg = config or {}
    configured = str(cfg.get("managed_tessdata_dir", "data/tesseract"))
    path = Path(configured)
    if not path.is_absolute():
        path = base_dir / path
    return _normalize_tessdata_base(path)


def _copy_existing_tessdata(source_dir: Path, target_dir: Path, languages: Iterable[str]) -> None:
    """Copy only requested existing language models into the project-managed cache."""
    for language in languages:
        source = source_dir / f"{language}.traineddata"
        target = target_dir / source.name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)


def _download_tessdata_language(language: str, target: Path) -> None:
    """Download and stage one official Tesseract model atomically."""
    url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata/main/{language}.traineddata"
    temporary = target.with_suffix(target.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            if getattr(response, "status", 200) and int(response.status) >= 400:
                raise urllib.error.HTTPError(url, int(response.status), "HTTP error", response.headers, None)
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
        if not temporary.exists() or temporary.stat().st_size <= 0:
            raise OSError("downloaded traineddata is empty")
        temporary.replace(target)
    except (OSError, urllib.error.URLError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"failed to download {language}.traineddata: {exc}") from exc


def ensure_tesseract_languages(
    executable_path: str,
    languages: Iterable[str],
    *,
    base_dir: Path | None = None,
    managed_tessdata_dir: Path | None = None,
    prompt: bool = True,
) -> tuple[bool, str]:
    """Ensure requested Tesseract models are available without modifying Program Files.

    Existing system models are left untouched. When one or more requested models are
    missing, the requested models are assembled into a user-writable project cache and
    runtime OCR uses that cache via --tessdata-dir.
    """
    requested = list(dict.fromkeys(lang.strip() for lang in languages if lang and lang.strip()))
    if not requested:
        return True, "no Tesseract language data configured"

    system_languages = installed_tesseract_languages(executable_path)
    if all(lang in system_languages for lang in requested):
        return True, "requested language data already installed"

    base = Path(base_dir) if base_dir is not None else default_base_dir()
    target_base = (managed_tessdata_dir or _managed_tessdata_base(base)).resolve()
    target_tessdata = target_base / "tessdata"
    target_tessdata.mkdir(parents=True, exist_ok=True)

    missing = [lang for lang in requested if lang not in system_languages]
    print(f"[DEPENDENCY] Tesseract language data missing: {', '.join(missing)}")
    if prompt:
        answer = input(
            "[DEPENDENCY] Download missing official language data to the project-managed OCR cache now? [Y/n]: "
        ).strip().lower()
        if answer not in {"", "y", "yes"}:
            return False, "language-data installation declined"

    source_tessdata = _tessdata_dir(executable_path)
    try:
        if source_tessdata is not None:
            # Keep the cache self-contained so runtime OCR can use one deterministic data root.
            _copy_existing_tessdata(source_tessdata, target_tessdata, requested)
        for language in requested:
            target = target_tessdata / f"{language}.traineddata"
            if not target.exists():
                _download_tessdata_language(language, target)

        available_managed = installed_tesseract_languages(executable_path, target_base)
    except (OSError, RuntimeError) as exc:
        return False, str(exc)

    missing_after = [lang for lang in requested if lang not in available_managed]
    if missing_after:
        return False, f"Tesseract language verification failed for: {', '.join(missing_after)}"
    return True, f"installed/verified language data in {target_base}: {', '.join(requested)}"

def docker_daemon_ready() -> bool:
    """Return whether a Docker daemon is responsive."""
    if executable("docker") is None:
        return False
    ok, _ = command_ok(["docker", "info"], timeout=20)
    return ok


def docker_image_exists(image: str) -> bool:
    """Return whether the configured Docker image exists locally."""
    docker = executable("docker")
    if not docker or not image:
        return False
    ok, _ = command_ok([docker, "image", "inspect", image], timeout=20)
    return ok


def start_docker_desktop() -> bool:
    """Start Docker Desktop if it is installed but its daemon is not ready."""
    if docker_daemon_ready():
        return True
    if os.name != "nt":
        return False
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DockerDesktop" / "Docker Desktop.exe",
    ]
    exe = next((path for path in candidates if path.exists()), None)
    if exe is None:
        return False
    try:
        subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=False)
    except OSError:
        return False
    for _ in range(60):
        if docker_daemon_ready():
            return True
        time.sleep(2)
    return False


def ensure_quarantine_image(base_dir: Path, *, prompt: bool = True) -> DependencyStatus:
    """Build the reusable Docker quarantine image when Docker is ready."""
    cfg = load_dependency_config(base_dir)["quarantine_image"]
    image = str(cfg.get("image", "scrapper-ollama-quarantine:1.0"))
    present, _ = command_ok(["docker", "image", "inspect", image], timeout=20)
    if present:
        return DependencyStatus("quarantine_image", "Docker quarantine image", True, image)
    if prompt:
        answer = input(f"[DEPENDENCY] Build quarantine image '{image}' now? [Y/n]: ").strip().lower()
        if answer not in {"", "y", "yes"}:
            return DependencyStatus("quarantine_image", "Docker quarantine image", False, "build declined")
    try:
        result = subprocess.run([sys.executable, str(base_dir / "scripts" / "build_quarantine_image.py"), "--image", image], check=False)
    except OSError as exc:
        return DependencyStatus("quarantine_image", "Docker quarantine image", False, str(exc))
    return DependencyStatus(
        "quarantine_image",
        "Docker quarantine image",
        result.returncode == 0,
        image if result.returncode == 0 else f"build exited with {result.returncode}",
    )


def ollama_server_ready(host: str = "http://127.0.0.1:11434") -> bool:
    """Return whether the local Ollama HTTP server is reachable."""
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=5) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def start_ollama_server() -> bool:
    """Start `ollama serve` in the background when the local server is unavailable."""
    if ollama_server_ready():
        return True
    resolved = resolve_dependency_executable("ollama", ["ollama"])
    if not resolved:
        return False
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [resolved, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=False,
            creationflags=creationflags,
        )
    except OSError:
        return False
    for _ in range(30):
        if ollama_server_ready():
            return True
        time.sleep(1)
    return False


def ensure_ollama_models(base_dir: Path, *, prompt: bool = True) -> list[DependencyStatus]:
    """Ensure configured Ollama models are installed when the Ollama CLI/server are available."""
    if not start_ollama_server():
        return [DependencyStatus("ollama_server", "Ollama server", False, "Ollama HTTP server is not ready")]
    settings_path = base_dir / "config" / "model_settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        settings = {"window_verifier_model": "qwen3:4b", "answer_model": "qwen3:8b", "vision_model": ""}
    models = []
    for key in ("window_verifier_model", "answer_model", "vision_model"):
        model = settings.get(key)
        if isinstance(model, str) and model.strip() and model not in models:
            models.append(model.strip())
    statuses: list[DependencyStatus] = []
    for model in models:
        present, _ = command_ok(["ollama", "show", model], timeout=20)
        if present:
            statuses.append(DependencyStatus(f"ollama_model:{model}", f"Ollama model {model}", True, "installed"))
            continue
        if prompt:
            answer = input(f"[DEPENDENCY] Install Ollama model '{model}' now? [Y/n]: ").strip().lower()
            if answer not in {"", "y", "yes"}:
                statuses.append(DependencyStatus(f"ollama_model:{model}", f"Ollama model {model}", False, "installation declined"))
                continue
        try:
            result = subprocess.run(["ollama", "pull", model], check=False)
        except OSError as exc:
            statuses.append(DependencyStatus(f"ollama_model:{model}", f"Ollama model {model}", False, str(exc)))
            continue
        statuses.append(
            DependencyStatus(
                f"ollama_model:{model}",
                f"Ollama model {model}",
                result.returncode == 0,
                "installed" if result.returncode == 0 else f"ollama pull exited with {result.returncode}",
            )
        )
    return statuses



def collect_dependency_diagnostics(base_dir: Path | None = None) -> dict[str, object]:
    """Collect a machine-readable dependency health report without installing anything."""
    base = Path(base_dir) if base_dir is not None else default_base_dir()
    config = load_dependency_config(base)
    report: dict[str, object] = {
        "schema_version": "1.0",
        "timestamp": time.time(),
        "platform": {"os": os.name, "python": sys.version.split()[0]},
        "dependencies": {},
    }
    dependencies = report["dependencies"]
    assert isinstance(dependencies, dict)

    minimum_python = str(config.get("python", {}).get("minimum_version", "3.13")) if isinstance(config.get("python"), dict) else "3.13"
    dependencies["python"] = {
        "installed": python_version_ok(minimum_python),
        "detected_version": sys.version.split()[0],
        "required_version": minimum_python,
        "stage_affected": "STARTUP",
        "how_to_fix": f"Install Python {minimum_python}+ and rerun setup.bat." if not python_version_ok(minimum_python) else None,
    }

    pip_ok, pip_detail = command_ok([sys.executable, "-m", "pip", "--version"])
    dependencies["python_packages"] = {
        "installed": pip_ok,
        "detected_version": pip_detail,
        "required_version": "configured packages",
        "stage_affected": "STARTUP",
        "how_to_fix": "Run setup.bat to install configured Python packages." if not pip_ok else None,
    }

    for key, label, names, stage in (
        ("firefox", "Firefox", ["firefox"], "CAPTURE"),
        ("docker", "Docker CLI", ["docker"], "DOCKER_INSPECTION"),
        ("tesseract", "Tesseract", ["tesseract"], "OCR"),
        ("ollama", "Ollama", ["ollama"], "TASK_SOLVING"),
    ):
        executable_path = resolve_dependency_executable("docker_desktop" if key == "docker" else key, names)
        ok = executable_path is not None
        details: dict[str, object] = {
            "installed": ok,
            "detected_path": executable_path,
            "required_version": None,
            "stage_affected": stage,
            "how_to_fix": f"Install or repair {label} using setup.bat." if not ok else None,
        }
        if key == "docker" and ok:
            daemon = docker_daemon_ready()
            details["daemon_ready"] = daemon
            if not daemon:
                details["how_to_fix"] = "Start Docker Desktop and wait for the daemon to become ready."
        if key == "tesseract" and ok:
            langs = installed_tesseract_languages(executable_path)
            requested = []
            cfg = config.get("tesseract")
            if isinstance(cfg, dict) and isinstance(cfg.get("languages"), list):
                requested = [str(item) for item in cfg["languages"]]
            details["installed_languages"] = sorted(langs)
            details["required_languages"] = requested
            tess_cfg = config.get("tesseract", {}) if isinstance(config.get("tesseract"), dict) else {}
            managed_base = _managed_tessdata_base(base, tess_cfg)
            managed_langs = installed_tesseract_languages(executable_path, managed_base) if managed_base.exists() else set()
            effective_langs = langs | managed_langs
            missing = [lang for lang in requested if lang not in effective_langs]
            details["missing_languages"] = missing
            details["managed_tessdata_dir"] = str(managed_base)
            details["managed_languages"] = sorted(managed_langs)
            if missing:
                details["installed"] = False
                details["how_to_fix"] = f"Run setup.bat to download/verify Tesseract language data: {', '.join(missing)}."
        dependencies[key] = details

    image_cfg = config.get("quarantine_image")
    image = str(image_cfg.get("image", "")) if isinstance(image_cfg, dict) else ""
    image_present = docker_daemon_ready() and bool(image) and docker_image_exists(image)
    dependencies["quarantine_image"] = {
        "installed": image_present,
        "detected_version": image,
        "required_version": image,
        "stage_affected": "DOCKER_INSPECTION",
        "how_to_fix": "Run python scripts/build_quarantine_image.py when Docker is ready." if not image_present else None,
    }

    if resolve_dependency_executable("ollama", ["ollama"]):
        model_status: dict[str, object] = {}
        settings_path = base / "config" / "model_settings.json"
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            settings = {}
        for key in ("window_verifier_model", "answer_model", "vision_model"):
            model = settings.get(key) if isinstance(settings, dict) else None
            if isinstance(model, str) and model.strip():
                present, detail = command_ok(["ollama", "show", model.strip()], timeout=20)
                model_status[key] = {"model": model.strip(), "installed": present, "detail": detail}
        dependencies["ollama_models"] = {
            "installed": all(bool(item.get("installed")) for item in model_status.values()) if model_status else True,
            "models": model_status,
            "stage_affected": "TASK_SOLVING",
            "how_to_fix": "Run setup.bat and accept installation of missing configured Ollama models.",
        }

    native_host = base / "native_messaging" / "active-firefox-scraper@example.local.json"
    dependencies["native_messaging"] = {
        "installed": native_host.exists(),
        "detected_path": str(native_host) if native_host.exists() else None,
        "required_version": "registered host manifest",
        "stage_affected": "CAPTURE",
        "how_to_fix": "Run python native_messaging/install_native_host.py." if not native_host.exists() else None,
    }
    return report

def ensure_all_dependencies(base_dir: Path | None = None, *, prompt: bool = True, include_models: bool = True) -> list[DependencyStatus]:
    """Verify/install the complete Windows runtime stack and project dependencies."""
    base = Path(base_dir) if base_dir is not None else default_base_dir()
    if os.name != "nt":
        raise DependencyInstallError("Automatic dependency installation currently targets Windows.")

    config = load_dependency_config(base)
    statuses: list[DependencyStatus] = []
    python_cfg = config["python"]
    minimum_python = str(python_cfg.get("minimum_version", "3.13"))
    if python_version_ok(minimum_python):
        statuses.append(DependencyStatus("python", "Python runtime", True, sys.version.split()[0]))
    else:
        print(f"[DEPENDENCY] Current Python is {sys.version.split()[0]}; Python {minimum_python}+ is required.")
        answer = input("[DEPENDENCY] Install the required Python runtime now? [Y/n]: ").strip().lower() if prompt else "y"
        if answer in {"", "y", "yes"} and winget_available():
            ok, message = run_winget_install(
                str(python_cfg.get("winget_id", "Python.Python.3.13")),
                source=str(python_cfg.get("source", "winget")),
            )
            py_ok, py_output = command_ok(["py", "-3.13", "-c", "import sys; print(sys.version_info[0:2])"], timeout=20)
            statuses.append(DependencyStatus(
                "python",
                "Python runtime",
                ok and py_ok,
                message if not py_ok else "Python 3.13 is installed; relaunch setup with that interpreter.",
            ))
        else:
            statuses.append(DependencyStatus("python", "Python runtime", False, "installation declined or WinGet unavailable"))

    for key, label in [
        ("firefox", "Firefox"),
        ("docker_desktop", "Docker Desktop"),
        ("tesseract", "Tesseract OCR"),
        ("ollama", "Ollama"),
    ]:
        statuses.append(ensure_app(key, label, config[key], prompt=prompt))

    statuses.append(ensure_python_packages(base, prompt=prompt))

    tesseract = find_tesseract()
    if tesseract:
        tess_cfg = config.get("tesseract", {}) if isinstance(config.get("tesseract"), dict) else {}
        managed_dir = _managed_tessdata_base(base, tess_cfg)
        ok, detail = ensure_tesseract_languages(
            tesseract,
            tess_cfg.get("languages", ["eng", "fra"]),
            base_dir=base,
            managed_tessdata_dir=managed_dir,
            prompt=prompt,
        )
        statuses.append(DependencyStatus("tesseract_language_data", "Tesseract language data", ok, detail))

    if not docker_daemon_ready():
        if not start_docker_desktop():
            statuses.append(DependencyStatus("docker_daemon", "Docker daemon", False, "Docker daemon is not ready"))
        else:
            statuses.append(DependencyStatus("docker_daemon", "Docker daemon", True, "ready"))
    else:
        statuses.append(DependencyStatus("docker_daemon", "Docker daemon", True, "ready"))

    if docker_daemon_ready():
        statuses.append(ensure_quarantine_image(base, prompt=prompt))
    if include_models and resolve_dependency_executable("ollama", ["ollama"]):
        statuses.extend(ensure_ollama_models(base, prompt=prompt))
    return statuses
