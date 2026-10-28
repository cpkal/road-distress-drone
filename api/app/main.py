from fastapi import FastAPI

app = FastAPI(title="web-drone API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
