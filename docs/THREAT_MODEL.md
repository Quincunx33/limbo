# Threat model

This note is an engineering aid. It is not an independent security assessment and it does not clear the 1.0 gate.

## Assets

- Request data, uploaded files, session payloads, CSRF secrets, tunnel logs, and the files a project asks Limbo to serve.

## Boundaries

- The HTTP parser is a custom threaded implementation. Tests cover malformed lines, ambiguous framing, bounds and deadlines. They do not replace a proxy or a fuzzer run against a public deployment.
- Static paths must stay inside the configured root. The documented symlink fallback is not a promise against every filesystem race.
- Session tokens are signed, not encrypted. The in-memory store is process-local.
- Upload files are temporary and removed after the request. `save()` has to be called inside the handler.
- Tunnel logs are redacted for a fixed set of credential shapes. Redaction is not a guarantee that every secret format is covered. Do not pass credentials on the command line when you can avoid it.
- `limbo dev --public` publishes a local port. Preflight warnings are printed. They do not stop you from making a bad choice.
- Database migrations execute the SQL you wrote. They parameterize history rows, not the contents of your migration files.

## Out of scope

- Distributed sessions, a durable job queue, a multi-tenant sandbox, and any claim that PostgreSQL or a framework SSR stack has been hardened here.
- Automatic installation of `cloudflared`, Node, or framework compilers.

## Before public traffic

Bind deliberately, put a reverse proxy in front, turn debug off, set CSRF, review uploads and trusted hosts, and run tests that match your own proxy and load.
