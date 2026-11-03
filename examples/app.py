from limbo import Limbo

app = Limbo(name="Example")


@app.get("/")
def home(req):
    return app.html("<h1>Limbo demo</h1><p>Try /api/items/7</p>")


@app.get("/api/items/{item_id}")
def get_item(req):
    return {"id": req.path_params["item_id"], "query": req.query_params}


@app.post("/api/items")
def create_item(req):
    return app.json({"accepted": req.json}, status=201)


@app.middleware
def add_security_header(req, next_handler):
    response = next_handler(req)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


if __name__ == "__main__":
    app.run(port=8000)
