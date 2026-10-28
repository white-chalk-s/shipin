"""Exercise the real Windows global listener with a focused temporary test window."""
import json
import time

from pynput import keyboard
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit
from region_recorder import MainWindow


def main():
    app = QApplication.instance() or QApplication([])
    main_window = MainWindow()
    actions = []
    main_window.hotkey_signals.effect_requested.connect(actions.append)
    window = QLineEdit()
    window.setWindowTitle("快捷键测试")
    window.show()
    window.activateWindow()
    window.setFocus()
    app.processEvents()
    controller = keyboard.Controller()

    def inject():
        controller.press(keyboard.Key.ctrl)
        controller.press("c")
        controller.release("c")
        controller.release(keyboard.Key.ctrl)
        time.sleep(.15)
        controller.press("v")
        controller.release("v")

    loop = QEventLoop()
    QTimer.singleShot(500, inject)
    QTimer.singleShot(1600, loop.quit)
    loop.exec()
    window.close()
    main_window.close()
    app.processEvents()
    print(json.dumps({"received_effect_keys": actions}))
    if actions != ["v"]:
        raise SystemExit("全局快捷键验证失败")


if __name__ == "__main__":
    main()
