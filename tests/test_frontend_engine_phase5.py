import json
import tempfile
import unittest
from pathlib import Path

from limbo.cli import main as cli_main
from contextlib import redirect_stdout
from io import StringIO

from limbo.frontend_engine import (
    AdapterError, AdapterRegistry, BrowserNativeAdapter, EsbuildAdapter,
    TransformResult,
)


class FrontendAdapterContractTests(unittest.TestCase):
    def test_browser_native_adapter_passes_js_and_css_without_node(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            js = root / "main.js"
            css = root / "site.css"
            js.write_text('export const answer = 42;', encoding="utf-8")
            css.write_text('body { color: red; }', encoding="utf-8")
            adapter = BrowserNativeAdapter()
            self.assertTrue(adapter.detect())
            self.assertEqual(adapter.transform(js).code, 'export const answer = 42;')
            self.assertEqual(adapter.transform(css).loader, "css")
            with self.assertRaises(AdapterError):
                adapter.transform(root / "main.ts")

    def test_esbuild_rejects_unsupported_extensions_and_outside_paths(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            root = Path(temp)
            adapter = EsbuildAdapter(root, executable=None)
            with self.assertRaises(AdapterError):
                adapter.transform(Path(outside) / "main.ts")
            path = root / "main.vue"
            path.write_text("<template/>", encoding="utf-8")
            with self.assertRaises(AdapterError):
                adapter.transform(path)

    def test_registry_manifest_is_stable_json_and_selects_by_extension(self):
        registry = AdapterRegistry((BrowserNativeAdapter(),))
        self.assertIsInstance(registry.select("js"), BrowserNativeAdapter)
        self.assertIsNone(registry.select(".tsx"))
        manifest = registry.manifest()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["adapters"][0]["adapter_id"], "browser-native")
        self.assertTrue(manifest["adapters"][0]["available"])
        self.assertEqual(json.loads(registry.manifest_json()), manifest)

    def test_cli_prints_json_capability_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli_main(["frontend", "capabilities", temp, "--json"]), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["schema_version"], 1)
            ids = {item["adapter_id"] for item in payload["adapters"]}
            self.assertEqual(ids, {"browser-native", "esbuild"})

    def test_registry_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            AdapterRegistry((BrowserNativeAdapter(), BrowserNativeAdapter()))


if __name__ == "__main__":
    unittest.main()
