#!/usr/bin/env python3
"""Build contexts shared by all package-specific recipes."""

import os
from pathlib import Path
import platform
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


def _host_os():
    name = platform.system().lower()
    return {"darwin": "macos", "windows": "windows", "linux": "linux"}.get(name, name)


def _architecture(value=None):
    value = (value or platform.machine()).lower()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(value, value)


def detect_host():
    return {"os": _host_os(), "architecture": _architecture()}


def _compiler_version(command):
    output = _run([command, "--version"], capture=True)
    match = re.search(r"\b\d+\.\d+(?:\.\d+)?\b", output)
    return match.group(0) if match else output.splitlines()[0]


def _require_build_tools():
    cmake = _require_command("cmake")
    _require_command("ninja")
    output = _run([cmake, "--version"], capture=True)
    match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?\b", output)
    if not match:
        raise ValueError("Unable to determine CMake version")
    version = tuple(int(part or 0) for part in match.groups())
    if version < (3, 22, 1):
        raise ValueError("CMake 3.22.1 or newer is required; found " + match.group(0))


def _require_command(command):
    found = shutil.which(command)
    if not found:
        raise ValueError("Required build tool is not available in PATH: " + command)
    return found


def _profile_cmake_arguments(profile, host):
    family = profile["family"]
    toolchain = profile["toolchain"]
    args = ["-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_POSITION_INDEPENDENT_CODE=ON"]
    observed = {"host": host, "target_id": profile["id"]}

    if family == "linux":
        libc, libc_version = platform.libc_ver()
        expected_libc = toolchain["libc"]
        expected_version = toolchain["builder_libc_version"]
        if libc != expected_libc or libc_version != expected_version:
            raise ValueError(
                "Linux target {} requires builder {} {}; found {} {}".format(
                    profile["id"], expected_libc, expected_version, libc or "unknown", libc_version or "unknown"
                )
            )
        cc = os.environ.get("CC", toolchain["c_compiler"])
        cxx = os.environ.get("CXX", toolchain["cxx_compiler"])
        if len(cc.split()) != 1 or len(cxx.split()) != 1:
            raise ValueError("CC and CXX must each name one compiler executable")
        _require_command(cc)
        _require_command(cxx)
        cc_version = _compiler_version(cc)
        cxx_version = _compiler_version(cxx)
        expected_version = toolchain["compiler_version"]
        if cc_version != expected_version or cxx_version != expected_version:
            raise ValueError(
                "Target {} requires compiler {}; found CC={} CXX={}".format(
                    profile["id"], expected_version, cc_version, cxx_version
                )
            )
        cflags = " ".join(value for value in (toolchain.get("cflags", ""), os.environ.get("CFLAGS", "")) if value)
        cxxflags = " ".join(value for value in (toolchain.get("cxxflags", ""), os.environ.get("CXXFLAGS", "")) if value)
        args.append("-DCMAKE_C_COMPILER={}".format(cc))
        if cflags:
            args.append("-DCMAKE_C_FLAGS={}".format(cflags))
        if cxxflags:
            args.append("-DCMAKE_CXX_FLAGS={}".format(cxxflags))
        observed.update({"c_compiler": cc, "c_compiler_version": cc_version,
                         "cxx_compiler": cxx, "cxx_compiler_version": cxx_version,
                         "libc": libc, "libc_version": libc_version,
                         "cflags": cflags, "cxxflags": cxxflags,
                         "ldflags": os.environ.get("LDFLAGS", "")})
    elif family == "macos":
        compiler = os.environ.get("CC", toolchain.get("compiler", "clang"))
        cxx = os.environ.get("CXX", toolchain.get("cxx_compiler", "clang++"))
        _require_command(compiler)
        _require_command(cxx)
        compiler_output = _run([compiler, "--version"], capture=True)
        if "Apple clang" not in compiler_output:
            raise ValueError("macOS target profiles require Apple Clang")
        version_match = re.search(r"\b\d+\.\d+(?:\.\d+)?\b", compiler_output)
        version = version_match.group(0) if version_match else compiler_output.splitlines()[0]
        sdk_version = _run(["xcrun", "--sdk", "macosx", "--show-sdk-version"], capture=True)
        args.extend([
            "-DCMAKE_C_COMPILER={}".format(compiler),
            "-DCMAKE_OSX_ARCHITECTURES={}".format(profile["architecture"]),
            "-DCMAKE_OSX_DEPLOYMENT_TARGET={}".format(toolchain["deployment_target"]),
        ])
        if os.environ.get("CFLAGS"):
            args.append("-DCMAKE_C_FLAGS={}".format(os.environ["CFLAGS"]))
        if os.environ.get("CXXFLAGS"):
            args.append("-DCMAKE_CXX_FLAGS={}".format(os.environ["CXXFLAGS"]))
        observed.update({"compiler": compiler, "compiler_version": version, "cxx_compiler": cxx,
                         "sdk_version": sdk_version,
                         "deployment_target": toolchain["deployment_target"],
                         "cflags": os.environ.get("CFLAGS", ""),
                         "ldflags": os.environ.get("LDFLAGS", "")})
    elif family == "windows":
        if _host_os() != "windows" or not os.environ.get("VSCMD_VER"):
            raise ValueError("Windows builds require an initialized Visual Studio developer environment")
        vc_tools = os.environ.get("VCToolsVersion", "")
        version_match = re.match(r"^(\d+)\.(\d+)", vc_tools)
        if (not version_match
                or int(version_match.group(1)) != toolchain["toolset_major"]
                or int(version_match.group(2)) < toolchain["toolset_minimum_minor"]):
            raise ValueError(
                "Target {} requires MSVC toolset {}; VCToolsVersion is {}".format(
                    profile["id"], toolchain["toolset"], vc_tools or "unset"
                )
            )
        compiler = os.environ.get("CC", "cl")
        cxx = os.environ.get("CXX", "cl")
        _require_command(compiler)
        _require_command(cxx)
        args.extend([
            "-DCMAKE_C_COMPILER={}".format(compiler),
            "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
            "-DCMAKE_MSVC_RUNTIME_LIBRARY={}".format(toolchain["runtime_library"]),
        ])
        observed.update({"compiler": compiler, "cxx_compiler": cxx,
                         "toolset": toolchain["toolset"], "vc_tools_version": vc_tools,
                         "runtime": toolchain["runtime"], "configuration": "Release"})
    elif family == "android":
        for variable in ("CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS"):
            if os.environ.get(variable):
                raise ValueError(
                    "{} is a host setting and cannot be applied to Android; use the target profile".format(variable)
                )
        ndk = os.environ.get("ANDROID_NDK_HOME") or os.environ.get("ANDROID_NDK_ROOT")
        if not ndk:
            raise ValueError("Set ANDROID_NDK_HOME or ANDROID_NDK_ROOT to the Android NDK")
        ndk = Path(ndk).expanduser().resolve()
        toolchain_file = ndk / "build" / "cmake" / "android.toolchain.cmake"
        revision_file = ndk / "source.properties"
        if not toolchain_file.is_file() or not revision_file.is_file():
            raise ValueError("Invalid Android NDK directory: " + str(ndk))
        revision = ""
        for line in revision_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("Pkg.Revision"):
                revision = line.partition("=")[2].strip()
                break
        if revision != toolchain["ndk_revision"]:
            raise ValueError(
                "Target {} requires Android NDK {}; found {}".format(
                    profile["id"], toolchain["ndk_revision"], revision or "unknown"
                )
            )
        args.extend([
            "-DCMAKE_TOOLCHAIN_FILE={}".format(toolchain_file),
            "-DANDROID_ABI={}".format(profile["architecture"]),
            "-DANDROID_PLATFORM=android-{}".format(toolchain["api_level"]),
            "-DANDROID_NDK={}".format(ndk),
        ])
        observed.update({"ndk_revision": revision,
                         "abi": profile["architecture"], "api_level": toolchain["api_level"]})
    else:
        raise ValueError("Unsupported target family: " + str(family))

    return args, observed


class TargetProfile:
    def __init__(self, document):
        self.id = document["id"]
        self.family = document["family"]
        self.architecture = document["architecture"]
        self.document = document


class RecipeContext:
    """Common source, target, command, CMake, and safe staging interface."""

    def __init__(self, *, source_dir, build_dir, stage_dir, profile, jobs):
        self.source_dir = Path(source_dir).resolve()
        self.build_dir = Path(build_dir).resolve()
        self.stage_dir = Path(stage_dir).resolve()
        self.profile = profile
        self.target = TargetProfile(profile)
        self.jobs = jobs
        self.host = detect_host()
        allowed_hosts = profile["hosts"]
        if not any(host["os"] == self.host["os"] and self.host["architecture"] in host["architectures"]
                   for host in allowed_hosts):
            raise ValueError(
                "Target {} cannot be built on host {}/{}".format(
                    profile["id"], self.host["os"], self.host["architecture"]
                )
            )
        if self.target.family != "android" and self.host["architecture"] != self.target.architecture:
            raise ValueError("This profile supports native builds only")
        _require_build_tools()
        self.cmake_args, self.observed_toolchain = _profile_cmake_arguments(profile, self.host)
        self.environment = dict(os.environ)
        toolchain = profile["toolchain"]
        if profile["family"] == "linux":
            self.environment.setdefault("CC", toolchain["c_compiler"])
            self.environment.setdefault("CXX", toolchain["cxx_compiler"])
        elif profile["family"] == "macos":
            self.environment.setdefault("CC", toolchain.get("compiler", "clang"))
            self.environment.setdefault("CXX", toolchain.get("cxx_compiler", "clang++"))
        elif profile["family"] == "windows":
            self.environment.setdefault("CC", "cl")
            self.environment.setdefault("CXX", "cl")

    def run(self, argv, *, cwd=None, env=None):
        command_env = dict(self.environment)
        if env:
            command_env.update(env)
        _run([str(value) for value in argv], cwd=cwd or self.build_dir, env=command_env)

    def cmake(self, *, options=None, targets=(), install=False):
        args = [
            "cmake", "-S", str(self.source_dir), "-B", str(self.build_dir),
            "-G", "Ninja", "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
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
            self.run([
                "cmake", "--install", str(self.build_dir), "--config", "Release",
                "--prefix", str(self.stage_dir),
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
    import json

    document = json.loads(Path(path).read_text(encoding="utf-8"))
    required = ("id", "family", "architecture", "hosts", "toolchain")
    missing = [key for key in required if key not in document]
    if missing:
        raise ValueError("Target profile is missing fields: " + ", ".join(missing))
    return document
