# API reference

This is the supported surface. Undocumented modules can change.

## Application

```python
from limbo import Limbo

app = Limbo(name="demo")

@app.get("/items/{item_id}")
def read_item(request):
    return {"id": request.path_params["item_id"]}

@app.post("/items")
def create_item(request):
    return {"received": request.json()}
```

- Methods: `get`, `post`, `put`, `patch`, `delete`, `route`.
- `app.html`, `app.redirect`, returning a `dict` or `list` sends JSON.
- `app.static(url_prefix, directory)` serves files inside that directory.
- `app.websocket(path)(handler)` upgrades after the optional Origin check.
- `app.exception_handler(status_or_type)` registers an error handler.
- `app.enable_frontend(directory, config=None)` mounts the dev module server.
- `app.frontend_script(entry)` returns the import-map and module tags.
- `app.run(host="127.0.0.1", port=8000)`.

`enable_frontend(..., config=load_config())` reads:

| Key | Meaning |
|---|---|
| `runtime_modules` | Must be `local`. |
| `module_url_prefix` | One segment, default `/_limbo_modules`. |
| `allow_remote_imports` | Default `false`. `true` does not insert a CDN; it only stops rejecting `http(s)` specifiers. |
| `jsx_runtime` | `automatic` or `classic`. |
| `jsx_import_source` | Package used by the automatic JSX runtime. Default `react`. |

## Request and response

`Request` exposes `method`, `path`, `query_params`, `path_params`, `headers`, `json()`, `form()`, and uploaded files. `Response` builders live on `limbo.http.Response`: `text`, `json`, `html`, redirects, and iterables for chunked bodies.

`HTTPError(status, message)` becomes that status.

## Data

```python
from limbo import Migrator, open_database

db = open_database("app.sqlite")
Migrator(db, [Migration(1, "up.sql", "down.sql")]).apply()
```

`postgres://` and `mysql://` raise `DatabaseURLError`.

## Build and tunnel

```python
from limbo import build_project, TunnelSupervisor
```

`build_project` writes hashed files, `manifest.json`, and integrity hashes. Installed browser ESM (and CommonJS transformed by local esbuild) is copied into the output. Missing packages stay external and are named in warnings. Code splitting raises `BuildError`.

`TunnelSupervisor` builds `cloudflared` arguments and redacts logs. It does not download a binary. Destructive delete still requires `--confirm`.

## Capability matrix

```python
from limbo import capability_matrix
capability_matrix()
```

`assert_no_blanket_claim()` fails if a framework without an integration test is marked `supported`.
