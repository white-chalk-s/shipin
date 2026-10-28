import os
import unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from region_recorder import RegionSelector, logical_rect_to_physical, physical_rect_to_logical

class SelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mixed_dpi_conversion_and_negative_origin(self):
        pairs = [(QRect(0, 0, 1280, 800), dict(left=0, top=0, width=1920, height=1200)),
                 (QRect(-1920, 0, 1920, 1080), dict(left=-1920, top=0, width=1920, height=1080))]
        r = logical_rect_to_physical(QRect(100, 200, 400, 300), pairs)
        self.assertEqual(r, dict(left=150, top=300, width=600, height=450))
        self.assertEqual(physical_rect_to_logical(r, pairs), QRect(100, 200, 400, 300))
        self.assertEqual(logical_rect_to_physical(QRect(-100, 0, 200, 100), pairs),
                         dict(left=-100, top=0, width=250, height=150))

    def test_selection_waits_for_confirmation_and_can_move_resize(self):
        pairs = [(QRect(0, 0, 500, 400), dict(left=0, top=0, width=750, height=600))]
        with patch('region_recorder._screen_pairs', return_value=pairs):
            selector = RegionSelector(QRect(0, 0, 500, 400))
            selected = []
            selector.region_selected.connect(selected.append)
            selector.show(); self.app.processEvents()
            QTest.mousePress(selector, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
            QTest.mouseMove(selector, QPoint(300, 250))
            QTest.mouseRelease(selector, Qt.MouseButton.LeftButton, pos=QPoint(300, 250))
            self.assertEqual(selected, [])
            QTest.mousePress(selector, Qt.MouseButton.LeftButton, pos=QPoint(200, 160))
            QTest.mouseMove(selector, QPoint(220, 170))
            QTest.mouseRelease(selector, Qt.MouseButton.LeftButton, pos=QPoint(220, 170))
            self.assertEqual(selector.selection.topLeft(), QPoint(120, 110))
            QTest.mousePress(selector, Qt.MouseButton.LeftButton, pos=QPoint(120, 180))
            QTest.mouseMove(selector, QPoint(140, 180))
            QTest.mouseRelease(selector, Qt.MouseButton.LeftButton, pos=QPoint(140, 180))
            self.assertEqual(selector.selection.left(), 140)
            QTest.mouseClick(selector.confirm_button, Qt.MouseButton.LeftButton)
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0]['left'], 210)
            selector.close()

if __name__ == '__main__': unittest.main()
