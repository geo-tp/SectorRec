from src.services.signature import extraction_totals


def test_signature_recent_files_are_bounded_and_do_not_follow_symlinks(tmp_path):
    prefix=tmp_path/'SectorRec_1';folder=tmp_path/'SectorRec_1.1';folder.mkdir()
    (folder/'photo.jpg').write_bytes(b'123')
    (folder/'report.pdf').write_bytes(b'12')
    (folder/'escape').symlink_to(tmp_path)
    recent=[]
    assert extraction_totals(tmp_path,prefix,recent)==(2,5)
    assert {x[0] for x in recent}=={'photo.jpg','report.pdf'}
    assert all(len(row)==4 for row in recent)
