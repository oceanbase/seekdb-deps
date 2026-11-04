#!/usr/bin/env python3
"""Build deterministic source and binary archives from a pinned package source."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def run(command, *, cwd=None, env=None, capture=False):
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=capture,
    )
    return result.stdout.strip() if capture else ""


def read_flat_yaml(path):
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#") or raw_line[0].isspace():
            continue
        key, separator, raw_value = raw_line.partition(":")
        if not separator:
            raise ValueError("Invalid top-level YAML field in " + str(path))
        raw_value = raw_value.strip()
        if raw_value == "[]":
            value = []
        elif raw_value.startswith("[") and raw_value.endswith("]"):
            value = json.loads(raw_value)
        elif raw_value.startswith('"') and raw_value.endswith('"'):
            value = json.loads(raw_value)
        else:
            value = raw_value
        values[key.strip()] = value
    return values


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_inventory(root):
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "manifest.json"):
        files.append({
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256(path),
        })
    return files


def patch_records(package_root, metadata):
    records = []
    for patch_name in metadata.get("patches", []):
        patch_path = (package_root / patch_name).resolve()
        try:
            patch_path.relative_to(package_root.resolve())
        except ValueError:
            raise ValueError("Invalid package patch path: " + patch_name)
        if not patch_path.is_file():
            raise ValueError("Invalid package patch path: " + patch_name)
        records.append({"path": patch_name, "sha256": sha256(patch_path)})
    return records


def prepare_source(package_root, source_dir, metadata, destination):
    shutil.copytree(source_dir, destination, ignore=shutil.ignore_patterns(".git"))
    patches = patch_records(package_root, metadata)
    for patch in patches:
        patch_path = package_root / patch["path"]
        run(["patch", "--batch", "--forward", "-p1", "-i", str(patch_path)], cwd=destination)
    return patches


def write_manifest(root, document):
    document["files"] = file_inventory(root)
    (root / "manifest.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def make_archive(stage, output):
    tar = subprocess.Popen(
        ["tar", "--sort=name", "--mtime=@0", "--owner=0", "--group=0", "--numeric-owner", "-cf", "-", "."],
        cwd=stage,
        stdout=subprocess.PIPE,
    )
    with output.open("wb") as compressed:
        zstd = subprocess.Popen(
            ["zstd", "--quiet", "-19", "-T1", "--compress", "--stdout"],
            stdin=tar.stdout,
            stdout=compressed,
        )
        tar.stdout.close()
        zstd_status = zstd.wait()
        tar_status = tar.wait()
    if zstd_status or tar_status:
        output.unlink(missing_ok=True)
        raise RuntimeError("tar/zstd failed while creating " + str(output))
    digest = sha256(output)
    output.with_name(output.name + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="ascii"
    )


def profile_for_target(target):
    path = ROOT / "platforms" / (target + ".yaml")
    if not path.is_file():
        raise ValueError("Unknown target profile: " + target)
    profile = read_flat_yaml(path)
    required = ("id", "os", "architecture", "libc", "libc_version", "compiler", "compiler_version", "cflags")
    missing = [key for key in required if not profile.get(key)]
    if missing:
        raise ValueError("Missing profile fields: " + ", ".join(missing))

    if platform.system().lower() != profile["os"]:
        raise ValueError("Builder OS does not match profile " + target)
    if platform.machine().lower() != profile["architecture"]:
        raise ValueError("Builder architecture does not match profile " + target)
    libc = run(["getconf", "GNU_LIBC_VERSION"], capture=True).split()
    if len(libc) != 2 or libc[0] != profile["libc"] or libc[1] != profile["libc_version"]:
        raise ValueError("Builder libc does not match profile " + target)

    compiler = shlex.split(profile["compiler"])
    compiler_version = run(compiler + ["--version"], capture=True).splitlines()[0]
    match = re.search(r"\b\d+\.\d+\.\d+\b", compiler_version)
    if not match or match.group(0) != profile["compiler_version"]:
        raise ValueError("Builder compiler does not match profile " + target)
    return path, profile, compiler_version


def source_revision(package_root, metadata):
    relative_path = (package_root / metadata["source_path"]).relative_to(ROOT).as_posix()
    tree_entry = run(["git", "ls-tree", "HEAD", "--", relative_path], cwd=ROOT, capture=True)
    parts = tree_entry.split()
    if len(parts) < 3 or parts[0] != "160000" or parts[1] != "commit":
        raise ValueError("Source path is not a committed Git submodule: " + relative_path)
    pinned_revision = parts[2]
    checked_out_revision = run(["git", "-C", str(package_root / metadata["source_path"]), "rev-parse", "HEAD"], capture=True)
    if pinned_revision != checked_out_revision or pinned_revision != metadata["upstream_revision"]:
        raise ValueError("Submodule, package metadata, and upstream revision do not match")
    upstream_url = run(
        ["git", "-C", str(package_root / metadata["source_path"]), "config", "--get", "remote.origin.url"],
        capture=True,
    )
    if upstream_url != metadata["upstream_repository"]:
        raise ValueError("Submodule URL and package metadata do not match")
    source_status = run(
        ["git", "-C", str(package_root / metadata["source_path"]), "status", "--porcelain", "--untracked-files=all"],
        capture=True,
    )
    if source_status:
        raise ValueError("Upstream source submodule has local changes")
    return pinned_revision, package_root / metadata["source_path"]


def repository_commit():
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, capture=True)
    if status:
        raise ValueError("Commit package metadata before creating release archives")
    return run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture=True)


def copy_package_inputs(package_root, archive_root, metadata):
    for child in package_root.iterdir():
        if child.name == metadata["source_path"]:
            continue
        destination = archive_root / child.name
        if child.is_dir():
            shutil.copytree(child, destination)
        else:
            shutil.copy2(child, destination)


def source_archive(package_root, metadata, source_revision_sha, source_dir, repo_sha, output_dir, date):
    name = metadata["name"]
    version = metadata["version"]
    output = output_dir / f"{name}-{version}-src-{date}-{repo_sha[:12]}.tar.zst"
    if output.exists() or output.with_name(output.name + ".sha256").exists():
        raise FileExistsError("Refusing to overwrite " + str(output))

    with tempfile.TemporaryDirectory(prefix="seekdb-deps-source-") as temporary:
        archive_root = Path(temporary) / "archive"
        stage = archive_root / f"{name}-{version}"
        archive_root.mkdir()
        stage.mkdir(parents=True)
        copy_package_inputs(package_root, stage, metadata)
        source_target = stage / metadata["source_path"]
        source_target.mkdir(parents=True)
        source_tar = Path(temporary) / "upstream-source.tar"
        run(["git", "-C", str(source_dir), "archive", "--format=tar", "--output", str(source_tar), source_revision_sha])
        run(["tar", "-xf", str(source_tar), "-C", str(source_target)])
        write_manifest(stage, {
            "schema_version": 1,
            "name": name,
            "version": version,
            "artifact_type": "source",
            "target": None,
            "seekdb_deps_commit": repo_sha,
            "upstream": {
                "repository": metadata["upstream_repository"],
                "tag": metadata["upstream_tag"],
                "revision": source_revision_sha,
            },
            "patches": patch_records(package_root, metadata),
            "license": {
                "spdx_id": metadata["license_spdx"],
                "path": metadata["license_path"],
            },
        })
        make_archive(archive_root, output)
    return output


def binary_archive(package_root, metadata, source_revision_sha, source_dir, repo_sha, output_dir, date, target):
    name = metadata["name"]
    version = metadata["version"]
    output = output_dir / f"{name}-{version}-bin-{target}-{date}-{repo_sha[:12]}.tar.zst"
    if output.exists() or output.with_name(output.name + ".sha256").exists():
        raise FileExistsError("Refusing to overwrite " + str(output))

    profile_path, profile, compiler_version = profile_for_target(target)
    build_script = package_root / metadata["build_script"]
    if not build_script.is_file():
        raise ValueError("Package build script is missing")

    with tempfile.TemporaryDirectory(prefix="seekdb-deps-binary-") as temporary:
        temporary = Path(temporary)
        build_dir = temporary / "build"
        prepared_source = temporary / "source"
        stage = temporary / "stage"
        build_dir.mkdir()
        stage.mkdir()
        patches = prepare_source(package_root, source_dir, metadata, prepared_source)
        environment = dict(os.environ)
        environment.update({
            "SOURCE_DIR": str(prepared_source),
            "BUILD_DIR": str(build_dir),
            "DESTDIR": str(stage),
            "PREFIX": metadata["install_prefix"],
            "JOBS": str(os.cpu_count() or 1),
            "CC": profile["compiler"],
            "CFLAGS": profile["cflags"],
        })
        run([str(build_script)], cwd=ROOT, env=environment)
        write_manifest(stage, {
            "schema_version": 1,
            "name": name,
            "version": version,
            "artifact_type": "binary",
            "target": target,
            "seekdb_deps_commit": repo_sha,
            "upstream": {
                "repository": metadata["upstream_repository"],
                "tag": metadata["upstream_tag"],
                "revision": source_revision_sha,
            },
            "patches": patches,
            "build": {
                "profile_file": profile_path.relative_to(ROOT).as_posix(),
                "compiler": compiler_version,
                "cflags": profile["cflags"],
                "linkage": metadata["linkage"],
            },
            "license": {
                "spdx_id": metadata["license_spdx"],
                "source_path": metadata["license_path"],
                "installed_path": metadata["install_prefix"].lstrip("/") + f"/share/licenses/{name}/LICENSE",
            },
        })
        make_archive(stage, output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("version")
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", default=str(ROOT / "dist"))
    args = parser.parse_args()

    package_root = ROOT / "packages" / args.name / args.version
    metadata_path = package_root / "package.yaml"
    if not metadata_path.is_file():
        raise ValueError("Unknown package version: " + str(package_root))
    metadata = read_flat_yaml(metadata_path)
    if metadata.get("name") != args.name or metadata.get("version") != args.version:
        raise ValueError("Package path and metadata name/version do not match")
    repo_sha = repository_commit()
    revision, source_dir = source_revision(package_root, metadata)
    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y%m%d")

    with tempfile.TemporaryDirectory(prefix=".package-", dir=output_dir) as temporary:
        temporary_output = Path(temporary)
        staged = [
            source_archive(package_root, metadata, revision, source_dir, repo_sha, temporary_output, date),
            binary_archive(package_root, metadata, revision, source_dir, repo_sha, temporary_output, date, args.target),
        ]
        final_paths = [output_dir / artifact.name for artifact in staged]
        if any(path.exists() or path.with_name(path.name + ".sha256").exists() for path in final_paths):
            raise FileExistsError("Refusing to overwrite an existing package archive")
        for artifact, final_path in zip(staged, final_paths):
            os.replace(artifact, final_path)
            os.replace(artifact.with_name(artifact.name + ".sha256"), final_path.with_name(final_path.name + ".sha256"))
    for artifact in final_paths:
        print(f"{artifact}  {sha256(artifact)}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        sys.exit(1)
