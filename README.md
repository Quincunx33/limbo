# Limbo

**A Python-first, local-first full-stack framework.** The core package has no required third-party runtime dependencies and includes a threaded HTTP/1.1 server. Current version: 0.9.0.

> 0.9.0 finishes the engineering slices in the roadmap. It is not a 1.0 release. No independent security review has been done, and this tree was not signed off on Windows or macOS. Do not expose it to untrusted public traffic.

## Quick start

```bash
python -m pip install -e .
limbo create hello-limbo
cd hello-limbo
limbo dev
```

Open `http://127.0.0.1:8000`. Python 3.10+ required. Or create `app.py`:

```python
from limbo import Limbo

app = Limbo()

@app.get("/")
def home(req):
    return app.html("<h1>Hello Limbo</h1>")

@app.get("/api/users/{user_id}")
def user(req):
    return {"user_id": req.path_params["user_id"]}

app.run(port=8000)
```

## Currently implemented

- Decorator routes for GET, POST, PUT, PATCH and DELETE; dynamic `{parameter}` segments; duplicate route detection; 404/405 responses; automatic HEAD fallback and OPTIONS for matching paths.
- Request properties for path, query parameters, headers, cookies, JSON and URL-encoded form data.
- JSON, text, HTML, redirects, byte/file responses and iterable streaming responses.
- Direct TCP server, concurrent client threads, HTTP/1.0 and HTTP/1.1 request parsing, bounded headers/body, Content-Length and chunked request bodies, keep-alive, HEAD response body suppression, and clean connection close.
- Ordered onion-style middleware.
- Static file mounts with traversal containment, ETag, Last-Modified, cache headers, If-None-Match and single byte-range responses.
- Escaped `string.Template` HTML rendering; incremental multipart parsing directly from the socket into request-scoped temporary files with body/file/field quotas; safe upload names, exclusive no-overwrite persistence and chunked file writes; streamed file responses; and a small serialized-connection SQLite helper with rollback-capable transactions.
- Password hashing with `hashlib.scrypt`, constant-time verification, HMAC-signed expiring stateless session tokens, secure cookie formatting helpers, and optional bounded server-side in-memory sessions with ID rotation/revocation.
- Basic RFC 6455 WebSocket upgrade, masked client frame validation, text/binary messages, continuation frames, ping/pong, close handling and message-size limits; optional exact per-route Origin allowlists.
- Local frontend module preview with safe project-contained resolution, `tsconfig.json` path aliases, package `exports`/`imports` conditional resolution, browser import maps, dependency graph traversal and invalidation. Hidden files and unapproved asset types are not served.
- Optional esbuild transform for TypeScript/JSX/TSX; optional Vue SFC and Svelte SFC compilers run locally through Node.js. Vue/Svelte outputs are served as modules with generated styles attached. Tools are detected, never installed by Limbo.
- `limbo create` starter templates (`python-api`, `static`, `browser-native`, `vue`), `dev`, `dev --public`, `start`, `build`, `preview`, `doctor`, `tunnel preflight`, `config validate`, `info`, `dependencies`, and safe `add`/`remove` package metadata edits. `limbo build` emits a reproducible browser-native `dist/` with a manifest. It does not install compilers, minify without a local esbuild, or claim code splitting.

- Signed double-submit CSRF middleware for browser requests, including header and URL-encoded form tokens.
- Bounded local background task execution with cancellation, error reporting, and graceful shutdown; synchronous application startup/shutdown hooks.
- Request convenience accessors for headers, query/path parameters, cookies, forms and files, plus JSON type validation with HTTP 400/422 errors.

Phase 3 is engineering-complete on the supported local Python path. Its request, upload, session, CSRF, authentication-hook, WebSocket-policy, background-task and lifecycle paths are covered by focused integration and failure-path tests. This is not a production security certification, durable job queue, distributed session store or cross-platform CI claim.

## Phase 4 configuration and CLI (in progress)\n\nLimbo loads typed TOML sections for app, server, frontend, build, static, security, database and tunnel. Unknown sections/keys, wrong types and invalid ranges are rejected. `limbo dev` and `limbo start` read server host and port. Environment overrides use supported `LIMBO_` variables; CLI `--host`/`--port` take precedence. Order: CLI > environment > TOML > safe defaults. Python 3.11+ uses `tomllib`; Python 3.10 requires optional `tomli`.\n\n`limbo config validate` checks the file and prints each configured value's source without printing values. `limbo preview` serves an existing build directory. `limbo add` and `limbo remove` edit `package.json` declarations only; they never invoke npm or download packages. `limbo create NAME --template ...` supports `python-api`, `static`, `browser-native` and `vue` starters. The production `limbo build` pipeline is intentionally deferred to Phase 6 and currently exits with an explicit not-implemented message; no placeholder build output is produced.\n\n## Phase 3 backend building blocks (experimental)

Session middleware stores session data on the server and sends only a random opaque ID in an HttpOnly cookie. The built-in SessionStore is bounded and process-local: sessions are lost on restart and are not shared across multiple worker processes. Rotate the ID after authentication or privilege changes, and destroy it on logout.

~~~python
import os
from limbo import Limbo, SessionStore, session_middleware, csrf_protect

app = Limbo()
app.middleware(session_middleware(SessionStore(), secure=True))
app.middleware(csrf_protect(os.environ["LIMBO_CSRF_SECRET"], secure=True))

@app.post("/login")
def login(request):
    request.session["user_id"] = 123
    request.session.regenerate()
    return {"ok": True}

@app.post("/logout")
def logout(request):
    request.session.destroy()
    return {"ok": True}
~~~

For unsafe browser requests, send the readable CSRF cookie token in the X-CSRF-Token header, or use the _csrf field in URL-encoded forms. Multipart requests should use the header. Do not expose the CSRF secret in source control.

BackgroundTasks is available as app.background_tasks; it bounds running plus queued work and shuts down with the app. It is not a durable queue. Use @app.on_startup and @app.on_shutdown for synchronous resource lifecycle hooks. WebSocket routes can set exact Origin checks, message-size limits and idle timeouts with @app.websocket("/ws", origins=("https://app.example",), max_message_bytes=65536, idle_timeout=30). Authentication and authorization helpers are extension points: authenticate(resolve_user) attaches request.user, and authorize(check) evaluates an application-defined policy. SessionBackend is a structural contract for user-supplied persistent/shared stores; Limbo does not ship a database-backed session adapter.

## Frontend preview

The project generator includes a dependency-free browser module example. For a custom project:

```python
app.enable_frontend("frontend")

@app.get("/")
def home(req):
    return app.html("<div id='app'></div>" + app.frontend_script("src/main.js"))
```

The import map is emitted before the module entry. `limbo frontend doctor [directory]` inspects optional tools. To transform TypeScript/JSX/TSX, install esbuild in the *frontend project* yourself, for example `npm install --save-dev esbuild`. Vue SFC compilation needs `vue` plus `@vue/compiler-sfc`; Svelte SFC compilation needs `svelte`. Limbo does not run npm or fetch compilers for you. The base server may be used without Node.js for browser-native JavaScript modules. `limbo frontend capabilities --json` prints the versioned adapter capability manifest. The public adapter contract and current support boundaries are documented in [Frontend adapters](docs/FRONTEND_ADAPTERS.md).

There is a Vue sample in `examples/frontend/`. From the repository root, install the sample’s optional modules with `cd examples/frontend && npm install`, return to the repository root, then run `python examples/frontend_app.py` and open `http://127.0.0.1:8000`.

Framework adapters are intentionally partial: React TSX syntax can be transformed, but the current React CommonJS runtime package is **not** browser-module served; the adapter returns an explicit diagnostic instead of claiming the app runs. Vue/Svelte compiler output and ESM runtime module graphs have been exercised, but these adapters are experimental and do not claim general production compatibility. The dependency scanner recognizes static ESM imports, side-effect imports, re-exports and literal dynamic imports, plus CSS `@import` and quoted `url()` references. Browser-native CSS side-effect imports are extracted to `<link>` tags by `limbo build`. CSS imports with bindings stay on the dev server and are rejected by the production builder instead of being emitted as broken modules. `limbo build` hashes one file per module, rewrites relative URLs, and writes `manifest.json`. It does not bundle npm packages or split code. A development polling client can apply opt-in accept/dispose updates and otherwise reloads the page. Svelte component-state HMR is not implemented.

## Not claimed

These are unimplemented or only partially exercised. Do not assume support:

- React, Preact, Solid, Lit, Angular, Astro, Qwik, Next, Nuxt, SvelteKit and Remix as verified app frameworks. A local process supervisor can start a child HTTP server; that is not those frameworks.
- PostgreSQL, MySQL and MariaDB. Those URL schemes are rejected.
- Downloading `cloudflared`, Node, or compilers. Quick Tunnel supervision requires an official binary you installed.
- Code splitting, a PostCSS-complete CSS Modules pipeline, and Svelte component-state HMR.
- A production security certification. See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) and [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md).

The capability rows and their evidence are in [docs/CAPABILITY_MATRIX.md](docs/CAPABILITY_MATRIX.md). Status of every roadmap phase is in [docs/STATUS.md](docs/STATUS.md).

## Static delivery and middleware

See [Static delivery and middleware](docs/STATIC_AND_MIDDLEWARE.md) for safe static mounts, response helpers, CORS, security headers, request IDs, access logs, rate limiting, gzip, and deployment limitations.

## Tests

The test suite uses only the Python standard library:

```bash
python -m unittest discover -s tests -v
```

## Security note

This is an early release. Keep it bound to localhost while evaluating. Stateless SessionSigner payloads are signed but **not encrypted**, so do not place secrets in them. The optional in-memory SessionStore keeps data server-side but is process-local and non-persistent. Password hashing uses scrypt with a fixed conservative default; assess resource policy for your deployment. The built-in static server checks resolved paths remain within the configured root; it does not promise a comprehensive security boundary against every filesystem race, malformed HTTP edge case, or hostile deployment environment. Set an upstream reverse proxy and review the code before any public deployment.


See [Static delivery and middleware](docs/STATIC_AND_MIDDLEWARE.md) for static mounts, conditional/range responses, middleware, compression and SPA fallback behavior.


## Phase 3 end-to-end example

See [`examples/phase3_app.py`](examples/phase3_app.py) for a runnable integration of sessions, CSRF, authentication/authorization extension points, bounded uploads, WebSocket policies and background tasks. Set `LIMBO_CSRF_SECRET` to at least 32 random bytes before running it. The demo identity map is intentionally not a real login system.
