"""Send real Ctrl+C only inside a new, hidden test console, never the user's."""
import argparse
import ctypes
import json
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request


def worker(executable, output):
    signal.signal(signal.SIGINT, lambda *_: None)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for guarded in (True, False):
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            config = root / 'settings.yaml'
            config.write_text(f'listen:\n  port: {port}\ninput:\n  interrupt_guard: {str(guarded).lower()}\n', encoding='utf-8')
            with (root / 'process.log').open('w', encoding='utf-8') as log:
                child = subprocess.Popen([executable, '--headless', '--config', str(config)], cwd=root, stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 90
                    while True:
                        try:
                            with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1) as response:
                                assert response.status == 200
                            break
                        except OSError:
                            if child.poll() is not None or time.monotonic() > deadline:
                                raise RuntimeError((root / 'process.log').read_text(encoding='utf-8'))
                            time.sleep(.2)
                    assert ctypes.windll.kernel32.GenerateConsoleCtrlEvent(0, 0)
                    if guarded:
                        time.sleep(7)  # Exceed Nuitka's normal child grace period.
                        assert child.poll() is None, 'Ctrl+C killed the guarded process'
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2) as response:
                            assert response.status == 200
                    else:
                        exit_code = child.wait(timeout=20)
                        # Nuitka waits for its child then returns FALSE from its
                        # console handler. Windows may terminate that parent with
                        # STATUS_CONTROL_C_EXIT instead of the child's zero code.
                        assert exit_code in (0, 0xC000013A, -1073741510), f'Unexpected Ctrl+C exit: {exit_code}'
                        try:
                            urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2).close()
                        except urllib.error.URLError:
                            pass
                        else:
                            raise AssertionError('Unguarded Ctrl+C left the HTTP server running')
                except Exception as exc:
                    details = (root / 'process.log').read_text(encoding='utf-8', errors='replace')
                    raise RuntimeError(f'guarded={guarded}, child_exit={child.poll()}: {exc}\n{details}') from exc
                finally:
                    if child.poll() is None:
                        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                        child.wait(timeout=10)
    output.write_text(json.dumps({'ctrl_c_guarded': 'pass', 'ctrl_c_exit': 'pass'}), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('executable', type=Path)
    parser.add_argument('--worker', type=Path)
    args = parser.parse_args()
    executable = str(args.executable.resolve())
    if args.worker:
        try:
            worker(executable, args.worker)
        except BaseException:
            args.worker.write_text(json.dumps({'error': traceback.format_exc()}), encoding='utf-8')
            raise
        return
    with tempfile.TemporaryDirectory() as temporary:
        result = Path(temporary) / 'result.json'
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), executable, '--worker', str(result)], creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=240)
        if process.returncode:
            details = result.read_text(encoding='utf-8') if result.exists() else process.stdout + process.stderr
            raise RuntimeError(f'Isolated Ctrl+C test failed ({process.returncode}):\n{details}')
        print('SIGNAL TEST PASS:', result.read_text(encoding='utf-8'))


if __name__ == '__main__':
    main()
