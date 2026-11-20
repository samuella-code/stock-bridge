"""Read-only decisions for admin Notifications, including current subscription payments."""
from datetime import timedelta
from sqlalchemy import and_, or_, func

from app import db
from app.models import AdminLoginAttempt, AuditLog, Business, Payment, RecurringSubscription, User
from app.admin import activity_data
from app.admin.dashboard import owner_alert_query


def decisions(now):
    payments=Payment.query.filter(Payment.product.in_(('lifetime','subscription')))
    security=db.session.query(AdminLoginAttempt.identifier).filter(AdminLoginAttempt.attempted_at>=now-timedelta(minutes=15)).group_by(
        AdminLoginAttempt.identifier).having(func.count(AdminLoginAttempt.id)>=5).count()
    failed=payments.filter(Payment.status=='failed').count()
    old_pending=payments.filter(Payment.status.in_(('initialized','pending')),Payment.created_at<now-timedelta(days=2)).count()
    from app.admin.routes import payment_issues_query
    anomalies=payment_issues_query().filter(Payment.status=='success').count()
    # A success marker is not enough: linked owner/business/subscription facts must agree.
    anomaly=or_(Payment.subscription_id.is_(None), RecurringSubscription.id.is_(None),User.id.is_(None),
        User.staff_only.is_(True), User.role!='user', User.admin_enabled.is_(True),
        Business.id.is_(None),Business.user_id!=Payment.user_id,RecurringSubscription.user_id!=Payment.user_id,
        RecurringSubscription.business_id!=Payment.business_id, Payment.amount_kobo!=RecurringSubscription.amount_kobo,
        Payment.plan_code.is_(None),Payment.billing_interval.is_(None),Payment.plan_code!=RecurringSubscription.plan_code,Payment.billing_interval!=RecurringSubscription.billing_interval,
        Payment.currency!='NGN',Payment.provider!='paystack',Payment.amount_kobo<=0,Payment.paid_at.is_(None))
    anomalies+=Payment.query.outerjoin(RecurringSubscription,Payment.subscription_id==RecurringSubscription.id).outerjoin(
        User,User.id==Payment.user_id).outerjoin(Business,Business.id==Payment.business_id).filter(
        Payment.product=='subscription',Payment.status=='success',anomaly).count()
    return dict(security=security,failed=failed,old_pending=old_pending,anomalies=anomalies,
        clear=not any((security,failed,old_pending,anomalies)))


def page(now):
    result=decisions(now)
    result.update(trials=owner_alert_query('trial-ended',now).count(),
        renewal_stopped=owner_alert_query('renewal-stopped',now).count(),
        events=activity_data.serialize(activity_data.filtered().order_by(AuditLog.created_at.desc(),AuditLog.id.desc()).limit(4).all(),now))
    return result
