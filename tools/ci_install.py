#!/usr/bin/env python3
"""tools/ci_install.py — Robust CI dependency installer for Chilli Gavva robot.

Installs Pillow with 3 retries, exponential backoff, and pip cache usage.
Installs poppler-utils and fonts via apt only if really needed (prefer none).
Prints a clear one-line log summary on completion or failure.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS = ROOT / ".agent-image-requirements.txt"
DEFAULT_PACKAGE = "Pillow==11.3.0"


def check_pillow_installed():
    """Return True if PIL (Pillow) can be imported successfully."""
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def check_dpkg_installed(package_name):
    """Return True if a debian package is already installed via dpkg-query."""
    try:
        res = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}", package_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        return (res.stdout or "").strip() == "install ok installed"
    except (FileNotFoundError, OSError):
        return False


def get_pip_cache_dir():
    """Retrieve pip cache directory path or default to ~/.cache/pip."""
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pip", "cache", "dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except (FileNotFoundError, OSError):
        pass
    return os.path.expanduser("~/.cache/pip")


def install_pillow(
    requirements_file=None,
    package=DEFAULT_PACKAGE,
    retries=3,
    backoff_factor=2.0,
    initial_delay=1.0,
    sleeper=time.sleep,
    runner=subprocess.run,
    log_func=print,
):
    """Install Pillow via pip with retries and exponential backoff.

    Returns:
        tuple: (success: bool, attempts: int, message: str)
    """
    req_file = Path(requirements_file) if requirements_file else DEFAULT_REQUIREMENTS
    cache_dir = get_pip_cache_dir()

    base_cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
    ]
    if cache_dir:
        base_cmd.extend(["--cache-dir", cache_dir])

    if req_file.is_file():
        cmd = base_cmd + ["-r", str(req_file)]
        target_name = f"-r {req_file.name}"
    else:
        cmd = base_cmd + [package]
        target_name = package

    last_error = ""
    delay = initial_delay

    for attempt in range(1, retries + 1):
        try:
            res = runner(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                return (True, attempt, f"Installed {target_name} on attempt {attempt}")
            last_error = (res.stdout or "").strip().splitlines()[-1] if res.stdout else f"exit {res.returncode}"
        except OSError as e:
            last_error = str(e)

        if attempt < retries:
            sleeper(delay)
            delay *= backoff_factor

    return (False, retries, f"Failed to install {target_name} after {retries} attempts: {last_error}")


def ensure_apt_packages(
    packages=("poppler-utils", "fonts-urw-base35"),
    runner=subprocess.run,
    checker=check_dpkg_installed,
    prefer_none=True,
    retries=3,
):
    """Install debian packages only if missing, retrying on network issues.

    Returns:
        tuple: (success: bool, installed_count: int, message: str)
    """
    if prefer_none:
        # Check if already installed
        missing = [pkg for pkg in packages if not checker(pkg)]
        if not missing:
            return (True, 0, "All apt packages already installed")
    else:
        missing = list(packages)

    # Need sudo apt-get
    if not shutil.which("apt-get"):
        return (False, 0, "apt-get not available on system")

    # Update first
    update_cmd = ["sudo", "apt-get", f"-o Acquire::Retries={retries}", "update", "-qq"]
    try:
        runner(update_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        pass

    install_cmd = [
        "sudo",
        "apt-get",
        f"-o Acquire::Retries={retries}",
        "install",
        "-y",
        "-qq",
        "--no-install-recommends",
    ] + missing

    try:
        res = runner(install_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if res.returncode == 0:
            return (True, len(missing), f"Installed apt packages: {', '.join(missing)}")
        return (False, 0, f"apt-get install failed with exit code {res.returncode}")
    except OSError as e:
        return (False, 0, f"apt-get failed: {e}")


def run_install(
    requirements_file=None,
    package=DEFAULT_PACKAGE,
    skip_apt=False,
    retries=3,
    backoff=2.0,
    sleeper=time.sleep,
    runner=subprocess.run,
    log_func=print,
):
    """Execute complete CI install sequence and produce a single-line log summary."""
    t0 = time.time()
    pip_ok, pip_attempts, pip_msg = install_pillow(
        requirements_file=requirements_file,
        package=package,
        retries=retries,
        backoff_factor=backoff,
        sleeper=sleeper,
        runner=runner,
        log_func=log_func,
    )

    apt_summary = "apt skipped"
    apt_ok = True
    if not skip_apt:
        apt_ok, apt_count, apt_msg = ensure_apt_packages(
            packages=("poppler-utils", "fonts-urw-base35"),
            runner=runner,
            checker=check_dpkg_installed,
            prefer_none=True,
            retries=retries,
        )
        apt_summary = f"apt={apt_count} new" if apt_ok else f"apt failed ({apt_msg})"

    elapsed = round(time.time() - t0, 2)

    # Verify image tools readiness
    image_tools_ready = False
    try:
        import mockups
        image_tools_ready = mockups.available()
    except Exception:
        image_tools_ready = False

    success = pip_ok and (apt_ok or skip_apt)
    status_str = "SUCCESS" if success else "FAILED"
    one_line_log = (
        f"[ci_install] status={status_str} pip_attempts={pip_attempts} "
        f"{apt_summary} image_tools_ready={image_tools_ready} time={elapsed}s"
    )
    log_func(one_line_log)
    return 0 if success else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chilli Gavva CI dependency installer")
    parser.add_argument(
        "-r",
        "--requirements",
        default=str(DEFAULT_REQUIREMENTS),
        help="Path to requirements file",
    )
    parser.add_argument(
        "--package",
        default=DEFAULT_PACKAGE,
        help="Fallback pip package if requirements file not found",
    )
    parser.add_argument(
        "--skip-apt",
        action="store_true",
        help="Skip apt package installations",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of pip retries",
    )
    parser.add_argument(
        "--backoff",
        type=float,
        default=2.0,
        help="Exponential backoff factor in seconds",
    )
    args = parser.parse_args(argv)

    return run_install(
        requirements_file=args.requirements,
        package=args.package,
        skip_apt=args.skip_apt,
        retries=args.retries,
        backoff=args.backoff,
    )


if __name__ == "__main__":
    sys.exit(main())
