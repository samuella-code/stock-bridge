"""Offline startup evidence tests: fake environment and no Flask/SQL startup."""
import json
import logging
import runpy
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import startup_database_diagnostic as diagnostic

URL = ('postgresql://synthetic_user:synthetic_password@'
       'ep-lingering-cherry-b8rquuz8.c-14.us-east-1.aws.neon.tech/'
       'synthetic_db?sslmode=require&channel_binding=require')


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(diagnostic, '_attempted', False)
    monkeypatch.setattr(socket, 'create_connection', Mock(side_effect=AssertionError('Network forbidden')))
    monkeypatch.setattr(socket.socket, 'connect', Mock(side_effect=AssertionError('Network forbidden')))


def environment():
    return {**diagnostic.METADATA, diagnostic.FLAG: 'true', 'NEON_DATABASE_URL': URL}


def execute(caplog, env=None, effective=None):
    env = environment() if env is None else env
    effective = diagnostic.normalized(URL) if effective is None else effective
    with caplog.at_level(logging.WARNING, logger='stockbridge.runtime'):
        diagnostic.startup_diagnostic({'SQLALCHEMY_DATABASE_URI':effective}, env)
    return [json.loads(r.getMessage()) for r in caplog.records if r.name=='stockbridge.runtime']


def test_disabled_by_default(caplog):
    env=environment(); del env[diagnostic.FLAG]
    assert execute(caplog, env)==[]


@pytest.mark.parametrize('flag', ['false', '', 'TRUE', ' true ', '1'])
def test_flag_requires_exact_opt_in(caplog, flag):
    env=environment(); env[diagnostic.FLAG]=flag
    assert execute(caplog,env)==[]


@pytest.mark.parametrize('key', list(diagnostic.METADATA))
@pytest.mark.parametrize('value', [None, '', 'wrong', ' preview '])
def test_missing_or_inconsistent_authorization_metadata_denied(caplog,key,value):
    env=environment()
    if value is None: del env[key]
    else: env[key]=value
    assert execute(caplog,env)==[]


@pytest.mark.parametrize('key,value', [
    ('VERCEL_ENV','production'), ('VERCEL_TARGET_ENV','production'),
    ('VERCEL_PROJECT_ID','prj_production'), ('VERCEL_GIT_COMMIT_REF','main'),
])
def test_production_denied(caplog,key,value):
    env=environment(); env[key]=value
    assert execute(caplog,env)==[]


@pytest.mark.parametrize('scheme', ['postgres://','postgresql://','postgresql+psycopg://'])
@pytest.mark.parametrize('pooled', [False,True])
def test_expected_endpoint_and_driver_normalization(caplog,scheme,pooled):
    raw=URL.replace('postgresql://',scheme)
    if pooled: raw=raw.replace('b8rquuz8.c-', 'b8rquuz8-pooler.c-')
    env=environment();env['NEON_DATABASE_URL']=raw
    assert execute(caplog,env,diagnostic.normalized(raw))==[{
        'selected_variable':'NEON_DATABASE_URL', 'endpoint_matches_staging':True,
        'database_identity_verified':False}]


@pytest.mark.parametrize('selected', diagnostic.VARIABLES)
def test_first_nonempty_precedence(caplog, selected):
    env=environment()
    for key in diagnostic.VARIABLES: env[key]=''
    start=diagnostic.VARIABLES.index(selected)
    env[selected]=URL
    for key in diagnostic.VARIABLES[start+1:]: env[key]='postgresql://invalid-lower-priority'
    result=execute(caplog,env)[0]
    assert result['selected_variable']==selected and result['endpoint_matches_staging'] is True


def test_invalid_higher_priority_does_not_fall_back(caplog):
    env=environment();env['NEON_DATABASE_URL']='invalid';env['NEON_POSTGRES_URL']=URL
    assert execute(caplog,env)[0]['endpoint_matches_staging'] is False


@pytest.mark.parametrize('raw', [
    '', 'not-a-url', 'sqlite:///stockbridge.db',
    URL.replace('ep-lingering-cherry-b8rquuz8','ep-production-fixture'),
    URL.replace('.neon.tech','.neon.tech.evil.invalid'),
    URL.replace('c-14.us-east-1','c-3.us-east-1'),
    URL.replace('postgresql://','https://'),
    URL.replace('/synthetic_db?',':not-a-port/synthetic_db?'),
    URL.replace('/synthetic_db?',':5433/synthetic_db?'),
    URL.replace('/synthetic_db?','/?'),
    URL.replace('synthetic_password','bad%ZZ'),
    URL.replace('synthetic_password','ambiguous@password'),
    URL+'#fragment', URL+'\n', ' '+URL,
    URL+'&host=production.invalid', URL+'&hostaddr=127.0.0.1',
    URL+'&options=unsafe', URL+'&sslmode=require', URL+'&unknown=true',
    URL+'&channel_binding=', 'postgresql://user:pass@[broken/db',
])
def test_malformed_missing_or_unsafe_endpoint_rejected(caplog,raw):
    env=environment();env['NEON_DATABASE_URL']=raw
    result=execute(caplog,env,diagnostic.normalized(raw) or 'sqlite:///stockbridge.db')[0]
    assert result['endpoint_matches_staging'] is False
    assert result['database_identity_verified'] is False


def test_effective_config_mismatch_rejected(caplog):
    assert execute(caplog,effective=URL.replace('b8rquuz8','otherendpoint'))[0]['endpoint_matches_staging'] is False


def test_missing_all_variables(caplog):
    env=environment();del env['NEON_DATABASE_URL']
    assert execute(caplog,env,effective='sqlite:///stockbridge.db')==[{
        'selected_variable':None,'endpoint_matches_staging':False,'database_identity_verified':False}]


def test_only_allowlisted_fields_and_no_secrets(caplog):
    result=execute(caplog)
    assert set(result[0])=={'selected_variable','endpoint_matches_staging','database_identity_verified'}
    for value in ('synthetic_user','synthetic_password','synthetic_db',diagnostic.ENDPOINT,
                  'neon.tech','sslmode','channel_binding','postgresql', URL):
        assert value not in caplog.text


def test_parser_exceptions_do_not_log_sensitive_messages(caplog,monkeypatch):
    monkeypatch.setattr(diagnostic,'urlsplit',Mock(side_effect=ValueError('synthetic_password')))
    assert execute(caplog)[0]['endpoint_matches_staging'] is False
    assert 'synthetic_password' not in caplog.text


def test_one_record_across_repeated_and_concurrent_invocations(caplog):
    env=environment();config={'SQLALCHEMY_DATABASE_URI':diagnostic.normalized(URL)}
    with caplog.at_level(logging.WARNING,logger='stockbridge.runtime'):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: diagnostic.startup_diagnostic(config,env),range(20)))
    assert len([r for r in caplog.records if r.name=='stockbridge.runtime'])==1


def test_disabled_startup_cannot_be_enabled_later_in_process(caplog):
    diagnostic.startup_diagnostic({}, {})
    assert execute(caplog)==[]


def test_wsgi_wiring_uses_effective_app_config_without_side_effects(monkeypatch,caplog):
    config={'SQLALCHEMY_DATABASE_URI':diagnostic.normalized(URL)}
    factory=Mock(return_value=SimpleNamespace(config=config))
    # Actual WSGI wiring; Flask, engines, SMTP, dispatchers and providers are not imported.
    monkeypatch.setitem(sys.modules,'app',SimpleNamespace(create_app=factory))
    monkeypatch.setitem(sys.modules,'runtime_diagnostics',SimpleNamespace(startup_watch=nullcontext))
    monkeypatch.setattr('os.environ',environment())
    with caplog.at_level(logging.WARNING,logger='stockbridge.runtime'):
        runpy.run_path(str(Path(__file__).resolve().parents[1]/'wsgi.py'))
    assert factory.call_count==1
    assert json.loads(caplog.records[-1].getMessage())['endpoint_matches_staging'] is True


def test_no_database_or_delivery_interfaces_called(caplog,monkeypatch):
    forbidden=Mock(side_effect=AssertionError('Side effect forbidden'))
    # Substitute interfaces without importing the application or opening engines.
    for name, module in {
        'psycopg':SimpleNamespace(connect=forbidden),
        'sqlalchemy':SimpleNamespace(create_engine=forbidden),
        'smtplib':SimpleNamespace(SMTP=forbidden,SMTP_SSL=forbidden),
        'app.notifications.email_outbox':SimpleNamespace(dispatch_one=forbidden,claim=forbidden),
    }.items():
        monkeypatch.setitem(sys.modules,name,module)
    assert execute(caplog)[0]['endpoint_matches_staging'] is True
    forbidden.assert_not_called()


@pytest.mark.parametrize('stage', ['import', 'diagnostic', 'config', 'parser', 'serialization', 'logging'])
def test_unexpected_diagnostic_failure_does_not_break_wsgi(monkeypatch, caplog, capsys, stage):
    import builtins
    config={'SQLALCHEMY_DATABASE_URI':diagnostic.normalized(URL)}
    application=SimpleNamespace(config=config)
    factory=Mock(return_value=application)
    monkeypatch.setitem(sys.modules,'app',SimpleNamespace(create_app=factory))
    monkeypatch.setitem(sys.modules,'runtime_diagnostics',SimpleNamespace(startup_watch=nullcontext))
    monkeypatch.setattr('os.environ',environment())
    failure=RuntimeError('synthetic_password connection-string-sentinel')
    def fail(*args, **kwargs):
        raise failure
    if stage=='import':
        original_import=builtins.__import__
        def checked_import(name,*args,**kwargs):
            if name=='startup_database_diagnostic':
                raise ImportError('synthetic_password connection-string-sentinel')
            return original_import(name,*args,**kwargs)
        monkeypatch.setattr(builtins,'__import__',checked_import)
    elif stage=='diagnostic':
        monkeypatch.setattr(diagnostic,'startup_diagnostic',fail)
    elif stage=='config':
        class BrokenConfig:
            get=fail
        application.config=BrokenConfig()
    elif stage=='parser':
        monkeypatch.setattr(diagnostic,'urlsplit',fail)
    elif stage=='serialization':
        monkeypatch.setattr(diagnostic.json,'dumps',fail)
    else:
        monkeypatch.setattr(logging.getLogger('stockbridge.runtime'),'warning',fail)
    with caplog.at_level(logging.WARNING):
        namespace=runpy.run_path(str(Path(__file__).resolve().parents[1]/'wsgi.py'))
    assert namespace['app'] is application
    factory.assert_called_once_with()
    assert not [r for r in caplog.records if r.name=='stockbridge.runtime']
    output=capsys.readouterr()
    assert output.out=='' and output.err==''
    assert 'synthetic_password' not in caplog.text
    assert 'connection-string-sentinel' not in caplog.text
    assert 'Traceback' not in caplog.text


def test_wsgi_preserves_real_application_startup_errors(monkeypatch,caplog):
    failure=RuntimeError('synthetic application initialization failure')
    monkeypatch.setitem(sys.modules,'app',SimpleNamespace(create_app=Mock(side_effect=failure)))
    monkeypatch.setitem(sys.modules,'runtime_diagnostics',SimpleNamespace(startup_watch=nullcontext))
    with pytest.raises(RuntimeError) as caught:
        runpy.run_path(str(Path(__file__).resolve().parents[1]/'wsgi.py'))
    assert caught.value is failure
    assert not [r for r in caplog.records if r.name=='stockbridge.runtime']


HOST = 'ep-lingering-cherry-b8rquuz8.c-14.us-east-1.aws.neon.tech'


@pytest.mark.parametrize('credentials', [
    ':pass', 'user:', 'user', 'user:pass:word@extra',
    'user%40other:pass', 'user%3Aother:pass', 'user%2Fother:pass',
    'user%3Fother:pass', 'user%23other:pass', 'user%25other:pass',
    'user%2540other:pass', 'user%20other:pass', 'user%00other:pass',
    'user:pass%00', 'user:pass%09', 'user:pass%0A', 'user:pass%0D',
    'user:pass%7f', 'user:pass%C2%85', 'user:pass%FF', 'user:pass%C0%AF',
    'user:pass%ED%A0%80', 'user:pass%', 'user:pass%2', 'user:pass%GG',
    'user:raw\\password', 'user:raw[password]', 'user:raw/password',
])
def test_encoded_or_malformed_credentials_fail_closed(caplog, credentials):
    raw=f'postgresql+psycopg://{credentials}@{HOST}/neondb?sslmode=require'
    env=environment(); env['NEON_DATABASE_URL']=raw
    assert execute(caplog,env,raw)==[{
        'selected_variable':'NEON_DATABASE_URL', 'endpoint_matches_staging':False,
        'database_identity_verified':False}]
    assert credentials not in caplog.text
    assert HOST not in caplog.text


@pytest.mark.parametrize('authority', [
    HOST+':', HOST+':05432', HOST+':+5432', HOST+':5432:5432',
    HOST+':5432,other.invalid:5432', HOST+',other.invalid',
    HOST+'.', HOST+'.evil.invalid', HOST.upper(),
    HOST.replace('ep-', '%65p-', 1), HOST+'%2Fother', HOST+'%40other',
    HOST+'%3A5432', HOST+'%00', HOST+'\\other', '['+HOST+']',
    '127.0.0.1', '[::1]',
])
def test_noncanonical_or_ambiguous_authority_rejected(caplog, authority):
    raw=f'postgresql+psycopg://user:pass@{authority}/neondb?sslmode=require'
    env=environment(); env['NEON_DATABASE_URL']=raw
    assert execute(caplog,env,raw)[0]['endpoint_matches_staging'] is False
    assert authority not in caplog.text


@pytest.mark.parametrize('database', [
    '', 'db/other', 'db%2Fother', 'db%5Cother', 'db%40other', 'db%3Fother',
    'db%23other', 'db%3Aother', 'db%20other', 'db%00other', 'db%0Aother',
    'db%7Fother', 'db%252Fother', 'db%253Fother', 'db%FF', 'db%2',
    'db%ED%A0%80', 'host%3Dother', 'db\\other',
])
def test_encoded_database_path_ambiguity_rejected(caplog,database):
    raw=f'postgresql+psycopg://user:pass@{HOST}/{database}?sslmode=require'
    env=environment(); env['NEON_DATABASE_URL']=raw
    assert execute(caplog,env,raw)[0]['endpoint_matches_staging'] is False
    assert HOST not in caplog.text


@pytest.mark.parametrize('query', [
    '', 'sslmode=garbage', 'sslmode=disable', 'sslmode=prefer',
    'channel_binding=garbage', 'sslmode=%72equire', '%73slmode=require',
    'sslmode=require%26host%3Devil.invalid', 'sslmode=require;host=evil.invalid',
    'sslmode=require&host=evil.invalid', 'sslmode=require&%68ost=evil.invalid',
    'sslmode=require&sslmode=require', 'sslmode=require&', '&sslmode=require',
    'sslmode=require&&channel_binding=require', 'sslmode=require#',
    'sslmode=require&channel_binding=', 'sslmode=require&options=anything',
    'sslmode=require&dbname=other', 'sslmode=require&service=other',
])
def test_encoded_duplicate_or_invalid_connection_options_rejected(caplog,query):
    raw=f'postgresql+psycopg://user:pass@{HOST}/neondb?{query}'
    env=environment(); env['NEON_DATABASE_URL']=raw
    assert execute(caplog,env,raw)[0]['endpoint_matches_staging'] is False
    assert HOST not in caplog.text


@pytest.mark.parametrize('password,decoded', [
    ('pass%40word','pass@word'), ('pass%2Fword','pass/word'),
    ('pass%3Aword','pass:word'), ('pass%3Fword','pass?word'),
    ('pass%23word','pass#word'), ('pass%25word','pass%word'),
    ('pass%2540word','pass%40word'), ('pass%2Bword','pass+word'),
    ('pass%20word','pass word'), ('pass%5Cword','pass\\word'),
    ('p%C3%A4ss','päss'), ('pass:word','pass:word'),
])
@pytest.mark.parametrize('pooled', [False,True])
def test_legitimate_password_encoding_matches_sqlalchemy(caplog,password,decoded,pooled):
    from sqlalchemy.engine import make_url
    host=HOST.replace('.c-', '-pooler.c-') if pooled else HOST
    raw=f'postgresql://neondb_owner:{password}@{host}/neondb?sslmode=require&channel_binding=require'
    env=environment(); env['NEON_DATABASE_URL']=raw
    effective=diagnostic.normalized(raw)
    parsed=make_url(effective)  # Parsing only: no engine or connection.
    assert parsed.host==host and parsed.database=='neondb'
    assert parsed.username=='neondb_owner' and parsed.password==decoded
    assert execute(caplog,env,effective)[0]['endpoint_matches_staging'] is True
    assert password not in caplog.text and decoded not in caplog.text
    assert host not in caplog.text


@pytest.mark.parametrize('path', ['neondb', 'stockbridge_test-1', 'stockbridge.db', '%6Eeondb'])
@pytest.mark.parametrize('query', ['', '?sslmode=require', '?sslmode=verify-ca',
    '?sslmode=verify-full&channel_binding=require', '?channel_binding=prefer&sslmode=require',
    '?sslmode=require&channel_binding=disable'])
def test_supported_neon_names_and_options(caplog,path,query):
    from sqlalchemy.engine import make_url
    raw=f'postgresql+psycopg://neondb_owner:synthetic_password@{HOST}:5432/{path}{query}'
    env=environment(); env['NEON_DATABASE_URL']=raw
    assert make_url(raw).host==HOST
    assert make_url(raw).database in ('neondb','stockbridge_test-1','stockbridge.db')
    assert execute(caplog,env,raw)[0]['endpoint_matches_staging'] is True


@pytest.mark.parametrize('value',[None, 1, True, {}, [], b'not-a-url'])
def test_nonstring_parser_inputs_rejected(value):
    assert diagnostic.endpoint_matches(value) is False
