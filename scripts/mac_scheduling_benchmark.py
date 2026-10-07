"""Compare launchd scheduling on this Mac with isolated, offline app workloads.

Run from the project: .venv/bin/python -m scripts.mac_scheduling_benchmark
Uses the installed internal-disk Python and server code. Temporary jobs run
synthetic DB/API workloads, never collectors or cloud actions, and are unloaded
after each sample. No installed service or machine setting is changed.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import statistics
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--output', type=Path, default=Path('data/mini-scheduling-benchmark.json'))
    args = parser.parse_args()
    if not 1 <= args.rounds <= 10:
        parser.error('rounds must be between 1 and 10')
    runtime = Path.home() / 'Library/Application Support/CharlesCloudMonitor'
    python = runtime / '.venv/bin/python'
    if not python.is_file():
        parser.error('installed internal-disk Python is required')
    domain = 'gui/' + str(os.getuid())
    samples = []
    with tempfile.TemporaryDirectory(prefix='monitor-scheduling-') as folder:
        folder = Path(folder)
        script = folder / 'runtime_benchmark.py'
        shutil.copy2(Path(__file__).with_name('runtime_benchmark.py'), script)
        for round_number in range(args.rounds):
            policies = ['Background', 'Standard', 'Interactive']
            if round_number % 2:
                policies.reverse()
            for policy in policies:
                label = f'charles.scheduling-probe-{os.getpid()}-{len(samples)}'
                output, error = folder / 'output.json', folder / 'error.log'
                output.write_text(''); error.write_text('')
                path = folder / 'job.plist'
                path.write_bytes(plistlib.dumps({
                    'Label':label, 'ProgramArguments':[str(python),str(script)],
                    'WorkingDirectory':str(folder), 'RunAtLoad':True, 'ProcessType':policy,
                    'StandardOutPath':str(output), 'StandardErrorPath':str(error),
                    'EnvironmentVariables':{'PYTHONUNBUFFERED':'1', 'PYTHONPATH':str(runtime)},
                }))
                subprocess.run(['/bin/launchctl', 'bootstrap', domain, str(path)], check=True)
                try:
                    deadline = time.monotonic() + 40
                    while True:
                        try:
                            result = json.loads(output.read_text())
                            break
                        except (OSError, ValueError):
                            if error.stat().st_size:
                                raise RuntimeError('Benchmark child failed: ' + error.read_text()[:1000])
                            if time.monotonic() >= deadline:
                                raise TimeoutError('Benchmark child did not finish')
                            time.sleep(.1)
                    samples.append({'process_type':policy, 'round':round_number + 1, **result})
                    print(json.dumps(samples[-1]), flush=True)
                finally:
                    subprocess.run(['/bin/launchctl', 'bootout', domain + '/' + label], check=True)
    fields = ['repeat_save_median_ms', 'state_api_median_ms', 'history_api_median_ms']
    summary = {policy:{field:round(statistics.median(s[field] for s in samples if s['process_type'] == policy),3)
                      for field in fields} for policy in ['Background','Standard','Interactive']}
    report = {'runtime':str(runtime), 'rounds':args.rounds, 'samples':samples, 'medians':summary}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
