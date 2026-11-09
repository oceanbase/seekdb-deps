"""Build the Linux VSAG archive and the libraries needed to link it."""

from pathlib import Path
import os
import shutil
import subprocess
import sys


FETCHCONTENT_SOURCES = {
    "NLOHMANN_JSON": "json",
    "CPUINFO": "cpuinfo",
    "FMT": "fmt",
    "THREAD_POOL": "thread_pool",
    "TSL": "tsl",
    "ROARINGBITMAP": "roaringbitmap",
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

THIRD_PARTY_LICENSES = {
    "antlr4": ("LICENSE.txt",),
    "boost": ("LICENSE_1_0.txt",),
    "openblas": ("LICENSE", "GotoBLAS_00License.txt", "lapack-netlib/LICENSE"),
    "spdlog": ("LICENSE",),
    "json": ("LICENSE.MIT",),
    "cpuinfo": ("LICENSE", "deps/clog/LICENSE"),
    "fmt": ("LICENSE",),
    "thread_pool": ("COPYING",),
    "tsl": ("LICENSE",),
    "roaringbitmap": ("LICENSE",),
}


def _compiler_library(ctx, compiler, name):
    path = subprocess.check_output(
        [compiler, "-print-file-name=" + name],
        env=ctx.environment, text=True,
    ).strip()
    if not path or path == name or not Path(path).is_file():
        raise ValueError("Compiler runtime is missing: " + name)
    return Path(path)


def _built_library(ctx, candidates):
    for name in candidates:
        path = ctx.build_dir / name
        if path.is_file():
            return path
    raise ValueError("VSAG build output is missing: " + ", ".join(candidates))


def build(ctx):
    if ctx.target.family != "linux":
        raise ValueError("VSAG 0.15.12 package currently supports Linux targets")

    fc = ctx.environment.get("FC", "gfortran")
    if not shutil.which(fc, path=ctx.environment.get("PATH")):
        raise ValueError("VSAG build needs a Fortran compiler for OpenBLAS: " + fc)
    ctx.environment["FC"] = fc

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
        "ENABLE_INTEL_MKL": False,
        "ENABLE_TESTS": False,
        "ENABLE_EXAMPLES": False,
        "ENABLE_TOOLS": False,
        "ROARING_DISABLE_AVX512": True,
        "NUM_BUILDING_JOBS": ctx.jobs,
        "FETCHCONTENT_FULLY_DISCONNECTED": True,
    }
    for content_name, directory in FETCHCONTENT_SOURCES.items():
        source = ctx.source_dir / ("vendor_" + directory)
        if not source.is_dir():
            raise ValueError("Pinned VSAG dependency source is missing: " + str(source))
        options["FETCHCONTENT_SOURCE_DIR_" + content_name] = str(source)

    try:
        ctx.cmake(options=options, targets=("vsag_static", "fmt"))
    except subprocess.CalledProcessError:
        for log in sorted((ctx.build_dir / "cmake/openblas/src/openblas-stamp").glob("openblas-build-*.log")):
            print("OpenBLAS build log: " + str(log), file=sys.stderr)
            print(log.read_text(errors="replace")[-16000:], file=sys.stderr)
        raise

    for header in sorted((ctx.source_dir / "include/vsag").iterdir()):
        if header.is_file():
            ctx.install(header, "include/vsag/" + header.name)

    staged_libraries = []
    for name, candidates in LIBRARIES.items():
        ctx.install(_built_library(ctx, candidates), "lib/" + name)
        staged_libraries.append(ctx.stage_dir / "lib" / name)

    compiler_libraries = {
        "libgfortran_static.a": (fc, "libgfortran.a"),
        "libgomp_static.a": (ctx.environment["CXX"], "libgomp.a"),
    }
    if ctx.target.architecture == "x86_64":
        compiler_libraries["libquadmath_static.a"] = (fc, "libquadmath.a")
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

    for dependency, licenses in THIRD_PARTY_LICENSES.items():
        for relative in licenses:
            ctx.install(
                ctx.source_dir / ("vendor_" + dependency) / relative,
                "share/licenses/vsag/third-party/" + dependency + "/" + relative,
            )
    for relative in ("LICENSE", "NOTICE.txt"):
        ctx.install(
            ctx.source_dir / "extern/diskann/DiskANN" / relative,
            "share/licenses/vsag/third-party/diskann/" + relative,
        )
    license_directory = Path(__file__).resolve().parents[1] / "licenses"
    for name in ("COPYING3", "COPYING.RUNTIME"):
        staged_license = ctx.build_dir / name
        shutil.copyfile(license_directory / name, staged_license)
        ctx.install(staged_license, "share/licenses/vsag/third-party/gcc/" + name)
