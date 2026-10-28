"""Track global key transitions without intercepting the target application's input."""
from pynput import keyboard


class HotkeyController:
    def __init__(self):
        self.pressed = set()
        self.modifiers = set()
        self._modifier_keys = {
            key for name in ("ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r",
                             "alt_gr", "cmd", "cmd_l", "cmd_r")
            if (key := getattr(keyboard.Key, name, None)) is not None
        }

    @staticmethod
    def _key_name(key):
        if key == keyboard.Key.f9:
            return "f9"
        vk = getattr(key, "vk", None)
        if vk in (67, 86):
            return chr(vk).lower()
        char = getattr(key, "char", None)
        return char.lower() if isinstance(char, str) and char.lower() in ("c", "v") else None

    def press(self, key):
        if key in self._modifier_keys:
            self.modifiers.add(key)
            return None
        name = self._key_name(key)
        if name is None or name in self.pressed:
            return None
        self.pressed.add(name)
        if name in ("c", "v") and self.modifiers:
            return None
        return name

    def release(self, key):
        self.modifiers.discard(key)
        name = self._key_name(key)
        if name:
            self.pressed.discard(name)
