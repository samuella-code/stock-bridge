"""Staff operations-only owner-lock races. No live actions at import, no retries/cleanup."""
import threading
import time
from sqlalchemy import event


class RaceTimeout(AssertionError):
    pass


def owner_lock(statement):
    text=' '.join(statement.lower().replace('"','').split())
    return (text.startswith('update user set last_activity_at=user.last_activity_at where user.id = ')
            and ';' not in text and '--' not in text)


def operations_paths(variant, membership_id, business_id, product_id):
    if any(type(v) is not int or v<=0 for v in (membership_id,business_id,product_id)):
        raise ValueError('Invalid operations race identity')
    base=f'/staff/memberships/{membership_id}/businesses/{business_id}'
    create=base+'/products/new';edit=base+f'/products/{product_id}/edit';expense=base+'/expenses'
    choices={'create':(create,create),'edit':(edit,edit),'stale':(edit,edit),
             'expense':(expense,expense),'revoke-product':('/staff/manage',edit),
             'revoke-expense':('/staff/manage',expense)}
    if type(variant) is not str or variant not in choices:raise ValueError('Invalid operations variant')
    return choices[variant]


def concurrent_operations_posts(engine, clients, paths, payloads, observer, *, owner_id,
                     membership_id, business_id, product_id, variant, timeout=30, evidence=None):
    if (len(clients)!=2 or len(paths)!=2 or len(payloads)!=2 or
            tuple(paths)!=operations_paths(variant,membership_id,business_id,product_id) or
            type(owner_id) is not int or owner_id<=0 or type(timeout) not in (int,float)
            or not .1<=timeout<=30):
        raise ValueError('Invalid Staff race configuration')
    end=time.monotonic()+timeout; active=end-min(5,timeout/3)
    names=('stockbridge-race-A','stockbridge-race-B')
    held=threading.Event(); second=threading.Event(); release=threading.Event()
    observed=threading.Event(); observer_done=threading.Event()
    connections={}; pids={}; statuses={}; errors=[]; mutex=threading.Lock()
    def remaining(final=False): return max(0,(end if final else active)-time.monotonic())
    def wait(flag):
        if not flag.wait(remaining()): raise RaceTimeout('Staff race deadline')
    def name(): return threading.current_thread().name
    def checkout(driver,record,proxy):
        if name() in names:
            with mutex: connections[id(driver)]=driver
    def before(conn,cursor,sql,params,context,many):
        if name() in names and owner_lock(sql):
            values=params.values() if isinstance(params,dict) else params
            if not values or owner_id not in values: raise AssertionError('Wrong owner lock')
            driver=conn.connection.driver_connection
            with mutex: connections[id(driver)]=driver; pids[name()]=driver.info.backend_pid
            if name()==names[1]: second.set()
    def after(conn,cursor,sql,params,context,many):
        if name()==names[0] and owner_lock(sql) and not held.is_set():
            held.set(); wait(release)
    def worker(index):
        try:
            if index: wait(held)
            statuses[index]=clients[index].post(paths[index],data=payloads[index]).status_code
        except BaseException as exc: errors.append(type(exc).__name__)
    def observe():
        try:
            while remaining() and not release.is_set():
                if pids[names[0]] in observer.execute('SELECT pg_blocking_pids(%s)',(pids[names[1]],)).fetchone()[0]:
                    observed.set(); return
                release.wait(min(.025,remaining()))
        except BaseException as exc: errors.append(type(exc).__name__)
        finally: observer_done.set()
    hooks=[('checkout',checkout),('before_cursor_execute',before),('after_cursor_execute',after)]
    installed=[]; workers=[]; monitor=None
    try:
        for hook,fn in hooks: event.listen(engine,hook,fn); installed.append((hook,fn))
        workers=[threading.Thread(target=worker,args=(i,),name=n,daemon=True) for i,n in enumerate(names)]
        for thread in workers: thread.start()
        wait(held); wait(second)
        assert len(set(pids.values()))==2, 'Staff workers require independent backends'
        assert observer.info.backend_pid not in pids.values(), 'Observer must use a separate backend'
        monitor=threading.Thread(target=observe,name='stockbridge-race-observer',daemon=True); monitor.start()
        wait(observer_done); assert observed.is_set(), 'No owner-lock contention observed'
        if evidence is not None:evidence.update(workers=list(pids.values()),observer=observer.info.backend_pid)
        release.set()
        for thread in workers: thread.join(remaining())
        if any(t.is_alive() for t in workers): raise RaceTimeout('Staff worker deadline')
        assert not errors and set(statuses)=={0,1}, 'Staff worker failed'
        return [statuses[0],statuses[1]]
    finally:
        release.set()
        live=[t for t in workers+([monitor] if monitor else []) if t.is_alive()]
        cancellation=[]
        if live:
            with mutex: tracked=list(connections.values())
            tracked.append(observer)
            def cancel(driver):
                try: driver.cancel_safe(timeout=max(.001,min(2,remaining(True))))
                except Exception: pass
            for driver in tracked:
                t=threading.Thread(target=cancel,args=(driver,),name='stockbridge-race-cancel',daemon=True)
                cancellation.append(t);t.start()
            for t in live+cancellation:t.join(remaining(True))
        if any(t.is_alive() for t in live+cancellation):
            import pytest
            pytest.exit('Staff shutdown unconfirmed; preserve target',returncode=2)
        for hook,fn in reversed(installed):event.remove(engine,hook,fn)
