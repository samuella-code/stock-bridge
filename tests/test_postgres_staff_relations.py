"""Disconnected metadata regression: no PostgreSQL or application execution."""
from copy import deepcopy
from unittest.mock import MagicMock
import pytest
from tests.support import postgres_staff_identity as identity
from tests.support.postgres_staff_guard import NAME, V17, V18
from tests.test_ubuntu_postgres_preflight import snapshot
from tests.test_postgres_staff_guards import env


def metadata(phase=V17):
    data=snapshot()
    for key in ('database','user','session_user','owner'): data[key]=NAME
    data.update(revision=phase,tables=['public.user'],indexes=[dict(name='public.user_pkey',table='public.user')],
        sequences=[dict(name='public.user_id_seq',owner=NAME,table='public.user',column='id',
            table_kind='r',dependency='a',identity='',default_reference=True)],
        all_relations=['public.user','public.user_pkey','public.user_id_seq'])
    return data


def validate(data,phase=V17):
    return identity.validate_database(data,phase,['public.user'],(1000,1001))


@pytest.mark.parametrize('phase',[V17,V18])
@pytest.mark.parametrize('kind',['serial','always','default'])
def test_serial_and_identity_sequences_are_separate_from_tables(phase,kind):
    data=metadata(phase)
    if kind!='serial':
        data['sequences'][0].update(dependency='i',identity='a' if kind=='always' else 'd',default_reference=False)
    assert validate(data,phase)


@pytest.mark.parametrize('field,value',[
    ('name','foreign.user_id_seq'),('owner','postgres'),('table','public.foreign'),('table',None),
    ('column',None),('column',''),('table_kind','v'),('dependency',None),('dependency','n'),
    ('identity','a'),('default_reference',False),('default_reference',1)])
def test_orphan_foreign_and_unbacked_sequence_rejected(field,value):
    data=metadata();data['sequences'][0][field]=value
    with pytest.raises(ValueError):validate(data)


@pytest.mark.parametrize('change',[
    'missing_evidence','duplicate_sequence','duplicate_column','unknown_relation','missing_relation',
    'duplicate_relation','sequence_in_tables','extra_table','foreign_index','duplicate_index',
    'foreign_index_table','orphan_sequence','missing_sequence','unknown_kind'])
def test_relation_inventory_is_exact(change):
    data=metadata()
    if change=='missing_evidence':data.pop('sequences')
    elif change=='duplicate_sequence':data['sequences']*=2
    elif change=='duplicate_column':
        seq=dict(data['sequences'][0],name='public.extra_seq');data['sequences'].append(seq);data['all_relations'].append(seq['name'])
    elif change=='unknown_relation':data['all_relations'].append('public.extra')
    elif change=='missing_relation':data['all_relations'].pop()
    elif change=='duplicate_relation':data['all_relations'].append(data['all_relations'][0])
    elif change=='sequence_in_tables':data['tables'].append('public.user_id_seq')
    elif change=='extra_table':data['tables'].append('public.extra')
    elif change=='foreign_index':data['indexes'][0]['name']='foreign.user_pkey'
    elif change=='duplicate_index':data['indexes']*=2
    elif change=='foreign_index_table':data['indexes'][0]['table']='public.foreign'
    elif change=='orphan_sequence':data['sequences'][0].update(table=None,column=None,dependency=None)
    elif change=='missing_sequence':data['sequences']=[]
    elif change=='unknown_kind':data['all_relations'].append('public.unexpected_view')
    with pytest.raises(ValueError):validate(data)


def test_empty_database_requires_empty_sequence_evidence():
    data=metadata();data.update(tables=[],indexes=[],all_relations=[],revision=None)
    with pytest.raises(ValueError):identity.validate_database(data,'empty',[],(1000,1001))
    data['sequences']=[]
    assert identity.validate_database(data,'empty',[],(1000,1001))


@pytest.mark.parametrize('field,value',[
    ('database_oid',9999),('role_oid',9999),('owner','postgres'),('port',5432),
    ('memberships',['monitor']),('schemas',['public','foreign']),('listen_addresses','*'),
    ('revision',V18)])
def test_sequence_acceptance_does_not_relax_identity_or_revision(field,value):
    data=metadata();data[field]=value
    with pytest.raises(ValueError):validate(data)


def test_collector_keeps_sequence_ownership_metadata_and_read_only_policy(monkeypatch):
    import psycopg
    from tests.support import postgres_preflight
    data=metadata()
    sequence=data['sequences'][0]
    connection=MagicMock()
    connection.__enter__.return_value=connection
    def execute(sql):
        sql=' '.join(sql.split())
        if sql=='SET TRANSACTION READ ONLY':return MagicMock()
        if sql=='SHOW transaction_read_only':return MagicMock(fetchone=lambda:('on',))
        if sql.startswith('SELECT nspname'):return [('public',)]
        if "c.relkind IN ('r','p')" in sql:return [('public.user',)]
        if sql.startswith('SELECT ni.nspname'):return [('public.user_pkey','public.user')]
        if "AND s.relkind='S'" in sql:
            assert 'LEFT JOIN pg_depend' in sql and "d.refobjsubid>0" in sql
            assert "dd.refobjid=s.oid" in sql and 'NOT a.attisdropped' in sql
            return [tuple(sequence.values())]
        if sql=='SELECT pg_backend_pid()':return MagicMock(fetchone=lambda:(5001,))
        if sql.startswith('SELECT ssl'):return MagicMock(fetchone=lambda:(False,))
        if sql.startswith("SELECT n.nspname||'.'||c.relname"):return [(name,) for name in data['all_relations']]
        raise AssertionError('Unexpected metadata query')
    connection.execute.side_effect=execute
    connector=MagicMock(return_value=connection)
    monkeypatch.setattr(psycopg,'connect',connector)
    monkeypatch.setattr(psycopg.pq,'version',lambda:160000)
    monkeypatch.setattr(postgres_preflight,'database_snapshot',lambda conn:deepcopy(data))
    monkeypatch.setattr(identity,'collect_evidence',lambda *args:({},{}))
    for name in ('validate_ubuntu_os','validate_backend','same_instance'):
        monkeypatch.setattr(identity,name,lambda *args:None)
    result=identity.preflight(env(),phase=V17,expected_tables=['public.user'],expected_identity=(1000,1001))
    assert result['status']=='PASS'
    options=connector.call_args.kwargs
    assert options['host']=='127.0.0.1' and options['port']==55432
    assert options['require_auth']=='scram-sha-256' and options['connect_timeout']==5
    assert options['options']=='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000 -c search_path=pg_catalog'
