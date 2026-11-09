"""Staff authorization is inert on import. No environment/database fallback."""
import hashlib
from pathlib import Path
import re

RUN = '7e10a9c42b6d830f'
NAME = 'stockbridge_test_' + RUN
BASE = 'b18fee6c1a9aec56fb9c92756f4c39ff2c82ba2c'
INTEGRATION = '934ea9b732489885bba6e609114da8dbbdba240f'
REVISION = 'staff-manager-v2'
MANAGER_CASES = ('G01','G02','G03','G04','G05','G06','C01','C02','C03','C04','C05','C06','B01','B02','B03','B04')
TARGET = dict(host='127.0.0.1', port=55432, dbname=NAME, user=NAME)
V17 = '0017_transaction_submission'
V18 = '0018_staff_foundation'
CASES = ('M00','M01','S01','R01','R02','I01','I02','A01','A02','X01','X02','D01','D02','D03','F01','F02','F03','F04')
REMAINING_CASES = ('I02','A01','A02','X01','X02','D01','D02','D03','F01','F02','F03','F04')
SALES_CASES = ('SLS01','SLS02','SLS03')
STOCK_CASES = ('STK01','STK02','STK03')
MODES = {
    'stock-state': (V18,V18,()),
    'stock': (V18,V18,STOCK_CASES),
    'sales-state': (V18,V18,()),
    'sales': (V18,V18,SALES_CASES),
    'staff-remaining-state': (V18,V18,()),
    'staff-remaining': (V18,V18,REMAINING_CASES),
    'preflight': ('empty', 'empty', ()),
    'i01-state': (V18,V18,()),
    'i01-continuation': (V18,V18,('I01',)),
    'migrate-0017': ('empty', V17, ()),
    'seed-baseline': (V17, V17, ()),
    'migration-rollback': (V17, V17, ('M00',)),
    'migrate-0018': (V17, V18, ('M01',)),
    'staff': (V18, V18, CASES[2:]),
    'manager': (V18,V18,MANAGER_CASES),
}
ACK = 'reviewed-staff-only'
RUNTIME = None  # Set only by the separately authorized launcher, never by pytest env alone.


class Rejected(ValueError):
    pass


def require(value, label):
    if not value: raise Rejected(label)


def sha(path):
    p = Path(path)
    require(p.is_file() and not p.is_symlink(), 'file-rejected')
    return hashlib.sha256(p.read_bytes()).hexdigest()


def target_url(env):
    from tests.support.isolated_postgres import validated_test_url
    require(env.get('STOCKBRIDGE_TEST_POSTGRES_MODE') == 'ubuntu-isolated-cluster', 'mode-rejected')
    require(env.get('STOCKBRIDGE_TEST_POSTGRES_RUN_ID') == RUN, 'run-rejected')
    url = validated_test_url(env)
    require((url.host,url.port,url.database,url.username)==('127.0.0.1',55432,NAME,NAME), 'target-rejected')
    return url


def authorize(auth, mode, root, files, *, source_commit):
    """No approval generated here; all fields bind externally reviewed evidence."""
    require(isinstance(auth,dict) and type(auth.get('schema')) is int and auth.get('schema') == 2 and auth.get('approved') is True, 'approval-required')
    require(mode in MODES and auth.get('mode') == mode, 'stage-rejected')
    require(auth.get('target') == TARGET and auth.get('run_id') == RUN, 'target-rejected')
    require(auth.get('repository') == str(Path(root).resolve()), 'source-path-rejected')
    require(isinstance(source_commit,str) and re.fullmatch('[0-9a-f]{40}',source_commit)
            and auth.get('source_commit') == source_commit and source_commit != BASE, 'source-rejected')
    require(auth.get('application_base_commit') == BASE, 'base-rejected')
    require(auth.get('staff_integration_commit') == INTEGRATION and auth.get('scenario_revision') == REVISION
            and source_commit != INTEGRATION, 'integration-review-required')
    before,after,cases=MODES[mode]
    require(auth.get('schema_before') == before and auth.get('schema_after') == after
            and auth.get('cases') == list(cases), 'schema-or-selection-rejected')
    if mode in ('migration-rollback','migrate-0018','staff'):
        require(isinstance(auth.get('baseline_sha256'),str) and re.fullmatch('[0-9a-f]{64}',auth['baseline_sha256']), 'baseline-hash-required')
    if mode in ('i01-state','i01-continuation'):
        require(isinstance(auth.get('continuation_namespace'),str) and re.fullmatch(r'i01-cont-[a-f0-9]{8}',auth['continuation_namespace']), 'continuation-namespace-required')
    if mode in ('staff-remaining-state','staff-remaining'):
        require(isinstance(auth.get('continuation_namespace'),str) and re.fullmatch(r'staff-cont-[a-f0-9]{8}',auth['continuation_namespace']), 'continuation-namespace-required')
    if mode in ('i01-continuation','staff-remaining'):
        require(isinstance(auth.get('starting_state_sha256'),str) and re.fullmatch('[0-9a-f]{64}',auth['starting_state_sha256']), 'starting-state-required')
    if mode=='manager':
        require(isinstance(auth.get('starting_state_sha256'),str) and re.fullmatch('[0-9a-f]{64}',auth['starting_state_sha256']), 'starting-state-required')
    if mode in ('sales-state','sales'):
        require(auth.get('sales_revision')=='staff-sales-pg-v1','sales-review-required')
        require(isinstance(auth.get('continuation_namespace'),str) and re.fullmatch(r'sales-cont-[a-f0-9]{8}',auth['continuation_namespace']),'sales-namespace-required')
        if mode=='sales':
            require(isinstance(auth.get('starting_state_sha256'),str) and re.fullmatch('[a-f0-9]{64}',auth['starting_state_sha256']),'sales-starting-state-required')
    if mode in ('stock-state','stock'):
        require(auth.get('stock_revision')=='staff-stock-pg-v1','stock-review-required')
        require(isinstance(auth.get('continuation_namespace'),str) and re.fullmatch(r'stock-cont-[a-f0-9]{8}',auth['continuation_namespace']),'stock-namespace-required')
        if mode=='stock':
            require(isinstance(auth.get('starting_state_sha256'),str) and re.fullmatch('[a-f0-9]{64}',auth['starting_state_sha256']),'stock-starting-state-required')
    require(auth.get('package_provenance_approved') is True, 'package-provenance-unapproved')
    require('network_isolation_approved' not in auth and auth.get('isolation_mode') == 'application-guards-only'
            and auth.get('os_outbound_enforcement') == 'unverified'
            and auth.get('application_isolation_risk_accepted') is True, 'isolation-policy-rejected')
    require(all(type(auth.get(k)) is int and 0<auth[k]<=4294967295 for k in ('database_oid','role_oid')), 'identity-oids-required')
    require(isinstance(auth.get('relations'),dict) and set(auth['relations']) == {'empty',V17,V18}, 'schema-inventory-required')
    require(auth['relations']['empty'] == [], 'empty-inventory-rejected')
    for revision in (V17,V18):
        values=auth['relations'][revision]
        require(isinstance(values,list) and values and len(values)==len(set(values))
                and all(isinstance(x,str) and re.fullmatch(r'public\.[a-z_][a-z_0-9]*',x) for x in values), 'relation-inventory-rejected')
    require(set(auth['relations'][V17]) < set(auth['relations'][V18]), 'schema-inventory-rejected')
    source=auth.get('source_files')
    require(isinstance(source,dict) and source and all(isinstance(k,str) and not Path(k).is_absolute() and '..' not in Path(k).parts and isinstance(v,str) and re.fullmatch('[0-9a-f]{64}',v) for k,v in source.items()), 'source-hashes-required')
    require(isinstance(auth.get('files'),dict) and set(auth['files'])==set(files), 'file-inventory-rejected')
    for key,path in files.items(): require(auth['files'][key]==sha(path), 'file-hash-rejected')
    return auth


def authorized_case(case, maxfail, env):
    # Called before importing fixtures/application or opening any connection.
    import pytest
    ack=env.get('STOCKBRIDGE_STAFF_PG_ACK')
    if ack is None or ack == '': pytest.skip('Staff PostgreSQL execution not authorized')
    require(ack == ACK and maxfail == 1, 'fail-fast-authorization-required')
    require(RUNTIME is not None and case in RUNTIME.auth['cases'], 'launcher-authorization-required')
    RUNTIME.check_source()
    return RUNTIME
