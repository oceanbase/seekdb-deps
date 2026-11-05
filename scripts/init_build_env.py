#!/usr/bin/env python3
"""Prepare and validate the host compiler environment for a target profile."""

import os
from pathlib import Path
import platform
import re
import shutil
import subprocess


MINIMUM_CMAKE_VERSION = (3, 22, 1)
MINIMUM_LINUX_COMPILER_VERSION = (9, 4, 0)
MINIMUM_WINDOWS_TOOLSET_VERSION = (14, 30)
MINIMUM_ANDROID_NDK_REVISION = (27, 2, 12479018)
COMMON_COMPILE_FLAGS = "-O2 -fPIC"


def _run(argv, *, env=None, capture=False, check=True, text=True):
    result = subprocess.run(
        argv,
        env=env,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=text,
    )
    return result.stdout if capture else ""


def _host_os():
    name = platform.system().lower()
    return {"darwin": "macos", "windows": "windows", "linux": "linux"}.get(name, name)


def _architecture(value=None):
    value = (value or platform.machine()).lower()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(value, value)


def detect_host():
    return {"os": _host_os(), "architecture": _architecture()}


def _version_tuple(value):
    match = re.search(r"\b(\d+(?:\.\d+)+)\b", value)
    if not match:
        raise ValueError("Unable to determine version from: " + value.strip())
    return tuple(int(part) for part in match.group(1).split("."))


def _version_at_least(found, minimum):
    width = max(len(found), len(minimum))
    return found + (0,) * (width - len(found)) >= minimum + (0,) * (width - len(minimum))


def _require_command(command, env):
    found = shutil.which(command, path=env.get("PATH"))
    if not found:
        raise ValueError("Required build tool is not available in PATH: " + command)
    return found


def _compiler_info(command, env):
    executable = _require_command(command, env)
    output = _run([executable, "--version"], env=env, capture=True)
    version = _version_tuple(output)
    return {"path": executable, "version": ".".join(str(part) for part in version)}, version


def _require_build_tools(env):
    cmake = _require_command("cmake", env)
    _require_command("ninja", env)
    output = _run([cmake, "--version"], env=env, capture=True)
    version = _version_tuple(output)
    if not _version_at_least(version, MINIMUM_CMAKE_VERSION):
        raise ValueError(
            "CMake 3.22.1 or newer is required; found " + ".".join(str(part) for part in version)
        )


def _join_flags(*values):
    return " ".join(value.strip() for value in values if value and value.strip())


def _windows_developer_environment(env):
    if env.get("VSCMD_VER"):
        return env

    program_files = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        raise ValueError("Visual Studio C++ tools were not found; install the x64 C++ build tools")
    installation = _run([
        str(vswhere), "-latest", "-products", "*", "-requires",
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath",
    ], env=env, capture=True).strip()
    if not installation:
        raise ValueError("Visual Studio installation with x64 C++ tools was not found")
    developer_command = Path(installation) / "Common7" / "Tools" / "VsDevCmd.bat"
    if not developer_command.is_file():
        raise ValueError("Visual Studio developer command was not found: " + str(developer_command))

    command = 'call "{}" -arch=x64 -host_arch=x64 >nul && set'.format(developer_command)
    result = subprocess.run(
        ["cmd.exe", "/d", "/u", "/s", "/c", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        message = result.stderr.decode("utf-16le", errors="replace").strip()
        raise ValueError("Visual Studio developer environment initialization failed: " + message)
    output = result.stdout.decode("utf-16le", errors="replace")
    initialized = dict(env)
    for line in output.splitlines():
        if not line or line.startswith("=") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        initialized[key] = value
    if not initialized.get("VSCMD_VER"):
        raise ValueError("Visual Studio developer environment initialization failed")
    return initialized


def _android_ndk(env):
    ndk_value = env.get("ANDROID_NDK_HOME") or env.get("ANDROID_NDK_ROOT")
    if not ndk_value:
        raise ValueError("Set ANDROID_NDK_HOME or ANDROID_NDK_ROOT to the Android NDK")
    ndk = Path(ndk_value).expanduser().resolve()
    toolchain_file = ndk / "build" / "cmake" / "android.toolchain.cmake"
    revision_file = ndk / "source.properties"
    if not toolchain_file.is_file() or not revision_file.is_file():
        raise ValueError("Invalid Android NDK directory: " + str(ndk))

    revision_text = ""
    for line in revision_file.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("Pkg.Revision"):
            revision_text = line.partition("=")[2].strip()
            break
    revision = _version_tuple(revision_text)
    if not _version_at_least(revision, MINIMUM_ANDROID_NDK_REVISION):
        raise ValueError(
            "Android NDK 27.2.12479018 or newer is required; found " + revision_text
        )

    host = _host_os()
    architecture = _architecture()
    host_tag = {
        ("linux", "x86_64"): "linux-x86_64",
        ("macos", "x86_64"): "darwin-x86_64",
        ("macos", "arm64"): "darwin-arm64",
        ("windows", "x86_64"): "windows-x86_64",
    }.get((host, architecture))
    if not host_tag:
        raise ValueError("Unsupported Android NDK build host: {}/{}".format(host, architecture))
    executable_suffix = ".exe" if host == "windows" else ""
    clang = ndk / "toolchains" / "llvm" / "prebuilt" / host_tag / "bin" / ("clang" + executable_suffix)
    if not clang.is_file():
        raise ValueError("Android NDK clang compiler was not found: " + str(clang))

    env["ANDROID_NDK_HOME"] = str(ndk)
    env["ANDROID_NDK_ROOT"] = str(ndk)
    return {
        "ndk": ndk,
        "toolchain_file": toolchain_file,
        "revision": revision_text,
        "clang": str(clang),
    }


def initialize_build_environment(profile):
    """Return the prepared child-process environment and target CMake settings."""
    host = detect_host()
    allowed_hosts = profile["hosts"]
    if not any(item["os"] == host["os"] and host["architecture"] in item["architectures"]
               for item in allowed_hosts):
        raise ValueError(
            "Target {} cannot be built on host {}/{}".format(
                profile["id"], host["os"], host["architecture"]
            )
        )
    if profile["family"] != "android" and profile["family"] != host["os"]:
        raise ValueError("Only Android targets support cross-platform build hosts")
    if profile["family"] != "android" and host["architecture"] != profile["architecture"]:
        raise ValueError("This profile supports native builds only")

    env = dict(os.environ)
    if profile["family"] == "windows":
        env = _windows_developer_environment(env)
    _require_build_tools(env)

    args = ["-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_POSITION_INDEPENDENT_CODE=ON"]
    abi = profile["abi"]
    observed = {"host": host, "target_id": profile["id"]}
    family = profile["family"]

    if family == "linux":
        libc, libc_version = platform.libc_ver()
        expected_libc = abi["libc"]
        minimum_libc_version = _version_tuple(abi["minimum_libc_version"])
        found_libc_version = _version_tuple(libc_version) if libc_version else ()
        if libc != expected_libc or not found_libc_version or not _version_at_least(
            minimum_libc_version, found_libc_version
        ):
            raise ValueError(
                "Target {} needs a {} {} or older build environment; found {} {}".format(
                    profile["id"], expected_libc, abi["minimum_libc_version"],
                    libc or "unknown", libc_version or "unknown",
                )
            )
        env.setdefault("CC", "cc")
        env.setdefault("CXX", "c++")
        if len(env["CC"].split()) != 1 or len(env["CXX"].split()) != 1:
            raise ValueError("CC and CXX must each name one compiler executable")
        c_compiler, c_version = _compiler_info(env["CC"], env)
        cxx_compiler, cxx_version = _compiler_info(env["CXX"], env)
        minimum = MINIMUM_LINUX_COMPILER_VERSION
        if not _version_at_least(c_version, minimum) or not _version_at_least(cxx_version, minimum):
            raise ValueError(
                "Linux C and C++ compilers must be version 9.4.0 or newer; found CC={} CXX={}".format(
                    c_compiler["version"], cxx_compiler["version"]
                )
            )
        cflags = _join_flags(
            COMMON_COMPILE_FLAGS,
            "-march={}".format(abi["cpu_baseline"]),
            env.get("CFLAGS", ""),
        )
        cxxflags = _join_flags(abi.get("cxxflags", ""), env.get("CXXFLAGS", ""))
        args.extend([
            "-DCMAKE_C_COMPILER={}".format(c_compiler["path"]),
            "-DCMAKE_CXX_COMPILER={}".format(cxx_compiler["path"]),
        ])
        if cflags:
            args.append("-DCMAKE_C_FLAGS=" + cflags)
        if cxxflags:
            args.append("-DCMAKE_CXX_FLAGS=" + cxxflags)
        observed.update({"c_compiler": c_compiler, "cxx_compiler": cxx_compiler,
                         "libc": libc, "libc_version": libc_version,
                         "cflags": cflags, "cxxflags": cxxflags,
                         "ldflags": env.get("LDFLAGS", "")})
    elif family == "macos":
        env.setdefault("CC", "clang")
        env.setdefault("CXX", "clang++")
        c_compiler, c_version = _compiler_info(env["CC"], env)
        cxx_compiler, cxx_version = _compiler_info(env["CXX"], env)
        sdk_path = _run(["xcrun", "--sdk", "macosx", "--show-sdk-path"], env=env, capture=True).strip()
        sdk_version = _run(["xcrun", "--sdk", "macosx", "--show-sdk-version"], env=env, capture=True).strip()
        cflags = env.get("CFLAGS", "")
        cxxflags = env.get("CXXFLAGS", "")
        args.extend([
            "-DCMAKE_C_COMPILER={}".format(c_compiler["path"]),
            "-DCMAKE_CXX_COMPILER={}".format(cxx_compiler["path"]),
            "-DCMAKE_OSX_ARCHITECTURES={}".format(profile["architecture"]),
            "-DCMAKE_OSX_DEPLOYMENT_TARGET={}".format(abi["minimum_os_version"]),
            "-DCMAKE_OSX_SYSROOT={}".format(sdk_path),
        ])
        if cflags:
            args.append("-DCMAKE_C_FLAGS=" + cflags)
        if cxxflags:
            args.append("-DCMAKE_CXX_FLAGS=" + cxxflags)
        observed.update({"c_compiler": c_compiler, "cxx_compiler": cxx_compiler,
                         "sdk_version": sdk_version,
                         "deployment_target": abi["minimum_os_version"],
                         "cflags": cflags, "cxxflags": cxxflags,
                         "ldflags": env.get("LDFLAGS", "")})
    elif family == "windows":
        toolset = env.get("VCToolsVersion", "")
        version = _version_tuple(toolset) if toolset else ()
        if not version or not _version_at_least(version, MINIMUM_WINDOWS_TOOLSET_VERSION):
            raise ValueError(
                "Visual Studio C++ tools 14.30 or newer are required; found " + (toolset or "unknown")
            )
        env.setdefault("CC", "cl")
        env.setdefault("CXX", "cl")
        c_compiler = _require_command(env["CC"], env)
        cxx_compiler = _require_command(env["CXX"], env)
        args.extend([
            "-DCMAKE_C_COMPILER={}".format(c_compiler),
            "-DCMAKE_CXX_COMPILER={}".format(cxx_compiler),
            "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
            "-DCMAKE_MSVC_RUNTIME_LIBRARY={}".format(abi["runtime_library"]),
        ])
        observed.update({"c_compiler": c_compiler, "cxx_compiler": cxx_compiler,
                         "toolset_version": toolset, "runtime": abi["runtime"],
                         "configuration": "Release"})
    elif family == "android":
        for variable in ("CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS"):
            if env.get(variable):
                raise ValueError(
                    "{} is a host setting and cannot be applied to Android; use the target profile".format(variable)
                )
        ndk = _android_ndk(env)
        args.extend([
            "-DCMAKE_TOOLCHAIN_FILE={}".format(ndk["toolchain_file"]),
            "-DANDROID_ABI={}".format(profile["architecture"]),
            "-DANDROID_PLATFORM=android-{}".format(abi["api_level"]),
            "-DANDROID_NDK={}".format(ndk["ndk"]),
        ])
        clang, clang_version = _compiler_info(ndk["clang"], env)
        observed.update({"ndk_revision": ndk["revision"], "android_clang": clang,
                         "android_clang_version": ".".join(str(part) for part in clang_version),
                         "abi": profile["architecture"], "api_level": abi["api_level"]})
    else:
        raise ValueError("Unsupported target family: " + str(family))

    return {"environment": env, "cmake_args": args, "observed": observed}
