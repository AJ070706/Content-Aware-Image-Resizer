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
    """Thread-safe bridge between the React UI and cached native seam orders.

    Each loaded image has a generation number so old async requests cannot select
    or save a preview after the user opens another image.
    """
    def __init__(self):
        self._window = None
        self._original = None
        self._current = None
        self._mask = None
        self._energy_mode = 'backward'
        self._name = ''
        self._lock = threading.Lock()
        self._generation = 0
        self._orders = {'width': None, 'height': None}
        self._order_ready = {'width': threading.Event(), 'height': threading.Event()}
        self._order_errors = {'width': None, 'height': None}
        self._selection = None
        self._latest_preview_request_id = -1

    def _snapshot(self):
        """Return the original image and metadata in a browser-friendly form."""
        buffer = io.BytesIO()
        self._current.save(buffer, format='PNG')
        return dict(name=self._name, width=self._current.width, height=self._current.height,
                    generation=self._generation, energy_mode=self._energy_mode,
                    preview='data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii'))

    def _load(self, path):
        """Normalize an image, cancel old work, then start both seam workers."""
        with Image.open(path) as image:
            original = ImageOps.exif_transpose(image).convert('RGB')
        self._original = original
        self._current = original.copy()
        self._mask = None
        self._name = Path(path).name
        self._start_orders()
        return self._snapshot()

    def _start_orders(self):
        """Replace both seam orders; call while holding the image lock."""
        for order in self._orders.values():
            if order is not None:
                order.cancel()
        self._generation += 1
        generation = self._generation
        self._orders = {'width': None, 'height': None}
        self._order_ready = {'width': threading.Event(), 'height': threading.Event()}
        self._order_errors = {'width': None, 'height': None}
        self._selection = None
        self._latest_preview_request_id = -1
        for direction in ('width', 'height'):
            threading.Thread(target=self._calculate_order,
                             args=(generation, self._original, self._mask, self._energy_mode, direction,
                                    self._order_ready[direction]),
                             name=f'{direction}-seams', daemon=True).start()

    def _calculate_order(self, generation, original, mask, energy_mode, direction, ready_event):
        """Publish the native order object before computing its seams in place."""
        order = None
        try:
            array = np.ascontiguousarray(np.asarray(original, dtype=np.uint8))
            order = engine.SeamOrder(array, 'vertical' if direction == 'width' else 'horizontal',
                                     mask, energy_mode)
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
        """Ask the desktop host for a local image and return its snapshot."""
        with self._lock:
            paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                file_types=('Images (*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tif;*.tiff)',))
            return self._load(paths[0]) if paths else None

    def set_mask(self, data_url, image_generation):
        """Replace painted guidance and restart both seam directions."""
        prefix = 'data:image/png;base64,'
        if not isinstance(data_url, str) or not data_url.startswith(prefix):
            raise ValueError('The brush mask must be a PNG image.')
        try:
            decoded = base64.b64decode(data_url[len(prefix):], validate=True)
            with Image.open(io.BytesIO(decoded)) as image:
                rgba = np.asarray(image.convert('RGBA'))
        except Exception as error:
            raise ValueError('Could not read the brush mask.') from error
        with self._lock:
            if self._original is None or image_generation != self._generation:
                raise ValueError('The image changed before the brush stroke was applied.')
            if rgba.shape[:2] != (self._original.height, self._original.width):
                raise ValueError('The brush mask must match the original image size.')
            # Canvas brush edges are antialiased; ignore faint residual alpha
            # so erasing a stroke at the same size removes all of its guidance.
            marked = rgba[:, :, 3] >= 128
            mask = np.zeros(rgba.shape[:2], dtype=np.int8)
            mask[marked & (rgba[:, :, 1] > rgba[:, :, 0])] = 1
            mask[marked & (rgba[:, :, 0] > rgba[:, :, 1])] = -1
            self._mask = mask
            self._start_orders()
            return dict(generation=self._generation,
                        protected=int(np.count_nonzero(mask == 1)),
                        removed=int(np.count_nonzero(mask == -1)))

    def set_energy_mode(self, mode, image_generation):
        """Change the seam scoring model and invalidate both cached directions."""
        if mode not in ('backward', 'forward'):
            raise ValueError('Choose either Backward or Forward energy.')
        with self._lock:
            if self._original is None or image_generation != self._generation:
                raise ValueError('The image changed before its energy mode was selected.')
            if mode != self._energy_mode:
                self._energy_mode = mode
                self._start_orders()
            return dict(generation=self._generation, energy_mode=self._energy_mode)

    def reset(self):
        """Clear the selected preview without restarting seam calculation."""
        with self._lock:
            if self._original is None:
                raise ValueError('Open an image first.')
            self._current = self._original.copy()
            self._selection = None
            return self._snapshot()

    def preview_progress(self, direction):
        """Report how many seams are published for the selected dimension."""
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
        """Wait for and return available original-coordinate seam positions."""
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
        """Commit the latest displayed target for Save after the UI catches up."""
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
        """Return a full overlay for callers using the older preview API."""
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
        """Render the selected cached prefix as a marked or resized PNG."""
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
    """Start pywebview, or run a hidden packaged-startup smoke check."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    page = base / 'ui' / 'index.html'
    if not page.exists():
        raise RuntimeError('Frontend missing. Run scripts/build.ps1 first.')
    api = ImageApi()
    smoke = '--smoke-test' in sys.argv
    window = webview.create_window('Content-Aware Image Resizer', str(page), js_api=api,
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
