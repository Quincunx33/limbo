# Deployment

Limbo's server is a small threaded HTTP/1.1 implementation. Put a reverse proxy in front of it before any public traffic. This document is an operator checklist, not a certification.

## Bind

```toml
[app]
debug = false

[server]
host = "127.0.0.1"
port = 8000

[security]
trusted_hosts = ["example.com"]
csrf = true
secure_cookies = true
```

- Bind loopback and let the proxy own the public port, or bind a private interface on purpose.
- Set `trusted_hosts`. Forwarded headers are preserved as untrusted input; do not treat them as proof of the client.
- Turn debug off. Do not serve the frontend dev server (`/_limbo_modules`, `/_pkgfile`) as your production asset host. Run `limbo build` and serve `dist/` or your own static directory.

## Proxy

- Terminate TLS at the proxy.
- Send `Host` unchanged.
- Allow only the methods you use.
- For streaming responses, do not buffer the whole body if you need backpressure.
- WebSocket upgrades need the proxy's upgrade settings. Origin allowlists are per route.

## Data and files

- SQLite files must live on a disk you control. Migrations run the SQL you wrote.
- Uploads are temporary until `save()` and are removed after the request. Cap size and count in config.
- Sessions are signed, not encrypted. The default store is process memory.

## Public preview

`limbo dev --public` starts a Quick Tunnel only when `cloudflared` is already installed. Read the preflight warnings. A tunnel publishes your dev process, including debug behavior if you left it on.

## Still required before you call it production

- Your own proxy and load test.
- An independent review of parsing, paths, sessions, CSRF, uploads, WebSockets and tunnel exposure. The threat model is not that review.
- macOS is not part of the tested platform matrix.
