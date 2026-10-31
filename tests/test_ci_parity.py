"""Full CI suites must retain the real image coverage available on a developer machine."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def image_dependencies_before_tests(workflow):
    # Keep this guard stdlib-only: it must run before any optional packages are available.
    steps = re.split(r"^      - ", workflow, flags=re.MULTILINE)[1:]
    tests = next(i for i, step in enumerate(steps) if step.startswith("name: Run tests first\n"))
    condition = re.search(r"^        if: (.+)$", steps[tests], re.MULTILINE).group(1)
    required = next(step for step in steps[:tests] if step.startswith("name: Install test dependencies\n"))
    install_condition = re.search(r"^        if: (.+)$", required, re.MULTILINE).group(1)
    if install_condition != condition or "continue-on-error: true" in required:
        return False
    # A later product-only installation cannot satisfy the full-suite prerequisite.
    script = required.split("        run: |\n", 1)[1]
    commands = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    pillow = re.search(r"python3 -m pip install[^\n]*-r \.agent-image-requirements\.txt", commands)
    poppler = re.search(r"sudo apt-get[^\n]*\binstall\b[^\n]*\bpoppler-utils\b", commands)
    requirements = "\n".join(steps[:tests])
    pinned_pillow = re.search(r"^        run: .*'Pillow==[0-9.]+'.*> \.agent-image-requirements\.txt$",
                             requirements, re.MULTILINE)
    return bool(pillow and poppler and pinned_pillow)


class CiParityTests(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/agent.yml").read_text()

    def test_full_suite_installs_pillow_and_poppler_first(self):
        self.assertTrue(image_dependencies_before_tests(self.workflow),
                        "Install Pillow and poppler-utils before every full Run tests first step")

    def test_guard_rejects_dropped_or_product_only_dependencies(self):
        for old, new in (
            ("python3 -m pip install", "echo skipped pip install"),
            ("--no-install-recommends poppler-utils", "--no-install-recommends"),
            ("'Pillow==", "'unrelated=="),
            ("name: Install test dependencies\n", "name: Removed test dependencies\n"),
        ):
            with self.subTest(removed=old):
                changed = self.workflow.replace(old, new, 1)
                try:
                    valid = image_dependencies_before_tests(changed)
                except (StopIteration, AttributeError, IndexError):
                    valid = False
                self.assertFalse(valid)

    def test_guard_rejects_installation_that_skips_some_full_suites(self):
        changed = self.workflow.replace(
            "name: Install test dependencies\n        if: ",
            "name: Install test dependencies\n        if: steps.image_work.outputs.needed == 'true' && ")
        self.assertFalse(image_dependencies_before_tests(changed))


if __name__ == "__main__":
    unittest.main()
