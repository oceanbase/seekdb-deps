"""Build and stage the Linux libaio shared library."""

import shutil


def build(ctx):
    source = ctx.build_dir / "src"
    shutil.copytree(ctx.source_dir / "src", source)
    library = "libaio.so.1.0.1"

    ctx.run(
        ["make", "-j", str(ctx.jobs), library],
        cwd=source,
        env={"CFLAGS": ctx.observed_build_environment["cflags"]},
    )

    ctx.install(source / "libaio.h", "include/libaio.h")
    ctx.install(source / library, "lib/" + library)
    lib_dir = ctx.stage_dir / "lib"
    (lib_dir / "libaio.so.1").symlink_to(library)
    (lib_dir / "libaio.so").symlink_to("libaio.so.1")
