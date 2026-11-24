"""Reopen encrypted backups with the existing run key; never log a key or file contents."""
import datetime as dt
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from scripts import db_crypt

ROOT=Path(__file__).resolve().parent


class BackupError(Exception):
    """Only safe, fixed diagnostics cross this boundary."""


def verify(root=None,environ=None,compare_live=True,compare_products=True):
    root=Path(root or ROOT)
    env=os.environ if environ is None else environ
    try:
        db_crypt.require_key(env)
    except db_crypt.CryptError:
        raise BackupError('Backup key run lo available ledu; saved key Settings check cheyandi.') from None
    with tempfile.TemporaryDirectory(prefix='backup-verify-') as temp:
        temp=Path(temp)
        for name in ('agent_data.db.enc','products.tar.gz.enc'):
            path=root/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size<=16:
                raise BackupError('Backup missing leda empty: '+name)
        try:
            db_crypt.decrypt(str(root/'agent_data.db.enc'),str(temp/'restored.db'),environ=env)
            with sqlite3.connect(temp/'restored.db') as conn:
                if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError
                restored_rows=list(conn.iterdump())
            if compare_live and (root/'agent_data.db').exists():
                with sqlite3.connect(f'file:{root / "agent_data.db"}?mode=ro',uri=True) as conn:
                    if list(conn.iterdump())!=restored_rows:raise ValueError
        except (db_crypt.CryptError,sqlite3.Error,OSError,ValueError):
            raise BackupError('Encrypted database open/match check fail ayindi.') from None
        try:
            db_crypt.unpack_products(str(root/'products.tar.gz.enc'),str(temp/'products'),environ=env)
            live=root/'exported_content'/'products'
            if compare_live and compare_products and (not live.is_dir() or not db_crypt.compare_product_trees(str(live),str(temp/'products'))):raise ValueError
        except (db_crypt.CryptError,OSError,ValueError):
            raise BackupError('Encrypted products open/match check fail ayindi.') from None
    return True


def restore_drill():
    """A real encrypt -> decrypt -> extract round trip using fake data and a fake key only."""
    with tempfile.TemporaryDirectory(prefix='backup-drill-') as temp:
        root=Path(temp);products=root/'exported_content'/'products';products.mkdir(parents=True)
        with sqlite3.connect(root/'agent_data.db') as conn:
            conn.execute('CREATE TABLE drill(id INTEGER PRIMARY KEY, value TEXT)')
            conn.execute("INSERT INTO drill VALUES(1,'fake restore row')")
        (products/'fake-product.txt').write_text('Fake restore product\n')
        env={'DB_KEY':'fake-monthly-restore-drill-key'}
        db_crypt.encrypt(str(root/'agent_data.db'),str(root/'agent_data.db.enc'),environ=env)
        db_crypt.pack_products(str(products),str(root/'products.tar.gz.enc'),environ=env)
        verify(root,env)
        # Separately prove restored files are usable, not merely decryptable.
        db_crypt.decrypt(str(root/'agent_data.db.enc'),str(root/'recovered.db'),environ=env)
        db_crypt.unpack_products(str(root/'products.tar.gz.enc'),str(root/'recovered-products'),environ=env)
        with sqlite3.connect(root/'recovered.db') as conn:
            if conn.execute('SELECT value FROM drill WHERE id=1').fetchone()!=('fake restore row',):raise BackupError('Restore drill row check fail ayindi.')
        if (root/'recovered-products'/'fake-product.txt').read_text()!='Fake restore product\n':raise BackupError('Restore drill file check fail ayindi.')
    return True


def monthly_drill(db_path=None,now=None,drill=None):
    import guardrails
    now=now or dt.datetime.now(dt.timezone.utc)
    month=now.strftime('%Y-%m')
    with guardrails.connect(db_path) as conn:
        previous=conn.execute("SELECT value FROM control WHERE key='backup_restore_drill_month'").fetchone()
    if previous and previous[0]==month:return False
    if not (drill or restore_drill)():raise BackupError('Monthly restore drill fail ayindi.')
    with guardrails.connect(db_path) as conn:
        conn.execute("INSERT INTO control VALUES('backup_restore_drill_month',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(month,))
    return True


def alert(text,send=None):
    if send is None:
        import notify
        send=notify.send
    return send('Backup check: '+text+' Fix: GitHub Actions > agent-cycle > Run workflow malli run cheyandi; /doctor lo errors chudandi. Owner key values chat lo pampakandi.')


def main(argv=None,root=None,environ=None,send=None):
    argv=sys.argv[1:] if argv is None else argv
    root=Path(root or ROOT)
    try:
        if argv==['drill']:
            monthly_drill(root/'agent_data.db')
        elif argv in ([],['--saved-products']):
            verify(root,environ,compare_products=not argv)
        else:
            print('usage: backup_check.py [drill|--saved-products]',file=sys.stderr);return 2
    except (BackupError,db_crypt.CryptError,OSError,sqlite3.Error) as exc:
        # BackupError messages are fixed and contain no keys or decrypted contents.
        text=str(exc) if isinstance(exc,BackupError) else 'Encrypted backup/restore check fail ayindi; last good backup ni retain cheyandi.'
        print('Backup check: FAIL',file=sys.stderr)
        alert(text,send)
        return 1
    print('Backup restore check: OK')
    return 0


if __name__=='__main__':
    sys.exit(main())
