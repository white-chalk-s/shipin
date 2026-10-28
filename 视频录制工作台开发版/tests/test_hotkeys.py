import unittest
from pynput import keyboard
from hotkeys import HotkeyController


class HotkeyTests(unittest.TestCase):
    def setUp(self):
        self.controller = HotkeyController()
        self.c = keyboard.KeyCode.from_char("c")
        self.v = keyboard.KeyCode.from_char("v")

    def test_single_keys_and_repeat(self):
        self.assertEqual(self.controller.press(self.c), "c")
        self.assertIsNone(self.controller.press(self.c))
        self.controller.release(self.c)
        self.assertEqual(self.controller.press(self.c), "c")
        self.assertEqual(self.controller.press(keyboard.Key.f9), "f9")

    def test_modified_keys_do_not_trigger_even_if_modifier_released_first(self):
        for modifier in (keyboard.Key.ctrl_l, keyboard.Key.alt_l, keyboard.Key.cmd):
            self.controller.press(modifier)
            self.assertIsNone(self.controller.press(self.v))
            self.controller.release(modifier)
            self.assertIsNone(self.controller.press(self.v))
            self.controller.release(self.v)
        self.assertEqual(self.controller.press(self.v), "v")

    def test_virtual_key_with_control_character(self):
        key = keyboard.KeyCode(vk=67, char="\x03")
        self.controller.press(keyboard.Key.ctrl_l)
        self.assertIsNone(self.controller.press(key))
        self.controller.release(key)
        self.controller.release(keyboard.Key.ctrl_l)
        self.assertEqual(self.controller.press(keyboard.KeyCode(vk=67, char="c")), "c")
