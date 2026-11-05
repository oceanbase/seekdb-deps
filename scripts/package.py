#!/usr/bin/env python3
"""Build pinned dependency source bundles and platform-specific binary packages."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from archive import create_archive, sha256_file, verify_manifest, write_manifest
from build import RecipeContext, load_target


def run(argv, *, cwd=None, capture=False):
    result = subprocess.run(
        [str(value) for value in argv],
        cwd=str(cwd) if cwd else None,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
    )
    return result.stdout.strip() if capture else ""


def sha256(path):
    return sha256_file(path)


def safe_relative(value, label):
    normalized = str(value).replace("\\", "/")
    path = PurePosixPath(normalized)
    if (path.is_absolute() or re.match(r"^[A-Za-z]:", normalized)
            or not path.parts or any(part in ("", ".", "..") for part in path.parts)):
        raise ValueError("{} must be a safe relative path: {}".format(label, value))
    return Path(*path.parts)


def package_paths(name, version):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$", name):
        raise ValueError("Invalid package name: " + name)
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$", version):
        raise ValueError("Invalid package version: " + version)
    root = (ROOT / "packages" / name / version).resolve()
    try:
        root.relative_to((ROOT / "packages").resolve())
    except ValueError:
        raise ValueError("Package path escapes packages/")
    metadata_path = root / "package.json"
    if not metadata_path.is_file():
        raise ValueError("Unknown package version: " + str(root))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("name") != name or metadata.get("version") != version:
        raise ValueError("Package path and metadata name/version do not match")
    if metadata.get("schema_version") != 1:
        raise ValueError("Unsupported package metadata schema_version")
    for field in ("source", "recipe", "license", "required_artifacts"):
        if field not in metadata:
            raise ValueError("Package metadata is missing field: " + field)
    for field in ("path", "repository", "tag", "revision"):
        if field not in metadata["source"]:
            raise ValueError("Package source metadata is missing field: " + field)
    if not re.match(r"^[0-9a-fA-F]{40,64}$", metadata["source"]["revision"]):
        raise ValueError("Package source revision must be a Git object id")
    for field in ("spdx_id", "source_path"):
        if field not in metadata["license"]:
            raise ValueError("Package license metadata is missing field: " + field)
    return root, metadata


def git_root():
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    if result.returncode:
        return None
    return Path(result.stdout.strip()).resolve()


def repository_commit():
    top = git_root()
    if top is None or top != ROOT.resolve():
        document = verify_manifest(ROOT, expected_type="source")
        commit = document.get("seekdb_deps_commit")
        if not commit:
            raise ValueError("Source bundle manifest has no seekdb-deps commit")
        return commit, False
    status = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, capture=True)
    if status:
        raise ValueError("Commit package metadata and scripts before creating release archives")
    return run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture=True), True


def source_revision(package_root, metadata, repo_checkout):
    source = metadata["source"]
    source_dir = (package_root / safe_relative(source["path"], "source.path")).resolve()
    try:
        source_dir.relative_to(package_root.resolve())
    except ValueError:
        raise ValueError("Upstream source path escapes package directory")
    if not source_dir.is_dir():
        raise ValueError("Pinned upstream source is missing: " + str(source_dir))
    if not repo_checkout:
        document = verify_manifest(ROOT, expected_type="source")
        upstream = document.get("upstream", {})
        if upstream.get("revision") != source["revision"]:
            raise ValueError("Source bundle and package metadata upstream revisions do not match")
        return source["revision"], source_dir

    relative = source_dir.relative_to(ROOT).as_posix()
    tree_entry = run(["git", "ls-tree", "HEAD", "--", relative], cwd=ROOT, capture=True)
    parts = tree_entry.split()
    if len(parts) < 3 or parts[0] != "160000" or parts[1] != "commit":
        raise ValueError("Source path is not a committed Git submodule: " + relative)
    pinned_revision = parts[2]
    checked_out_revision = run(["git", "-C", str(source_dir), "rev-parse", "HEAD"], capture=True)
    if pinned_revision != checked_out_revision or pinned_revision != source["revision"]:
        raise ValueError("Submodule, package metadata, and upstream revision do not match")
    upstream_url = run(
        ["git", "-C", str(source_dir), "config", "--get", "remote.origin.url"], capture=True
    )
    if upstream_url != source["repository"]:
        raise ValueError("Submodule URL and package metadata do not match")
    status = run(
        ["git", "-C", str(source_dir), "status", "--porcelain", "--untracked-files=all"], capture=True
    )
    if status:
        raise ValueError("Upstream source submodule has local changes")
    return pinned_revision, source_dir


def patch_records(package_root, metadata):
    records = []
    for patch_name in metadata.get("patches", []):
        relative = safe_relative(patch_name, "patch path")
        patch_path = (package_root / relative).resolve()
        try:
            patch_path.relative_to(package_root.resolve())
        except ValueError:
            raise ValueError("Patch path escapes package directory: " + patch_name)
        if not patch_path.is_file():
            raise ValueError("Patch file is missing: " + patch_name)
        records.append({"path": relative.as_posix(), "sha256": sha256(patch_path)})
    return records


def copy_tree(source, destination):
    shutil.copytree(
        source, destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )


def copy_repository_inputs(destination, package_root):
    for name in ("README.md", "LICENSE"):
        shutil.copyfile(str(ROOT / name), str(destination / name))
    copy_tree(ROOT / "scripts", destination / "scripts")
    copy_tree(ROOT / "platforms", destination / "platforms")
    package_destination = destination / "packages" / package_root.relative_to(ROOT / "packages")
    package_destination.parent.mkdir(parents=True, exist_ok=True)
    copy_tree(package_root, package_destination)


def apply_patches(package_root, metadata, source_dir, workspace):
    patches = patch_records(package_root, metadata)
    prepared = workspace / "source"
    copy_tree(source_dir, prepared)
    for patch in patches:
        patch_path = package_root / patch["path"]
        run(["git", "apply", "--check", str(patch_path)], cwd=prepared)
        run(["git", "apply", "--binary", str(patch_path)], cwd=prepared)
    return prepared, patches


def source_artifact(package_root, metadata, source_revision_sha, source_dir,
                    repo_sha, output_dir, release_date):
    name = metadata["name"]
    version = metadata["version"]
    output = output_dir / "{}-{}-src-{}-{}.tar.gz".format(name, version, release_date, repo_sha[:12])
    root = output_dir / "seekdb-deps-{}-{}".format(name, version)
    root.mkdir(parents=True)
    copy_repository_inputs(root, package_root)
    document = {
        "schema_version": 1,
        "name": name,
        "version": version,
        "artifact_type": "source",
        "release_date": release_date,
        "seekdb_deps_commit": repo_sha,
        "upstream": {
            "repository": metadata["source"]["repository"],
            "tag": metadata["source"]["tag"],
            "revision": source_revision_sha,
        },
        "patches": patch_records(package_root, metadata),
        "license": metadata["license"],
    }
    write_manifest(root, document)
    return create_archive(root, output)


def load_recipe(path):
    spec = importlib.util.spec_from_file_location("seekdb_package_recipe", str(path))
    if spec is None or spec.loader is None:
        raise ValueError("Unable to load package build recipe: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    build = getattr(module, "build", None)
    if not callable(build):
        raise ValueError("Package recipe must define build(ctx)")
    return build


def required_artifacts(metadata, target_family):
    requirements = metadata.get("required_artifacts", {})
    selected = requirements.get(target_family, requirements.get("default", []))
    if not selected:
        raise ValueError("No required_artifacts are declared for target family " + target_family)
    return [safe_relative(path, "required artifact").as_posix() for path in selected]


def binary_artifact(package_root, metadata, source_revision_sha, source_dir,
                    repo_sha, output_dir, release_date, target_id, jobs):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", target_id):
        raise ValueError("Invalid target profile id: " + target_id)
    profile_path = ROOT / "platforms" / (target_id + ".json")
    if not profile_path.is_file():
        raise ValueError("Unknown target profile: " + target_id)
    profile = load_target(profile_path)
    if profile["id"] != target_id:
        raise ValueError("Target profile path and id do not match")
    output = output_dir / "{}-{}-bin-{}-{}-{}.tar.gz".format(
        metadata["name"], metadata["version"], target_id, release_date, repo_sha[:12]
    )
    package_root_in_archive = output_dir / "{}-{}".format(metadata["name"], metadata["version"])
    package_root_in_archive.mkdir(parents=True)
    build_dir = output_dir / "build"
    build_dir.mkdir()
    prepared_source, patches = apply_patches(package_root, metadata, source_dir, build_dir)
    context = RecipeContext(
        source_dir=prepared_source,
        build_dir=build_dir / "cmake",
        stage_dir=package_root_in_archive,
        profile=profile,
        jobs=jobs,
    )
    context.build_dir.mkdir(parents=True)
    context.stage_dir.mkdir(parents=True, exist_ok=True)
    recipe_path = (package_root / safe_relative(metadata["recipe"], "recipe")).resolve()
    try:
        recipe_path.relative_to(package_root.resolve())
    except ValueError:
        raise ValueError("Recipe path escapes package directory")
    recipe = load_recipe(recipe_path)
    recipe(context)

    license_source = prepared_source / safe_relative(metadata["license"]["source_path"], "license.source_path")
    if not license_source.is_file():
        raise ValueError("Package license file is missing: " + str(license_source))
    license_path = "share/licenses/{}/LICENSE".format(metadata["name"])
    context.install(license_source, license_path)

    expected = required_artifacts(metadata, profile["family"])
    missing = [path for path in expected if not (context.stage_dir / path).is_file()]
    if missing:
        raise ValueError("Package recipe did not stage required files: " + ", ".join(missing))

    write_manifest(context.stage_dir, {
        "schema_version": 1,
        "name": metadata["name"],
        "version": metadata["version"],
        "artifact_type": "binary",
        "release_date": release_date,
        "seekdb_deps_commit": repo_sha,
        "upstream": {
            "repository": metadata["source"]["repository"],
            "tag": metadata["source"]["tag"],
            "revision": source_revision_sha,
        },
        "patches": patches,
        "target": profile,
        "observed": context.observed_toolchain,
        "recipe": metadata["recipe"],
        "license": {
            "spdx_id": metadata["license"]["spdx_id"],
            "path": license_path,
        },
    })
    return create_archive(context.stage_dir, output)


def release_date(value):
    if not re.match(r"^\d{8}$", value):
        raise ValueError("Date must use YYYYMMDD")
    datetime.strptime(value, "%Y%m%d")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="package name under packages/")
    parser.add_argument("version", help="package version under packages/<name>/")
    parser.add_argument("--kind", choices=("source", "binary", "both"), default="both")
    parser.add_argument("--target", help="target profile ID; required for binary packages")
    parser.add_argument("--output", default=str(ROOT / "dist"))
    parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"), help="artifact date (YYYYMMDD)")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    args = parser.parse_args()

    if args.kind in ("binary", "both") and not args.target:
        raise ValueError("--target is required for --kind {}".format(args.kind))
    if args.kind == "source" and args.target:
        raise ValueError("--target is only used for binary packages")
    if args.jobs < 1:
        raise ValueError("--jobs must be positive")
    date = release_date(args.date)
    package_root, metadata = package_paths(args.name, args.version)
    repo_sha, repo_checkout = repository_commit()
    source_revision_sha, source_dir = source_revision(package_root, metadata, repo_checkout)
    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    target = args.target if args.kind in ("binary", "both") else None
    expected_names = []
    if args.kind in ("source", "both"):
        expected_names.append("{}-{}-src-{}-{}.tar.gz".format(args.name, args.version, date, repo_sha[:12]))
    if target:
        expected_names.append("{}-{}-bin-{}-{}-{}.tar.gz".format(args.name, args.version, target, date, repo_sha[:12]))
    for name in expected_names:
        if (output_dir / name).exists() or (output_dir / (name + ".sha256")).exists():
            raise FileExistsError("Refusing to overwrite " + str(output_dir / name))

    artifacts = []
    with tempfile.TemporaryDirectory(prefix=".seekdb-deps-", dir=str(output_dir)) as temporary:
        temporary = Path(temporary)
        if args.kind in ("source", "both"):
            artifacts.append(source_artifact(
                package_root, metadata, source_revision_sha, source_dir,
                repo_sha, temporary, date,
            ))
        if target:
            artifacts.append(binary_artifact(
                package_root, metadata, source_revision_sha, source_dir,
                repo_sha, temporary, date, target, args.jobs,
            ))
        for artifact in artifacts:
            final_path = output_dir / artifact["path"].name
            if final_path.exists() or final_path.with_name(final_path.name + ".sha256").exists():
                raise FileExistsError("Refusing to overwrite " + str(final_path))
        published = []
        for artifact in artifacts:
            final_path = output_dir / artifact["path"].name
            os.replace(str(artifact["path"]), str(final_path))
            os.replace(str(artifact["checksum"]), str(final_path.with_name(final_path.name + ".sha256")))
            published.append((final_path, artifact["sha256"]))
    for path, digest in published:
        print("{}  {}".format(path, digest))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        sys.exit(1)
