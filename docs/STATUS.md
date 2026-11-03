# Limbo 0.9.0 status

This ledger matches the code and the test suite. A row is done only when a test exercises it.

| Phase | State | What is true now |
|---|---|---|
| 0 | Complete | Versioned package, private GitHub repository, CI workflow for Python 3.10–3.13, and green Linux/Windows platform coverage. |
| 1 | Complete | HTTP parsing, routing, deadlines, host checks, shutdown tests. Not a protocol certification. |
| 2 | Complete | Static files, ranges, middleware, gzip. Symlink races remain documented. |
| 3 | Engineering-complete | Uploads, sessions, CSRF, WebSocket policy, background tasks. Not a durable queue. |
| 4 | Engineering-complete | `limbo.toml`, CLI, starters. `add`/`remove` never install packages. |
| 5 | Verified slice | Adapter contract, graph, cache, source maps, opt-in HMR, Vue reload hook, WASM assets and browser smoke fixtures. Not full Node resolution, not PostCSS, not Svelte state HMR. |
| 6 | Complete for browser-native projects | `limbo build` writes hashed files, `manifest.json`, integrity, CSS `<link>` extraction and a stable digest. esbuild minifies only when it is already installed. Code splitting throws. `.ts`/`.vue`/`.svelte` fail closed without their compiler. |
| 7 | Contract only, except native JS | `LocalProcessRuntime` starts, health-checks, forwards and stops a child. The capability matrix marks React, Next, Nuxt, SvelteKit, Remix, Angular, Astro, Qwik, Solid, Lit, Preact unsupported. Vue and Svelte stay experimental. |
| 8 | SQLite complete | `Migrator` applies, checksums and rolls back. `postgres://` and `mysql://` raise `DatabaseURLError`. No pool. |
| 9 | Optional binary | Detect, redact, supervise, preflight, and refuse unconfirmed delete. No download and no tunnel protocol. |
| 10 | Open | Threat model, checklist, changelog, fuzz checks and `benchmarks/bench_core.py` are in the tree. 1.0 is not declared. |

## Assurance boundary

The final GitHub Actions run [41719193704](https://github.com/taaissu/limbo-framework/actions/runs/41719193704) is green for Python 3.10–3.13, Ubuntu, Windows and the optional frontend-tool/browser job. The hosted macOS runner repeatedly hung and is intentionally excluded; this is not proof of macOS behavior. Passing `python -m unittest discover -s tests` is not a security certification. Run deployment-specific proxy, load and tunnel tests before any public exposure.
