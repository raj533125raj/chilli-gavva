"""product_parts.py — keep the encrypted products archive under GitHub's 100 MB file limit.

GitHub refuses any single file over 100 MB (run #1013: products.tar.gz.enc was 103.85 MB, so "Save state" could not push).
The runner still works with ONE file, products.tar.gz.enc; only git stores it as small parts:

  split : products.tar.gz.enc -> products.tar.gz.enc.parts/part-000, part-001, ... + SHA256 (the whole file's checksum)
  join  : products.tar.gz.enc.parts/ -> products.tar.gz.enc (checksum verified). No parts folder = nothing to do
          (an older checkout that still commits the single file keeps working).

The parts are the same encrypted bytes cut into pieces: nothing is decrypted here and no key is needed.
Exit code 0 = OK, 1 = a problem (message printed, never file contents).
"""
import hashlib
import os
import sys

ARCHIVE = "products.tar.gz.enc"
PARTS_DIR = ARCHIVE + ".parts"
PART_BYTES = 45 * 1024 * 1024      # well below GitHub's 50 MB warning and 100 MB hard limit
CHUNK = 1024 * 1024


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _part_names(parts_dir):
    if not os.path.isdir(parts_dir):
        return []
    return sorted(name for name in os.listdir(parts_dir) if name.startswith("part-"))


def split(archive=ARCHIVE, parts_dir=PARTS_DIR, part_bytes=PART_BYTES):
    if not os.path.isfile(archive) or os.path.islink(archive):
        raise ValueError("products archive missing; nothing to split")
    os.makedirs(parts_dir, exist_ok=True)
    wanted = []
    with open(archive, "rb") as source:
        index = 0
        while True:
            data = source.read(part_bytes)
            if not data:
                break
            name = f"part-{index:03d}"
            path = os.path.join(parts_dir, name)
            # Rewrite only when the bytes differ, so an unchanged archive leaves git clean.
            if not (os.path.isfile(path) and os.path.getsize(path) == len(data) and _same(path, data)):
                tmp = path + ".tmp"
                with open(tmp, "wb") as handle:
                    handle.write(data)
                os.replace(tmp, path)
            wanted.append(name)
            index += 1
    if not wanted:
        raise ValueError("products archive is empty; nothing to split")
    for name in _part_names(parts_dir):
        if name not in wanted:
            os.remove(os.path.join(parts_dir, name))
    checksum = _sha256(archive) + "\n"
    sha_path = os.path.join(parts_dir, "SHA256")
    if not (os.path.isfile(sha_path) and open(sha_path).read() == checksum):
        with open(sha_path, "w") as handle:
            handle.write(checksum)
    return wanted


def _same(path, data):
    with open(path, "rb") as handle:
        return handle.read() == data


def join(archive=ARCHIVE, parts_dir=PARTS_DIR):
    names = _part_names(parts_dir)
    if not names:
        return False
    sha_path = os.path.join(parts_dir, "SHA256")
    if not os.path.isfile(sha_path):
        raise ValueError("products parts checksum missing")
    want = open(sha_path).read().strip()
    tmp = archive + ".joining"
    with open(tmp, "wb") as out:
        for name in names:
            with open(os.path.join(parts_dir, name), "rb") as handle:
                for block in iter(lambda: handle.read(CHUNK), b""):
                    out.write(block)
    if _sha256(tmp) != want:
        os.remove(tmp)
        raise ValueError("products parts do not match their checksum")
    os.replace(tmp, archive)
    return True


def main(argv):
    if len(argv) != 2 or argv[1] not in ("split", "join"):
        print("usage: product_parts.py split|join", file=sys.stderr)
        return 2
    try:
        if argv[1] == "split":
            names = split()
            print(f"product_parts: split into {len(names)} part(s)")
        else:
            joined = join()
            print("product_parts: joined" if joined else "product_parts: no parts (single file kept)")
    except (OSError, ValueError) as error:
        print(f"product_parts: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
