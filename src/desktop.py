"""Local desktop host for the bundled React interface."""
import base64
import io
import json
import sys
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
import webview
import main as engine


class ImageApi:
    def __init__(self):
        self._window = None
        self._original = None
        self._current = None
        self._name = ''
        self._lock = threading.Lock()

    def _snapshot(self):
        buffer = io.BytesIO()
        self._current.save(buffer, format='PNG')
        return dict(name=self._name, width=self._current.width, height=self._current.height,
                    preview='data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'))

    def _load(self, path):
        with Image.open(path) as image:
            original = ImageOps.exif_transpose(image).convert('RGB')
        self._original = original
        self._current = original.copy()
        self._name = Path(path).name
        return self._snapshot()

    def open_image(self):
        with self._lock:
            paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                file_types=('Images (*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tif;*.tiff)',))
            return self._load(paths[0]) if paths else None

    def reset(self):
        with self._lock:
            if self._original is None:
                raise ValueError('Open an image first.')
            self._current = self._original.copy()
            return self._snapshot()

    def highlight(self, width):
        with self._lock:
            if self._original is None:
                raise ValueError('Open an image first.')
            if isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= self._original.width:
                raise ValueError('Target width must be a whole number within the original image width.')
            array = np.array(self._original, dtype=np.uint8)
            # The existing engine supports vertical highlighting only.
            self._current = Image.fromarray(engine.highlight(array, width, self._original.height))
            return self._snapshot()

    def save_image(self):
        with self._lock:
            if self._current is None:
                raise ValueError('Open an image first.')
            path = self._window.create_file_dialog(webview.FileDialog.SAVE,
                save_filename=Path(self._name).stem + '-seams.png', file_types=('PNG (*.png)',))
            if not path:
                return None
            path = Path(path[0] if isinstance(path, (list, tuple)) else path)
            if path.suffix.lower() != '.png':
                path = path.with_suffix('.png')
            self._current.save(path, format='PNG')
            return str(path)


def launch():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    page = base / 'ui' / 'index.html'
    if not page.exists():
        raise RuntimeError('Frontend missing. Run scripts/build.ps1 first.')
    api = ImageApi()
    smoke = '--smoke-test' in sys.argv
    window = webview.create_window('ImageResizer', str(page), js_api=api,
        width=1180, height=780, min_size=(800, 600), background_color='#151617', hidden=smoke)
    api._window = window
    if smoke:
        def check():
            import time
            result = {'ok': False}
            try:
                for _ in range(100):
                    if window.evaluate_js("Boolean(window.pywebview?.api && document.querySelector('h1'))"):
                        break
                    time.sleep(.1)
                else:
                    raise RuntimeError('React or desktop bridge did not initialize')
                result = {'ok': True, 'title': window.evaluate_js('document.querySelector("h1").innerText')}
            except Exception as exc:
                result['error'] = str(exc)
            finally:
                Path(sys.argv[sys.argv.index('--smoke-test') + 1]).write_text(json.dumps(result))
                window.destroy()
        webview.start(check, gui='edgechromium')
    else:
        webview.start(gui='edgechromium')


if __name__ == '__main__':
    launch()
