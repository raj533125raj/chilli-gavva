"""Encrypt or decrypt the agent database with the key in the DB_KEY environment variable.

    DB_KEY=... python3 scripts/db_crypt.py encrypt    # agent_data.db -> agent_data.db.enc
    DB_KEY=... python3 scripts/db_crypt.py decrypt    # agent_data.db.enc -> agent_data.db

Uses the openssl command: AES-256-CBC, PBKDF2 with 200000 iterations, salted. The key is passed to
openssl through an environment variable, never on the command line. Without DB_KEY nothing is read
or written. A failed decrypt leaves no partial file behind. Only the encrypted file goes into git.
"""
import os
import shutil
import subprocess
import sys
import tempfile

PLAIN = "agent_data.db"
ENCRYPTED = "agent_data.db.enc"
OPENSSL_ARGS = ["-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt"]
KEY_VAR = "DB_KEY"


class CryptError(Exception):
    """A clear, safe message. Never contains the key."""


def require_key(environ=None):
    key = (os.environ if environ is None else environ).get(KEY_VAR, "")
    if not key.strip():
        raise CryptError("DB_KEY is not set. Nothing was read or written.")
    return key


def _openssl(mode, src, dst, key):
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
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def encrypt(src=PLAIN, dst=ENCRYPTED, environ=None):
    _openssl("encrypt", src, dst, require_key(environ))


def decrypt(src=ENCRYPTED, dst=PLAIN, environ=None):
    _openssl("decrypt", src, dst, require_key(environ))


def main(argv):
    if len(argv) != 2 or argv[1] not in ("encrypt", "decrypt"):
        print("usage: db_crypt.py encrypt|decrypt", file=sys.stderr)
        return 2
    try:
        (encrypt if argv[1] == "encrypt" else decrypt)()
    except CryptError as error:
        print(f"db_crypt: {error}", file=sys.stderr)
        return 1
    print(f"db_crypt: {argv[1]} done")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
