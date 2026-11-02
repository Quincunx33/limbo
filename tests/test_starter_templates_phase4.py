"""Phase 4 CLI starter-template smoke tests."""
import tempfile
import unittest
from pathlib import Path

from limbo.cli import _create


class StarterTemplateTests(unittest.TestCase):
    def test_all_starters_create_valid_config_and_readme(self):
        for template in ("python-api", "static", "browser-native", "vue"):
            with self.subTest(template=template), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "starter"
                self.assertEqual(_create(str(target), template), 0)
                self.assertTrue((target / "limbo.toml").is_file())
                self.assertTrue((target / "README.md").is_file())
                if template == "static":
                    self.assertTrue((target / "dist" / "index.html").is_file())
                else:
                    source = (target / "app.py").read_text(encoding="utf-8")
                    compile(source, str(target / "app.py"), "exec")
                    self.assertTrue((target / "frontend" / "src" / "main.js").is_file())
                if template == "vue":
                    self.assertTrue((target / "frontend" / "src" / "App.vue").is_file())
                    self.assertTrue((target / "package.json").is_file())

    def test_unknown_template_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(_create(str(Path(directory) / "starter"), "unsupported"), 2)


if __name__ == "__main__":
    unittest.main()
