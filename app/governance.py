import time
from typing import Dict
from fastapi import HTTPException
from app.metrics import RATE_LIMIT_EXCEEDED

# Simple in-memory rate limiting (use Redis in production)
user_requests: Dict[str, list] = {}

# Rate limits per user (requests per minute)
RATE_LIMITS = {
    "alice": 30,
    "bob": 1,
    "premium": 100,
    "default": 10  # For unknown users
}

def get_user_limit(user: str) -> int:
    """Get rate limit for user (per minute)"""
    return RATE_LIMITS.get(user, RATE_LIMITS["default"])

def check_rate_limit(user: str) -> bool:
    """Check if user has exceeded rate limit in the last 60 seconds"""
    current_time = time.time()
    limit = get_user_limit(user)
    
    # Initialize user tracking
    if user not in user_requests:
        user_requests[user] = []
    
    # Keep only requests within the last 60 seconds
    cutoff_time = current_time - 60
    user_requests[user] = [
        req_time for req_time in user_requests[user]
        if req_time > cutoff_time
    ]
    
    # If limit exceeded, record metric and deny
    if len(user_requests[user]) >= limit:
        RATE_LIMIT_EXCEEDED.labels(user=user).inc()
        return False
    
    # Otherwise record this request
    user_requests[user].append(current_time)
    return True

def enforce_rate_limit(user: str):
    """Enforce rate limit, raise exception if exceeded"""
    if not check_rate_limit(user):
        current_requests = len(user_requests.get(user, []))
        limit = get_user_limit(user)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {current_requests}/{limit} requests per minute"
        )

def get_user_stats(user: str) -> Dict:
    """Get current usage stats for user"""
    current_time = time.time()
    cutoff_time = current_time - 60  # last 1 minute window
    
    if user not in user_requests:
        user_requests[user] = []
    
    # Count requests in the last 60 seconds
    recent_requests = [
        req_time for req_time in user_requests[user]
        if req_time > cutoff_time
    ]
    
    limit = get_user_limit(user)
    
    return {
        "user": user,
        "requests_last_minute": len(recent_requests),
        "rate_limit": limit,
        "remaining": max(0, limit - len(recent_requests))
    }
