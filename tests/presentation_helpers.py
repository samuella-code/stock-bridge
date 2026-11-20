"""Semantic HTML assertions for presentation tests, without extra dependencies."""
from html import unescape
from html.parser import HTMLParser
import re

class Nodes(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.nodes=[]; self.feed(html)
    def handle_starttag(self, tag, attrs): self.nodes.append((tag, dict(attrs)))

def nodes(html, tag, **attrs):
    return [a for t,a in Nodes(html).nodes if t==tag and all(a.get(k)==v for k,v in attrs.items())]

def field(html, name):
    matches=nodes(html, 'input', name=name)
    assert len(matches)==1, (name, matches)
    return matches[0]

def text(html):
    return ' '.join(unescape(re.sub(r'<[^>]*>', ' ', html)).split())

def naira(value):
    return ('₦{:,.2f}'.format(value)).removesuffix('.00')
