import unittest
from src.controllers.controller import Controller
from src.services.partition import PartitionSearchWorker

class DesktopNoPreselect(unittest.TestCase):
    def test_no_selection_initially(self):
        c=Controller(demo=True,desktop=True)
        self.assertIsNone(c.device)
        self.assertEqual(c.selection,-1)
        c.device_refresh_ready=('ok',[{'path':'/dev/test','label':'USB','size':4096,'fs':'VFAT'}])
        c.apply_device_refresh()
        self.assertIsNone(c.device)
        c.selection=0
        self.assertEqual(c.device['path'],'/dev/test')
        c.close()

    def test_partition_search_not_exclusively_reserved(self):
        from unittest.mock import patch
        c=Controller(demo=True,desktop=True)
        c.devices=[{'path':'/dev/sdx','label':'test','size':4096,'partition_disk':True,'is_image':False,'mounted':False}]
        c.selection=0
        c.demo=False
        with patch.object(c,'begin_unmount') as unmount:
            c.start_partition_search()
            unmount.assert_called_once_with(next_action='partition_search')
        c.demo=True
        c.close()

    def test_whole_image_metadata_uses_offset_workflow(self):
        import struct
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'mbr.img'
            data=bytearray(4096)
            data[446+4]=0x0b
            struct.pack_into('<II',data,446+8,1,7)
            data[510:512]=b'\x55\xaa'
            image.write_bytes(data)
            c=Controller(demo=True,desktop=True)
            try:
                c.add_image(image)
                c.desktop_select_method('metadata')
                self.assertEqual(c.engine,'partition')
                self.assertEqual(c.stage,'scan_ready')
                self.assertEqual(c.partition_selected.start,1)
            finally:
                c.close()

if __name__=='__main__':unittest.main()
