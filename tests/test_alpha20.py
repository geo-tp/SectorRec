import tempfile
import unittest
from pathlib import Path
from unittest import mock
from src.controllers.controller import Controller as UI

class ImageOrderingTests(unittest.TestCase):
    def test_partitioned_image_detected_and_first(self):
        # Test the disk layout classification with a minimally marked MBR.
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'test.img'; data=bytearray(1024)
            data[446+4]=0x0b;data[510:512]=b'\x55\xaa';img.write_bytes(data)
            ui=UI.__new__(UI); ui.images=[];ui.devices=[{'path':'/dev/sdz'}];ui.selection=0
            item=ui.add_image(str(img))
            self.assertTrue(item['partition_disk'])
            self.assertEqual(ui.devices[0]['path'],str(img))
            self.assertEqual(ui.selection,0)
    def test_raw_image_not_assumed_partitioned(self):
        with tempfile.TemporaryDirectory() as d:
            img=Path(d)/'fs.img';img.write_bytes(b'\0'*1024)
            ui=UI.__new__(UI);ui.images=[];ui.devices=[];ui.selection=0
            self.assertFalse(ui.add_image(str(img))['partition_disk'])
