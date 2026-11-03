# Limbo — Local Frontend Runtime & Framework Integration Roadmap

**Status:** Planned engineering work. This document does not mark any planned capability complete.
**Scope:** Local npm runtime packages, compiler adapters, framework integrations and local SSR process supervision.
**Scheduling:** No deadlines. Completion is determined by verified acceptance gates.

This document refines Phases 5 and 7 of ROADMAP.md. Existing phase status and capability claims remain unchanged until the tests described here pass.

## 1. Target outcome

Limbo should run frontend projects using runtime and compiler dependencies installed in that project's own node_modules. Limbo resolves and serves browser-compatible local modules, transforms supported source files with locally installed compilers, and supervises framework-owned servers where a framework requires its own runtime.

Example developer workflow:

    npm install react react-dom preact solid-js lit
    npm install --save-dev esbuild
    limbo dev

The developer runs package-manager commands. Limbo must not invoke npm, download packages, fetch remote compiler code or silently install missing dependencies.

Example project layout:

    project/
    ├── node_modules/
    │   ├── react/
    │   ├── react-dom/
    │   ├── preact/
    │   ├── solid-js/
    │   ├── lit/
    │   └── esbuild/
    ├── frontend/
    ├── package.json
    ├── package-lock.json
    └── limbo.toml

This is illustrative; projects install only the packages required by their selected framework.

## 2. Non-negotiable rules

1. **No CDN:** Never emit or depend on unpkg.com, esm.sh, cdn.jsdelivr.net or another remote runtime/compiler host.
2. **Local package ownership:** Runtime and compiler packages are project dependencies in node_modules, not hidden Limbo dependencies.
3. **No automatic installation:** Detect optional tools and report the missing package plus an install command. Never run a package manager automatically.
4. **No remote fallback:** Failed local resolution produces an actionable diagnostic, never a network retry.
5. **Offline after provisioning:** Once required packages are installed locally, dev, transform, runtime serving and build must work without network access. Initial package installation may need network access unless packages are already cached.
6. **Project-root containment:** Resolution and serving must not escape permitted project/package roots through traversal, symlinks or malformed metadata.
7. **Honest module compatibility:** A package existing in node_modules does not prove its entry point is browser-compatible ESM. CommonJS, Node built-ins, native addons and server-only exports require a verified local transform or a clear rejection.
8. **Stable local URLs:** Target a Limbo-owned namespace such as /_limbo_modules/<package-path>. Audit the existing /_pkgfile/ route and provide compatibility or a deliberate migration; do not silently break existing projects.
9. **Reproducibility:** Respect lockfiles, package versions and tested export conditions. Resolution must not change based on network availability.
10. **Production parity:** Production output contains local assets and resolved dependencies, or documents a framework-owned output boundary. No accidental CDN references.
11. **Safe process execution:** Invoke local compiler/framework executables with argument arrays, controlled working directories and bounded output. Do not interpolate untrusted values into shell commands.

## 3. Target architecture

    Frontend source
          |
          v
    Limbo resolver
          |
          +--> project source files
          +--> package metadata and lockfile
          +--> local node_modules
                    |
                    +--> browser ESM runtime packages
                    +--> local compiler packages
                    +--> package export conditions
          |
          v
    Dependency graph + import-map generator
          |
          +--> Limbo-local module URLs
          +--> compiler adapters when required
          +--> actionable diagnostics
          |
          v
    Dev server / HMR / local production build
          |
          v
        Browser

Python remains responsible for orchestration, configuration, local HTTP serving, diagnostics, dependency graph lifecycle and process supervision. Mature compiler tools are invoked locally through adapters. Limbo does not reimplement framework runtimes or Node.js.

### Runtime module serving

The resolver must:

- Resolve bare package specifiers against the nearest applicable local package installation.
- Read package metadata and support a tested subset of exports, imports, browser, module and type fields.
- Select browser-compatible ESM entries with documented condition precedence.
- Resolve package subpaths and transitive imports without escaping the project boundary.
- Generate deterministic import maps whose targets are Limbo-local URLs.
- Serve resolved files with correct MIME types and safe cache/validation behavior.
- Detect cycles, missing transitive dependencies and unsupported entry points.
- Reject Node built-ins, native modules, server-only exports and unresolved CommonJS with precise diagnostics.
- Never rewrite unsupported modules to remote URLs.

Illustrative generated import map:

    {
      "imports": {
        "react": "/_limbo_modules/react/index.js",
        "react-dom/client": "/_limbo_modules/react-dom/client.js",
        "lit": "/_limbo_modules/lit/index.js"
      }
    }

The generated paths must come from the actual installed package versions and resolved entries. They are examples, not assumptions about every package release.

### Compiler execution

- Detect compilers from the current project, not Limbo's global installation.
- Execute only locally installed, explicitly detected tools.
- Capture exit status, diagnostics, source maps and dependency information.
- Include compiler identity/version and transform options in cache keys.
- Fail clearly when a compiler is missing or a transform fails.
- Never report success for invalid or incomplete compiler output.

## 4. Phase 5 refinement — Local runtime and compiler engine

### 5A — Runtime package resolver hardening

**Goal:** Make local npm package resolution predictable before expanding framework claims.

- Audit the current resolver and /_pkgfile/ serving route.
- Define the canonical /_limbo_modules/ namespace and compatibility behavior.
- Implement tested browser ESM entry selection.
- Cover package subpaths, scoped packages, nested dependencies, duplicate versions and nearest-package resolution.
- Add adversarial tests for traversal, symlink escapes, malformed manifests and cyclic dependencies.
- Add explicit diagnostics for CommonJS, Node built-ins and server-only entry points.
- Make import-map generation deterministic and local-only.

**Acceptance gate:** Temporary node_modules fixtures resolve direct and transitive dependencies; generated maps contain no external URL; browser tests execute a local ESM graph with network access blocked; unsupported formats fail clearly; containment tests pass.

### 5B — Local ESM runtime serving

**Goal:** Serve framework runtime packages without a CDN.

- Serve resolved package files from the local module namespace.
- Resolve their package-relative and transitive imports.
- Handle MIME types, URL encoding, query strings and safe cache headers.
- Prevent access to files outside the permitted resolver policy.
- Test nested and scoped packages.
- Verify development invalidation when a runtime package changes.

**Acceptance gate:** Real browser tests execute a multi-package ESM graph using only Limbo-local URLs. Network interception confirms no CDN request.

### 5C — Local JSX/TSX compiler

**Goal:** Make JSX and TypeScript work with a project-local compiler.

- Detect local esbuild and report its version.
- Support explicit JSX/TSX loader and JSX runtime configuration.
- Resolve React automatic/classic JSX runtime imports locally.
- Resolve Preact and Solid JSX runtime imports according to project configuration.
- Preserve source maps and actionable source locations.
- Track compiler-discovered dependencies.
- Cache by source content, compiler identity/version and transform options.
- Fail clearly when esbuild is missing or options are unsupported.

**Acceptance gate:** React, Preact and Solid JSX fixtures compile with local esbuild, load their matching local runtime and execute in a real browser. Missing compiler/runtime tests produce actionable diagnostics.

### 5D — React + ReactDOM

- Verify installed React and ReactDOM versions.
- Resolve browser ESM runtime entries and transitive imports locally.
- Support react, react-dom/client and the configured JSX runtime.
- Keep dev and production runtime resolution compatible.
- Test hooks, state updates, events, composition and unmount behavior.
- Test refresh/reload behavior. Do not claim React Fast Refresh until separately implemented and verified.
- Reject unsupported package formats; JSX transformation alone is not React integration.

**Acceptance gate:** A clean React starter works in dev and production preview after local dependency installation, with no CDN requests.

### 5E — Preact

- Resolve Preact core and JSX runtime locally.
- Test optional React-compat aliases only when explicitly configured.
- Verify rendering, hooks, events, state updates and package subpaths.
- Verify production output and reload behavior.

**Acceptance gate:** A real Preact starter passes browser tests in dev and production preview without remote dependencies.

### 5F — Solid

- Verify local Solid compiler/runtime requirements and version compatibility.
- Configure the correct JSX transform; do not treat Solid as ordinary React JSX.
- Resolve Solid exports and transitive ESM dependencies locally.
- Test signals, effects, component updates and disposal.
- Verify production output and document compiler limitations.

**Acceptance gate:** A real Solid starter passes dev and production browser tests without remote dependencies.

### 5G — Lit

- Resolve Lit package exports and transitive dependencies locally.
- Support TypeScript/decorator transforms only when explicitly configured and locally available.
- Test custom element registration, reactive properties, templates, events and updates.
- Verify package CSS/assets and production output.

**Acceptance gate:** A real Lit starter runs using local package URLs and passes production preview tests.

### 5H — Cross-runtime and offline verification

- Maintain one real fixture per runtime: React, Preact, Solid and Lit.
- Test multiple runtimes in one project without alias collisions.
- Test nested package versions where supported.
- Verify lockfile reproducibility and deterministic import maps.
- Run dev/build/preview with outbound network blocked after dependencies are installed.
- Scan generated HTML, JS, CSS, source maps and manifests for CDN URLs.
- Verify cache invalidation after package version or entry changes.
- Update the capability manifest only from verified evidence.

**Phase 5 refined exit gate:** Every runtime marked supported has a real browser fixture, local dependency graph, production preview test, offline test and documented compatibility range. Everything else remains experimental or unsupported.

## 5. Compiler-assisted framework integrations

These require their own compiler adapters. Serving JavaScript does not make a framework buildless.

### Vue

- Use local vue and @vue/compiler-sfc packages.
- Compile SFC script, template and style blocks through the official local compiler.
- Resolve Vue runtime imports locally.
- Verify scoped styles, asset URLs, source maps, HMR and production output.

**Acceptance gate:** A real Vue starter passes dev, browser, offline and production-preview tests.

### Svelte

- Use the locally installed Svelte compiler.
- Verify compiler-version-specific output and runtime dependencies.
- Test SFC/script/style compilation, source maps, assets, HMR and production output.
- Add component-state HMR only after state preservation is demonstrated by browser tests.

**Acceptance gate:** A real Svelte starter passes dev, browser, offline and production-preview tests. State-preserving HMR is a separate claim.

### Angular

- Integrate the official local Angular compiler/build toolchain, not a generic JSX approximation.
- Detect supported CLI/compiler versions and local dependencies.
- Treat decorators, templates, dependency injection, styles, assets and generated chunks as compiler-owned behavior.
- Invoke the official local build pipeline through a documented adapter.
- Diagnose unsupported builders/plugins/configuration explicitly.

**Acceptance gate:** A generated Angular application builds and runs locally, with verified production output and no CDN dependency. Supported versions and limitations are documented.

## 6. Framework-owned local server integrations

Next.js, Nuxt, SvelteKit, Remix, Astro and Qwik have framework-specific build, routing, SSR and deployment semantics. Supervise their own local processes instead of reimplementing them as generic browser runtimes.

For each integration:

- Detect the local framework package and supported version.
- Require developer-installed dependencies.
- Validate command, working directory and port.
- Start the child process with a controlled environment.
- Wait for readiness using bounded health checks.
- Proxy requests without corrupting streaming, headers, WebSockets or upgrades where supported.
- Forward bounded logs and actionable failures.
- Detect early exit and report the exit code.
- Stop child processes and descendants on shutdown.
- Never leak environment secrets in logs.
- Distinguish framework dev mode from production start mode.
- Verify build output and production start separately.
- Do not equate a child server starting with a fully supported framework integration.

| Framework | Integration path | Minimum evidence |
|---|---|---|
| Next.js | Local framework server adapter | Declared router scope, SSR, assets, routing, readiness and shutdown tests |
| Nuxt | Local Nuxt server adapter | Nuxt app, SSR, assets, runtime config and production start tests |
| SvelteKit | Local SvelteKit server adapter | Adapter-specific build, SSR, routing and production start tests |
| Remix | Local Remix server adapter | Route/data loading, SSR, streaming where supported and production start tests |
| Astro | Local Astro dev/build/preview adapter | Static/SSR mode, integrations and generated output tests |
| Qwik | Local Qwik compiler/server adapter | Optimizer/build behavior, routing, resumability and production output tests |

A generic command or proxy smoke test is not sufficient to mark all six supported.

## 7. CLI and configuration target

Developer workflow:

    npm install
    npm install --save-dev esbuild
    limbo doctor
    limbo frontend capabilities
    limbo dev
    limbo build
    limbo preview

Illustrative configuration shape (not an instruction to add unimplemented keys):

    [frontend]
    root = "frontend"
    runtime_modules = "local"
    module_url_prefix = "/_limbo_modules"
    allow_remote_imports = false

    [frontend.jsx]
    compiler = "esbuild"
    runtime = "automatic"

    [frontend.framework]
    adapter = "react"

Do not add configuration keys until their behavior, defaults, validation and compatibility tests exist. Local-only resolution is the default; remote imports must never be an implicit fallback.

## 8. Cross-cutting test strategy

Every runtime/compiler/framework adapter requires:

1. Unit tests for detection, version parsing, resolution and diagnostics.
2. Resolver tests for direct, transitive, scoped and nested package imports.
3. Security tests for traversal, symlink escapes and malformed metadata.
4. Compiler tests for valid/invalid source, diagnostics and source maps.
5. Browser tests against a real generated starter.
6. Production build and preview tests.
7. Offline tests with network blocked after local dependency installation.
8. Cache invalidation and lockfile/version regression tests.
9. Negative tests proving unsupported syntax and package formats fail closed.
10. Documentation and capability-manifest evidence.

A framework is not supported merely because its package installs, compiler exits successfully or development server starts. The complete documented application workflow must pass.

## 9. Capability status policy

- **supported:** Real browser and production workflow tests pass for the declared scope.
- **optional:** A named local tool/package is required and the relevant tests pass.
- **experimental:** A real partial workflow has been exercised; limitations are explicit.
- **unsupported:** No verified end-to-end workflow exists.
- **process-supervised:** Limbo manages a child process; this alone does not imply framework integration.

Do not promote React, Preact, Solid, Lit, Vue, Svelte, Angular, Next, Nuxt, SvelteKit, Remix, Astro or Qwik until its own acceptance gate is met.

## 10. Delivery order

1. Audit and harden the existing package resolver and local module-serving route.
2. Establish the canonical local module URL namespace and compatibility behavior.
3. Complete local ESM runtime serving and import-map generation.
4. Complete local esbuild JSX/TSX integration.
5. Deliver React runtime integration.
6. Deliver Preact runtime integration.
7. Deliver Solid runtime integration.
8. Deliver Lit runtime integration.
9. Pass cross-runtime offline and production gates.
10. Harden Vue and Svelte compiler adapters.
11. Implement Angular's official local compiler/build integration.
12. Mature the process supervisor and lifecycle/proxy contract.
13. Integrate Next.js and Nuxt as separate local server adapters.
14. Integrate SvelteKit and Remix as separate local server adapters.
15. Integrate Astro and Qwik through framework-specific build/server adapters.
16. Update capability matrix, examples, docs and release checklist from verified evidence.

This is an engineering dependency order, not a calendar schedule. Later work may be explored, but it cannot be marked complete by inference from an earlier item.

## 11. Completion reporting

For every completed item, report:

- Commit and changed files.
- Exact local package/compiler versions tested.
- Reproduction commands.
- Unit, integration and browser test results.
- Whether outbound network was blocked during the offline test.
- Production build/preview evidence.
- Known limitations and capability-matrix changes.

Never mark a phase complete while a required acceptance gate is failing or untested.
