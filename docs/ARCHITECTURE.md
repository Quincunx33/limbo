# Architecture

Limbo is one Python package. The public import stays `from limbo import Limbo`. Internals move only behind that import.

## Process

```text
limbo CLI
   |
   v
Limbo app  --->  threaded HTTP/1.1 server
   |                 |
   |                 +-- parser, deadlines, host checks, WebSocket upgrade
   |
   +-- router (groups, names, reversal)
   +-- middleware (CORS, security headers, gzip, rate limit, sessions, CSRF)
   +-- static files, uploads, templates
   +-- SQLite + checksummed migrations
   +-- in-process background tasks
   +-- optional cloudflared supervisor
   +-- frontend dev server and production build
```

Python owns orchestration. Compilers, framework CLIs and `cloudflared` are optional local processes. Limbo does not download them and does not reimplement their protocols.

## Frontend path

```text
source import
    |
    v
ModuleResolver  (relative, tsconfig paths, package exports/imports)
    |
    +-- project files
    +-- node_modules browser ESM
    |
    v
DependencyGraph + import map
    |
    +-- /_limbo_modules/<package>/<file>     canonical
    +-- /_pkgfile/node_modules/...           compatibility
    |
    v
browser
```

CommonJS entries are not served as JavaScript modules. When a local `esbuild` exists, Limbo transforms that one entry to ESM with `--packages=external` and leaves sibling packages on the import map. Missing esbuild is an error that names the install command. Remote CDNs are rejected.

## What is intentionally not here

- A remote build service or VPS requirement.
- PostgreSQL, MySQL, a connection pool, or a durable job queue.
- Code splitting, a PostCSS-complete CSS Modules implementation, and Svelte component-state HMR.
- A claim that Next, Nuxt, SvelteKit, Remix, Angular, Astro or Qwik are integrated. `LocalProcessRuntime` only supervises a child process.
