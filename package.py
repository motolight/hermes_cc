"""Build the standalone, deterministic Hermes Command Center Python release."""

import argparse
import hashlib
import io
from pathlib import Path
import stat
import zipfile

from install import (
    MAX_PACKAGE, InstallError, atomic_write, check_manifest, json_bytes, public_name, runtime_name, source_payload,
)


def package(source, output):
    payload = source_payload(source)
    public = {name: data for name, data in payload.items() if public_name(name)}
    runtime = {name: data for name, data in payload.items() if runtime_name(name)}
    version = check_manifest(public)
    if payload.keys() != public.keys() | runtime.keys() or "sidecar/server.py" not in runtime:
        raise InstallError("Release has unexpected files or is missing the sidecar.")
    files = {
        "__main__.py": b"from install import main\nraise SystemExit(main())\n",
        "install.py": (source / "install.py").read_bytes(),
        **{f"payload/{name}": data for name, data in payload.items()},
    }
    docs = source / "docs" / "INSTALL.md"
    if docs.is_file():
        files["docs/INSTALL.md"] = docs.read_bytes()
    files["bundle.json"] = json_bytes({
        "format": 1,
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    })
    if sum(map(len, files.values())) > MAX_PACKAGE:
        raise InstallError("Release exceeds the supported uncompressed size.")
    binary = io.BytesIO()
    with zipfile.ZipFile(binary, "w") as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f"hermes-command-center-{version}.pyz"
    digest_path = archive_path.with_suffix(".pyz.sha256")
    data = binary.getvalue()
    atomic_write(archive_path, data, 0o644)
    atomic_write(digest_path, f"{hashlib.sha256(data).hexdigest()}  {archive_path.name}\n".encode("ascii"), 0o644)
    return archive_path, digest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = Path(__file__).resolve().parent
    parser.add_argument("--output", type=Path, default=source / "dist")
    args = parser.parse_args()
    try:
        for path in package(source, args.output):
            print(path.resolve())
    except (InstallError, OSError) as exc:
        parser.exit(1, f"Hermes Command Center: {exc}\n")


if __name__ == "__main__":
    main()
