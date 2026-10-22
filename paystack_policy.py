"""Offline release-scoped staging authorization; no Flask, SQL or provider calls."""
import re
from urllib.parse import urlsplit

STAGING_PROJECT_ID = 'prj_yeCWXPd44jShVUGHAgJu9khMjOMv'
STAGING_HOST = 'stock-bridge-staging.vercel.app'


def staging_callback_origin(config):
    value = config.get('STAGING_PAYSTACK_CALLBACK_ORIGIN', '')
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (value != f'https://{STAGING_HOST}' or parsed.hostname != STAGING_HOST
                or parsed.username or parsed.password or parsed.port is not None):
            return None
    except ValueError:
        return None
    return value


def staging_preview_authorized(config, environ):
    """All identity comes from server configuration, never HTTP request data."""
    sha = environ.get('VERCEL_GIT_COMMIT_SHA', '')
    return bool(
        config.get('STAGING_PAYSTACK_TEST_ENABLED') is True
        and config.get('SUBSCRIPTIONS_ENABLED') is True
        and config.get('BILLING_PROVIDER_ENABLED') is True
        and environ.get('VERCEL_PROJECT_ID') == STAGING_PROJECT_ID
        and environ.get('VERCEL_ENV') == 'preview'
        and environ.get('VERCEL_TARGET_ENV') == 'preview'
        and environ.get('VERCEL_GIT_REPO_OWNER') == 'samuella-code'
        and environ.get('VERCEL_GIT_REPO_SLUG') == 'stock-bridge'
        and environ.get('VERCEL_GIT_COMMIT_REF') == 'release/staging-phase6'
        and isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}', sha)
        and sha == config.get('STAGING_PAYSTACK_APPROVED_SHA')
        and isinstance(config.get('PAYSTACK_PUBLIC_KEY'), str)
        and re.fullmatch(r'pk_test_[A-Za-z0-9]+', config['PAYSTACK_PUBLIC_KEY'])
        and isinstance(config.get('PAYSTACK_SECRET_KEY'), str)
        and re.fullmatch(r'sk_test_[A-Za-z0-9]+', config['PAYSTACK_SECRET_KEY'])
        and staging_callback_origin(config)
    )


def subscription_environment_allowed(config, environ):
    environment = environ.get('VERCEL_ENV')
    if environment == 'production':
        return True  # Preserve the existing production policy.
    if environment == 'preview':
        return staging_preview_authorized(config, environ)
    # A hosted deployment with incomplete metadata must not masquerade as local.
    return not environment and not any(
        key in environ for key in ('VERCEL', 'VERCEL_PROJECT_ID', 'VERCEL_TARGET_ENV'))
