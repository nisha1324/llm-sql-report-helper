"""Download the Chinook sample database (a fictional digital music store) into data/.

The file is pinned to release v1.4.5 and checked against a SHA-256, so every
gold answer in eval/ is computed on exactly the same data.
"""
import hashlib
import sys
from pathlib import Path
from urllib.request import urlretrieve

URL = "https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_Sqlite.sqlite"
SHA256 = "bdf635be69850bd3be09c9a2dbeef7ddfb80036bd3ef3381383cd03b61e4a61a"
DEST = Path(__file__).resolve().parent.parent / "data" / "chinook.sqlite"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    DEST.parent.mkdir(exist_ok=True)
    if not DEST.exists():
        print(f"Downloading {URL} ...")
        tmp = DEST.with_suffix(".part")
        urlretrieve(URL, tmp)
        tmp.rename(DEST)
    digest = sha256(DEST)
    if digest != SHA256:
        sys.exit(f"SHA-256 mismatch for {DEST}: got {digest}, expected {SHA256}. Delete it and retry.")
    print(f"OK: {DEST} ({DEST.stat().st_size:,} bytes, SHA-256 verified)")


if __name__ == "__main__":
    main()
