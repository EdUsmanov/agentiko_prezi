"""Explicit local installation; Microsoft binaries are never bundled with source."""

import argparse
import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
import httpx

URL = "https://download.microsoft.com/download/8/6/0/860a94fa-7feb-44ef-ac79-c072d9113d69/Microsoft%20Aptos%20Fonts.zip"
SHA256 = "6528fd120e719a9f985e94214eca6887d1653b88456916a792a630b02e95b025"
DEST = Path(__file__).resolve().parents[1] / "data/local-fonts/aptos"


def main():
    parser = argparse.ArgumentParser(
        description="Install Microsoft's Aptos 4.40 for LOCAL use only. No redistribution or commercial hosting under this package's EULA."
    )
    parser.add_argument(
        "--accept-license",
        action="store_true",
        help="Confirm acceptance of the Microsoft Aptos Fonts EULA linked from download page 106087",
    )
    args = parser.parse_args()
    if not args.accept_license:
        parser.error(
            "Read https://www.microsoft.com/en-us/download/details.aspx?id=106087 and the package EULA; explicit --accept-license is required."
        )
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", URL) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 10_000_000:
                    raise ValueError("Archive exceeds 10 MB")
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError(
            "Microsoft archive changed; review the new license and contents before updating the pin"
        )
    with ZipFile(BytesIO(data)) as archive:
        for entry in archive.infolist():
            if Path(entry.filename).name != entry.filename or entry.file_size > 16_000_000:
                raise ValueError("Unexpected archive entry")
        DEST.mkdir(parents=True, exist_ok=True)
        for entry in archive.infolist():
            target = DEST / entry.filename
            raw = archive.read(entry)
            if target.exists() and target.read_bytes() != raw:
                raise ValueError("Existing font differs; refusing overwrite: " + entry.filename)
            target.write_bytes(raw)
    print("Aptos 4.40 installed locally in " + str(DEST))


if __name__ == "__main__":
    main()
