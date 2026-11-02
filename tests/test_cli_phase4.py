"""Phase 4 command-line integration tests."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from limbo.cli import main


class CliPhase4Tests(unittest.TestCase):
    def _run(self, argv, directory):
        output = io.StringIO()
        with patch("pathlib.Path.cwd", return_value=Path(directory)), contextlib.redirect_stdout(output):
            result = main(argv)
        return result, output.getvalue()

    def test_config_validate_accepts_generated_config(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "limbo.toml").write_text('[app]\nname="test"\n', encoding="utf-8")
            result, output = self._run(["config", "validate"], directory)
            self.assertEqual(result, 0)
            self.assertIn("Configuration valid", output)

    def test_add_and_remove_commands_only_edit_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "package.json"
            manifest.write_text('{"name":"demo"}', encoding="utf-8")
            result, output = self._run(["add", "vite", "--dev"], directory)
            self.assertEqual(result, 0)
            self.assertIn("package manager was not run", output)
            self.assertEqual(json.loads(manifest.read_text())["devDependencies"]["vite"], "*")
            result, _ = self._run(["remove", "vite"], directory)
            self.assertEqual(result, 0)
            self.assertNotIn("vite", json.loads(manifest.read_text())["devDependencies"])

    def test_build_is_explicitly_not_implemented(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            result = main(["build"])
        self.assertEqual(result, 2)
        self.assertIn("not implemented", errors.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
