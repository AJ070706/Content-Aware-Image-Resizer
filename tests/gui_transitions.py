"""Run a hidden WebView interaction check against a built app directory.

Usage: set PYTHONPATH to the build's app directory, then run this script with
that app directory as its only argument. Requires Windows WebView2.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image
import webview

from desktop import ImageApi


class FixtureApi(ImageApi):
    def __init__(self, sources):
        super().__init__()
        self.sources = iter(sources)

    def open_image(self):
        with self._lock:
            return self._load(next(self.sources))

    def seam_batch(self, direction, first, limit, image_generation, operation='shrink'):
        # Give the UI time to change direction while a width request is outstanding.
        if direction == 'width' and first >= 5:
            time.sleep(.35)
        return super().seam_batch(direction, first, limit, image_generation, operation)


def wait_for(window, expression, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if window.evaluate_js(expression):
            return
        time.sleep(.05)
    raise AssertionError('Timed out waiting for ' + expression)


def inspect(window, expression):
    return window.evaluate_js(expression)


def target(window, value):
    window.evaluate_js('''(() => {
      const field = document.querySelector('input[type=number]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(field, '%d');
      field.dispatchEvent(new Event('input', {bubbles:true}));
      field.dispatchEvent(new Event('change', {bubbles:true}));
    })()''' % value)
    wait_for(window, f'Number(document.querySelector(".target-slider")?.value) === {value}')


def click(window, selector):
    window.evaluate_js(f'document.querySelector({json.dumps(selector)}).click()')


def main(app_dir):
    with tempfile.TemporaryDirectory() as directory:
        first = Path(directory) / 'first.png'
        second = Path(directory) / 'second.png'
        Image.fromarray(np.random.default_rng(811).integers(0, 256, (12, 16, 3), dtype=np.uint8)).save(first)
        Image.fromarray(np.random.default_rng(812).integers(0, 256, (9, 7, 3), dtype=np.uint8)).save(second)
        api = FixtureApi((str(first), str(second)))
        window = webview.create_window('Transition check', str(app_dir / 'ui' / 'index.html'),
                                       js_api=api, width=1180, height=780, hidden=True)
        api._window = window
        result = {'ok': False}

        def run():
            try:
                wait_for(window, 'Boolean(window.pywebview?.api && document.querySelector(".empty"))')
                inspect(window, 'window.requestAnimationFrame = callback => setTimeout(callback, 0)')
                click(window, '.toolbar button')
                wait_for(window, 'Boolean(document.querySelector("input[type=number]") && !document.querySelector("input[type=number]").disabled)')
                target(window, 12)
                wait_for(window, '!document.querySelector(".toolbar button.primary").disabled')

                click(window, '.brush-toggle button')
                assert inspect(window, 'Number(document.querySelector("input[type=number]").value)') == 12
                inspect(window, '''(() => {
                  const canvas = document.querySelector('canvas.mask-overlay');
                  canvas.setPointerCapture = () => {};
                  canvas.hasPointerCapture = () => false;
                  const box = canvas.getBoundingClientRect();
                  const point = {bubbles:true, pointerId:1, button:0,
                    clientX:box.left + box.width/2, clientY:box.top + box.height/2};
                  canvas.dispatchEvent(new PointerEvent('pointerdown', point));
                  canvas.dispatchEvent(new PointerEvent('pointerup', point));
                })()''')
                wait_for(window, '/Protected: [1-9]/.test(document.querySelector(".mask-count")?.innerText || "")')
                wait_for(window, '!document.querySelector(".toolbar button.primary").disabled')
                assert inspect(window, 'Number(document.querySelector("input[type=number]").value)') == 12
                result['guidance_keeps_target'] = True

                click(window, '.mode-toggle button:nth-child(2)')
                wait_for(window, 'document.querySelector(".mode-toggle button:nth-child(2)")?.getAttribute("aria-pressed") === "true"')
                assert inspect(window, 'document.querySelector(".brush-toggle button")?.disabled')
                assert inspect(window, 'Number(document.querySelector("input[type=number]").value)') == 12
                assert api._mask is not None and np.count_nonzero(api._mask) > 0
                assert inspect(window, 'Boolean(document.querySelector(".edit-guidance"))')
                click(window, '.edit-guidance')
                wait_for(window, 'document.querySelector(".mode-toggle button")?.getAttribute("aria-pressed") === "true"')
                assert inspect(window, 'Number(document.querySelector("input[type=number]").value)') == 12
                result['mode_keeps_guidance_and_target'] = True

                target(window, 8)
                click(window, '.direction-toggle button:nth-child(2)')
                wait_for(window, 'document.querySelector(".direction-toggle button:nth-child(2)")?.getAttribute("aria-pressed") === "true"')
                wait_for(window, '!document.querySelector(".toolbar button.primary").disabled')
                assert inspect(window, 'Number(document.querySelector("input[type=number]").value)') == 12
                assert api._selection is not None and api._selection[0] is api._orders['height']
                result['direction_replaces_pending_preview'] = True

                target(window, 10)
                click(window, '.quality-toggle button:nth-child(2)')
                wait_for(window, 'document.querySelector(".quality-toggle button:nth-child(2)")?.getAttribute("aria-pressed") === "true"')
                assert inspect(window, 'Number(document.querySelector("input[type=number]").value)') == 10
                wait_for(window, '!document.querySelector(".toolbar button.primary").disabled')
                result['quality_keeps_target_and_guidance'] = bool(np.count_nonzero(api._mask))

                click(window, '.brush-toggle button')
                assert inspect(window, 'document.querySelector(".brush-toggle button")?.getAttribute("aria-pressed")') == 'true'
                click(window, '.canvasbar button')
                wait_for(window, 'document.querySelector("img.base-image")?.alt === "First-seam cumulative energy map"')
                click(window, '.canvasbar button')
                assert inspect(window, 'document.querySelector(".brush-toggle button")?.getAttribute("aria-pressed")') == 'true'
                result['map_restores_brush'] = True

                click(window, '.mode-toggle button:nth-child(2)')
                wait_for(window, '!document.querySelector(".toolbar button.primary").disabled')
                click(window, '.canvasbar button:nth-child(2)')
                assert inspect(window, 'document.querySelector(".canvasbar button:nth-child(2)")?.getAttribute("aria-pressed")') == 'true'
                click(window, '.canvasbar button')
                wait_for(window, 'document.querySelector("img.base-image")?.alt === "First-seam cumulative energy map"')
                assert inspect(window, 'document.querySelector(".toolbar button.primary")?.disabled')
                click(window, '.canvasbar button')
                assert inspect(window, 'document.querySelector(".canvasbar button:nth-child(2)")?.getAttribute("aria-pressed")') == 'true'
                assert inspect(window, 'Number(document.querySelector("input[type=number]").value)') == 10
                result['map_restores_comparison'] = True

                click(window, '.brush-panel > button:last-child')
                wait_for(window, 'document.querySelector(".mask-count")?.innerText?.includes("Protected: 0")')
                wait_for(window, '!document.querySelector(".toolbar button.primary").disabled')
                assert inspect(window, 'Number(document.querySelector("input[type=number]").value)') == 10
                assert api._mask is not None and not np.count_nonzero(api._mask)
                result['clear_guidance_keeps_target'] = True

                click(window, '#info-tab')
                assert inspect(window, 'document.getElementById("info-tab")?.getAttribute("aria-selected")') == 'true'
                click(window, '.toolbar button')
                wait_for(window, 'document.querySelector(".canvasbar > span")?.innerText === "second.png"')
                assert inspect(window, 'document.getElementById("workspace-tab")?.getAttribute("aria-selected")') == 'true'
                assert inspect(window, 'document.querySelector(".mode-toggle button")?.getAttribute("aria-pressed")') == 'true'
                assert inspect(window, 'Number(document.querySelector("input[type=number]").value)') == 7
                assert inspect(window, 'document.querySelector(".quality-toggle button:nth-child(2)")?.getAttribute("aria-pressed")') == 'true'
                assert inspect(window, 'document.querySelector(".canvasbar button:nth-child(2)")?.getAttribute("aria-pressed")') == 'false'
                assert inspect(window, 'document.querySelector(".mask-count")?.innerText?.includes("Protected: 0")')
                result['new_image_resets_view'] = True
                result['ok'] = True
            except Exception as error:
                result['error'] = str(error)
            finally:
                print(json.dumps(result), flush=True)
                window.destroy()

        webview.start(run, gui='edgechromium')
        if not result['ok']:
            raise SystemExit(1)


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
