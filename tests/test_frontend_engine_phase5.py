import json
import base64
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from limbo.cli import main as cli_main
from contextlib import redirect_stdout
from io import StringIO

from limbo.frontend import DependencyGraph, ModuleResolver
from limbo.frontend_scanner import rewrite_import_meta_hot, scan_imports
from limbo.css_scanner import scan_css_dependencies
from limbo.frontend_cache import TransformCache
from limbo.frontend_engine import (
    AdapterError, AdapterRegistry, BrowserNativeAdapter, EsbuildAdapter, FrameworkSFCAdapter,
)


class FrontendAdapterContractTests(unittest.TestCase):
    def test_transform_cache_persists_and_keys_compiler_options(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = TransformCache(temp)
            source = b"const value = 1;"
            key = cache.key(source, compiler="esbuild-1", options={"target": "es2020"})
            self.assertIsNone(cache.get(key))
            cache.put(key, "const value=1;")
            self.assertEqual(cache.get(key), "const value=1;")
            self.assertEqual((cache.hits, cache.misses), (1, 1))
            self.assertNotEqual(key, cache.key(source, compiler="esbuild-1", options={"target": "es2022"}))
            self.assertEqual(cache.clear(), 1)

    def test_css_scanner_finds_imports_and_url_assets_but_ignores_comments(self):
        source = '''
/* @import "fake.css"; url(fake.png) */
@import "theme.css";
.hero { background: url("../img/hero.webp"); }
.icon { background: url(data:image/svg+xml,abc); }
'''
        refs = scan_css_dependencies(source)
        self.assertEqual([(r.specifier, r.kind) for r in refs],
                         [("theme.css", "import"), ("../img/hero.webp", "asset")])

    def test_css_graph_resolves_bare_package_imports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package_css = root / "node_modules" / "theme-package" / "css" / "theme.css"
            package_css.parent.mkdir(parents=True)
            package_css.write_text("body{color:blue}", encoding="utf-8")
            (root / "node_modules" / "theme-package" / "package.json").write_text(
                '{"name":"theme-package","exports":{"./css/theme.css":"./css/theme.css"}}',
                encoding="utf-8",
            )
            (root / "main.js").write_text('import "./theme.css";', encoding="utf-8")
            (root / "theme.css").write_text('@import "theme-package/css/theme.css";', encoding="utf-8")
            graph = DependencyGraph(ModuleResolver(root))
            edges = graph.build("main.js")
            self.assertIn(package_css.resolve(), edges[(root / "theme.css").resolve()])

    def test_dependency_graph_tracks_css_and_binary_asset_edges(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src" / "img").mkdir(parents=True)
            (root / "src" / "main.js").write_text('import "./site.css";', encoding="utf-8")
            (root / "src" / "site.css").write_text('@import "./theme.css"; .x{background:url("./img/pixel.png")}', encoding="utf-8")
            (root / "src" / "theme.css").write_text("body{margin:0}", encoding="utf-8")
            (root / "src" / "img" / "pixel.png").write_bytes(b"not-really-png")
            graph = DependencyGraph(ModuleResolver(root))
            edges = graph.build("src/main.js")
            css = (root / "src" / "site.css").resolve()
            main = (root / "src" / "main.js").resolve()
            asset = (root / "src" / "img" / "pixel.png").resolve()
            self.assertIn(css, edges[main])
            self.assertIn(asset, edges[css])
            self.assertIn((root / "src" / "theme.css").resolve(), edges[css])

    def test_scanner_finds_static_reexport_side_effect_and_literal_dynamic_imports(self):
        source = '''
// import "comment-only";
const text = "import 'string-only'";
import "polyfill";
import {
  value,
  other
} from "./values.js";
export { value as publicValue } from "./public.js";
const lazy = import("./lazy.js");
const computed = import("./pages/" + page + ".js");
const meta = import.meta.url;
'''
        result = scan_imports(source)
        pairs = {(item.specifier, item.kind) for item in result.imports}
        self.assertIn(("polyfill", "side-effect"), pairs)
        self.assertIn(("./values.js", "static"), pairs)
        self.assertIn(("./public.js", "re-export"), pairs)
        self.assertIn(("./lazy.js", "dynamic"), pairs)
        self.assertNotIn(("comment-only", "side-effect"), pairs)
        self.assertNotIn(("string-only", "side-effect"), pairs)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertIn("non-literal dynamic import", result.diagnostics[0])

    def test_scanner_handles_template_interpolation_and_regex_literals(self):
        source = r'''
const message = `plain import("./template-text.js") ${import("./inside-template.js")}`;
const guarded = `${/[}]/.test("}") ? import("./after-regex.js") : null}`;
const pattern = /import\("\.\/regex-fake\.js"\)/g;
const nested = /[{}]import\("\.\/also-fake\.js"\)/;
'''
        result = scan_imports(source)
        self.assertEqual(
            [(item.specifier, item.kind) for item in result.imports],
            [("./inside-template.js", "dynamic"), ("./after-regex.js", "dynamic")],
        )
        self.assertEqual(result.diagnostics, ())

    def test_hmr_rewriter_ignores_comments_and_strings(self):
        source = 'const text="import.meta.hot"; // import.meta.hot\nimport.meta.hot.accept();'
        rewritten = rewrite_import_meta_hot(source)
        self.assertIn('const text="import.meta.hot"', rewritten)
        self.assertIn('// import.meta.hot', rewritten)
        self.assertIn("__LIMBO_HMR_HOT__(import.meta.url)", rewritten)
        self.assertNotIn("import.meta.hot.accept", rewritten)

    def test_dependency_graph_uses_scanner_and_records_dynamic_imports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.js").write_text(
                'const note = "import \\"./fake.js\\""; import("./lazy.js");', encoding="utf-8")
            (root / "lazy.js").write_text("export default 1;", encoding="utf-8")
            (root / "fake.js").write_text("export default 0;", encoding="utf-8")
            graph = DependencyGraph(ModuleResolver(root))
            edges = graph.build("main.js")
            main = (root / "main.js").resolve()
            self.assertEqual({path.name for path in edges[main]}, {"lazy.js"})

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

    def test_esbuild_adapter_uses_project_source_as_stdin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "entry.ts"
            path.write_text("const value: number = 1;", encoding="utf-8")
            adapter = EsbuildAdapter(root, executable="esbuild", jsx_import_source="preact")
            map_data = base64.b64encode(b'{"version":3,"sources":["entry.ts"]}').decode("ascii")
            emitted = "const value = 1;\\n//# sourceMappingURL=data:application/json;base64," + map_data
            completed = SimpleNamespace(returncode=0, stdout=emitted, stderr="")
            with patch("limbo.frontend_engine.subprocess.run", return_value=completed) as run:
                result = adapter.transform(path)
            self.assertEqual(result.code, emitted)
            self.assertEqual(json.loads(result.source_map), {"version": 3, "sources": ["entry.ts"]})
            self.assertEqual(run.call_args.kwargs["input"], "const value: number = 1;")
            self.assertIn("--loader=ts", run.call_args.args[0])
            self.assertIn("--sourcemap=inline", run.call_args.args[0])
            self.assertIn("--jsx-import-source=preact", run.call_args.args[0])

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
            self.assertEqual(ids, {"browser-native", "esbuild", "vue", "svelte"})

    def test_framework_adapters_are_registered_but_optional(self):
        with tempfile.TemporaryDirectory() as temp:
            vue = FrameworkSFCAdapter(temp, "vue")
            svelte = FrameworkSFCAdapter(temp, "svelte")
            self.assertEqual(vue.capability.extensions, (".vue",))
            self.assertEqual(svelte.capability.extensions, (".svelte",))
            with patch.object(vue, "detect", return_value=False):
                with self.assertRaisesRegex(AdapterError, "Requires Node.js"):
                    vue.transform(Path(temp) / "Component.vue")

    def test_sfc_adapter_serves_compiler_source_map_inline(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Component.vue"
            path.write_text("<template><p>hello</p></template>", encoding="utf-8")
            source_map = {"version": 3, "sources": ["Component.vue"], "names": [], "mappings": "AAAA"}
            adapter = FrameworkSFCAdapter(temp, "vue")
            with patch.object(adapter, "detect", return_value=True), \
                 patch("limbo.frontend.FrontendCompiler._compile_sfc_result", return_value=("export default {}", source_map)):
                result = adapter.transform(path)
            self.assertEqual(result.source_map, json.dumps(source_map, separators=(",", ":")))
            self.assertIn("sourceMappingURL=data:application/json;base64,", result.code)
            encoded = result.code.rsplit("sourceMappingURL=data:application/json;base64,", 1)[1].strip()
            self.assertEqual(json.loads(base64.b64decode(encoded)), source_map)

    def test_typescript_sfc_composes_compiler_and_esbuild_source_maps(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Component.vue"
            path.write_text('<script lang="ts">const value: number = 1;</script>', encoding="utf-8")
            compiler_map = {"version": 3, "sources": ["Component.vue"], "names": [], "mappings": "AAAA"}
            output_map = {"version": 3, "sources": ["Component.vue"], "names": [], "mappings": "AAAA"}
            output_code = "const value = 1;\\n//# sourceMappingURL=data:application/json;base64," + base64.b64encode(
                json.dumps(output_map).encode("utf-8")
            ).decode("ascii")
            adapter = FrameworkSFCAdapter(temp, "vue")
            with patch.object(adapter, "detect", return_value=True), \
                 patch("limbo.frontend.shutil.which", side_effect=lambda name: "esbuild" if name == "esbuild" else "node"), \
                 patch("limbo.frontend.FrontendCompiler._compile_sfc_result", return_value=("const value: number = 1;", compiler_map)), \
                 patch("limbo.frontend.FrontendCompiler._esbuild_stdin", return_value=output_code) as esbuild:
                result = adapter.transform(path)
            self.assertEqual(json.loads(result.source_map), output_map)
            self.assertTrue(esbuild.call_args.kwargs["source_map"])
            self.assertIn("sourceMappingURL=data:application/json;base64,", esbuild.call_args.args[0])

    def test_registry_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            AdapterRegistry((BrowserNativeAdapter(), BrowserNativeAdapter()))


if __name__ == "__main__":
    unittest.main()
