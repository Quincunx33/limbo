# Static delivery and middleware

Limbo's static mount is intentionally conservative:

```python
from limbo import Limbo

app = Limbo()
app.static(
    "/assets",
    "public",
    index="index.html",
    cache_control="public, max-age=3600",
    symlinks="deny",
)

# Optional: use this for a client-side routed application.
app.static("/app", "frontend-dist", spa_fallback="index.html")
```

- Only GET and HEAD are served. Other methods receive 405.
- Directory listings are never generated. An index file is served only when explicitly configured.
- `symlinks="deny"` rejects symbolic links. On platforms with directory-relative file opening, every component is opened with no-follow semantics; other platforms use a documented best-effort containment fallback.
- `symlinks="within-root"` permits links only when their resolved target remains inside the configured root. It is a convenience policy, not a race-proof sandbox.
- Files are streamed in bounded chunks. The open descriptor is the source of truth after path resolution.
- Weak metadata ETags, Last-Modified, conditional GET, single byte ranges, If-Range, MIME and Cache-Control are emitted. Unsupported units, malformed syntax and multiple ranges are ignored; valid but unsatisfiable byte ranges return 416. End offsets beyond the file size are clamped.
- HEAD returns representation headers without sending a body.
- Optional SPA fallback serves only missing paths that accept `text/html`; missing API routes and JS/CSS assets remain 404.

## Response helpers

```python
from limbo import Response

response = Response.json({"ok": True})
response.set_header("X-Trace", "request-123")
response.set_cookie("session", "opaque-value", secure=True, httponly=True, samesite="Lax")
response.add_vary("Origin", "Accept-Encoding")
return response
```

Use `Response.redirect("/login", 303)` for validated redirects. Multiple cookies are serialized as separate Set-Cookie fields.

## Middleware

```python
from limbo import Limbo, access_log, cors, gzip_compress
from limbo import rate_limit, request_id, security_headers

app = Limbo()
app.middleware(request_id())
app.middleware(access_log())
app.middleware(security_headers())
app.middleware(cors(allow_origins=("https://app.example",)))
app.middleware(rate_limit(capacity=60, refill_rate=1.0))
app.middleware(gzip_compress(minimum_size=500))
```

Middleware is process-local and ordered: the first registered middleware is outermost. The in-memory token bucket is not a distributed limiter and resets on process restart. Configure CORS with explicit origins for credentialed applications. Security headers are defaults and do not overwrite headers set by the application. Gzip applies only to negotiated, eligible responses; it skips HEAD, partial/no-transform/already-encoded responses and statuses that must not carry a body.

## Scope and deployment notes

This is framework-level behavior, not a substitute for a hardened reverse proxy, CDN, external rate limiter, antivirus upload scanning, or an independent security review. The `within-root` symlink policy and platforms lacking directory-relative no-follow APIs have weaker race guarantees. Test filesystem semantics on the deployment OS before serving untrusted writable directories.
