import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ollama import installer


class InstallerBehaviorTests(unittest.TestCase):
    def test_decline_matrix_stops_without_install(self):
        for answer in ["", "n", "N", "no", "NO", "  n  ", "maybe"]:
            with self.subTest(answer=answer), \
                patch("builtins.input", return_value=answer), \
                patch.object(installer, "install_model") as install, \
                patch.object(installer, "restart_program") as restart:
                self.assertFalse(installer.prompt_install_and_restart("qwen3:8b", "answer_model"))
                install.assert_not_called()
                restart.assert_not_called()

    def test_accept_matrix_installs_and_restarts(self):
        for answer in ["y", "Y", "yes", "YES", "  yes  "]:
            with self.subTest(answer=answer), \
                patch("builtins.input", return_value=answer), \
                patch.object(installer, "install_model", return_value=(True, "ok")) as install, \
                patch.object(installer, "restart_program") as restart:
                with self.assertRaises(SystemExit) as caught:
                    installer.prompt_install_and_restart("qwen3:8b", "answer_model", resume_file="Question.html")
                self.assertEqual(caught.exception.code, 0)
                install.assert_called_once_with("qwen3:8b")
                restart.assert_called_once_with(resume_file="Question.html")

    def test_failed_install_does_not_restart(self):
        with patch("builtins.input", return_value="y"), \
            patch.object(installer, "install_model", return_value=(False, "failed")) as install, \
            patch.object(installer, "restart_program") as restart:
            self.assertFalse(installer.prompt_install_and_restart("qwen3:8b", "answer_model"))
            install.assert_called_once_with("qwen3:8b")
            restart.assert_not_called()

    def test_restart_failure_does_not_kill_process(self):
        with patch("builtins.input", return_value="y"), \
            patch.object(installer, "install_model", return_value=(True, "ok")), \
            patch.object(installer, "restart_program", side_effect=installer.RestartError("boom")) as restart:
            self.assertFalse(installer.prompt_install_and_restart("qwen3:8b", "answer_model", resume_file="Question.html"))
            restart.assert_called_once_with(resume_file="Question.html")

    def test_install_model_rejects_empty_name(self):
        for model in ["", "   "]:
            with self.subTest(model=model):
                self.assertEqual(installer.install_model(model), (False, "The requested Ollama model name is empty."))

    def test_install_model_requires_ollama_in_path(self):
        with patch.object(installer.shutil, "which", return_value=None):
            ok, message = installer.install_model("qwen3:8b")
        self.assertFalse(ok)
        self.assertIn("not found in PATH", message)

    def test_install_model_success(self):
        completed = subprocess.CompletedProcess(args=["ollama", "pull", "qwen3:8b"], returncode=0)
        with patch.object(installer.shutil, "which", return_value="ollama"), \
            patch.object(installer.subprocess, "run", return_value=completed) as run:
            ok, message = installer.install_model("  qwen3:8b  ")
        self.assertTrue(ok)
        self.assertIn("installed successfully", message)
        run.assert_called_once_with(["ollama", "pull", "qwen3:8b"], check=False)

    def test_install_model_failure(self):
        completed = subprocess.CompletedProcess(args=["ollama", "pull", "qwen3:8b"], returncode=17)
        with patch.object(installer.shutil, "which", return_value="ollama"), \
            patch.object(installer.subprocess, "run", return_value=completed):
            ok, message = installer.install_model("qwen3:8b")
        self.assertFalse(ok)
        self.assertIn("17", message)

    def test_install_model_start_failure(self):
        with patch.object(installer.shutil, "which", return_value="ollama"), \
            patch.object(installer.subprocess, "run", side_effect=OSError("spawn failed")):
            ok, message = installer.install_model("qwen3:8b")
        self.assertFalse(ok)
        self.assertIn("Could not start ollama", message)

    def test_build_restart_command_uses_absolute_python_and_main(self):
        with patch.object(sys, "argv", ["main.py", "--process-existing", "--file", "old.html"]):
            command, root = installer.build_restart_command("new.html")
        self.assertEqual(Path(command[0]), Path(sys.executable).resolve())
        self.assertEqual(Path(command[1]), root / "main.py")
        self.assertIn("--process-existing", command)
        self.assertIn("--file", command)
        self.assertEqual(command[command.index("--file") + 1], "new.html")
        self.assertNotIn("old.html", command)

    def test_build_restart_command_adds_resume_args(self):
        with patch.object(sys, "argv", ["main.py"]):
            command, _ = installer.build_restart_command("page.html")
        self.assertIn("--process-existing", command)
        self.assertEqual(command[-2:], ["--file", "page.html"])

    def test_build_restart_command_preserves_unrelated_flags(self):
        argv = ["main.py", "--answer-model", "qwen3:8b", "--max-answer-chars", "50000"]
        with patch.object(sys, "argv", argv):
            command, _ = installer.build_restart_command("page.html")
        self.assertIn("--answer-model", command)
        self.assertIn("qwen3:8b", command)
        self.assertIn("--max-answer-chars", command)
        self.assertIn("50000", command)

    def test_build_restart_command_replaces_existing_file(self):
        with patch.object(sys, "argv", ["main.py", "--process-existing", "--file", "one.html"]):
            command, _ = installer.build_restart_command("two.html")
        self.assertEqual(command[command.index("--file") + 1], "two.html")
        self.assertEqual(command.count("--file"), 1)

    def test_restart_program_spawns_without_new_console(self):
        with patch.object(sys, "argv", ["main.py"]), \
            patch.object(installer.subprocess, "Popen") as popen:
            installer.restart_program("page.html")
        _, kwargs = popen.call_args
        self.assertEqual(kwargs["close_fds"], False)
        self.assertNotIn("creationflags", kwargs)


    def test_install_model_invokes_real_executable_success(self):
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td)
            script = bin_dir / "ollama"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            script.chmod(0o755)
            with patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ.get('PATH','')}"}, clear=False):
                ok, message = installer.install_model("qwen3:8b")
            self.assertTrue(ok)
            self.assertIn("installed successfully", message)

    def test_install_model_invokes_real_executable_failure(self):
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td)
            script = bin_dir / "ollama"
            script.write_text("#!/bin/sh\nexit 17\n", encoding="utf-8")
            script.chmod(0o755)
            with patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ.get('PATH','')}"}, clear=False):
                ok, message = installer.install_model("qwen3:8b")
            self.assertFalse(ok)
            self.assertIn("17", message)

    def test_restart_program_uses_current_python_not_shell(self):
        with patch.object(sys, "argv", ["main.py"]), \
            patch.object(installer.subprocess, "Popen") as popen:
            installer.restart_program("page.html")
        command = popen.call_args.args[0]
        self.assertEqual(Path(command[0]), Path(sys.executable).resolve())
        self.assertEqual(Path(command[1]), installer._project_root() / "main.py")
        self.assertNotEqual(command[0], "cmd.exe")

    def test_restart_program_passes_absolute_working_directory(self):
        with patch.object(sys, "argv", ["main.py"]), \
            patch.object(installer.subprocess, "Popen") as popen:
            installer.restart_program("page.html")
        self.assertEqual(Path(popen.call_args.kwargs["cwd"]), installer._project_root())

    def test_restart_program_keeps_existing_process_flags(self):
        with patch.object(sys, "argv", ["main.py", "--no-window-llm", "--answer-model", "qwen3:8b"]), \
            patch.object(installer.subprocess, "Popen") as popen:
            installer.restart_program("page.html")
        command = popen.call_args.args[0]
        self.assertIn("--no-window-llm", command)
        self.assertIn("--answer-model", command)
        self.assertIn("qwen3:8b", command)

    def test_restart_program_reports_spawn_failure(self):
        with patch.object(sys, "argv", ["main.py"]), \
            patch.object(installer.subprocess, "Popen", side_effect=OSError("no process")):
            with self.assertRaises(installer.RestartError):
                installer.restart_program("page.html")


if __name__ == "__main__":
    unittest.main()

class DependencyBootstrapTests(unittest.TestCase):
    def test_python_floor(self):
        from app.dependency_installer import python_version_ok
        self.assertTrue(python_version_ok("3.0"))
        self.assertFalse(python_version_ok("99.0"))

    def test_winget_install_uses_exact_id(self):
        from app.dependency_installer import run_winget_install
        with patch("app.dependency_installer.winget_available", return_value=True), \
            patch("app.dependency_installer.subprocess.run") as run:
            run.return_value.returncode = 0
            ok, _ = run_winget_install("Docker.DockerDesktop")
        self.assertTrue(ok)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["winget", "install", "--id", "Docker.DockerDesktop"])
        self.assertIn("-e", command)

    def test_winget_install_does_not_upgrade_existing_apps(self):
        from app.dependency_installer import run_winget_install
        with patch("app.dependency_installer.winget_available", return_value=True), \
            patch("app.dependency_installer.subprocess.run") as run:
            run.return_value.returncode = 0
            ok, _ = run_winget_install("Ollama.Ollama")
        self.assertTrue(ok)
        command = run.call_args.args[0]
        self.assertIn("--no-upgrade", command)

    def test_existing_ollama_is_preserved_without_installer_call(self):
        from app.dependency_installer import ensure_app
        config = {"winget_id": "Ollama.Ollama", "source": "winget", "official_install_ps1": "https://ollama.com/install.ps1"}
        with patch("app.dependency_installer.resolve_dependency_executable", return_value="C:/Users/test/AppData/Local/Programs/Ollama/ollama.exe"), \
            patch("app.dependency_installer.ollama_cli_version", return_value="ollama version is 0.30.10"), \
            patch("app.dependency_installer.run_winget_install") as install, \
            patch("app.dependency_installer.subprocess.run") as run:
            status = ensure_app("ollama", "Ollama", config, prompt=True)
        self.assertTrue(status.installed)
        self.assertIn("0.30.10", status.detail)
        install.assert_not_called()
        run.assert_not_called()

    def test_tesseract_missing_language_uses_project_writable_cache(self):
        from app.dependency_installer import ensure_tesseract_languages

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            exe = base / "Tesseract-OCR" / "tesseract.exe"
            system_tessdata = exe.parent / "tessdata"
            system_tessdata.mkdir(parents=True)
            (system_tessdata / "eng.traineddata").write_bytes(b"eng-model")

            class Response:
                status = 200
                headers = {}
                def __init__(self):
                    self._done = False
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, *args):
                    if self._done:
                        return b""
                    self._done = True
                    return b"fra-model"

            def fake_urlopen(url, timeout=30):
                return Response()

            def fake_command(command, timeout=15):
                if "--tessdata-dir" in command:
                    base_arg = Path(command[command.index("--tessdata-dir") + 1])
                    langs = sorted(p.stem for p in (base_arg / "tessdata").glob("*.traineddata"))
                    return True, "List of available languages in x:\n" + "\n".join(langs)
                return True, "List of available languages in x:\neng"

            with patch("app.dependency_installer.command_ok", side_effect=fake_command), \
                 patch("app.dependency_installer.urllib.request.urlopen", side_effect=fake_urlopen):
                ok, detail = ensure_tesseract_languages(
                    str(exe),
                    ["eng", "fra"],
                    base_dir=base,
                    prompt=False,
                )

            self.assertTrue(ok)
            managed = base / "data" / "tesseract" / "tessdata"
            self.assertTrue((managed / "eng.traineddata").exists())
            self.assertTrue((managed / "fra.traineddata").exists())
            self.assertIn("verified", detail)

    def test_tesseract_language_parser_normalizes_nested_tessdata_prefix(self):
        from app.dependency_installer import installed_tesseract_languages
        completed = subprocess.CompletedProcess(
            args=["tesseract", "--list-langs"],
            returncode=0,
            stdout="List of available languages in \"x\":\ntessdata/eng\ntessdata\\fra\nosd\n",
            stderr="",
        )
        with patch("app.dependency_installer.command_ok", return_value=(True, completed.stdout)):
            langs = installed_tesseract_languages("tesseract", Path("C:/cache"))
        self.assertEqual(langs, {"eng", "fra", "osd"})

    def test_tesseract_language_parser(self):
        from app.dependency_installer import installed_tesseract_languages
        completed = subprocess.CompletedProcess(
            args=["tesseract", "--list-langs"],
            returncode=0,
            stdout="List of available languages in \"x\":\neng\nfra\nosd\n",
            stderr="",
        )
        with patch("app.dependency_installer.command_ok", return_value=(True, completed.stdout)):
            langs = installed_tesseract_languages("tesseract")
        self.assertEqual(langs, {"eng", "fra", "osd"})

    def test_docker_ready_requires_daemon(self):
        from app.dependency_installer import docker_daemon_ready
        with patch("app.dependency_installer.executable", return_value="docker"), \
            patch("app.dependency_installer.command_ok", return_value=(False, "daemon not running")):
            self.assertFalse(docker_daemon_ready())

    def test_dependency_config_has_required_stack(self):
        from app.dependency_installer import load_dependency_config
        config = load_dependency_config(Path(__file__).resolve().parents[1])
        for key in ["python", "firefox", "docker_desktop", "tesseract", "ollama", "quarantine_image"]:
            self.assertIn(key, config)

    def test_setup_status_is_explicit_when_python_packages_fail(self):
        from app.dependency_installer import ensure_python_packages
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "config").mkdir()
            (base / "config" / "dependencies.json").write_text(
                '{"python_packages": ["beautifulsoup4", "defusedxml>=0.7.1"]}',
                encoding="utf-8",
            )
            with patch("app.dependency_installer.command_ok", return_value=(True, "pip 25")), \
                patch("app.dependency_installer.subprocess.run") as run:
                run.return_value.returncode = 19
                status = ensure_python_packages(base, prompt=False)
        self.assertFalse(status.installed)
        self.assertIn("19", status.detail)

    def test_setup_batch_exists_and_contains_every_dependency(self):
        root = Path(__file__).resolve().parents[1]
        batch = root / "setup.bat"
        self.assertTrue(batch.exists())
        text = batch.read_text(encoding="utf-8")
        for required in [
            "beautifulsoup4",
            "defusedxml>=0.7.1",
            "Python.Python.3.13",
            r"scripts\setup_windows.py",
            r"native_messaging\install_native_host.py",
        ]:
            self.assertIn(required, text)
        config = (root / "config" / "dependencies.json").read_text(encoding="utf-8")
        self.assertIn("Ollama.Ollama", config)
        self.assertNotIn("winget install --id Ollama.Ollama", text)
        self.assertIn("do not call winget install directly here", text)

    def test_root_has_github_readme_consolidated_information_and_single_installer(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "README.md").exists())
        self.assertTrue((root / "information" / "INFORMATION.md").exists())
        self.assertTrue((root / "setup.bat").exists())
        for forbidden in [
            "INSTALLATION.md",
            "SECURITY.md",
            "FILE_EVIDENCE.md",
            "SCREENSHOT_ANALYSIS.md",
            "TASK_MODES.md",
            "TEST_REPORT.md",
            "install_extension.txt",
            "requirements.txt",
            "setup_windows.bat",
            "install_model.py",
        ]:
            self.assertFalse((root / forbidden).exists(), forbidden)


class DependencyDiagnosticsTests(unittest.TestCase):
    def test_dependency_diagnostics_uses_resolved_tesseract_for_managed_cache(self):
        from app.dependency_installer import collect_dependency_diagnostics

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "config").mkdir()
            (base / "data" / "tesseract" / "tessdata").mkdir(parents=True)
            (base / "config" / "dependencies.json").write_text(
                '{"python":{"minimum_version":"3.13"},"quarantine_image":{"image":"test:1"},"tesseract":{"languages":["eng","fra"],"managed_tessdata_dir":"data/tesseract"}}',
                encoding="utf-8",
            )

            real_executable = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            calls = []

            def fake_languages(executable, tessdata_base=None):
                calls.append((executable, tessdata_base))
                if tessdata_base is None:
                    return {"eng"}
                return {"fra"}

            def fake_resolve(name, names):
                return real_executable if name in {"tesseract", "docker"} else None

            with patch("app.dependency_installer.resolve_dependency_executable", side_effect=fake_resolve), \
                 patch("app.dependency_installer.installed_tesseract_languages", side_effect=fake_languages), \
                 patch("app.dependency_installer.command_ok", return_value=(True, "ok")), \
                 patch("app.dependency_installer.docker_daemon_ready", return_value=False), \
                 patch("app.dependency_installer.docker_image_exists", return_value=False):
                report = collect_dependency_diagnostics(base)

        tesseract = report["dependencies"]["tesseract"]
        self.assertEqual(tesseract["managed_languages"], ["fra"])
        self.assertEqual(tesseract["missing_languages"], [])
        self.assertTrue(tesseract["installed"])
        self.assertEqual(calls[1][0], real_executable)

    def test_dependency_diagnostics_has_machine_readable_fields(self):
        from app.dependency_installer import collect_dependency_diagnostics

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "config").mkdir()
            (base / "config" / "dependencies.json").write_text(
                '{"python":{"minimum_version":"3.13"},"quarantine_image":{"image":"test:1"},"tesseract":{"languages":["eng"]}}',
                encoding="utf-8",
            )
            with patch("app.dependency_installer.resolve_dependency_executable", return_value=None), \
                 patch("app.dependency_installer.command_ok", return_value=(False, "missing")), \
                 patch("app.dependency_installer.docker_daemon_ready", return_value=False):
                report = collect_dependency_diagnostics(base)

        self.assertEqual(report["schema_version"], "1.0")
        self.assertIn("dependencies", report)
        for key in ("python", "python_packages", "firefox", "docker", "tesseract", "ollama", "quarantine_image", "native_messaging"):
            self.assertIn(key, report["dependencies"])
        self.assertIn("stage_affected", report["dependencies"]["docker"])
        self.assertIn("how_to_fix", report["dependencies"]["docker"])

class ObsoleteCodeCleanupTests(unittest.TestCase):
    def test_obsolete_capture_bridge_and_local_ingest_are_removed(self):
        root = Path(__file__).resolve().parents[1]
        for obsolete in [
            root / "app" / "bridge",
            root / "app" / "browser",
            root / "app" / "downloads" / "ingest.py",
        ]:
            self.assertFalse(obsolete.exists(), obsolete)
