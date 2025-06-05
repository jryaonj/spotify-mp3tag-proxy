"""
main.py ──────────────────────────────────────────────────────
Dependencies:  pip install fastapi[all] httpx spotipy python-dotenv
Environment:   CLIENT_ID  CLIENT_SECRET  (generated from Spotify Developer Console)
              PORT=8000 (optional)
Start:         uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
"""
import os, asyncio, time
from itertools import islice
from typing import List

import httpx
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from fastapi import FastAPI, Request, HTTPException, Response, Query
from fastapi.responses import JSONResponse

from merge_dict import merge_missing_props_by_id

# ─── Environment & Globals ────────────────────────────────────────────
PORT = int(os.getenv("PORT", 8000))
CID  = os.getenv("CLIENT_ID")
CSC  = os.getenv("CLIENT_SECRET")
if not CID or not CSC:
    raise RuntimeError("CLIENT_ID and CLIENT_SECRET environment variables must be set")

SPOTIFY_API_ROOT = "https://api.spotify.com"
MAX_PAR = 15                     # Concurrency threshold for track requests

# For Python 3.11 compatibility for batched
# try:
#     from itertools import batched
# except ImportError:
def batched(it, n):
    it = iter(it)
    while batch := list(islice(it, n)):
        yield batch

# ─── FastAPI init ───────────────────────────────────────────
app = FastAPI(title="Spotify Proxy + Album Expander")

# Global httpx connection pool
_client: httpx.AsyncClient | None = None

# Wrapper for Spotipy (synchronous API)
sp: spotipy.Spotify | None = None

@app.on_event("startup")
async def startup() -> None:
    global _client, sp
    _client = httpx.AsyncClient(base_url=SPOTIFY_API_ROOT, timeout=30)

    auth = SpotifyClientCredentials(client_id=CID, client_secret=CSC)
    sp = spotipy.Spotify(auth_manager=auth, requests_timeout=15, retries=3)

@app.on_event("shutdown")
async def shutdown() -> None:
    await _client.aclose()        # type: ignore[arg-type]

# ─── Tool: Get / Refresh token (for httpx transparent proxy) ───────
_token = ""; _exp = 0
async def bearer() -> str:
    return sp.auth_manager.get_access_token(as_dict=False)   # Spotipy ≥2.23

    # global _token, _exp
    # if _token and _exp - 60 > time.time():     # Refresh one minute ahead
    #     return _token
    #
    # basic = httpx.BasicAuth(CID, CSC)
    # data  = {"grant_type":"client_credentials"}
    # r = await _client.post("/api/token", auth=basic, data=data)
    # if r.status_code != 200:
    #     raise RuntimeError(f"Token refresh failed: {r.text}")
    #
    # payload = r.json()
    # _token, _exp = payload["access_token"], time.time() + payload["expires_in"]
    # return _token

# ─── 1) Transparent Proxy: /v1/... ─────────────────────────────────────
@app.api_route("/v1/{full_path:path}", methods=["GET","POST","PUT","DELETE","PATCH"])
async def proxy(req: Request, full_path: str) -> Response:
    """
    Equivalent to the official Spotify API.
    - Retains the original QueryString, Body, and Headers.
    - Only appends `Authorization: Bearer <token>`.
    """
    qs   = f"?{req.query_params}" if req.query_params else ""
    url  = f"/v1/{full_path}{qs}"
    hdrs = {"Authorization": f"Bearer {await bearer()}"}

    body = await req.body()
    method = req.method.lower()
    httpx_call = getattr(_client, method)
    if req.method in {"GET", "DELETE"} or not body:
        r = await _client.request(req.method, url, headers=hdrs)
    else:
        r = await _client.request(req.method, url, headers=hdrs, content=body)

    # Filter hop-by-hop headers
    skip = {"content-encoding", "transfer-encoding", "content-length", "connection"}
    out_headers = {k: v for k, v in r.headers.items() if k.lower() not in skip}

    return Response(content=r.content,
                    status_code=r.status_code,
                    headers=out_headers,
                    media_type=r.headers.get("content-type"))

# ─── 2) Deep Expansion: /mp3tag/album/{id} ────────────────────────
@app.get("/mp3tag/album/{album_id}")
def expand_album(
    album_id: str,
    market: str | None = Query(None, pattern="^[A-Za-z]{2}$")   # e.g. ?market=US
) -> JSONResponse:
    """
    - Paginate album tracks to complete them.
    - Use batch API to fill in complete information for each track.
    """
    try:
        album = sp.album(album_id, market=market)               # Pass-through
    except spotipy.SpotifyException as e:
        raise HTTPException(status_code=e.http_status, detail=e.msg)

    tracks = album["tracks"]
    items: List[dict] = tracks["items"]
    total = tracks["total"]
    limit = tracks["limit"]          # Currently fixed at 50 by Spotify

    # --- Complete pagination ---
    for off in range(limit, total, limit):
        items.extend(
            sp.album_tracks(album_id, limit=limit, offset=off, market=market)["items"]
        )  # type: ignore[attr-defined]

    # --- Batch retrieval for complete track details ---
    detailed = []
    for id_batch in batched([t["id"] for t in items], 50):   # Batch API limit is 50
        # print(id_batch)
        detailed.extend(sp.tracks(id_batch, market=market)["tracks"])  # type: ignore[attr-defined]

    # Merge detailed track info into original items
    merge_missing_props_by_id(items, detailed)

    # --- Overwrite & Clean-up ---
    tracks["items"] = items  # or use detailed if preferred
    tracks.pop("next", None)
    tracks.pop("previous", None)
    # tracks["limit"] = len(detailed)
    tracks["limit"] = len(items)

    # Postprocess for MP3tag DSL requirements 
    album["mp3tag"] = dict()
    if album["album_type"] == "compilation":
        album["mp3tag"]["compilation"] = 1

    try:
        album["mp3tag"]["disc_total"] = items[-1]["disc_number"]
    except Exception:
        album["mp3tag"]["disc_total"] = 1

    try:
        if len(album["copyrights"]) != 0:
            album["mp3tag"]["copyright"] = album["copyrights"][0]["text"]
    except Exception:
        pass

    return JSONResponse(content=album)