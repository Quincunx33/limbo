"""Phase 4 project metadata and preview path tests."""
import json
import tempfile
import unittest
from pathlib import Path

from limbo.project import ProjectMetadataError, edit_package_metadata, preview_directory


class ProjectMetadataTests(unittest.TestCase):
    def test_add_runtime_dependency_without_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.json"
            path.write_text('{"name":"demo"}', encoding="utf-8")
            message = edit_package_metadata(directory, "add", "react")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["dependencies"]["react"], "*")
            self.assertIn("package manager was not run", message)

    def test_add_dev_dependency_and_scoped_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.json"
            path.write_text("{}", encoding="utf-8")
            edit_package_metadata(directory, "add", "@types/node", dev=True)
            self.assertEqual(json.loads(path.read_text())["devDependencies"]["@types/node"], "*")

    def test_duplicate_add_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.json"
            path.write_text('{"dependencies":{"vite":"^6"}}', encoding="utf-8")
            message = edit_package_metadata(directory, "add", "vite")
            self.assertIn("already declared", message)
            self.assertEqual(json.loads(path.read_text())["dependencies"]["vite"], "^6")

    def test_remove_from_all_dependency_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.json"
            path.write_text('{"dependencies":{"x":"1"},"devDependencies":{"x":"2"}}', encoding="utf-8")
            edit_package_metadata(directory, "remove", "x")
            data = json.loads(path.read_text())
            self.assertNotIn("x", data["dependencies"])
            self.assertNotIn("x", data["devDependencies"])

    def test_reject_invalid_names_and_missing_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ProjectMetadataError):
                edit_package_metadata(directory, "add", "../escape")
            with self.assertRaises(ProjectMetadataError):
                edit_package_metadata(directory, "add", "react")

    def test_reject_malformed_package_json(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "package.json").write_text("{", encoding="utf-8")
            with self.assertRaises(ProjectMetadataError):
                edit_package_metadata(directory, "add", "react")

    def test_remove_missing_package_is_actionable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "package.json"
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ProjectMetadataError):
                edit_package_metadata(directory, "remove", "missing")

    def test_preview_requires_existing_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dist"
            output.mkdir()
            self.assertEqual(preview_directory(directory, "dist"), output.resolve())
            with self.assertRaises(ProjectMetadataError):
                preview_directory(directory, "missing")


if __name__ == "__main__":
    unittest.main()
