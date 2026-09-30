"""Deterministic note/TXT ingestion; drafts only, no database writes."""
import csv
import io
import re
from sqlalchemy import func
from app import db
from app.models import Product
from app.products.catalogue import HEADERS, MAX_ROWS, MAX_UPLOAD

ALIASES = {label.lower(): field for label, field in HEADERS.items()}
ALIASES.update({'name': 'name', 'quantity': 'stock_quantity', 'opening qty': 'stock_quantity',
                'cost': 'buying_price', 'selling': 'selling_price'})
POSITIONAL = ['name', 'stock_quantity', 'buying_price', 'selling_price']


def read_text_file(upload):
    if not (upload.filename or '').lower().endswith('.txt'):
        raise ValueError('Upload a plain UTF-8 .txt product list.')
    blob = upload.stream.read(MAX_UPLOAD + 1)
    if len(blob) > MAX_UPLOAD:
        raise ValueError('The text file must be smaller than 2 MB.')
    try:
        return parse_product_list(blob.decode('utf-8-sig'))
    except UnicodeError:
        raise ValueError('This text file could not be read. Save it as UTF-8 plain text.')


def parse_product_list(text):
    if len(text.encode('utf-8')) > MAX_UPLOAD:
        raise ValueError('The product list must be smaller than 2 MB.')
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        raise ValueError('Use plain text; binary files and control characters are not allowed.')
    lines = text.lstrip('\ufeff').splitlines()
    if len(lines) > MAX_ROWS:
        raise ValueError(f'Use at most {MAX_ROWS} lines per import, including empty lines.')
    rows, fields, delimiter = [], None, None
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        candidate = '\t' if '\t' in line else ',' if ',' in line else None
        try:
            cells = next(csv.reader([line], delimiter=candidate, strict=True)) if candidate else [line.strip()]
        except csv.Error:
            cells = [line.strip()]
        headers = [cell.strip().lower().removesuffix(' *') for cell in cells]
        if not rows and fields is None and 'product name' in headers:
            if any(h not in ALIASES for h in headers) or len(set(ALIASES[h] for h in headers)) != len(headers):
                raise ValueError('The text header has unknown or duplicate columns. Use the template column names.')
            fields, delimiter = [ALIASES[h] for h in headers], candidate
            continue
        raw = {'_row': number}
        if fields is not None:
            try:
                cells = next(csv.reader([line], delimiter=delimiter, strict=True)) if delimiter else [line.strip()]
            except csv.Error:
                cells = []
            if len(cells) != len(fields):
                raw.update(name=line.strip(), _warning='Columns could not be mapped safely. Review this row and enter the missing values.')
            else:
                raw.update(zip(fields, [c.strip() for c in cells]))
        else:
            if candidate is None:
                cells = re.split(r'\s+-\s+', line.strip())
                # Split from the right so a hyphen inside a product name is retained.
                if len(cells) > 4:
                    cells = [' - '.join(cells[:-3])] + cells[-3:]
            numeric = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$|^(?:nan|inf(?:inity)?)$', re.I)
            if len(cells) == 4 and all(not c.strip() or numeric.fullmatch(c.strip()) for c in cells[1:]):
                raw.update(zip(POSITIONAL, [c.strip() for c in cells]))
            else:
                raw['name'] = line.strip()
                if candidate or len(cells) > 1:
                    raw['_warning'] = 'Ambiguous separators: kept the whole line as the name. Please review it.'
        rows.append(raw)
    if not rows:
        raise ValueError('No products found. Paste or upload at least one product name.')
    return rows


def existing_matches(bid, rows):
    """Exact case-insensitive name / SKU suggestions, scoped and bounded."""
    names = list({str(r.get('name') or '').strip().lower() for r in rows})
    skus = list({str(r.get('sku') or '').strip().upper() for r in rows} - {''})
    found = {}
    for values, column in ((names, func.lower(Product.name)), (skus, Product.sku)):
        for start in range(0, len(values), 400):
            for product in Product.query.filter(Product.business_id == bid, column.in_(values[start:start+400])).limit(2000):
                found[product.id] = product
    by_name, by_sku = {}, {}
    for product in found.values():
        by_name.setdefault(product.name.strip().lower(), []).append(product)
        if product.sku:
            by_sku[product.sku] = product
    result = []
    for row in rows:
        matches = {p.id:p for p in by_name.get(str(row.get('name') or '').strip().lower(), [])}
        sku_match = by_sku.get(str(row.get('sku') or '').strip().upper())
        if sku_match:
            matches[sku_match.id] = sku_match
        result.append(sorted(matches.values(), key=lambda p:p.id)[:10])
    return result
