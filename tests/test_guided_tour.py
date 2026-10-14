"""Account tour acceptance in disposable SQLite; not PostgreSQL proof."""
import pytest
from app import db
from app.models import AuditLog, Product, Sale, Restock, Expense, User
from app.main.tour import tour_state
from tests.test_multi_business import app, seed, login


def post(client, action, step, version=0):
    return client.post('/onboarding/tour',json=dict(action=action,step=step,version=version))


def test_first_time_and_checklist_unchanged(app):
    user,shops=seed('plus',businesses=2);client=app.test_client();login(client,user)
    html=client.get('/dashboard').data
    assert b'Start Tour' in html and b'Skip Tour' in html
    assert b'0 of 3 setup actions completed' in html
    assert AuditLog.query.count()==0
    for model in (Product,Sale,Restock,Expense): assert model.query.count()==0
    state=post(client,'start',0).json
    with client.session_transaction() as session:session['active_business_id']=shops[1].id
    assert tour_state(user)['version']==state['version']
    assert post(client,'start',0,state['version']).status_code==409
    other,_=seed('plus',email='other@example.invalid')
    assert tour_state(other)['version']==0


@pytest.mark.parametrize('terminal',['skip','finish'])
def test_terminal_persists_no_replay(app,terminal):
    user,_=seed();client=app.test_client();login(client,user)
    state=post(client,'start',0).json
    if terminal=='finish':
        for step in range(1,7):state=post(client,'progress',step,state['version']).json
    response=post(client,terminal,state['step'],state['version'])
    assert response.status_code==200 and not response.json['eligible']
    count=AuditLog.query.count()
    assert post(client,terminal,state['step'],state['version']).status_code==200
    assert AuditLog.query.count()==count
    assert post(client,'start',0,response.json['version']).status_code==409
    assert b'id="stockbridge-tour"' not in client.get('/dashboard').data


def test_progress_back_interrupt_stale_and_rollback(app,monkeypatch):
    user,_=seed();client=app.test_client();login(client,user)
    first=post(client,'start',0).json
    state=post(client,'progress',1,first['version']).json
    assert post(client,'progress',2,first['version']).status_code==409
    state=post(client,'progress',0,state['version']).json
    assert tour_state(user)['started'] and tour_state(user)['step']==0
    assert b'id="stockbridge-tour"' in client.get('/dashboard').data
    assert post(client,'finish',6,state['version']).status_code==409
    count=AuditLog.query.count()
    def fail():raise RuntimeError('synthetic failure')
    with monkeypatch.context() as patch:
        patch.setattr(db.session,'commit',fail)
        with pytest.raises(RuntimeError):post(client,'skip',0,state['version'])
    assert AuditLog.query.count()==count and tour_state(user)['eligible']


def test_established_account_and_admin_excluded(app):
    user,shops=seed('plus',businesses=2)
    db.session.add(Product(business_id=shops[1].id,name='Rice'));db.session.commit()
    assert not tour_state(user)['eligible']
    user.role='admin';db.session.commit()
    assert not tour_state(user)['eligible']


def test_invalid_states_and_anonymous(app):
    user,_=seed();client=app.test_client()
    assert post(client,'start',0).status_code==302
    login(client,user)
    for action,step in [('restart',0),([],0),({},0),(None,0),('progress',7),('progress',True),('progress',-1)]:
        assert post(client,action,step).status_code==400
    assert post(client,'progress',1).status_code==409
    assert AuditLog.query.count()==0


def test_public_ui_has_no_restart_controls(app):
    user,_=seed();client=app.test_client();login(client,user)
    html=client.get('/dashboard').data
    for phrase in [b'Restart Tour',b'Take Tour Again',b'Replay Tour']: assert phrase not in html
    assert b'aria-labelledby="tour-title"' in html and b'aria-describedby="tour-copy"' in html
    assert b'data-tour-back hidden' in html


def test_csrf_required():
    from app import create_app
    application=create_app({'TESTING':False,'SECRET_KEY':'isolated-csrf',
        'SQLALCHEMY_DATABASE_URI':'sqlite:///:memory:'})
    assert application.test_client().post('/onboarding/tour',json={'action':'skip','step':0,'version':0}).status_code==400


def test_started_tour_survives_activity_and_other_account_terminal(app):
    user,shops=seed();client=app.test_client();login(client,user)
    state=post(client,'start',0).json
    db.session.add(Product(business_id=shops[0].id,name='Rice'));db.session.commit()
    assert tour_state(user)['eligible'] and tour_state(user)['version']==state['version']
    other,_=seed(email='other@example.invalid')
    db.session.add(AuditLog(actor_id=other.id,target_type='user',target_id=other.id,
                           action='CUSTOMER_TOUR_SKIPPED',description='0'));db.session.commit()
    assert tour_state(user)['eligible'] and not tour_state(other)['eligible']


def test_mobile_rules_and_reference_label():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    css=(root/'app/static/css/tour.css').read_text()
    assert '@media(max-width:820px)' in css and 'max-height:42dvh' in css
    assert 'min-height:44px' in css and 'prefers-reduced-motion' in css
    source=(root/'app/templates/restocking/index.html').read_text()
    assert "Restock Reference: {{receipt.reference.split(':',1)[1]}}" in source
