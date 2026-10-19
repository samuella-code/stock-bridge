"""Operator-started, bounded OS-only helper. No sudo, database or evidence files.

Install alongside ubuntu_os_evidence.py in a reviewed root-owned directory.
The normal preflight authenticates the PostgreSQL OS user's live Unix peer.
"""
import argparse
import json
import os
from pathlib import Path
try:
    import pwd
except ImportError:  # Windows can collect pure policy tests; live use still rejects non-Linux.
    pwd = None
import secrets
import socket
import stat
import struct
import sys
import time

try:
    from . import ubuntu_os_evidence as collector
except ImportError:
    # Standalone operator copy: only this root-owned script directory is imported.
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    import ubuntu_os_evidence as collector

SOCKET=Path('/run/stockbridge-preflight/evidence.sock')
MAX_BYTES=65536
BUDGET=120


def reject(category): collector.reject('privileged helper '+category)


def secure_path(path, owner, directory=False):
    node=path.lstat()
    if (node.st_uid!=owner or node.st_mode & 0o022 or stat.S_ISLNK(node.st_mode)
            or directory and not stat.S_ISDIR(node.st_mode)
            or not directory and not stat.S_ISREG(node.st_mode)):
        reject('path ownership or permissions unsafe')


def secure_parent():
    secure_path(Path('/run'),0,True)
    secure_path(SOCKET.parent,pwd.getpwnam('postgres').pw_uid,True)


def peer_uid(connection):
    return struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]


def receive(connection, deadline=None, read_timeout=5):
    deadline = time.monotonic() + 5 if deadline is None else deadline
    payload=b''
    while not payload.endswith(b'\n'):
        remaining = deadline - time.monotonic()
        if remaining <= 0: reject('message deadline exceeded')
        connection.settimeout(min(read_timeout, remaining))
        chunk=connection.recv(min(4096,MAX_BYTES+1-len(payload)))
        if not chunk: reject('message incomplete')
        payload+=chunk
        if len(payload)>MAX_BYTES: reject('message too large')
    if b'\n' in payload[:-1]: reject('ambiguous message')
    try: value=json.loads(payload)
    except (ValueError,UnicodeError): reject('message invalid')
    if not isinstance(value,dict): reject('message not an object')
    return value


def request_evidence(backend_pid=None):
    if sys.platform!='linux': reject('requires Linux')
    try:
        secure_parent()
        node=SOCKET.lstat()
        if node.st_uid!=pwd.getpwnam('postgres').pw_uid or not stat.S_ISSOCK(node.st_mode): reject('socket identity unsafe')
        nonce=secrets.token_hex(32)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            deadline=time.monotonic()+35
            conn.settimeout(35);conn.connect(str(SOCKET))
            if peer_uid(conn)!=pwd.getpwnam('postgres').pw_uid: reject('peer identity not PostgreSQL OS user')
            conn.sendall(json.dumps({'nonce':nonce,'backend_pid':backend_pid}).encode()+b'\n')
            response=receive(conn, deadline, read_timeout=35)
        if (set(response)!={'nonce','evidence','backend'} or response['nonce']!=nonce
                or not isinstance(response['evidence'],dict)):
            reject('challenge or evidence mismatch')
        return response['evidence'],response['backend']
    except collector.EvidenceError: raise
    except (OSError,ValueError,KeyError): reject('unavailable or incomplete')


def session_policy(preflights=1, seconds=BUDGET, acknowledgment=None):
    # One preflight needs two collections. Larger sessions require operator opt-in.
    if (type(preflights) is not int or not 1 <= preflights <= 64
            or type(seconds) is not int or not 1 <= seconds <= 1800):
        reject('session bounds invalid')
    if (preflights != 1 or seconds > BUDGET) and acknowledgment != 'approved-bounded-phase9a-evidence':
        reject('extended session not authorized')
    return preflights * 2, seconds


def instance_signature(evidence):
    # Pin one live instance for the entire operator session, not just each pair.
    process = evidence['process']
    sockets = sorted((s['protocol'], s['address'], s['port'], tuple(s['pids']))
        for s in evidence['sockets'] if s['port'] == collector.PORT or process['pid'] in s['pids'])
    return tuple(evidence[k] for k in ('cluster', 'directory', 'port', 'postmaster_started',
        'postgres_uid', 'process', 'commandline')), sockets


def diagnostic_category(error):
    """Classify without formatting exception messages or evidence."""
    if isinstance(error, TimeoutError): return 'timeout'
    if isinstance(error, PermissionError): return 'permission-denied'
    if isinstance(error, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return 'peer-disconnected'
    if isinstance(error, KeyError): return 'missing-evidence'
    return 'inspection-failed'


def inspection_failure(stage, request_ordinal, error):
    # stage and ordinal are assigned only by this module, never by a peer.
    request = '' if request_ordinal is None else f' request={request_ordinal}'
    reject(f'failed: stage={stage}{request} category={diagnostic_category(error)}')


def serve(client_uid, *, preflights=1, seconds=BUDGET, acknowledgment=None):
    requests, seconds = session_policy(preflights, seconds, acknowledgment)
    # Never launch this from preflight. No interpreter/site-packages from a user venv.
    if sys.platform!='linux' or os.geteuid()!=pwd.getpwnam('postgres').pw_uid: reject('operator must use PostgreSQL OS user')
    if type(client_uid) is not int or client_uid<=0 or client_uid==os.geteuid(): reject('client UID invalid')
    if not sys.flags.isolated: reject('isolated system interpreter required')
    secure_path(Path(sys.executable).resolve(),0)
    own_dir=Path(__file__).resolve().parent
    for parent in [own_dir,*own_dir.parents]: secure_path(parent,0,True)
    for name in ('ubuntu_evidence_helper.py','ubuntu_os_evidence.py'): secure_path(own_dir/name,0)
    secure_parent()
    if SOCKET.exists() or SOCKET.is_symlink(): reject('socket already exists; operator review required')
    server=None
    created=None
    stage='socket-setup'
    request_ordinal=None
    deadline=time.monotonic()+seconds
    baseline=None
    try:
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        server.bind(str(SOCKET));created=SOCKET.lstat().st_ino
        os.chmod(SOCKET,0o666)  # Future explicitly approved helper node only; peer UID gates requests.
        server.listen(2)
        # Finite operator-approved capacity; never restart or extend automatically.
        for request_index in range(requests):
            request_ordinal=request_index % 2 + 1
            stage='accept'
            if time.monotonic() >= deadline: reject('session expired')
            server.settimeout(max(.001,deadline-time.monotonic()))
            conn,_=server.accept()
            with conn:
                stage='peer-authentication'
                conn.settimeout(min(5,max(.001,deadline-time.monotonic())))
                if peer_uid(conn)!=client_uid: reject('client identity mismatch')
                if time.monotonic()>=deadline: reject('session expired')
                stage='receive'
                request=receive(conn, deadline)
                if set(request)!={'nonce','backend_pid'} or not isinstance(request['nonce'],str) or len(request['nonce'])!=64:
                    reject('request shape invalid')
                try: bytes.fromhex(request['nonce'])
                except ValueError: reject('challenge invalid')
                pid=request['backend_pid']
                if pid is not None and (type(pid) is not int or pid<=0): reject('backend identifier invalid')
                def bounded_command(args):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: reject('session expired')
                    return collector.command(args, timeout=min(collector.TIMEOUT, remaining))
                stage='evidence-collection'
                evidence=collector.collect(command=bounded_command)
                stage='instance-signature'
                signature=instance_signature(evidence)
                if baseline is not None and signature != baseline:
                    reject('cluster changed during operator session')
                baseline=signature
                stage='backend-inspection'
                backend=collector.process_identity(pid) if pid is not None else None
                if backend and (backend['parent_pid']!=evidence['process']['pid'] or backend['uid']!=evidence['postgres_uid'] or backend['executable']!=collector.EXECUTABLE):
                    reject('backend outside isolated cluster')
                stage='response-serialization'
                payload=json.dumps({'nonce':request['nonce'],'evidence':evidence,'backend':backend}).encode()+b'\n'
                if len(payload)>MAX_BYTES or time.monotonic()>=deadline: reject('evidence budget exceeded')
                stage='response-transmission'
                conn.settimeout(max(.001, deadline-time.monotonic()))
                conn.sendall(payload)
    except collector.EvidenceError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        inspection_failure(stage, request_ordinal, error)
    finally:
        if server is not None: server.close()
        if created is not None:
            try:
                node=SOCKET.lstat()
                if node.st_ino==created and node.st_uid==os.geteuid() and stat.S_ISSOCK(node.st_mode): SOCKET.unlink()
            except OSError: pass


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Operator-approved read-only OS evidence; no database access')
    parser.add_argument('--client-uid',type=int,required=True)
    parser.add_argument('--preflights',type=int,default=1)
    parser.add_argument('--session-seconds',type=int,default=BUDGET)
    parser.add_argument('--session-ack')
    args=parser.parse_args()
    try: serve(args.client_uid, preflights=args.preflights, seconds=args.session_seconds,
               acknowledgment=args.session_ack)
    except collector.EvidenceError as error:
        print(str(error));raise SystemExit(1) from None
    except Exception:
        print('PostgreSQL preflight rejected: Ubuntu privileged helper inspection unavailable')
        raise SystemExit(1) from None
