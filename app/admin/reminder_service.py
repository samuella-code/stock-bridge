"""Owner-only email reminders. No provider writes or implicit delivery enablement."""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from app import db, email_service
from app.config_safety import STAGING_PROJECT_ID
from app.models import (AccountBilling, AdminAlertPreference, AdminAuthenticator, AdminCustomerMessage,
    AdminReminderBatch, AdminReminderDelivery, AdminReminderFeedback, AdminReminderSchedule,
    Business, CustomerReminderPreference, Payment, RecurringSubscription, User, AuditLog)
from app.notifications.email_outbox import valid_recipient
from app.staff.service import lock_owner
from app.subscriptions.entitlements import CURRENT_STATUSES, EntitlementSnapshot

ORIGIN = 'https://stock-bridge-staging.vercel.app'
LAGOS = ZoneInfo('Africa/Lagos')
TIMINGS = {
    'trial': [('day5','Day 5 of the trial'), ('ends','The day the trial ends'), ('after3','3 days after the trial ends')],
    'winback': [('before7','7 days before paid access ends'), ('ends','The day paid access ends'), ('after3','3 days after paid access ends')],
}
MESSAGES = {
    'trial': 'Hi {first_name}, your StockBridge trial for {business_name} has ended. Your records are safe and you can view them any time. Choose a plan to keep recording sales: {plan_link}.',
    'winback': 'Hi {first_name}, your StockBridge plan for {business_name} ends on {end_date} because renewal is off. Your records stay safe, but you will not be able to add new ones after that date. Turn renewal back on here: {billing_link}.',
}
REASONS = ('Too expensive', 'Not using it', 'Missing a feature', 'Other')
PLACEHOLDERS = ('first_name','business_name','plan_link','end_date','billing_link')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def email_gate(config=None, environ=None, *, automatic=False):
    config = current_app.config if config is None else config
    environ = os.environ if environ is None else environ
    if config.get('ADMIN_REMINDERS_EMAIL_ENABLED') != 'true':return 'disabled', set()
    if automatic and config.get('ADMIN_REMINDERS_AUTOMATION_ENABLED') != 'true':return 'disabled', set()
    expected = dict(VERCEL='1', VERCEL_PROJECT_ID=STAGING_PROJECT_ID, VERCEL_ENV='preview',
        VERCEL_TARGET_ENV='preview', VERCEL_GIT_REPO_OWNER='samuella-code', VERCEL_GIT_REPO_SLUG='stock-bridge',
        VERCEL_GIT_COMMIT_REF='release/staging-phase6')
    sha = environ.get('VERCEL_GIT_COMMIT_SHA','')
    allowed = config.get('ADMIN_REMINDERS_ALLOWED_RECIPIENTS','').split(',')
    if (any(environ.get(k)!=v for k,v in expected.items()) or not re.fullmatch('[a-f0-9]{40}',sha)
        or sha!=config.get('ADMIN_REMINDERS_APPROVED_SHA') or config.get('ADMIN_REMINDERS_ORIGIN')!=ORIGIN
        or not allowed or any(not valid_recipient(a) or a!=a.lower() for a in allowed)
        or not config.get('SMTP_HOST') or not config.get('SMTP_FROM_EMAIL')):
        return 'configuration_required', set()
    return 'ready', set(allowed)


def admin_ready(user, *, auth_version=None, mfa_verified=False):
    from app.admin.routes import _admin_mfa_required
    return bool(user and not user.suspended_at and user.email_verified_at
        and (user.role=='admin' or user.admin_enabled) and not user.staff_only
        and auth_version is not None and auth_version==user.admin_auth_version
        and (not _admin_mfa_required(user) or (mfa_verified and db.session.get(AdminAuthenticator,user.id)
            and db.session.get(AdminAuthenticator,user.id).enabled_at)))


def local_day(value):
    return value.replace(tzinfo=timezone.utc).astimezone(LAGOS).date()


def candidate(user, kind, now, *, override=False):
    """Return current facts, or a stable exclusion reason. Never create a trial."""
    if kind not in TIMINGS:raise ValueError('Unknown reminder type.')
    if not user or user.role!='user' or user.admin_enabled or user.staff_only:return None,'not_owner'
    if user.suspended_at or not user.email_verified_at or not valid_recipient(user.email):return None,'unavailable'
    preference=db.session.get(CustomerReminderPreference,user.id)
    if preference and preference.stopped_at:return None,'stopped'
    account=db.session.get(AccountBilling,user.id)
    # Existing valid legacy access is never described as ending.
    if account and account.legacy_granted_at:return None,'existing_access'
    subscriptions=RecurringSubscription.query.filter(RecurringSubscription.user_id==user.id,
        RecurringSubscription.created_at<=now).order_by(RecurringSubscription.current_period_end.desc(),
        RecurringSubscription.current_period_start.desc(),RecurringSubscription.id.desc()).all()
    access=EntitlementSnapshot([user],now=now).access(user)
    if kind=='trial':
        if not account or not account.trial_started_at or not account.trial_ends_at:return None,'no_trial'
        if any(s.current_period_start or s.status not in ('failed','abandoned') for s in subscriptions):return None,'plan_chosen'
        if now<account.trial_started_at or not -7<= (local_day(now)-local_day(account.trial_ends_at)).days<=30:return None,'outside_window'
        ends=account.trial_ends_at
        shop=Business.query.filter_by(user_id=user.id,suspended_at=None).order_by(Business.id).first()
        if account.primary_business_id:
            primary=db.session.get(Business,account.primary_business_id)
            if primary and primary.user_id==user.id and not primary.suspended_at:shop=primary
        subscription=None
    else:
        subscription=access.subscription or next((s for s in subscriptions if s.current_period_end
            and s.current_period_start and s.current_period_start<=now and s.status in (*CURRENT_STATUSES,'expired')),None)
        if not subscription:return None,'no_paid_period'
        ends=subscription.current_period_end
        stopped=subscription.cancel_at_period_end or subscription.cancelled_at or subscription.status in ('cancel_at_period_end','cancelled')
        if ends>now and not stopped:return None,'renewal_on'
        if not -7<=(local_day(now)-local_day(ends)).days<=30:return None,'outside_window'
        # A subsequent paid period, even if scheduled in the future, ends this campaign.
        if any(s.id!=subscription.id and s.current_period_end and s.current_period_end>ends
            and s.current_period_start and s.status in (*CURRENT_STATUSES,'scheduled') for s in subscriptions):return None,'paid_again'
        paid=Payment.query.filter(Payment.user_id==user.id,Payment.status=='success',Payment.product=='subscription',
            Payment.paid_at.isnot(None),Payment.paid_at>(subscription.cancelled_at or subscription.current_period_start)).first()
        if paid and (subscription.cancelled_at or paid.subscription_id!=subscription.id):return None,'paid_again'
        shop=db.session.get(Business,subscription.business_id)
        if not shop or shop.user_id!=user.id or shop.suspended_at:return None,'unavailable'
    if not shop:return None,'no_business'
    if not override:
        cutoff=now-timedelta(days=7)
        if ((preference and preference.last_contact_at and preference.last_contact_at>cutoff)
            or AdminCustomerMessage.query.filter(AdminCustomerMessage.user_id==user.id,AdminCustomerMessage.created_at>cutoff).first()):
            return None,'recent_contact'
    return dict(user=user, business=shop, subscription=subscription, end=ends,
        trial_start=account.trial_started_at if account else None),None


def audience(kind, now, *, override=False, user_id=None):
    query=User.query.filter_by(role='user',admin_enabled=False)
    if user_id is not None:query=query.filter_by(id=user_id)
    rows=[];excluded={};last=0
    while True:
        users=query.filter(User.id>last).order_by(User.id).limit(100).all()
        if not users:break
        for user in users:
            facts,reason=candidate(user,kind,now,override=override)
            if reason:excluded[reason]=excluded.get(reason,0)+1
            else:rows.append(facts)
        last=users[-1].id
    return rows,excluded


def render_message(template, facts, now):
    if not isinstance(template,str) or not template.strip() or len(template)>5000:raise ValueError('Use a message of at most 5,000 characters.')
    unknown=set(re.findall(r'\{([^{}]+)\}',template))-set(PLACEHOLDERS)
    if unknown:raise ValueError('Use only the listed message placeholders.')
    values=dict(first_name=facts['user'].full_name.split()[0] if facts['user'].full_name.split() else 'there',
        business_name=facts['business'].name, plan_link=ORIGIN+'/plans/', billing_link=ORIGIN+'/plans/',
        end_date=facts['end'].replace(tzinfo=timezone.utc).astimezone(LAGOS).strftime('%d/%m/%Y'))
    for key,value in values.items():template=template.replace('{'+key+'}',value)
    return template


def automatic_message(kind, facts, now):
    if facts['end']>now:
        if kind=='trial':return 'Hi {first_name}, your StockBridge trial for {business_name} ends on {end_date}. Your records stay safe. Choose a plan to keep recording sales: {plan_link}.'
        return MESSAGES[kind]
    if kind=='winback':return 'Hi {first_name}, your StockBridge paid access for {business_name} ended on {end_date}. Your records stay safe and remain available to view. Choose a plan to record new activity: {billing_link}.'
    return MESSAGES[kind]


def token(user, purpose, delivery_id=None):
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],salt='stockbridge-reminders-v1').dumps(
        dict(user=user.id,email=digest(user.email.lower()),purpose=purpose,delivery=delivery_id))


def token_user(value, purpose):
    try:
        data=URLSafeTimedSerializer(current_app.config['SECRET_KEY'],salt='stockbridge-reminders-v1').loads(value,max_age=365*86400)
        user=db.session.get(User,data['user'])
        if data.get('purpose')!=purpose or not user or data.get('email')!=digest(user.email.lower()):raise ValueError
        return user,data
    except (BadSignature,KeyError,TypeError,ValueError):raise ValueError('This link is invalid or has expired.')


def audit(action, actor_id, user_id, description, delivery_id=None):
    db.session.add(AuditLog(actor_id=actor_id,target_type='reminder_delivery' if delivery_id else 'user',
        target_id=delivery_id or user_id,action=action,description=description[:300]))


def batch_result(batch):
    rows=AdminReminderDelivery.query.filter_by(batch_id=batch.id).order_by(AdminReminderDelivery.id).all()
    return [dict(id=r.id,owner_id=r.user_id,status=r.email_status,kind=r.kind) for r in rows]


def claim(actor, key, kind, ids, template, *, override_reason='', feedback=False, is_test=False,
          auth_version=None, mfa_verified=False, now=None, automatic=False, schedule_key=None):
    now=now or datetime.utcnow()
    if not admin_ready(actor,auth_version=auth_version,mfa_verified=mfa_verified):raise ValueError('Verified admin access is required.')
    if not re.fullmatch('[A-Za-z0-9_-]{16,100}',key or ''):raise ValueError('Refresh the send panel before trying again.')
    if kind not in TIMINGS or not isinstance(template,str) or not template.strip() or len(template)>5000:raise ValueError('Check the reminder message.')
    if not ids or len(ids)>25 or any(type(i) is not int for i in ids) or len(set(ids))!=len(ids):raise ValueError('Choose 1 to 25 owners.')
    if not isinstance(override_reason,str) or len(override_reason)>300 or (override_reason and len(override_reason.strip())<10):raise ValueError('Explain a frequency override in at least 10 characters.')
    if feedback and kind!='winback':raise ValueError('The optional question belongs to win-back messages.')
    if automatic and schedule_key not in {kind+':'+timing for timing,label in TIMINGS[kind]}:raise ValueError('Choose a valid automatic timing.')
    state,allowed=email_gate(automatic=automatic)
    if state!='ready':raise ValueError('Email delivery needs separate approval and configuration.')
    fingerprint=digest(json.dumps([kind,sorted(ids),template,override_reason,feedback,is_test,automatic,schedule_key],sort_keys=True))
    lock_owner(actor.id)
    actor=db.session.get(User,actor.id)
    if not admin_ready(actor,auth_version=auth_version,mfa_verified=mfa_verified):raise ValueError('Verified admin access is required.')
    existing=AdminReminderBatch.query.filter_by(actor_id=actor.id,request_key=key).first()
    if existing:
        if existing.content_digest!=fingerprint:raise ValueError('This request was already used for different content.')
        return existing,False
    # Stable ordering and the same write lock used by existing customer messaging.
    facts=[]
    for user_id in sorted(ids):
        if user_id!=actor.id:lock_owner(user_id)
        user=db.session.get(User,user_id)
        if is_test:
            if user_id!=actor.id:raise ValueError('Tests can only be sent to your verified admin email.')
            shop=Business(name='Example business',user_id=user_id)
            fact=dict(user=user,business=shop,end=now,subscription=None,trial_start=None)
        else:
            fact,reason=candidate(user,kind,now,override=bool(override_reason))
            if reason:raise ValueError('An owner is no longer eligible. Refresh the audience before sending.')
        if user.email.lower() not in allowed:raise ValueError('One selected email is outside the approved recipient list.')
        facts.append(fact)
    if is_test and AdminReminderBatch.query.filter_by(actor_id=actor.id).filter(AdminReminderBatch.created_at>now-timedelta(minutes=1)).first():
        raise ValueError('Wait one minute before sending another test.')
    row=AdminReminderBatch(actor_id=actor.id,request_key=key,content_digest=fingerprint,created_at=now)
    db.session.add(row);db.session.flush()
    for fact in facts:
        owner=fact['user']
        body=render_message(template,fact,now)
        delivery=AdminReminderDelivery(batch_id=row.id,user_id=owner.id,kind=kind,is_test=is_test,is_automatic=automatic,schedule_key=schedule_key,
            recipient_digest=digest(owner.email.lower()),body=body,email_status='sending',override_reason=override_reason.strip(),
            include_feedback=feedback,period_end=fact['end'],created_at=now)
        db.session.add(delivery)
        if not is_test:
            preference=db.session.get(CustomerReminderPreference,owner.id)
            if not preference:
                preference=CustomerReminderPreference(user_id=owner.id);db.session.add(preference)
            preference.last_contact_at=now
        audit('ADMIN_REMINDER_CLAIMED',actor.id,owner.id,kind+' email prepared for '+owner.email+('. Frequency override: '+override_reason if override_reason else '.'))
    db.session.flush()
    identifier=row.id
    db.session.commit()  # Durable dedupe, cap and audit before SMTP; interrupted claims never retry.
    return db.session.get(AdminReminderBatch,identifier),True


def deliver(batch_id, *, auth_version=None, mfa_verified=False, now=None):
    now=now or datetime.utcnow()
    batch=db.session.get(AdminReminderBatch,batch_id)
    if batch is None:return []
    actor_id=batch.actor_id
    identifiers=[r.id for r in AdminReminderDelivery.query.filter_by(batch_id=batch_id).order_by(AdminReminderDelivery.id)]
    for identifier in identifiers:
        # Consume the transport claim durably before any SMTP call. Unknown is
        # terminal: a crash cannot cause either a retry or a duplicate send.
        won=db.session.execute(update(AdminReminderDelivery).where(
            AdminReminderDelivery.id==identifier,AdminReminderDelivery.email_status=='sending'
        ).values(email_status='unknown')).rowcount
        db.session.commit()
        if not won:continue
        row=db.session.get(AdminReminderDelivery,identifier)
        lock_owner(actor_id)
        if row.user_id!=actor_id:lock_owner(row.user_id)
        actor=db.session.get(User,actor_id);owner=db.session.get(User,row.user_id)
        state,allowed=email_gate(automatic=row.is_automatic)
        ok=admin_ready(actor,auth_version=auth_version,mfa_verified=mfa_verified)
        if row.kind.startswith('alert_'):
            preference=db.session.get(AdminAlertPreference,actor_id)
            flag=row.kind.removeprefix('alert_')
            stopped=db.session.get(CustomerReminderPreference,actor_id)
            ok=ok and preference and getattr(preference,flag,False) and preference.auth_version==actor.admin_auth_version
            ok=ok and not (stopped and stopped.stopped_at)
        elif not row.is_test:
            facts,reason=candidate(owner,row.kind,now,override=True)
            ok=ok and not reason and facts['end']==row.period_end
            if row.is_automatic:
                schedule=db.session.get(AdminReminderSchedule,row.schedule_key)
                ok=ok and schedule and schedule.enabled and schedule.actor_id==actor_id and schedule.auth_version==auth_version
        else:ok=ok and owner and owner.id==actor_id
        ok=ok and state=='ready' and owner and digest(owner.email.lower())==row.recipient_digest and owner.email.lower() in allowed
        if not ok:
            status='suppressed'
        else:
            body=row.body+'\n\nStop reminders: '+ORIGIN+'/admin/reminders/stop/'+token(owner,'stop')
            if row.include_feedback:
                body+='\nOptional: why did you stop? '+ORIGIN+'/admin/reminders/feedback/'+token(owner,'feedback',row.id)
            row.body=body
            # Keep the same owner lock through transport and recording. Account
            # and opt-out changes committed before this lock win the send race.
            db.session.flush()
            try:
                subject='StockBridge '+('admin alert' if row.kind.startswith('alert_') else 'trial reminder' if row.kind=='trial' else 'plan reminder')
                status='sent' if email_service._send_email(('Test: ' if row.is_test else '')+subject,owner.email,body) else 'failed'
            except Exception:status='unknown'
        row.email_status=status;row.completed_at=now
        recipient=owner.email if owner else 'unavailable account'
        audit('ADMIN_REMINDER_RESULT',actor_id,row.user_id,row.kind+' email '+status+' for '+recipient+'.',delivery_id=row.id)
        db.session.commit()
    return batch_result(db.session.get(AdminReminderBatch,batch_id))


def due(kind,timing,facts,now):
    day=local_day(now);end=local_day(facts['end'])
    if kind=='trial' and timing=='day5':return day==local_day(facts['trial_start'])+timedelta(days=5)
    offset={'before7':-7,'ends':0,'after3':3}.get(timing)
    return offset is not None and day==end+timedelta(days=offset)


def dispatch_one(now=None):
    now=now or datetime.utcnow()
    if email_gate(automatic=True)[0]!='ready':return 'disabled'
    # Schedules are explicit, attributed admin consent, never implicit global switches.
    for schedule in AdminReminderSchedule.query.filter_by(enabled=True).order_by(AdminReminderSchedule.key):
        actor=db.session.get(User,schedule.actor_id)
        if not admin_ready(actor,auth_version=schedule.auth_version,mfa_verified=schedule.mfa_verified):continue
        if schedule.key not in {kind+':'+timing for kind,items in TIMINGS.items() for timing,label in items}:continue
        kind,timing=schedule.key.split(':')
        rows,_=audience(kind,now)
        for facts in rows:
            if not due(kind,timing,facts,now):continue
            if facts['user'].email.lower() not in email_gate()[1]:continue
            key='auto-'+digest(schedule.key+':'+str(facts['user'].id)+':'+facts['end'].isoformat())
            existing=AdminReminderBatch.query.filter_by(actor_id=actor.id,request_key=key).first()
            if existing:continue
            try:
                row,new=claim(actor,key,kind,[facts['user'].id],automatic_message(kind,facts,now),
                    auth_version=schedule.auth_version,mfa_verified=schedule.mfa_verified,now=now,automatic=True,schedule_key=schedule.key)
                if new:return deliver(row.id,auth_version=schedule.auth_version,mfa_verified=schedule.mfa_verified,now=now)[0]['status']
            except (ValueError,IntegrityError):
                # An account or competing worker may win after the read-only
                # projection. Roll back; do not broaden eligibility or retry SMTP.
                db.session.rollback()
                return 'suppressed'

    return 'idle'


def feedback_summary(start,end):
    counts=dict.fromkeys(REASONS,0)
    for reason,count in db.session.query(AdminReminderFeedback.reason,db.func.count()).filter(
        AdminReminderFeedback.created_at>=start,AdminReminderFeedback.created_at<end).group_by(AdminReminderFeedback.reason):
        if reason in counts:counts[reason]=count
    return counts


def dispatch_alert_one(now=None):
    """Opt-in admin alerts; one durable attempt per admin/type/WAT day."""
    now=now or datetime.utcnow()
    state,allowed=email_gate(automatic=True)
    if state!='ready':return state
    from app.admin.notification_data import decisions
    notices=decisions(now)
    for preference in AdminAlertPreference.query.order_by(AdminAlertPreference.user_id):
        user=db.session.get(User,preference.user_id)
        if not admin_ready(user,auth_version=preference.auth_version,mfa_verified=preference.mfa_verified):continue
        if user.email.lower() not in allowed:continue
        stopped=db.session.get(CustomerReminderPreference,user.id)
        if stopped and stopped.stopped_at:continue
        flags=[('failed_payments',notices['failed']>0),('security_alerts',notices['security']>0),
            ('daily_summary',now.replace(tzinfo=timezone.utc).astimezone(LAGOS).hour==8)]
        for flag,needed in flags:
            if not needed or not getattr(preference,flag):continue
            key='alert-'+flag+'-'+local_day(now).isoformat()
            lock_owner(user.id)
            if AdminReminderBatch.query.filter_by(actor_id=user.id,request_key=key).first():continue
            body=('StockBridge admin summary: '+str(notices['failed'])+' failed payments; '
                +str(notices['security'])+' repeated sign-in sources; '+str(notices['old_pending'])
                +' old pending payments; '+str(notices['anomalies'])+' payment anomalies. Review: '+ORIGIN+'/admin/notifications')
            batch=AdminReminderBatch(actor_id=user.id,request_key=key,content_digest=digest(body),created_at=now)
            db.session.add(batch);db.session.flush()
            row=AdminReminderDelivery(batch_id=batch.id,user_id=user.id,kind='alert_'+flag,
                recipient_digest=digest(user.email.lower()),body=body,email_status='sending',is_automatic=True,created_at=now)
            db.session.add(row);db.session.flush();identifier=batch.id
            audit('ADMIN_REMINDER_CLAIMED',user.id,user.id,'Admin '+flag+' email prepared.')
            auth_version,mfa=preference.auth_version,preference.mfa_verified
            db.session.commit()
            return deliver(identifier,auth_version=auth_version,mfa_verified=mfa,now=now)[0]['status']
    return 'idle'
