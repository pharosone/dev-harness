"""Adaptive host capacity admission; no slots, semaphore, services or cache changes.

Call wait_for_capacity only on execution paths and pass its returned environment
into heavy subprocesses. heavy=False skips all configuration, sensors and markers.
Overlay project.json load_guard keys/defaults: enabled=True, load_per_core=1.5,
max_wait_seconds=300, backoff_seconds=2, min_available_percent=10 (Linux),
max_memory_some_percent=10 and max_memory_full_percent=1 (Linux PSI avg10).
HARNESS_LOAD_GUARD=0/1 overrides enabled; numeric overrides are HARNESS_LOAD_PER_CORE,
HARNESS_LOAD_MAX_WAIT_SECONDS, HARNESS_LOAD_BACKOFF_SECONDS and
HARNESS_LOAD_MIN_AVAILABLE_PERCENT, HARNESS_LOAD_MEMORY_SOME_PERCENT and
HARNESS_LOAD_MEMORY_FULL_PERCENT. HARNESS_LOAD_GUARD_STATE_DIR overrides the
same-user host /tmp registry. HARNESS_LOAD_GUARD_ANCESTOR is internal: PID/start
identity verified against actual ancestry, never a blindly trusted boolean.
Missing sensors fail open individually; known pressure still waits. macOS uses
native VM pressure (1 normal); Linux uses MemAvailable, including reusable cache,
plus host /proc/pressure/memory stalls (read-only; no PSI trigger registration).
"""
from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid

ANCESTOR = 'HARNESS_LOAD_GUARD_ANCESTOR'
DEFAULTS = dict(enabled=True, load_per_core=1.5, max_wait_seconds=300,
                backoff_seconds=2, min_available_percent=10,
                max_memory_some_percent=10, max_memory_full_percent=1)
OVERRIDES = {'load_per_core': 'HARNESS_LOAD_PER_CORE',
             'max_wait_seconds': 'HARNESS_LOAD_MAX_WAIT_SECONDS',
             'backoff_seconds': 'HARNESS_LOAD_BACKOFF_SECONDS',
             'min_available_percent': 'HARNESS_LOAD_MIN_AVAILABLE_PERCENT',
             'max_memory_some_percent': 'HARNESS_LOAD_MEMORY_SOME_PERCENT',
             'max_memory_full_percent': 'HARNESS_LOAD_MEMORY_FULL_PERCENT'}


@dataclass(frozen=True)
class Capacity:
    load: float | None
    cores: int
    memory_normal: bool | None
    memory_sensors: tuple[tuple[str, bool | None], ...] = ()


def enabled(value, where):
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in ('0', '1', 'false', 'true', 'off', 'on'):
        return value.lower() in ('1', 'true', 'on')
    raise ValueError(f'{where}: expected boolean or 0/1')


def config(root, env=None):
    env = os.environ if env is None else env
    # Honor a kill switch even if a project has unusable guard configuration.
    if 'HARNESS_LOAD_GUARD' in env and not enabled(env['HARNESS_LOAD_GUARD'], 'HARNESS_LOAD_GUARD'):
        return {**DEFAULTS, 'enabled': False}
    overlay = Path(env.get('HARNESS_PROJECT') or 'harness/project')
    overlay = overlay if overlay.is_absolute() else Path(root) / overlay
    path = overlay / 'project.json'
    data = json.loads(read_limited(path)) if path.is_file() else {}
    if not isinstance(data, dict):
        raise ValueError('project.json: expected an object')
    section = data.get('load_guard', {})
    if not isinstance(section, dict) or section.keys() - DEFAULTS.keys():
        raise ValueError('project.json load_guard: expected an object with known keys')
    result = {**DEFAULTS, **section}
    if type(result['enabled']) is not bool:
        raise ValueError('load_guard.enabled: expected a boolean')
    if 'HARNESS_LOAD_GUARD' in env:
        result['enabled'] = enabled(env['HARNESS_LOAD_GUARD'], 'HARNESS_LOAD_GUARD')
    if not result['enabled']:
        return result
    limits = {'load_per_core': (0, 64), 'max_wait_seconds': (0, 3600),
              'backoff_seconds': (0.05, 10), 'min_available_percent': (0, 100),
              'max_memory_some_percent': (0, 100), 'max_memory_full_percent': (0, 100)}
    for key, variable in OVERRIDES.items():
        value = env.get(variable, result[key])
        where = variable if variable in env else 'load_guard.' + key
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ValueError(f'{where}: expected a finite number') from None
        low, high = limits[key]
        if isinstance(value, bool) or not math.isfinite(numeric) or not low <= numeric <= high \
                or (key in ('load_per_core', 'backoff_seconds') and numeric <= 0):
            raise ValueError(f'{where}: expected a finite number in {low}..{high}'
                             + (' (greater than zero)' if key in ('load_per_core', 'backoff_seconds') else ''))
        result[key] = numeric
    return result


def read_limited(path, limit=65536):
    with Path(path).open(encoding='utf-8') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('probe/config file exceeds size limit')
    return value


def probe(argv, timeout=0.25, env=None):
    """Only fixed, read-only OS commands, never command lines or process environments."""
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL,
                          env={**(os.environ if env is None else env), 'LC_ALL': 'C'})
    if done.returncode or len(done.stdout) > 65536:
        return None
    return done.stdout.strip()


def memory_available(min_available_percent):
    try:
        values = dict(re.findall(r'^(MemTotal|MemAvailable):\s*(\d+)\s+kB$',
                                 read_limited('/proc/meminfo'), re.MULTILINE))
        total, available = int(values['MemTotal']), int(values['MemAvailable'])
        if total > 0 and 0 <= available <= total:
            return available / total * 100 >= min_available_percent
    except (OSError, ValueError, KeyError):
        pass
    return None


def memory_stalls(some_percent, full_percent):
    """PSI percentages over ten seconds; unavailable/disabled PSI stays unknown."""
    try:
        values = {}
        for name, raw in re.findall(r'^(some|full)\s+avg10=(\S+)',
                                    read_limited('/proc/pressure/memory'), re.MULTILINE):
            with suppress(ValueError):
                value = float(raw)
                if math.isfinite(value) and 0 <= value <= 100:
                    values[name] = value
        if values.get('some', 0) > some_percent or values.get('full', 0) > full_percent:
            return False
        if set(values) == {'some', 'full'}:
            return True
    except (OSError, ValueError):
        pass
    return None


def sample_capacity(min_available_percent=10, max_memory_some_percent=10, max_memory_full_percent=1):
    load = None
    with suppress(OSError, AttributeError, ValueError):
        value = os.getloadavg()[0]
        if math.isfinite(value) and value >= 0:
            load = value
    cores = os.cpu_count() or 1
    if sys.platform == 'darwin':
        normal = None
        try:
            value = probe(['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'])
            if value in ('1', '2', '4'):
                normal = value == '1'
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        sensors = (('native_pressure', normal),)
    elif sys.platform.startswith('linux'):
        sensors = (('mem_available', memory_available(min_available_percent)),
                   ('memory_psi', memory_stalls(max_memory_some_percent, max_memory_full_percent)))
    else:
        sensors = (('memory_pressure', None),)
    statuses = [value for _, value in sensors if value is not None]
    normal = all(statuses) if statuses else None
    return Capacity(load, cores, normal, sensors)


def process_info(pid):
    """Parent PID and start identity, or None for dead/zombie/unknown processes."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        if sys.platform.startswith('linux'):
            value = read_limited(f'/proc/{pid}/stat')
            fields = value[value.rindex(')') + 2:].split()
            if fields[0] in ('Z', 'X'):
                return None
            boot = read_limited('/proc/sys/kernel/random/boot_id').strip()
            return int(fields[1]), f'{boot}:{fields[19]}'
        if sys.platform == 'darwin':
            value = probe(['/bin/ps', '-p', str(pid), '-o', 'ppid=', '-o', 'stat=', '-o', 'lstart='])
            if value:
                parent, state, start = value.split(None, 2)
                if not state.startswith('Z'):
                    return int(parent), start.strip()
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return None


def descendant(pid, ancestor, start=None):
    """Bounded actual ancestry check; unrelated live PIDs never count as nested."""
    if type(ancestor) is not int or ancestor <= 0:
        return False
    deadline = time.monotonic() + 2
    seen = set()
    for _ in range(64):
        if pid <= 0 or pid in seen or time.monotonic() > deadline:
            break
        seen.add(pid)
        info = process_info(pid)
        if info is None:
            break
        if pid == ancestor:
            return start is None or info[1] == start
        pid = info[0]
    return False


def nested(env):
    try:
        raw = env.get(ANCESTOR, '')
        if len(raw) > 512:
            return False
        mark = json.loads(raw)
        return isinstance(mark, dict) and isinstance(mark.get('start'), str) \
            and descendant(os.getpid(), mark.get('pid'), mark['start'])
    except (ValueError, TypeError):
        return False


def registry(env):
    base = Path('/tmp') if os.name == 'posix' else Path(tempfile.gettempdir())
    user = str(os.getuid()) if hasattr(os, 'getuid') else 'user'
    override = env.get('HARNESS_LOAD_GUARD_STATE_DIR')
    if override is not None and (not isinstance(override, str) or not override.strip() or '\x00' in override):
        raise ValueError('HARNESS_LOAD_GUARD_STATE_DIR: expected a nonempty directory path without NUL')
    return Path(override if override is not None else base / f'harness-load-guard-{user}')


def private_registry(folder):
    """Inspect without chmod: user-selected existing directories belong to the user."""
    if folder.is_symlink():
        return False
    info = folder.stat()
    return (not hasattr(os, 'getuid') or info.st_uid == os.getuid()) and not info.st_mode & 0o077


def safe_label(label):
    # Arbitrary caller labels can be commands/credentials: never persist or print them.
    return label if label in ('gate', 'skill-evals', 'xreview') else 'heavy job'


def current_pane(env):
    """Caller-context lookup only. Never guess IDs or inspect/type another pane."""
    def valid(value):
        return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_:.-]{1,64}', value)
    pane = env.get('HERDR_PANE_ID')
    if valid(pane):
        return pane
    if env.get('HERDR_ENV') == '1':
        try:
            raw = probe(['herdr', 'pane', 'current', '--current'], env=env)
            data = json.loads(raw) if raw else {}
            pane = data['result']['pane']['pane_id']
            if valid(pane):
                return pane
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            pass
    return None


def wait_record(root, run, label, env, capacity, seconds, info):
    if info is None:
        return None
    path = None
    try:
        folder = registry(env)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not private_registry(folder):
            return None
        job = uuid.uuid4().hex
        now = time.time()
        pane = current_pane(env)
        if pane is None and env.get('HERDR_ENV') == '1':
            print('load guard: waiting-job lane association unknown (Herdr current pane unavailable)',
                  file=sys.stderr, flush=True)
        record = {'id': job, 'pid': os.getpid(), 'start': info[1], 'host': socket.gethostname(),
                  'label': safe_label(label), 'since': now, 'expires': now + seconds + 5,
                  'root': str(Path(root).resolve()), 'run': str(Path(run).resolve()) if run else None,
                  'pane_id': pane,
                  'load': capacity.load, 'cores': capacity.cores,
                  'memory_normal': capacity.memory_normal}
        path = folder / ('wait-' + job + '.json')
        temp = folder / ('wait-' + job + '.tmp')
        try:
            with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                json.dump(record, stream)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        return path
    except (OSError, ValueError):
        return None  # Monitoring is advisory; an unwritable registry cannot stop admission.


def waiting_jobs(*, env=None):
    """Live host-wide waits, independent of root/worktree. Remove invalid/stale records."""
    env = os.environ if env is None else env
    try:
        folder = registry(env)
        if not folder.is_dir():
            return []
        if not private_registry(folder):
            print('load guard: waiting-job monitoring unavailable (registry is not private)', file=sys.stderr)
            return []
    except (OSError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else 'registry cannot be read'
        print(f'load guard: waiting-job monitoring unavailable ({reason})', file=sys.stderr)
        return []
    jobs = []
    now = time.time()
    deadline = time.monotonic() + 2
    try:
        for index, path in enumerate(folder.glob('wait-*.json')):
            if index >= 256 or time.monotonic() > deadline:
                break
            if not re.fullmatch(r'wait-[0-9a-f]{32}\.json', path.name):
                continue  # Never prune unrelated files in a user-selected directory.
            try:
                if path.is_symlink():
                    raise ValueError('symlink')
                job = json.loads(read_limited(path, 16384))
                if not isinstance(job, dict) or job.get('host') != socket.gethostname() \
                        or not isinstance(job.get('start'), str) \
                        or not isinstance(job.get('id'), str) \
                        or not re.fullmatch(r'[0-9a-f]{32}', job['id']):
                    raise ValueError('invalid wait')
                since, expires = job['since'], job['expires']
                if type(since) not in (int, float) or type(expires) not in (int, float) \
                        or not math.isfinite(since) or not math.isfinite(expires) \
                        or not since <= now + 5 or not now < expires <= since + 3605:
                    raise ValueError('expired wait')
                info = process_info(job.get('pid'))
                if info is None or info[1] != job['start']:
                    raise ValueError('dead/reused PID')
                # Only the known metadata schema reaches consumers, even for hand-edited files.
                jobs.append({key: job.get(key) for key in (
                    'id', 'pid', 'start', 'since', 'expires', 'root', 'run', 'pane_id',
                    'load', 'cores', 'memory_normal')} | {'label': safe_label(job.get('label'))})
            except (OSError, ValueError, KeyError, TypeError):
                with suppress(OSError):
                    path.unlink()
    except OSError:
        pass
    return jobs


def wait_for_capacity(root, *, heavy=True, label='', run=None, env=None):
    """Return a complete child environment; bounded busy wait, no fixed slot count."""
    environment = dict(os.environ if env is None else env)
    if not heavy:
        return environment
    if 'HARNESS_LOAD_GUARD' in environment and not enabled(environment['HARNESS_LOAD_GUARD'], 'HARNESS_LOAD_GUARD'):
        return environment
    if nested(environment):
        return environment
    settings = config(root, environment)
    if not settings['enabled']:
        return environment
    started = time.monotonic()
    deadline = started + settings['max_wait_seconds']
    info = process_info(os.getpid())
    record = None
    announced = False
    unknown = set()
    try:
        while True:
            capacity = sample_capacity(settings['min_available_percent'],
                                       settings['max_memory_some_percent'], settings['max_memory_full_percent'])
            for sensor, status in capacity.memory_sensors:
                if status is None and sensor not in unknown:
                    print(f'load guard: {sensor} sensor unknown (fail-open)', file=sys.stderr, flush=True)
                    unknown.add(sensor)
            busy = (capacity.load is not None and capacity.load >= capacity.cores * settings['load_per_core']) \
                or capacity.memory_normal is False
            if not busy:
                break
            remaining = max(0, deadline - time.monotonic())
            if not announced:
                load = f'{capacity.load:g}' if capacity.load is not None else 'unknown'
                print(f'machine busy (load {load} / {capacity.cores} cores), waiting up to '
                      f'{settings["max_wait_seconds"]:g} s'
                      + ('; memory pressure' if capacity.memory_normal is False else ''),
                      file=sys.stderr, flush=True)
                announced = True
                if remaining:
                    record = wait_record(root, run, label, environment, capacity, remaining, info)
                    if record is None:
                        print('load guard: waiting-job monitoring unavailable', file=sys.stderr, flush=True)
            if not remaining:
                print('machine busy; maximum wait reached, starting anyway', file=sys.stderr, flush=True)
                break
            time.sleep(min(settings['backoff_seconds'], remaining))
    finally:
        if record:
            with suppress(OSError):
                record.unlink()
    environment.pop(ANCESTOR, None)
    if info:
        environment[ANCESTOR] = json.dumps({'pid': os.getpid(), 'start': info[1]}, separators=(',', ':'))
    else:
        # Only the returned child environment changes. Identity probes are advisory:
        # failure must never make an admitted process tree wait again or deadlock.
        environment['HARNESS_LOAD_GUARD'] = '0'
        print('load guard: process identity unknown; disabling further admission in returned child environment',
              file=sys.stderr, flush=True)
    return environment
