"""Isolated startup check used by the development build script."""
import json
import os
import tempfile
import traceback
from pathlib import Path


def run_smoke(result_path):
    import region_recorder as app_module
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication

    result = {'ok': False}
    try:
        with tempfile.TemporaryDirectory(prefix='vcw-smoke-') as temp:
            os.environ['LOCALAPPDATA'] = temp
            settings = QSettings(str(Path(temp) / 'settings.ini'), QSettings.Format.IniFormat)
            settings.setValue('save_dir', temp)
            app_module.QSettings = lambda *_args: settings
            app = QApplication.instance() or QApplication([])
            window = app_module.MainWindow()
            window.show()
            def finish():
                result.update(ok=window.isVisible(), title=window.windowTitle(),
                              pages=window.page_stack.count(), screen=app.primaryScreen() is not None)
                window.close()
                app.quit()
            QTimer.singleShot(500, finish)
            app.exec()
    except Exception:
        result['error'] = traceback.format_exc()
    Path(result_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result.get('ok') and result.get('screen') else 1
