"""The auto-fix workflow runs its verifier from a copied folder. Every robot module those
copied files import at load time must be copied too, or auto-fix dies with ModuleNotFoundError
(this happened when run_failure.py and notify.py started importing telegram_diagnostics)."""
import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _copied_files():
    with open(os.path.join(ROOT, ".github", "workflows", "autofix.yml"), encoding="utf-8") as fh:
        text = fh.read()
    m = re.search(r"^\s*cp ((?:\S+\.py\s+)+)\"\$RUNNER_TEMP/autofix-control/\"", text, re.M)
    assert m, "copy line not found in autofix.yml"
    return m.group(1).split()


def _top_level_local_imports(name, local):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return {m + ".py" for m in found if m in local}


class AutofixControlCopyTests(unittest.TestCase):
    def test_every_load_time_import_of_the_copied_files_is_copied(self):
        local = {f[:-3] for f in os.listdir(ROOT) if f.endswith(".py")}
        copied = set(_copied_files())
        needed, todo = set(), list(copied)
        while todo:
            f = todo.pop()
            if f in needed:
                continue
            needed.add(f)
            todo.extend(_top_level_local_imports(f, local))
        self.assertEqual(sorted(needed - copied), [], "add these files to the cp line in autofix.yml")


if __name__ == "__main__":
    unittest.main()
