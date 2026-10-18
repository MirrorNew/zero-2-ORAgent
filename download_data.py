"""Download and verify the complete public FrontierOR release (no code is run)."""

import hashlib
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
    lfs = entry.get("lfs")
    expected_hash = lfs["sha256"] if lfs else entry["blobId"]

    def checksum(path):
        digest = hashlib.sha256() if lfs else hashlib.sha1()
        if not lfs:
            digest.update(f"blob {entry['size']}\0".encode())
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    if target.exists() and target.stat().st_size == entry["size"]:
        if checksum(target) == expected_hash:
            return entry["size"]
    url = f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{revision}/{relative.as_posix()}"
    temporary = target.with_name(target.name + ".partial")
    for attempt in range(4):
        try:
            offset = temporary.stat().st_size if temporary.exists() else 0
            if offset >= entry["size"]:
                if offset == entry["size"] and checksum(temporary) == expected_hash:
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
            if temporary.stat().st_size != entry["size"] or checksum(temporary) != expected_hash:
                raise ValueError(f"Size/checksum mismatch: {relative}")
            temporary.replace(target)
            return entry["size"]
        except (requests.RequestException, ValueError) as error:
            print(f"Retry {attempt + 1}: {relative}: {type(error).__name__}", flush=True)
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    response = requests.get(f"https://huggingface.co/api/datasets/{REPOSITORY}/revision/{REVISION}?blobs=true", timeout=60)
    response.raise_for_status()
    release = response.json()
    files = release["siblings"]
    # Download the first learning task and every task's small contracts first.
    contracts = {"problem_description.txt", "instance_schema.json", "solution_schema.json", "feasibility_check.py"}
    def priority(item):
        name = item["rfilename"]
        group = 2
        if name.startswith(("tasks/bergman2019/", "metadata/")):
            group = 0
        elif Path(name).name in contracts:
            group = 1
        return group, item["size"]
    files.sort(key=priority)
    # Start large instances early while other workers finish the small files.
    core = [item for item in files if priority(item)[0] < 2]
    small = [item for item in files if priority(item)[0] == 2 and item["size"] < 10_000_000]
    large = [item for item in files if priority(item)[0] == 2 and item["size"] >= 10_000_000]
    large.sort(key=lambda item: item["size"], reverse=True)
    files = core
    for start in range(0, len(small), 100):
        if large:
            files.append(large.pop(0))
        files.extend(small[start:start + 100])
    files.extend(large)
    manifest = {"repository": REPOSITORY, "revision": release["sha"],
                "destination": str(DESTINATION.resolve()), "file_count": len(files),
                "total_bytes": sum(item["size"] for item in files),
                "complete": False, "files": files}
    manifest_path = DESTINATION / "download_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in manifest.items() if key != "files"}), flush=True)
    downloaded = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        for index, size in enumerate(pool.map(lambda item: download_file(item, release["sha"]), files), 1):
            downloaded += size
            if index % 25 == 0 or index == len(files):
                print(f"Verified {index}/{len(files)} files; {downloaded:,} bytes", flush=True)
    manifest["complete"] = True
    manifest["verified_bytes"] = downloaded
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("COMPLETE: every release file passed its official size and checksum.", flush=True)


if __name__ == "__main__":
    main()
