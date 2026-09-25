"""Compare native output against exhaustive tiny-image seam enumeration."""
import itertools
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import main as engine


def reference_seam(image, mode='backward', mask=None):
    h, w = image.shape[:2]
    a = image[:, :, :3].astype(np.int64)
    paths = (p for p in itertools.product(range(w), repeat=h)
             if all(abs(x-y) <= 1 for x, y in zip(p, p[1:])))
    # Exhaustive search, independent of the C++ dynamic programming/backtracking.
    def score(path):
        cost = 0
        for y, x in enumerate(path):
            left = a[y, max(0, x-1)]
            right = a[y, min(w-1, x+1)]
            cost += abs(left - right).sum()
            if mode == 'backward':
                cost += abs(a[max(0, y-1), x] - a[min(h-1, y+1), x]).sum()
            elif y:
                above = a[y-1, x]
                if path[y-1] < x:
                    cost += abs(above - left).sum()
                elif path[y-1] > x:
                    cost += abs(above - right).sum()
            if mask is not None:
                cost += 10000000 if mask[y, x] == 1 else -1000000 if mask[y, x] == -1 else 0
        return cost, tuple(reversed(path))
    return min(paths, key=score)


def reference(image, width, height, mode='backward', guidance=None):
    working = image.copy()
    ids = np.arange(image.shape[0]*image.shape[1]).reshape(image.shape[:2])
    marked = image.copy().reshape(-1, image.shape[2])
    mask_values = None if guidance is None else guidance.copy()
    for target, horizontal in [(width, False), (height, True)]:
        if horizontal:
            working = working.swapaxes(0,1)
            ids = ids.T
            if mask_values is not None:
                mask_values = mask_values.T
        while working.shape[1] > target:
            seam = reference_seam(working, mode, mask_values)
            mask = np.ones(working.shape[:2], dtype=bool)
            for row,col in enumerate(seam):
                marked[ids[row,col],:3] = [255,0,0]
                mask[row,col] = False
            h,w,c = working.shape
            working = working[mask].reshape(h,w-1,c)
            ids = ids[mask].reshape(h,w-1)
            if mask_values is not None:
                mask_values = mask_values[mask].reshape(h,w-1)
        if horizontal:
            working = working.swapaxes(0,1)
            ids = ids.T
            if mask_values is not None:
                mask_values = mask_values.T
    return working, marked.reshape(image.shape)


def reference_enlargement(image, count, mode='backward', guidance=None, horizontal=False):
    """Enumerate distinct paths on a shrinking copy, then blend into the source."""
    source = image.swapaxes(0, 1) if horizontal else image
    working = source.copy()
    h, w = source.shape[:2]
    origins = (np.arange(image.shape[0] * image.shape[1]).reshape(image.shape[:2]).T
               if horizontal else np.arange(h * w).reshape(h, w))
    guidance_working = None if guidance is None else (guidance.T.copy() if horizontal else guidance.copy())
    selected = np.zeros(image.shape[0] * image.shape[1], dtype=bool)
    ordered = []
    for _ in range(min(count, w - 1)):
        seam = reference_seam(working, mode, guidance_working)
        positions = [int(origins[row, col]) for row, col in enumerate(seam)]
        ordered.append(positions)
        selected[positions] = True
        keep = np.ones(working.shape[:2], dtype=bool)
        for row, col in enumerate(seam):
            keep[row, col] = False
        working = working[keep].reshape(h, working.shape[1] - 1, source.shape[2])
        origins = origins[keep].reshape(h, working.shape[1])
        if guidance_working is not None:
            guidance_working = guidance_working[keep].reshape(h, working.shape[1])
    if count == w:
        positions = [int(value) for value in origins[:, 0]]
        ordered.append(positions)
        selected[positions] = True
    output = np.empty((h, w + count, source.shape[2]), dtype=np.uint8)
    original_ids = (np.arange(image.shape[0] * image.shape[1]).reshape(image.shape[:2]).T
                    if horizontal else np.arange(h * w).reshape(h, w))
    for row in range(h):
        destination = 0
        for col in range(w):
            output[row, destination] = source[row, col]
            destination += 1
            if selected[original_ids[row, col]]:
                neighbor = 0 if w == 1 else col + 1 if col + 1 < w else col - 1
                output[row, destination] = ((source[row, col].astype(np.uint16) +
                                             source[row, neighbor].astype(np.uint16)) // 2).astype(np.uint8)
                destination += 1
    marked = image.copy().reshape(-1, image.shape[2])
    marked[selected, :3] = [255, 0, 0]
    enlarged = output.swapaxes(0, 1) if horizontal else output
    return np.ascontiguousarray(enlarged), marked.reshape(image.shape), ordered


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
            for target in [0,-1,9,2**100]:
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

    def test_incremental_orders_match_every_final_prefix_both_directions(self):
        rng = np.random.default_rng(72)
        image = rng.integers(0,250,(5,6,4),dtype=np.uint8)
        for direction, total, width_mode in [('vertical', image.shape[1]-1, True),
                                             ('horizontal', image.shape[0]-1, False)]:
            order = engine.SeamOrder(image, direction)
            self.assertEqual(order.progress(), (0,total,False))
            worker = threading.Thread(target=order.compute_all)
            worker.start()
            for removed in range(total+1):
                preview = order.render(removed)
                overlay = order.render_overlay(removed)
                expected = (engine.highlight(image,image.shape[1]-removed,image.shape[0])
                            if width_mode else engine.highlight(image,image.shape[1],image.shape[0]-removed))
                np.testing.assert_array_equal(preview,expected)
                modified = order.render_modified(removed)
                expected_modified = (engine.modify(image, image.shape[1]-removed, image.shape[0])
                                     if width_mode else engine.modify(image, image.shape[1], image.shape[0]-removed))
                np.testing.assert_array_equal(modified, expected_modified)
                np.testing.assert_array_equal(overlay[:,:,3] == 255,
                    np.all(preview[:,:,:3] == (255,0,0), axis=2))
                if removed:
                    positions = order.seam_positions_batch(removed, 1)[0]
                    previous = order.render_overlay(removed - 1)[:,:,3]
                    added = np.flatnonzero((overlay[:,:,3] == 255) & (previous == 0))
                    np.testing.assert_array_equal(np.sort(positions), added)
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(order.progress(), (total,total,True))

    def test_forward_energy_matches_exhaustive_seams_and_prefixes(self):
        rng = np.random.default_rng(930)
        different = 0
        for case in range(20):
            image = rng.integers(0, 256, (4, 5, 4), dtype=np.uint8)
            image[:, :, 3] = np.arange(20, dtype=np.uint8).reshape(4, 5)
            if reference_seam(image, 'forward') != reference_seam(image):
                different += 1
            for direction in ('vertical', 'horizontal'):
                order = engine.SeamOrder(image, direction, None, 'forward')
                order.compute_all()
                total = (image.shape[1] if direction == 'vertical' else image.shape[0]) - 1
                for count in range(total + 1):
                    width = image.shape[1] - count if direction == 'vertical' else image.shape[1]
                    height = image.shape[0] - count if direction == 'horizontal' else image.shape[0]
                    expected_modified, expected_highlight = reference(image, width, height, 'forward')
                    with self.subTest(case=case, direction=direction, count=count):
                        np.testing.assert_array_equal(order.render_modified(count), expected_modified)
                        np.testing.assert_array_equal(order.render(count), expected_highlight)
                        np.testing.assert_array_equal(engine.modify(image, width, height, 'forward'), expected_modified)
                        np.testing.assert_array_equal(engine.highlight(image, width, height, 'forward'), expected_highlight)
        self.assertGreater(different, 0)

    def test_forward_energy_respects_brush_guidance_and_rejects_invalid_modes(self):
        image = np.random.default_rng(931).integers(0, 256, (4, 5, 3), dtype=np.uint8)
        mask = np.zeros((4, 5), dtype=np.int8)
        mask[:, 0] = 1
        mask[2, 3] = -1
        for direction in ('vertical', 'horizontal'):
            order = engine.SeamOrder(image, direction, mask, 'forward')
            order.compute_all()
            total = (image.shape[1] if direction == 'vertical' else image.shape[0]) - 1
            for count in range(total + 1):
                width = image.shape[1] - count if direction == 'vertical' else image.shape[1]
                height = image.shape[0] - count if direction == 'horizontal' else image.shape[0]
                modified, highlighted = reference(image, width, height, 'forward', mask)
                np.testing.assert_array_equal(order.render_modified(count), modified)
                np.testing.assert_array_equal(order.render(count), highlighted)
        with self.assertRaises(ValueError):
            engine.SeamOrder(image, 'vertical', mask, 'unknown')
        for operation in (engine.modify, engine.highlight):
            with self.assertRaises(ValueError):
                operation(image, 4, 4, 'unknown')

    def test_insertion_matches_exhaustive_distinct_paths_and_blended_pixels(self):
        rng = np.random.default_rng(412)
        for mode in ('backward', 'forward'):
            for channels in (3, 4):
                for height, width in ((1, 1), (1, 4), (4, 1), (3, 4), (4, 3)):
                    image = rng.integers(0, 256, (height, width, channels), dtype=np.uint8)
                    for direction, dimension in (('vertical', width), ('horizontal', height)):
                        horizontal = direction == 'horizontal'
                        order = engine.SeamOrder(image, direction, None, mode)
                        order.compute_all()
                        for count in range(dimension + 1):
                            enlarged, marked, paths = reference_enlargement(image, count, mode,
                                                                               horizontal=horizontal)
                            with self.subTest(mode=mode, shape=image.shape, direction=direction, count=count):
                                np.testing.assert_array_equal(order.render_enlarged(count), enlarged)
                                np.testing.assert_array_equal(order.render_insertion(count), marked)
                                expected_mask = np.zeros(height * width, dtype=bool)
                                for path in paths:
                                    expected_mask[path] = True
                                np.testing.assert_array_equal(order.render_insertion_overlay(count)[:, :, 3] == 255,
                                                              expected_mask.reshape(height, width))
                                if count:
                                    np.testing.assert_array_equal(order.insertion_positions_batch(count, 1)[0], paths[-1])
                                target_width = width + count if not horizontal else width
                                target_height = height + count if horizontal else height
                                np.testing.assert_array_equal(engine.modify(image, target_width, target_height, mode), enlarged)
                                np.testing.assert_array_equal(engine.highlight(image, target_width, target_height, mode), marked)

    def test_insertion_respects_guidance_and_rejects_more_than_double(self):
        image = np.random.default_rng(613).integers(0, 256, (4, 5, 3), dtype=np.uint8)
        guidance = np.zeros((4, 5), dtype=np.int8)
        guidance[:, 0] = 1
        guidance[2, 3] = -1
        for direction, dimension in (('vertical', 5), ('horizontal', 4)):
            order = engine.SeamOrder(image, direction, guidance, 'forward')
            order.compute_all()
            for count in range(dimension + 1):
                expected, _, paths = reference_enlargement(image, count, 'forward', guidance,
                                                             horizontal=direction == 'horizontal')
                np.testing.assert_array_equal(order.render_enlarged(count), expected)
                if count:
                    np.testing.assert_array_equal(order.insertion_positions_batch(count, 1)[0], paths[-1])
        with self.assertRaises(ValueError):
            engine.modify(image, 11, 4)
        with self.assertRaises(ValueError):
            engine.highlight(image, 5, 9)
        with self.assertRaises(ValueError):
            engine.SeamOrder(image, 'vertical').insertion_positions_batch(6, 1)

    def test_native_resize_two_dimensions_with_insertion(self):
        image = np.random.default_rng(783).integers(0, 256, (4, 5, 4), dtype=np.uint8)
        for mode in ('backward', 'forward'):
            wider, _, _ = reference_enlargement(image, 2, mode)
            wider_and_taller, _, _ = reference_enlargement(wider, 1, mode, horizontal=True)
            np.testing.assert_array_equal(engine.modify(image, 7, 5, mode), wider_and_taller)
            narrower, _ = reference(image, 3, 4, mode)
            narrower_and_taller, _, _ = reference_enlargement(narrower, 2, mode, horizontal=True)
            np.testing.assert_array_equal(engine.modify(image, 3, 6, mode), narrower_and_taller)

    def test_incremental_order_cancellation_wakes_waiting_preview(self):
        image = np.random.default_rng(12).integers(0,255,(120,160,3),dtype=np.uint8)
        order = engine.SeamOrder(image,'vertical')
        worker = threading.Thread(target=order.compute_all)
        worker.start()
        order.cancel()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(order.progress()[2])
        if order.progress()[0] == 0:
            with self.assertRaises(RuntimeError): order.render(1)

    def test_painted_guidance_changes_both_seam_directions(self):
        image = np.full((4, 5, 3), 80, dtype=np.uint8)
        plain = engine.SeamOrder(image, 'vertical')
        plain.compute_all()
        np.testing.assert_array_equal(plain.seam_positions_batch(1, 1)[0], [0, 5, 10, 15])

        protect = np.zeros((4, 5), dtype=np.int8)
        protect[:, 0] = 1
        guided = engine.SeamOrder(image, 'vertical', protect)
        guided.compute_all()
        np.testing.assert_array_equal(guided.seam_positions_batch(1, 1)[0], [1, 6, 11, 16])

        remove = protect.copy()
        remove[:, 4] = -1
        guided = engine.SeamOrder(image, 'vertical', remove)
        guided.compute_all()
        np.testing.assert_array_equal(guided.seam_positions_batch(1, 1)[0], [4, 9, 14, 19])
        np.testing.assert_array_equal(guided.render_modified(1), image[:, :4])

        horizontal = np.zeros((4, 5), dtype=np.int8)
        horizontal[2, :] = -1
        guided = engine.SeamOrder(image, 'horizontal', horizontal)
        guided.compute_all()
        np.testing.assert_array_equal(guided.seam_positions_batch(1, 1)[0], [10, 11, 12, 13, 14])
        np.testing.assert_array_equal(guided.render_modified(1), image[[0, 1, 3]])

        with self.assertRaises(TypeError): engine.SeamOrder(image, 'vertical', horizontal.astype(np.uint8))
        with self.assertRaises(ValueError): engine.SeamOrder(image, 'vertical', horizontal[:3])
        invalid = horizontal.copy()
        invalid[0, 0] = 2
        invalid_order = engine.SeamOrder(image, 'vertical', invalid)
        invalid_order.compute_all()
        with self.assertRaises(RuntimeError): invalid_order.seam_positions_batch(1, 1)


if __name__ == '__main__': unittest.main()
