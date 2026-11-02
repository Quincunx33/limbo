# Frontend compiler adapters

Limbo's frontend engine is Python-orchestrated and local-first. The core does not
install packages, download compiler binaries, or require Node.js for browser-native
JavaScript and CSS.

## Adapter contract

Implementations satisfy `CompilerAdapter`:

- `capability`: immutable `AdapterCapability` metadata.
- `detect()`: reports whether the adapter's required local tools are available.
- `transform(source_path, source=None)`: returns a `TransformResult` or raises
  `AdapterError`.

A transform result contains emitted code, loader, discovered dependency strings,
an optional source map, and diagnostics. The adapter contract deliberately keeps
dependency discovery and source maps explicit so later resolver/graph work does
not have to infer compiler behavior from arbitrary return strings.

## Current adapters

| Adapter | Extensions | Requirement | Status |
|---|---|---|---|
| `browser-native` | `.js`, `.mjs`, `.css` | None | Supported pass-through |
| `esbuild` | `.ts`, `.tsx`, `.jsx` | Local `esbuild` executable | Optional |

The esbuild adapter never invokes a package manager. Install esbuild in the
frontend project yourself if you want this optional transform. Its source-map
capability is not advertised until the adapter returns and serves a verified map.

Vue and Svelte are currently handled by the existing SFC bridge, not registered
as adapters in this first manifest. Their presence must not be inferred from the
two adapter rows above.

## Capability manifest

Run:

```sh
limbo frontend capabilities
limbo frontend capabilities --json
```

The JSON output has a versioned top-level `schema_version` and stable, sorted
adapter entries. Each entry describes extensions, availability, requirements,
source-map/HMR claims, maturity status, and a human-readable reason. Consumers
should ignore unknown future fields and check `schema_version` before relying
on the schema.

## Transform cache and development reload\n\nTransformed source is cached persistently under `.limbo/cache/frontend`, ignored by Git and inaccessible through the public frontend route. Cache keys include source bytes, relative path, extension, compiler executable identity/version fingerprint, local SFC package manifest fingerprints and transform options; writes use an atomic replace. Editing a file naturally produces a new key, so stale output is not reused.\n\nThe dev server exposes a version endpoint and a small polling client. When a watched module or asset changes, it performs a safe full-page reload. This is a reload fallback, not Hot Module Replacement: module state is not preserved and the manifest correctly continues to report HMR as unavailable. Watched files are content-hashed for the reload version token.\n\n## Non-goals of this first slice

This contract does not claim that all package exports, CSS imports, dynamic
imports, source maps, HMR, or production builds are implemented. Those remain
separate Phase 5/6 acceptance items. The existing `ModuleResolver` and
`DependencyGraph` are still the active dev-server path until the next
integration steps replace their regex-only dependency scanner with a structured
scanner and verified graph updates.
