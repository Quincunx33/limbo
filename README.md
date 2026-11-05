# Limbo

<p align="center">
  <strong>A Python-first web framework with a browser-native frontend toolchain.</strong>
  <br />
  Build APIs, serve web apps, manage local frontend assets, and ship browser-native builds — without adding a third-party runtime dependency to Limbo's core.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/RELEASE-BETA-orange?style=for-the-badge" alt="BETA release" />
  <img src="https://img.shields.io/badge/version-0.9.0-blue" alt="Version 0.9.0" />
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" />
</p>

<p align="center">
  <a href="https://github.com/Quincunx33/limbo">Repository</a> ·
  <a href="docs/STATUS.md">Roadmap status</a> ·
  <a href="docs/CAPABILITY_MATRIX.md">Capability matrix</a> ·
  <a href="docs/THREAT_MODEL.md">Threat model</a>
</p>

---

> [!WARNING]
> **BETA RELEASE — Limbo 0.9.0**
>
> Limbo is under active development and is **not a stable 1.0 release**. Features, APIs, and behavior may change without a compatibility guarantee. Some roadmap phases remain open, and some integrations are experimental or unsupported. Use it for evaluation and testing; do not assume production readiness. Review the status, capability matrix, threat model, and release checklist before deployment.

## What is Limbo?

Limbo is a Python-first, local-first framework for building HTTP applications and browser-based projects. Its core uses Python's standard library: no Flask, FastAPI, Django, Starlette, or external application server is required.

It combines a lightweight HTTP server and routing layer with backend building blocks, local frontend module tooling, and a production build path for supported browser-native projects.

- **PyPI distribution name:** `limbo33`
- **Python import package:** `limbo`
- **CLI command:** `limbo`
- **Current development version:** `0.9.0`
- **Python:** `3.10+`
- **License:** MIT

## Installation

The package name used for installation is `limbo33`:

```bash
python -m pip install limbo33
```

Check the installed CLI:

```bash
limbo info
limbo doctor
```

For local development from a cloned repository, install the project in editable mode from its root:

```bash
python -m pip install -e .
```

## Get started

### Option 1 — Generate a project

The easiest way to start is with the Limbo CLI:

```bash
limbo create hello-limbo
cd hello-limbo
limbo dev
```

Then open **http://127.0.0.1:8000** in your browser.

You can choose a starter template:

```bash
limbo create my-api --template python-api
limbo create my-site --template static
limbo create my-browser-app --template browser-native
limbo create my-vue-app --template vue
```

The Vue starter is experimental. Check the generated project and the frontend capability guide before relying on it.

### Option 2 — Write an app yourself

Create a file named `app.py`:

```python
from limbo import Limbo

app = Limbo()

@app.get("/")
def home(request):
    return app.html("<h1>Hello from Limbo</h1>")

@app.get("/api/hello")
def hello(request):
    return {"message": "Hello from Limbo"}

@app.get("/api/users/{user_id}")
def get_user(request):
    return {"user_id": request.path_params["user_id"]}

@app.post("/api/echo")
def echo(request):
    return {"received": request.json()}

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000)
```

Run the application:

```bash
python app.py
```

Try the endpoints:

```bash
curl http://127.0.0.1:8000/
curl http://127.0.0.1:8000/api/hello
curl http://127.0.0.1:8000/api/users/42
curl -X POST http://127.0.0.1:8000/api/echo \
  -H "Content-Type: application/json" \
  -d '{"name":"Limbo"}'
```

The last endpoint reads the JSON request body with `request.json()` and returns it as JSON.

## What can you build?

### HTTP APIs and routing

- Decorator-based routes: GET, POST, PUT, PATCH and DELETE.
- Dynamic path parameters and route groups.
- Duplicate-route detection and clear 404/405 handling.
- Request helpers for headers, query/path parameters, cookies, JSON, URL-encoded forms and uploaded files.
- JSON, HTML, text, redirects, byte/file responses and iterable streaming responses.
- Threaded HTTP server with request/body limits, keep-alive handling and graceful shutdown.

### Backend features

- Ordered middleware, CORS, security headers, request IDs and access logging.
- Rate limiting and gzip compression.
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

## CLI reference

| Command | Purpose |
|---|---|
| `limbo create NAME` | Generate a starter project |
| `limbo dev` | Run the local development application |
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

**Important:** `limbo add` and `limbo remove` edit package metadata only. They do not run npm or install dependencies.

## Frontend support: read before choosing a stack

Limbo's frontend toolchain is deliberately incremental. A syntax transform is not the same as a complete framework integration.

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

A process supervisor is not framework support. Limbo avoids advertising unverified integrations as working features.

See [Frontend adapters](docs/FRONTEND_ADAPTERS.md) and the [Capability matrix](docs/CAPABILITY_MATRIX.md) for exact boundaries and setup requirements.

## Configuration

Limbo can load typed configuration from `limbo.toml`. Example:

```toml
[app]
name = "My Limbo app"
debug = true

[server]
host = "127.0.0.1"
port = 8000
```

Configuration precedence:

1. CLI arguments
2. Supported environment overrides
3. `limbo.toml`
4. Safe defaults

Validate it with:

```bash
limbo config validate
```

## Build and preview

For supported browser-native frontend projects, run:

```bash
limbo build
limbo preview
```

The build writes production assets to `dist/`, including hashed filenames and a manifest. TypeScript/Vue/Svelte files require their corresponding optional local compiler. Code splitting is not currently supported; see [Project status](docs/STATUS.md) for the precise build boundary.

## Testing

From the repository root:

```bash
python -m unittest discover -s tests -v
```

The project also maintains focused frontend/browser acceptance checks. A passing test suite is useful evidence, but it is not a security certification or a guarantee for every operating system and deployment environment.

## Current development status

Limbo is not feature-complete. The status ledger is the source of truth and records the actual verification boundary for each phase.

| Phase | Status |
|---|---|
| 0 | Complete |
| 1 | Complete |
| 2 | Complete |
| 3 | Engineering-complete |
| 4 | Engineering-complete |
| 5 | Verified slice — not full Node resolution or complete HMR |
| 6 | Complete for browser-native projects; code splitting remains unsupported |
| 7 | Contract only, except native JavaScript; major framework integrations remain unsupported |
| 8 | SQLite complete; PostgreSQL/MySQL unsupported |
| 9 | Optional binary integration; no tunnel protocol implementation or binary download |
| 10 | Open — project hardening and release work remain |

These labels describe the current repository evidence, not a promise that every feature is production-ready. See the detailed [roadmap/status ledger](docs/STATUS.md).

## Security and deployment notes

Limbo should be evaluated locally first. Keep the server bound to `127.0.0.1` while developing.

Before exposing an application publicly:

- Put it behind a properly configured reverse proxy.
- Review the [Threat Model](docs/THREAT_MODEL.md) and [Release Checklist](docs/RELEASE_CHECKLIST.md).
- Configure secrets through environment variables; never commit them.
- Remember that the built-in in-memory session store is process-local and is lost on restart.
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

Bug reports, focused tests and documentation improvements are welcome. When reporting a bug, include your Python version, operating system, a minimal reproduction and the exact command or test output.

## License

Limbo is licensed under the [MIT License](LICENSE).
