"""Pure Jinja/HTML checks: no Flask app, database or network."""
from pathlib import Path
from html.parser import HTMLParser
from types import SimpleNamespace
import pytest
import subprocess
from jinja2 import Environment, FileSystemLoader, DictLoader, ChoiceLoader, StrictUndefined

ROOT=Path(__file__).resolve().parents[1]
class Nodes(HTMLParser):
    def __init__(self,html):
        super().__init__(); self.nodes=[]; self.feed(html)
    def handle_starttag(self,tag,attrs): self.nodes.append((tag,dict(attrs)))

def render(template, source=None, **values):
    env=Environment(loader=ChoiceLoader([DictLoader({'base.html':'{% block content %}{% endblock %}{% block scripts %}{% endblock %}', **({template:source} if source else {})}),FileSystemLoader(ROOT/'app/templates')]),undefined=StrictUndefined,autoescape=True)
    env.globals.update(url_for=lambda endpoint,**kw: '/static/'+kw['filename'] if endpoint=='static' else '/'+endpoint.replace('.','/'),csrf_token=lambda:'synthetic-csrf',config=SimpleNamespace(SUBSCRIPTIONS_ENABLED=False,SOCIAL_GOOGLE_ENABLED=False,SOCIAL_APPLE_ENABLED=False),request=SimpleNamespace(form={},args={}),social_providers=[],social_ready={'google':False})
    return env.get_template(template).render(**values)

@pytest.mark.parametrize('page,count',[('login',1),('signup',1),('reset_password',2),('social_finish',1)])
def test_customer_fields_hidden_and_associated(page,count):
    html=render('auth/'+page+'.html',existing=True,pending=SimpleNamespace(provider='google',email='synthetic@example.invalid'))
    nodes=Nodes(html).nodes
    inputs=[a for t,a in nodes if t=='input' and a.get('type')=='password']
    buttons=[a for t,a in nodes if t=='button' and 'data-customer-password-toggle' in a]
    assert len(inputs)==len(buttons)==count
    assert len({a['id'] for a in inputs})==count
    for field,button in zip(inputs,buttons):
        assert button['type']=='button' and 'hidden' in button
        assert button['aria-controls']==field['id'] and button['aria-pressed']=='false'
        assert 'required' in field
        assert field['autocomplete']==('current-password' if page in ('login','social_finish') else 'new-password')
        assert any(t=='label' and a.get('for')==field['id'] for t,a in nodes)
    assert 'js/password_visibility.js' in html

def test_showcase_progressive_fallback():
    nodes=Nodes(render('components/hero_showcase.html')).nodes
    panels=[a for t,a in nodes if 'data-showcase-panel' in a]
    assert len(panels)==7 and 'hidden' not in panels[0]
    assert all('hidden' in p for p in panels[1:])
    indicators=next(a for t,a in nodes if 'data-showcase-indicators' in a)
    assert indicators['role']=='group'
    controls=next(a for t,a in nodes if 'data-showcase-controls' in a)
    assert 'hidden' in controls
    assert 'data-showcase-step' not in render('components/hero_showcase.html')

def test_password_component_preserves_validation_and_hint():
    env=Environment(loader=FileSystemLoader(ROOT/'app/templates'),autoescape=True)
    macro=env.get_template('components/password_field.html').module.password_field
    html=str(macro('signup-password','Password',autocomplete='new-password',minlength=8,help='Use at least 8 characters.',autofocus=True))
    field=next(a for t,a in Nodes(html).nodes if t=='input')
    assert field['minlength']=='8' and field['aria-describedby']=='password-help' and 'autofocus' in field
    assert '<small id="password-help">Use at least 8 characters.</small>' in html

def test_scoped_responsive_rules():
    css=(ROOT/'app/static/css/entry.css').read_text()
    assert '.entry-hero>.hero-copy{min-width:0;align-self:start;padding-top:24px}' in css
    assert '@media(max-width:768px){.entry-hero>.hero-copy{padding-top:0}' in css
    assert '.showcase-indicators button[aria-pressed=true]::before{background:#14663e}' in css
    assert '.customer-password-toggle[hidden]{display:none}' in css

@pytest.mark.parametrize('page',['login','signup','reset_password','social_finish'])
def test_original_input_attributes_and_form_contract_preserved(page):
    template='app/templates/auth/'+page+'.html'
    original=subprocess.check_output(['git','show','6b56788a5d882b53bb43dbf3d8d705c9cbfe6dee:'+template],cwd=ROOT,text=True)
    context=dict(existing=True,pending=SimpleNamespace(provider='google',email='synthetic@example.invalid'))
    old=Nodes(render('auth/'+page+'.html',source=original,**context)).nodes
    new=Nodes(render('auth/'+page+'.html',**context)).nodes
    old_fields=[a for t,a in old if t=='input']
    new_fields=[{k:v for k,v in a.items() if k!='id'} for t,a in new if t=='input']
    assert new_fields==old_fields
    assert [a for t,a in old if t=='form']==[a for t,a in new if t=='form']


def test_toggle_not_inside_label():
    html=render('auth/reset_password.html')
    class Labels(HTMLParser):
        depth=0
        def handle_starttag(self,tag,attrs):
            if tag=='label': self.depth+=1
            if tag=='button': assert self.depth==0
        def handle_endtag(self,tag):
            if tag=='label': self.depth-=1
    Labels().feed(html)


def test_workspace_has_only_sidebar_and_stage_in_grid():
    class Grid(HTMLParser):
        def __init__(self):super().__init__();self.stack=[];self.children=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if self.stack and self.stack[-1]=='showcase-workspace':self.children.append(attrs.get('class',''))
            if tag not in ('br','input','img','meta','link','hr'):self.stack.append(attrs.get('class',''))
        def handle_endtag(self,tag):
            if self.stack:self.stack.pop()
    p=Grid();p.feed(render('components/hero_showcase.html'))
    assert p.children==['showcase-sidebar','showcase-stage']


def test_auth_dots_have_no_visible_numbered_controls():
    html=render('auth/login.html')
    assert 'data-auth-indicators' in html
    assert 'data-auth-step' not in html
    assert 'Pause preview' in html


def test_social_confirmation_loads_shared_animation():
    html=render('auth/social_finish.html',existing=True,pending=SimpleNamespace(provider='google',email='synthetic@example.invalid'))
    assert 'data-auth-indicators' in html and 'js/marketing.js' in html
