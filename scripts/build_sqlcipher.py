#!/usr/bin/env python3
"""Build the pinned native dependency locally; run with uv run python."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

VERSION = "4.19.0"
SHA256 = "7075f96cbabe45b4ecfc2e6b1745a625f856f695b0827a5506ce9ed85b906aa0"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--openssl-prefix", type=Path, default=Path("/opt/homebrew/opt/openssl@3"))
    parser.add_argument("--prefix", type=Path, default=Path.home() / f".local/share/wechat-bridge/sqlcipher-{VERSION}")
    args = parser.parse_args()
    if not (args.openssl_prefix / "include/openssl/ssl.h").is_file():
        parser.error("OpenSSL headers are required")
    base = Path(__file__).resolve().parents[1] / ".superpowers/native-build"
    base.mkdir(parents=True, exist_ok=True)
    archive = base / f"sqlcipher-{VERSION}.tar.gz"
    if not archive.exists():
        urllib.request.urlretrieve(f"https://github.com/sqlcipher/sqlcipher/archive/refs/tags/v{VERSION}.tar.gz", archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise SystemExit("Source checksum mismatch")
    source = base / f"sqlcipher-{VERSION}"
    if not source.exists():
        with tarfile.open(archive) as package:
            package.extractall(base, filter="data")
    flags = "-O2 -DSQLITE_HAS_CODEC -DSQLITE_EXTRA_INIT=sqlcipher_extra_init -DSQLITE_EXTRA_SHUTDOWN=sqlcipher_extra_shutdown -DSQLITE_ENABLE_FTS5"
    commands = [
        ["./configure", "--disable-tcl", "--dll-basename=libsqlcipher", f"--prefix={args.prefix}",
         "--with-tempstore=yes", f"CFLAGS={flags} -I{args.openssl_prefix}/include",
         f"LDFLAGS=-L{args.openssl_prefix}/lib -lcrypto"],
        ["make", "-j4"], ["make", "install"],
    ]
    for name, command in zip(("configure", "build", "install"), commands):
        with (base / f"{name}.log").open("w") as log:
            subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True)
    license_dir = args.prefix / "share/licenses/sqlcipher"
    license_dir.mkdir(parents=True, exist_ok=True)
    for name in ("LICENSE.md", "README.md"):
        if (source / name).is_file(): shutil.copy2(source / name, license_dir / name)
    print(f"Built SQLCipher {VERSION}; native library installed under the selected prefix.")

if __name__ == "__main__":
    main()
