# Limbo

**An experimental, Python-first web framework foundation.** The core package has no runtime third-party dependencies and contains a threaded HTTP/1.1 server implemented with Python sockets. Current package version: 0.3.0.

> Limbo 0.3.0 is an early, experimental foundation—not the universal full-stack framework described in the original project brief. Do not expose it to untrusted public traffic without an independent security review.

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
- Escaped `string.Template` HTML rendering, multipart fields and upload objects with non-overwriting save, streamed file responses, and a small serialized-connection SQLite helper with rollback-capable transactions.
- Password hashing with `hashlib.scrypt`, constant-time verification, HMAC-signed expiring stateless sessions, and secure cookie formatting helpers.
- Basic RFC 6455 WebSocket upgrade, masked client frame validation, text/binary messages, continuation frames, ping/pong, close handling, and message-size limits.
- Local frontend module preview with safe project-contained resolution, `tsconfig.json` path aliases, package `exports`/`imports` conditional resolution, browser import maps, dependency graph traversal and invalidation. Hidden files and unapproved asset types are not served.
- Optional esbuild transform for TypeScript/JSX/TSX; optional Vue SFC and Svelte SFC compilers run locally through Node.js. Vue/Svelte outputs are served as modules with generated styles attached. Tools are detected, never installed by Limbo.
- `limbo create`, `dev`, `start`, `doctor`, `info`, `dependencies`; an explicit not-implemented response for `build`.

## Frontend preview

The project generator includes a dependency-free browser module example. For a custom project:

```python
app.enable_frontend("frontend")

@app.get("/")
def home(req):
    return app.html("<div id='app'></div>" + app.frontend_script("src/main.js"))
```

The import map is emitted before the module entry. `limbo frontend doctor [directory]` inspects optional tools. To transform TypeScript/JSX/TSX, install esbuild in the *frontend project* yourself, for example `npm install --save-dev esbuild`. Vue SFC compilation needs `vue` plus `@vue/compiler-sfc`; Svelte SFC compilation needs `svelte`. Limbo does not run npm or fetch compilers for you. The base server may be used without Node.js for browser-native JavaScript modules.

There is a Vue sample in `examples/frontend/`. From the repository root, install the sample’s optional modules with `cd examples/frontend && npm install`, return to the repository root, then run `python examples/frontend_app.py` and open `http://127.0.0.1:8000`.

Framework adapters are intentionally partial: React TSX syntax can be transformed, but the current React CommonJS runtime package is **not** browser-module served; the adapter returns an explicit diagnostic instead of claiming the app runs. Vue/Svelte compiler output and ESM runtime module graphs have been exercised, but these adapters are experimental and do not claim general production compatibility. The dependency scanner follows literal ESM imports; computed dynamic imports, CSS module imports, and general CSS import graphs are not yet resolved. There is no HMR or persistent compiler cache in this wave.

## Not implemented (do not assume support)

Full React runtime integration, HMR, persistent compiler caching, CSS modules, general CSS import processing, production build optimization, code splitting, framework SSR, full standards-compliant proxy/host validation, CSRF protection, an integrated login/authorization system, database migrations/pooling, configuration loading from TOML, Cloudflare Tunnel management, route groups, and named tunnel lifecycle are not implemented. WebSockets do not support extensions/compression or application-level Origin policy; configure an allowlist in application code before exposing a WebSocket endpoint. Node.js and cloudflared are not used or downloaded by the core. `limbo dev --public` is not available.

## Static delivery and middleware

See [Static delivery and middleware](docs/STATIC_AND_MIDDLEWARE.md) for safe static mounts, response helpers, CORS, security headers, request IDs, access logs, rate limiting, gzip, and deployment limitations.

## Tests

The test suite uses only the Python standard library:

```bash
python -m unittest discover -s tests -v
```

## Security note

This is an early release. Keep it bound to localhost while evaluating. Session payloads are signed but **not encrypted**, so do not place secrets in them. Password hashing uses scrypt with a fixed conservative default; assess resource policy for your deployment. The built-in static server checks resolved paths remain within the configured root; it does not promise a comprehensive security boundary against every filesystem race, malformed HTTP edge case, or hostile deployment environment. Set an upstream reverse proxy and review the code before any public deployment.


See [Static delivery and middleware](docs/STATIC_AND_MIDDLEWARE.md) for static mounts, conditional/range responses, middleware, compression and SPA fallback behavior.
