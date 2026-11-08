# Limbo 1.0.0 status

This ledger matches the code and the test suite. A row is done only when a test exercises it.

| Phase | State | What is true now |
|---|---|---|
| 0 | Complete | Versioned package, private GitHub repository, CI workflow for Python 3.10–3.13, and Linux/Windows platform coverage; macOS is now included in the workflow and awaits a hosted run. |
| 1 | Complete | HTTP parsing, routing, deadlines, host checks, shutdown tests. Not a protocol certification. |
| 2 | Complete | Static files, ranges, middleware, gzip. Symlink races remain documented. |
| 3 | Engineering-complete | Uploads, sessions, CSRF, WebSocket policy, background tasks. Not a durable queue. |
| 4 | Engineering-complete | `limbo.toml`, CLI, starters. `add`/`remove` never install packages. |
| 5 | Verified slice | Adapter contract, graph, cache, source maps, opt-in HMR, Vue reload hook, WASM assets. Canonical `/_limbo_modules` namespace with `/_pkgfile/` still served. Local esbuild turns CommonJS packages into browser ESM. Not full Node resolution, not PostCSS, not Svelte state HMR. |
| 6 | Complete for browser-native projects | `limbo build` writes hashed files, `manifest.json`, integrity, CSS `<link>` extraction and a stable digest. Installed browser ESM is vendored into `dist`. esbuild minifies and rewrites CommonJS only when it is already installed. Code splitting throws. `.ts`/`.vue`/`.svelte` fail closed without their compiler. |
| 7 | Honest matrix | Preact, Lit and React are optional local runtimes with browser fixtures. Vue and Svelte stay experimental. Next, Nuxt, SvelteKit, Remix, Angular, Astro, Qwik and Solid stay unsupported. |
| 8 | SQLite complete | `Migrator` applies, checksums and rolls back. `postgres://` and `mysql://` raise `DatabaseURLError`. No pool. |
| 9 | Optional binary | Detect, redact, supervise, preflight, and refuse unconfirmed delete. No download and no tunnel protocol. |
| 10 | Open | Threat model, architecture, API, deployment, troubleshooting, migration and deprecation notes, changelog, fuzz checks and `benchmarks/bench_core.py` are in the tree. Independent security review and hosted macOS verification remain open. |

## Assurance boundary

The previously recorded GitHub Actions run [41719193704](https://github.com/taaissu/limbo-framework/actions/runs/41719193704) is green for Python 3.10–3.13, Ubuntu, Windows and the optional frontend-tool/browser job. The workflow now includes `macos-latest`, but no new hosted macOS result is recorded yet. Passing tests are not a security certification. Run deployment-specific proxy, load and tunnel tests before any public exposure.
