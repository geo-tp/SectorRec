import unittest
from src.views.desktop_native import DesktopWindow

class ProperWarningTests(unittest.TestCase):
    def test_source_failure_not_mislabeled_as_destination_failure(self):
        msg = DesktopWindow._explain_destination_error(
            'Cannot establish the source disk; choose a safe external destination.')
        self.assertIn('source', msg.lower())
        self.assertIn('destination folder may be valid', msg.lower())
        self.assertNotIn('cannot confirm which physical disk contains this folder', msg)

    def test_destination_failure_still_explicit(self):
        msg = DesktopWindow._explain_destination_error('Destination physical disk cannot be verified.')
        self.assertIn('folder', msg)

if __name__ == '__main__':
    unittest.main()
