"""Desktop bridge tests with local file dialogs replaced by deterministic results."""
import base64
import io
import tempfile
import threading
import unittest
from pathlib import Path
import numpy as np
from PIL import Image
from desktop import ImageApi
import main as engine


class DialogWindow:
    def __init__(self, result):
        self.result = result
    def create_file_dialog(self, *args, **kwargs):
        return self.result


class DesktopTests(unittest.TestCase):
    """Exercise selection, export, validation, and stale-request behavior."""
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
            for width in [0, 33, 1.5, True, '8']:
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
            height_batch = api.seam_batch('height', 1, 3, opened['generation'])
            self.assertEqual(height_batch['first'], 1)
            self.assertGreaterEqual(len(height_batch['seams']), 1)
            self.assertEqual(len(height_batch['seams'][0]), 16)
            self.assertEqual(api.select_preview('height', 8, 3, opened['generation']), {'target': 8})
            self.assertEqual(api._selection[1], 4)
            api.save_image()
            with Image.open(output) as image:
                self.assertEqual(image.size, (16, 12))
                self.assertEqual(np.sum(np.asarray(image)[:, :, 0] == 255), 4 * 16)
            self.assertEqual(api.select_preview('width', 12, 4, opened['generation'], 'modify'), {'target': 12})
            api.save_image()
            with Image.open(output) as image:
                self.assertEqual(image.size, (12, 12))
                self.assertTrue(np.all(np.asarray(image) == 75))
            self.assertEqual(api.select_preview('height', 8, 5, opened['generation'], 'modify'), {'target': 8})
            api.save_image()
            with Image.open(output) as image:
                self.assertEqual(image.size, (16, 8))
                self.assertTrue(np.all(np.asarray(image) == 75))
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

    def test_brush_mask_restarts_both_orders_and_rejects_stale_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.png'
            output = Path(directory) / 'guided.png'
            pixels = np.full((4, 5, 3), 80, dtype=np.uint8)
            pixels[:, 4] = 200
            Image.fromarray(pixels).save(source)
            api = ImageApi()
            api._window = DialogWindow([str(source)])
            opened = api.open_image()
            marks = np.zeros((4, 5, 4), dtype=np.uint8)
            marks[:, 0] = [53, 217, 133, 255]
            marks[:, 4] = [236, 69, 92, 255]
            buffer = io.BytesIO()
            Image.fromarray(marks, 'RGBA').save(buffer, format='PNG')
            data_url = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
            result = api.set_mask(data_url, opened['generation'])
            self.assertEqual((result['protected'], result['removed']), (4, 4))
            self.assertGreater(result['generation'], opened['generation'])
            with self.assertRaises(ValueError):
                api.seam_batch('width', 1, 1, opened['generation'])
            batch = api.seam_batch('width', 1, 1, result['generation'])
            self.assertEqual(batch['seams'][0], [4, 9, 14, 19])
            api.select_preview('width', 4, 1, result['generation'], 'modify')
            api._window = DialogWindow(str(output))
            api.save_image()
            with Image.open(output) as exported:
                self.assertEqual(exported.size, (4, 4))
                self.assertTrue(np.all(np.asarray(exported) == 80))
            self.assertEqual(api.preview_progress('height')['total'], 3)
            with self.assertRaises(ValueError): api.set_mask(data_url, opened['generation'])
            with self.assertRaises(ValueError): api.set_mask('bad data', result['generation'])

    def test_energy_switch_restarts_both_directions_and_keeps_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.png'
            output = Path(directory) / 'forward.png'
            pixels = np.random.default_rng(650).integers(0, 256, (5, 6, 3), dtype=np.uint8)
            Image.fromarray(pixels).save(source)
            api = ImageApi()
            api._window = DialogWindow([str(source)])
            opened = api.open_image()
            self.assertEqual(opened['energy_mode'], 'backward')
            marks = np.zeros((5, 6, 4), dtype=np.uint8)
            marks[:, 0] = [53, 217, 133, 255]
            marks[2, 4] = [236, 69, 92, 255]
            buffer = io.BytesIO()
            Image.fromarray(marks, 'RGBA').save(buffer, format='PNG')
            data_url = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
            masked = api.set_mask(data_url, opened['generation'])
            switched = api.set_energy_mode('forward', masked['generation'])
            self.assertGreater(switched['generation'], masked['generation'])
            self.assertEqual(switched['energy_mode'], 'forward')
            self.assertEqual(api.preview_progress('width')['total'], 5)
            self.assertEqual(api.preview_progress('height')['total'], 4)
            with self.assertRaises(ValueError):
                api.seam_batch('height', 1, 1, masked['generation'])
            with self.assertRaises(ValueError):
                api.set_energy_mode('backward', masked['generation'])
            with self.assertRaises(ValueError):
                api.set_energy_mode('invalid', switched['generation'])
            self.assertEqual(api.set_energy_mode('forward', switched['generation']), switched)
            self.assertEqual(api._mask[:, 0].tolist(), [1] * 5)
            for direction in ('width', 'height'):
                native = engine.SeamOrder(pixels, 'vertical' if direction == 'width' else 'horizontal',
                                          api._mask, 'forward')
                native.compute_all()
                actual = api.seam_batch(direction, 1, 1, switched['generation'])['seams'][0]
                self.assertEqual(actual, native.seam_positions_batch(1, 1)[0].tolist())
            api.seam_batch('width', 2, 2, switched['generation'])
            api.select_preview('width', 4, 1, switched['generation'], 'modify')
            api._window = DialogWindow(str(output))
            api.save_image()
            with Image.open(output) as exported:
                order = engine.SeamOrder(pixels, 'vertical', api._mask, 'forward')
                order.compute_all()
                np.testing.assert_array_equal(np.asarray(exported), order.render_modified(2))

    def test_enlargement_previews_and_exports_both_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.png'
            output = Path(directory) / 'enlarged.png'
            pixels = np.random.default_rng(741).integers(0, 256, (4, 5, 3), dtype=np.uint8)
            Image.fromarray(pixels).save(source)
            api = ImageApi()
            api._window = DialogWindow([str(source)])
            opened = api.open_image()
            generation = opened['generation']
            for index, (direction, dimension) in enumerate((('width', 5), ('height', 4))):
                native = engine.SeamOrder(pixels, 'vertical' if direction == 'width' else 'horizontal')
                native.compute_all()
                batch = api.seam_batch(direction, dimension, 1, generation, 'enlarge')
                self.assertEqual(batch['seams'][0], native.insertion_positions_batch(dimension, 1)[0].tolist())
                with self.assertRaises(ValueError):
                    api.seam_batch(direction, dimension, 1, generation, 'shrink')
                target = dimension * 2
                self.assertEqual(api.select_preview(direction, target, index * 2 + 1, generation, 'modify'),
                                 {'target': target})
                api._window = DialogWindow(str(output))
                api.save_image()
                with Image.open(output) as exported:
                    np.testing.assert_array_equal(np.asarray(exported), native.render_enlarged(dimension))
                api._window = DialogWindow([str(source)])
                overlay = api.preview(direction, target, index * 2 + 2, generation)
                rendered = Image.open(io.BytesIO(base64.b64decode(overlay['overlay'].split(',')[1])))
                np.testing.assert_array_equal(np.asarray(rendered), native.render_insertion_overlay(dimension))
            with self.assertRaises(ValueError):
                api.select_preview('width', 11, 5, generation, 'modify')
            with self.assertRaises(ValueError):
                api.select_preview('height', 9, 5, generation, 'modify')

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
