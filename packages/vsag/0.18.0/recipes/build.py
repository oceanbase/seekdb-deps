"""Build VSAG and stage the archives used by seekdb on each platform."""

from pathlib import Path
import subprocess
import sys


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


def build(ctx):
    options = {
        "CMAKE_INSTALL_PREFIX": str(ctx.build_dir / "install"),
        "ENABLE_CXX11_ABI": True,
        "ENABLE_INTEL_MKL": False,
        "ENABLE_LIBAIO": False,
        "ENABLE_TESTS": False,
        "ENABLE_EXAMPLES": False,
        "ENABLE_TOOLS": False,
        "ROARING_DISABLE_AVX512": True,
        "NUM_BUILDING_JOBS": ctx.jobs,
    }
    try:
        ctx.cmake(options=options, targets=("vsag_static", "fmt"))
    except subprocess.CalledProcessError:
        for log in sorted(ctx.build_dir.rglob("*-configure-*.log")) + sorted(
            ctx.build_dir.rglob("*-build-*.log")
        ):
            print("VSAG dependency log: " + str(log), file=sys.stderr)
            print(log.read_text(errors="replace")[-12000:], file=sys.stderr)
        raise

    for header in sorted((ctx.source_dir / "include/vsag").iterdir()):
        if header.is_file():
            ctx.install(header, "include/vsag/" + header.name)

    libraries = WINDOWS_LIBRARIES if ctx.target.family == "windows" else LIBRARIES
    for name, candidates in libraries.items():
        ctx.install(_built_library(ctx, candidates), "lib/" + name)
    if ctx.target.family == "macos":
        ctx.install(_built_library(ctx, ("src/io/libio.a",)), "lib/libio.a")
    if ctx.target.family == "android":
        ctx.install(
            _built_library(ctx, ("spdlog/install/lib/libspdlog.a",)),
            "lib/libspdlog.a",
        )

    for dependency, (source_path, licenses) in THIRD_PARTY_LICENSES.items():
        for relative in licenses:
            candidate = ctx.build_dir / source_path / relative
            if candidate.is_file():
                ctx.install(candidate, "share/licenses/vsag/third-party/" + dependency + "/" + relative)
    for relative in ("LICENSE", "NOTICE.txt"):
        candidate = ctx.source_dir / "extern/diskann/DiskANN" / relative
        if candidate.is_file():
            ctx.install(candidate, "share/licenses/vsag/third-party/diskann/" + relative)
