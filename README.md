# Spotify API Proxy - Tag-Source for MP3tag & Beyond

> **Disclaimer**
> This README started life as an AI‑generated skeleton. I have subsequently edited it by hand while experimenting with **"vibe‑programming"**. The FastAPI proxy itself took ~10 % of the total time; the remaining ~90 % was spent wrestling with MP3tag’s home‑brewed tag‑source DSL-proof that generative tools stumble when the target language is niche or undocumented. Expect rough edges and feel free to improve them.

## Why This Exists

- **One-stop metadata** Spotify exposes rich album / artist / track info.  
- **MP3tag speaks its own DSL** ...which the AI "vibe" utterly mangled. I reverted to manual trial–and–error, so the `.src` files you see are the hard-won, human-verified versions.  
- **Async FastAPI proxy** A thin layer that signs requests, paginates tracks, and exposes clean endpoints for any metadata client (sideload for MP3tag, beets, custom scripts).


---

## Table of Contents

0. [Pre-Requisites](#pre-requisites)
1. [Quick-Start](#quick-start)
2. [Environment & Dependencies](#environment--dependencies)
3. [Running the Server](#running-the-server)
4. [MP3tag Integration](#mp3tag-integration)
5. [Project Layout](#project-layout)
6. [Credits & Inspiration](#credits--inspiration)
7. [Contributing](#contributing)
8. [License](#license)

---

## Quick-Start

* using [uv](https://github.com/astral-sh/uv) for python package management now

```bash
git clone https://github.com/jryaonj/spotify-mp3tag-proxy.git
cd spotify-mp3tag-proxy
python -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -U uv                                      # Modern drop-in for pip+virtualenv
uv pip install -r requirements.txt
cp .env_example .env                                    # Fill in CLIENT_ID/CLIENT_SECRET
uvicorn main:app --host 127.0.0.1 --port 12880 --env-file .env 
# adding --reload when you want to debug, 
```

The FastAPI docs live at `http://127.0.0.1:12880/docs`.

---

## Environment & Dependencies

| Variable        | Purpose                      |
| --------------- | ---------------------------- |
| `CLIENT_ID`     | Spotify App client ID        |
| `CLIENT_SECRET` | Spotify App client secret    |
| `PORT`          | Optional – default **12880** |

- **Python 3.12 or higher**
- Environment variables:
  - `CLIENT_ID`: Your Spotify API client ID.
  - `CLIENT_SECRET`: Your Spotify API client secret.
  - `PORT`: (Optional) Port number for the API server; defaults to 8000.
- Dependencies:
  - [FastAPI](https://fastapi.tiangolo.com/)
  - [httpx](https://www.python-httpx.org/)
  - [spotipy](https://spotipy.readthedocs.io/)
  - [python-dotenv](https://github.com/theskumar/python-dotenv)
  - [uvicorn](https://www.uvicorn.org/)
- Python package management:
  - [uv](https://github.com/astral-sh/uv)

> Install all dependencies via `uv sync`
> direct running `uv add fastapi httpx spotipy python-dotenv` inside project directory is also acceptable

---

## Running the Server

```bash
cd spotify-mp3tag-proxy
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port ${PORT:-12880} --env-file .env
```

Endpoints of interest:

| Route                | What It Does                              |
| -------------------- | ----------------------------------------- |
| `/albums/{id}`       | Minimal Spotify pass-through              |
| `/expand/album/{id}` | Full track list with pagination/ISRC/etc. |
| `/tracks/{id}`       | Single-track lookup                       |

---

## MP3tag Integration

MP3tag uses a proprietary *Tag Source* DSL (`.src` files). The repo ships two fully-tested sources:

| File                                   | Typical Use-Case                          |
| -------------------------------------- | ----------------------------------------- |
| `Spotify API Proxy#Album + Artist.src` | Search by album *and* (primary) artist    |
| `Spotify API Proxy#Album ID.src`       | Fast lookup when you already have the URI |

Key mapping cheatsheet (see **json_idtag_example**):

| MP3tag Field  | JSON Path                           |
| ------------- | ----------------------------------- |
| `ALBUM`       | `.name`                             |
| `ALBUMARTIST` | `.artists[0].name`                  |
| `TITLE`       | `.tracks.items[].name`              |
| `TRACK`       | `index + 1`                         |
| `TRACKTOTAL`  | `.total`                            |
| `YEAR`        | `.release_date`                     |
| ...           | ...                                 |

### Troubleshooting the DSL

1. **Nested arrays**? Use `json_foreach_next` sparingly-prefetch and flatten in Python when possible.
2. **Rate limits**? The proxy batches 50-item pages; MP3tag loops until complete.
3. **Edge cases**? Compilations, multi-disc releases, and market-restricted editions are covered in `merge_dict.py`.

---

## Project Layout

```
.env_example
.gitignore
.python-version
json_idtag_example     # real-world mapping walkthrough
main.py               # FastAPI app
merge_dict.py         # safe-merge utils for nested dicts/lists
resp_mp3tag.json      # example response on /mp3tag/
resp_origin.json      # example response on origin spotify api
Spotify#Album + Artist.src
Spotify#Album ID.src
README.md             # you’re reading it
pyproject.toml        # optional modern build/dep spec
```

---

## Credits & Inspiration
- **spotify_api_proxy_wss_mp3tag**  
  <https://github.com/aorinngoDo/spotify_api_proxy_wss_mp3tag> 
  The original minimal proxy + MP3tag web-source scripts that proved the idea.  
  Portions of its token-refresh logic and early `.src` field mappings seeded this rewrite.

---

## Contributing

* **Issues & PRs welcome** - especially battle-tested improvements to the `.src` templates.
* Please keep commit messages factual; vibe-programming stories belong in comments or docs 😄.

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for full text.
