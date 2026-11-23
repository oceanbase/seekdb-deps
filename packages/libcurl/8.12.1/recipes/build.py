"""Build static libcurl against the pinned OpenSSL package for this target."""

import shlex


OPENSSL_VERSION = "3.5.9"


def build(ctx):
    openssl = ctx.source_dir / ("openssl-" + ctx.target.id)
    environment = {
        "CPPFLAGS": "-I" + shlex.quote(str(openssl / "include")),
        "CFLAGS": ctx.observed_build_environment["cflags"],
        "LDFLAGS": "-L" + shlex.quote(str(openssl / "lib")) + " " + ctx.environment.get("LDFLAGS", ""),
        # Only use the pinned OpenSSL headers and archives.
        "PKG_CONFIG_PATH": "",
        "PKG_CONFIG_LIBDIR": str(openssl / "lib/pkgconfig"),
    }
    ctx.run([
        ctx.source_dir / "configure",
        "--with-openssl=" + str(openssl),
        "--disable-shared", "--enable-static", "--with-pic",
        "--without-zlib", "--without-brotli", "--without-zstd", "--without-libpsl",
        "--without-libidn2", "--without-nghttp2", "--without-libssh2",
        "--disable-ftp", "--disable-ldap", "--disable-ldaps", "--disable-cookies",
        "--disable-rtsp", "--disable-pop3", "--disable-smtp", "--disable-imap",
        "--disable-telnet", "--disable-tftp", "--disable-verbose", "--disable-gopher",
        "--with-ca-bundle=/etc/pki/tls/certs/ca-bundle.crt", "--without-ca-path",
    ], env=environment)
    ctx.run(["make", "-j", str(ctx.jobs), "-C", "lib"], env=environment)

    headers = ctx.source_dir / "include/curl"
    library = ctx.build_dir / "lib/.libs/libcurl.a"
    ctx.install(library, "lib/libcurl.a")
    for header in sorted(headers.glob("*.h")):
        ctx.install(header, "include/curl/" + header.name)

    # Exercise the published headers and archives in the same link a consumer uses.
    source = ctx.build_dir / "link-check.c"
    source.write_text(
        '#include <curl/curl.h>\n'
        '#include <string.h>\n'
        'int main(void) {\n'
        '    const curl_version_info_data *info = curl_version_info(CURLVERSION_NOW);\n'
        '    if (!info || !info->ssl_version || strcmp(info->ssl_version, "OpenSSL/' + OPENSSL_VERSION + '")) return 1;\n'
        '    CURL *curl = curl_easy_init();\n'
        '    if (!curl) return 1;\n'
        '    curl_easy_cleanup(curl);\n'
        '    return 0;\n'
        '}\n',
        encoding="ascii",
    )
    program = ctx.build_dir / "link-check"
    ctx.run([
        ctx.environment["CC"], *shlex.split(environment["CFLAGS"]),
        *shlex.split(ctx.environment.get("LDFLAGS", "")),
        "-I" + str(ctx.stage_dir / "include"), source, "-o", program,
        ctx.stage_dir / "lib/libcurl.a", openssl / "lib/libssl.a",
        openssl / "lib/libcrypto.a", "-ldl", "-pthread",
    ])
    ctx.run([program])
    ctx.observed_build_environment["openssl_version"] = OPENSSL_VERSION
    ctx.observed_build_environment["openssl_link_check"] = "passed"
