#!/usr/bin/env python3
"""Validate workflow inputs and prepare the package build matrix."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re


TARGETS = [
    {
        "id": "linux-x86_64-glibc217",
        "family": "linux",
        "runner": "ubuntu-24.04",
        "conda_arch": "64",
        "compiler_prefix": "x86_64-conda-linux-gnu",
    },
    {
        "id": "linux-aarch64-glibc217",
        "family": "linux",
        "runner": "ubuntu-24.04-arm",
        "conda_arch": "aarch64",
        "compiler_prefix": "aarch64-conda-linux-gnu",
    },
    {"id": "macos-arm64-min13", "family": "macos", "runner": "macos-15"},
    {"id": "windows-x86_64-md", "family": "windows", "runner": "windows-2022"},
    {"id": "windows-x86_64-mt", "family": "windows", "runner": "windows-2022"},
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


def release_timestamp(requested_timestamp):
    requested_timestamp = requested_timestamp.strip()
    if not requested_timestamp:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%z")
    if not re.fullmatch(r"\d{8}T\d{6}\+0000", requested_timestamp):
        raise SystemExit("Timestamp must use YYYYMMDDTHHMMSS+0000 (UTC)")
    try:
        datetime.strptime(requested_timestamp, "%Y%m%dT%H%M%S%z")
    except ValueError as error:
        raise SystemExit("Invalid timestamp: " + str(error))
    return requested_timestamp


def main():
    metadata_path = (Path("packages") / os.environ["PACKAGE_NAME"]
                     / os.environ["PACKAGE_VERSION"] / "package.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    default_targets = [target["id"] for target in TARGETS if target["id"] != "windows-x86_64-mt"]
    supported_targets = metadata.get("supported_targets", default_targets)
    requested_target = ("all" if os.environ["REQUESTED_KIND"] == "source"
                        else os.environ["REQUESTED_TARGET"])
    targets = selected_targets(requested_target, supported_targets)
    timestamp = release_timestamp(os.environ["REQUESTED_TIMESTAMP"])
    repository_sha = os.environ["REPOSITORY_SHA"]
    output_path = Path(os.environ["GITHUB_OUTPUT"])

    with output_path.open("a", encoding="utf-8") as output:
        print("release_timestamp=" + timestamp, file=output)
        print("repo_sha_short=" + repository_sha[:12], file=output)
        print("targets=" + json.dumps(targets, separators=(",", ":")), file=output)


if __name__ == "__main__":
    main()
