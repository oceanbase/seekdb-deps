"""Build static libcurl with OpenSSL or Windows Schannel."""

import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import shlex
import shutil
import subprocess
from threading import Thread


OPENSSL_VERSION = "3.5.9"
WINDOWS_SYSTEM_LIBS = (
    "ws2_32.lib", "iphlpapi.lib", "bcrypt.lib", "advapi32.lib",
    "crypt32.lib", "secur32.lib",
)
STATIC_CMAKE_OPTIONS = {
    "BUILD_SHARED_LIBS": False,
    "BUILD_STATIC_LIBS": True,
    "BUILD_TESTING": False,
    "BUILD_EXAMPLES": False,
    "BUILD_LIBCURL_DOCS": False,
    "BUILD_MISC_DOCS": False,
    "CURL_USE_PKGCONFIG": False,
    "CURL_USE_LIBPSL": False,
    "CURL_USE_LIBSSH2": False,
    "USE_LIBIDN2": False,
    "USE_NGHTTP2": False,
    "CURL_BROTLI": False,
    "CURL_ZSTD": False,
    "CURL_DISABLE_LDAP": True,
    "CURL_DISABLE_LDAPS": True,
}


def _build_linux(ctx, openssl):
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

    _install(ctx, ctx.build_dir / "lib/.libs/libcurl.a")
    source = ctx.build_dir / "link-check.c"
    _write_link_check(source)
    program = ctx.build_dir / "link-check"
    ctx.run([
        ctx.environment["CC"], *shlex.split(environment["CFLAGS"]),
        *shlex.split(ctx.environment.get("LDFLAGS", "")),
        "-I" + str(ctx.stage_dir / "include"), source, "-o", program,
        ctx.stage_dir / "lib/libcurl.a", openssl / "lib/libssl.a",
        openssl / "lib/libcrypto.a", "-ldl", "-pthread",
    ])
    ctx.run([program])


def _install(ctx, library):
    ctx.install(library, "lib/" + library.name)
    for header in sorted((ctx.source_dir / "include/curl").glob("*.h")):
        ctx.install(header, "include/curl/" + header.name)


def _write_link_check(source):
    source.write_text(
        '#include <curl/curl.h>\n'
        '#include <string.h>\n'
        'int main(void) {\n'
        '    const curl_version_info_data *info = curl_version_info(CURLVERSION_NOW);\n'
        '    if (!info || !info->ssl_version ||\n'
        '        strcmp(info->ssl_version, "OpenSSL/' + OPENSSL_VERSION + '")) return 1;\n'
        '    CURL *curl = curl_easy_init();\n'
        '    if (!curl) return 1;\n'
        '    curl_easy_cleanup(curl);\n'
        '    return 0;\n'
        '}\n',
        encoding="ascii",
    )


def _build_sdk_target(ctx, openssl):
    use_zlib = ctx.target.family in ("android", "ios")
    options = {
        **STATIC_CMAKE_OPTIONS,
        "BUILD_CURL_EXE": True,
        "BUILD_STATIC_CURL": True,
        "ENABLE_CURL_MANUAL": False,
        "CURL_USE_OPENSSL": True,
        "CURL_ZLIB": use_zlib,
        "CURL_CA_BUNDLE": "/etc/ssl/cert.pem" if ctx.target.family == "macos" else "none",
        "CURL_CA_PATH": "none",
        "OPENSSL_INCLUDE_DIR": str(openssl / "include"),
        "OPENSSL_SSL_LIBRARY": str(openssl / "lib/libssl.a"),
        "OPENSSL_CRYPTO_LIBRARY": str(openssl / "lib/libcrypto.a"),
        "OPENSSL_USE_STATIC_LIBS": True,
    }
    # The upstream curl executable exercises the full static link on this SDK.
    ctx.cmake(options=options, targets=("curl",))
    if ctx.target.family == "macos":
        curl = ctx.build_dir / "src/curl"
        version = subprocess.check_output([str(curl), "--version"], env=ctx.environment, text=True)
        if "OpenSSL/" + OPENSSL_VERSION not in version.split():
            raise ValueError("curl did not link the pinned OpenSSL: " + version)
    _install(ctx, ctx.build_dir / "lib/libcurl.a")


def _verify_windows(ctx, zlib):
    check = ctx.build_dir / "link-check"
    check.mkdir()
    source = check / "main.c"
    source.write_text(
        """#include <curl/curl.h>
#include <string.h>

int main(int argc, char **argv) {
    const curl_version_info_data *info = curl_version_info(CURLVERSION_NOW);
    if (argc != 2 || !info || !info->ssl_version ||
        strncmp(info->ssl_version, "Schannel", 8) || !info->libz_version)
        return 1;
    CURL *curl = curl_easy_init();
    if (!curl)
        return 1;
    curl_easy_setopt(curl, CURLOPT_URL, argv[1]);
    curl_easy_setopt(curl, CURLOPT_ACCEPT_ENCODING, "gzip");
    curl_easy_setopt(curl, CURLOPT_NOPROXY, "*");
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
    CURLcode result = curl_easy_perform(curl);
    curl_easy_cleanup(curl);
    return result != CURLE_OK;
}
""",
        encoding="ascii",
    )
    client = check / "link-check.exe"
    ctx.run([ctx.observed_build_environment["c_compiler"], "/nologo", "/MD", "/DCURL_STATICLIB",
             "/I" + str(ctx.stage_dir / "include"), source, "/Fe" + str(client),
             ctx.stage_dir / "lib/libcurl.lib", zlib, *WINDOWS_SYSTEM_LIBS], cwd=check)

    dumpbin = shutil.which("dumpbin", path=ctx.environment["PATH"])
    if not dumpbin:
        raise ValueError("dumpbin was not found in the Visual Studio environment")
    imports = subprocess.check_output([dumpbin, "/dependents", str(client)],
                                      env=ctx.environment, text=True)
    if re.search(r"^\s*(?:libcurl|zlib)\S*\.dll\s*$", imports, re.I | re.M):
        raise ValueError("The test executable must link both libcurl and zlib statically")
    payload = b"seekdb-deps curl gzip check"
    compressed = gzip.compress(payload)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(compressed)))
            self.end_headers()
            self.wfile.write(compressed)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = subprocess.check_output(
                [str(client), "http://127.0.0.1:{}/".format(server.server_port)],
                env=ctx.environment, cwd=check, timeout=20,
            )
            if response != payload:
                raise ValueError("libcurl did not decode the gzip response")
        finally:
            server.shutdown()
            thread.join()


def _build_windows(ctx):
    zlib = ctx.source_dir / "zlib"
    options = {
        **STATIC_CMAKE_OPTIONS,
        "BUILD_CURL_EXE": False,
        "CURL_STATIC_CRT": False,
        "CURL_USE_SCHANNEL": True,
        "CURL_USE_OPENSSL": False,
        "CURL_ZLIB": True,
        "ZLIB_INCLUDE_DIR": str(zlib / "include"),
        "ZLIB_LIBRARY": str(zlib / "lib/libzs.lib"),
    }
    ctx.cmake(options=options, targets=("libcurl_static",))
    _install(ctx, ctx.build_dir / "lib/libcurl.lib")
    _verify_windows(ctx, zlib / "lib/libzs.lib")
    ctx.observed_build_environment.update({
        "curl_tls_backend": "Schannel",
        "curl_linkage": "static",
        "zlib_linkage": "separate-static",
        "windows_system_libraries": list(WINDOWS_SYSTEM_LIBS),
        "windows_gzip_check": "passed",
    })


def build(ctx):
    if ctx.target.family == "windows":
        _build_windows(ctx)
        return

    openssl = ctx.source_dir / ("openssl-" + ctx.target.id)
    if ctx.target.family == "linux":
        _build_linux(ctx, openssl)
    else:
        _build_sdk_target(ctx, openssl)
    ctx.observed_build_environment["openssl_version"] = OPENSSL_VERSION
    ctx.observed_build_environment["openssl_link_check"] = "passed"
