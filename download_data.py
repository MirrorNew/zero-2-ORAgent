"""Download the complete public FrontierOR release; resume partial files."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests


REPOSITORY = "frontieror/FrontierOR"
REVISION = "609a71b64f33942ba2392a4835f05d56503961c2"
DESTINATION = Path(__file__).parent / "data" / "FrontierOR"
SESSIONS = threading.local()


def download_file(entry, revision):
    if not hasattr(SESSIONS, "http"):
        SESSIONS.http = requests.Session()
    relative = Path(entry["rfilename"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe dataset path: {relative}")
    target = DESTINATION / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == entry["size"]:
        return entry["size"]
    url = f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{revision}/{relative.as_posix()}"
    temporary = target.with_name(target.name + ".partial")
    for attempt in range(4):
        try:
            offset = temporary.stat().st_size if temporary.exists() else 0
            if offset >= entry["size"]:
                if offset == entry["size"]:
                    temporary.replace(target)
                    return entry["size"]
                offset = 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            with SESSIONS.http.get(url, headers=headers, stream=True, timeout=(30, 90)) as response:
                response.raise_for_status()
                resume = offset > 0 and response.status_code == 206
                if resume and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                    raise ValueError("Server returned an unexpected byte range")
                with temporary.open("ab" if resume else "wb") as output:
                    for chunk in response.iter_content(4 * 1024 * 1024):
                        output.write(chunk)
            if temporary.stat().st_size != entry["size"]:
                raise ValueError(f"File length mismatch: {relative}")
            temporary.replace(target)
            return entry["size"]
        except (requests.RequestException, ValueError) as error:
            print(f"Retry {attempt + 1}: {relative}: {type(error).__name__}", flush=True)
            if attempt == 3:
                raise
            response = getattr(error, "response", None)
            delay = 2 ** attempt
            if response is not None and response.status_code == 429:
                delay = int(response.headers.get("Retry-After", "60")) + 1
                print(f"Rate limit: waiting {delay} seconds", flush=True)
            time.sleep(delay)


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    response = requests.get(f"https://huggingface.co/api/datasets/{REPOSITORY}/revision/{REVISION}?blobs=true", timeout=60)
    response.raise_for_status()
    release = response.json()
    files = [{"rfilename": item["rfilename"], "size": item["size"]} for item in release["siblings"]]
    files.sort(key=lambda item: item["size"])  # Small contracts and instances arrive first.
    manifest = {"repository": REPOSITORY, "revision": release["sha"],
                "destination": str(DESTINATION.resolve()), "file_count": len(files),
                "total_bytes": sum(item["size"] for item in files),
                "complete": False, "files": files}
    manifest_path = DESTINATION / "download_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in manifest.items() if key != "files"}), flush=True)
    downloaded = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for index, size in enumerate(pool.map(lambda item: download_file(item, release["sha"]), files), 1):
            downloaded += size
            if index % 25 == 0 or index == len(files):
                print(f"Completed {index}/{len(files)} files; {downloaded:,} bytes", flush=True)
    manifest["complete"] = True
    manifest["completed_bytes"] = downloaded
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("COMPLETE: every release file has its expected length.", flush=True)


if __name__ == "__main__":
    main()
