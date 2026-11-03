# Limbo Universal Framework — Engineering Plan

This document is the implementation ledger for evolving the existing Limbo 0.3.0 repository into the Universal Python Full-Stack Framework. It deliberately separates verified existing code from planned work. A feature is not considered supported until its tests exercise the real implementation.

## Non-negotiable architecture

- Python Standard Library is the only mandatory runtime dependency of the core.
- Python owns application lifecycle, HTTP serving, routing, request/response handling, middleware, CLI, local build orchestration, and optional process supervision.
- Frontend compilers are mature, optional, locally installed tools. Limbo detects and invokes them; it does not silently install them.
- No VPS, remote build service, remote compiler, or mandatory cloud service.
- Cloudflare Tunnel is an optional adapter around the official `cloudflared` executable. Limbo must never reimplement the tunnel protocol or download an unverified binary.
- Integrations are advertised as supported only after end-to-end tests pass.

## Repository baseline inspected

The current default branch is `main`, package version `0.3.0`, Python requirement `>=3.10`, with no mandatory runtime dependencies. Existing modules include:

- `limbo/app.py`: public application API, route dispatch, middleware chain, static mounts, templates, frontend mounts.
- `limbo/server.py`: threaded TCP HTTP server, request framing, keep-alive, response writing, WebSocket upgrade.
- `limbo/http.py`: request/response primitives, query/cookie/JSON/form/multipart access.
- `limbo/router.py`: decorator route table and dynamic path matching.
- `limbo/frontend.py`: local module resolver, dependency graph, optional compiler invocation, frontend dev serving.
- `limbo/database.py`, `security.py`, `uploads.py`, `websocket.py`, `templates.py`, `cli.py`.
- Existing tests cover core routing/server behavior, uploads, SQLite transactions, session primitives, templates, WebSocket handshake/frames, frontend resolution and local preview.

This is a useful foundation, not yet the target product. The README's explicit limitations remain authoritative until implementation and tests change them.

## Verified gaps from the current source

1. `limbo build` implements browser-native production output. It does not split code or bundle npm packages.
2. `limbo dev --public` supervises an existing `cloudflared`. It does not download one.
3. TOML configuration is loaded and validated, including `[build].base` and `[tunnel].config`.
4. Route groups, nested prefixes, route names, URL reversal, route priorities and route introspection now exist on the engineering branch. Route-specific middleware and custom error handlers remain incomplete.
5. The HTTP server is a custom threaded implementation. The engineering branch now enforces an absolute per-request read deadline, validates Host authority and optional trusted-host allowlists, rejects malformed request lines and ambiguous framing, validates chunked bodies/trailers, bounds headers/body/requests per connection, validates response framing and includes concurrent socket tests. This is substantial protocol hardening, not a production security certification.
6. Static serving has useful containment, validators and byte ranges; compression, robust conditional semantics, SPA fallback and an explicit symlink policy remain incomplete.
7. Frontend dependency scanning is not a complete JavaScript parser. Literal ESM import support must not be confused with arbitrary dynamic import/CSS graph support.
8. HMR, persistent compiler cache, production optimization, code splitting and source-map pipeline are missing.
9. React runtime integration and framework SSR adapters are not complete. Vue/Svelte preview integrations remain experimental.
10. CSRF, authentication/authorization hooks, host validation and rate limiting are implemented; deployment-specific production security defaults still require review and security testing.
11. Bounded in-process background task execution is implemented; durable scheduling and distributed workers are intentionally absent.
12. Database support is SQLite-only; migrations, pooling and optional adapters are absent.

## Target module boundaries

Keep current public imports stable where possible, then move implementation behind compatible modules in small steps:

- `limbo/core/`: server lifecycle, parser, request, response, router, middleware, exceptions.
- `limbo/static/`: MIME policy, validators/cache, ranges, compression and streaming.
- `limbo/frontend/`: resolver, compiler adapters, graph, cache and HMR protocol.
- `limbo/build/`: dev/build orchestration, manifests and output validation.
- `limbo/runtime/`: local child-process adapters for SSR frameworks.
- `limbo/tunnel/`: cloudflared discovery, process supervision, quick/named tunnel lifecycle.
- `limbo/config/`: TOML parsing and typed validation, with Python 3.10 compatibility handled explicitly.
- `limbo/security/`, `limbo/database/`, `limbo/websocket/`, `limbo/tasks/`, `limbo/cli/`, `limbo/testing/`.

Do not perform a large directory-only rewrite. Introduce compatibility re-exports and move one responsibility at a time.

## Verified milestone sequence

### Milestone 0 — Baseline and engineering controls
- Record the current API and test coverage.
- Add CI for supported Python versions; operating-system coverage remains pending.
- Add security-focused tests and a capability matrix.
- Keep this branch isolated until the baseline is green.

### Milestone 1 — Core HTTP and routing correctness
- [x] Add route groups/prefixes, names, URL reversal, deterministic priority/conflict rules, route introspection and route-specific middleware.
- [x] Add status- and exception-class-based error handlers, including custom 404/405 handling.
- [x] Validate method tokens, origin-form request targets, Host authority and optional trusted-host allowlists.
- [x] Enforce an absolute request deadline across request-line, headers and body reads; test slow-drip behavior.
- [x] Validate Content-Length/Transfer-Encoding framing, chunked bodies/trailers, header/body limits and malformed CRLF cases.
- [x] Audit response Content-Length, Transfer-Encoding, chunked streaming, HEAD and no-body status handling.
- [x] Add concurrent real-socket and malformed-wire tests; CI passed on Python 3.10–3.13.
- [ ] Complete broader proxy interoperability, sustained-load shutdown and external protocol testing before production claims.

### Milestone 2 — Request, response, static and middleware completeness
- Add response cookies and safe header helpers.
- Complete conditional request precedence, range edge cases, HEAD, MIME policy, streaming backpressure and compression.
- Add explicit symlink policy, SPA fallback and directory index behavior without exposing filesystem paths.
- Add CORS, rate limiting, security headers, request logging and exception hooks as composable middleware.

### Milestone 3 — Full-stack foundation
- Harden multipart parsing and upload lifecycle.
- Improve SQLite lifecycle/transaction semantics while keeping it optional-by-use.
- Add CSRF primitives and clear auth/session policy hooks.
- Complete WebSocket protocol edge cases and Origin policy hooks.
- Add local background task primitives with cancellation and shutdown semantics.

### Milestone 4 — Configuration and CLI
- [x] Implement typed `limbo.toml` parsing/validation.
- [x] Add `limbo add/remove` as explicit dependency metadata operations (never silent installation).
- [x] Add truthful `doctor`, `info`, and `dependencies` capability reporting.
- [x] Preserve no-config defaults.

Milestone 4 is engineering-complete. Focused configuration, CLI, metadata and starter-template tests pass; cross-platform CI and release packaging remain final release-gate work.

### Milestone 5 — Frontend development engine
- Define a compiler adapter contract and capability model.
- Add safe resolver coverage for package exports, aliases, dynamic imports that can be statically analyzed, CSS and assets.
- Integrate locally installed TypeScript/esbuild and framework compilers as optional tools.
- Add persistent content-addressed cache, dependency invalidation, source maps and HMR.
- Reject unsupported Node-only modules with actionable diagnostics.

### Milestone 6 — Local production build and SSR runtimes
- Implement local build graph, manifest, output directory, asset hashing and reproducible build metadata.
- Enable minification/code splitting only through adapters that actually support them.
- Supervise local SSR child processes with bounded logs, readiness checks, signals and cleanup.
- Test real starter applications for each adapter before advertising support.

### Milestone 7 — Cloudflare Tunnel
- Detect `cloudflared`; never download it automatically.
- Implement Quick Tunnel readiness, URL extraction, status monitoring and process cleanup.
- Implement named tunnel operations only through documented `cloudflared` commands/configuration, with credential redaction and explicit destructive-action confirmation.
- Require explicit public-exposure warning and production security checks.

### Milestone 8 — Stabilization and release
- Run protocol/security review, cross-platform CI, realistic frontend fixture tests and benchmarks.
- Publish complete docs and compatibility matrix.
- Package and release only after test matrix and security review are green.

## Definition of done

A milestone is complete only when:
1. The implementation is real (no mock or placeholder path).
2. Unit and integration tests cover success, failure and adversarial cases.
3. The supported capability matrix and docs match the tested behavior.
4. Optional dependencies remain optional and missing tools produce actionable errors.
5. CI passes on the declared support matrix.

## Immediate next implementation slice

Continue Milestone 1 with the remaining connection-lifecycle and response-framing audit. The current parser-hardening slice and routing API changes are on `engineering/universal-limbo`; they are not yet merged to `main`.
