"""Psycopg API compatibility using synthetic results only; never connect."""
import inspect
from unittest.mock import MagicMock
import pytest
import psycopg
from psycopg.rows import dict_row, tuple_row
from tests.support import ubuntu_database_evidence as scoped
from tests.support.postgres_preflight import database_snapshot
from tests.support.isolated_postgres import UnsafeTestDatabase


class OfflineConnection:
    """Reject unsupported Connection.execute kwargs using installed Psycopg's signature."""
    def __init__(self, rows=()):
        self.row_factory=tuple_row
        self.queries=[]
        self.cursor_queries=[]
        self.reader=MagicMock()
        self.reader.__enter__.return_value=self.reader
        self.reader.fetchall.return_value=list(rows)
        self.reader.__iter__.side_effect=lambda:iter(rows)
        self.reader.execute.side_effect=lambda sql:self.cursor_queries.append(sql)
        self.cursor_factories=[]
    def cursor(self, *, row_factory):
        inspect.signature(psycopg.Connection.cursor).bind(self,row_factory=row_factory)
        self.cursor_factories.append(row_factory)
        return self.reader
    def execute(self, sql, *args, **kwargs):
        inspect.signature(psycopg.Connection.execute).bind(self,sql,*args,**kwargs)
        self.queries.append(sql)
        result=MagicMock();result.__iter__.return_value=iter([])
        if 'current_database(),current_user' in sql:
            result.fetchone.return_value=('synthetic','synthetic','synthetic','127.0.0.1',55432,160011,'synthetic')
        elif 'SELECT rolsuper' in sql:result.fetchone.return_value=(False,False,False,False,False,True)
        elif 'SELECT oid' in sql:result.fetchone.return_value=(123,)
        elif sql=='SHOW data_directory':result.fetchone.return_value=('/synthetic',)
        elif sql=='SHOW listen_addresses':result.fetchone.return_value=('localhost',)
        elif sql=='SELECT stockbridge_preflight.read_evidence()':result.fetchone.return_value=({'synthetic':True},)
        return result


def trusted_function():
    return dict(prosrc=scoped.BODY,prosecdef=True,provolatile='s',proconfig=['search_path=pg_catalog'],
        owner='postgres',language='plpgsql',schema_owner='postgres',can_create=False,executors=['postgres',scoped.TARGET])


def test_scoped_metadata_uses_dict_cursor_and_preserves_tuple_connection():
    connection=OfflineConnection([trusted_function()])
    assert scoped.read_scoped_evidence(connection)=={'synthetic':True}
    assert connection.cursor_factories==[dict_row] and connection.row_factory is tuple_row
    assert len(connection.cursor_queries)==1 and "p.prorettype='jsonb'::regtype" in connection.cursor_queries[0]
    assert connection.queries==['SELECT stockbridge_preflight.read_evidence()']
    connection.reader.__exit__.assert_called_once()


@pytest.mark.parametrize('field,value',[('prosrc','untrusted'),('owner','untrusted'),('prosecdef',False),('can_create',True),('executors',['postgres'])])
def test_scoped_cursor_retains_security_rejections(field,value):
    row=trusted_function();row[field]=value;connection=OfflineConnection([row])
    with pytest.raises(UnsafeTestDatabase):scoped.read_scoped_evidence(connection)
    assert not connection.queries
    connection.reader.__exit__.assert_called_once()


def test_hba_snapshot_uses_dict_cursor_preserves_order_and_shape():
    rules=[dict(type='host',database=['synthetic'],user=['synthetic'],address='127.0.0.1',netmask='255.255.255.255',auth_method='scram-sha-256',error=None),
           dict(type='host',database=['all'],user=['all'],address='0.0.0.0',netmask='0.0.0.0',auth_method='reject',error=None)]
    connection=OfflineConnection(rules);snapshot=database_snapshot(connection)
    assert snapshot['hba_rules']==rules
    assert snapshot['database']=='synthetic' and snapshot['data_directory']=='/synthetic'
    assert connection.cursor_factories==[dict_row] and connection.row_factory is tuple_row
    assert connection.cursor_queries==['SELECT type,database,user_name AS "user",address,netmask,auth_method,error FROM pg_hba_file_rules ORDER BY rule_number']
    connection.reader.__exit__.assert_called_once()


@pytest.mark.parametrize('operation',['scoped','hba'])
def test_cursor_query_failure_propagates_without_fallback(operation):
    connection=OfflineConnection([trusted_function()])
    error=PermissionError('synthetic-only')
    connection.reader.execute.side_effect=error
    with pytest.raises(PermissionError) as caught:
        (scoped.read_scoped_evidence if operation=='scoped' else database_snapshot)(connection)
    assert caught.value is error
    assert 'SELECT stockbridge_preflight.read_evidence()' not in connection.queries
    connection.reader.__exit__.assert_called_once()


def test_protected_snapshot_does_not_query_hba_cursor():
    connection=OfflineConnection()
    protected=dict(data_directory='/synthetic',listen_addresses='localhost',hba_rules=[])
    snapshot=database_snapshot(connection,protected_evidence=protected)
    assert snapshot['hba_rules']==[] and not connection.cursor_factories
    assert not any(sql.startswith('SHOW ') for sql in connection.queries)

def test_database_snapshot_extracts_ipv4_host_without_netmask():
    """Snapshot SQL must return the host, not its CIDR representation."""
    import inspect
    from tests.support.postgres_preflight import database_snapshot

    source = inspect.getsource(database_snapshot)

    assert "host(inet_server_addr())" in source
    assert "inet_server_addr()::text" not in source
