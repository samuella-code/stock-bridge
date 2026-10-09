"""Disposable local DBs and mocked SMTP only; hosted delivery is not inferred."""
import io
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from PIL import Image
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from app import create_app, db
from app.models import EmailOutbox, Notification, NotificationPreference, Product, Restock, Sale, StockMovement
from app.notifications.email_outbox import claim, dispatch_one, finish
from app.notifications.service import stock_transition
from tests.test_notifications_business_profile import app, setup, product, sale, restock, upload, local
from tests.test_multi_business import seed, login
from werkzeug.datastructures import MultiDict


def configure(app):
    app.config.update(INVENTORY_EMAILS_ENABLED=True, INVENTORY_EMAIL_BASE_URL='https://stock-bridge-staging.vercel.app',
        INVENTORY_EMAIL_DISPATCH_SECRET='test-dispatch-token-' + 'x' * 32)


def queued(app):
    client, owner, shops = setup(app)
    p = product(client)
    sale(client, p, 3)
    return client, owner, shops[0], p, EmailOutbox.query.one()


def test_threshold_snapshot_owner_and_atomic_sale(app):
    client, owner, business, p, row = queued(app)
    assert (row.recipient_user_id, row.recipient_email, row.business_id) == (owner.id, owner.email, business.id)
    assert row.payload == dict(business_name=business.name, product_name='Coke', quantity=5, threshold=5, unit='bottles')
    assert row.event_type == 'low_stock' and 'Coke' in row.subject
    assert Sale.query.count() == 1 and db.session.get(Product, p.id).stock_quantity == 5
    assert row.event_key == Notification.query.filter_by(kind='low_stock').one().event_key
    assert row.status == 'pending' and row.attempt_count == 0


def test_low_repeat_restock_and_out_cycles(app):
    client, _, _, p, _ = queued(app)
    sale(client, p, 1)
    assert EmailOutbox.query.count() == 1
    sale(client, p, 4)
    assert [x.event_type for x in EmailOutbox.query.order_by(EmailOutbox.id)] == ['low_stock', 'out_of_stock']
    sale(client, p, 1)  # Oversell does not create another event.
    assert EmailOutbox.query.count() == 2
    restock(client, p, 20)
    assert EmailOutbox.query.count() == 3
    sale(client, p, 15)
    assert EmailOutbox.query.count() == 4
    assert len({x.event_key for x in EmailOutbox.query.all()}) == 4


def test_single_restock_queues_one_outbox_email(app):
    client, owner, shops = setup(app)
    p = product(client, 'Coffee')

    response = restock(client, p, 7)

    row = EmailOutbox.query.one()
    assert response.status_code == 302
    assert Restock.query.count() == 1
    assert (row.business_id, row.recipient_user_id, row.recipient_email) == (
        shops[0].id, owner.id, owner.email
    )
    assert row.event_type == 'restock_recorded'
    assert row.payload == {
        'business_name': shops[0].name,
        'items': [{'name': 'Coffee', 'quantity': 7, 'unit': 'bottles'}],
    }
    assert row.event_key.startswith('restock:')


def test_multi_product_restock_queues_one_email_with_each_item(app):
    client, _, shops = setup(app)
    coffee = product(client, 'Coffee')
    tea = product(client, 'Tea')
    data = MultiDict([
        ('product_id', str(coffee.id)), ('product_id', str(tea.id)),
        ('quantity', '7'), ('quantity', '11'),
        ('unit_cost', '280'), ('unit_cost', '290'),
    ])

    response = client.post('/restocking/receive', data=data)

    row = EmailOutbox.query.one()
    assert response.status_code == 302
    assert Restock.query.count() == 2
    assert row.event_type == 'restock_recorded'
    assert row.payload == {
        'business_name': shops[0].name,
        'items': [
            {'name': 'Coffee', 'quantity': 7, 'unit': 'bottles'},
            {'name': 'Tea', 'quantity': 11, 'unit': 'bottles'},
        ],
    }


def test_disabled_restocking_preference_does_not_queue_email(app):
    client, _, shops = setup(app)
    db.session.add(NotificationPreference(business_id=shops[0].id, restocking=False))
    db.session.commit()
    p = product(client, 'Coffee')

    response = restock(client, p, 7)

    assert response.status_code == 302
    assert Restock.query.count() == 1
    assert Notification.query.filter_by(kind='restock_recorded').count() == 1
    assert EmailOutbox.query.count() == 0


def test_restock_preference_change_suppresses_queued_email(app, monkeypatch):
    configure(app)
    client, _, shops = setup(app)
    p = product(client, 'Coffee')
    restock(client, p, 7)
    db.session.add(NotificationPreference(business_id=shops[0].id, restocking=False))
    db.session.commit()
    calls = []
    monkeypatch.setattr(
        'app.email_service._send_email',
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    assert dispatch_one() == 'suppressed'
    assert calls == []
    assert EmailOutbox.query.one().status == 'suppressed'


def test_restock_email_rendering_and_staging_link(app, monkeypatch):
    configure(app)
    client, _, shops = setup(app)
    shops[0].name = '<script>Business</script>'
    db.session.commit()
    coffee = product(client, '<b>Coffee</b>')
    tea = product(client, 'Tea')
    data = MultiDict([
        ('product_id', str(coffee.id)), ('product_id', str(tea.id)),
        ('quantity', '7'), ('quantity', '11'),
        ('unit_cost', '280'), ('unit_cost', '290'),
    ])
    client.post('/restocking/receive', data=data)
    sent = []
    monkeypatch.setattr(
        'app.email_service._send_email',
        lambda *args, **kwargs: sent.append((args, kwargs)) or True,
    )

    assert dispatch_one() == 'sent'

    args, kwargs = sent[0]
    assert args[0].startswith('Stock Replenished — 2 Products')
    assert 'Business: <script>Business</script>' in args[2]
    assert '+7 bottles' in args[2] and '+11 bottles' in args[2]
    assert 'https://stock-bridge-staging.vercel.app/restocking/' in args[2]
    assert 'href="https://stock-bridge-staging.vercel.app/restocking/"' in kwargs['html']
    assert '&lt;b&gt;Coffee&lt;/b&gt;' in kwargs['html']
    assert '<script>Business' not in kwargs['html']
    assert '&lt;script&gt;Business&lt;/script&gt;' in kwargs['html']
    assert 'event_key' not in kwargs['html'] and 'business_id' not in kwargs['html']


def test_restock_outbox_rolls_back_with_failed_restock_commit(app, monkeypatch):
    client, _, _ = setup(app)
    p = product(client, 'Coffee')
    monkeypatch.setattr(
        db.session, 'commit',
        lambda: (_ for _ in ()).throw(RuntimeError('fixture')),
    )

    with pytest.raises(RuntimeError):
        restock(client, p, 7)

    db.session.rollback()
    assert Restock.query.count() == 0
    assert StockMovement.query.filter_by(kind='restock').count() == 0
    assert Notification.query.filter_by(kind='restock_recorded').count() == 0
    assert EmailOutbox.query.count() == 0
    assert db.session.get(Product, p.id).stock_quantity == 8


@pytest.mark.parametrize('delta,kind', [(-4, 'low_stock'), (-8, 'out_of_stock')])
def test_adjustment_shared_alert_rules(app, delta, kind):
    client, _, _ = setup(app)
    p = product(client)
    assert client.post(f'/products/{p.id}/adjust', data={'quantity':abs(delta),'direction':'decrease','reason':'Stock count correction'}).status_code == 302
    row = EmailOutbox.query.one()
    assert row.event_type == kind and row.event_key.startswith('adjustment:')
    assert row.payload['quantity'] == 8 + delta


def test_zero_transition_has_no_low_email_and_no_negative_stock(app):
    client, _, _ = setup(app)
    p = product(client)
    sale(client, p, 8)
    assert EmailOutbox.query.one().event_type == 'out_of_stock'
    sale(client, p, 1)
    assert db.session.get(Product, p.id).stock_quantity == 0 and EmailOutbox.query.count() == 1
    stock_transition(p, 0, 0, 'zero-no-op'); db.session.commit()
    assert EmailOutbox.query.count() == 1


@pytest.mark.parametrize('field,quantity', [('low_stock_email',3),('out_of_stock_email',8)])
def test_disabled_email_does_not_disable_stock_warning(app, field, quantity):
    client, _, shops = setup(app)
    pref = NotificationPreference(business_id=shops[0].id, **{field:False})
    db.session.add(pref); db.session.commit()
    p = product(client); sale(client, p, quantity)
    assert EmailOutbox.query.count() == 0
    assert Notification.query.filter_by(kind=field.removesuffix('_email')).count() == 1


@pytest.mark.parametrize('field,event', [('product_added','product_created'),('sales','sale_recorded'),('restocking','restock_recorded')])
def test_optional_activity_preference(app, field, event):
    client, _, shops = setup(app)
    db.session.add(NotificationPreference(business_id=shops[0].id, **{field:False})); db.session.commit()
    p = product(client); sale(client, p, 3); restock(client, p, 8)
    assert Notification.query.filter_by(kind=event).count() == (1 if event == 'restock_recorded' else 0)
    assert Notification.query.filter_by(kind='low_stock').count() == 1
    assert EmailOutbox.query.count() == (1 if field == 'restocking' else 2)
    assert EmailOutbox.query.filter_by(event_type='restock_recorded').count() == (
        0 if field == 'restocking' else 1
    )


def test_queue_and_inventory_rollback_together(app, monkeypatch):
    client, _, _ = setup(app); p = product(client)
    original = db.session.commit
    monkeypatch.setattr(db.session,'commit',lambda: (_ for _ in ()).throw(RuntimeError('fixture')))
    with pytest.raises(RuntimeError): sale(client,p,3)
    monkeypatch.setattr(db.session,'commit',original)
    db.session.rollback()
    assert EmailOutbox.query.count() == Sale.query.count() == 0
    assert db.session.get(Product,p.id).stock_quantity == 8
    assert Notification.query.filter_by(kind='low_stock').count() == 0


def test_unique_event_rejects_replay_atomically(app):
    _, _, _, p, row = queued(app)
    key = row.event_key.removesuffix(':low')
    with pytest.raises(IntegrityError):
        stock_transition(p,8,5,key)
        db.session.commit()
    db.session.rollback()
    assert EmailOutbox.query.count() == 1


@pytest.mark.parametrize('operation', ['sale','adjustment'])
def test_smtp_failure_preserves_inventory(app, monkeypatch, caplog, operation):
    configure(app)
    client, _, _ = setup(app); p = product(client)
    calls=[]
    def failing(*args,**kwargs):
        calls.append(args)
        raise RuntimeError('sensitive-provider-credential-must-not-be-logged')
    monkeypatch.setattr('app.email_service._send_email',failing)
    if operation == 'sale': sale(client,p,3)
    else: client.post(f'/products/{p.id}/adjust',data={'quantity':3,'direction':'decrease','reason':'Stock count correction'})
    assert calls == []  # Network work is not in the stock transaction.
    assert dispatch_one() == 'retry'
    assert db.session.get(Product,p.id).stock_quantity == 5
    assert Sale.query.count() == (1 if operation == 'sale' else 0)
    row = EmailOutbox.query.one()
    assert row.attempt_count == 1 and row.next_attempt_at > row.last_attempt_at
    assert row.last_error == 'RuntimeError'
    assert 'sensitive-provider-credential' not in caplog.text


def test_retry_backoff_success_and_terminal_limit(app,monkeypatch):
    configure(app); _, _, _, _, row=queued(app)
    row.max_attempts=3; db.session.commit()
    now=datetime.utcnow()
    assert dispatch_one(now=now)=='retry'
    row=db.session.get(EmailOutbox,row.id); first=row.next_attempt_at
    assert dispatch_one(now=now)=='idle'
    assert dispatch_one(now=first)=='retry'
    row=db.session.get(EmailOutbox,row.id)
    assert (row.next_attempt_at-first).total_seconds()==120
    assert dispatch_one(now=row.next_attempt_at)=='failed'
    assert dispatch_one(now=now+timedelta(days=1))=='idle'
    assert EmailOutbox.query.one().attempt_count==3


def test_retry_then_success(app,monkeypatch):
    configure(app); _, _, _, _, row=queued(app)
    assert dispatch_one()=='retry'
    next_at=EmailOutbox.query.one().next_attempt_at
    monkeypatch.setattr('app.email_service._send_email',lambda *a,**k:True)
    assert dispatch_one(now=next_at)=='sent'
    row=EmailOutbox.query.one()
    assert row.sent_at and row.attempt_count==2 and row.claim_token is None
    assert dispatch_one(now=next_at+timedelta(days=1))=='idle'


def test_claim_exclusive_recovery_and_old_completion_rejected(app):
    configure(app); _, _, _, _, row=queued(app)
    now=datetime.utcnow(); old=claim(now)
    assert old and claim(now) is None
    new=claim(now+timedelta(seconds=301))
    assert new and new[0]==old[0] and new[1]!=old[1]
    assert finish(*old,'sent',now) is False
    assert finish(*new,'sent',now) is True
    assert EmailOutbox.query.one().attempt_count==2


def test_crashed_last_attempt_is_terminal(app):
    configure(app); _, _, _, _, row=queued(app)
    row.max_attempts=1; db.session.commit()
    now=datetime.utcnow(); assert claim(now)
    assert claim(now+timedelta(seconds=301)) is None
    assert EmailOutbox.query.one().status=='failed'


@pytest.mark.parametrize('mutation',['email_changed','unverified','suspended','business_suspended','invalid_email','wrong_owner'])
def test_invalid_or_changed_recipient_not_sent(app,monkeypatch,mutation):
    configure(app); _,owner,business,_,row=queued(app)
    if mutation=='email_changed': owner.email='new@example.invalid'
    elif mutation=='unverified': owner.email_verified_at=None
    elif mutation=='suspended': owner.suspended_at=datetime.utcnow()
    elif mutation=='business_suspended': business.suspended_at=datetime.utcnow()
    elif mutation=='invalid_email': row.recipient_email='bad\r\nBcc: stolen@example.invalid'
    else:
        other,_=seed('basic',email='other@example.invalid'); business.user_id=other.id
    db.session.commit()
    calls=[];monkeypatch.setattr('app.email_service._send_email',lambda *a,**k:calls.append(a))
    assert dispatch_one()=='failed' and not calls


@pytest.mark.parametrize('field,quantity',[('low_stock_email',3),('out_of_stock_email',8)])
def test_opt_out_after_queue_suppresses_delivery(app,monkeypatch,field,quantity):
    configure(app);client,_,shops=setup(app);p=product(client);sale(client,p,quantity)
    db.session.add(NotificationPreference(business_id=shops[0].id,**{field:False}));db.session.commit()
    calls=[];monkeypatch.setattr('app.email_service._send_email',lambda *a,**k:calls.append(a))
    assert dispatch_one()=='suppressed' and not calls
    assert Notification.query.filter_by(kind=field.removesuffix('_email')).count()==1


@pytest.mark.parametrize('origin',['http://example.invalid','https://user:password@example.invalid','https://example.invalid/path','https://example.invalid?x=1','','https://example.invalid:444'])
def test_bad_origin_does_not_consume_attempts(app,origin):
    configure(app);queued(app);app.config['INVENTORY_EMAIL_BASE_URL']=origin
    assert dispatch_one()=='configuration_required'
    assert EmailOutbox.query.one().attempt_count==0


def test_staging_links_escaping_plain_text_and_no_ids(app,monkeypatch):
    configure(app);client,_,shops=setup(app)
    shops[0].name='<script>Business</script>';db.session.commit()
    p=product(client,'<b>Drink</b>');sale(client,p,3)
    sent=[];monkeypatch.setattr('app.email_service._send_email',lambda *a,**k:sent.append((a,k)) or True)
    assert dispatch_one()=='sent'
    args,kwargs=sent[0]
    assert 'https://stock-bridge-staging.vercel.app/products/?view=inventory' in args[2]
    assert '&lt;b&gt;Drink&lt;/b&gt;' in kwargs['html'] and '<script>Business' not in kwargs['html']
    assert '&lt;script&gt;Business&lt;/script&gt;' in kwargs['html']
    assert 'event_key' not in kwargs['html'] and 'business_id' not in kwargs['html']


def test_plus_business_snapshots_and_scoped_dispatch(app,monkeypatch):
    configure(app);client,user,shops=setup(app,2)
    a=product(client,'A');sale(client,a,3)
    client.post(f'/businesses/{shops[1].id}/switch')
    b=product(client,'B');sale(client,b,8)
    sent=[];monkeypatch.setattr('app.email_service._send_email',lambda *a,**k:sent.append(a) or True)
    result=client.post('/notifications/email-dispatch').json
    assert result['result']=='sent' and 'Shop 1' in sent[0][2] and 'Product: A' not in sent[0][2]
    assert EmailOutbox.query.filter_by(business_id=shops[0].id).one().status=='pending'


def test_preferences_defaults_validation_and_history(app):
    client,_,shops=setup(app);p=product(client)
    html=client.get('/profile/').get_data(as_text=True)
    assert html.count('checked')>=5
    response=client.post('/notifications/preferences',data={'low_stock_email':'not-a-boolean'})
    assert response.status_code==400 and NotificationPreference.query.count()==0
    response=client.post('/notifications/preferences',data={})
    assert response.status_code==302
    assert not NotificationPreference.query.one().low_stock_email
    assert Notification.query.count()==1
    sale(client,p,3)
    assert Notification.query.filter_by(kind='low_stock').count()==1 and EmailOutbox.query.count()==0


def test_click_to_read_count_idempotency_persistence_and_history(app):
    client,_,_,_,_=queued(app)
    row=Notification.query.filter_by(kind='low_stock').one();initial=Notification.query.filter_by(read_at=None).count()
    assert client.get('/notifications/').status_code==200 and row.read_at is None
    path=f'/notifications/{row.id}/open'
    result=client.post(path,headers={'Accept':'application/json'}).json
    assert result['unread_count']==initial-1 and result['destination'].startswith('/products/')
    when=db.session.get(Notification,row.id).read_at
    assert client.post(path,headers={'Accept':'application/json'}).json['unread_count']==initial-1
    assert db.session.get(Notification,row.id).read_at==when
    assert Notification.query.count()==initial
    assert client.get('/notifications/').status_code==200 and db.session.get(Notification,row.id).read_at
    assert client.get(path).status_code==405


def test_click_fallback_and_alternative_read_controls(app):
    client,_,_,_,_=queued(app);row=Notification.query.first()
    assert client.post(f'/notifications/{row.id}/open').status_code==302
    assert client.post(f'/notifications/{row.id}/read').status_code==302
    assert client.post('/notifications/read-all').status_code==302
    assert Notification.query.filter_by(read_at=None).count()==0


def test_cross_user_and_cross_business_read_rejected(app):
    client,user,shops=setup(app,2);p=product(client);n=Notification.query.one()
    client.post(f'/businesses/{shops[1].id}/switch')
    assert client.post(f'/notifications/{n.id}/open').status_code==404
    other,_=seed('basic',email='second@example.invalid');login(client,other)
    assert client.post(f'/notifications/{n.id}/open',data={'user_id':user.id,'business_id':shops[0].id}).status_code==404
    assert db.session.get(Notification,n.id).read_at is None


def test_csrf_and_worker_authentication(app):
    configure(app);client,_,_,_,_=queued(app);app.config['WTF_CSRF_ENABLED']=True
    n=Notification.query.first()
    for path in (f'/notifications/{n.id}/open','/notifications/email-dispatch','/notifications/preferences'):
        assert client.post(path).status_code==400
    for auth in ('','Bearer wrong','Bearer ü'):
        assert client.post('/internal/inventory-emails/dispatch',headers={'Authorization':auth}).status_code==404
    good='Bearer '+app.config['INVENTORY_EMAIL_DISPATCH_SECRET']
    response=client.post('/internal/inventory-emails/dispatch',headers={'Authorization':good})
    assert response.status_code==200 and response.json['result']=='retry'


def test_dispatch_disabled_by_default(app):
    queued(app);assert dispatch_one()=='disabled'
    assert EmailOutbox.query.one().attempt_count==0


@pytest.mark.parametrize('fmt',['JPEG','PNG','WEBP'])
def test_5mib_image_boundary_optimized(app,tmp_path,fmt):
    local(app,tmp_path);client,_,shops=setup(app)
    buf=io.BytesIO();Image.new('RGBA' if fmt!='JPEG' else 'RGB',(1600,800),(20,80,120,100) if fmt!='JPEG' else (20,80,120)).save(buf,format=fmt)
    data=buf.getvalue();data+=b'\0'*(5*1024*1024-len(data))
    assert upload(client,shops[0],fmt,data=data).status_code==302
    key=db.session.get(type(shops[0]),shops[0].id).logo_key;assert key
    raw=(tmp_path/key).read_bytes();assert len(raw)<500*1024
    with Image.open(io.BytesIO(raw)) as img:
        assert img.format=='WEBP' and img.size==(1024,512)
        if fmt!='JPEG': assert img.convert('RGBA').getpixel((1,1))[3]==100


def test_over_5mib_rejected_preserving_existing(app,tmp_path):
    local(app,tmp_path);client,_,shops=setup(app);upload(client,shops[0]);old=shops[0].logo_key
    response=upload(client,shops[0],data=b'x'*(5*1024*1024+1))
    assert response.status_code==302 and db.session.get(type(shops[0]),shops[0].id).logo_key==old
    assert '5 MB (5 MiB)' in client.get(response.location).get_data(as_text=True)


@pytest.mark.parametrize('mime',['text/plain','application/octet-stream','image/gif','image/jpeg'])
def test_image_mime_mismatch_rejected(app,tmp_path,mime):
    local(app,tmp_path);client,_,shops=setup(app);buf=io.BytesIO();Image.new('RGB',(40,30)).save(buf,format='PNG')
    response=client.post(f'/businesses/{shops[0].id}/logo',data={'logo':(io.BytesIO(buf.getvalue()),'image.png',mime)})
    assert response.status_code==302 and shops[0].logo_key is None


def test_exif_orientation_and_metadata_removed(app,tmp_path):
    local(app,tmp_path);client,_,shops=setup(app);buf=io.BytesIO();image=Image.new('RGB',(1200,600),'red')
    exif=Image.Exif();exif[274]=6;exif[270]='private metadata';image.save(buf,format='JPEG',exif=exif)
    upload(client,shops[0],'JPEG',data=buf.getvalue())
    with Image.open(tmp_path/shops[0].logo_key) as img:
        assert img.size==(512,1024) and not img.getexif()


def test_migration_preserves_existing_false_preferences_and_logo(tmp_path):
    from flask_migrate import upgrade, downgrade
    application=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':f"sqlite:///{tmp_path/'migration.db'}"})
    with application.app_context():
        upgrade(revision='0015_notifications_business_logo')
        db.session.execute(text("INSERT INTO user(id,full_name,email,password_hash,created_at,role,admin_enabled,admin_auth_version) VALUES(1,'Owner','test@example.invalid','fixture',CURRENT_TIMESTAMP,'user',0,0)"))
        db.session.execute(text("INSERT INTO business(id,user_id,name,created_at,trial_started_at,trial_ends_at,logo_key) VALUES(1,1,'Shop',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'businesses/1/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.webp')"))
        db.session.execute(text("INSERT INTO notification_preference(business_id,low_stock_email,out_of_stock_email) VALUES(1,0,1)"));db.session.commit()
        upgrade()
        assert db.session.execute(text('SELECT low_stock_email,out_of_stock_email,product_added,sales,restocking FROM notification_preference')).one()==(0,1,1,1,1)
        assert db.session.execute(text('SELECT logo_key FROM business')).scalar().endswith('.webp')
        assert db.session.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0016_inventory_email_outbox'
        downgrade(revision='0015_notifications_business_logo')
        assert db.session.execute(text('SELECT low_stock_email,out_of_stock_email FROM notification_preference')).one()==(0,1)
        db.session.remove()


def test_simultaneous_claims_local_database_only(tmp_path):
    application=create_app({'TESTING':True,'SQLALCHEMY_DATABASE_URI':f"sqlite:///{tmp_path/'claims.db'}",'INVENTORY_EMAILS_ENABLED':True})
    with application.app_context():
        db.create_all();owner,shops=seed();p=Product(business_id=shops[0].id,name='Claim',stock_quantity=5,opening_quantity=8,minimum_stock_level=5,buying_price=1,selling_price=2,unit='units')
        db.session.add(p);db.session.flush();stock_transition(p,8,5,'race');db.session.commit()
    barrier=Barrier(2)
    def attempt():
        with application.app_context():
            barrier.wait();result=claim();db.session.remove();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:attempt(),range(2)))
    assert sum(x is not None for x in results)==1
    with application.app_context():
        assert EmailOutbox.query.one().attempt_count==1;db.session.remove()


def test_postcommit_durable_trigger_and_failure_do_not_reverse_sale(app,monkeypatch,caplog):
    configure(app)
    app.config.update(INVENTORY_EMAIL_TRIGGER_URL='https://queue.example.invalid/ingress',INVENTORY_EMAIL_TRIGGER_SECRET='x'*40)
    client,_,_=setup(app);p=product(client)
    calls=[]
    def ingress(*args,**kwargs):
        assert Sale.query.count()==1 and EmailOutbox.query.count()==1
        calls.append((args,kwargs))
        raise RuntimeError('external-secret-must-not-appear')
    monkeypatch.setattr('app.notifications.email_outbox.requests.post',ingress)
    response=sale(client,p,3)
    assert response.status_code==302 and len(calls)==1
    assert db.session.get(Product,p.id).stock_quantity==5
    assert EmailOutbox.query.one().status=='pending'
    assert 'external-secret-must-not-appear' not in caplog.text
    assert calls[0][1]['allow_redirects'] is False
    client.get('/notifications/')
    assert len(calls)==1


def test_rollback_does_not_publish_inventory_trigger(app,monkeypatch):
    configure(app);app.config.update(INVENTORY_EMAIL_TRIGGER_URL='https://queue.example.invalid/ingress',INVENTORY_EMAIL_TRIGGER_SECRET='x'*40)
    client,_,_=setup(app);p=product(client);calls=[]
    monkeypatch.setattr('app.notifications.email_outbox.requests.post',lambda *a,**k:calls.append(a))
    with app.test_request_context('/sales/',method='POST'):
        stock_transition(p,8,5,'rolled-back');db.session.rollback()
        from app.notifications.email_outbox import publish_committed_alert
        from flask import Response
        publish_committed_alert(Response())
    assert calls==[] and EmailOutbox.query.count()==0


def test_multiple_sale_items_queue_separate_stock_events(app):
    from werkzeug.datastructures import MultiDict
    client,_,_=setup(app);a=product(client,'A',8);b=product(client,'B',8)
    response=client.post('/sales/',data=MultiDict([('product_id',str(a.id)),('product_id',str(b.id)),('quantity','3'),('quantity','8'),('unit_price',''),('unit_price','')]))
    assert response.status_code==302 and Sale.query.count()==1
    assert {r.event_type for r in EmailOutbox.query.all()}=={'low_stock','out_of_stock'}
    assert {r.payload['product_name'] for r in EmailOutbox.query.all()}=={'A','B'}


def test_unverified_recipient_not_queued(app):
    client,owner,shops=setup(app);p=product(client)
    owner.email_verified_at=None;db.session.commit()
    stock_transition(p,8,5,'unverified');db.session.commit()
    assert EmailOutbox.query.count()==0 and Notification.query.filter_by(kind='low_stock').count()==1


def test_repeated_inventory_request_does_not_repeat_crossing_email(app):
    client,_,_=setup(app);p=product(client)
    sale(client,p,3);sale(client,p,3)
    assert EmailOutbox.query.count()==1
    assert db.session.get(Product,p.id).stock_quantity==2
    sale(client,p,2);sale(client,p,2)
    assert EmailOutbox.query.count()==2
    assert db.session.get(Product,p.id).stock_quantity==0


def test_positive_adjustment_resets_email_eligibility(app):
    client,_,_,p,_=queued(app)
    client.post(f'/products/{p.id}/adjust',data={'quantity':10,'direction':'increase','reason':'Stock count correction'})
    assert EmailOutbox.query.count()==1
    client.post(f'/products/{p.id}/adjust',data={'quantity':10,'direction':'decrease','reason':'Stock count correction'})
    assert EmailOutbox.query.count()==2


def test_dispatch_runs_without_open_inventory_transaction(app,monkeypatch):
    configure(app);queued(app)
    def transport(*args,**kwargs):
        assert not db.session().in_transaction()
        return True
    monkeypatch.setattr('app.email_service._send_email',transport)
    assert dispatch_one()=='sent'


def test_postgres_migration_and_locking_sql_compile_only(app):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.dialects import postgresql
    from app.notifications.email_outbox import eligible
    spec=importlib.util.spec_from_file_location('outbox_migration',Path('migrations/versions/0016_inventory_email_outbox.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    output=io.StringIO()
    context=MigrationContext.configure(url='postgresql://',opts={'as_sql':True,'output_buffer':output})
    with Operations.context(context): module.upgrade()
    sql=output.getvalue()
    assert 'CREATE TABLE email_outbox' in sql and 'FOREIGN KEY(business_id)' in sql
    assert 'ALTER COLUMN low_stock_email SET DEFAULT true' in sql
    assert 'DROP TABLE' not in sql
    query=EmailOutbox.query.filter(eligible(datetime.utcnow())).with_for_update(skip_locked=True)
    assert 'FOR UPDATE SKIP LOCKED' in str(query.statement.compile(dialect=postgresql.dialect()))
