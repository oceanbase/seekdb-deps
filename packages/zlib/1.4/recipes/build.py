"""zlib-specific build and artifact selection."""


def build(ctx):
    ctx.cmake(targets=("zlibstatic",))

    if ctx.target.family == "windows":
        library = ctx.build_dir / "libzs.lib"
    else:
        library = ctx.build_dir / "libz.a"

    ctx.install(library, "lib/" + library.name)
    ctx.install(ctx.source_dir / "zlib.h", "include/zlib.h")
    ctx.install(ctx.build_dir / "zconf.h", "include/zconf.h")
