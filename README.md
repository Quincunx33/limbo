# Limbo

<p align="center">
  <strong>A Python-first web framework with a browser-native frontend toolchain.</strong>
  <br />
  Build APIs, serve web apps, manage local frontend assets, and ship browser-native builds — without adding a third-party runtime dependency to Limbo's core.
</p>

<p align="center">
  <a href="https://github.com/Quincunx33/limbo">Repository</a> ·
  <a href="docs/STATUS.md">Roadmap status</a> ·
  <a href="docs/CAPABILITY_MATRIX.md">Capability matrix</a> ·
  <a href="docs/THREAT_MODEL.md">Threat model</a>
</p>

---

## What is Limbo?

Limbo is a Python-first, local-first framework foundation for building HTTP applications and browser-based projects. Its core uses Python's standard library: no Flask, FastAPI, Django, Starlette, or external application server is required.

The project combines a small HTTP server and routing layer with practical backend building blocks, local frontend module tooling, and a production build path for supported browser-native projects.

**Current release:** `0.9.0` · **Python:** `3.10+` · **License:** MIT

> **Project status:** Limbo is an early-stage framework, not a declared 1.0 release. Some integrations are optional or experimental, and several popular frontend frameworks are not currently supported. Review the capability matrix and security notes before choosing it for a deployment.

## Install

The published Python distribution name is **`limbo33`**. The import package and command-line executable are named **`limbo`**.

```bash
python -m pip install limbo33
```

Verify the installation:

```bash
limbo info
```

## Quick start

Create a starter project:

```bash
limbo create hello-limbo
cd hello-limbo
limbo dev
```

Open **http://127.0.0.1:8000**.

Or create an application manually:

```python
from limbo import Limbo

app = Limbo()

@app.get("/")
def home(request):
    return app.html("<h1>Hello from Limbo</h1>")

@app.get("/api/users/{user_id}")
def get_user(request):
    return {"user_id": request.path_params["user_id"]}

@app.post("/api/echo")
def echo(request):
    return {"received": request.json()}

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

Run it:

```bash
python app.py
```

## Features

### HTTP and routing
- Decorator-based routes: GET, POST, PUT, PATCH and DELETE.
- Dynamic path parameters, route groups, duplicate-route detection, and clear 404/405 handling.
- Request helpers for headers, query/path parameters, cookies, JSON, URL-encoded forms and uploaded files.
- JSON, HTML, text, redirects, byte/file responses and iterable streaming responses.
- Threaded HTTP server with request/body limits, keep-alive handling and graceful shutdown.

### Backend building blocks
- Ordered middleware, CORS, security headers, request IDs, access logging, rate limiting and gzip compression.
- Static file delivery with path containment checks, ETags, conditional requests, cache headers and byte ranges.
- Password hashing with scrypt and constant-time verification.
- Signed stateless session tokens and optional bounded, server-side in-memory sessions.
- Double-submit CSRF protection for browser requests.
- Multipart uploads with configurable limits and request-scoped temporary files.
- SQLite helper and checksummed, rollback-capable SQLite migrations.
- WebSocket support with frame validation, message limits and optional Origin allowlists.
- Bounded in-process background tasks and application startup/shutdown hooks.

### Frontend and build tooling
- Serve browser-native JavaScript, CSS, WebAssembly and static assets locally.
- Local module resolution, dependency graph tracking, import maps and incremental invalidation.
- Optional TypeScript/JSX/TSX transforms through a locally installed esbuild.
- Experimental Vue and Svelte single-file-component adapters using locally installed compilers.
- Opt-in development HMR with a safe full-page reload fallback.
- `limbo build` generates browser-native production assets, hashed filenames and a manifest.
- Optional minification only when a local esbuild is available.

Limbo detects optional tools; it does **not** install packages, download compilers, or fetch runtime dependencies for you.

## Project templates

```bash
limbo create my-api --template python-api
limbo create my-site --template static
limbo create my-browser-app --template browser-native
limbo create my-vue-app --template vue
```

| Template | Purpose |
|---|---|
| `python-api` | Python HTTP application starter |
| `static` | Static site preview starter |
| `browser-native` | JavaScript modules served locally by Limbo |
| `vue` | Experimental Vue SFC starter |

## CLI

| Command | What it does |
|---|---|
| `limbo create NAME` | Generate a starter project |
| `limbo dev` | Run the local application |
| `limbo dev --public` | Start a Quick Tunnel using an existing `cloudflared` binary |
| `limbo start` | Run the application |
| `limbo build` | Build supported frontend assets into `dist/` |
| `limbo preview` | Preview an existing build directory |
| `limbo doctor` | Inspect configuration and optional tools |
| `limbo info` | Show version and core runtime information |
| `limbo dependencies` | List required and optional integrations |
| `limbo config validate` | Validate `limbo.toml` |
| `limbo frontend doctor` | Inspect frontend compiler tools |
| `limbo frontend capabilities --json` | Print the adapter capability manifest |
| `limbo add PACKAGE` | Add a frontend package declaration |
| `limbo remove PACKAGE` | Remove a frontend package declaration |
| `limbo tunnel preflight` | Check optional tunnel prerequisites |

The `add` and `remove` commands edit package metadata only. They do not run npm or install dependencies.

## Frontend support boundaries

Limbo's frontend toolchain is deliberately incremental. Use the capability matrix as the source of truth.

| Technology | Current status |
|---|---|
| HTML, CSS, browser JavaScript | Supported |
| Web Components | Supported through browser-native APIs |
| WebAssembly assets | Supported for serving; your app handles instantiation |
| TypeScript, JSX, TSX | Optional local esbuild transform |
| Vue SFC | Experimental; local compiler required |
| Svelte SFC | Experimental; local compiler required |
| React, Preact, Solid, Lit | Not declared supported as framework integrations |
| Angular, Astro, Qwik | Not supported |
| Next.js, Nuxt, SvelteKit, Remix | Not supported as integrated frameworks |
| SQLite | Supported |
| PostgreSQL, MySQL | Not supported |

A syntax transform is not the same as a complete framework runtime integration. A process supervisor is not framework support. Limbo avoids advertising unverified integrations as working features.

Read [Frontend adapters](docs/FRONTEND_ADAPTERS.md) for setup details and limitations.

## Configuration

Limbo can load typed configuration from `limbo.toml`. For example:

```toml
[app]
name = "My Limbo app"
debug = true

[server]
host = "127.0.0.1"
port = 8000
```

Configuration precedence is:

1. CLI arguments
2. Supported environment overrides
3. `limbo.toml`
4. Safe defaults

Validate the configuration with:

```bash
limbo config validate
```

## Tests

Run the standard-library test suite from the repository root:

```bash
python -m unittest discover -s tests -v
```

The project also maintains focused frontend/browser acceptance checks. Passing the test suite is not a security certification or a guarantee for every operating system and deployment environment.

## Security and deployment

Limbo is designed to be evaluated locally first. Keep the server bound to `127.0.0.1` while developing.

Before exposing an application publicly:

- Put it behind a properly configured reverse proxy.
- Review the [Threat Model](docs/THREAT_MODEL.md) and [Release Checklist](docs/RELEASE_CHECKLIST.md).
- Configure secrets through environment variables; never commit them.
- Understand that the built-in in-memory session store is process-local and is lost on restart.
- Do not put secrets in stateless signed session payloads; signatures provide integrity, not encryption.
- Treat the built-in server and optional tunnel integration as framework building blocks, not a substitute for deployment hardening or an independent security review.

The optional tunnel workflow requires an official `cloudflared` executable that you install yourself. Limbo does not download it.

## Documentation

- [Project status and roadmap](docs/STATUS.md)
- [Capability matrix](docs/CAPABILITY_MATRIX.md)
- [Frontend adapter guide](docs/FRONTEND_ADAPTERS.md)
- [Frontend runtime integration roadmap](docs/FRONTEND_RUNTIME_ROADMAP.md)
- [Static delivery and middleware](docs/STATIC_AND_MIDDLEWARE.md)
- [Threat model](docs/THREAT_MODEL.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)

## Contributing

Issues, focused bug reports, tests and documentation improvements are welcome. When reporting a bug, include your Python version, operating system, a minimal reproduction and the exact command or test output.

## License

Limbo is licensed under the [MIT License](LICENSE).
