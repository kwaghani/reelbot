"""Temporary phone-test entry point: only previously registered devices may connect.

Run with the same explicit DATABASE_URL as the local test service. This does not
open registration or expose the database when an HTTPS preview tunnel is used.
"""
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from api.main import app, require_user

@app.middleware('http')
async def existing_devices_only(request, call_next):
    if request.url.path != '/healthz':
        try:
            await run_in_threadpool(require_user, request.headers.get('authorization', ''))
        except HTTPException as error:
            return JSONResponse({'detail': error.detail}, status_code=error.status_code)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'private, no-store'
    return response
