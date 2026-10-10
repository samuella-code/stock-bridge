"""Allowlisted catalogue context; never accept a caller-provided destination."""
def catalogue_context(args):
    context = {}
    if args.get('view') == 'inventory':
        context['view'] = 'inventory'
    for key, limit in (('q', 100), ('category', 180)):
        value = args.get(key, '').strip()[:limit]
        if value:
            context[key] = value
    choices = {
        'stock': {'all', 'in', 'healthy', 'low', 'out', 'archived'},
        'sort': {'recent', 'name', 'stock', 'price', 'cost', 'value', 'oldest', 'priority'},
    }
    for key, allowed in choices.items():
        if args.get(key) in allowed:
            context[key] = args[key]
    return context
