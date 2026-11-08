# Changelog

## 1.0.0 — 2026-11-08

- Added `macos-latest` to the GitHub Actions platform smoke-test matrix.
- Verified Python compilation, 211 standard-library tests, 57 frontend/browser tests, and local TypeScript/JSX browser subtests with esbuild.
- Verified wheel and source-distribution builds plus clean virtual-environment installation locally.
- Independent security review and hosted macOS execution remain release-process follow-ups; this entry does not certify production security or macOS behavior before CI completes.

## 0.9.1 — 2026-11-08

- Canonical module namespace is `/_limbo_modules/`. `/_pkgfile/` remains a compatible alias.
- Local esbuild, when already installed, rewrites CommonJS packages to browser ESM with named exports. No CDN fallback.
- Production builds vendor installed browser ESM and refuse remote module URLs.
- Config keys: `runtime_modules`, `module_url_prefix`, `allow_remote_imports`, `jsx_runtime`, `jsx_import_source`.
- Browser fixtures cover Preact, Lit and React in development and in `limbo build` output. React Fast Refresh is not included.
- Phase 10 notes: architecture, API, deployment, troubleshooting, migration, deprecation, HTTP parser fuzz and a static benchmark. 1.0 is not signed off.

## 0.9.0 engineering updates — 2026-11-04

- Added a private GitHub repository and green Actions coverage for Python 3.10–3.13 on Linux and Windows, plus optional frontend/compiler and Chromium browser fixtures.
- Stabilized Windows canonical-path assertions and Chromium HMR/WASM smoke fixtures across Python versions.
- Kept macOS explicitly unverified because the hosted runner repeatedly hung; the workflow makes no macOS support claim.
- Added local-first buildless runtime resolution: browser ESM packages are served from project `node_modules` through generated import maps, while remote CDN/data imports are rejected by default.

## 0.9.0

- Production build for browser-native modules: content-addressed files, manifest, integrity, base path, CSS side-effect extraction, identity source maps, optional esbuild minify. Code splitting is an error. Missing compilers are errors.
- SQLite migrations with checksums, directory loader and rollback. PostgreSQL and MySQL URLs are rejected.
- Optional Cloudflare Tunnel supervisor: Quick Tunnel argv, named-tunnel argv, log redaction, process stop, preflight warnings, delete refused without `--confirm`. No binary download.
- Local SSR process supervisor with readiness, forward and shutdown. Framework rows stay unsupported.
- Public capability matrix and `assert_no_blanket_claim()`.
- Docs: status, threat model, release checklist.

## 0.3.0 – 0.5.x

- Experimental HTTP server, routing, static files, sessions, uploads, frontend dev engine, configuration and CLI.
