"""Read-only platform report projections; billing and entitlement rules stay shared."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, literal_column, union_all

from app import db
from app.admin.dashboard import LAGOS, owner_query
from app.admin.payment_data import money
from app.admin.subscription_reporting import (current_subscription_accounts,
    verified_subscription_payments_query)
from app.models import Payment, User

PERIODS = {'7d': 7, '30d': 30, '90d': 90, 'today': 1}


def parse_day(value):
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except ValueError:
        return date.fromisoformat(value)  # Existing ISO query links remain valid.


def report_range(args, now):
    period = args.get('period', '30d')
    local_today = now.replace(tzinfo=timezone.utc).astimezone(LAGOS).date()
    if period == 'custom':
        try:
            first, last = parse_day(args.get('start', '')), parse_day(args.get('end', ''))
            if first > last or (last-first).days > 366:
                raise ValueError
            finish = datetime.combine(last+timedelta(days=1), datetime.min.time(), tzinfo=LAGOS)
        except (ValueError, OverflowError):
            raise ValueError('Choose valid dates in day/month/year order, no more than one year apart.')
    elif period in PERIODS:
        last = local_today
        first = last-timedelta(days=PERIODS[period]-1)
        finish = now.replace(tzinfo=timezone.utc)
    else:
        raise ValueError('Choose a valid report period.')
    start = datetime.combine(first, datetime.min.time(), tzinfo=LAGOS).astimezone(timezone.utc).replace(tzinfo=None)
    end = finish.astimezone(timezone.utc).replace(tzinfo=None)
    return dict(period=period, start=start, end=end, first=first, last=last)


def daily_collections_query(start, end, legacy_query, *, dialect=None):
    queries = [verified_subscription_payments_query(), legacy_query]
    # Owners are customers; employee and administrator receipts are not platform customer collections.
    entries = []
    for query in queries:
        query = query.filter(User.staff_only.is_(False), Payment.paid_at >= start, Payment.paid_at < end)
        entries.append(query.with_entities(Payment.paid_at.label('paid_at'), Payment.amount_kobo.label('amount')).statement)
    records = union_all(*entries).subquery()
    if (dialect or db.engine.dialect.name) == 'postgresql':
        day = func.date(records.c.paid_at+literal_column("INTERVAL '1 hour'"))
    else:
        day = func.date(records.c.paid_at, '+1 hour')
    return db.session.query(day.label('day'), func.sum(records.c.amount).label('amount'), func.count().label('count')).group_by(day).order_by(day)


def naira(kobo):
    return format(Decimal(kobo)/100, '.2f')


def report(window, legacy_query, now):
    owners = owner_query().all()
    buckets = {}
    payment_count = 0
    for day, amount, count in daily_collections_query(window['start'], window['end'], legacy_query).all():
        payment_count += count
        local_day = day if isinstance(day, date) else date.fromisoformat(day)
        monday = local_day-timedelta(days=local_day.weekday())
        buckets[monday] = buckets.get(monday, 0)+amount
    weeks = []
    monday = window['first']-timedelta(days=window['first'].weekday())
    while monday <= window['last']:
        first, last = max(monday, window['first']), min(monday+timedelta(days=6), window['last'])
        weeks.append(dict(start=first.strftime('%d/%m/%Y'), end=last.strftime('%d/%m/%Y'),
                          monday=monday.strftime('%d/%m/%Y'), amount=buckets.get(monday, 0)))
        monday += timedelta(days=7)
    peak = max((week['amount'] for week in weeks), default=0)
    for week in weeks:
        week['percent'] = round(100*week['amount']/peak, 2) if peak else 0
    paying = current_subscription_accounts(owners, now=now)
    return dict(new_customers=sum(window['start'] <= u.created_at < window['end'] for u in owners),
        verified=sum(bool(u.email_verified_at and window['start'] <= u.email_verified_at < window['end']) for u in owners),
        payment_count=payment_count, suspended=sum(u.suspended_at is not None for u in owners),
        by_plan={plan:sum(intervals.values()) for plan,intervals in paying['by_plan'].items()},
        collected=sum(w['amount'] for w in weeks), active=sum(u.suspended_at is None for u in owners),
        paying=paying['total'], weeks=weeks,
        generated=now.replace(tzinfo=timezone.utc).astimezone(LAGOS).strftime('%d/%m/%Y · %H:%M WAT'))
