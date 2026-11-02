# Limbo — Universal Full-Stack Framework Roadmap

**Status:** Engineering roadmap  
**Current baseline:** 0.3.0 experimental foundation  
**Working branch:** `engineering/universal-limbo`  
**Target:** 1.0.0 stable, Python-first, local-first universal full-stack framework

This roadmap is a delivery plan, not a claim that planned features already exist. A capability is supported only after its implementation, end-to-end tests, documentation and compatibility entry are complete.

## 1. Product definition

Limbo is a Python-first framework for building and running complete web applications from one project:

- Python application backend: HTTP, routing, middleware, APIs, WebSockets, uploads, sessions and background work.
- Frontend development engine: browser-native HTML/CSS/JavaScript plus optional TypeScript, JSX/TSX and framework compilers.
- Local build system: dependency graph, compiler orchestration, cache, source maps, HMR, production assets and manifests.
- Full-stack integrations: frontend framework adapters and optional local SSR runtimes.
- Data layer: built-in SQLite path plus optional database adapters, migrations and connection policies.
- Developer workflow: project generator, CLI, configuration, testing helpers, diagnostics and documentation.
- Optional public access: Cloudflare Tunnel controlled through the official `cloudflared` executable.

The goal is one coherent developer experience—not to reimplement every compiler, browser runtime, database or framework.

## 2. Non-negotiable constraints

1. **No mandatory third-party Python runtime dependencies.** The core uses the Python standard library.
2. **No mandatory Node.js.** Browser-native JavaScript projects and Python backend projects must work without Node. Node is an optional local tool for specific compiler adapters or SSR frameworks that require it.
3. **No VPS, remote build or cloud compiler.** Build, transform, cache, HMR and orchestration happen on the developer's machine.
4. **No silent installs or downloads.** Limbo detects optional tools, explains missing capabilities and tells the developer how to install them. It does not silently install packages or download executable binaries.
5. **Cloudflare Tunnel is optional.** Limbo supervises the official `cloudflared` process; it does not implement the tunnel protocol or bundle an unverified binary.
6. **No fake framework support.** React, Vue, Svelte, Preact, Angular, Solid, Lit, Alpine, HTMX, Astro, Qwik, Next, Nuxt, SvelteKit and Remix are listed as supported only after real integration tests for the advertised capability.
7. **WASM is a capability, not a marketing label.** Serve and load valid WebAssembly modules in the browser; use WASM internally only where a real portable compute workload justifies it. Do not force the compiler engine into WASM.
8. **Security and compatibility claims must be evidence-based.** A green unit suite is not a production security certification.

## 3. Target architecture

Keep the public `from limbo import Limbo` API stable. Refactor internals incrementally with compatibility re-exports.

| Layer | Responsibility | Planned package area |
|---|---|---|
| Public API | App lifecycle, decorators, helpers | `limbo/app.py`, `limbo/__init__.py` |
| Core HTTP | Server lifecycle, parser, request/response | `limbo/core/` |
| Routing | Route groups, URL reversing, dependencies | `limbo/router.py`, `limbo/routing/` |
| Middleware | Global, group and route middleware; error hooks | `limbo/middleware/` |
| Static/assets | MIME, validators, ranges, compression, safe paths | `limbo/static/` |
| Frontend engine | Resolver, graph, compiler adapters, cache, HMR | `limbo/frontend/` |
| Build engine | Build graph, manifest, output validation | `limbo/build/` |
| SSR adapters | Local process lifecycle and framework bridges | `limbo/runtime/` |
| Data | SQLite core path, optional adapters and migrations | `limbo/database/` |
| Security | Sessions, CSRF, CORS, trusted hosts, rate limits | `limbo/security/` |
| Realtime | WebSocket protocol and application policies | `limbo/websocket/` |
| Background work | Local task execution, cancellation, shutdown | `limbo/tasks/` |
| Tunnel | cloudflared detection and process lifecycle | `limbo/tunnel/` |
| Config | TOML loading, schema validation, env overrides | `limbo/config/` |
| CLI/testing | Commands, test client, fixtures, diagnostics | `limbo/cli/`, `limbo/testing/` |

Avoid a large directory-only rewrite. Move one responsibility at a time, keep imports compatible, and test before deleting old paths.

### Frontend compiler design

Python owns orchestration and the stable adapter contract. Actual transforms are delegated to mature, locally installed compiler tools where required:

- Browser-native JS, CSS, HTML and Web Components: no compiler required.
- TypeScript: optional local TypeScript compiler or a tested esbuild adapter.
- JSX/TSX: an explicitly selected, locally available transform plus the matching framework runtime.
- Vue/Svelte and other SFCs: their official local compiler packages through adapters.
- CSS, assets and WASM: explicit resolver/loader rules and MIME handling.
- Unsupported syntax or Node-only imports: clear build-time diagnostic, never silently served as if valid.

The compiler contract must expose capability detection, transform inputs/outputs, dependency discovery, diagnostics, source maps and cache keys. Framework runtime packages remain project dependencies, not hidden Limbo dependencies.

## 4. Delivery phases

### Phase 0 — Baseline, API contract and engineering gates
**Goal:** Establish a trustworthy starting point.

- Freeze a documented public API baseline and define supported Python versions and OS matrix.
- Keep `engineering/universal-limbo` isolated from `main`.
- Add CI for Python versions and then Linux, Windows and macOS.
- Add capability matrix: implemented, experimental, planned, unsupported.
- Add security policy, test conventions, benchmark baselines and release checklist.

**Exit gate:** CI green; documented baseline; every existing feature has a test or an explicit known limitation.

### Phase 1 — Core HTTP and routing correctness
**Goal:** Make the Python server predictable before adding more surface area.

- Request-line/header parsing, framing ambiguity rejection, bounded body/header resources and absolute request deadlines.
- Host authority validation and optional trusted-host allowlist.
- Keep-alive lifecycle, connection shutdown, concurrent clients and response framing.
- HEAD/OPTIONS semantics, error responses and malformed-wire tests.
- Route groups, nested prefixes, names, URL reversal, priority/conflict rules and introspection.
- Global/group/route middleware and custom status/exception error handlers.
- Interoperability tests against standard HTTP clients and proxy-style request cases.

**Already present on the engineering branch:** route groups/names/reversal/introspection; initial parser hardening; absolute request deadline; Host validation and allowlist; response framing checks; route middleware and custom error handlers; concurrent socket tests.

**Progress (2026-11-02):** Added a standard-library `http.client` interoperability test and active accepted-socket shutdown so idle keep-alive workers are unblocked immediately. Added a sustained idle-client shutdown regression test. These checks cover one standard HTTP client path and a bounded 24-connection shutdown scenario; they are not an exhaustive proxy matrix or a sustained throughput/load benchmark.

**Phase 1 implementation status: COMPLETE (2026-11-02).** The supported core HTTP and routing behavior is covered by the standard-library test suite and CI matrix. Coverage includes malformed request lines and headers, framing ambiguity, bounded resources, absolute deadlines, Host validation and allowlisting, standard HTTP client interoperability, proxy-forwarding metadata preservation as untrusted input, HTTP/1.0 response versioning and close-delimited streaming, HEAD fallback/body suppression, route-level OPTIONS and server-wide `OPTIONS *`, concurrent clients, response framing and prompt shutdown of idle keep-alive connections.

**Assurance boundary:** this is an engineering phase completion, not production certification. No independent external protocol audit or third-party security assessment has been performed. Run deployment-specific proxy and load tests before exposing the server to untrusted public traffic.

**Exit gate:** supported request/response behavior is specified and tested, including malformed input, concurrency and shutdown.

### Phase 2 — Static files, responses and middleware completeness
**Goal:** Make ordinary web delivery complete and safe.

**Status: COMPLETE (2026-11-02).** Static responses stream from open file descriptors and expose an explicit Response.close() lifecycle for abandoned streams. The default symlink-deny path uses directory-relative no-follow opens where supported; portable fallback and within-root symlink race limitations are documented. Directory indexes are opt-in. Conditional GET precedence, weak ETags, Last-Modified, single byte ranges, 416, If-Range, HEAD metadata, MIME/cache headers, redirects, cookies, Vary and Accept negotiation are implemented. Unsupported units, malformed syntax and multiple ranges are ignored; valid unsatisfiable ranges return 416 and oversized end offsets are clamped. Optional SPA fallback is restricted to missing paths whose request accepts HTML; non-HTML API/asset requests remain 404. Built-in CORS, security headers, request IDs, access logging, bounded process-local token-bucket rate limiting and negotiated gzip are implemented, with integration and failure-path tests.

**Verification:** Full standard-library suite passed on Linux with Python 3.10, 3.11, 3.12 and 3.13. Phase 2 static/middleware smoke suites passed on Ubuntu, Windows and macOS. CI run: https://github.com/Quincunx33/limbo/actions/runs/41465088436.

**Assurance boundary:** this is implementation and CI completion, not an independent security audit or production certification. Descriptor-relative no-follow protection depends on OS support; fallback and within-root mode have weaker race guarantees. The in-memory rate limiter is process-local, not distributed. Configure deployment-specific proxy, CSP, HSTS, CORS and cache policies before public exposure.

- Response cookie/header/redirect/Vary helpers and Accept negotiation.
- Conditional GET precedence and bodyless 304 responses.
- Single byte ranges; unsupported/malformed/multiple ranges ignored; unsatisfiable ranges return 416.
- MIME/cache policy, optional gzip, opt-in directory indexes, SPA fallback and symlink policy.
- Directory-relative no-follow traversal where supported; documented platform fallbacks.
- CORS, security headers, request IDs, access logging, rate limiting and gzip middleware.
- Adversarial tests for traversal, symlinks, validators, range boundaries, streaming and middleware failures.

**Exit gate: PASSED.**

### Phase 3 — Backend application essentials
**Goal:** Make Limbo useful for real multi-user applications.

**Status: IN PROGRESS (2026-11-02).** Phase 3 implementation begins with bounded server-side session storage and session middleware. The session cookie contains only a high-entropy opaque identifier; session data remains server-side. Session IDs can be explicitly rotated after authentication/privilege changes and revoked on logout. This first vertical slice is process-local and is not a distributed or persistent session backend.

**Acceptance work remaining:** request accessors and validation responses; bounded upload streaming/persistence lifecycle; CSRF tokens and authentication/authorization extension points; WebSocket Origin and lifecycle policies; bounded background tasks with cancellation and shutdown; structured logging and app lifecycle hooks; integration/failure tests and examples.


- Request API consistency: query, path, headers, cookies, JSON, forms, multipart and validation errors.
- Upload lifecycle: size limits, safe filenames, streaming to disk, cleanup and non-overwrite policy.
- Session lifecycle, cookie policy, rotation/expiry and clear signed-versus-encrypted semantics.
- CSRF tokens and validation hooks; safe CORS defaults; authentication/authorization extension points.
- WebSocket protocol edge cases, Origin allowlists, size/time limits and lifecycle tests.
- Local background task primitives with bounded concurrency, cancellation, error reporting and graceful shutdown.
- Structured logging and application lifecycle hooks.

**Exit gate:** end-to-end examples cover login/session, protected routes, file upload, WebSocket and background work without claiming a full job queue or distributed worker system.

### Phase 4 — Configuration and CLI experience
**Goal:** Make projects reproducible and easy to operate.

- Implement and validate `limbo.toml` with typed sections for app, server, frontend, build, static, security, database and tunnel.
- Define precedence: CLI flags > environment variables > config file > safe defaults.
- Helpful configuration errors with file/line/key context; never print secrets.
- Complete `create`, `dev`, `start`, `build`, `preview`, `doctor`, `info`, `dependencies`, `add` and `remove` semantics.
- `add/remove` edit declared project metadata only; they do not silently install packages.
- Doctor reports exact tool versions, missing optional tools and which capabilities are unavailable.
- Generate small templates: Python API, static site, browser-native frontend and supported framework starter.

**Exit gate:** generated projects work from a clean environment with documented commands and no hidden setup.

### Phase 5 — Frontend development engine
**Goal:** Turn current frontend preview into a reliable local compiler/dev pipeline.

1. Specify compiler adapter API and machine-readable capability manifest.
2. Upgrade dependency discovery beyond regex-only literal imports; handle static ESM, re-exports, CSS imports, assets and supported dynamic imports.
3. Implement package resolution rules: package exports/imports, conditions, aliases, extensions, index files and deterministic errors.
4. Add adapters for browser-native modules, TypeScript, JSX/TSX and CSS/assets.
5. Add official local compiler adapters for Vue and Svelte; then evaluate Preact, Solid, Lit, Angular and others independently.
6. Build persistent content-addressed cache keyed by source, compiler version, options and dependency state.
7. Add incremental invalidation and dependency graph updates.
8. Add source-map generation/serving and useful compiler diagnostics.
9. Implement HMR protocol and client runtime with safe full-reload fallback.
10. Handle WASM assets, MIME types, import/instantiate patterns and browser compatibility checks.

**Exit gate:** repeatable real sample apps pass clean-start, edit/invalidate, compiler-error, cache-hit and browser smoke tests. Unsupported features are rejected with actionable diagnostics.

### Phase 6 — Local production build
**Goal:** Produce deployable frontend assets without a remote service.

- Deterministic build graph and clean build directory.
- Hashed assets, manifest, static HTML output and configurable base path.
- Minification, tree-shaking and code splitting only through compiler adapters that genuinely support them.
- CSS extraction/processing and asset URL rewriting.
- Source maps configurable for development/production.
- Build reports, warnings, size summaries and reproducibility checks.
- Build output served by Limbo's static layer and usable behind an ordinary reverse proxy.

**Exit gate:** clean builds are reproducible; generated manifest resolves every referenced asset; sample apps pass production smoke tests.

### Phase 7 — Framework adapters and local SSR
**Goal:** Support frameworks in a verified, honest compatibility matrix.

Adapter order:
1. Browser-native HTML/CSS/JS/Web Components.
2. TypeScript and JSX/TSX transform pipeline.
3. React + Preact.
4. Vue + Svelte.
5. Solid + Lit + Alpine + HTMX.
6. Angular after its compiler/build integration is proven.
7. Astro and Qwik after adapter and runtime requirements are proven.
8. Next, Nuxt, SvelteKit and Remix only as separate local SSR integrations with documented constraints.

For each adapter, implement and test:
- Tool detection and version compatibility.
- Transform/build invocation and diagnostics.
- Runtime dependency resolution.
- Dev server/HMR behavior where supported.
- Production build output.
- SSR process startup, readiness, request proxy/bridge, crash handling and graceful shutdown where applicable.

SSR frameworks may require local Node.js. This is an optional integration requirement, not a core Limbo requirement. Limbo must not claim that Python itself executes Node-based SSR.

**Exit gate:** each framework has its own tested capability row. No blanket “supports all frameworks” claim.

### Phase 8 — Database and data integrations
**Goal:** Provide a clear data layer without making the core heavy.

- Stabilize built-in SQLite connection ownership, transactions, rollback and concurrency semantics.
- Add schema migration interface and migration history for SQLite.
- Define optional adapter contract for PostgreSQL, MySQL/MariaDB and other databases only when tested.
- Pooling belongs in adapters with explicit lifecycle and limits; no fake pool abstraction over a single SQLite connection.
- Parameterized queries, transaction boundaries and safe error mapping.
- Optional model/query layer only after adapter contracts are stable.
- Integration tests use real temporary SQLite databases; external DB tests run in service-backed CI jobs when available.

**Exit gate:** migration/transaction behavior is documented and each database adapter passes its own integration suite.

### Phase 9 — Cloudflare Tunnel (optional)
**Goal:** One-command public preview of a local Limbo server.

- Detect an existing official `cloudflared` executable and inspect its version.
- Implement `limbo dev --public`: start local server, launch Quick Tunnel, detect the assigned HTTPS URL and show live status.
- Monitor tunnel process exit, log bounded output, surface actionable errors and stop child processes on Ctrl+C or app shutdown.
- Support Named Tunnel only through documented `cloudflared` configuration/auth flows; never expose credentials in logs.
- Require explicit confirmation for destructive account/tunnel operations.
- Explain public exposure risks and run configurable preflight checks.
- No automatic binary download, no tunnel-protocol reimplementation, no cloud build and no VPS dependency.

**Exit gate:** Quick Tunnel works with no account/domain; Named Tunnel works only with valid user-provided setup; process cleanup and secret-redaction tests pass.

### Phase 10 — Testing, performance, security and 1.0 release
**Goal:** Prove the product is stable enough to call 1.0.

- Unit, integration, malformed-wire, concurrency, lifecycle and regression suites.
- Linux/Windows/macOS CI and supported Python-version matrix.
- Real browser smoke tests for each frontend adapter.
- Benchmarks for routing, static files, request concurrency, compiler cache and build time.
- Threat model and independent review of HTTP parsing, path handling, sessions, CSRF, CORS, uploads, WebSockets and tunnel exposure.
- Fuzz/property tests for parsers and resolver edge cases.
- Documentation: quick start, architecture, API reference, tutorials, deployment, security, troubleshooting and migration guides.
- Versioned compatibility matrix and deprecation policy.
- Release artifacts, checksums, changelog and reproducible package install test.

**1.0 exit gate:** no critical known security issue; supported matrix fully green; docs match behavior; examples build from clean environments; release checklist signed off.

## 5. Framework capability matrix policy

The following is a target evaluation list, not a current support claim.

| Technology | Planned path | Support is declared only when |
|---|---|---|
| HTML/CSS/JS/ESM | Native local dev/build | Browser smoke + production build |
| Web Components | Native browser runtime | Fixture and HMR/reload tests |
| TypeScript | Local compiler adapter | Type diagnostics + emitted module tests |
| JSX/TSX | Explicit transform adapter | Transform + runtime + app smoke tests |
| React / Preact | JSX transform + browser ESM runtime | Real component app works in dev and build |
| Vue / Svelte | Official local SFC compilers | SFC, styles, imports and HMR/build tests |
| Solid / Lit / Alpine / HTMX | Individual adapters or native integration | Per-technology fixture tests |
| Angular | Official compiler/build integration | Real generated app builds and runs |
| Astro / Qwik | Dedicated build/runtime adapter | Real starter app and output tests |
| Next / Nuxt / SvelteKit / Remix | Local SSR process adapter | SSR lifecycle, routing and production tests |
| WASM | Asset/module support + browser instantiation | Real module loads and executes in browser |

## 6. Release ladder

| Release | Intended deliverable |
|---|---|
| 0.3.x | Existing experimental foundation and correctness fixes |
| 0.4 | HTTP/static/middleware completeness |
| 0.5 | Backend essentials, security policies and config/CLI |
| 0.6 | Frontend resolver/compiler adapter foundation |
| 0.7 | Cache, source maps, HMR and local production build |
| 0.8 | Verified framework adapters and selected local SSR integrations |
| 0.9 | Database integrations, tunnel lifecycle, cross-platform hardening |
| 1.0 | Security-reviewed, documented stable release with explicit support matrix |

Release numbers are planning targets; a release is not cut just because the calendar or phase number says so.

## 7. Working method for every phase

For each feature, follow this exact loop:

1. Inspect current source, public API and existing tests on the working branch.
2. Write acceptance criteria and identify security/compatibility risks.
3. Implement the smallest coherent vertical slice; avoid broad speculative rewrites.
4. Add unit tests, real integration tests and failure/adversarial tests.
5. Run the full test suite and relevant OS/Python CI.
6. Test a real example project where the feature crosses process/browser/compiler boundaries.
7. Update README, capability matrix and implementation ledger.
8. Commit to `engineering/universal-limbo`; never change `main` or merge without explicit approval.
9. Report commit, changed files, test evidence and remaining limitations.

A feature is not “done” because code exists. It is done only when the documented use case works end-to-end.

## 8. Current next actions

Work in this order; do not jump to framework adapters or tunnel before the foundations are reliable:

1. Close Phase 1: HTTP interoperability, sustained concurrent load, shutdown and protocol/security review.
2. Complete Phase 2: static response semantics, safe paths, compression and middleware utilities.
3. Complete Phase 3: request/upload/session/CSRF/WebSocket/background-task lifecycle.
4. Implement TOML configuration and finish CLI semantics.
5. Design the compiler adapter contract before adding more frontend frameworks.
6. Build the resolver/graph/cache/source-map/HMR pipeline.
7. Implement local production build.
8. Add and verify framework adapters one at a time.
9. Stabilize database adapters and optional Cloudflare Tunnel.
10. Run the 1.0 security, cross-platform and release gates.

## 9. Explicit non-goals

- A VPS or remote build service.
- Mandatory Node.js or mandatory third-party Python packages.
- Automatic installation of compilers, framework packages or cloudflared.
- Reimplementing Cloudflare's tunnel protocol.
- Claiming every framework works through a generic JSX transform.
- Treating every npm package as browser-compatible.
- Treating a successful unit test run as a security certification.
- Rewriting all of Limbo at once or breaking the public API without a migration path.
