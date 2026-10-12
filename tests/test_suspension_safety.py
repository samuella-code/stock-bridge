"""Disposable in-memory tests; external email is mocked, no hosted resources."""
from datetime import datetime
import pytest
from app import db
from app.models import User, Business, AuditLog
from tests.test_admin_portal import client, seed, admin_login
from tests.test_multi_business import app, seed as multi_seed, login


def form(desired='suspend', stamp=''):
    return dict(confirm='on', reason='Safety review', desired_state=desired,
        expected_state='active' if desired=='suspend' else 'suspended', expected_suspended_at=stamp)


@pytest.mark.parametrize('kind', ['users', 'businesses'])
def test_explicit_replay_and_reactivation(client, monkeypatch, kind):
    _, uid, bid=seed(client); admin_login(client)
    emails=[]
    monkeypatch.setattr('app.admin.routes.safely_send', lambda *a, **k: emails.append(k))
    model, ident=(User,uid) if kind=='users' else (Business,bid)
    path=f'/admin/{kind}/{ident}/suspension'
    assert client.post(path,data=form()).status_code==302
    assert client.post(path,data=form()).status_code==409
    with client.application.app_context():
        stamp=db.session.get(model,ident).suspended_at.isoformat()
        assert AuditLog.query.filter(AuditLog.action.like('%_SUSPENDED')).count()==1
    assert len(emails)==1
    assert client.post(path,data=form('reactivate',stamp)).status_code==302
    assert client.post(path,data=form('reactivate',stamp)).status_code==409
    assert len(emails)==2
    with client.application.app_context():
        assert db.session.get(model,ident).suspended_at is None


def test_invalid_conflicting_and_rollback(client, monkeypatch):
    _,uid,_=seed(client); admin_login(client); path=f'/admin/users/{uid}/suspension'
    assert client.post(path,data={**form(),'desired_state':'toggle'}).status_code==400
    assert client.post(path,data={**form(),'expected_state':'suspended'}).status_code==409
    def fail(*a,**k): raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr('app.admin.routes.log',fail)
    with pytest.raises(RuntimeError): client.post(path,data=form())
    with client.application.app_context():
        assert db.session.get(User,uid).suspended_at is None
        assert AuditLog.query.filter_by(action='USER_SUSPENDED').count()==0


@pytest.mark.parametrize('kind', ['plus','basic','disabled'])
def test_business_scope_and_plan_slots(app, kind):
    user,shops=multi_seed('plus' if kind=='disabled' else kind, businesses=2)
    user.set_password('customer-password123')
    shops[0].suspended_at=datetime.utcnow();db.session.commit()
    if kind=='disabled': app.config['SUBSCRIPTIONS_ENABLED']=False
    browser=app.test_client()
    assert browser.post('/auth/login',data={'email':user.email,'password':'customer-password123'}).status_code==302
    response=browser.get('/dashboard')
    if kind=='plus':
        assert response.status_code==200
        with browser.session_transaction() as sess: assert sess['active_business_id']==shops[1].id
    else:
        assert response.status_code==302 and response.headers['Location'].endswith('/businesses/')
    assert browser.get(f'/businesses/{shops[0].id}/profile').status_code==403
    assert browser.post(f'/businesses/{shops[0].id}/logo/remove').status_code in (403,404)
    assert browser.post(f'/businesses/{shops[0].id}/switch').headers['Location'].endswith('/businesses/')
    if kind!='plus': assert browser.post(f'/businesses/{shops[1].id}/switch').headers['Location'].endswith('/businesses/')
    shops[1].suspended_at=datetime.utcnow();db.session.commit()
    assert b'No eligible active business available' in browser.get('/businesses/').data
    user.suspended_at=datetime.utcnow();db.session.commit()
    for path in ['/dashboard','/businesses/','/plans/','/products/']:
        assert browser.get(path).status_code==403


def test_selected_suspension_and_cross_customer(app):
    user,shops=multi_seed('plus',businesses=2)
    other,foreign=multi_seed('plus',email='other@example.invalid')
    browser=app.test_client();login(browser,user)
    with browser.session_transaction() as sess: sess['active_business_id']=shops[0].id
    shops[0].suspended_at=datetime.utcnow();db.session.commit()
    assert browser.get('/dashboard').status_code==200
    with browser.session_transaction() as sess: assert sess['active_business_id']==shops[1].id
    assert browser.post(f'/businesses/{foreign[0].id}/switch').status_code==404
    assert browser.get(f'/businesses/{foreign[0].id}/profile').status_code==404


def test_stale_reactivation_timestamp(client,monkeypatch):
    _,uid,_=seed(client);admin_login(client)
    monkeypatch.setattr('app.admin.routes.safely_send',lambda *a,**k:None)
    path=f'/admin/users/{uid}/suspension'
    assert client.post(path,data=form()).status_code==302
    with client.application.app_context(): stamp=db.session.get(User,uid).suspended_at.isoformat()
    assert client.post(path,data=form('reactivate',stamp)).status_code==302
    assert client.post(path,data=form()).status_code==302
    assert client.post(path,data=form('reactivate',stamp)).status_code==409


def test_commit_failure_rolls_back_status_and_audit(client,monkeypatch):
    _,uid,_=seed(client);admin_login(client)
    path=f'/admin/users/{uid}/suspension'
    def fail(): raise RuntimeError('synthetic commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(db.session,'commit',fail)
        with pytest.raises(RuntimeError): client.post(path,data=form())
    with client.application.app_context():
        assert db.session.get(User,uid).suspended_at is None
        assert AuditLog.query.filter_by(action='USER_SUSPENDED').count()==0


def test_conditional_update_compiles_for_postgres():
    # SQL shape only: not evidence of executed PostgreSQL concurrency.
    from sqlalchemy import update
    from sqlalchemy.dialects import postgresql
    sql=str(update(User).where(User.id==1,User.suspended_at.is_(None))
        .values(suspended_at=datetime.utcnow()).returning(User.id).compile(dialect=postgresql.dialect()))
    assert 'suspended_at IS NULL' in sql and 'RETURNING' in sql


def test_simultaneous_suspend_requests_have_one_winner(tmp_path,monkeypatch):
    """Real independent SQLite requests; PostgreSQL execution remains pending."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import time
    from app import create_app
    from sqlalchemy import event
    application=create_app({'TESTING':True,'SECRET_KEY':'isolated-concurrency',
        'SQLALCHEMY_DATABASE_URI':f'sqlite:///{tmp_path / "suspension.sqlite"}',
        'SUBSCRIPTIONS_ENABLED':False,'BILLING_PROVIDER_ENABLED':False})
    emails=[]
    monkeypatch.setattr('app.admin.routes.safely_send',lambda *a,**k:emails.append(k))
    with application.app_context():
        db.create_all()
        admin=User(full_name='Operator',email='operator@example.invalid',role='admin',password_hash='unused')
        customer=User(full_name='Customer',email='customer@example.invalid',password_hash='unused')
        db.session.add_all([admin,customer]);db.session.commit()
        aid,uid=admin.id,customer.id
        barrier=Barrier(2)
        engine=db.engine
        def synchronize(conn,cursor,statement,parameters,context,executemany):
            if statement.startswith('UPDATE user SET suspended_at='):
                barrier.wait(timeout=5)
        event.listen(engine,'before_cursor_execute',synchronize)
    def request():
        browser=application.test_client()
        with browser.session_transaction() as sess:
            sess.update(_user_id=str(aid),_fresh=True,admin_session=True,
                admin_last_activity=time.time(),admin_auth_version=0)
        return browser.post(f'/admin/users/{uid}/suspension',data=form()).status_code
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(request) for _ in range(2)]
            assert sorted(f.result(timeout=15) for f in futures)==[302,409]
        with application.app_context():
            assert db.session.get(User,uid).suspended_at is not None
            assert AuditLog.query.filter_by(action='USER_SUSPENDED').count()==1
        assert len(emails)==1
    finally:
        event.remove(engine,'before_cursor_execute',synchronize)
        with application.app_context(): db.session.remove();db.engine.dispose()
