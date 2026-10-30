"""Imaging cancellation and guard ownership without a GUI or real devices."""
from unittest.mock import Mock

import pytest

from src.controllers.imaging import ImagingJob


@pytest.fixture
def imaging(monkeypatch, tmp_path):
    monkeypatch.setattr('src.controllers.imaging.sys.platform', 'linux')
    unmount = Mock()
    settle = Mock()
    acquire = Mock()
    worker = Mock()
    monkeypatch.setattr('src.infrastructure.linux_devices.unmount_disk_children', unmount)
    monkeypatch.setattr('src.infrastructure.linux_devices.wait_for_unmounted_source', settle)
    monkeypatch.setattr('src.infrastructure.source_guard.SourceMountGuard.acquire', acquire)
    monkeypatch.setattr('src.services.image_rescue.ImageWorker', worker)
    source = {'path': '/dev/mock', 'size': 1000, 'partition_disk': True,
              'imaging_identity': {'serial': 'original'}}
    job = ImagingJob(source, tmp_path / 'image.img', {'state': 'preparing'})
    return job, unmount, settle, acquire, worker


def test_cancel_before_preparation_never_touches_source(imaging):
    job, unmount, settle, acquire, worker = imaging
    job.cancel()
    job.run()
    assert job.status['state'] == 'cancelled'
    for operation in (unmount, settle, acquire, worker):
        operation.assert_not_called()


def test_cancel_during_preparation_never_launches_worker(imaging):
    job, unmount, settle, acquire, worker = imaging
    unmount.side_effect = lambda _source, **_kwargs: job.cancel()
    job.run()
    assert job.status['state'] == 'cancelled'
    acquire.assert_not_called()
    worker.assert_not_called()


def test_cancel_after_guard_acquisition_releases_guard(imaging):
    job, unmount, settle, acquire, worker = imaging
    guard = Mock()

    def acquire_and_cancel(_source):
        job.cancel()
        return guard

    acquire.side_effect = acquire_and_cancel
    job.run()
    assert job.status['state'] == 'cancelled'
    guard.release.assert_called_once_with()
    worker.assert_not_called()


def test_worker_failure_releases_guard_and_preserves_error(imaging):
    job, unmount, settle, acquire, worker = imaging
    worker.return_value.run.side_effect = RuntimeError('copy failed')
    job.run()
    assert job.status == {'state': 'error', 'error': 'copy failed'}
    acquire.return_value.release.assert_called_once_with()


def test_cancel_running_worker_keeps_guard_until_worker_returns(imaging):
    job, unmount, settle, acquire, worker = imaging

    def copy():
        job.cancel()
        worker.return_value.cancel.assert_called_once_with()
        acquire.return_value.release.assert_not_called()
        job.status['state'] = 'cancelled'

    worker.return_value.run.side_effect = copy
    job.run()
    assert job.status['state'] == 'cancelled'
    acquire.return_value.release.assert_called_once_with()


def test_windows_passes_resume_identity_without_linux_preparation(imaging, monkeypatch):
    job, unmount, settle, acquire, worker = imaging
    monkeypatch.setattr('src.controllers.imaging.sys.platform', 'win32')
    job.source['path'] = r'\\.\PhysicalDrive3'
    job.resume = True
    job.run()
    worker.assert_called_once_with(
        job.source['path'], job.output, 1000, job.status, resume=True,
        expected_identity=job.source['imaging_identity'], source_info=job.source,
    )
    worker.return_value.run.assert_called_once_with()
    for operation in (unmount, settle, acquire):
        operation.assert_not_called()


def test_imaging_whole_disk_unmount_can_use_session_helper(imaging):
    job, unmount, settle, acquire, worker = imaging
    job.run()
    unmount.assert_called_once()
    assert unmount.call_args.args == ('/dev/mock',)
    from src.infrastructure.privileged import helper_command
    assert unmount.call_args.kwargs['helper_command'] is helper_command


def test_imaging_mounted_partition_can_use_session_helper(imaging, monkeypatch):
    job, unmount, settle, acquire, worker = imaging
    job.source.update(partition_disk=False, mounted=True)
    from unittest.mock import Mock
    unmount_volume = Mock()
    monkeypatch.setattr('src.infrastructure.linux_devices.unmount_volume', unmount_volume)
    job.run()
    unmount.assert_not_called()
    unmount_volume.assert_called_once()
    from src.infrastructure.privileged import helper_command
    assert unmount_volume.call_args.args == ('/dev/mock',)
    assert unmount_volume.call_args.kwargs['helper_command'] is helper_command
