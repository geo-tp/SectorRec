"""Optional PC-only, read-only block-device mount guard.

On Linux, O_RDONLY|O_EXCL on a block device prevents an automounter
from mounting that device for the lifetime of the open FD.  Privileged
validation and the exclusive open occur in the existing root reader,
which holds no write-capable descriptor.  Losing the GUI's stdin pipe
ends the privileged holder automatically.  Cardputer never uses this.
"""
from __future__ import annotations
import selectors
import subprocess
import os
from src.infrastructure.privileged import helper_command, stop_proc, isolated_process_group

class SourceMountGuard:
    def __init__(self, source: str, process):
        self.source, self.process = source, process

    @classmethod
    def acquire(cls, source: str, timeout: float = 5.0):
        if not source.startswith('/dev/'):
            raise ValueError('A source mount guard requires a Linux block device')
        command = helper_command('hold_source', source)
        # The worker has its own process group for cancellation, but MUST
        # remain in the GUI session for Polkit's active-session authorization.
        # A user reading the password prompt may need substantially longer than
        # the 5 seconds allowed for an already-authorized helper.
        privileged = (os.path.basename(command[0]) in ('pkexec', 'sudo') or '--proxy' in command)
        if privileged:
            timeout = max(timeout, 300.0)
        process = subprocess.Popen(command,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, **isolated_process_group())
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(timeout):
                    raise RuntimeError('Source reservation timed out; check the authorization dialog or close programs using this drive')
                answer = process.stdout.readline().decode('utf-8', 'replace').strip()
            if answer != 'READY':
                detail = process.stderr.read(600).decode('utf-8', 'replace').strip() if process.poll() is not None else ''
                raise RuntimeError(detail or 'Source was re-mounted or could not be reserved safely')
            return cls(source, process)
        except BaseException:
            stop_proc(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream: stream.close()
            raise

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def release(self):
        process = self.process
        if process is None: return
        self.process = None
        if process.stdin:
            try: process.stdin.close()
            except OSError: pass
        try:
            process.wait(timeout=0.8)
        except subprocess.TimeoutExpired:
            stop_proc(process)
        for stream in (process.stdout, process.stderr):
            if stream: stream.close()
