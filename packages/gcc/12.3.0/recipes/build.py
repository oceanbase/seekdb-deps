"""Build GCC 12.3 and stage the C++ runtime used by seekdb's Clang toolchain."""

from pathlib import Path
import shutil
import subprocess


PREFIX = Path("usr/local/oceanbase/devtools")


def _copy(source, destination):
    if source.is_symlink():
        destination.symlink_to(source.readlink())
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    elif source.is_file():
        shutil.copy2(source, destination)
    else:
        raise ValueError("Required GCC output is missing: " + str(source))


def build(ctx):
    if ctx.target.family != "linux":
        raise ValueError("GCC runtime package is only used by Linux seekdb builds")

    triple = "{}-redhat-linux".format("aarch64" if ctx.target.architecture == "arm64" else "x86_64")
    configure = [
        str(ctx.source_dir / "configure"),
        "--build=" + triple,
        "--prefix=/" + PREFIX.as_posix(),
        "--enable-languages=c,c++",
        "--disable-bootstrap",
        "--disable-multilib",
        "--enable-shared",
        "--enable-threads=posix",
        "--enable-checking=release",
        "--disable-libunwind-exceptions",
        "--with-gcc-major-version-only",
        # GCC 12 forces ABI 0 when dual ABI is disabled; seekdb uses ABI 1.
        "--with-default-libstdcxx-abi=new",
        "--disable-nls",
        "--disable-libsanitizer",
        "--disable-libssp",
        "--disable-libquadmath",
        "--disable-libgfortran",
        "--disable-libgomp",
        "--disable-libitm",
        "--disable-libvtv",
        "--disable-libcc1",
        "--disable-libstdcxx-pch",
    ]
    compiler_sysroot = subprocess.check_output(
        [ctx.environment["CXX"], "-print-sysroot"],
        env=ctx.environment, text=True,
    ).strip()
    if compiler_sysroot:
        configure.append("--with-sysroot=" + compiler_sysroot)
    ctx.run(configure)
    ctx.run(["make", "-j", str(ctx.jobs)])
    installed = ctx.build_dir / "installed"
    ctx.run(["make", "install", "DESTDIR=" + str(installed)])

    stage(ctx, installed)


def stage(ctx, installed):
    triple = "{}-redhat-linux".format("aarch64" if ctx.target.architecture == "arm64" else "x86_64")
    source_prefix = installed / PREFIX
    target_prefix = ctx.stage_dir / PREFIX
    (target_prefix / "include").mkdir(parents=True)
    (target_prefix / "lib").mkdir(parents=True)
    (target_prefix / "lib64").mkdir(parents=True)
    _copy(source_prefix / "include/c++", target_prefix / "include/c++")
    source_gcc_lib = source_prefix / "lib/gcc" / triple / "12"
    target_gcc_lib = target_prefix / "lib/gcc" / triple / "12"
    target_gcc_lib.mkdir(parents=True)
    for name in ("crtbegin.o", "crtbeginS.o", "crtbeginT.o", "crtend.o", "crtendS.o",
                 "libgcc.a", "libgcc_eh.a", "libgcov.a"):
        _copy(source_gcc_lib / name, target_gcc_lib / name)
    for name in (
        "libstdc++.a", "libstdc++.so", "libstdc++.so.6", "libstdc++.so.6.0.30",
        "libstdc++fs.a", "libsupc++.a", "libgcc_s.so", "libgcc_s.so.1",
        "libatomic.a", "libatomic.so", "libatomic.so.1", "libatomic.so.1.2.0",
    ):
        candidate = source_prefix / "lib64" / name
        if candidate.exists():
            _copy(candidate, target_prefix / "lib64" / name)
    strip_tool = subprocess.check_output(
        [ctx.environment["CC"], "-print-prog-name=strip"],
        env=ctx.environment, text=True,
    ).strip()
    for path in (*target_gcc_lib.iterdir(), *(target_prefix / "lib64").iterdir()):
        if (path.is_file() and not path.is_symlink()
                and (path.suffix in (".a", ".o") or ".so." in path.name)):
            ctx.run([strip_tool, "--strip-debug", str(path)])
    ctx.install(ctx.source_dir / "COPYING.RUNTIME", "share/licenses/gcc/COPYING.RUNTIME")
