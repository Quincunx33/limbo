"""Local preview of Limbo 0.9.1. Binds the public demo port."""
from __future__ import annotations

import json
from pathlib import Path

from limbo import Limbo, capability_matrix
from limbo import __version__

ROOT = Path(__file__).resolve().parent / "preview_frontend"
app = Limbo(name="Limbo")
app.enable_frontend(ROOT)

PHASES = (
    ("0", "Complete", "Package, CI for 3.10–3.13, Linux and Windows."),
    ("1", "Complete", "HTTP parsing, routing, deadlines, shutdown."),
    ("2", "Complete", "Static files, ranges, middleware, gzip."),
    ("3", "Engineering-complete", "Uploads, sessions, CSRF, WebSocket policy, tasks."),
    ("4", "Engineering-complete", "limbo.toml, CLI, starters. add/remove never install."),
    ("5", "Verified slice", "Local import maps, /_limbo_modules, esbuild for CommonJS."),
    ("6", "Browser-native build", "Hashed assets. Installed ESM is copied. No code splitting."),
    ("7", "Honest matrix", "Preact, Lit and React are optional local runtimes. SSR frameworks are not."),
    ("8", "SQLite only", "Checksummed migrations. Postgres and MySQL URLs are rejected."),
    ("9", "Optional tunnel", "Supervises an existing cloudflared. No download, no protocol."),
    ("10", "Open", "Docs, fuzz and benchmarks are in tree. 1.0 is not signed off."),
)


def _rows(items: list[tuple[str, str, str]]) -> str:
    return "".join(
        "<tr><td>{}</td><td><span class='state'>{}</span></td><td>{}</td></tr>".format(*row)
        for row in items
    )


def _page() -> str:
    matrix = [
        (row["id"], row["status"], row["evidence"])
        for row in capability_matrix()
    ]
    tags = app.frontend_script("src/main.js")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Limbo {__version__}</title>
<style>
  :root {{
    --bg: #10120e;
    --ink: #efe7d2;
    --muted: #b4ad9a;
    --line: #2a2e26;
    --card: #181b15;
    --signal: #d6ff4a;
    --warn: #e2b15a;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; background: var(--bg); color: var(--ink); }}
  body {{
    font: 16px/1.45 "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
    min-height: 100vh;
  }}
  header, main {{ width: min(920px, calc(100% - 32px)); margin: 0 auto; }}
  header {{ padding: 36px 0 12px; }}
  .mark {{
    display: flex; justify-content: space-between; gap: 16px; align-items: baseline;
    border-bottom: 1px solid var(--line); padding-bottom: 16px;
  }}
  h1 {{ font-weight: 500; letter-spacing: -0.04em; font-size: 42px; margin: 0; }}
  .ver {{ color: var(--signal); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; }}
  p.lead {{ color: var(--muted); max-width: 62ch; }}
  section {{ margin: 28px 0; }}
  h2 {{
    font: 12px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    letter-spacing: 0.14em; text-transform: uppercase; color: var(--warn); margin: 0 0 10px;
  }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card); }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
  th {{
    font: 11px/1.2 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); font-weight: 500;
  }}
  td:first-child {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 13px; white-space: nowrap; }}
  .state {{ color: var(--signal); }}
  form {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  input, button {{
    font: 15px/1.2 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    border: 1px solid var(--line); background: var(--card); color: var(--ink);
    padding: 12px 14px; border-radius: 0;
  }}
  button {{ background: var(--signal); color: #14160f; cursor: pointer; border-color: var(--signal); }}
  button:hover {{ filter: brightness(1.05); }}
  pre {{
    margin: 12px 0 0; padding: 12px; background: var(--card); border: 1px solid var(--line);
    overflow: auto; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }}
  #runtime {{ min-height: 1.4em; }}
  footer {{ color: var(--muted); font-size: 13px; padding-bottom: 48px; }}
  @media (max-width: 640px) {{
    h1 {{ font-size: 34px; }}
    td:first-child {{ white-space: normal; }}
  }}
</style>
</head>
<body>
<header>
  <div class="mark">
    <h1>Limbo</h1>
    <div class="ver">v{__version__} · not 1.0</div>
  </div>
  <p class="lead">Python-first local framework. This page is the running server: routes, a local import map, and the capability matrix from the package itself. No CDN.</p>
</header>
<main>
  <section>
    <h2>Live module</h2>
    <div id="runtime">Loading local module…</div>
    {tags}
  </section>
  <section>
    <h2>Try the API</h2>
    <form id="echo">
      <input name="message" aria-label="Message" value="from the preview" autocomplete="off">
      <button type="submit">POST /api/echo</button>
    </form>
    <pre id="out">waiting</pre>
  </section>
  <section>
    <h2>Phases</h2>
    <table>
      <thead><tr><th>Phase</th><th>State</th><th>What is true</th></tr></thead>
      <tbody>{_rows(list(PHASES))}</tbody>
    </table>
  </section>
  <section>
    <h2>Capability matrix</h2>
    <table>
      <thead><tr><th>Id</th><th>Status</th><th>Evidence</th></tr></thead>
      <tbody>{_rows(matrix)}</tbody>
    </table>
  </section>
  <footer>Independent security review is still open. macOS is unverified. Angular, Next, Nuxt, SvelteKit, Remix, Astro, Qwik, PostgreSQL and MySQL stay unsupported.</footer>
</main>
<script>
document.getElementById("echo").addEventListener("submit", async (event) => {{
  event.preventDefault();
  const message = new FormData(event.currentTarget).get("message");
  const response = await fetch("/api/echo", {{
    method: "POST",
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{message}})
  }});
  document.getElementById("out").textContent = JSON.stringify(await response.json(), null, 2);
}});
</script>
</body>
</html>"""


@app.get("/")
def home(request):
    return app.html(_page())


@app.get("/api/matrix")
def matrix(request):
    return {"version": __version__, "capabilities": capability_matrix()}


@app.post("/api/echo")
def echo(request):
    payload = request.json()
    if not isinstance(payload, dict):
        return app.json({"error": "JSON object required"}, status=400)
    return {"ok": True, "echo": payload}


def main() -> None:
    app.run(host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
