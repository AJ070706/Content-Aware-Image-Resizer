"""Compare native output against exhaustive tiny-image seam enumeration."""
import itertools
import unittest
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import main as engine


def reference_seam(image):
    h, w = image.shape[:2]
    a = image[:, :, :3].astype(np.int64)
    energy = np.zeros((h, w), dtype=np.int64)
    for y in range(h):
        for x in range(w):
            energy[y, x] = (abs(a[y, max(0, x-1)] - a[y, min(w-1, x+1)]).sum()
                            + abs(a[max(0, y-1), x] - a[min(h-1, y+1), x]).sum())
    paths = (p for p in itertools.product(range(w), repeat=h)
             if all(abs(x-y) <= 1 for x, y in zip(p, p[1:])))
    # Exhaustive search, independent of the C++ dynamic programming/backtracking.
    return min(paths, key=lambda p: (sum(energy[y,x] for y,x in enumerate(p)), tuple(reversed(p))))


def reference(image, width, height):
    working = image.copy()
    ids = np.arange(image.shape[0]*image.shape[1]).reshape(image.shape[:2])
    marked = image.copy().reshape(-1, image.shape[2])
    for target, horizontal in [(width, False), (height, True)]:
        if horizontal:
            working = working.swapaxes(0,1)
            ids = ids.T
        while working.shape[1] > target:
            seam = reference_seam(working)
            mask = np.ones(working.shape[:2], dtype=bool)
            for row,col in enumerate(seam):
                marked[ids[row,col],:3] = [255,0,0]
                mask[row,col] = False
            h,w,c = working.shape
            working = working[mask].reshape(h,w-1,c)
            ids = ids[mask].reshape(h,w-1)
        if horizontal:
            working = working.swapaxes(0,1)
            ids = ids.T
    return working, marked.reshape(image.shape)


class NativeTests(unittest.TestCase):
    def check_case(self, image, width, height):
        before = image.copy()
        resized, highlighted = reference(image,width,height)
        for operation, expected in [(engine.modify,resized),(engine.highlight,highlighted)]:
            actual = operation(image,width,height)
            np.testing.assert_array_equal(actual,expected)
            self.assertEqual(actual.dtype,np.uint8)
            self.assertTrue(actual.flags.c_contiguous)
            self.assertFalse(np.shares_memory(actual,image))
        np.testing.assert_array_equal(image,before)

    def test_every_target_of_small_rectangles_rgb_and_rgba(self):
        rng = np.random.default_rng(194)
        for channels in (3,4):
            for h in range(1,5):
                for w in range(1,5):
                    a = rng.integers(0,250,(h,w,channels),dtype=np.uint8)
                    for height in range(1,h+1):
                        for width in range(1,w+1):
                            with self.subTest(shape=a.shape,target=(width,height)):
                                self.check_case(a,width,height)

    def test_100_random_seams_against_exhaustive_search(self):
        rng = np.random.default_rng(194)
        for case in range(100):
            with self.subTest(case=case):
                self.check_case(rng.integers(0,250,(4,4,3),dtype=np.uint8),3,4)

    def test_multiple_seams_mark_distinct_original_pixels(self):
        a = np.full((5,8,4),75,dtype=np.uint8)
        b = engine.highlight(a,4,5)
        np.testing.assert_array_equal(np.all(b[:,:,:3]==[255,0,0],axis=2).sum(axis=1),[4]*5)
        np.testing.assert_array_equal(b[:,:,3],a[:,:,3])
        b = engine.highlight(a,4,2)
        self.assertEqual(np.all(b[:,:,:3]==[255,0,0],axis=2).sum(),40-8)

    def test_strides_readonly_broadcast_and_fortran_arrays(self):
        a = np.arange(8*8*4,dtype=np.uint8).reshape(8,8,4)
        views = [a[::2,::2],a[1:5,1:5,::-1],a[3::-1,3::-1],
                 np.asfortranarray(a[:4,:4]),np.broadcast_to(a[0,0],(4,4,4))]
        for view in views:
            view.setflags(write=False)
            self.check_case(view,view.shape[1],view.shape[0])
            self.check_case(view,2,2)

    def test_invalid_inputs_fail_cleanly(self):
        valid = np.zeros((3,4,3),dtype=np.uint8)
        for operation in (engine.modify,engine.highlight):
            for shape in [(4,), (3,4), (3,4,1),(3,4,2),(3,4,5),(0,4,3),(3,0,3),(2,3,4,3)]:
                with self.subTest(operation=operation.__name__,shape=shape):
                    with self.assertRaises(ValueError): operation(np.zeros(shape,dtype=np.uint8),1,1)
            for dtype in [np.float32,np.int16,np.bool_,object]:
                with self.assertRaises(TypeError): operation(valid.astype(dtype),1,1)
            with self.assertRaises(TypeError): operation(valid.tolist(),1,1)
            for target in [0,-1,5,2**100]:
                with self.assertRaises(ValueError): operation(valid,target,1)
                with self.assertRaises(ValueError): operation(valid,1,target)
            for target in [1.5,True,'2',None]:
                with self.assertRaises(TypeError): operation(valid,target,1)
                with self.assertRaises(TypeError): operation(valid,1,target)
            self.assertEqual(operation(valid,np.int64(2),np.int32(2)).dtype,np.uint8)

    def test_repeated_concurrent_calls(self):
        a = np.arange(4*4*3,dtype=np.uint8).reshape(4,4,3)
        expected,_ = reference(a,2,2)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for result in pool.map(lambda _:engine.modify(a,2,2),range(40)):
                np.testing.assert_array_equal(result,expected)


if __name__ == '__main__': unittest.main()
