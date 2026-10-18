"""Bounded HTTP race orchestrator; only explicitly authorized PostgreSQL tests."""
import math
import threading
import time
from sqlalchemy import event


class RaceTimeout(AssertionError):
    pass


def target_statement(statement, kind):
    normalized = ' '.join(statement.lower().split())
    if kind == 'void':
        return 'from sale ' in normalized and 'for update' in normalized
    if kind == 'product':
        return ('from product ' in normalized and 'for update' in normalized
                or normalized.startswith('update product set ') and 'stock_quantity' in normalized)
    return normalized.startswith('insert into transaction_submission ')


def concurrent_posts(engine, clients, path, payloads, observer, *, lock_kind='receipt',
                     observe_lock=True, scenario_timeout=20):
    """Use a shared path or two fixed paths; never retry a request.

    All joins/observation/cancellation fit one wall-clock budget. A surviving
    thread is fatal to pytest, not an ordinary failure followed by another test.
    """
    paths = [path, path] if isinstance(path, str) else path
    if (not isinstance(paths, (list, tuple)) or len(paths) != 2
            or any(not isinstance(p, str) or not p.startswith('/') or p.startswith('//') for p in paths)
            or len(clients) != 2 or len(payloads) != 2
            or lock_kind not in ('receipt', 'void', 'product')
            or type(scenario_timeout) not in (int, float)
            or not math.isfinite(scenario_timeout) or not 0 < scenario_timeout <= 60):
        raise ValueError('Invalid bounded race configuration')
    deadline = time.monotonic() + scenario_timeout
    active_deadline = deadline - min(4, scenario_timeout / 3)
    def remaining(active=True):
        return max(0, (active_deadline if active else deadline) - time.monotonic())
    def wait(flag):
        if not flag.wait(remaining()):
            raise RaceTimeout('Scenario deadline exceeded')
    barrier = threading.Barrier(2)
    held = threading.Event(); release = threading.Event(); second = threading.Event()
    observed = threading.Event(); observation_done = threading.Event()
    pids = {}; connections = {}; results = {}; errors = []
    connection_lock = threading.Lock()
    names = ('stockbridge-race-A', 'stockbridge-race-B')
    # Checkout captures connections even when a worker blocks before the target SQL.
    def checkout(driver, record, proxy):
        name = threading.current_thread().name
        if name in names:
            with connection_lock:
                connections.setdefault(name, {})[id(driver)] = driver
    def before(conn, cursor, statement, parameters, context, many):
        name = threading.current_thread().name
        if name not in names:
            return
        driver = conn.connection.driver_connection
        with connection_lock:
            connections.setdefault(name, {})[id(driver)] = driver
        if target_statement(statement, lock_kind):
            pids[name] = driver.info.backend_pid
            if name == names[1]: second.set()
    def after(conn, cursor, statement, parameters, context, many):
        if (threading.current_thread().name == names[0]
                and target_statement(statement, lock_kind) and not held.is_set()):
            held.set()
            wait(release)
    def worker(index):
        try:
            barrier.wait(timeout=remaining())
            if index == 1: wait(held)
            results[index] = clients[index].post(paths[index], data=payloads[index]).status_code
        except Exception as error:
            errors.append(type(error).__name__)  # Never include messages or SQL values.
    def observation():
        try:
            while remaining() and not release.is_set():
                blockers = observer.execute('SELECT pg_blocking_pids(%s)', (pids[names[1]],)).fetchone()[0]
                if pids[names[0]] in blockers:
                    observed.set()
                    break
                release.wait(min(.025, remaining()))
        except Exception as error:
            errors.append(type(error).__name__)
        finally:
            observation_done.set()
    workers = [threading.Thread(target=worker, args=(i,), name=name, daemon=True)
               for i, name in enumerate(names)]
    observer_thread = None
    hooks = [('checkout', checkout), ('before_cursor_execute', before), ('after_cursor_execute', after)]
    installed = []
    try:
        for name, callback in hooks:
            event.listen(engine, name, callback); installed.append((name, callback))
        for thread in workers: thread.start()
        wait(held); wait(second)
        assert len(set(pids.values())) == 2, 'Workers must use independent PostgreSQL backends'
        if observe_lock:
            observer_thread = threading.Thread(target=observation, name='stockbridge-race-observer', daemon=True)
            observer_thread.start()
            wait(observation_done)
            assert observed.is_set(), 'No PostgreSQL lock contention observed'
        release.set()
        for thread in workers: thread.join(remaining())
        if any(t.is_alive() for t in workers): raise RaceTimeout('Worker execution deadline exceeded')
        assert not errors, 'Worker error categories: ' + ','.join(errors)
        assert set(results) == {0, 1}, 'Both workers must return'
        return [results[0], results[1]], observed.is_set()
    finally:
        release.set()
        barrier.abort()
        live = [t for t in workers if t.ident is not None and t.is_alive()]
        if observer_thread and observer_thread.is_alive(): live.append(observer_thread)
        cancel_threads = []
        with connection_lock:
            drivers = {key: value for name in names for key, value in connections.get(name, {}).items()}
        if observer_thread and observer_thread.is_alive(): drivers[id(observer)] = observer
        if live:
            def cancel(driver):
                try: driver.cancel_safe(timeout=max(.001, min(2, remaining(False))))
                except Exception: pass  # Shutdown is established by joins, never by cancellation return.
            for driver in drivers.values():
                t = threading.Thread(target=cancel, args=(driver,), daemon=True)
                cancel_threads.append(t); t.start()
            for thread in live + cancel_threads: thread.join(remaining(False))
        if any(t.is_alive() for t in live + cancel_threads):
            import pytest
            pytest.exit('PostgreSQL race worker shutdown unconfirmed; stop run and inspect isolated instance.', returncode=2)
        for name, callback in reversed(installed): event.remove(engine, name, callback)
