"""Exercise the additive migration and its own downgrade on a disposable SQLite DB."""
from datetime import datetime
from flask_migrate import upgrade, downgrade
from sqlalchemy import inspect, text
from app import create_app, db


def test_social_migration_upgrade_downgrade_preserves_existing_records(tmp_path):
    application=create_app({'TESTING':True, 'SECRET_KEY':'migration-only',
        'SQLALCHEMY_DATABASE_URI':f'sqlite:///{tmp_path / "migration.db"}',
        'SUBSCRIPTIONS_ENABLED':False, 'BILLING_PROVIDER_ENABLED':False})
    with application.app_context():
        upgrade(revision='0013_account_billing')
        db.session.execute(text('INSERT INTO "user" (id,full_name,email,password_hash,created_at,role,admin_enabled,admin_auth_version) VALUES (1,:name,:email,:password,:now,\'user\',false,0)'),
            {'name':'Existing Owner','email':'migration@example.invalid','password':'original-hash','now':datetime.utcnow()})
        db.session.commit()
        upgrade()
        assert 'social_identity' in inspect(db.engine).get_table_names()
        assert db.session.execute(text('SELECT password_hash FROM "user" WHERE id=1')).scalar()=='original-hash'
        db.session.execute(text('INSERT INTO social_identity (user_id,provider,provider_subject,created_at) VALUES (1,\'google\',\'stable-subject\',:now)'), {'now':datetime.utcnow()}); db.session.commit()
        downgrade(revision='0013_account_billing')
        assert 'social_identity' not in inspect(db.engine).get_table_names()
        assert db.session.execute(text('SELECT password_hash FROM "user" WHERE id=1')).scalar()=='original-hash'
        db.session.commit()
        upgrade()
        assert db.session.execute(text('SELECT COUNT(*) FROM "user"')).scalar()==1
        assert db.session.execute(text('SELECT COUNT(*) FROM social_identity')).scalar()==0
        db.session.remove()
