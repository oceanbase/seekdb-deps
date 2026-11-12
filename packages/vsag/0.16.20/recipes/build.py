"""Build the VSAG version used by seekdb's 1.1.0 Linux RPM."""

from pathlib import Path
import shutil
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
    for relative in candidates:
        path = ctx.build_dir / relative
        if path.is_file():
            return path
    raise ValueError("VSAG build output is missing: " + ", ".join(candidates))


def _compiler_library(ctx, compiler, name):
    path = subprocess.check_output(
        [compiler, "-print-file-name=" + name], env=ctx.environment, text=True,
    ).strip()
    if not path or path == name or not Path(path).is_file():
        raise ValueError("Compiler runtime is missing: " + name)
    return Path(path)


def build(ctx):
    try:
        ctx.cmake(options={
            "CMAKE_INSTALL_PREFIX": str(ctx.build_dir / "install"),
            "ENABLE_CXX11_ABI": True,
            "ENABLE_INTEL_MKL": False,
            "ENABLE_TESTS": False,
            "ENABLE_EXAMPLES": False,
            "ENABLE_TOOLS": False,
            "ROARING_DISABLE_AVX512": True,
            "NUM_BUILDING_JOBS": ctx.jobs,
        }, targets=("vsag_static", "fmt"))
    except subprocess.CalledProcessError:
        for log in sorted(ctx.build_dir.rglob("*-build-*.log")):
            print("VSAG dependency log: " + str(log), file=sys.stderr)
            lines = log.read_text(errors="replace").splitlines()
            for index, line in enumerate(lines):
                if any(word in line.lower() for word in ("failed:", "error:", "fatal error")):
                    print("\n".join(lines[max(0, index - 2):index + 5]), file=sys.stderr)
            print("\n".join(lines[-20:]), file=sys.stderr)
        raise

    for header in sorted((ctx.source_dir / "include/vsag").iterdir()):
        if header.is_file():
            ctx.install(header, "include/vsag/" + header.name)

    for name, candidates in LIBRARIES.items():
        ctx.install(_built_library(ctx, candidates), "lib/" + name)

    fortran = ctx.environment.get("FC")
    if not fortran:
        cc = ctx.environment["CC"]
        fortran = cc[:-3] + "-gfortran" if cc.endswith("-cc") else "gfortran"
    runtimes = {
        "libgfortran_static.a": (fortran, "libgfortran.a"),
        "libgomp_static.a": (ctx.environment["CXX"], "libgomp.a"),
    }
    if ctx.target.architecture == "x86_64":
        runtimes["libquadmath_static.a"] = (fortran, "libquadmath.a")
    runtime_dir = ctx.build_dir / "runtime"
    runtime_dir.mkdir()
    for destination, (compiler, name) in runtimes.items():
        copied = runtime_dir / destination
        shutil.copy2(_compiler_library(ctx, compiler, name), copied)
        ctx.install(copied, "lib/" + destination)

    licenses = Path(__file__).resolve().parents[1] / "licenses"
    for name in ("COPYING3", "COPYING.RUNTIME"):
        copied = runtime_dir / name
        shutil.copy2(licenses / name, copied)
        ctx.install(copied, "share/licenses/vsag/third-party/gcc/" + name)

    for dependency, (source_path, licenses) in THIRD_PARTY_LICENSES.items():
        for relative in licenses:
            candidate = ctx.build_dir / source_path / relative
            if candidate.is_file():
                ctx.install(candidate, "share/licenses/vsag/third-party/" + dependency + "/" + relative)
    for relative in ("LICENSE", "NOTICE.txt"):
        candidate = ctx.source_dir / "extern/diskann/DiskANN" / relative
        if candidate.is_file():
            ctx.install(candidate, "share/licenses/vsag/third-party/diskann/" + relative)
