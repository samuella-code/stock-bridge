"""Explicit worker; never scheduled by application startup or deployment."""
from datetime import datetime, timedelta
import click
from flask import current_app
from sqlalchemy import update
from app import db
from app.models import AccountBilling, BillingEvent, RecurringSubscription, StaffMembership, User
from app.subscriptions.billing import lock_account, notice
from app.subscriptions.entitlements import effective_access
from app.email_service import _transactional


def _active_staff(user):
    return StaffMembership.query.filter_by(user_id=user.id, state='active').first()


def queue_due(now=None):
    now = now or datetime.utcnow()
    for account in AccountBilling.query.filter(AccountBilling.trial_ends_at.isnot(None)).yield_per(100):
        user=db.session.get(User,account.user_id)
        if not user or user.staff_only or user.role != 'user' or user.admin_enabled or _active_staff(user):
            continue
        lock_account(user.id)
        if effective_access(user,now=now).kind == 'trial' and now >= account.trial_ends_at-timedelta(days=2):
            notice(user.id,f'trial-ending:{user.id}','trial_ending','Your trial ends soon. Choose Basic, Plus or Pro to keep recording business activity. No automatic charge follows your trial.')
        elif now >= account.trial_ends_at and effective_access(user,now=now).kind == 'restricted':
            notice(user.id,f'trial-expired:{user.id}','trial_expired','Your free trial has ended. Your business records remain available to view. Choose a plan to record new activity.')
    for sub in RecurringSubscription.query.filter(RecurringSubscription.current_period_end.isnot(None)).yield_per(100):
        renewal_at = sub.next_renewal_at or sub.current_period_end
        if (sub.status != 'active' or sub.cancel_at_period_end or not renewal_at
                or now < renewal_at-timedelta(days=3) or now >= renewal_at):
            continue
        user=db.session.get(User,sub.user_id)
        if not user or user.staff_only or user.role != 'user' or user.admin_enabled or not user.email_verified_at or _active_staff(user):
            continue
        lock_account(user.id)
        amount=f"₦{sub.amount_kobo / 100:,.2f}"
        notice(user.id,f'renewal-reminder:{sub.id}:{renewal_at.isoformat()}','renewal_reminder',
            f"Your {sub.plan_code.title()} {sub.billing_interval} subscription is scheduled to renew on {renewal_at:%d %b %Y}. The expected renewal amount is {amount}. Review your billing details if anything has changed.",
            sub.id)
    for sub in RecurringSubscription.query.filter(RecurringSubscription.current_period_end<=now).yield_per(100):
        user=db.session.get(User,sub.user_id)
        if not user or user.staff_only or user.role != 'user' or user.admin_enabled or _active_staff(user):
            continue
        lock_account(user.id)
        if effective_access(user,now=now).subscription:
            continue
        fallback='Your existing account access continues.' if effective_access(user,now=now).legacy else 'Your records remain available to view.'
        notice(user.id,f'period-ended:{sub.id}:{sub.current_period_end.isoformat()}','subscription_expired','This paid subscription period has ended. '+fallback,sub.id)
    db.session.commit()


def deliver(limit=100):
    ids=[row[0] for row in db.session.query(BillingEvent.id).filter(BillingEvent.kind!='provider_event',BillingEvent.email_claimed_at.is_(None)).order_by(BillingEvent.id).limit(limit)]
    sent=0
    for event_id in ids:
        now=datetime.utcnow()
        claimed=db.session.execute(update(BillingEvent).where(BillingEvent.id==event_id,BillingEvent.email_claimed_at.is_(None)).values(email_claimed_at=now))
        db.session.commit()
        if not claimed.rowcount:continue
        event=db.session.get(BillingEvent,event_id);user=db.session.get(User,event.user_id)
        if (not user or user.role!='user' or user.admin_enabled or user.staff_only or _active_staff(user)):
            continue
        # Durable claim precedes SMTP. Unknown delivery failures require operator review; no blind duplicate send.
        titles = {
            'trial_ending': 'Your StockBridge trial ends soon',
            'trial_expired': 'Your free trial has ended',
            'renewal_reminder': 'Your subscription renews soon',
            'renewal_failed': "We couldn't renew your subscription",
            'renewal_succeeded': 'Your subscription payment was successful',
            'subscription_expired': 'Your subscription period has ended',
        }
        title=titles.get(event.kind,'An update about your subscription')
        try:
            delivered=_transactional('billing',title+' | StockBridge',user.email,user.full_name,event.message,
                link='https://'+current_app.config['CUSTOMER_HOST']+current_app.url_map.bind(current_app.config['CUSTOMER_HOST']).build('subscriptions.index'),
                message=event.message,title=title,preheader=event.message[:120])
        except Exception:
            current_app.logger.warning("Billing email delivery needs review for event %s.", event.id)
            delivered=False
        if delivered:
            event.email_sent_at=datetime.utcnow();db.session.commit();sent+=1
    return sent


def register_commands(app):
    @app.cli.command('billing-preflight')
    def preflight_command():
        from app.subscriptions.legacy import preflight
        _,report=preflight(db.session.connection())
        for key,value in report.items():click.echo(f'{key}: {value}')
        db.session.rollback()
        if report['multiple_lifetime_businesses'] or report['active_lifetime_without_payment_evidence']:
            raise click.ClickException('STOP: lifetime evidence needs review; no changes made.')
        click.echo('READ ONLY: no records changed.')

    @app.cli.command('billing-notifications')
    def notifications_command():
        if not app.config.get('SUBSCRIPTIONS_ENABLED'):
            raise click.ClickException('Subscription rollout is disabled.')
        queue_due()
        click.echo(f'Billing notifications sent: {deliver()}')
