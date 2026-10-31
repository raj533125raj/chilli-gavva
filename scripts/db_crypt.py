"""Encrypt or decrypt the agent database with the key in the DB_KEY environment variable.

    DB_KEY=... python3 scripts/db_crypt.py encrypt    # agent_data.db -> agent_data.db.enc
    DB_KEY=... python3 scripts/db_crypt.py decrypt    # agent_data.db.enc -> agent_data.db

Uses the openssl command: AES-256-CBC, PBKDF2 with 200000 iterations, salted. The key is passed to
openssl through an environment variable, never on the command line. Without DB_KEY nothing is read
or written. A failed decrypt leaves no partial file behind. Only the encrypted file goes into git.
"""
import gzip
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import tarfile
import zlib

PLAIN = "agent_data.db"
ENCRYPTED = "agent_data.db.enc"
OPENSSL_ARGS = ["-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt"]
KEY_VAR = "DB_KEY"
PRODUCTS = os.path.join("exported_content", "products")
PRODUCTS_ENCRYPTED = "products.tar.gz.enc"


class CryptError(Exception):
    """A clear, safe message. Never contains the key."""


def require_key(environ=None):
    key = (os.environ if environ is None else environ).get(KEY_VAR, "")
    if not key.strip():
        raise CryptError("DB_KEY is not set. Nothing was read or written.")
    return key


def _verify_plaintext(path, plaintext_type):
    try:
        if plaintext_type == "database":
            with open(path, "rb") as plain:
                if plain.read(16) != b"SQLite format 3\x00":
                    raise ValueError
        else:
            with gzip.open(path, "rb") as plain:
                with tarfile.open(fileobj=plain, mode="r|") as archive:
                    for member in archive:
                        pass
                # Read through the gzip trailer to check for truncation or corruption.
                while plain.read(1024 * 1024):
                    pass
    except (OSError, EOFError, ValueError, tarfile.TarError, zlib.error):
        raise CryptError("decrypt failed: the DB_KEY is wrong or the file is damaged.") from None


def _openssl(mode, src, dst, key, plaintext_type="database"):
    """Run openssl. The key travels in a child environment variable, not in argv."""
    if not os.path.exists(src):
        raise CryptError(f"{os.path.basename(src)} not found. Nothing was written.")
    if shutil.which("openssl") is None:
        raise CryptError("openssl is not installed.")
    dst_dir = os.path.dirname(os.path.abspath(dst))
    fd, tmp = tempfile.mkstemp(prefix=".db-crypt-", dir=dst_dir)
    os.close(fd)
    try:
        env = dict(os.environ)
        env["DB_CRYPT_PASS"] = key
        cmd = ["openssl", "enc", *OPENSSL_ARGS, "-pass", "env:DB_CRYPT_PASS", "-in", src, "-out", tmp]
        if mode == "decrypt":
            cmd.insert(2, "-d")
        result = subprocess.run(cmd, env=env, capture_output=True, check=False)
        if result.returncode != 0:
            raise CryptError("decrypt failed: the DB_KEY is wrong or the file is damaged." if mode == "decrypt"
                             else "encrypt failed.")
        if mode == "decrypt":
            _verify_plaintext(tmp, plaintext_type)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def encrypt(src=PLAIN, dst=ENCRYPTED, environ=None):
    _openssl("encrypt", src, dst, require_key(environ))


def decrypt(src=ENCRYPTED, dst=PLAIN, environ=None):
    _openssl("decrypt", src, dst, require_key(environ))


def _products_archive(source):
    """Build a stable tar.gz of regular product files, without following symlinks."""
    if not os.path.isdir(source):
        raise CryptError("exported_content/products not found. Nothing was written.")
    fd, archive = tempfile.mkstemp(prefix=".products-", suffix=".tar.gz")
    os.close(fd)
    try:
        with open(archive, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w|") as tar:
                for root, dirs, files in os.walk(source, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
                    for name in sorted(files):
                        path = os.path.join(root, name)
                        if os.path.islink(path):
                            raise CryptError("Product bundle contains an unsupported file. Nothing was written.")
                        info = tar.gettarinfo(path, arcname=os.path.relpath(path, source))
                        info.mtime = 0
                        with open(path, "rb") as content:
                            tar.addfile(info, content)
        return archive
    except Exception:
        if os.path.exists(archive):
            os.remove(archive)
        raise


def pack_products(source=PRODUCTS, dst=PRODUCTS_ENCRYPTED, environ=None):
    key = require_key(environ)
    archive = _products_archive(source)
    plain_existing = None
    try:
        if os.path.exists(dst):
            existing_fd, plain_existing = tempfile.mkstemp(prefix=".products-existing-")
            os.close(existing_fd)
            try:
                _openssl("decrypt", dst, plain_existing, key, plaintext_type="archive")
                if _file_digest(archive) == _file_digest(plain_existing):
                    return
            except CryptError:
                pass
        _openssl("encrypt", archive, dst, key)
    finally:
        for path in (archive, plain_existing):
            if path and os.path.exists(path):
                os.remove(path)


def unpack_products(src=PRODUCTS_ENCRYPTED, destination="exported_content/products", environ=None):
    key = require_key(environ)
    fd, archive = tempfile.mkstemp(prefix=".products-unpack-")
    os.close(fd)
    try:
        _openssl("decrypt", src, archive, key, plaintext_type="archive")
        os.makedirs(destination, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tar:
            root = os.path.realpath(destination)
            for member in tar.getmembers():
                target = os.path.realpath(os.path.join(root, member.name))
                if not member.name or os.path.commonpath((root, target)) != root or not member.isfile():
                    raise CryptError("Product bundle contains an unsafe entry. Nothing was extracted.")
            tar.extractall(root)
    except (tarfile.TarError, OSError, ValueError) as error:
        if isinstance(error, CryptError):
            raise
        raise CryptError("Product bundle is damaged. Nothing was extracted.") from error
    finally:
        if os.path.exists(archive):
            os.remove(archive)


def _file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compare_product_trees(left, right):
    """Compare relative file lists and content checksums without revealing file names."""
    def contents(root):
        result = {}
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(base, d))]
            for name in files:
                path = os.path.join(base, name)
                if os.path.islink(path):
                    raise CryptError("Product tree contains an unsupported file.")
                result[os.path.relpath(path, root)] = _file_digest(path)
        return result
    return contents(left) == contents(right)


def _delivery_receipt(encrypted):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Migration helpers also copy this script into standalone temporary repos.
    if not os.path.isfile(os.path.join(project_root, "product_pipeline.py")):
        return
    sys.path.insert(0, project_root)
    import product_pipeline
    if encrypted:
        product_pipeline.mark_encrypted(archive=PRODUCTS_ENCRYPTED)
    else:
        product_pipeline.encryption_failed()


def main(argv):
    if len(argv) < 2 or argv[1] not in ("encrypt", "decrypt", "pack-products", "unpack-products") or len(argv) > 4:
        print("usage: db_crypt.py encrypt|decrypt|pack-products|unpack-products [path] [destination]", file=sys.stderr)
        return 2
    try:
        if argv[1] == "encrypt":
            encrypt()
        elif argv[1] == "decrypt":
            decrypt()
        elif argv[1] == "pack-products":
            pack_products(*(argv[2:]))
            if len(argv) == 2:
                # The scheduled builder and encryption run in separate processes.
                # Confirm encryption before Save state, then Telegram can deliver
                # the persisted readiness receipt on its next owner inbox cycle.
                _delivery_receipt(encrypted=True)
        else:
            unpack_products(*(argv[2:]))
    except CryptError as error:
        if argv[1] == "pack-products" and len(argv) == 2:
            _delivery_receipt(encrypted=False)
        print(f"db_crypt: {error}", file=sys.stderr)
        return 1
    print(f"db_crypt: {argv[1]} done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
