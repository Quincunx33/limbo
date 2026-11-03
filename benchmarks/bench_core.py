"""Local routing micro-benchmark. A sample, not a certification."""
import json
import time

from limbo.router import Router


def main() -> None:
    router = Router()
    for index in range(200):
        router.add("GET", f"/item/{index}/{{id}}", lambda request: None, name=f"route-{index}")
    started = time.perf_counter()
    for _ in range(2000):
        route, params, _allowed = router.match("GET", "/item/50/abc")
        if route is None or params.get("id") != "abc":
            raise SystemExit("benchmark route did not match")
    elapsed = time.perf_counter() - started
    print(json.dumps({"matches": 2000, "routes": 200, "seconds": round(elapsed, 4)}))


if __name__ == "__main__":
    main()
