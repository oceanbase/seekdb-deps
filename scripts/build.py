#!/usr/bin/env python3
"""Build contexts shared by all package-specific recipes."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from pathlib import PurePosixPath


def _run(argv, *, cwd=None, env=None, capture=False):
    result = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
    )
    return result.stdout.strip() if capture else ""


class TargetProfile:
    def __init__(self, document):
        self.id = document["id"]
        self.family = document["family"]
        self.architecture = document["architecture"]
        self.document = document


class RecipeContext:
    """Common source, target, command, CMake, and safe staging interface."""

    def __init__(self, *, source_dir, build_dir, stage_dir, profile, build_environment, jobs):
        self.source_dir = Path(source_dir).resolve()
        self.build_dir = Path(build_dir).resolve()
        self.stage_dir = Path(stage_dir).resolve()
        self.profile = profile
        self.target = TargetProfile(profile)
        self.jobs = jobs
        self.cmake_args = list(build_environment["cmake_args"])
        self.environment = dict(build_environment["environment"])
        self.observed_build_environment = dict(build_environment["observed"])

    def run(self, argv, *, cwd=None, env=None):
        command_env = dict(self.environment)
        if env:
            command_env.update(env)
        _run([str(value) for value in argv], cwd=cwd or self.build_dir, env=command_env)

    def cmake(self, *, options=None, targets=(), install=False):
        args = [
            "cmake", "-S", str(self.source_dir), "-B", str(self.build_dir),
            "-G", "Ninja",
        ] + self.cmake_args
        for key, value in sorted((options or {}).items()):
            if isinstance(value, bool):
                value = "ON" if value else "OFF"
            args.append("-D{}={}".format(key, value))
        self.run(args, cwd=self.build_dir)
        command = ["cmake", "--build", str(self.build_dir), "--parallel", str(self.jobs), "--config", "Release"]
        if targets:
            command.extend(["--target"] + list(targets))
        self.run(command, cwd=self.build_dir)
        if install:
            install_prefix = Path(os.path.relpath(self.stage_dir, self.build_dir)).as_posix()
            self.run([
                "cmake", "--install", str(self.build_dir), "--config", "Release",
                "--prefix", install_prefix,
            ], cwd=self.build_dir)

    def install(self, source, relative_destination):
        source = Path(source).resolve()
        if not (source == self.source_dir or self.source_dir in source.parents
                or source == self.build_dir or self.build_dir in source.parents):
            raise ValueError("Recipe may only install files from source_dir or build_dir")
        if not source.is_file():
            raise ValueError("Recipe output does not exist: " + str(source))
        normalized = str(relative_destination).replace("\\", "/")
        destination = PurePosixPath(normalized)
        if (destination.is_absolute() or re.match(r"^[A-Za-z]:", normalized)
                or not destination.parts or any(part in ("", ".", "..") for part in destination.parts)):
            raise ValueError("Invalid relative package path: " + str(relative_destination))
        output = self.stage_dir.joinpath(*destination.parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(source), str(output))
        executable = bool(source.stat().st_mode & 0o111)
        output.chmod(0o755 if executable else 0o644)


def load_target(path):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    required = ("id", "family", "architecture", "hosts", "abi")
    missing = [key for key in required if key not in document]
    if missing:
        raise ValueError("Target profile is missing fields: " + ", ".join(missing))
    return document
