"""
main.py ──────────────────────────────────────────────────────
Dependencies:  pip install fastapi[all] httpx spotipy python-dotenv
Environment:   CLIENT_ID  CLIENT_SECRET  (generated from Spotify Developer Console)
              PORT=8000 (optional)
Start:         uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
"""
from pprint import pprint
import os, asyncio, time
from itertools import islice
from typing import List

import httpx
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from fastapi import FastAPI, APIRouter, Request, HTTPException, Response, Query
from fastapi.responses import JSONResponse

from merge_dict import merge_missing_props_by_id
from fastapi.responses import StreamingResponse
import csv
from io import StringIO

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

# Add this helper function after the existing imports
def inferred_artist_genres(artist_id: str, sp) -> list[str]:
    """
    Fetch genres for a given artist and return a sorted list.
    """
    artist = sp.artist(artist_id)   # full Artist object
    genres = artist.get("genres", [])
    return [genre.capitalize() for genre in genres]

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

    # After getting the album data, fetch genres from album artists
    # Fetch and merge all artist genres efficiently
    artist_ids = [artist["id"] for artist in album["artists"]]
    genres = []
    seen = set()
    for artist_id in artist_ids:
        for genre in inferred_artist_genres(artist_id, sp):
            if genre not in seen:
                genres.append({"text": genre})
                seen.add(genre)
    album["mp3tag"]["genres"] = genres

    return JSONResponse(content=album)

spmusic_router = APIRouter(prefix="/spmusic", tags=["spmusic"])

@spmusic_router.get("/albums/by-artist/{artist_name}")
async def get_artist_albums(
    artist_name: str,
    down: bool = Query(False, description="Return CSV if true, else JSON"),
    appears: bool = Query(False, description="Include appears_on albums"),
    compilation: bool = Query(True, description="Include compilations"),
):
    """
    Get all albums belonging to an artist by their name.
    Returns expanded album information including tracks and genres, as JSON or CSV.
    """
    album_types = ["album", "single"]
    if appears:
        album_types.append("appears_on")
    if compilation:
        album_types.append("compilation")
    
    album_type_str = ",".join(album_types)

    try:
        # Search for the artist
        results = sp.search(artist_name, type='artist', limit=1)
        if not results['artists']['items']:
            raise HTTPException(status_code=404, detail=f"Artist '{artist_name}' not found")
        
        artist = results['artists']['items'][0]
        
        # Get all albums for the artist
        albums = []
        offset = 0
        limit = 50  # Spotify API limit
        
        # as there is bug in different type search
        # we need to iterate over each album type
        albums = []
        for album_type in album_types:
            offset = 0
            while True:
                results = sp.artist_albums(
                    artist['id'],
                    include_groups=album_type,
                    country=None,  # Use None to get all markets
                    limit=limit,
                    offset=offset,
                )
                if not results['items']:
                    break
                albums.extend(results['items'])
                if len(results['items']) < limit:
                    break
                offset += limit

        if down:
            # Prepare CSV with UTF-8 BOM for Excel compatibility
            output = StringIO()
            output.write('\ufeff')  # Add UTF-8 BOM
            writer = csv.DictWriter(
                output,
                fieldnames=["release_date", "album_type", "albumartist", "name", "id", "total_tracks", "external_url"],
                extrasaction='ignore'
            )
            writer.writeheader()
            for album in albums:
                writer.writerow({
                    "release_date": album.get("release_date"),
                    "album_type": album.get("album_type"),
                    "albumartist": artist_name,
                    "name": album.get("name"),
                    "id": album.get("id"),
                    "total_tracks": album.get("total_tracks"),
                    "external_url": album.get("external_urls", {}).get("spotify"),
                })
            output.seek(0)
            safe_artist_name = artist_name.replace(' ', '_')
            return StreamingResponse(
                output,
                media_type="text/csv; charset=utf-8",
                headers={
                    "Content-Disposition": f"attachment; filename={safe_artist_name}_spotify_albums.csv",
                    "Content-Type": "text/csv; charset=utf-8"
                }
            )
        else:
            return JSONResponse(content={
                "artist": artist,
                "albums": albums
            })
        
    except spotipy.SpotifyException as e:
        raise HTTPException(status_code=e.http_status, detail=e.msg)

# ─── 3) YouTube Music Album Finder ────────────────────────────────
# only artist full album mode, to avoid iterated searches

from ytmusicapi import YTMusic
# from rapidfuzz import process, fuzz

ytmusic_cookie_path = "ytmusic_cookie.json"
if os.path.exists(ytmusic_cookie_path):
    yt = YTMusic(ytmusic_cookie_path)
else:
    yt = YTMusic() # no auth needed for public catalogue      

ytmusic_router = APIRouter(prefix="/ytmusic", tags=["ytmusic"])

def _artists(hit):
    # Helper to extract artist(s) from YTMusic search result
    if "artist" in hit and isinstance(hit["artist"], str):
        return hit["artist"]
    if "artists" in hit and isinstance(hit["artists"], list):
        return ", ".join(a["name"] for a in hit["artists"])
    return ""

def browse_to_urls(browse_id: str) -> dict:
    # Case 1 – already a playlist-style ID (VL…, PL…): just use it
    if browse_id.startswith(('VL', 'PL')):
        url = f"https://music.youtube.com/playlist?list={browse_id}"
        return {"playlist_url": url, "browse_url": url.replace("playlist?list=", "browse/")}

    # Case 2 – album/single page (MPREb_…)
    info = yt.get_album(browse_id)          # one network trip :contentReference[oaicite:0]{index=0}
    plist = info.get("audioPlaylistId")     # present for >99 % of albums :contentReference[oaicite:1]{index=1}
    if not plist:
        raise ValueError("Album has no audioPlaylistId (rare but possible).")
    return {
        "playlist_url": f"https://music.youtube.com/playlist?list={plist}",
        "browse_url":   f"https://music.youtube.com/browse/{browse_id}"
    }

@ytmusic_router.get("/albums/by-artist/{artist_name}")
async def ytmusic_albums_by_artist(
    artist_name: str,
    down: bool = Query(False, description="Return CSV if true, else JSON"),
):
    """
    Search YouTube Music albums by artist name.
    Returns JSON or CSV depending on `csv` query param.
    """
    hits = yt.search(artist_name, filter="albums", limit=250)
    albums = []
    for h in hits:
        album = {
            "title": h.get("title"),
            "artist": _artists(h),
            "year": h.get("year"),
            "browseId": h.get("browseId"),
            "audioPlaylistId": h.get("audioPlaylistId"),
            "trackCount": h.get("trackCount"),
        }
        albums.append(album)

    if down:
        output = StringIO()
        output.write('\ufeff')
        writer = csv.DictWriter(
            output,
            fieldnames=["title", "artist", "year", "browseId", "audioPlaylistId", "trackCount", "playlist_url", "browse_url"],
            extrasaction='ignore'
        )
        writer.writeheader()
        for album_batch in batched(albums, 50):
            batch_with_urls = []
            for album in album_batch:
                urls = browse_to_urls(album["browseId"]) if album.get("browseId") else {"playlist_url": "", "browse_url": ""}
                batch_with_urls.append({
                    **album,
                    "playlist_url": urls.get("playlist_url", ""),
                    "browse_url": urls.get("browse_url", ""),
                })
            for album in batch_with_urls:
               writer.writerow(album)
        output.seek(0)
        safe_artist_name = artist_name.replace(' ', '_')
        return StreamingResponse(
            output,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f"attachment; filename={safe_artist_name}_ytmusic_albums.csv",
                "Content-Type": "text/csv; charset=utf-8"
            }
        )
    return {"artist": artist_name, "albums": albums}

# Register router
app.include_router(ytmusic_router)
app.include_router(spmusic_router)