# Frontend compiler adapters

Limbo's frontend engine is Python-orchestrated and local-first. The core does not
install packages, download compiler binaries, or require Node.js for
browser-native JavaScript and CSS.

For the staged plan covering local runtime packages, no-CDN import maps,
React/Preact/Solid/Lit, compiler-assisted Vue/Svelte/Angular and framework-owned
local servers (Next/Nuxt/SvelteKit/Remix/Astro/Qwik), see
[Frontend Runtime & Framework Integration Roadmap](FRONTEND_RUNTIME_ROADMAP.md).
That roadmap is a plan, not a claim that these integrations are already supported.

## Adapter contract

Implementations satisfy CompilerAdapter:

- capability: immutable AdapterCapability metadata.
- detect(): reports whether required local tools are available.
- transform(source_path, source=None): returns a TransformResult or raises
  AdapterError.

A transform result contains emitted code, loader, discovered dependency strings,
an optional source map and diagnostics. The registry selects adapters by file
extension and publishes a stable, versioned JSON capability manifest.

## Current adapters

| Adapter | Extensions | Requirement | Status |
|---|---|---|---|
| browser-native | .js, .mjs, .css | None | Supported pass-through |
| esbuild | .ts, .tsx, .jsx | Local esbuild executable | Optional; emits inline source maps |
| vue | .vue | Node.js and local @vue/compiler-sfc | Optional SFC bridge |
| svelte | .svelte | Node.js and local svelte | Optional SFC bridge |

No adapter invokes a package manager. Install optional tools in the frontend
project yourself. The live dev-server transform path selects browser-native,
esbuild or Vue/Svelte adapters through the same registry used for capability
detection. Vue and Svelte adapters use the existing local SFC bridge. Their
runtime compatibility remains experimental; JSON keeps an explicit compiler
fallback because it is not currently a registered adapter.

## Local runtime packages

Buildless development is local-first by default. Bare imports are resolved from
the project's node_modules, emitted into a generated import map, and served
under /_pkgfile/; no CDN URL is inserted. The dev server rejects http:, https:
and data: module imports unless an explicit remote-import adapter is selected.
Install a browser-native ESM runtime locally, for example React, Preact, Solid
or Lit, and import it by package name from the frontend source. CommonJS-only
packages still require a local transform or bundler and are reported as an
actionable error rather than being guessed at runtime.

## Capability manifest

Run:

    limbo frontend capabilities
    limbo frontend capabilities --json

The JSON output has a versioned top-level schema_version and stable, sorted
adapter entries. Each entry describes extensions, availability, requirements,
source-map/HMR claims, maturity status and a human-readable reason. Consumers
should ignore unknown future fields and check schema_version before relying
on the schema.

## Dependency scanning and resolution

The dependency graph uses an optional local Acorn parser for ordinary JavaScript
when the repository/project installs the frontend tooling in package.json.
Acorn provides AST-accurate static imports, side-effect imports, re-exports,
literal dynamic imports, import attributes and source ranges. TypeScript/JSX
continues through the local esbuild adapter before graph scanning. If Acorn or
Node is unavailable, Limbo falls back to its standard-library lexical scanner;
computed dynamic imports remain diagnostic-only rather than guessed.

A separate CSS scanner detects quoted @import and url() references.
Relative CSS imports and assets are tracked in the graph; binary assets are
leaves and are served with MIME types inferred by Python. JavaScript side-effect
imports of package CSS and bare package CSS @import dependencies work when the
package path resolves through the package resolver. Side-effect imports add a
stylesheet link. Bound CSS imports are treated as CSS modules and export a
deterministic class map. Local classes/IDs, nested selectors,
:global/:local scope markers, keyframes, animation names,
composes/compose-with, cycle detection and composition invalidation are
supported. @value is rejected with an actionable diagnostic rather than
served as invalid CSS; this is a focused CSS Modules subset, not a complete
PostCSS-compatible implementation.

The resolver supports local paths, tsconfig path aliases, package exports
subpaths/patterns and conditions, and package imports maps. Active conditions
are selected in the order written in the package's condition object, and more
specific wildcard patterns win. Null targets are rejected. Node's full fallback
array, validation and nested-pattern semantics are not yet completely covered.
The resolver rejects Node.js built-ins and package paths that escape the project
root; do not assume complete Node or bundler compatibility.

## Transform cache and development reload

Transformed source is cached persistently under .limbo/cache/frontend,
ignored by Git and inaccessible through the public frontend route. Cache keys
include source bytes, relative path, extension, compiler executable identity
fingerprint, local SFC package manifest fingerprints and transform options.
Writes use an atomic replace. Editing a file produces a new content key.
The dependency graph supports incremental invalidation and a refresh() rebuild
operation; removed imports are pruned from both forward and reverse edges so
old modules are not retained after edits.

The dev server exposes per-module version deltas and a polling client. Modules
may opt into accept-based updates with import.meta.hot.accept() or
import.meta.hot.accept('./dependency.js', callback). A module can register a
dispose(data => ...) callback and use data to carry state into its next
evaluation. Changed modules without a registered accept boundary still trigger
the safe full-page reload fallback. The opt-in JSON state bridge remains
available as window.__LIMBO_HMR_STATE__ and emits limbo:hmr-ready after a
fallback reload. Vue SFC modules register a stable component identity and use
Vue's __VUE_HMR_RUNTIME__.reload() hook when the browser runtime exposes it,
preserving component instances across accepted updates. Svelte remains on the
generic accept/dispose or full-reload path.

TypeScript-in-SFC transforms pass the SFC compiler's inline map through esbuild
and expose esbuild's composed inline map when local esbuild is available.
Chromium smoke fixtures cover explicit HMR updates and provide optional
TypeScript/JSX/Vue/Svelte execution checks. CI installs the optional parser,
esbuild and framework compiler packages in a dedicated frontend acceptance job.

## WebAssembly and assets

The dev server can serve .wasm files and common image, font, audio and video
assets as binary files. The graph records imported assets as dependency leaves.
Browser instantiation remains the application's responsibility, for example
with WebAssembly.instantiateStreaming(fetch("/path/module.wasm")); a real
Chromium headless fixture verifies that a served WASM module instantiates and
executes. Limbo does not provide a WASM compiler or runtime.

## Remaining boundaries

This is an incremental local development engine, not a production bundler.
Parser-grade JavaScript scanning is available when optional Acorn tooling is
installed; the dependency-free lexical fallback remains for minimal projects.
Arbitrary computed imports, full npm condition semantics, complete PostCSS CSS
Modules behavior and automatic framework component-state preservation remain
outside the currently verified capability set. Production optimization and
code splitting belong to Phase 6.
