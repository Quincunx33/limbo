"""Phase 6 local production build."""
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from limbo.build_engine import BuildError, build_project
from limbo.config import load_config


def _project(root: Path) -> None:
    frontend = root / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "src" / "greet.js").write_text('export function greet(name){ return "Hello " + name; }\n', encoding="utf-8")
    (frontend / "src" / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>\n", encoding="utf-8")
    (frontend / "src" / "app.css").write_text(
        "body{background:#111}\n.mark{background:url(\"./logo.svg\")}\n", encoding="utf-8"
    )
    (frontend / "src" / "main.js").write_text(
        'import "./app.css";\nimport { greet } from "./greet.js";\n'
        'document.querySelector("#app").textContent = greet("Limbo");\n',
        encoding="utf-8",
    )
    (frontend / "index.html").write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Demo</title></head>"
        "<body><div id=\"app\"></div><script type=\"module\" src=\"./src/main.js\"></script></body></html>\n",
        encoding="utf-8",
    )
    (root / "limbo.toml").write_text('[build]\nout_dir = "dist"\nbase = "/"\n', encoding="utf-8")


class BuildTests(unittest.TestCase):
    def test_browser_native_build_is_reproducible_and_resolves_assets(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _project(root)
            config = load_config(root / "limbo.toml", environ={})
            first = build_project(root, config)
            second = build_project(root, config)
            self.assertEqual(first.digest, second.digest)
            manifest = json.loads((first.out_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["code_splitting"])
            for source, output in manifest["files"].items():
                self.assertTrue((first.out_dir / output).is_file(), source)
            html = (first.out_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn('rel="stylesheet"', html)
            self.assertNotIn("./src/main.js", html)
            self.assertNotIn("logo.svg\"", (first.out_dir / manifest["files"]["src/app.css"]).read_text(encoding="utf-8"))
            javascript = (first.out_dir / manifest["files"]["src/main.js"]).read_text(encoding="utf-8")
            self.assertNotIn("./app.css", javascript)
            self.assertNotIn("./greet.js", javascript)
            self.assertIn("./", javascript)

    def test_second_build_bytes_match(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _project(root)
            build_project(root)
            snapshot = {path.relative_to(root / "dist").as_posix(): path.read_bytes() for path in (root / "dist").rglob("*") if path.is_file()}
            build_project(root)
            again = {path.relative_to(root / "dist").as_posix(): path.read_bytes() for path in (root / "dist").rglob("*") if path.is_file()}
            self.assertEqual(snapshot, again)

    def test_rejects_escape_and_code_splitting_and_uncompiled_typescript(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _project(root)
            with self.assertRaises(BuildError):
                build_project(root, out_dir="../outside")
            with self.assertRaises(BuildError):
                build_project(root, code_splitting=True)
            (root / "frontend" / "src" / "main.ts").write_text("export const n: number = 1;\n", encoding="utf-8")
            with self.assertRaisesRegex(BuildError, "esbuild"):
                build_project(root, entry="src/main.ts")

    def test_bare_import_stays_external(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _project(root)
            (root / "frontend" / "src" / "main.js").write_text(
                'import "vue";\nimport { greet } from "./greet.js";\ngreet("x");\n', encoding="utf-8"
            )
            report = build_project(root)
            self.assertIn("vue", report.manifest["external"])
            self.assertTrue(any("vue" in warning for warning in report.warnings))

    def test_minify_without_esbuild_warns(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _project(root)
            report = build_project(root, minify=True)
            if report.minify == "skipped":
                self.assertTrue(any("esbuild" in warning for warning in report.warnings))
            else:
                self.assertEqual(report.minify, "esbuild")

    def test_base_path_is_applied(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _project(root)
            report = build_project(root, base="/app/")
            html = (report.out_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("/app/assets/", html)


if __name__ == "__main__":
    unittest.main()
