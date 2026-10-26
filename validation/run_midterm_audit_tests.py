"""Run the existing suite against disposable local metadata, keeping the live app intact."""
from pathlib import Path
import json
import os
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]


def main():
    runtime = json.loads((ROOT / '.local/runtime.json').read_text(encoding='utf-8-sig'))
    if runtime.get('marker') != 'wafer-local-demo-v1' or runtime.get('port') != 55432:
        raise RuntimeError('Unrecognized local test runtime')
    name = 'audit_midterm_' + uuid.uuid4().hex[:12]
    env = dict(os.environ)
    def url(user, password, database):
        return URL.create('postgresql+psycopg', username=user, password=password,
                          host='127.0.0.1', port=runtime['port'], database=database).render_as_string(hide_password=False)
    env.update(APP_DATABASE_URL=url('wafer_metadata', runtime['app_password'], name),
               SOURCE_DATABASE_URL=url('wafer_source', runtime['source_password'], 'wafer_demo'),
               DEMO_MODE='true', AUTH_MODE='local_admin', LLM_BASE_URL='', LLM_MODEL='',
               DB_CREDENTIALS_KEY=Fernet.generate_key().decode(),
               LLM_CREDENTIALS_KEY=Fernet.generate_key().decode(),
               TABLE_IGNORE='[]', PYTHONPATH=str(ROOT / 'backend'), PYTHONIOENCODING='utf-8')
    with psycopg.connect(host='127.0.0.1', port=runtime['port'], dbname='postgres',
                         user='wafer_local_admin', password=runtime['admin_password'],
                         autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {} OWNER wafer_metadata').format(sql.Identifier(name)))
        print('Disposable audit metadata created; live settings are untouched.', flush=True)
        try:
            log_name = os.environ.get('PM_AUDIT_LOG_NAME', 'midterm-backend-tests.log')
            if Path(log_name).name != log_name or not log_name.endswith('.log'):
                raise ValueError('Use a local log filename')
            log = ROOT / '.local' / log_name
            with log.open('w', encoding='utf-8') as output:
                targets = sys.argv[1:] or ['backend/tests', 'demo/test_generator.py']
                result = subprocess.run([sys.executable, '-m', 'pytest', *targets,
                                         '-q', '--tb=short', '-ra'],
                                        cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
            print(log.read_text(encoding='utf-8')[-26000:], flush=True)
            return result.returncode
        finally:
            if not name.startswith('audit_midterm_') or len(name) != 26:
                raise RuntimeError('Refusing to clean an unexpected database name')
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
            print('Disposable audit metadata removed.', flush=True)


if __name__ == '__main__':
    sys.exit(main())
