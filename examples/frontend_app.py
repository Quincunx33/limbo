from limbo import Limbo

app = Limbo(name="Limbo Vue preview")
app.enable_frontend("examples/frontend")


@app.get("/")
def home(req):
    tags = app.frontend_script("src/main.js")
    return app.html(
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>Limbo frontend preview</title></head><body><div id='app'></div>" + tags + "</body></html>"
    )


if __name__ == "__main__":
    app.run(port=8000)
