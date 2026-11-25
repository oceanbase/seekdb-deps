"""Extract the original obshell executable and verify its Linux ABI."""

import re
import subprocess


def build(ctx):
    binary = ctx.build_dir / "obshell"
    rpm = ctx.source_dir / "upstream.rpm"
    with subprocess.Popen(["rpm2cpio", str(rpm)], stdout=subprocess.PIPE) as payload:
        try:
            with binary.open("wb") as output:
                subprocess.run(
                    ["cpio", "--extract", "--to-stdout", "--quiet", "./home/admin/oceanbase/bin/obshell"],
                    stdin=payload.stdout, stdout=output, check=True,
                )
        finally:
            payload.stdout.close()
        if payload.wait():
            raise RuntimeError("Could not read obshell RPM payload")

    environment = dict(ctx.environment, LC_ALL="C")
    elf = subprocess.check_output(
        ["readelf", "-h", "--version-info", str(binary)], env=environment, text=True,
    )
    machine = {"x86_64": "Advanced Micro Devices X86-64", "arm64": "AArch64"}[ctx.target.architecture]
    if not re.search(r"(?m)^\s*Machine:\s+" + re.escape(machine) + r"$", elf):
        raise ValueError("obshell architecture does not match " + ctx.target.id)
    versions = [tuple(map(int, value.split(".")))
                for value in re.findall(r"Name: GLIBC_(\d+(?:\.\d+)+)", elf)]
    required = max(versions)
    baseline = tuple(map(int, ctx.profile["abi"]["minimum_libc_version"].split(".")))
    if required > baseline:
        raise ValueError("obshell exceeds glibc baseline for " + ctx.target.id)
    ctx.observed_build_environment["glibc_requirement"] = ".".join(map(str, required))
    binary.chmod(0o755)
    ctx.install(binary, "bin/obshell")
