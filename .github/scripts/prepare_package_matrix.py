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
    {"id": "windows-x86_64-md", "family": "windows", "runner": "windows-2025"},
    {"id": "android-arm64-v8a-api26", "family": "android", "runner": "ubuntu-24.04"},
]


def selected_targets(requested_target):
    if requested_target == "all":
        return TARGETS

    selected = [target for target in TARGETS if target["id"] == requested_target]
    if not selected:
        raise SystemExit("Unsupported target: " + requested_target)
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
    targets = selected_targets(os.environ["REQUESTED_TARGET"])
    date = release_date(os.environ["REQUESTED_DATE"])
    repository_sha = os.environ["REPOSITORY_SHA"]
    output_path = Path(os.environ["GITHUB_OUTPUT"])

    with output_path.open("a", encoding="utf-8") as output:
        print("release_date=" + date, file=output)
        print("repo_sha_short=" + repository_sha[:12], file=output)
        print("targets=" + json.dumps(targets, separators=(",", ":")), file=output)


if __name__ == "__main__":
    main()
