"""Cancellable disk-image preparation and worker lifecycle, independent of Tk."""
import sys
import threading
import errno


def _transient_mount_conflict(exc):
    """Only retry a mount/open race, never an authentication or identity error."""
    if isinstance(exc, OSError) and exc.errno == errno.EBUSY:
        return True
    message = str(exc).lower()
    return any(phrase in message for phrase in (
        'device or resource busy', 'errno 16', 'mounted again',
        'another process may have remounted', 'drive busy',
    ))


class ImagingJob(threading.Thread):
    """Own source preparation, ddrescue and the guard until cleanup completes.

    The view registers this job before starting it and observes ``status``.
    Cancellation remains effective during preparation, before an ImageWorker
    exists. ``is_alive()`` includes guard release, so closing the view can wait
    for the entire operation without managing individual workers.
    """

    def __init__(self, source, output, status, resume=False):
        super().__init__(daemon=True, name='sectorrec-imaging')
        self.source = source
        self.output = output
        self.status = status
        self.resume = resume
        self.cancel_event = threading.Event()
        self.worker = None

    def cancel(self):
        self.cancel_event.set()
        if self.worker is not None:
            self.worker.cancel()

    def _cancelled(self):
        if self.cancel_event.is_set():
            self.status['state'] = 'cancelled'
            return True
        return False

    def run(self):
        from src.services.image_rescue import ImageWorker

        source = self.source
        guard = None
        try:
            if self._cancelled():
                return
            if sys.platform.startswith('linux'):
                from src.infrastructure.linux_devices import (
                    unmount_disk_children, unmount_volume, wait_for_unmounted_source,
                )
                from src.infrastructure.privileged import helper_command
                from src.infrastructure.source_guard import SourceMountGuard
                # Automounters can race *after* the stable-unmount check. Recheck
                # and unmount before each bounded O_EXCL attempt; never bypass
                # the exclusive open or proceed with a mounted/busy source.
                for attempt in range(3):
                    if self._cancelled():
                        return
                    try:
                        if source.get('partition_disk'):
                            unmount_disk_children(source['path'], helper_command=helper_command)
                        else:
                            # The selection snapshot may be stale: a partition
                            # that was unmounted earlier can be mounted now.
                            unmount_volume(source['path'], helper_command=helper_command)
                        wait_for_unmounted_source(source['path'], expected=source)
                        if self._cancelled():
                            return
                        guard = SourceMountGuard.acquire(source['path'])
                        break
                    except (OSError, RuntimeError) as exc:
                        if not _transient_mount_conflict(exc):
                            raise
                        if attempt == 2:
                            raise RuntimeError(
                                'Could not reserve the unmounted source after 3 attempts. '
                                'Close the file manager or other programs using the drive, '
                                'then retry. Original error: ' + str(exc)
                            ) from exc
                        # Interruptible backoff; cancellation must not wait on a
                        # long USB-automounter retry cycle.
                        if self.cancel_event.wait(0.4 * (attempt + 1)):
                            self.status['state'] = 'cancelled'
                            return
            if self._cancelled():
                return
            self.worker = ImageWorker(
                source['path'], self.output, int(source['size']), self.status,
                resume=self.resume, expected_identity=source.get('imaging_identity'),
                **({'source_info': source} if sys.platform == 'win32' else {}),
            )
            if self._cancelled():
                self.worker.cancel()
                return
            self.worker.run()
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.update(
                state='cancelled' if self.cancel_event.is_set() else 'error',
                error=str(exc),
            )
        finally:
            if guard:
                guard.release()
