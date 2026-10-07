"""Disposable fixtures only. No SMTP, provider payments or hosted DB writes."""
import io
from datetime import datetime, timedelta
from pathlib import Path
import pytest
from PIL import Image
from werkzeug.datastructures import MultiDict
from sqlalchemy.exc import IntegrityError
from app import db
from app.models import Notification, NotificationPreference, Business, Product, Sale, Restock, StockMovement, RecurringSubscription
from app.notifications.service import stock_transition
from app.businesses.images import LocalStorage, S3Storage, StorageError, checked_key, image_storage
from tests.test_multi_business import app, seed, login


def setup(app, businesses=1):
    user, shops = seed('plus', businesses)
    client = app.test_client()
    login(client, user)
    return client, user, shops


def product(client, name='Coke', stock=8, minimum=5):
    response = client.post('/products/new', data={'name': name, 'stock_quantity': str(stock),
        'minimum_stock_level': str(minimum), 'buying_price': '250', 'selling_price': '350', 'unit': 'bottles'})
    assert response.status_code == 302
    return Product.query.filter_by(name=name).one()


def sale(client, p, quantity):
    return client.post('/sales/', data={'product_id': p.id, 'quantity': str(quantity), 'unit_price': ''})


def restock(client, p, quantity):
    return client.post('/restocking/receive', data={'product_id': p.id, 'quantity': str(quantity), 'unit_cost': '280'})


def count(kind):
    return Notification.query.filter_by(kind=kind).count()


def upload(client, business, fmt='PNG', filename=None, data=None):
    if data is None:
        stream = io.BytesIO()
        Image.new('RGB', (40, 30), 'red').save(stream, format=fmt)
        data = stream.getvalue()
    return client.post(f'/businesses/{business.id}/logo', data={'logo': (io.BytesIO(data), filename or 'logo.' + fmt.lower())})


def local(app, tmp_path):
    app.config.update(BUSINESS_IMAGE_STORAGE='local', BUSINESS_IMAGE_LOCAL_DIR=str(tmp_path))


def test_product_success_and_invalid_form(app):
    client, user, shops = setup(app)
    p = product(client)
    n = Notification.query.one()
    assert (n.user_id, n.business_id, n.kind, n.resource_id) == (user.id, shops[0].id, 'product_created', p.id)
    assert '8 bottles' in n.body and n.read_at is None
    client.post('/products/new', data={'name': '', 'stock_quantity': '8'})
    assert Notification.query.count() == Product.query.count() == 1


def test_product_commit_failure_rolls_back_notification(app, monkeypatch):
    client, _, _ = setup(app)
    monkeypatch.setattr(db.session, 'commit', lambda: (_ for _ in ()).throw(IntegrityError('fixture', {}, Exception())))
    response = client.post('/products/new', data={'name': 'Failed', 'stock_quantity': '8', 'buying_price': '2', 'selling_price': '3'})
    assert response.status_code == 200
    assert Product.query.count() == Notification.query.count() == StockMovement.query.count() == 0


def test_one_sale_notification_for_multiple_items_and_oversell(app):
    client, _, _ = setup(app)
    a, b = product(client, 'Coke', 20), product(client, 'Bread', 20)
    data = MultiDict([('product_id', str(a.id)), ('product_id', str(b.id)), ('quantity', '2'), ('quantity', '3'), ('unit_price', ''), ('unit_price', '')])
    assert client.post('/sales/', data=data).status_code == 302
    assert Sale.query.count() == count('sale_recorded') == 1
    assert '₦1,750.00' in Notification.query.filter_by(kind='sale_recorded').one().body
    assert sale(client, a, 100).status_code == 302
    assert Sale.query.count() == count('sale_recorded') == 1


def test_sale_commit_failure_rolls_back_all(app, monkeypatch):
    client, _, _ = setup(app)
    p = product(client)
    before = Notification.query.count()
    monkeypatch.setattr(db.session, 'commit', lambda: (_ for _ in ()).throw(RuntimeError('fixture')))
    with pytest.raises(RuntimeError):
        sale(client, p, 3)
    assert Sale.query.count() == 0 and Notification.query.count() == before
    assert db.session.get(Product, p.id).stock_quantity == 8


def test_low_stock_crossing_replenishment_and_zero_cycles(app):
    client, _, _ = setup(app)
    p = product(client)
    sale(client, p, 3)  # 8 -> 5
    assert count('low_stock') == 1
    sale(client, p, 1)  # 5 -> 4
    assert count('low_stock') == 1
    restock(client, p, 8)  # 4 -> 12
    sale(client, p, 7)  # 12 -> 5
    assert count('low_stock') == 2
    sale(client, p, 5)  # 5 -> 0
    assert count('out_of_stock') == 1 and count('low_stock') == 2
    sale(client, p, 1)  # rejected at zero
    assert count('out_of_stock') == 1
    restock(client, p, 2)
    sale(client, p, 2)
    assert count('out_of_stock') == 2 and count('low_stock') == 2


@pytest.mark.parametrize('before,after,minimum,expected', [(8,0,5,'out_of_stock'),(8,5,5,'low_stock'),(5,4,5,None),(0,0,5,None),(0,8,5,None),(1,0,0,'out_of_stock'),(8,6,5,None),(4,12,5,None)])
def test_transition_rules(app, before, after, minimum, expected):
    client, _, _ = setup(app)
    p = product(client, stock=before, minimum=minimum)
    stock_transition(p, before, after, 'fixture-transition')
    db.session.commit()
    alerts = Notification.query.filter(Notification.kind.in_(['low_stock', 'out_of_stock'])).all()
    assert [row.kind for row in alerts] == ([expected] if expected else [])


@pytest.mark.parametrize('quantity,kind', [(3,'low_stock'),(8,'out_of_stock')])
def test_adjustment_alert_and_history(app, quantity, kind):
    client, _, _ = setup(app)
    p = product(client)
    client.post(f'/products/{p.id}/adjust', data={'quantity': str(quantity), 'direction': 'decrease', 'reason': 'Damaged'})
    assert count(kind) == 1
    assert StockMovement.query.filter_by(kind='adjustment').one().quantity_change == -quantity


def test_restock_one_notification_per_batch_and_failed_receipt(app):
    client, _, _ = setup(app)
    a, b = product(client, 'Coke'), product(client, 'Bread')
    data = MultiDict([('product_id',str(a.id)),('product_id',str(b.id)),('quantity','2'),('quantity','3'),('unit_cost','280'),('unit_cost','290')])
    client.post('/restocking/receive', data=data)
    assert Restock.query.count() == 2 and count('restock_recorded') == 1
    assert '2 products' in Notification.query.filter_by(kind='restock_recorded').one().body
    client.post('/restocking/receive', data={'product_id': a.id, 'quantity': '-2', 'unit_cost': '280'})
    assert Restock.query.count() == 2 and count('restock_recorded') == 1


def test_restock_commit_failure(app, monkeypatch):
    client, _, _ = setup(app)
    p = product(client)
    monkeypatch.setattr(db.session, 'commit', lambda: (_ for _ in ()).throw(RuntimeError('fixture')))
    with pytest.raises(RuntimeError):
        restock(client, p, 3)
    assert Restock.query.count() == count('restock_recorded') == 0
    assert db.session.get(Product, p.id).stock_quantity == 8


def test_unread_mark_read_all_order_and_business_isolation(app):
    client, user, shops = setup(app, 2)
    first = product(client, 'Earlier')
    second = product(client, 'Later')
    html = client.get('/notifications/').get_data(as_text=True)
    history = html.split('<div class="notification-list">', 1)[1]
    assert history.index('Later was added') < history.index('Earlier was added')
    assert 'Notifications, 2 unread' in html
    n = Notification.query.filter_by(resource_id=first.id).one()
    client.post(f'/notifications/{n.id}/read')
    assert 'Notifications, 1 unread' in client.get('/dashboard').get_data(as_text=True)
    client.post(f'/notifications/{n.id}/read')
    client.post(f'/businesses/{shops[1].id}/switch')
    product(client, 'Private B')
    assert client.post(f'/notifications/{n.id}/read').status_code == 404
    html = client.get('/notifications/').get_data(as_text=True)
    assert 'Private B was added' in html and 'Earlier was added' not in html
    client.post('/notifications/read-all')
    assert Notification.query.filter_by(business_id=shops[0].id, read_at=None).count() == 1
    assert Notification.query.filter_by(business_id=shops[1].id, read_at=None).count() == 0
    client.post(f'/businesses/{shops[0].id}/switch')
    client.post('/notifications/read-all')
    assert Notification.query.filter_by(read_at=None).count() == 0


def test_cross_user_notification_and_profile_requests(app, tmp_path):
    local(app,tmp_path)
    client, user, shops = setup(app)
    product(client)
    n = Notification.query.one()
    other, foreign = seed('plus', email='foreign@example.invalid')
    client2 = app.test_client(); login(client2,other)
    assert client2.post(f'/notifications/{n.id}/read').status_code == 404
    assert n.read_at is None
    assert 'Coke was added' not in client2.get('/notifications/').get_data(as_text=True)
    for path in ('profile','logo'):
        assert client2.get(f'/businesses/{shops[0].id}/{path}').status_code == 404
    assert upload(client2, shops[0]).status_code == 404
    assert client2.post(f'/businesses/{shops[0].id}/logo/remove').status_code == 404


def test_badge_caps_large_counts_and_pagination(app):
    from app.notifications.service import notify
    client, _, shops = setup(app)
    for i in range(102):
        notify(shops[0].id, 'restock_recorded', 'Stock replenished', 'Fixture', f'fixture:{i}')
    db.session.commit()
    html = client.get('/notifications/').get_data(as_text=True)
    assert '99+' in html and 'Page 1 of 5' in html
    assert html.count('notification-row ') == 25


def test_preferences_scoped_without_email(app):
    client, _, shops = setup(app,2)
    client.post('/notifications/preferences', data={'low_stock_email':'on','business_id':shops[1].id})
    pref = db.session.get(NotificationPreference,shops[0].id)
    assert pref.low_stock_email and not pref.out_of_stock_email
    assert db.session.get(NotificationPreference,shops[1].id) is None
    assert 'Email alerts are not enabled yet' in client.get('/notifications/').get_data(as_text=True)


@pytest.mark.parametrize('fmt',['JPEG','PNG','WEBP'])
def test_valid_upload_private_get_and_default_avatar(app,tmp_path,fmt):
    local(app,tmp_path)
    client, _, shops = setup(app)
    b = shops[0]
    assert 'default business avatar' in client.get(f'/businesses/{b.id}/profile').get_data(as_text=True)
    assert upload(client,b,fmt).status_code == 302
    db.session.refresh(b)
    assert b.logo_key.startswith(f'businesses/{b.id}/') and b.logo_key.endswith('.webp')
    assert (tmp_path / b.logo_key).is_file()
    response = client.get(f'/businesses/{b.id}/logo')
    assert response.status_code == 200 and response.mimetype == 'image/webp'
    assert response.headers['Cache-Control'] == 'no-store, private'
    assert Image.open(io.BytesIO(response.data)).format == 'WEBP'
    from flask import g
    g.pop('_login_user', None)
    assert app.test_client().get(f'/businesses/{b.id}/logo').status_code == 302


@pytest.mark.parametrize('filename,data',[('evil.svg',b'<svg/>'),('evil.png',b'not an image'),('evil.exe',b'not an image'),('big.png',b'x'*(1024*1024+1))])
def test_invalid_unsupported_and_oversize_image(app,tmp_path,filename,data):
    local(app,tmp_path)
    client, _, shops = setup(app)
    upload(client,shops[0],filename=filename,data=data)
    assert db.session.get(Business,shops[0].id).logo_key is None
    assert not list(tmp_path.rglob('*.webp'))


def test_spoofed_format_rejected_and_filename_never_used(app,tmp_path):
    local(app,tmp_path)
    client, _, shops = setup(app)
    upload(client, shops[0],fmt='JPEG',filename='logo.png')
    assert shops[0].logo_key is None
    upload(client, shops[0],filename='../../malicious.png')
    assert checked_key(shops[0].logo_key) == shops[0].logo_key
    assert 'malicious' not in shops[0].logo_key


def test_replace_remove_and_plus_switch_avatar(app,tmp_path):
    local(app,tmp_path)
    client, _, shops = setup(app,2)
    upload(client,shops[0]); first_key=shops[0].logo_key
    upload(client,shops[0],fmt='JPEG'); second_key=shops[0].logo_key
    assert first_key!=second_key and not (tmp_path/first_key).exists()
    upload(client,shops[1],fmt='WEBP')
    client.post(f'/businesses/{shops[1].id}/switch')
    html=client.get('/dashboard').get_data(as_text=True)
    assert f'/businesses/{shops[1].id}/logo?' in html and f'/businesses/{shops[0].id}/logo?' not in html
    client.post(f'/businesses/{shops[1].id}/logo/remove')
    assert shops[1].logo_key is None and shops[0].logo_key==second_key
    assert 'default business avatar' in client.get('/dashboard').get_data(as_text=True)


def test_storage_put_failure_preserves_business_and_products(app,tmp_path,monkeypatch):
    local(app,tmp_path)
    client, _, shops = setup(app)
    p = product(client)
    upload(client,shops[0]); old=shops[0].logo_key
    monkeypatch.setattr(LocalStorage,'put',lambda *a: (_ for _ in ()).throw(OSError('fixture')))
    upload(client,shops[0])
    assert shops[0].logo_key==old and (tmp_path/old).exists()
    assert p.stock_quantity==8 and Product.query.count()==1


def test_image_db_failure_removes_new_object_keeps_old(app,tmp_path,monkeypatch):
    local(app,tmp_path)
    client, _, shops = setup(app)
    upload(client,shops[0]); old=shops[0].logo_key
    monkeypatch.setattr(db.session,'commit',lambda: (_ for _ in ()).throw(RuntimeError('fixture')))
    upload(client,shops[0])
    assert db.session.get(Business,shops[0].id).logo_key==old
    assert [str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*.webp')]==[old]


def test_cleanup_failure_does_not_rollback_new_reference(app,tmp_path,monkeypatch):
    local(app,tmp_path)
    client, _, shops = setup(app)
    upload(client,shops[0]); old=shops[0].logo_key
    monkeypatch.setattr(LocalStorage,'delete',lambda *a: (_ for _ in ()).throw(OSError('fixture')))
    upload(client,shops[0])
    assert shops[0].logo_key!=old
    client.post(f'/businesses/{shops[0].id}/logo/remove')
    assert shops[0].logo_key is None


def test_disabled_storage_and_vercel_local_guard(app,tmp_path,monkeypatch):
    client, _, shops = setup(app)
    assert 'uploads are not available yet' in client.get(f'/businesses/{shops[0].id}/profile').get_data(as_text=True)
    upload(client,shops[0]); assert shops[0].logo_key is None
    local(app,tmp_path); monkeypatch.setenv('VERCEL','1')
    with pytest.raises(StorageError): image_storage()


def test_locked_and_expired_business_image_security(app,tmp_path):
    local(app,tmp_path)
    client, user, shops = setup(app,2)
    upload(client,shops[1]); key=shops[1].logo_key
    sub=RecurringSubscription.query.one(); sub.current_period_end=datetime.utcnow()-timedelta(seconds=1)
    db.session.commit()
    for path in ('profile','logo'):
        assert client.get(f'/businesses/{shops[1].id}/{path}').status_code==403
    assert upload(client,shops[1]).status_code==403
    assert client.post(f'/businesses/{shops[1].id}/logo/remove').status_code==403
    assert shops[1].logo_key==key and (tmp_path/key).exists()


def test_csrf_on_notification_and_logo_mutations(app,tmp_path):
    local(app,tmp_path)
    client, _, shops=setup(app)
    product(client); n=Notification.query.one()
    app.config['WTF_CSRF_ENABLED']=True
    for path in (f'/notifications/{n.id}/read','/notifications/read-all','/notifications/preferences',f'/businesses/{shops[0].id}/logo',f'/businesses/{shops[0].id}/logo/remove'):
        assert client.post(path).status_code==400
    assert n.read_at is None and shops[0].logo_key is None


def test_local_storage_rejects_arbitrary_keys(app,tmp_path):
    storage=LocalStorage(tmp_path)
    for key in ('../../secret','businesses/1/../secret','https://example.invalid/logo','businesses/0/'+'a'*32+'.webp'):
        with pytest.raises(StorageError): storage.get(key)


def test_s3_private_storage_contract_without_network(app,monkeypatch):
    from botocore.stub import Stubber
    app.config.update(BUSINESS_IMAGE_STORAGE='s3',BUSINESS_IMAGE_S3_BUCKET='fixture-private-bucket',
        BUSINESS_IMAGE_S3_ACCESS_KEY_ID='fixture-only',BUSINESS_IMAGE_S3_SECRET_ACCESS_KEY='fixture-only')
    storage=S3Storage(); key='businesses/1/'+'a'*32+'.webp'
    with Stubber(storage.client) as stub:
        stub.add_response('put_object',{}, {'Bucket':'fixture-private-bucket','Key':key,'Body':b'fixture','ContentType':'image/webp'})
        stub.add_response('delete_object',{}, {'Bucket':'fixture-private-bucket','Key':key})
        storage.put(key,b'fixture'); storage.delete(key)
        stub.assert_no_pending_responses()


def test_new_migration_preserves_existing_business_and_round_trip(tmp_path):
    from app import create_app
    from flask_migrate import upgrade, downgrade
    from sqlalchemy import text, inspect
    application=create_app({'TESTING':True,'SECRET_KEY':'migration-only',
        'SQLALCHEMY_DATABASE_URI':f'sqlite:///{tmp_path / "notification-migration.db"}',
        'SUBSCRIPTIONS_ENABLED':False,'BILLING_PROVIDER_ENABLED':False})
    with application.app_context():
        upgrade(revision='0014_social_identity')
        db.session.execute(text("INSERT INTO user (id,full_name,email,password_hash,created_at,role,admin_enabled,admin_auth_version) VALUES (1,'Original','migration@example.invalid','original-password',CURRENT_TIMESTAMP,'user',0,0)"))
        db.session.execute(text("INSERT INTO business (id,user_id,name,created_at,subscription_plan,subscription_status,trial_started_at,trial_ends_at) VALUES (1,1,'Original shop',CURRENT_TIMESTAMP,'lifetime','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        db.session.commit()
        upgrade()
        assert db.session.execute(text('SELECT name,subscription_plan,logo_key FROM business')).one()==('Original shop','lifetime',None)
        assert db.session.execute(text('SELECT password_hash FROM user')).scalar()=='original-password'
        assert Notification.query.count()==0 and NotificationPreference.query.count()==0
        assert {'notification','notification_preference'} <= set(inspect(db.engine).get_table_names())
        columns={c['name'] for c in inspect(db.engine).get_columns('notification')}
        assert {'user_id','business_id','kind','title','body','read_at','created_at','event_key'} <= columns
        assert {index['name'] for index in inspect(db.engine).get_indexes('notification')}=={'ix_notification_owner_business_read','ix_notification_business_created'}
        db.session.remove()
        downgrade(revision='0014_social_identity')
        assert 'notification' not in inspect(db.engine).get_table_names()
        assert 'logo_key' not in {c['name'] for c in inspect(db.engine).get_columns('business')}
        assert db.session.execute(text('SELECT name,subscription_plan FROM business')).one()==('Original shop','lifetime')
        db.session.commit()
        upgrade()
        assert db.session.execute(text('SELECT COUNT(*) FROM business')).scalar()==1
        db.session.remove()


def test_import_replay_does_not_duplicate_product_notifications(app):
    from app.products.catalogue import save_batch
    client,user,shops=setup(app)
    rows=[{'name':'Imported','stock_quantity':'10','buying_price':'200','selling_price':'300'}]
    assert save_batch(shops[0].id,rows,user.id,receipt='fixture-receipt')==1
    assert save_batch(shops[0].id,rows,user.id,receipt='fixture-receipt')==0
    assert Product.query.count()==count('product_created')==1


def test_request_limit_returns_friendly_error(app,tmp_path):
    local(app,tmp_path)
    client,_,shops=setup(app)
    response=upload(client,shops[0],data=b'x'*(2*1024*1024+1))
    assert response.status_code==302
    assert 'Choose an image no larger than 1 MB.' in client.get(response.location).get_data(as_text=True)
    assert shops[0].logo_key is None


@pytest.mark.parametrize('problem',['animated','too_many_pixels'])
def test_image_resource_limits(app,tmp_path,problem):
    local(app,tmp_path)
    client,_,shops=setup(app)
    stream=io.BytesIO()
    if problem=='animated':
        Image.new('RGB',(20,20),'red').save(stream,format='WEBP',save_all=True,append_images=[Image.new('RGB',(20,20),'blue')],duration=100,loop=0)
        filename='animated.webp'
    else:
        Image.new('RGB',(4000,4000),'red').save(stream,format='PNG')
        filename='large-pixels.png'
    upload(client,shops[0],filename=filename,data=stream.getvalue())
    assert shops[0].logo_key is None


def test_readonly_selected_business_keeps_notifications_but_cannot_upload(app,tmp_path):
    from app.models import AccountBilling
    from app.businesses.service import choose_primary
    local(app,tmp_path)
    client,user,shops=setup(app)
    product(client)
    sub=RecurringSubscription.query.one(); sub.current_period_end=datetime.utcnow()-timedelta(seconds=1)
    db.session.commit()
    assert client.get('/notifications/').status_code==200
    assert client.get(f'/businesses/{shops[0].id}/profile').status_code==200
    assert upload(client,shops[0]).status_code==403
    assert count('product_created')==1


def test_unverified_owner_cannot_access_profile_or_notifications(app):
    client,user,shops=setup(app)
    user.email_verified_at=None; db.session.commit()
    for path in (f'/businesses/{shops[0].id}/profile','/notifications/'):
        response=client.get(path)
        assert response.status_code==302 and '/auth/verify-pending' in response.location
