import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import native_capture
from app.storage.writer import save_raw_page
from native_messaging import native_host


class NativeMessagingTests(unittest.TestCase):
    def test_message_roundtrip(self):
        payload = {"action": "capture", "html": "<html>é</html>", "n": 123}
        encoded = native_host.encode_message(payload)
        self.assertEqual(native_host.decode_message(encoded), payload)

    def test_native_host_response_stays_under_response_limit(self):
        with self.assertRaises(RuntimeError):
            native_host.encode_message({"data": "x" * (1024 * 1024)})

    def test_capture_saves_timestamp_named_raw_file_and_starts_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_native = root / "native_messaging"
            fake_native.mkdir()
            source_root = Path(native_capture.project_root())
            html = '<html><body><h1>Question</h1></body></html>'

            with patch.object(native_capture, "project_root", return_value=root), \
                 patch.object(native_capture, "launch_pipeline") as launch:
                result = native_capture.capture_and_launch({
                    "action": "capture",
                    "url": "https://example.test/page",
                    "title": "Titre é à ?",
                    "html": html,
                })

            raw = list((root / "data" / "raw_pages").glob("*.html"))
            self.assertEqual(len(raw), 1)
            self.assertNotIn("Titre", raw[0].name)
            self.assertTrue(raw[0].name.endswith(".html"))
            self.assertEqual(raw[0].read_text(encoding="utf-8"), html)
            self.assertTrue(result["ok"])
            self.assertEqual(result["raw_file"], raw[0].name)
            launch.assert_called_once()

    def test_capture_saves_png_screenshot_when_supplied(self):
        import base64
        png = b"\x89PNG\r\n\x1a\n" + b"test"
        data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            html = "<html><body><h1>Question</h1></body></html>"
            with patch.object(native_capture, "project_root", return_value=root), \
                 patch.object(native_capture, "launch_pipeline"):
                result = native_capture.capture_and_launch({
                    "action": "capture",
                    "url": "https://example.test/page",
                    "title": "Question",
                    "html": html,
                    "screenshot_data_url": data_url,
                })
            shots = list((root / "data" / "screenshots").glob("*.png"))
            self.assertEqual(len(shots), 1)
            self.assertEqual(shots[0].read_bytes(), png)
            self.assertEqual(result["screenshot_file"], shots[0].name)

    def test_capture_rejects_invalid_screenshot_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(native_capture, "project_root", return_value=root), \
                 patch.object(native_capture, "launch_pipeline"):
                with self.assertRaises(ValueError):
                    native_capture.capture_and_launch({
                        "action": "capture",
                        "url": "https://example.test/page",
                        "title": "Question",
                        "html": "<html></html>",
                        "screenshot_data_url": "data:image/png;base64,AAAA",
                    })


if __name__ == "__main__":
    unittest.main()

class NativeLaunchHardeningTests(unittest.TestCase):
    def test_windows_pipeline_launch_uses_persistent_cmd_k_and_env_metadata(self):
        from pathlib import Path
        from unittest.mock import patch
        from app import native_capture

        raw = Path("20260930T190000_123456Z.html")
        title = 'Libre | Question 3 & <unsafe> "quoted"'
        url = 'https://example.test/?a=1&b=2'
        with patch.object(native_capture, "_is_windows", return_value=True), \
            patch.object(native_capture.subprocess, "Popen") as popen:
            native_capture.launch_pipeline(raw, url=url, title=title)

        args, kwargs = popen.call_args
        command = args[0]
        self.assertEqual(command[:3], ["cmd.exe", "/d", "/k"])
        self.assertNotIn(title, command)
        self.assertNotIn(url, command)
        self.assertEqual(kwargs["env"]["SCRAPPER_CAPTURE_TITLE"], title)
        self.assertEqual(kwargs["env"]["SCRAPPER_CAPTURE_URL"], url)

    def test_windows_launch_keeps_raw_filename_in_command_only(self):
        from pathlib import Path
        from unittest.mock import patch
        from app import native_capture

        raw = Path("20260930T190000_123456Z.html")
        with patch.object(native_capture, "_is_windows", return_value=True), \
            patch.object(native_capture.subprocess, "Popen") as popen:
            native_capture.launch_pipeline(raw)
        inner = popen.call_args.args[0][3]
        self.assertIn(raw.name, inner)
        self.assertNotIn("SCRAPPER_CAPTURE_TITLE", inner)
        self.assertNotIn("SCRAPPER_CAPTURE_URL", inner)
