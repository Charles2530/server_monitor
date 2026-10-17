"""Synchronize source code into the macOS LaunchAgent runtime."""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import urlopen

SOURCE = Path(__file__).resolve().parent.parent
RUNTIME = Path.home() / 'Library/Application Support/CharlesCloudMonitor'
LABEL = 'charles.server-monitor'
DOMAIN = 'gui/' + str(os.getuid())


def source_digest(root):
    digest = hashlib.sha256()
    for directory in ('server', 'static'):
        base = root / directory
        if not base.exists():
            continue
        for path in sorted((item for item in base.rglob('*') if item.is_file() and '__pycache__' not in item.parts),
                           key=lambda item: item.relative_to(root).as_posix()):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b'\0')
            digest.update(path.read_bytes())
            digest.update(b'\0')
    return digest.digest()


def copy_sources(source=SOURCE, runtime=RUNTIME):
    """Copy code and static assets while preserving runtime-only data."""
    for directory in ('server', 'static'):
        destination = runtime / directory
        if directory == 'static':
            shutil.rmtree(destination / 'editor-appearance', ignore_errors=True)
        shutil.copytree(source / directory, destination, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))


def restart_service():
    target = DOMAIN + '/' + LABEL
    if subprocess.run(['/bin/launchctl', 'print', target], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode != 0:
        return False
    subprocess.run(['/bin/launchctl', 'kickstart', '-k', target], check=True)
    return True


def wait_for_api():
    for _ in range(50):
        try:
            with urlopen('http://127.0.0.1:8766/api/state', timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(.2)


def sync_once(source=SOURCE, runtime=RUNTIME):
    for _ in range(3):
        expected = source_digest(source)
        if expected == source_digest(runtime):
            return False
        copy_sources(source, runtime)
        if expected == source_digest(runtime):
            return restart_service()
        time.sleep(.1)
    raise RuntimeError('运行副本同步后校验不一致。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--runtime', type=Path, default=RUNTIME)
    args = parser.parse_args(argv)
    if os.name != 'posix' or not args.source.is_dir() or not args.runtime.is_dir():
        return
    lock_path = args.source / 'data' / 'controller.lock'
    lock_path.parent.mkdir(exist_ok=True, mode=0o700)
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        restarted = sync_once(args.source, args.runtime)
    if restarted:
        wait_for_api()
        print('源码已自动同步并重启：http://127.0.0.1:8766')


if __name__ == '__main__':
    main()
