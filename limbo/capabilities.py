"""Honest capability matrix.

A row is `supported` only when this repository tests the behavior. Optional
tools stay optional. Frameworks without an integration test stay unsupported,
even if a syntax transform exists.
"""
from __future__ import annotations

MATRIX: tuple[dict[str, str], ...] = (
    {"id": "html-css-js", "status": "supported", "evidence": "Browser-native pass-through and production build."},
    {"id": "web-components", "status": "supported", "evidence": "Custom elements are ordinary browser JavaScript; no compiler is required."},
    {"id": "wasm", "status": "supported", "evidence": "WASM assets are copied and served with a WASM MIME type. The browser instantiates them."},
    {"id": "typescript", "status": "optional", "evidence": "Local esbuild adapter in development. Production build refuses .ts files when esbuild is absent."},
    {"id": "jsx-tsx", "status": "optional", "evidence": "Local esbuild transform only. No framework runtime is bundled."},
    {"id": "react", "status": "unsupported", "evidence": "JSX can be transformed when esbuild is installed, but the React runtime is not a verified browser ESM integration."},
    {"id": "preact", "status": "unsupported", "evidence": "No Preact runtime fixture has passed."},
    {"id": "vue", "status": "experimental", "evidence": "Optional local @vue/compiler-sfc dev adapter. Production SFC emit is not claimed."},
    {"id": "svelte", "status": "experimental", "evidence": "Optional local svelte dev adapter. Svelte component-state HMR is not implemented."},
    {"id": "solid", "status": "unsupported", "evidence": "No Solid adapter or fixture."},
    {"id": "lit", "status": "unsupported", "evidence": "Lit is browser JavaScript, but Limbo has no dedicated fixture, so it is not declared supported."},
    {"id": "alpine", "status": "native-script", "evidence": "No compiler. Serve a script you install yourself. Limbo does not bundle Alpine."},
    {"id": "htmx", "status": "native-script", "evidence": "No compiler. Serve a script you install yourself. Limbo does not bundle htmx."},
    {"id": "angular", "status": "unsupported", "evidence": "Angular's compiler and build are not integrated."},
    {"id": "astro", "status": "unsupported", "evidence": "No Astro adapter or starter test."},
    {"id": "qwik", "status": "unsupported", "evidence": "No Qwik adapter or starter test."},
    {"id": "next", "status": "unsupported", "evidence": "Local process supervisor exists. Next.js itself is not integration-tested."},
    {"id": "nuxt", "status": "unsupported", "evidence": "Local process supervisor exists. Nuxt itself is not integration-tested."},
    {"id": "sveltekit", "status": "unsupported", "evidence": "Local process supervisor exists. SvelteKit itself is not integration-tested."},
    {"id": "remix", "status": "unsupported", "evidence": "Local process supervisor exists. Remix itself is not integration-tested."},
    {"id": "sqlite", "status": "supported", "evidence": "Serialized connection, transactions, and checksummed migrations."},
    {"id": "postgres", "status": "unsupported", "evidence": "URL scheme is rejected. No driver and no integration suite."},
    {"id": "mysql", "status": "unsupported", "evidence": "URL scheme is rejected. No driver and no integration suite."},
    {"id": "cloudflare-quick-tunnel", "status": "optional", "evidence": "Supervises an existing cloudflared process. Limbo does not download it or implement the protocol."},
    {"id": "cloudflare-named-tunnel", "status": "optional", "evidence": "Runs only with a user-supplied config. Destructive delete requires --confirm. Credentials are redacted from logs."},
)


def capability_matrix() -> list[dict[str, str]]:
    return [dict(row) for row in MATRIX]


def assert_no_blanket_claim(rows: list[dict[str, str]] | None = None) -> None:
    data = rows if rows is not None else capability_matrix()
    supported = {row["id"] for row in data if row["status"] == "supported"}
    forbidden = {"react", "preact", "angular", "astro", "qwik", "next", "nuxt", "sveltekit", "remix", "postgres", "mysql"}
    overlap = supported & forbidden
    if overlap:
        raise AssertionError(f"Capability matrix claims unsupported technologies: {sorted(overlap)}")
