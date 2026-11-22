"""CXL-1: default chunks isolate modules and retain all failure evidence."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import run_tests_chunked as runner


class ChunkIsolationTests(unittest.TestCase):
    def test_default_dispatch_keeps_every_module_and_continues_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('alpha','beta','gamma'):
                (Path(tmp)/f'test_{name}.py').write_text('')
            calls=[];logs=[]
            def run(command,**kwargs):
                modules=command[3:]
                calls.append(modules)
                failed=modules==['tests.test_beta']
                output=('FAIL: test_beta (tests.test_beta.Beta)\nRan 1 test in 0.001s\nFAILED (failures=1)\n'
                        if failed else 'Ran 1 test in 0.001s\nOK\n')
                return subprocess.CompletedProcess(command,int(failed),stdout=output)
            status=runner.run_all_chunked(tests_dir=tmp,runner=run,log_func=logs.append)
            self.assertEqual(calls,[['tests.test_alpha'],['tests.test_beta'],['tests.test_gamma']])
            self.assertEqual(status,1)
            self.assertTrue(any('test_beta' in line for line in logs))
            self.assertTrue(any('Total tests run: 3' in line for line in logs))


if __name__=='__main__':unittest.main()
