"""Fail-closed, bounded staging test cohorts; no transport or database access."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import os
import re

from flask import current_app
from sqlalchemy import false, func
from app.models import EmailOutbox
from app.config_safety import PRODUCTION_PROJECT_ID

PROJECT = 'prj_yeCWXPd44jShVUGHAgJu9khMjOMv'
ORIGIN = 'https://stock-bridge-staging.vercel.app'
COHORT_KEY = '_staging_test_run'
METADATA = {
    'VERCEL_PROJECT_ID': PROJECT,
    'VERCEL_ENV': 'preview',
    'VERCEL_TARGET_ENV': 'preview',
    'VERCEL_GIT_REPO_OWNER': 'samuella-code',
    'VERCEL_GIT_REPO_SLUG': 'stock-bridge',
    'VERCEL_GIT_COMMIT_REF': 'release/staging-phase6',
}


@dataclass(frozen=True)
class Policy:
    mode: str
    run_id: str = ''
    recipients: frozenset = frozenset()
    started_at: datetime | None = None

    def allows(self, recipient):
        return isinstance(recipient, str) and recipient.lower() in self.recipients

    def conditions(self):
        if self.mode == 'blocked':
            return (false(),)
        if self.mode == 'production':
            return ()
        return (
            EmailOutbox.payload[COHORT_KEY].as_string() == self.run_id,
            EmailOutbox.created_at >= self.started_at,
            EmailOutbox.created_at < self.started_at + timedelta(hours=1),
            func.lower(EmailOutbox.recipient_email).in_(sorted(self.recipients)),
        )


def policy(now=None):
    config = current_app.config
    opted_in = config.get('STAGING_INVENTORY_EMAIL_TEST_ENABLED', False)
    project = os.getenv('VERCEL_PROJECT_ID')
    raw_origin = config.get('INVENTORY_EMAIL_BASE_URL', '')
    origin = raw_origin.rstrip('/') if isinstance(raw_origin, str) else ''
    # Unrestricted delivery requires positive production identity. Absence of
    # staging signals (including a missing/wrong origin) is never authorization.
    production = (
        project == PRODUCTION_PROJECT_ID
        and os.getenv('VERCEL_ENV') == 'production'
        and os.getenv('VERCEL_TARGET_ENV') == 'production'
        and not opted_in and origin != ORIGIN
        and all(os.getenv(key) in (None, expected) for key, expected in (
            ('VERCEL_GIT_REPO_OWNER', 'samuella-code'),
            ('VERCEL_GIT_REPO_SLUG', 'stock-bridge'),
            ('VERCEL_GIT_PROVIDER', 'github'),
            ('VERCEL', '1'),
        ))
    )
    if production:
        return Policy('production')
    if (opted_in is not True or any(os.getenv(k) != v for k, v in METADATA.items())
            or origin != ORIGIN):
        return Policy('blocked')
    run_id = config.get('STAGING_INVENTORY_EMAIL_TEST_RUN_ID', '')
    recipients = config.get('STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS', '')
    started = config.get('STAGING_INVENTORY_EMAIL_TEST_STARTED_AT', '')
    if (not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id)
            or not isinstance(recipients, str) or not isinstance(started, str)
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', started)):
        return Policy('blocked')
    entries = [entry.strip().lower() for entry in recipients.split(',')]
    if (not 1 <= len(entries) <= 20 or len(entries) != len(set(entries))
            or any(len(e) > 180 or not re.fullmatch(r"[a-z0-9][a-z0-9.!#$%&'*+/=?^_`{|}~-]*@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", e) for e in entries)):
        return Policy('blocked')
    try:
        start = datetime.strptime(started, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        return Policy('blocked')
    now = now or datetime.utcnow()
    if not start <= now < start + timedelta(hours=1):
        return Policy('blocked')
    return Policy('staging', run_id, frozenset(entries), start)


def stamp_test_job(row):
    """Only jobs newly queued during an approved session receive a cohort tag."""
    selected = policy()
    if selected.mode == 'staging' and selected.allows(row.recipient_email):
        row.payload = {**row.payload, COHORT_KEY: selected.run_id}
