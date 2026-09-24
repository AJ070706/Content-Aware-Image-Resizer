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
        self._generation = 0
        self._orders = {'width': None, 'height': None}
        self._order_ready = {'width': threading.Event(), 'height': threading.Event()}
        self._order_errors = {'width': None, 'height': None}
        self._selection = None
        self._latest_preview_request_id = -1

    def _snapshot(self):
        buffer = io.BytesIO()
        self._current.save(buffer, format='PNG')
        return dict(name=self._name, width=self._current.width, height=self._current.height,
                    generation=self._generation,
                    preview='data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'))

    def _load(self, path):
        with Image.open(path) as image:
            original = ImageOps.exif_transpose(image).convert('RGB')
        for order in self._orders.values():
            if order is not None:
                order.cancel()
        self._generation += 1
        generation = self._generation
        self._original = original
        self._current = original.copy()
        self._name = Path(path).name
        self._orders = {'width': None, 'height': None}
        self._order_ready = {'width': threading.Event(), 'height': threading.Event()}
        self._order_errors = {'width': None, 'height': None}
        self._selection = None
        self._latest_preview_request_id = -1
        snapshot = self._snapshot()
        for direction in ('width', 'height'):
            threading.Thread(target=self._calculate_order,
                             args=(generation, original, direction, self._order_ready[direction]),
                             name=f'{direction}-seams', daemon=True).start()
        return snapshot

    def _calculate_order(self, generation, original, direction, ready_event):
        order = None
        try:
            array = np.ascontiguousarray(np.asarray(original, dtype=np.uint8))
            order = engine.SeamOrder(array, 'vertical' if direction == 'width' else 'horizontal')
            with self._lock:
                if generation != self._generation:
                    order.cancel()
                    return
                self._orders[direction] = order
            # Readiness means the order object can serve incremental requests;
            # compute_all publishes each seam prefix and render waits for only
            # the requested count.
            ready_event.set()
            order.compute_all()
        except Exception as error:
            with self._lock:
                if generation == self._generation:
                    self._order_errors[direction] = str(error)
        finally:
            ready_event.set()

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
            self._selection = None
            return self._snapshot()

    def preview_progress(self, direction):
        if direction not in ('width', 'height'):
            raise ValueError('Choose either width or height adjustment.')
        with self._lock:
            order = self._orders[direction]
            error = self._order_errors[direction]
            ready = self._order_ready[direction]
            total = ((self._original.width if direction == 'width' else self._original.height) - 1
                     if self._original else 0)
        if order is None:
            return dict(computed=0, total=total, done=ready.is_set(), error=error)
        computed, total, done = order.progress()
        return dict(computed=computed, total=total, done=done, error=error)

    def seam_batch(self, direction, first, limit, image_generation):
        with self._lock:
            if self._original is None or image_generation != self._generation:
                raise ValueError('The image changed before its seam preview was ready.')
            if direction not in ('width', 'height'):
                raise ValueError('Choose either width or height adjustment.')
            maximum = self._original.width if direction == 'width' else self._original.height
            if isinstance(first, bool) or not isinstance(first, int) or not 1 <= first < maximum:
                raise ValueError('Requested seam is outside the image dimension.')
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError('Seam batch size must be positive.')
            ready = self._order_ready[direction]
        ready.wait()
        with self._lock:
            if image_generation != self._generation:
                raise ValueError('The image changed before its seam preview was ready.')
            order = self._orders[direction]
            error = self._order_errors[direction]
        if error:
            raise RuntimeError(error)
        if order is None:
            raise RuntimeError('Seam order could not be started.')
        seams = order.seam_positions_batch(first, limit).tolist()
        with self._lock:
            if image_generation != self._generation:
                raise ValueError('The image changed before its seam preview was ready.')
        return dict(first=first, seams=seams)

    def select_preview(self, direction, target, request_id, image_generation, mode='highlight'):
        with self._lock:
            if self._original is None or image_generation != self._generation:
                raise ValueError('The image changed before its seam preview was ready.')
            if direction not in ('width', 'height'):
                raise ValueError('Choose either width or height adjustment.')
            if mode not in ('highlight', 'modify'):
                raise ValueError('Choose either Highlight seams or Modify image mode.')
            maximum = self._original.width if direction == 'width' else self._original.height
            if isinstance(target, bool) or not isinstance(target, int) or not 1 <= target <= maximum:
                raise ValueError(f'Target {direction} must be a whole number from 1 to {maximum}.')
            if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 0:
                raise ValueError('Preview request ID must be a nonnegative whole number.')
            if request_id < self._latest_preview_request_id:
                raise ValueError('A newer seam preview has replaced this request.')
            self._latest_preview_request_id = request_id
            self._selection = None
            order = self._orders[direction]
            count = maximum - target
        if count:
            if order is None or order.progress()[0] < count:
                raise RuntimeError('The requested seam has not been calculated yet.')
        with self._lock:
            if image_generation != self._generation:
                raise ValueError('The image changed before its seam preview was ready.')
            if request_id != self._latest_preview_request_id:
                raise ValueError('A newer seam preview has replaced this request.')
            self._selection = (order, count, mode)
        return dict(target=target)

    def preview(self, direction, target, request_id=0, image_generation=None):
        with self._lock:
            if self._original is None:
                raise ValueError('Open an image first.')
            if image_generation is not None and image_generation != self._generation:
                raise ValueError('The image changed before its seam preview was ready.')
            if direction not in ('width', 'height'):
                raise ValueError('Choose either width or height adjustment.')
            maximum = self._original.width if direction == 'width' else self._original.height
            if isinstance(target, bool) or not isinstance(target, int) or not 1 <= target <= maximum:
                raise ValueError(f'Target {direction} must be a whole number from 1 to {maximum}.')
            if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 0:
                raise ValueError('Preview request ID must be a nonnegative whole number.')
            if request_id < self._latest_preview_request_id:
                raise ValueError('A newer seam preview has replaced this request.')
            self._latest_preview_request_id = request_id
            self._selection = None
            generation = self._generation
            ready = self._order_ready[direction]
        ready.wait()
        with self._lock:
            if generation != self._generation:
                raise ValueError('The image changed before its seam preview was ready.')
            order = self._orders[direction]
            error = self._order_errors[direction]
        if error:
            raise RuntimeError(error)
        if order is None:
            raise RuntimeError('Seam order could not be started.')
        count = maximum - target
        buffer = io.BytesIO()
        Image.fromarray(order.render_overlay(count)).save(buffer, format='PNG', compress_level=1)
        overlay = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
        with self._lock:
            if generation != self._generation:
                raise ValueError('The image changed before its seam preview was ready.')
            if request_id != self._latest_preview_request_id:
                raise ValueError('A newer seam preview has replaced this request.')
            self._selection = (order, count, 'highlight')
            return dict(overlay=overlay)

    def save_image(self):
        with self._lock:
            if self._current is None:
                raise ValueError('Open an image first.')
            selection = self._selection
            suffix = '-modified.png' if selection and selection[2] == 'modify' else '-seams.png'
            path = self._window.create_file_dialog(webview.FileDialog.SAVE,
                save_filename=Path(self._name).stem + suffix, file_types=('PNG (*.png)',))
            if not path:
                return None
            path = Path(path[0] if isinstance(path, (list, tuple)) else path)
            if path.suffix.lower() != '.png':
                path = path.with_suffix('.png')
            if selection and selection[1]:
                order, count, mode = selection
                array = order.render_modified(count) if mode == 'modify' else order.render(count)
                image = Image.fromarray(array)
            else:
                image = self._current
            image.save(path, format='PNG')
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
