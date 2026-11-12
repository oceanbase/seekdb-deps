"""Build VSAG and stage the archives used by seekdb on each platform."""

from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile


LIBOMP_VERSION = "23.1.5"
LIBOMP_SOURCE_SHA256 = "fb50b1eac93ddeb3aa8dab4cfe324dff74f4a9c2bdee6160c9e36188f8d06a6e"
WINDOWS_LIBOMP_VERSION = "18.1.8"
WINDOWS_LIBOMP_SOURCE_SHA256 = {
    "openmp": "60ed57245e73894e4a2a89b15889f367bd906abfe6d3f92e1718223d4b496150",
    "cmake": "59badef592dd34893cd319d42b323aaa990b452d05c7180ff20f23ab1b41e837",
}


LIBRARIES = {
    "libvsag_static.a": ("src/libvsag_static.a",),
    "libsimd.a": ("src/simd/libsimd.a",),
    "libcpuinfo.a": ("_deps/cpuinfo-build/libcpuinfo.a",),
    "libdiskann.a": ("libdiskann.a",),
    "libopenblas.a": ("openblas/install/lib/libopenblas.a",),
    "libantlr4-runtime.a": (
        "antlr4/install/lib/libantlr4-runtime.a",
        "antlr4/install/lib64/libantlr4-runtime.a",
    ),
    "libantlr4-autogen.a": ("libantlr4-autogen.a",),
    "libfmt.a": ("_deps/fmt-build/libfmt.a",),
}

WINDOWS_LIBRARIES = {
    "vsag_static.lib": ("src/vsag_static.lib",),
    "simd.lib": ("src/simd/simd.lib",),
    "cpuinfo.lib": ("_deps/cpuinfo-build/cpuinfo.lib",),
    "diskann.lib": ("diskann.lib",),
    "openblas.lib": ("openblas/install/lib/openblas.lib",),
    "antlr4-runtime-static.lib": ("antlr4/install/lib/antlr4-runtime-static.lib",),
    "antlr4-autogen.lib": ("antlr4-autogen.lib",),
    "fmt.lib": ("_deps/fmt-build/fmt.lib",),
}

THIRD_PARTY_LICENSES = {
    "antlr4": ("antlr4/source", ("LICENSE.txt",)),
    "boost": ("boost/source", ("LICENSE_1_0.txt",)),
    "openblas": ("openblas/source", ("LICENSE", "GotoBLAS_00License.txt", "lapack-netlib/LICENSE")),
    "spdlog": ("spdlog/source", ("LICENSE",)),
    "json": ("_deps/nlohmann_json-src", ("LICENSE.MIT",)),
    "cpuinfo": ("_deps/cpuinfo-src", ("LICENSE", "deps/clog/LICENSE")),
    "fmt": ("_deps/fmt-src", ("LICENSE",)),
    "thread_pool": ("_deps/thread_pool-src", ("COPYING",)),
    "tsl": ("_deps/tsl-src", ("LICENSE",)),
    "roaringbitmap": ("_deps/roaringbitmap-src", ("LICENSE",)),
}


def _built_library(ctx, candidates):
    for name in candidates:
        path = ctx.build_dir / name
        if path.is_file():
            return path
    raise ValueError("VSAG build output is missing: " + ", ".join(candidates))


def _compiler_library(ctx, compiler, name):
    path = subprocess.check_output(
        [compiler, "-print-file-name=" + name],
        env=ctx.environment, text=True,
    ).strip()
    if not path or path == name or not Path(path).is_file():
        raise ValueError("Compiler runtime is missing: " + name)
    return Path(path)


def _verify_macos_deployment_target(ctx, archive):
    readobj = Path(ctx.environment["CXX"]).resolve().parent / "llvm-readobj"
    metadata = subprocess.check_output(
        [str(readobj), "--macho-version-min", str(archive)],
        env=ctx.environment, text=True,
    )
    minimum_versions = re.findall(r"\bVersion: (\d+)\.(\d+)\b", metadata)
    target_version = tuple(map(int, ctx.target.document["abi"]["minimum_os_version"].split(".")))
    if not minimum_versions or any(
        tuple(map(int, version)) > target_version for version in minimum_versions
    ):
        raise ValueError(str(archive) + " exceeds macOS deployment target")


def _verify_ios_archive(ctx, archive):
    archs = subprocess.check_output(
        ["xcrun", "lipo", "-archs", str(archive)], env=ctx.environment, text=True,
    ).strip()
    if archs != "arm64":
        raise ValueError(str(archive) + " has unexpected architecture: " + archs)
    metadata = subprocess.check_output(
        ["xcrun", "otool", "-l", str(archive)], env=ctx.environment, text=True,
    )
    expected = "7" if ctx.target.document["abi"]["sdk"] == "iphonesimulator" else "2"
    platforms = set(re.findall(r"^\s*platform\s+(\S+)", metadata, re.MULTILINE))
    if not platforms or not platforms.issubset({expected, "IOSSIMULATOR" if expected == "7" else "IOS"}):
        raise ValueError(str(archive) + " has unexpected Apple platforms: " + repr(platforms))
    minimum = tuple(map(int, ctx.target.document["abi"]["minimum_os_version"].split(".")))
    versions = re.findall(r"^\s*minos\s+(\d+)\.(\d+)", metadata, re.MULTILINE)
    if not versions or any(tuple(map(int, version)) > minimum for version in versions):
        raise ValueError(str(archive) + " exceeds iOS deployment target")


def _build_apple_openmp(ctx):
    archive = ctx.build_dir / ("llvm-project-" + LIBOMP_VERSION + ".src.tar.xz")
    url = ("https://github.com/llvm/llvm-project/releases/download/llvmorg-" + LIBOMP_VERSION
           + "/" + archive.name)
    ctx.run(["curl", "--fail", "--location", "--retry", "3", "--output", archive, url])
    with archive.open("rb") as input_file:
        actual_sha256 = hashlib.file_digest(input_file, "sha256").hexdigest()
    if actual_sha256 != LIBOMP_SOURCE_SHA256:
        raise ValueError("LLVM OpenMP source SHA256 mismatch: " + actual_sha256)

    ctx.run(["tar", "-xf", archive, "-C", ctx.build_dir])
    source_dir = ctx.build_dir / ("llvm-project-" + LIBOMP_VERSION + ".src/runtimes")
    build_dir = ctx.build_dir / "openmp-static-build"
    command = [
        "cmake", "-S", source_dir, "-B", build_dir, "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_C_COMPILER=" + ctx.environment["CC"],
        "-DCMAKE_CXX_COMPILER=" + ctx.environment["CXX"],
        "-DCMAKE_OSX_ARCHITECTURES=" + ctx.target.architecture,
        "-DCMAKE_OSX_DEPLOYMENT_TARGET=" + ctx.target.document["abi"]["minimum_os_version"],
        "-DLLVM_ENABLE_RUNTIMES=openmp",
        "-DLIBOMP_ENABLE_SHARED=OFF",
        "-DLIBOMP_INSTALL_ALIASES=OFF",
        "-DOPENMP_ENABLE_LIBOMPTARGET=OFF",
        "-DOPENMP_ENABLE_OMPT_TOOLS=OFF",
    ]
    if ctx.target.family == "ios":
        command.extend(arg for arg in ctx.cmake_args if arg.startswith((
            "-DCMAKE_SYSTEM_NAME=", "-DCMAKE_SYSTEM_VERSION=", "-DCMAKE_OSX_SYSROOT=",
            "-DCMAKE_C_COMPILER_TARGET=", "-DCMAKE_CXX_COMPILER_TARGET=",
            "-DCMAKE_TRY_COMPILE_TARGET_TYPE=",
        )))
    ctx.run(command)
    ctx.run(["cmake", "--build", build_dir, "--parallel", str(ctx.jobs)])
    libraries = list(build_dir.rglob("libomp.a"))
    if len(libraries) != 1:
        raise ValueError("Expected one macOS static OpenMP library, found " + str(len(libraries)))
    if ctx.target.family == "ios":
        _verify_ios_archive(ctx, libraries[0])
    else:
        _verify_macos_deployment_target(ctx, libraries[0])
    return libraries[0]


def _build_windows_openmp(ctx):
    for component, expected_sha256 in WINDOWS_LIBOMP_SOURCE_SHA256.items():
        archive = ctx.build_dir / (component + "-" + WINDOWS_LIBOMP_VERSION + ".src.tar.xz")
        url = ("https://github.com/llvm/llvm-project/releases/download/llvmorg-"
               + WINDOWS_LIBOMP_VERSION + "/" + archive.name)
        ctx.run(["curl", "--fail", "--location", "--retry", "3", "--output", archive, url])
        with archive.open("rb") as input_file:
            actual_sha256 = hashlib.file_digest(input_file, "sha256").hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError("LLVM " + component + " source SHA256 mismatch: " + actual_sha256)
        with tarfile.open(archive, "r:xz") as source:
            source.extractall(ctx.build_dir)

    (ctx.build_dir / ("cmake-" + WINDOWS_LIBOMP_VERSION + ".src")).rename(
        ctx.build_dir / "cmake"
    )
    source_dir = ctx.build_dir / ("openmp-" + WINDOWS_LIBOMP_VERSION + ".src")
    build_dir = ctx.build_dir / "openmp-windows-build"
    ctx.run([
        "cmake", "-S", source_dir, "-B", build_dir, "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_C_COMPILER=" + ctx.environment["CC"],
        "-DCMAKE_CXX_COMPILER=" + ctx.environment["CXX"],
        "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
        "-DCMAKE_MSVC_RUNTIME_LIBRARY=" + ctx.target.document["abi"]["runtime_library"],
        "-DLIBOMP_ENABLE_SHARED=ON",
        "-DLIBOMP_INSTALL_ALIASES=OFF",
        "-DOPENMP_ENABLE_LIBOMPTARGET=OFF",
        "-DOPENMP_ENABLE_OMPT_TOOLS=OFF",
    ])
    ctx.run(["cmake", "--build", build_dir, "--parallel", str(ctx.jobs)])
    libraries = {}
    for name in ("libomp.lib", "libomp.dll"):
        matches = list(build_dir.rglob(name))
        if len(matches) != 1:
            raise ValueError("Expected one Windows OpenMP " + name + ", found " + str(len(matches)))
        libraries[name] = matches[0]
    return libraries


def build(ctx):
    if ctx.target.family == "linux":
        libaio_header = Path("/usr/include/libaio.h")
        if not libaio_header.is_file():
            raise ValueError("VSAG build needs the libaio development header")
        build_headers = ctx.build_dir / "build-headers"
        build_headers.mkdir()
        shutil.copy2(libaio_header, build_headers / "libaio.h")
        ctx.environment["CPATH"] = os.pathsep.join(
            value for value in (str(build_headers), ctx.environment.get("CPATH", "")) if value
        )

    options = {
        "CMAKE_INSTALL_PREFIX": str(ctx.build_dir / "install"),
        "ENABLE_CXX11_ABI": True,
        "ENABLE_WERROR": ctx.target.family == "linux",
        "ENABLE_INTEL_MKL": False,
        "ENABLE_LIBAIO": ctx.target.family == "linux",
        "ENABLE_TESTS": False,
        "ENABLE_EXAMPLES": False,
        "ENABLE_TOOLS": False,
        "ROARING_DISABLE_AVX512": True,
        "NUM_BUILDING_JOBS": ctx.jobs,
    }
    try:
        targets = ("vsag_static", "fmt", "io_static") if ctx.target.family in ("macos", "ios") else ("vsag_static", "fmt")
        ctx.cmake(options=options, targets=targets)
    except subprocess.CalledProcessError:
        logs = sorted(ctx.build_dir.rglob("*-configure-*.log"))
        logs += sorted(ctx.build_dir.rglob("*-build-*.log"))
        logs += sorted(ctx.build_dir.rglob("*-install-*.log"))
        for log in logs:
            print("VSAG dependency log: " + str(log), file=sys.stderr)
            lines = log.read_text(errors="replace").splitlines()
            for index, line in enumerate(lines):
                if any(word in line.lower() for word in ("failed:", "error:", "error c", "fatal error", "undefined")):
                    print("\n".join(lines[max(0, index - 2):index + 5]), file=sys.stderr)
            print("\n".join(lines[-20:]), file=sys.stderr)
        raise

    for header in sorted((ctx.source_dir / "include/vsag").iterdir()):
        if header.is_file():
            ctx.install(header, "include/vsag/" + header.name)

    libraries = WINDOWS_LIBRARIES if ctx.target.family == "windows" else LIBRARIES
    staged_libraries = []
    for name, candidates in libraries.items():
        archive = _built_library(ctx, candidates)
        if ctx.target.family == "macos":
            _verify_macos_deployment_target(ctx, archive)
        elif ctx.target.family == "ios":
            _verify_ios_archive(ctx, archive)
        ctx.install(archive, "lib/" + name)
        staged_libraries.append(ctx.stage_dir / "lib" / name)
    if ctx.target.family in ("macos", "ios"):
        io_archive = _built_library(ctx, ("src/io/libio.a",))
        if ctx.target.family == "ios":
            _verify_ios_archive(ctx, io_archive)
        else:
            _verify_macos_deployment_target(ctx, io_archive)
        ctx.install(io_archive, "lib/libio.a")
        ctx.install(_build_apple_openmp(ctx), "lib/libomp_static.a")
    if ctx.target.family == "windows":
        openmp = _build_windows_openmp(ctx)
        ctx.install(openmp["libomp.lib"], "lib/libomp.lib")
        ctx.install(openmp["libomp.dll"], "bin/libomp.dll")
    if ctx.target.family == "android":
        ctx.install(
            _built_library(ctx, ("spdlog/install/lib/libspdlog.a",)),
            "lib/libspdlog.a",
        )
    if ctx.target.family == "linux":
        fortran = ctx.environment.get("FC")
        if not fortran:
            cc = ctx.environment["CC"]
            fortran = cc[:-3] + "-gfortran" if cc.endswith("-cc") else "gfortran"
        compiler_libraries = {
            "libgomp_static.a": (ctx.environment["CXX"], "libgomp.a"),
            "libgfortran_static.a": (fortran, "libgfortran.a"),
        }
        if ctx.target.architecture == "x86_64":
            compiler_libraries["libquadmath_static.a"] = (
                fortran, "libquadmath.a"
            )
        runtime_dir = ctx.build_dir / "runtime"
        runtime_dir.mkdir()
        for destination, (compiler, name) in compiler_libraries.items():
            copied = runtime_dir / destination
            shutil.copy2(_compiler_library(ctx, compiler, name), copied)
            ctx.install(copied, "lib/" + destination)
            staged_libraries.append(ctx.stage_dir / "lib" / destination)

        strip_tool = subprocess.check_output(
            [ctx.environment["CC"], "-print-prog-name=strip"],
            env=ctx.environment, text=True,
        ).strip()
        for archive in staged_libraries:
            ctx.run([strip_tool, "--strip-debug", str(archive)])

        license_directory = Path(__file__).resolve().parents[1] / "licenses"
        for name in ("COPYING3", "COPYING.RUNTIME"):
            staged_license = ctx.build_dir / name
            shutil.copyfile(license_directory / name, staged_license)
            ctx.install(staged_license, "share/licenses/vsag/third-party/gcc/" + name)

    if ctx.target.family in ("macos", "ios", "windows"):
        libomp_license = Path(__file__).resolve().parents[1] / "licenses/LIBOMP-LICENSE.TXT"
        staged_license = ctx.build_dir / "LIBOMP-LICENSE.TXT"
        shutil.copyfile(libomp_license, staged_license)
        ctx.install(staged_license, "share/licenses/vsag/third-party/libomp/LICENSE.TXT")

    for dependency, (source_path, licenses) in THIRD_PARTY_LICENSES.items():
        for relative in licenses:
            candidate = ctx.build_dir / source_path / relative
            if candidate.is_file():
                ctx.install(candidate, "share/licenses/vsag/third-party/" + dependency + "/" + relative)
    for relative in ("LICENSE", "NOTICE.txt"):
        candidate = ctx.source_dir / "extern/diskann/DiskANN" / relative
        if candidate.is_file():
            ctx.install(candidate, "share/licenses/vsag/third-party/diskann/" + relative)
