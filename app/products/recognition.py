"""Public barcode reference data only; never store business data here."""
from collections import OrderedDict, deque
from copy import deepcopy
import json
import re
from threading import Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, HTTPRedirectHandler, build_opener
from flask import current_app

MAX_RESPONSE = 64 * 1024
LICENSE_URL = 'https://opendatacommons.org/licenses/odbl/1-0/'
HOSTS = {'world.openfoodfacts.org', 'world.openbeautyfacts.org',
         'world.openpetfoodfacts.org', 'world.openproductsfacts.org'}
SOURCES = {'food': ('Open Food Facts', 'world.openfoodfacts.org'),
           'beauty': ('Open Beauty Facts', 'world.openbeautyfacts.org'),
           'petfood': ('Open Pet Food Facts', 'world.openpetfoodfacts.org'),
           'product': ('Open Products Facts', 'world.openproductsfacts.org')}

def is_gtin(code):
    if not isinstance(code, str) or not re.fullmatch(r'(?:[0-9]{8}|[0-9]{12,14})', code):
        return False
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(code[:-1])))
    return (10 - total % 10) % 10 == int(code[-1])

def clean(value, limit):
    if not isinstance(value, str):
        return ''
    return ' '.join(''.join(c for c in value if ord(c) >= 32).split())[:limit]

class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if target.scheme != 'https' or target.hostname not in HOSTS or target.username or target.port not in (None, 443):
            raise ValueError('Unexpected provider redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

class OpenFactsProvider:
    key = 'openfacts'

    def lookup(self, barcode):
        # Universal read endpoint routes food, beauty, pet food and other products.
        url = f'https://world.openfoodfacts.org/api/v3/product/{barcode}?product_type=all&fields=code,product_name,product_name_en,brands,quantity,categories_tags,product_type'
        request = Request(url, headers={'Accept': 'application/json',
            'User-Agent': 'StockBridge/1.0 (https://github.com/samuella-code/stock-bridge)'})
        try:
            with build_opener(SafeRedirect()).open(request, timeout=3) as response:
                if response.status != 200:
                    return {'status': 'unavailable'}
                raw = response.read(MAX_RESPONSE + 1)
                host = urlparse(response.url).hostname
            if len(raw) > MAX_RESPONSE or host not in HOSTS:
                return {'status': 'unavailable'}
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return {'status': 'unavailable'}
            product = payload.get('product')
            if not product:
                return {'status': 'unknown'}
            if not isinstance(product, dict):
                return {'status': 'unavailable'}
            returned = product.get('code', payload.get('code'))
            if not is_gtin(returned) or returned.zfill(14) != barcode.zfill(14):
                return {'status': 'unavailable'}
            name = clean(product.get('product_name_en') or product.get('product_name'), 140)
            if not name:
                return {'status': 'unknown'}
            source, domain = next((value for value in SOURCES.values() if value[1] == host), SOURCES['food'])
            category = ''
            tags = product.get('categories_tags', [])
            if isinstance(tags, list):
                groups = [('Drinks', {'en:beverages', 'en:waters', 'en:sodas'}),
                          ('Food', {'en:foods', 'en:snacks', 'en:dairies', 'en:cereals-and-their-products'}),
                          ('Personal care', {'en:cosmetics', 'en:shampoos', 'en:soaps'}),
                          ('Household', {'en:household-products', 'en:cleaning-products'}),
                          ('Pet food', {'en:pet-foods'})]
                category = next((label for label, allowed in groups if allowed.intersection(t for t in tags if isinstance(t, str))), '')
            brand, variant = clean(product.get('brands'), 120), clean(product.get('quantity'), 80)
            description = '; '.join(text for text in (f'Brand: {brand}' if brand else '', f'Pack size: {variant}' if variant else '') if text)
            return {'status': 'recognized', 'reference': {'barcode': barcode, 'product_name': name,
                'brand': brand, 'variant': variant, 'category': category, 'description': description,
                'provider': source, 'source_url': f'https://{domain}/product/{returned}',
                'license_url': LICENSE_URL, 'completeness': 'partial' if not brand or not variant else 'named_with_brand_and_size'}}
        except HTTPError as error:
            return {'status': 'rate_limited' if error.code == 429 else 'unknown' if error.code == 404 else 'unavailable'}
        except (URLError, TimeoutError, OSError, ValueError, TypeError, RecursionError):
            return {'status': 'unavailable'}

class LookupState:
    def __init__(self):
        self.lock = Lock()
        self.cache = OrderedDict()
        self.calls = deque()
        self.users = OrderedDict()
        self.cooldown = 0

def lookup_product_by_barcode(barcode, actor_id):
    """Provider-neutral result with bounded process-local cache/rate budgets.

    Catalogue lookup MUST happen in the caller before this method. In-memory
    budgets are not a distributed quota; provider 429 opens a one-minute circuit.
    """
    if not current_app.config.get('BARCODE_EXTERNAL_LOOKUP_ENABLED', False):
        return {'status': 'disabled'}
    if not is_gtin(barcode):
        return {'status': 'unsupported'}
    state = current_app.extensions.setdefault('barcode_recognition', LookupState())
    now = monotonic()
    with state.lock:
        cached = state.cache.get(barcode)
        if cached and cached[0] > now:
            return deepcopy(cached[1])
        while state.calls and state.calls[0] <= now - 60:
            state.calls.popleft()
        user_calls = state.users.setdefault(actor_id, deque())
        state.users.move_to_end(actor_id)
        while user_calls and user_calls[0] <= now - 60:
            user_calls.popleft()
        if now < state.cooldown or len(state.calls) >= 10 or len(user_calls) >= 6:
            return {'status': 'rate_limited'}
        state.calls.append(now)
        user_calls.append(now)
        while len(state.users) > 512:
            state.users.popitem(last=False)
    # Additional adapters can implement lookup(barcode) without changing routes.
    result = OpenFactsProvider().lookup(barcode)
    with state.lock:
        if result['status'] == 'rate_limited':
            state.cooldown = monotonic() + 60
        if result['status'] in {'recognized', 'unknown'}:
            state.cache[barcode] = (monotonic() + (3600 if result['status'] == 'recognized' else 300), deepcopy(result))
            state.cache.move_to_end(barcode)
            while len(state.cache) > 512:
                state.cache.popitem(last=False)
    return result
