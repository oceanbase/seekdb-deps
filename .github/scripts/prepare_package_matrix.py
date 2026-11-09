#!/usr/bin/env python3
"""Validate workflow inputs and prepare the package build matrix."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re


LINUX_COMPILER_VERSION = "12"
EXTRA_LINUX_COMPILERS = {"fortran": "gfortran"}


TARGETS = [
    {
        "id": "linux-x86_64-glibc217",
        "family": "linux",
        "runner": "ubuntu-24.04",
        "conda_arch": "64",
        "compiler_version": LINUX_COMPILER_VERSION,
        "compiler_prefix": "x86_64-conda-linux-gnu",
    },
    {
        "id": "linux-aarch64-glibc217",
        "family": "linux",
        "runner": "ubuntu-24.04-arm",
        "conda_arch": "aarch64",
        "compiler_version": LINUX_COMPILER_VERSION,
        "compiler_prefix": "aarch64-conda-linux-gnu",
    },
    {"id": "macos-arm64-min13", "family": "macos", "runner": "macos-15"},
    {"id": "windows-x86_64-md", "family": "windows", "runner": "windows-2022"},
    {"id": "android-arm64-v8a-api26", "family": "android", "runner": "ubuntu-24.04"},
]


def selected_targets(requested_target, supported_targets):
    known = {target["id"] for target in TARGETS}
    if not supported_targets or any(target not in known for target in supported_targets):
        raise SystemExit("Package declares no usable build targets")
    if requested_target == "all":
        return [target for target in TARGETS if target["id"] in supported_targets]

    selected = [target for target in TARGETS
                if target["id"] == requested_target and target["id"] in supported_targets]
    if not selected:
        raise SystemExit("Unsupported target: " + requested_target)
    return selected


def add_build_dependencies(targets, metadata):
    dependencies = metadata.get("build_dependencies", {})
    if not isinstance(dependencies, dict):
        raise SystemExit("Build dependencies must be an object")
    requirements = dependencies.get("linux", {})
    if not isinstance(requirements, dict):
        raise SystemExit("Linux build dependencies must be an object")
    compilers = requirements.get("compilers", [])
    packages = requirements.get("system_packages", [])
    if not isinstance(compilers, list) or any(
            not isinstance(name, str) or name not in EXTRA_LINUX_COMPILERS
            for name in compilers):
        raise SystemExit("Unsupported Linux build compiler requirement")
    if not isinstance(packages, list) or any(
            not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", name)
            for name in packages):
        raise SystemExit("Invalid Linux system package requirement")
    selected = [dict(target) for target in targets]
    for target in selected:
        if target["family"] == "linux":
            target["conda_extra"] = " ".join(
                "{}_linux-{}={}".format(EXTRA_LINUX_COMPILERS[name],
                                        target["conda_arch"], target["compiler_version"])
                for name in compilers
            )
            target["system_packages"] = " ".join(packages)
    return selected


def release_date(requested_date):
    requested_date = requested_date.strip()
    if not requested_date:
        return datetime.now(timezone.utc).strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", requested_date):
        raise SystemExit("Date must use YYYYMMDD")
    try:
        datetime.strptime(requested_date, "%Y%m%d")
    except ValueError as error:
        raise SystemExit("Invalid date: " + str(error))
    return requested_date


def main():
    package = os.environ["PACKAGE_NAME"]
    version = os.environ["PACKAGE_VERSION"]
    metadata_path = Path("packages") / package / version / "package.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    supported_targets = metadata.get("supported_targets", [target["id"] for target in TARGETS])
    requested_target = "all" if os.environ["REQUESTED_KIND"] == "source" else os.environ["REQUESTED_TARGET"]
    targets = add_build_dependencies(selected_targets(requested_target, supported_targets), metadata)
    date = release_date(os.environ["REQUESTED_DATE"])
    repository_sha = os.environ["REPOSITORY_SHA"]
    output_path = Path(os.environ["GITHUB_OUTPUT"])

    with output_path.open("a", encoding="utf-8") as output:
        print("release_date=" + date, file=output)
        print("repo_sha_short=" + repository_sha[:12], file=output)
        print("targets=" + json.dumps(targets, separators=(",", ":")), file=output)


if __name__ == "__main__":
    main()
