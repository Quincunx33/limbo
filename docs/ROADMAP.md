# Limbo — Universal Full-Stack Framework Roadmap

**Status:** Engineering roadmap  
**Current baseline:** 0.9.0 engineering release  
**Working branch:** `main`
**Target:** 1.0.0 remains unreleased until the Phase 10 review gates are actually signed off

**Engineering status (2026-11-04):** Phases 1–4 are complete. Phase 5 has a verified local pipeline and browser smoke coverage, with PostCSS-complete CSS Modules and Svelte state HMR still explicitly out of scope. Phases 6–9 have tested implementations: browser-native production builds, an honest framework matrix plus a local process supervisor, SQLite migrations, and optional `cloudflared` supervision. The private repository's final GitHub Actions run is green on Linux and Windows across Python 3.10–3.13, including the optional frontend-tool/browser job. The hosted macOS runner repeatedly hung and is intentionally excluded from the workflow; no macOS support claim is made. Independent security review is still outstanding, so this is not 1.0. See [STATUS.md](STATUS.md).

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
- Add CI for supported Python versions and deterministic hosted platforms; keep unavailable or unstable hosted runners out of the support claim.
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

**Verification:** Full standard-library suite passed on Linux with Python 3.10, 3.11, 3.12 and 3.13. Phase 2 static/middleware smoke suites passed on Ubuntu and Windows. The final CI run is [Tests #41719193704](https://github.com/taaissu/limbo-framework/actions/runs/41719193704).

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

**Status: ENGINEERING-COMPLETE (2026-11-03).** Implemented: bounded process-local server-side sessions with opaque HttpOnly cookies, explicit ID rotation/revocation and expiry; a structural `SessionBackend` contract for application-supplied persistent/shared adapters; signed double-submit CSRF middleware; request convenience accessors and JSON 400/422 validation; configurable application-level request/file/count upload quotas; safe filenames and exclusive chunked persistence; exact per-route WebSocket Origin allowlists plus configurable message-size and idle-timeout policies; bounded in-process background tasks with cancellation/error reporting; synchronous startup/shutdown hooks and task cleanup. Authentication/authorization extension helpers (`authenticate`/`authorize`) and a runnable combined backend example are documented. The default session store remains process-local and non-persistent. Multipart HTTP requests are parsed incrementally from Content-Length and chunked streams; uploaded file payloads are written to private request-scoped temporary files, not retained in the complete request body. Dispatch removes temporary files in a `finally` path after the handler returns or fails. `UploadFile` temporary storage is request-scoped; applications must call `save()` inside the handler to persist files. Directly constructed `Request` objects retain a bounded in-memory compatibility path.

**Verification:** 51 focused Phase 3 tests and the complete 169-test standard-library suite pass on Linux with the current Python runtime. Coverage includes Content-Length and chunked streaming, bounded reads, large-file spooling, cleanup on success/failure, upload limits, sessions, expiry/concurrency, CSRF, authentication/authorization, WebSocket policies/timeouts and background-task shutdown.

**Assurance boundary:** macOS CI and deployment-specific security review remain release-gate work; the hosted macOS runner is not part of the current workflow. Durable queues, distributed sessions and production security certification are explicitly outside Phase 3.


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

**Status: ENGINEERING-COMPLETE (2026-11-03).** Implemented typed TOML sections, strict unknown-key/type/range validation, supported `LIMBO_` environment overrides, CLI-over-environment-over-file precedence for server host/port, `limbo doctor`, and `limbo config validate`. Python 3.11+ uses `tomllib`; Python 3.10 uses optional `tomli`. `limbo create` emits a valid starter config. `limbo preview` serves an existing configured output directory; `limbo add/remove` safely edit `package.json` declarations only and never run a package manager. `limbo create --template` provides Python API, static-site, browser-native and Vue starters. `limbo doctor` reports runtime and optional frontend-tool availability/version information. The production compiler/build pipeline remains Phase 6 and `limbo build` explicitly reports that it is not implemented yet; no fake build is performed.

**Verification:** 22 focused Phase 4 tests pass, including configuration precedence/validation, CLI failure semantics, metadata-only dependency edits, preview directory checks and clean starter generation. Generated Python starters compile; all four templates emit valid configuration/readme files, and the Vue starter's generated module source is covered against escaped-newline regressions.

**Assurance boundary:** macOS CI and release-level packaging checks remain release-gate work. Limbo does not silently install dependencies, and production `build` is intentionally deferred to Phase 6.

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

**Status: VERIFIED SLICE — resolver/graph/cache, adapters and browser smoke foundations landed.** Added a public `CompilerAdapter` protocol, immutable capability/result models, deterministic adapter registry, versioned JSON capability manifest, browser-native JS/MJS/CSS adapter, optional local esbuild adapter for TS/TSX/JSX, and optional Vue/Svelte SFC adapters backed by local compiler packages. Added `limbo frontend capabilities [directory] [--json]`; no compiler is downloaded or installed. Dependency discovery uses lexical ESM and CSS scanners for static imports, re-exports, literal dynamic imports, CSS `@import` and quoted `url()` assets. CSS side-effect imports are served through JS stylesheet-link modules; CSS imports with bindings are rejected. Transformed modules are cached persistently using content-addressed keys under ignored `.limbo/cache/frontend`. Opt-in accepted updates, safe full-page reload fallback, Vue reload hooks, WASM execution and browser fixtures are covered in CI. Svelte state HMR and full PostCSS-compatible CSS Modules remain out of scope. See [Frontend adapter contract](FRONTEND_ADAPTERS.md).

**Verification added:** unit coverage for native JS/CSS pass-through, extension rejection, project-root enforcement, duplicate adapter IDs, stable manifest JSON and CLI output; lexical ESM/CSS scanner coverage; CSS module serving; content-addressed cache persistence/invalidation; optional Vue/Svelte adapter detection; and full-reload endpoint/version behavior. Browser/HMR/WASM smoke fixtures run in the dedicated optional-tool job; deterministic unit and platform smoke coverage runs on Linux and Windows.

**Remaining Phase 5 work:** full package `exports`/`imports` edge semantics, complete PostCSS-compatible CSS Modules behavior, and Svelte-specific component-state HMR. Acorn AST scanning now covers ordinary JavaScript, while TypeScript/JSX use esbuild and a lexical fallback remains for minimal environments. TypeScript-in-SFC composed maps have real-tool validation. Vue SFCs register stable HMR identities and use Vue's runtime reload hook. CI installs Acorn, esbuild, Vue and Svelte tooling and runs the real browser fixture gate. This phase remains a verified slice rather than a universal framework-support claim.

1. Specify compiler adapter API and machine-readable capability manifest. **Foundation implemented; continue evolving contract against real adapters.**
2. Upgrade dependency discovery beyond regex-only literal imports; handle static ESM, re-exports, CSS imports, assets and supported dynamic imports. **Optional Acorn AST scanning is parser-grade for ordinary JavaScript; esbuild handles TS/JSX and computed imports remain diagnostic-only.**
3. Implement package resolution rules: package exports/imports, conditions, aliases, extensions, index files and deterministic errors.
4. Add adapters for browser-native modules, TypeScript, JSX/TSX and CSS/assets. **Browser-native and esbuild adapters are integrated; CSS assets and initial CSS-module maps are served, with full CSS Modules semantics still pending.**
5. Add official local compiler adapters for Vue and Svelte; then evaluate Preact, Solid, Lit, Angular and others independently. **Optional Vue/Svelte SFC adapters registered; runtime compatibility remains experimental.**
6. Build persistent content-addressed cache keyed by source, compiler version, options and dependency state. **Initial persistent source/options/tool cache implemented; dependency-aware invalidation needs more coverage.**
7. Add incremental invalidation and dependency graph updates.
8. Add source-map generation/serving and useful compiler diagnostics. **Inline source maps are emitted by esbuild; TypeScript-in-SFC compiler maps are passed through esbuild and covered by a real-tool end-to-end test.**
9. Implement HMR protocol and client runtime with safe full-reload fallback. **Opt-in accept/dispose updates, real Chromium coverage and Vue SFC component reload preservation are implemented; Svelte-specific state preservation remains.**
10. Handle WASM assets, MIME types, import/instantiate patterns and browser compatibility checks. **WASM assets are served with inferred MIME and tracked as graph leaves; Chromium fixtures cover WASM, HMR and optional real Vue/Svelte SFC execution in CI.**

**Exit gate:** repeatable real sample apps pass clean-start, edit/invalidate, compiler-error, cache-hit and browser smoke tests. Unsupported features are rejected with actionable diagnostics.

### Phase 6 — Local production build
**Status: COMPLETE for browser-native projects (2026-11-03).** `limbo build` emits hashed assets, a manifest, integrity, CSS link extraction and a stable digest. Minify runs only through a local esbuild. Code splitting is refused. TypeScript and SFC files fail closed when their compiler is absent.

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
**Status: HONEST MATRIX (2026-11-03).** A local process supervisor is tested with a child HTTP server. React, Preact, Solid, Lit, Angular, Astro, Qwik, Next, Nuxt, SvelteKit and Remix are unsupported. Vue and Svelte remain experimental dev adapters. Alpine and htmx are native script tags, not compilers.

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
**Status: SQLITE COMPLETE (2026-11-03).** Checksummed migrations, rollback and transactional failure handling are tested. PostgreSQL and MySQL URLs raise an error. There is no connection pool and no fake adapter.

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
**Status: SUPERVISOR COMPLETE (2026-11-03).** Detection, Quick Tunnel argv, named-tunnel argv, bounded redacted logs, process cleanup and `--confirm` for delete are tested. Limbo does not download `cloudflared` and does not implement the tunnel protocol. A live public tunnel still requires the official binary on the machine.

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
**Status: OPEN.** Documentation, a fuzz check and a local routing benchmark are in the tree. The 1.0 exit gate is not met.

**Goal:** Prove the product is stable enough to call 1.0.

- Unit, integration, malformed-wire, concurrency, lifecycle and regression suites.
- Linux/Windows CI and supported Python-version matrix; macOS remains an explicit unverified platform because the hosted runner is unstable.
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

The engineering slices below are implemented and covered by the standard-library suite. Do not treat that as a 1.0 sign-off.

1. Keep the capability matrix honest. Do not promote React, Next, Nuxt, SvelteKit, Remix, Angular, Astro, Qwik, PostgreSQL or MySQL without a real integration test.
2. Run the existing GitHub workflow on Windows and macOS before any release that claims those platforms.
3. Commission an independent review of HTTP parsing, path handling, sessions, CSRF, uploads, WebSockets and tunnel exposure. The threat model is a starting note, not that review.
4. Leave code splitting, PostCSS-complete CSS Modules and Svelte component-state HMR unimplemented until they can be tested for real.

## 9. Explicit non-goals

- A VPS or remote build service.
- Mandatory Node.js or mandatory third-party Python packages.
- Automatic installation of compilers, framework packages or cloudflared.
- Reimplementing Cloudflare's tunnel protocol.
- Claiming every framework works through a generic JSX transform.
- Treating every npm package as browser-compatible.
- Treating a successful unit test run as a security certification.
- Rewriting all of Limbo at once or breaking the public API without a migration path.
