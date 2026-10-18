"""Live Ubuntu cluster evidence and pure validation. No provisioning or elevation.

Only locally collected evidence is consumed; no evidence files or bypass flags.
Missing /proc, ss, cluster or restricted SQL metadata fails closed.
"""
from pathlib import Path
import re
import subprocess
import sys

class EvidenceError(ValueError):
    pass

DIRECTORY = '/var/lib/postgresql/16/stockbridge-test'
EXECUTABLE = '/usr/lib/postgresql/16/bin/postgres'
PORT = 55432
TIMEOUT = 10


def reject(category):
    raise EvidenceError('PostgreSQL preflight rejected: Ubuntu ' + category)


def read_text(path):
    try:
        return Path(path).read_text()
    except (OSError, UnicodeError):
        reject('OS metadata unavailable')


def command(args, *, timeout=TIMEOUT):
    # Absolute tool paths, no shell, bounded waits; never return output in errors.
    if timeout <= 0: reject('OS inspection deadline exceeded')
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=min(TIMEOUT, timeout), check=False,
                                env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'})
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        reject('bounded OS inspection unavailable')
    if result.returncode or result.stderr.strip():
        reject('OS inspection incomplete')
    return result.stdout


def process_identity(pid):
    if type(pid) is not int or pid <= 0:
        reject('process identifier missing')
    try:
        executable = str(Path(f'/proc/{pid}/exe').resolve(strict=True))
        stat = read_text(f'/proc/{pid}/stat')
        suffix = stat[stat.rindex(')')+2:].split()
        parent, started = int(suffix[1]), int(suffix[19])
        status = read_text(f'/proc/{pid}/status')
        uid = re.search(r'^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$', status, re.M)
        if not uid or len(set(uid.groups())) != 1:
            reject('process ownership ambiguous')
        return {'pid': pid, 'executable': executable, 'parent_pid': parent,
                'start_ticks': started, 'uid': int(uid.group(1))}
    except (OSError, RuntimeError, ValueError, IndexError):
        reject('process identity unavailable')


def parse_sockets(output, protocol):
    sockets = []
    for line in output.splitlines():
        if not line.strip(): continue
        parts = line.split()
        if len(parts) < 5 or parts[0] not in (('LISTEN',) if protocol=='TCP' else ('UNCONN','ESTAB')):
            reject('socket evidence ambiguous')
        endpoint = parts[3]
        address, separator, port = endpoint.rpartition(':')
        if not separator or not port.isdigit(): reject('socket endpoint ambiguous')
        address = address.strip('[]')
        pids = [int(p) for p in re.findall(r'pid=(\d+)', line)]
        sockets.append({'protocol':protocol, 'state':parts[0], 'address':address, 'port':int(port), 'pids':pids})
    return sockets


def collect(read_text=read_text, command=command, process_identity=process_identity):
    if sys.platform != 'linux': reject('Linux operating system required')
    os_release = read_text('/etc/os-release')
    if not re.search(r'^ID=["\']?ubuntu["\']?$', os_release, re.M):
        reject('Ubuntu operating system not established')
    # pg_lsclusters is read-only. Parse its fixed no-header fields conservatively.
    rows = []
    for line in command(['/usr/bin/pg_lsclusters','-h']).splitlines():
        fields = line.split()
        if len(fields) < 7: reject('cluster metadata ambiguous')
        if fields[1] == 'stockbridge-test': rows.append(fields)
    if len(rows) != 1: reject('dedicated cluster identity ambiguous')
    row = rows[0]
    if row[:6] != ['16','stockbridge-test','55432','online','postgres',DIRECTORY]:
        reject('dedicated cluster configuration mismatch')
    try:
        if str(Path(DIRECTORY).resolve(strict=True)) != DIRECTORY:
            reject('data directory symlink or alias rejected')
    except (OSError, RuntimeError):
        reject('data directory identity unavailable')
    pidfile = read_text(DIRECTORY + '/postmaster.pid').splitlines()
    try:
        pid, directory, start, port = int(pidfile[0]), pidfile[1], int(pidfile[2]), int(pidfile[3])
    except (ValueError, IndexError): reject('postmaster identity unavailable')
    import pwd
    try: postgres_uid = pwd.getpwnam('postgres').pw_uid
    except KeyError: reject('PostgreSQL OS owner unavailable')
    arguments=read_text(f'/proc/{pid}/cmdline').split('\0')
    if (not arguments or arguments[0]!=EXECUTABLE or arguments.count('-D')!=1
            or arguments.index('-D')+1>=len(arguments) or arguments[arguments.index('-D')+1]!=DIRECTORY):
        reject('postmaster command identity mismatch')
    return {'os':'linux','distribution':'ubuntu','cluster':'16/stockbridge-test',
            'version_major':16,'directory':directory,'port':port,'postmaster_started':start,
            'postgres_uid':postgres_uid,'process':process_identity(pid),
            'commandline':[EXECUTABLE,'-D',DIRECTORY],
            'sockets':parse_sockets(command(['/usr/bin/ss','-H','-ltnp']),'TCP')
                      + parse_sockets(command(['/usr/bin/ss','-H','-aunp']),'UDP')}
