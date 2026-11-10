"""Build VSAG and stage the archives used by seekdb on each platform."""

from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import sys


LIBOMP_VERSION = "23.1.5"
LIBOMP_SOURCE_SHA256 = "fb50b1eac93ddeb3aa8dab4cfe324dff74f4a9c2bdee6160c9e36188f8d06a6e"


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


def _build_macos_openmp(ctx):
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
    ctx.run([
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
    ])
    ctx.run(["cmake", "--build", build_dir, "--parallel", str(ctx.jobs)])
    libraries = list(build_dir.rglob("libomp.a"))
    if len(libraries) != 1:
        raise ValueError("Expected one macOS static OpenMP library, found " + str(len(libraries)))
    readobj = Path(ctx.environment["CXX"]).resolve().parent / "llvm-readobj"
    metadata = subprocess.check_output(
        [str(readobj), "--macho-version-min", str(libraries[0])],
        env=ctx.environment, text=True,
    )
    minimum_versions = re.findall(r"\bVersion: (\d+)\.(\d+)\b", metadata)
    target_version = tuple(map(int, ctx.target.document["abi"]["minimum_os_version"].split(".")))
    if not minimum_versions or any(
        tuple(map(int, version)) > target_version for version in minimum_versions
    ):
        raise ValueError("Static OpenMP library exceeds macOS deployment target")
    return libraries[0]


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
        "ENABLE_WERROR": ctx.target.family != "windows",
        "ENABLE_INTEL_MKL": False,
        "ENABLE_LIBAIO": ctx.target.family == "linux",
        "ENABLE_TESTS": False,
        "ENABLE_EXAMPLES": False,
        "ENABLE_TOOLS": False,
        "ROARING_DISABLE_AVX512": True,
        "NUM_BUILDING_JOBS": ctx.jobs,
    }
    try:
        targets = ("vsag_static", "fmt", "io_static") if ctx.target.family == "macos" else ("vsag_static", "fmt")
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
        ctx.install(_built_library(ctx, candidates), "lib/" + name)
        staged_libraries.append(ctx.stage_dir / "lib" / name)
    if ctx.target.family == "macos":
        ctx.install(_built_library(ctx, ("src/io/libio.a",)), "lib/libio.a")
        ctx.install(_build_macos_openmp(ctx), "lib/libomp_static.a")
    if ctx.target.family == "windows":
        compiler_root = Path(ctx.environment["CXX"]).resolve().parent.parent
        for source, destination in (
            (compiler_root / "lib/libomp.lib", "lib/libomp.lib"),
            (compiler_root / "bin/libomp.dll", "bin/libomp.dll"),
        ):
            if not source.is_file():
                raise ValueError("Windows LLVM OpenMP runtime is missing: " + str(source))
            copied = ctx.build_dir / source.name
            shutil.copy2(source, copied)
            ctx.install(copied, destination)
    if ctx.target.family == "android":
        ctx.install(
            _built_library(ctx, ("spdlog/install/lib/libspdlog.a",)),
            "lib/libspdlog.a",
        )
    if ctx.target.family == "linux":
        compiler_libraries = {
            "libgomp_static.a": (ctx.environment["CXX"], "libgomp.a"),
        }
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

    if ctx.target.family in ("macos", "windows"):
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
