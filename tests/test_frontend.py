import json
import importlib.util
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from limbo import Limbo, Request
from limbo.frontend import (DependencyGraph, FrontendCompiler, FrontendDevServer,
                            FrontendError, ModuleResolver)
from limbo.cli import _create


class FrontendResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "src" / "lib").mkdir(parents=True)
        (self.root / "node_modules" / "demo-package" / "features").mkdir(parents=True)
        (self.root / "src" / "main.ts").write_text(
            'import "./util.js"; import {add} from "@lib/math"; import "demo-package"; export {add};', encoding="utf-8")
        (self.root / "src" / "util.ts").write_text('import "./main.ts"; export const value: number = 1;', encoding="utf-8")
        (self.root / "src" / "lib" / "math.ts").write_text("export const add = (a:number,b:number) => a+b;", encoding="utf-8")
        package = self.root / "node_modules" / "demo-package"
        (package / "package.json").write_text(json.dumps({
            "name": "demo-package",
            "exports": {".": {"browser": "./browser.js", "import": "./esm.js", "default": "./cjs.js"},
                        "./features/*": "./features/*.js"},
        }), encoding="utf-8")
        (package / "browser.js").write_text("export const platform='browser';", encoding="utf-8")
        (package / "esm.js").write_text("export const platform='esm';", encoding="utf-8")
        (package / "cjs.js").write_text("module.exports={};", encoding="utf-8")
        (package / "features" / "one.js").write_text("export default 1;", encoding="utf-8")
        (self.root / "tsconfig.json").write_text(json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["src/lib/*"]}}}), encoding="utf-8")

    def tearDown(self): self.tmp.cleanup()

    def test_relative_alias_and_conditional_package_resolution(self):
        resolver = ModuleResolver(self.root)
        self.assertEqual(resolver.resolve("./util.js", self.root / "src" / "main.ts").path.name, "util.ts")
        self.assertEqual(resolver.resolve("@lib/math").path.name, "math.ts")
        package = resolver.resolve("demo-package")
        self.assertEqual(package.path.name, "browser.js")
        self.assertEqual(resolver.resolve("demo-package/features/one").path.name, "one.js")
        with self.assertRaises(FrontendError): resolver.resolve("demo-package/private")
        with self.assertRaises(FrontendError): resolver.resolve("node:fs")

    def test_cycle_safe_graph_and_dependency_invalidation(self):
        resolver = ModuleResolver(self.root)
        graph = DependencyGraph(resolver)
        built = graph.build("src/main.ts")
        self.assertGreaterEqual(len(built), 4)
        main = (self.root / "src" / "main.ts").resolve()
        util = (self.root / "src" / "util.ts").resolve()
        self.assertIn(util, built[main])
        self.assertIn("demo-package", graph.bare_imports[main])
        invalidated = graph.invalidate(util)
        self.assertIn(main, invalidated)
        self.assertIn(util, invalidated)
        (self.root / "src" / "main.ts").write_text("export {};", encoding="utf-8")
        graph.build("src/main.ts")
        self.assertNotIn(main, graph.reverse.get(util, set()))

    def test_package_resolution_searches_nested_node_modules(self):
        importer = self.root / "node_modules" / "outer" / "src" / "index.js"
        nested = importer.parent.parent / "node_modules" / "inner"
        nested.mkdir(parents=True)
        (nested / "package.json").write_text('{"name":"inner","module":"index.js"}', encoding="utf-8")
        (nested / "index.js").write_text("export default 1;", encoding="utf-8")
        self.assertEqual(ModuleResolver(self.root).resolve("inner", importer).path, nested / "index.js")

    def test_alias_cannot_escape_root(self):
        outside = self.root.parent / (self.root.name + "-outside.ts")
        outside.write_text("export {};", encoding="utf-8")
        try:
            resolver = ModuleResolver(self.root, aliases={"@evil/*": ["../../" + outside.name]})
            with self.assertRaises(FrontendError): resolver.resolve("@evil/x")
        finally:
            outside.unlink(missing_ok=True)


class FrontendServingTests(unittest.TestCase):
    def test_generated_project_serves_frontend_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "sample"
            with redirect_stdout(StringIO()): self.assertEqual(_create(str(target)), 0)
            old_cwd = Path.cwd()
            module_name = "limbo_generated_frontend_test"
            try:
                os.chdir(target)
                spec = importlib.util.spec_from_file_location(module_name, target / "app.py")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                home = module.app.dispatch(Request("GET", "/", {}))
                js = module.app.dispatch(Request("GET", "/__limbo/src/main.js", {}))
                self.assertEqual(home.status, 200)
                self.assertIn(b'type="importmap"', home.body)
                self.assertEqual(js.status, 200)
                self.assertIn(b"served locally by Limbo", js.body)
            finally:
                os.chdir(old_cwd)
                sys.modules.pop(module_name, None)

    def test_local_module_import_map_app_mount_and_secret_block(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "main.js").write_text('import "dep"; console.log("ok");', encoding="utf-8")
            (root / ".env").write_text("SECRET=do-not-serve", encoding="utf-8")
            (root / "node_modules" / "dep").mkdir(parents=True)
            (root / "node_modules" / "dep" / "package.json").write_text('{"name":"dep","module":"index.js"}', encoding="utf-8")
            (root / "node_modules" / "dep" / "index.js").write_text("export default 3;", encoding="utf-8")
            app = Limbo(); frontend = app.enable_frontend(root)
            tags = app.frontend_script("src/main.js")
            self.assertIn('"dep":"/__limbo/_pkgfile/node_modules/dep/index.js"', tags)
            js = app.dispatch(Request("GET", "/__limbo/src/main.js", {}))
            self.assertEqual(js.status, 200)
            self.assertIn(b'console.log', js.body)
            package_url = urlsplit(frontend.import_map("src/main.js")["imports"]["dep"]).path
            package_js = app.dispatch(Request("GET", package_url, {}))
            self.assertEqual(package_js.status, 200)
            secret = frontend.serve(Request("GET", "/__limbo/.env", {}))
            self.assertEqual(secret.status, 404)
            self.assertNotIn(b"SECRET", secret.body)
            hidden = root / ".private.js"
            hidden.write_text("export const SECRET='private';", encoding="utf-8")
            try:
                (root / "src" / "public.js").symlink_to(hidden)
                self.assertEqual(frontend.serve(Request("GET", "/__limbo/src/public.js", {})).status, 404)
            except OSError:
                pass
            with self.assertRaises(ValueError): FrontendDevServer(root, url_prefix="/bad<script>")
            missing = frontend.serve(Request("GET", "/__limbo/_importmap.json", {}))
            self.assertEqual(missing.status, 400)

    def test_dev_server_emits_full_reload_fallback_and_version_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.js").write_text('export const value = 1;', encoding="utf-8")
            frontend = FrontendDevServer(root)
            tags = frontend.script_tag("main.js")
            self.assertIn("/__limbo/_client.js", tags)
            frontend.import_map("main.js")
            first = frontend.serve(Request("GET", "/__limbo/_version", {}))
            client = frontend.serve(Request("GET", "/__limbo/_client.js", {}))
            self.assertEqual(first.status, 200)
            self.assertEqual(client.status, 200)
            self.assertIn(b"location.reload()", client.body)
            old_version = json.loads(first.body)["version"]
            (root / "main.js").write_text('export const value = 2;', encoding="utf-8")
            new_version = frontend.serve(Request("GET", "/__limbo/_version", {}))
            self.assertNotEqual(old_version, json.loads(new_version.body)["version"])

    def test_dev_server_uses_content_addressed_transform_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.js").write_text("export const value = 1;", encoding="utf-8")
            frontend = FrontendDevServer(root)
            first = frontend.serve(Request("GET", "/__limbo/main.js", {}))
            second = frontend.serve(Request("GET", "/__limbo/main.js", {}))
            self.assertEqual(first.status, 200)
            self.assertEqual(second.status, 200)
            self.assertEqual(frontend.cache.hits, 1)
            (root / "main.js").write_text("export const value = 2;", encoding="utf-8")
            third = frontend.serve(Request("GET", "/__limbo/main.js", {}))
            self.assertIn(b"value = 2", third.body)
            self.assertEqual(frontend.cache.misses, 2)
            hidden_cache = frontend.serve(Request("GET", "/__limbo/.limbo/cache/frontend", {}))
            self.assertEqual(hidden_cache.status, 404)

    def test_css_side_effect_import_is_rewritten_and_served_as_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "main.js").write_text('import "./site.css"; export const ready = true;', encoding="utf-8")
            (root / "src" / "site.css").write_text("body { color: red; }", encoding="utf-8")
            frontend = FrontendDevServer(root)
            entry = frontend.serve(Request("GET", "/__limbo/src/main.js", {}))
            self.assertEqual(entry.status, 200)
            self.assertIn(b"/__limbo/_css/src/site.css", entry.body)
            css_module = frontend.serve(Request("GET", "/__limbo/_css/src/site.css", {}))
            self.assertEqual(css_module.status, 200)
            self.assertIn(b"rel = 'stylesheet'", css_module.body)
            self.assertIn(b"document.head.appendChild", css_module.body)
            self.assertEqual(frontend.serve(Request("GET", "/__limbo/_css/src/unknown.css", {})).status, 404)

    def test_css_import_with_bindings_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "main.js").write_text('import styles from "./site.css";', encoding="utf-8")
            (root / "src" / "site.css").write_text("body{}", encoding="utf-8")
            response = FrontendDevServer(root).serve(Request("GET", "/__limbo/src/main.js", {}))
            self.assertEqual(response.status, 422)
            self.assertIn(b"side-effect import", response.body)

    def test_wasm_asset_is_served_with_browser_mime_type(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "module.wasm").write_bytes(b"\\x00asm\\x01\\x00\\x00\\x00")
            response = FrontendDevServer(root).serve(Request("GET", "/__limbo/module.wasm", {}))
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body, b"\\x00asm\\x01\\x00\\x00\\x00")
            self.assertIn("application/wasm", response.headers.get("Content-Type", ""))

    def test_optional_esbuild_failure_is_actionable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "main.ts"
            path.write_text("const count: number = 1;", encoding="utf-8")
            compiler = FrontendCompiler(temp)
            if compiler.esbuild:
                self.assertIn("count", compiler.transform(path))
            else:
                with self.assertRaisesRegex(FrontendError, "npm install --save-dev esbuild"):
                    compiler.transform(path)


if __name__ == "__main__": unittest.main()
