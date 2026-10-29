"""Pro overview: bounded grouped reads of already authorized business IDs."""
from datetime import datetime, time, timedelta
from app.main.insights import comparison
from app.main.reporting import grouped_financial_totals, combined_financial_totals


METRICS = (
    ('revenue', 'Revenue'), ('cogs', 'Cost of goods sold'),
    ('gross_profit', 'Estimated gross profit'), ('expenses', 'Recorded operating expenses'),
    ('net_profit', 'Estimated result after recorded expenses'),
)


def period_comparison(current, previous, *, incomplete):
    uncertain = bool(current['zero_cost_lines'] or previous['zero_cost_lines'])
    return {key: comparison(current[key], previous[key], incomplete=incomplete,
                            uncertain_cost=uncertain and key in ('gross_profit', 'net_profit'))
            for key, _ in METRICS}


def build_overview(businesses, start, end, *, now):
    previous_start = start-(end-start)
    incomplete = end > datetime.combine(now.date(), time.min)
    ids = [business.id for business in businesses]
    current = grouped_financial_totals(ids, start, end)
    previous = grouped_financial_totals(ids, previous_start, start)
    rows = [dict(business=b, current=current[b.id], previous=previous[b.id],
                 trends=period_comparison(current[b.id], previous[b.id], incomplete=incomplete))
            for b in businesses]
    combined = combined_financial_totals([row['current'] for row in rows])
    preceding = combined_financial_totals([row['previous'] for row in rows])
    return dict(start=start, end=end, last=end-timedelta(days=1),
                previous_start=previous_start, previous_last=start-timedelta(days=1),
                incomplete=incomplete, rows=rows, current=combined, previous=preceding,
                trends=period_comparison(combined, preceding, incomplete=incomplete),
                zero_cost=bool(combined['zero_cost_lines'] or preceding['zero_cost_lines']),
                metrics=METRICS)
