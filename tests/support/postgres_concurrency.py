"""Bounded HTTP race orchestrator; used only by explicitly authorized PG tests."""
import threading
import time
from sqlalchemy import event


def concurrent_posts(engine, clients, path, payloads, observer, *, lock_kind='receipt', observe_lock=True):
    barrier=threading.Barrier(2,timeout=5)
    held=threading.Event();release=threading.Event();second=threading.Event()
    pids={};connections={};results={};errors=[]
    def target(statement):
        normalized=' '.join(statement.lower().split())
        if lock_kind=='void':return 'from sale ' in normalized and 'for update' in normalized
        return normalized.startswith('insert into transaction_submission ')
    def before(conn,cursor,statement,parameters,context,many):
        name=threading.current_thread().name
        if name not in ('stockbridge-race-A','stockbridge-race-B') or not target(statement):return
        driver=conn.connection.driver_connection
        pids[name]=driver.info.backend_pid;connections[name]=driver
        if name.endswith('B'):second.set()
    def after(conn,cursor,statement,parameters,context,many):
        name=threading.current_thread().name
        if name=='stockbridge-race-A' and target(statement):
            held.set()
            if not release.wait(5):raise AssertionError('First-worker hold timed out')
    def worker(index):
        try:
            barrier.wait()
            # A must own the intended lock before B reaches its claim/void query.
            if index==1 and not held.wait(5):raise AssertionError('First worker did not reach lock')
            results[index]=clients[index].post(path,data=payloads[index]).status_code
        except Exception as error:
            errors.append(type(error).__name__)  # No SQL parameters or credentials.
    threads=[threading.Thread(target=worker,args=(i,),name='stockbridge-race-'+name,daemon=True)
             for i,name in enumerate(('A','B'))]
    event.listen(engine,'before_cursor_execute',before)
    event.listen(engine,'after_cursor_execute',after)
    observed=False
    try:
        for thread in threads:thread.start()
        if not held.wait(5) or not second.wait(5):raise AssertionError('Workers did not reach concurrent SQL')
        assert len(set(pids.values()))==2, 'Workers must use independent PostgreSQL backends'
        if observe_lock:
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                blockers=observer.execute('SELECT pg_blocking_pids(%s)',(pids['stockbridge-race-B'],)).fetchone()[0]
                if pids['stockbridge-race-A'] in blockers:
                    observed=True;break
                time.sleep(.025)
            assert observed, 'No PostgreSQL lock contention observed'
        release.set()
        for thread in threads:thread.join(12)
        assert not any(t.is_alive() for t in threads), 'Worker timeout'
        assert not errors, 'Worker error categories: '+','.join(errors)
        return [results[0],results[1]],observed
    finally:
        release.set()
        for thread in threads:
            if thread.is_alive():
                driver=connections.get(thread.name)
                if driver:
                    try:driver.cancel_safe(timeout=2)
                    except Exception:pass
                thread.join(2)
        event.remove(engine,'before_cursor_execute',before)
        event.remove(engine,'after_cursor_execute',after)
