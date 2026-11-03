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
        self.assertNotIn(util, graph.edges)

    def test_refresh_rebuilds_graph_and_prunes_unreachable_modules(self):
        resolver = ModuleResolver(self.root)
        graph = DependencyGraph(resolver)
        graph.build("src/main.ts")
        main = (self.root / "src" / "main.ts").resolve()
        util = (self.root / "src" / "util.ts").resolve()
        (self.root / "src" / "main.ts").write_text("export const clean = true;", encoding="utf-8")
        affected, refreshed = graph.refresh("src/main.ts", main)
        self.assertIn(main, affected)
        self.assertIn(main, refreshed)
        self.assertNotIn(util, graph.edges)
        self.assertNotIn(main, graph.reverse.get(util, set()))

    def test_package_resolution_searches_nested_node_modules(self):
        importer = self.root / "node_modules" / "outer" / "src" / "index.js"
        nested = importer.parent.parent / "node_modules" / "inner"
        nested.mkdir(parents=True)
        (nested / "package.json").write_text('{"name":"inner","module":"index.js"}', encoding="utf-8")
        (nested / "index.js").write_text("export default 1;", encoding="utf-8")
        # Windows may resolve the temporary directory through a different
        # casing or its 8.3 short-path alias; compare canonical paths.
        self.assertEqual(ModuleResolver(self.root).resolve("inner", importer).path, (nested / "index.js").resolve())

    def test_alias_cannot_escape_root(self):
        outside = self.root.parent / (self.root.name + "-outside.ts")
        outside.write_text("export {};", encoding="utf-8")
        try:
            resolver = ModuleResolver(self.root, aliases={"@evil/*": ["../../" + outside.name]})
            with self.assertRaises(FrontendError): resolver.resolve("@evil/x")
        finally:
            outside.unlink(missing_ok=True)

    def test_package_conditions_are_configurable_and_patterns_are_specific(self):
        package = self.root / "node_modules" / "demo-package"
        (package / "development.js").write_text("export const mode='development';", encoding="utf-8")
        (package / "features" / "special.js").write_text("export const match='generic';", encoding="utf-8")
        (package / "features" / "special" / "one.js").parent.mkdir(parents=True, exist_ok=True)
        (package / "features" / "special" / "one.js").write_text("export const match='specific';", encoding="utf-8")
        (package / "package.json").write_text(json.dumps({
                "name": "demo-package",
                "exports": {
                    ".": {"development": "./development.js", "default": "./browser.js"},
                "./features/*": "./features/generic/*.js",
                "./features/special/*": "./features/special/*.js",
            },
        }), encoding="utf-8")
        resolver = ModuleResolver(self.root, conditions=("development", "default"))
        self.assertEqual(resolver.resolve("demo-package").path.name, "development.js")
        self.assertEqual(resolver.resolve("demo-package/features/special/one").path.name, "one.js")
        with self.assertRaises(ValueError): ModuleResolver(self.root, conditions=())

    def test_package_condition_object_order_and_null_exports(self):
        package = self.root / "node_modules" / "demo-package"
        (package / "import-first.js").write_text("export default 'import';", encoding="utf-8")
        (package / "package.json").write_text(json.dumps({
            "name": "demo-package",
            "exports": {
                ".": {"import": "./import-first.js", "browser": "./browser.js", "default": "./cjs.js"},
                "./private": None,
            },
        }), encoding="utf-8")
        resolver = ModuleResolver(self.root, conditions=("browser", "import", "default"))
        self.assertEqual(resolver.resolve("demo-package").path.name, "import-first.js")
        with self.assertRaisesRegex(FrontendError, "null export/imports target"):
            resolver.resolve("demo-package/private")

    def test_exports_and_imports_fallback_arrays_skip_missing_targets(self):
        package = self.root / "node_modules" / "demo-package"
        (package / "fallback.js").write_text("export default 'fallback';", encoding="utf-8")
        (package / "package.json").write_text(json.dumps({
            "name": "demo-package",
            "exports": {".": ["./missing.js", "./fallback.js"]},
        }), encoding="utf-8")
        resolver = ModuleResolver(self.root)
        self.assertEqual(resolver.resolve("demo-package").path.name, "fallback.js")
        (self.root / "package.json").write_text(json.dumps({
            "imports": {"#internal": ["./missing-internal.js", "./src/util.ts"]},
        }), encoding="utf-8")
        self.assertEqual(resolver.resolve("#internal", self.root / "src" / "main.ts").path.name, "util.ts")


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

    def test_local_runtime_packages_are_served_without_cdn_imports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "main.js").write_text(
                'import { createElement } from "local-ui"; export const app = createElement("main");',
                encoding="utf-8",
            )
            package = root / "node_modules" / "local-ui"
            package.mkdir(parents=True)
            (package / "package.json").write_text(
                json.dumps({"name": "local-ui", "exports": {".": "./index.js"}}), encoding="utf-8"
            )
            (package / "index.js").write_text(
                "export function createElement(tag){ return document.createElement(tag); }", encoding="utf-8"
            )
            frontend = FrontendDevServer(root)
            imports = frontend.import_map("src/main.js")["imports"]
            self.assertEqual(imports["local-ui"], "/__limbo/_pkgfile/node_modules/local-ui/index.js")
            package_response = frontend.serve(Request("GET", imports["local-ui"], {}))
            self.assertEqual(package_response.status, 200)
            self.assertIn(b"createElement", package_response.body)

    def test_remote_imports_are_rejected_in_local_first_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "main.js").write_text(
                'import "https://example.com/react.js";', encoding="utf-8"
            )
            frontend = FrontendDevServer(root)
            with self.assertRaisesRegex(FrontendError, "Remote frontend import"):
                frontend.import_map("src/main.js")

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
            self.assertIn(b"sessionStorage", client.body)
            self.assertIn(b"limbo:hmr-ready", client.body)
            self.assertIn(b"__LIMBO_HMR_HOT__", client.body)
            self.assertIn(b"importFresh", client.body)
            old_version = json.loads(first.body)["version"]
            (root / "main.js").write_text('export const value = 2;', encoding="utf-8")
            new_version = frontend.serve(Request("GET", "/__limbo/_version", {}))
            payload = json.loads(new_version.body)
            self.assertNotEqual(old_version, payload["version"])
            self.assertIn("/__limbo/main.js", payload["changed"])

    def test_dev_server_uses_content_addressed_transform_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "main.js").write_text('export const value = 1;', encoding="utf-8")
            frontend = FrontendDevServer(root)
            self.assertIsNotNone(frontend.registry.select(".js"))
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

    def test_css_module_import_exports_mapping_and_rewrites_class_selectors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "main.js").write_text(
                'import styles from "./button.module.css"; export const button = styles.button;', encoding="utf-8")
            (root / "src" / "button.module.css").write_text(
                ".button, .active:hover { color: red; }\n", encoding="utf-8")
            frontend = FrontendDevServer(root)
            entry = frontend.serve(Request("GET", "/__limbo/src/main.js", {}))
            self.assertEqual(entry.status, 200)
            self.assertIn(b"/_cssmodule/src/button.module.css", entry.body)
            module = frontend.serve(Request("GET", "/__limbo/_cssmodule/src/button.module.css", {}))
            self.assertEqual(module.status, 200)
            self.assertIn(b'"button":"button_', module.body)
            self.assertIn(b"export default styles", module.body)
            css = frontend.serve(Request("GET", "/__limbo/_cssmodule-css/src/button.module.css", {}))
            self.assertEqual(css.status, 200)
            self.assertIn(b".button_", css.body)
            self.assertIn(b".active_", css.body)

    def test_package_css_asset_is_served_with_css_mime_type(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            package = root / "node_modules" / "theme" / "dist"
            package.mkdir(parents=True)
            (root / "src" / "main.js").write_text('import "theme/style.css";', encoding="utf-8")
            (root / "node_modules" / "theme" / "package.json").write_text(
                '{"name":"theme","exports":{"./style.css":"./dist/style.css"}}', encoding="utf-8")
            (package / "style.css").write_text('@import "theme-base/reset.css";body{color:blue}', encoding="utf-8")
            base = root / "node_modules" / "theme-base"
            base.mkdir(parents=True)
            (base / "package.json").write_text(
                '{"name":"theme-base","exports":{"./reset.css":"./reset.css"}}', encoding="utf-8")
            (base / "reset.css").write_text("*{box-sizing:border-box}", encoding="utf-8")
            frontend = FrontendDevServer(root)
            frontend.import_map("src/main.js")
            entry = frontend.serve(Request("GET", "/__limbo/src/main.js", {}))
            self.assertEqual(entry.status, 200)
            self.assertIn(b"/__limbo/_css/node_modules/theme/dist/style.css", entry.body)
            css_module = frontend.serve(Request("GET", "/__limbo/_css/node_modules/theme/dist/style.css", {}))
            self.assertEqual(css_module.status, 200)
            self.assertIn(b"stylesheet", css_module.body)
            package_css = frontend.serve(Request("GET", "/__limbo/_pkgfile/node_modules/theme/dist/style.css", {}))
            self.assertEqual(package_css.status, 200)
            self.assertIn("text/css", package_css.headers.get("Content-Type", ""))
            self.assertIn(b"/__limbo/_pkgfile/node_modules/theme-base/reset.css", package_css.body)

    def test_css_import_with_bindings_serves_css_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "main.js").write_text('import styles from "./site.css";', encoding="utf-8")
            (root / "src" / "site.css").write_text("body{}", encoding="utf-8")
            frontend = FrontendDevServer(root)
            response = frontend.serve(Request("GET", "/__limbo/src/main.js", {}))
            self.assertEqual(response.status, 200)
            self.assertIn(b"/_cssmodule/src/site.css", response.body)
            module = frontend.serve(Request("GET", "/__limbo/_cssmodule/src/site.css", {}))
            self.assertEqual(module.status, 200)
            self.assertIn(b"export default styles", module.body)

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
