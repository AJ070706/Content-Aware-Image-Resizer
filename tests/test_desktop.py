import tempfile
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
            self.assertEqual(api.open_image()['width'], 16)
            for width in [0, 17, 1.5, True, '8']:
                with self.assertRaises(ValueError): api.preview('width', width)
            result = api.preview('width', 12)
            self.assertEqual((result['width'],result['height']), (16,12))
            self.assertTrue(np.any(np.array(api._current)[:,:,0] == 255))
            api._window = DialogWindow(str(output))
            api.save_image()
            with Image.open(output) as image: self.assertEqual(image.size,(16,12))
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


if __name__ == '__main__': unittest.main()
