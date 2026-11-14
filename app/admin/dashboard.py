"""Read-only platform dashboard figures."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app import db
from app.admin.subscription_reporting import collected_subscription_payments, subscription_payments_query
from app.models import AccountBilling, AdminLoginAttempt, AuditLog, Business, Payment, RecurringSubscription, User
from app.subscriptions.entitlements import CURRENT_STATUSES, PLANS, EntitlementSnapshot

LAGOS = ZoneInfo("Africa/Lagos")
PERIODS = {"today": 1, "7d": 7, "30d": 30}
ISSUE_STATUSES = ("initialized", "pending", "failed")

ACTIVITY_LABELS = {
    "STAFF_ROLE_CHANGED": "Staff role changed",
    "SALE_CREATED": "Sale recorded",
    "EXPENSE_CREATED": "Expense added",
    "RESTOCK_CREATED": "Stock receipt recorded",
    "USER_REGISTERED": "Customer signed up",
    "ADMIN_LOGIN_SUCCESS": "Admin signed in",
    "ADMIN_LOGIN_FAILED": "Admin sign-in failed",
    "ADMIN_MFA_FAILED": "Admin verification failed",
    "ADMIN_MFA_ENROLLED": "Admin set up an authenticator",
    "ADMIN_MFA_RECOVERY_USED": "Admin used a recovery code",
    "ADMIN_LOGOUT": "Admin signed out",
    "ADMIN_PASSWORD_RESET": "Admin reset their password",
    "USER_SUSPENDED": "Customer account suspended",
    "USER_REACTIVATED": "Customer account reactivated",
    "BUSINESS_SUSPENDED": "Business suspended",
    "BUSINESS_REACTIVATED": "Business reactivated",
}


def owner_query():
    return User.query.filter_by(role="user", admin_enabled=False, staff_only=False)


def business_access_counts(businesses, snapshot):
    active = sum(
        snapshot.access(snapshot.users[business.user_id], business=business).can_write
        if current_app.config["SUBSCRIPTIONS_ENABLED"]
        else not (business.suspended_at or snapshot.users[business.user_id].suspended_at)
        and business.subscription_status == "active"
        and (business.subscription_ends_at is None or business.subscription_ends_at > snapshot.now)
        for business in businesses
    )
    return {"active_businesses": active, "unpaid_users": len(businesses) - active}


def owner_alert_query(kind, now):
    owners = owner_query().filter(User.suspended_at.is_(None), User.email_verified_at.isnot(None))
    if kind == "trial-ended":
        paid = RecurringSubscription.query.filter(
            RecurringSubscription.user_id == User.id,
            RecurringSubscription.current_period_start <= now,
            RecurringSubscription.current_period_end > now,
            RecurringSubscription.status.in_(CURRENT_STATUSES),
        ).exists()
        return owners.join(AccountBilling, AccountBilling.user_id == User.id).filter(
            AccountBilling.trial_started_at.isnot(None), AccountBilling.trial_ends_at <= now,
            AccountBilling.legacy_granted_at.is_(None), ~paid,
        )
    # Apply the same winning paid period used by the entitlement evaluator.
    ranked = db.session.query(
        RecurringSubscription.user_id, RecurringSubscription.cancel_at_period_end,
        RecurringSubscription.status,
        func.row_number().over(
            partition_by=RecurringSubscription.user_id,
            order_by=(RecurringSubscription.current_period_start.desc(), RecurringSubscription.id.desc()),
        ).label("position"),
    ).filter(
        RecurringSubscription.current_period_start <= now,
        RecurringSubscription.current_period_end > now,
        RecurringSubscription.status.in_(CURRENT_STATUSES),
    ).subquery()
    return owners.join(ranked, ranked.c.user_id == User.id).filter(
        ranked.c.position == 1,
        ranked.c.cancel_at_period_end | ranked.c.status.in_(("cancel_at_period_end", "cancelled")),
    )


def dashboard(period, legacy_payments, *, now=None):
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    local_today = now.replace(tzinfo=timezone.utc).astimezone(LAGOS).date()
    first_day = local_today - timedelta(days=PERIODS[period] - 1)
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=LAGOS).astimezone(timezone.utc).replace(tzinfo=None)

    owners = owner_query().all()
    snapshot = EntitlementSnapshot(owners, now=now)
    businesses = Business.query.filter(Business.user_id.in_(owner_query().with_entities(User.id))).all()
    owners_with_businesses = {business.user_id for business in businesses if business.created_at <= now}
    plans = {f"{plan.title()} {interval}": 0 for plan, interval in PLANS}
    paying = set()
    annualized_kobo = 0
    for owner in owners:
        access = snapshot.access(owner)
        sub = access.subscription
        if access.can_write and sub and (sub.plan_code, sub.billing_interval) in PLANS:
            paying.add(owner.id)
            plans[f"{sub.plan_code.title()} {sub.billing_interval}"] += 1
            annualized_kobo += PLANS[sub.plan_code, sub.billing_interval]["amount"] * (
                1 if sub.billing_interval == "yearly" else 12
            )

    ended = RecurringSubscription.query.filter(
        RecurringSubscription.user_id.in_(owner_query().with_entities(User.id)),
        RecurringSubscription.current_period_end <= now,
        RecurringSubscription.status.in_((*CURRENT_STATUSES, "expired")),
    ).with_entities(RecurringSubscription.user_id).distinct()
    ended_owners = {uid for uid, in ended if uid not in snapshot.subscriptions and not snapshot.results[uid].can_write}
    trial_ended = {uid for uid, in owner_alert_query("trial-ended", now).with_entities(User.id)}
    ended_owners.update(trial_ended)
    plans["Trial or subscription ended"] = len(ended_owners)
    cohort = [owner for owner in owners if start <= owner.created_at <= now]
    verified = [owner for owner in cohort if owner.email_verified_at and owner.email_verified_at <= now]
    added = [owner for owner in verified if owner.id in owners_with_businesses]

    legacy = legacy_payments.with_entities(func.count(Payment.id), func.coalesce(func.sum(Payment.amount_kobo), 0)).one()
    collections = collected_subscription_payments()
    security_count = AdminLoginAttempt.query.filter(
        AdminLoginAttempt.attempted_at >= now - timedelta(minutes=15),
    ).group_by(AdminLoginAttempt.identifier).having(func.count(AdminLoginAttempt.id) >= 5).count()
    events = AuditLog.query.options(joinedload(AuditLog.business)).filter(AuditLog.created_at >= start, AuditLog.created_at <= now).order_by(
        AuditLog.created_at.desc(), AuditLog.id.desc(),
    ).limit(4).all()
    return {
        "owners": len(owners),
        "employees": User.query.filter_by(role="user", admin_enabled=False, staff_only=True).count(),
        "paying": len(paying),
        "paying_percent": round(100 * len(paying) / len(owners)) if owners else 0,
        "collected": collections["revenue_kobo"] + legacy[1],
        "payment_count": collections["count"] + legacy[0],
        "monthly": Decimal(annualized_kobo) / 12,
        "funnel": [("Signed up", len(cohort)), ("Email verified", len(verified)), ("Added a business", len(added)),
                   ("Paying", sum(owner.id in paying for owner in added))],
        "plans": plans,
        "trials_ended": len(trial_ended),
        "renewal_stopped": owner_alert_query("renewal-stopped", now).count(),
        "payment_issues": subscription_payments_query().filter(Payment.status.in_(ISSUE_STATUSES)).count(),
        "security": security_count,
        "activity": [
            (row.created_at.replace(tzinfo=timezone.utc).astimezone(LAGOS),
             ACTIVITY_LABELS.get(row.action, row.action.replace("_", " ").capitalize()),
             row.business.name if row.business else None)
            for row in events
        ],
        **business_access_counts(businesses, snapshot),
    }
