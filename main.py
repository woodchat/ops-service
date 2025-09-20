from fastapi import FastAPI
import requests

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/ask")
def ask(q: str):
    try:
        return {"status" : "yeh"}
    except Exception as e:
        return {"error": str(e)}
