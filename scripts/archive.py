#!/usr/bin/env python3
"""Portable, deterministic archive and checksum helpers."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import tarfile


def _generated_python_cache(relative):
    return "__pycache__" in relative.parts or relative.suffix == ".pyc"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root, *, exclude_manifest=True):
    root = Path(root)
    entries = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative_path = path.relative_to(root)
        if _generated_python_cache(relative_path):
            continue
        relative = relative_path.as_posix()
        if exclude_manifest and relative == "manifest.json":
            continue
        if path.is_symlink():
            target = os.readlink(str(path))
            encoded = target.encode("utf-8")
            entries.append({
                "path": relative,
                "type": "symlink",
                "size": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            })
        elif path.is_file():
            entries.append({
                "path": relative,
                "type": "file",
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            })
        elif not path.is_dir():
            raise ValueError("Unsupported package input: " + str(path))
    return entries


def write_manifest(root, document):
    root = Path(root)
    document["files"] = inventory(root)
    (root / "manifest.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def verify_manifest(root, *, expected_type=None):
    root = Path(root)
    path = root / "manifest.json"
    if not path.is_file():
        raise ValueError("Source bundle has no manifest.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    if expected_type and document.get("artifact_type") != expected_type:
        raise ValueError("Unexpected source bundle artifact type")
    if document.get("files") != inventory(root):
        raise ValueError("Source bundle file inventory does not match manifest.json")
    return document


def _tar_info(path, arcname):
    info = tarfile.TarInfo(arcname)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    if path.is_symlink():
        info.type = tarfile.SYMTYPE
        info.linkname = os.readlink(str(path))
        info.mode = 0o777
        info.size = 0
    elif path.is_dir():
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.size = 0
    elif path.is_file():
        info.type = tarfile.REGTYPE
        info.mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
        info.size = path.stat().st_size
    else:
        raise ValueError("Unsupported package input: " + str(path))
    return info


def create_archive(source_root, output):
    source_root = Path(source_root)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT) as archive:
                root_info = _tar_info(source_root, source_root.name)
                archive.addfile(root_info)
                for path in sorted(source_root.rglob("*"), key=lambda item: item.relative_to(source_root).as_posix()):
                    relative = path.relative_to(source_root)
                    if _generated_python_cache(relative):
                        continue
                    arcname = source_root.name + "/" + relative.as_posix()
                    info = _tar_info(path, arcname)
                    if info.type == tarfile.REGTYPE:
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                    else:
                        archive.addfile(info)

    digest = sha256_file(output)
    checksum = output.with_name(output.name + ".sha256")
    checksum.write_bytes("{}  {}\n".format(digest, output.name).encode("ascii"))
    output.chmod(0o644)
    checksum.chmod(0o644)
    return {"path": output, "sha256": digest, "checksum": checksum}
