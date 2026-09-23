import base64
import io
import tempfile
import threading
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from desktop import ImageApi


class DialogWindow:
    def __init__(self, result):
        self.result = result
    def create_file_dialog(self, *args, **kwargs):
        return self.result


class DesktopTests(unittest.TestCase):
    def test_image_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.png'
            output = Path(directory) / 'result.png'
            Image.fromarray(np.full((12,16,3), 75, dtype=np.uint8)).save(source)
            api = ImageApi()
            api._window = DialogWindow([str(source)])
            opened = api.open_image()
            self.assertEqual(opened['width'], 16)
            self.assertEqual(opened['generation'], 1)
            for width in [0, 17, 1.5, True, '8']:
                with self.assertRaises(ValueError): api.preview('width', width)
            with self.assertRaises(ValueError): api.preview('width', 12, 1, opened['generation'] - 1)
            result = api.preview('width', 12)
            self.assertNotIn('preview', result)
            overlay = Image.open(io.BytesIO(base64.b64decode(result['overlay'].split(',')[1])))
            self.assertEqual(overlay.size, (16,12))
            self.assertTrue(np.any(np.asarray(overlay)[:,:,3] == 255))
            batch = api.seam_batch('width', 1, 3, opened['generation'])
            self.assertEqual(batch['first'], 1)
            self.assertGreaterEqual(len(batch['seams']), 1)
            self.assertEqual(len(batch['seams'][0]), 12)
            self.assertEqual(api.select_preview('width', 12, 2, opened['generation']), {'target': 12})
            self.assertEqual(api._selection[1], 4)
            api._window = DialogWindow(str(output))
            api.save_image()
            with Image.open(output) as image:
                self.assertEqual(image.size,(16,12))
                self.assertTrue(np.any(np.asarray(image)[:,:,0] == 255))
            api.reset()
            self.assertTrue(np.all(np.array(api._current) == 75))
            api._window = DialogWindow(None)
            self.assertIsNone(api.open_image())
            self.assertIsNone(api.save_image())
    def test_requires_image(self):
        api = ImageApi()
        with self.assertRaises(ValueError): api.preview('width', 5)
        with self.assertRaises(ValueError): api.reset()
        with self.assertRaises(ValueError): api.save_image()

    def test_latest_live_preview_wins_when_requests_finish_out_of_order(self):
        started = threading.Event()
        release = threading.Event()

        class DelayedOrder:
            def render_overlay(self, count):
                if count == 4:
                    started.set()
                    if not release.wait(5):
                        raise TimeoutError('The first preview did not resume.')
                return np.zeros((4, 5, 4), dtype=np.uint8)

        api = ImageApi()
        api._original = Image.new('RGB', (5, 4))
        api._current = api._original.copy()
        api._generation = 1
        api._orders['width'] = DelayedOrder()
        api._order_ready['width'].set()
        failures = []

        def request_old_preview():
            try:
                api.preview('width', 1, 1)
            except Exception as error:
                failures.append(error)

        worker = threading.Thread(target=request_old_preview)
        worker.start()
        self.assertTrue(started.wait(2))
        try:
            self.assertIn('overlay', api.preview('width', 4, 2))
        finally:
            release.set()
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValueError)
        self.assertEqual(api._selection[1], 1)


if __name__ == '__main__': unittest.main()
