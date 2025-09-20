# app/main.py

import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import generate_latest
import requests
import time
import logging
from typing import Optional
import tiktoken
import os

from app.metrics import (
    record_request, ACTIVE_REQUESTS, MODEL_INFO, 
    TOKENS_PER_SECOND
)
from app.governance import enforce_rate_limit, get_user_stats

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI
app = FastAPI(
    title="LLMOps Ollama Tiny LLM Service",
    version="2.0.0"
)

# Configuration for Ollama Tiny LLM
OLLAMA_URL = "http://ollama:11434/api/generate"  # Fixed endpoint
MODEL_NAME = "tinyllama"
BACKEND = "ollama"

# Initialize tokenizer
try:
    tokenizer = tiktoken.get_encoding("cl100k_base")
except Exception as e:
    logger.warning(f"Could not load tokenizer: {e}")
    tokenizer = None

# Set model info
MODEL_INFO.info({
    'model_name': MODEL_NAME,
    'backend': BACKEND,
    'version': '2.0.0'
})

def count_tokens(text: str) -> int:
    """Count tokens in text"""
    if not text or not tokenizer:
        return len(text.split())  # Fallback to word count
    return len(tokenizer.encode(text))

@app.on_event("startup")
async def startup_event():
    """Initialize service and check Ollama health"""
    logger.info("Starting LLMOps Ollama Tiny LLM service")
    try:
        response = requests.get("http://ollama:11434/api/version", timeout=5)
        logger.info(f"Ollama health check: {response.status_code}")
    except Exception as e:
        logger.error(f"Could not connect to Ollama: {e}")

@app.get("/")
def root():
    """Service information endpoint"""
    return {
        "service": "LLMOps Ollama Tiny LLM Service",
        "version": "2.0.0",
        "backend": BACKEND,
        "model": MODEL_NAME,
        "endpoints": {
            "generate": "/generate",
            "health": "/health",
            "metrics": "/metrics",
            "user_stats": "/users/{user}/stats"
        }
    }

@app.get("/health")
def health():
    """Health check endpoint"""
    try:
        response = requests.get("http://ollama:11434/api/version", timeout=5)
        healthy = response.status_code == 200
    except:
        healthy = False

    status = "healthy" if healthy else "degraded"
    return {
        "status": status,
        "ollama_backend": healthy,
        "model": MODEL_NAME,
        "active_requests": int(ACTIVE_REQUESTS._value._value),
        "timestamp": time.time()
    }

@app.get("/metrics")
def get_metrics():
    """Prometheus metrics endpoint"""
    return PlainTextResponse(
        generate_latest(),
        media_type="text/plain"
    )

@app.get("/users/{user}/stats")
def user_stats(user: str):
    """Get usage statistics for a user"""
    return get_user_stats(user)


# Read Ollama host from environment (fallback to 'ollama')
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "ollama")
OLLAMA_PORT = os.getenv("OLLAMA_PORT", "11434")
OLLAMA_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate"

@app.post("/generate")
async def generate_text(payload: dict):
    """Generate text with Ollama Tiny LLM"""
    user = payload.get("user", "anonymous")
    prompt = payload.get("prompt", "").strip()
    max_tokens = min(payload.get("max_tokens", 50), 200)
    temperature = max(0.0, min(2.0, payload.get("temperature", 0.7)))

    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    # Enforce governance
    enforce_rate_limit(user)

    ACTIVE_REQUESTS.inc()
    start_time = time.time()

    try:
        ollama_payload = {
            "model": MODEL_NAME,
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False
        }

        logger.info(f"Processing request for user {user} -> Ollama: {OLLAMA_URL}")

        response = requests.post(
            OLLAMA_URL,
            json=ollama_payload,
            timeout=60,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()

        # Robust JSON parsing
        text = response.text.strip()
        try:
            result = response.json()
            logger.info(f"result: {result}")
        except ValueError:
            # handle cases where Ollama returns extra newlines/logs
            first_line = text.splitlines()[0]
            result = json.loads(first_line)
        generated_text = None
        # Extract generated text
        if "response" in result and isinstance(result["response"], str):
            generated_text = result["response"].strip()
        elif "completions" in result and len(result["completions"]) > 0:
            generated_text = result["completions"][0].get("text", "").strip()

        if not generated_text:
            raise HTTPException(
                status_code=500,
                detail="No valid response returned from Ollama model"
            )

        end_time = time.time()
        latency = end_time - start_time

        # Count tokens
        input_tokens = count_tokens(prompt)
        output_tokens = count_tokens(generated_text)
        total_tokens = input_tokens + output_tokens

        # Record metrics
        record_request(
            backend=BACKEND,
            user=user,
            model=MODEL_NAME,
            status="200",
            latency=latency,
            tokens_in=input_tokens,
            tokens_out=output_tokens
        )

        logger.info(
            f"Request completed: user={user}, latency={latency:.2f}s, tokens={total_tokens}"
        )

        return {
            "backend": BACKEND,
            "model": MODEL_NAME,
            "user": user,
            "generated_text": generated_text,
            "metrics": {
                "latency_seconds": round(latency, 3),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "tokens_per_second": round(total_tokens / latency, 2) if latency > 0 else 0,
                "temperature": temperature,
                "max_tokens_requested": max_tokens
            }
        }

    except requests.RequestException as e:
        logger.error(f"Ollama request failed: {e}")
        record_request(BACKEND, user, MODEL_NAME, "503", time.time() - start_time)
        raise HTTPException(
            status_code=503,
            detail=f"Model service unavailable: {str(e)}"
        )

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        record_request(BACKEND, user, MODEL_NAME, "500", time.time() - start_time)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )

    finally:
        ACTIVE_REQUESTS.dec()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)



