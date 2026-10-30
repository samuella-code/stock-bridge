"""Staff-only owner-lock races. No live actions at import, no retries/cleanup."""
import threading
import time
from sqlalchemy import event


class RaceTimeout(AssertionError):
    pass


def owner_lock(statement):
    text=' '.join(statement.lower().replace('"','').split())
    return (text.startswith('update user set last_activity_at=user.last_activity_at where user.id = ')
            and ';' not in text and '--' not in text)


def concurrent_posts(engine, clients, paths, payloads, observer, *, owner_id,
                     rollback=False, baseline_check=None, timeout=30, fail_audit_number=1, evidence=None):
    if (len(clients)!=2 or len(paths)!=2 or len(payloads)!=2 or
            any(p not in ('/staff/manage','/staff/invite') for p in paths) or
            type(owner_id) is not int or owner_id<=0 or type(timeout) not in (int,float)
            or not .1<=timeout<=30 or (rollback and baseline_check is None)):
        raise ValueError('Invalid Staff race configuration')
    end=time.monotonic()+timeout; active=end-min(5,timeout/3)
    names=('stockbridge-race-A','stockbridge-race-B')
    held=threading.Event(); second=threading.Event(); release=threading.Event()
    a_done=threading.Event(); rolled_back=threading.Event(); b_precommit=threading.Event()
    commit_release=threading.Event(); observed=threading.Event(); observer_done=threading.Event()
    connections={}; pids={}; statuses={}; errors=[]; mutex=threading.Lock(); audit_writes=[0]
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
        if rollback and name()==names[0] and ' '.join(sql.lower().split()).startswith('insert into staff_audit_event '):
            audit_writes[0]+=1
            if audit_writes[0]==fail_audit_number:raise ValueError('Synthetic Staff rollback injection')
    def on_rollback(conn):
        if name()==names[0]: rolled_back.set()
    def on_commit(conn):
        if rollback and name()==names[1]: b_precommit.set(); wait(commit_release)
    def worker(index):
        try:
            if index: wait(held)
            statuses[index]=clients[index].post(paths[index],data=payloads[index]).status_code
        except BaseException as exc: errors.append(type(exc).__name__)
        finally:
            if index==0: a_done.set()
    def observe():
        try:
            while remaining() and not release.is_set():
                if pids[names[0]] in observer.execute('SELECT pg_blocking_pids(%s)',(pids[names[1]],)).fetchone()[0]:
                    observed.set(); return
                release.wait(min(.025,remaining()))
        except BaseException as exc: errors.append(type(exc).__name__)
        finally: observer_done.set()
    hooks=[('checkout',checkout),('before_cursor_execute',before),('after_cursor_execute',after),
           ('rollback',on_rollback),('commit',on_commit)]
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
        if rollback:
            wait(a_done); workers[0].join(remaining())
            assert not workers[0].is_alive() and statuses.get(0)==400 and rolled_back.is_set() and audit_writes[0]>=fail_audit_number, 'Rollback completion unconfirmed'
            wait(b_precommit)
            baseline_check()  # NEW read-only connection; B still cannot commit.
            commit_release.set()
        for thread in workers: thread.join(remaining())
        if any(t.is_alive() for t in workers): raise RaceTimeout('Staff worker deadline')
        assert not errors and set(statuses)=={0,1}, 'Staff worker failed'
        return [statuses[0],statuses[1]]
    finally:
        release.set(); commit_release.set()
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
