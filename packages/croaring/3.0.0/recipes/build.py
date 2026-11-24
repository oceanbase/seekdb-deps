"""Build CRoaring without upstream test and benchmark dependencies."""


def build(ctx):
    windows = ctx.target.family == "windows"
    ctx.cmake(options={
        "CMAKE_ARCHIVE_OUTPUT_DIRECTORY": str(ctx.build_dir),
        "CMAKE_RUNTIME_OUTPUT_DIRECTORY": str(ctx.build_dir),
        "ENABLE_ROARING_TESTS": False,
        "ROARING_BUILD_STATIC": not windows,
        "ROARING_DISABLE_AVX512": True,
        "ROARING_USE_CPM": False,
    }, targets=("roaring",))

    include = ctx.source_dir / "include"
    for header in sorted((include / "roaring").rglob("*.h")):
        ctx.install(header, "include/" + header.relative_to(include).as_posix())
    for header in sorted((ctx.source_dir / "cpp").glob("*.hh")):
        ctx.install(header, "include/roaring/" + header.name)

    if windows:
        ctx.install(ctx.build_dir / "roaring.lib", "lib/roaring.lib")
        ctx.install(ctx.build_dir / "roaring.dll", "bin/roaring.dll")
    else:
        ctx.install(ctx.build_dir / "libroaring.a", "lib/libroaring.a")
