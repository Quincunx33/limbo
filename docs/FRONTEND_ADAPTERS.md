# Frontend compiler adapters

Limbo's frontend engine is Python-orchestrated and local-first. The core does not
install packages, download compiler binaries, or require Node.js for
browser-native JavaScript and CSS.

## Adapter contract

Implementations satisfy `CompilerAdapter`:

- `capability`: immutable `AdapterCapability` metadata.
- `detect()`: reports whether required local tools are available.
- `transform(source_path, source=None)`: returns a `TransformResult` or raises
  `AdapterError`.

A transform result contains emitted code, loader, discovered dependency strings,
an optional source map and diagnostics. The registry selects adapters by file
extension and publishes a stable, versioned JSON capability manifest.

## Current adapters

| Adapter | Extensions | Requirement | Status |
|---|---|---|---|
| `browser-native` | `.js`, `.mjs`, `.css` | None | Supported pass-through |
| `esbuild` | `.ts`, `.tsx`, `.jsx` | Local `esbuild` executable | Optional; emits inline source maps |
| `vue` | `.vue` | Node.js and local `@vue/compiler-sfc` | Optional SFC bridge |
| `svelte` | `.svelte` | Node.js and local `svelte` | Optional SFC bridge |

No adapter invokes a package manager. Install optional tools in the frontend
project yourself. The esbuild adapter is integrated into the active transform
path for TypeScript/JSX-family files. Vue and Svelte adapters use the existing
local SFC bridge. Their runtime compatibility remains experimental.

## Capability manifest

Run:

```sh
limbo frontend capabilities
limbo frontend capabilities --json
```

The JSON output has a versioned top-level `schema_version` and stable, sorted
adapter entries. Each entry describes extensions, availability, requirements,
source-map/HMR claims, maturity status and a human-readable reason. Consumers
should ignore unknown future fields and check `schema_version` before relying
on the schema.

## Dependency scanning and resolution

The dependency graph uses a standard-library lexical ESM scanner for static
imports, side-effect imports, re-exports and literal dynamic imports. It ignores
comments and ordinary quoted strings. Computed dynamic imports are reported as
diagnostics rather than guessed.

A separate CSS scanner detects quoted `@import` and `url()` references.
Relative CSS imports and assets are tracked in the graph; binary assets are
leaves and are served with MIME types inferred by Python. Bare CSS dependencies
are rejected with an actionable diagnostic. Browser-native JavaScript
side-effect imports of CSS are rewritten to a small JS module that adds a
stylesheet link. Using a link preserves relative `url()` and nested CSS
`@import` resolution. CSS imports with bindings/CSS modules are not supported.

The resolver supports local paths, tsconfig path aliases, package `exports`
subpaths/patterns and conditions, and package `imports` maps. It rejects
Node.js built-ins and package paths that escape the project root. This is not a
complete Node or bundler-compatible resolver; unsupported conditions and
syntax must not be assumed to work.

## Transform cache and development reload

Transformed source is cached persistently under `.limbo/cache/frontend`,
ignored by Git and inaccessible through the public frontend route. Cache keys
include source bytes, relative path, extension, compiler executable identity
fingerprint, local SFC package manifest fingerprints and transform options.
Writes use an atomic replace. Editing a file produces a new content key.

The dev server exposes a version endpoint and a small polling client. When a
watched module or asset changes, it performs a safe full-page reload. The
version token hashes watched file contents. This is a reload fallback, not
Hot Module Replacement: module state is not preserved and the manifest
correctly continues to report HMR as unavailable.

## WebAssembly and assets

The dev server can serve `.wasm` files and common image, font, audio and video
assets as binary files. The graph records imported assets as dependency leaves.
Browser instantiation remains the application's responsibility, for example
with `WebAssembly.instantiateStreaming(fetch("/path/module.wasm"))`; the
current phase does not provide a WASM compiler or runtime.

## Remaining boundaries

This is an incremental local development engine, not a production bundler.
Parser-grade JavaScript scanning (including template interpolation and
regex-literal edge cases), arbitrary computed imports, full npm condition
semantics, CSS modules, bare CSS imports, SFC source maps, state-preserving HMR,
production optimization/code splitting and real browser-level framework
fixtures remain outside the currently verified capability set. Production
build belongs to Phase 6.
