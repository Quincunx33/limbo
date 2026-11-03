# Changelog

## 0.9.0

- Production build for browser-native modules: content-addressed files, manifest, integrity, base path, CSS side-effect extraction, identity source maps, optional esbuild minify. Code splitting is an error. Missing compilers are errors.
- SQLite migrations with checksums, directory loader and rollback. PostgreSQL and MySQL URLs are rejected.
- Optional Cloudflare Tunnel supervisor: Quick Tunnel argv, named-tunnel argv, log redaction, process stop, preflight warnings, delete refused without `--confirm`. No binary download.
- Local SSR process supervisor with readiness, forward and shutdown. Framework rows stay unsupported.
- Public capability matrix and `assert_no_blanket_claim()`.
- Docs: status, threat model, release checklist.

## 0.3.0 – 0.5.x

- Experimental HTTP server, routing, static files, sessions, uploads, frontend dev engine, configuration and CLI.
