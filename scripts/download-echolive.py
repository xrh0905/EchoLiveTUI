"""Download a verified official release into an isolated, reproducible test folder."""
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile


def main():
    release = json.load(urllib.request.urlopen("https://api.github.com/repos/sheep-realms/Echo-Live/releases/latest"))
    asset = next(a for a in release["assets"] if a["name"].endswith(".zip"))
    destination = Path(".reference/releases").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / Path(asset["name"]).name
    with urllib.request.urlopen(asset["browser_download_url"]) as response:
        archive.write_bytes(response.read())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if asset.get("digest") and asset["digest"] != "sha256:" + digest:
        raise ValueError("Release asset SHA256 mismatch")
    extracted = destination / ("Echo-Live-" + release["tag_name"])
    extracted.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            path = (extracted / item.filename).resolve()
            if not path.is_relative_to(extracted):
                raise ValueError("Unsafe archive member")
        bundle.extractall(extracted)
    root = next(p.parent for p in extracted.rglob("live.html") if (p.parent / "config.js").is_file())
    record = {"version": release["tag_name"], "published_at": release["published_at"], "url": asset["browser_download_url"], "sha256": digest, "root": str(root)}
    (destination / "latest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
