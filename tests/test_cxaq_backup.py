import contextlib
import datetime as dt
import io
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock
import backup_check as backup
from scripts import db_crypt

@unittest.skipUnless(shutil.which('openssl'),'needs openssl')
class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.env={'DB_KEY':'fake-backup-unit-test-key'}
        self.products=self.root/'exported_content'/'products';self.products.mkdir(parents=True)
        (self.products/'pack.txt').write_text('fake buyer data')
        with sqlite3.connect(self.root/'agent_data.db') as conn:
            conn.execute('CREATE TABLE evidence(value TEXT)');conn.execute("INSERT INTO evidence VALUES('fake row')")
        db_crypt.encrypt(str(self.root/'agent_data.db'),str(self.root/'agent_data.db.enc'),environ=self.env)
        db_crypt.pack_products(str(self.products),str(self.root/'products.tar.gz.enc'),environ=self.env)

    def test_real_open_and_missing_empty_corrupt_wrong_key_and_stale_backups(self):
        self.assertTrue(backup.verify(self.root,self.env))
        for name in ('agent_data.db.enc','products.tar.gz.enc'):
            path=self.root/name;original=path.read_bytes()
            for broken in (None,b'',b'corrupt encrypted data'):
                with self.subTest(name=name,broken=broken):
                    if broken is None:path.unlink()
                    else:path.write_bytes(broken)
                    with self.assertRaises(backup.BackupError):backup.verify(self.root,self.env)
                    path.write_bytes(original)
        with self.assertRaises(backup.BackupError):backup.verify(self.root,{'DB_KEY':'fake-wrong-key'})
        (self.products/'pack.txt').write_text('changed content absent from backup')
        with self.assertRaises(backup.BackupError):backup.verify(self.root,self.env)
        with self.assertRaises(backup.BackupError):backup.verify(self.root,{})

    def test_recovery_reopens_saved_products_without_requiring_unpacked_tree(self):
        (self.products/'pack.txt').unlink()
        self.assertTrue(backup.verify(self.root,self.env,compare_products=False))
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/agent.yml').read_text()
        self.assertIn('python3 backup_check.py --saved-products',workflow.split('  recovery_inbox:',1)[1])

    def test_alert_contains_fix_without_any_key_or_plain_data(self):
        sent=[];out=io.StringIO()
        with contextlib.redirect_stderr(out),contextlib.redirect_stdout(out):
            rc=backup.main([],self.root,{'DB_KEY':'fake-wrong-key'},sent.append)
        self.assertEqual(rc,1);self.assertIn('Run workflow',sent[0]);self.assertIn('/doctor',sent[0])
        self.assertNotIn('fake-wrong-key',out.getvalue()+sent[0]);self.assertNotIn('fake row',out.getvalue()+sent[0])

    def test_monthly_real_fake_data_restore_drill_and_failed_drill_is_not_recorded(self):
        db=self.root/'monthly.db';now=dt.datetime(2026,11,24,12,tzinfo=dt.timezone.utc)
        self.assertTrue(backup.monthly_drill(db,now))
        with mock.patch.object(backup,'restore_drill',side_effect=AssertionError):
            self.assertFalse(backup.monthly_drill(db,now))
        with self.assertRaises(backup.BackupError):backup.monthly_drill(db,now+dt.timedelta(days=31),lambda:False)
        self.assertTrue(backup.monthly_drill(db,now+dt.timedelta(days=31)))

    def test_workflow_verifies_before_committing_and_also_on_quiet_saves(self):
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/agent.yml').read_text()
        save=workflow.split('- name: Save state',1)[1].split('- name: Report minutes',1)[0]
        self.assertLess(save.index('backup_check.py drill'),save.index('python3 save_check.py'))
        self.assertLess(save.index('python3 scripts/db_crypt.py encrypt'),save.rindex('python3 backup_check.py'))
        self.assertLess(save.rindex('python3 backup_check.py'),save.index('git commit'))
        quiet=save.split('if [ "$rc" = "3" ]; then',1)[1].split('fi',1)[0]
        self.assertIn('python3 backup_check.py',quiet)
