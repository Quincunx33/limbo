"""Local micro-benchmarks. A sample, not a certification."""
import json
import tempfile
import time
from pathlib import Path

from limbo import Limbo
from limbo.http import Request
from limbo.router import Router


def bench_router() -> dict:
    router = Router()
    for index in range(200):
        router.add("GET", f"/item/{index}/{{id}}", lambda request: None, name=f"route-{index}")
    started = time.perf_counter()
    for _ in range(2000):
        route, params, _allowed = router.match("GET", "/item/50/abc")
        if route is None or params.get("id") != "abc":
            raise SystemExit("benchmark route did not match")
    return {"name": "router", "matches": 2000, "routes": 200, "seconds": round(time.perf_counter() - started, 4)}


def bench_dispatch() -> dict:
    app = Limbo()

    @app.get("/ping")
    def ping(request):
        return {"ok": True}

    with tempfile.TemporaryDirectory() as temp:
        static = Path(temp)
        (static / "app.js").write_text("export const ready = true;\n", encoding="utf-8")
        app.static("/assets", static)
        started = time.perf_counter()
        for _ in range(500):
            api = app.dispatch(Request("GET", "/ping", {}))
            asset = app.dispatch(Request("GET", "/assets/app.js", {}))
            if api.status != 200 or asset.status != 200:
                raise SystemExit("benchmark dispatch failed")
    return {"name": "dispatch", "requests": 1000, "seconds": round(time.perf_counter() - started, 4)}


def main() -> None:
    print(json.dumps({"router": bench_router(), "dispatch": bench_dispatch()}))


if __name__ == "__main__":
    main()
