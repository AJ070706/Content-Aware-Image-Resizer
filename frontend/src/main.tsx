import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { InfoView } from './InfoView';
import './style.css';

// The desktop bridge supplies immutable seam positions; this file owns the live canvas preview.

type Picture = { name: string; width: number; height: number; preview: string; generation: number };
type SeamBatch = { first: number; seams: number[][] };
type Progress = { computed: number; total: number; done: boolean; error: string | null };
type Direction = 'width' | 'height';
type Mode = 'highlight' | 'modify';
type BrushTool = 'off' | 'protect' | 'remove' | 'erase';
type Tab = 'workspace' | 'info';
type Api = {
  open_image: () => Promise<Picture | null>;
  reset: () => Promise<Picture>;
  seam_batch: (direction: Direction, first: number, limit: number, generation: number) => Promise<SeamBatch>;
  select_preview: (direction: Direction, target: number, requestId: number, generation: number, mode: Mode) => Promise<{ target: number }>;
  preview_progress: (direction: Direction) => Promise<Progress>;
  save_image: () => Promise<string | null>;
  set_mask: (png: string, generation: number) => Promise<{ generation: number; protected: number; removed: number }>;
};
declare global { interface Window { pywebview?: { api: Api } } }

type TargetControlProps = {
  direction: Direction;
  maximum: number;
  value: number;
  disabled: boolean;
  onChange: (value: number) => void;
};

/** A number field and slider editing the same selected dimension. */
function TargetControl({ direction, maximum, value, disabled, onChange }: TargetControlProps) {
  const [input, setInput] = useState(String(value));
  useEffect(() => setInput(String(value)), [value, direction]);

  function editInput(next: string) {
    setInput(next);
    if (!/^\d+$/.test(next)) return;
    const number = Number(next);
    if (Number.isSafeInteger(number) && number >= 1 && number <= maximum) onChange(number);
  }

  return <div className="target-control">
    <label className="field">Target {direction}<div className="inputwrap"><input aria-label={`Target ${direction}`} type="number" min="1" max={maximum} value={input} disabled={disabled} onChange={event => editInput(event.target.value)} onBlur={() => setInput(String(value))} /><span>px</span></div></label>
    <input className="target-slider" aria-label={`Target ${direction} slider`} type="range" min="1" max={maximum} step="1" value={value} disabled={disabled} onChange={event => onChange(Number(event.target.value))} />
    <div className="hint">{disabled ? 'Load an image to choose a target size.' : `Choose between 1 and ${maximum} pixels.`}</div>
  </div>;
}

/** Paint or erase one seam at its original pixel coordinates. */
function paintSeam(canvas: HTMLCanvasElement, pixels: number[], width: number, add: boolean) {
  const context = canvas.getContext('2d');
  if (!context) throw new Error('The image preview canvas is unavailable.');
  context.beginPath();
  for (const pixel of pixels) context.rect(pixel % width, Math.floor(pixel / width), 1, 1);
  if (add) {
    context.fillStyle = '#ff0000';
    context.fill();
  } else {
    context.save();
    context.globalCompositeOperation = 'destination-out';
    context.fillStyle = '#000';
    context.fill();
    context.restore();
  }
}

/** Decode the source PNG once for browser-side resized previews. */
function loadOriginalPixels(pic: Picture): Promise<Uint32Array> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = pic.width;
      canvas.height = pic.height;
      const context = canvas.getContext('2d');
      if (!context) { reject(new Error('The image preview canvas is unavailable.')); return; }
      context.drawImage(image, 0, 0);
      resolve(new Uint32Array(context.getImageData(0, 0, pic.width, pic.height).data.buffer));
    };
    image.onerror = () => reject(new Error('Could not load the image for modification.'));
    image.src = pic.preview;
  });
}

/** Compact surviving pixels into the selected output dimension. */
function drawModified(canvas: HTMLCanvasElement, original: Uint32Array, removed: Uint8Array,
  width: number, height: number, direction: Direction, count: number) {
  const outputWidth = width - (direction === 'width' ? count : 0);
  const outputHeight = height - (direction === 'height' ? count : 0);
  if (canvas.width !== outputWidth) canvas.width = outputWidth;
  if (canvas.height !== outputHeight) canvas.height = outputHeight;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('The image preview canvas is unavailable.');
  const image = context.createImageData(outputWidth, outputHeight);
  const output = new Uint32Array(image.data.buffer);
  if (direction === 'width') {
    let destination = 0;
    for (let source = 0; source < original.length; source++) {
      if (!removed[source]) output[destination++] = original[source];
    }
  } else {
    const destinationRows = new Uint32Array(width);
    for (let source = 0; source < original.length; source++) {
      if (!removed[source]) {
        const column = source % width;
        output[destinationRows[column]++ * width + column] = original[source];
      }
    }
  }
  context.putImageData(image, 0, 0);
}

function nextFrame() {
  return new Promise<void>(resolve => window.requestAnimationFrame(() => resolve()));
}

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('workspace');
  const [ready, setReady] = useState(!!window.pywebview?.api);
  const [busy, setBusy] = useState(false);
  const [pic, setPic] = useState<Picture | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const maskCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const paintingRef = useRef<{ pointerId: number; x: number; y: number; changed: boolean; before: ImageData } | null>(null);
  const drawnCountRef = useRef(0);
  const imageGenerationRef = useRef<number | null>(null);
  const seamCacheRef = useRef<Record<Direction, Map<number, number[]>>>({ width: new Map(), height: new Map() });
  // The mask is reversible: moving the slider back restores cached pixels.
  const removedPixelsRef = useRef<Uint8Array | null>(null);
  const originalPixelsRef = useRef<{ generation: number; pixels: Promise<Uint32Array> } | null>(null);
  const [mode, setMode] = useState<Mode>('highlight');
  const [direction, setDirection] = useState<Direction>('width');
  const [target, setTarget] = useState(1);
  const [displayedSize, setDisplayedSize] = useState(1);
  const [requestId, setRequestId] = useState(0);
  const [settledId, setSettledId] = useState(0);
  const [previewFailed, setPreviewFailed] = useState(false);
  const requestIdRef = useRef(0);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [zoom, setZoom] = useState('fit');
  const [compare, setCompare] = useState(false);
  const [comparison, setComparison] = useState(50);
  const [brushTool, setBrushTool] = useState<BrushTool>('off');
  const [brushSize, setBrushSize] = useState(18);
  const [maskCount, setMaskCount] = useState({ protected: 0, removed: 0 });
  const [message, setMessage] = useState('Open an image to get started.');
  const [error, setError] = useState('');

  useEffect(() => {
    const onReady = () => setReady(true);
    window.addEventListener('pywebviewready', onReady);
    return () => window.removeEventListener('pywebviewready', onReady);
  }, []);

  const pending = !!pic && requestId !== 0 && settledId !== requestId;
  const maximum = pic ? (direction === 'width' ? pic.width : pic.height) : 0;
  const targetCount = maximum - target;
  const waitingForCalculation = pending && targetCount > maximum - displayedSize &&
    progress !== null && !progress.done && progress.computed < maximum - displayedSize + 1;
  const previewWidth = pic ? (mode === 'modify' && direction === 'width' ? displayedSize : pic.width) : 0;
  const previewHeight = pic ? (mode === 'modify' && direction === 'height' ? displayedSize : pic.height) : 0;
  const comparing = !!pic && mode === 'modify' && compare;

  useLayoutEffect(() => {
    // Switching image, direction, or mode starts from the unmodified source.
    if (!pic || !canvasRef.current) return;
    const canvas = canvasRef.current;
    canvas.width = pic.width;
    canvas.height = pic.height;
    drawnCountRef.current = 0;
    removedPixelsRef.current = new Uint8Array(pic.width * pic.height);
    setDisplayedSize(direction === 'width' ? pic.width : pic.height);
    if (imageGenerationRef.current !== pic.generation) {
      seamCacheRef.current = { width: new Map(), height: new Map() };
      imageGenerationRef.current = pic.generation;
    }
  }, [pic?.generation, direction, mode]);

  useEffect(() => {
    // Poll only while a request is active; an old poll must not overwrite the new direction.
    if (!pending || !ready) return;
    let active = true;
    let polling = false;
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const next = await window.pywebview!.api.preview_progress(direction);
        if (active) setProgress(next);
      } catch { /* The preview request reports errors through the main status. */ }
      finally { polling = false; }
    };
    void poll();
    const timer = window.setInterval(poll, 150);
    return () => { active = false; window.clearInterval(timer); };
  }, [pending, direction, ready]);

  useEffect(() => {
    // Reconcile the currently displayed seam count with the newest target in small frame batches.
    if (!ready || !pic || requestId === 0) return;
    let active = true;
    const api = window.pywebview!.api;
    const cache = seamCacheRef.current[direction];
    const desiredCount = (direction === 'width' ? pic.width : pic.height) - target;
    const fullSize = direction === 'width' ? pic.width : pic.height;
    const run = async () => {
      try {
        let original: Uint32Array | null = null;
        if (mode === 'modify') {
          if (originalPixelsRef.current?.generation !== pic.generation) {
            originalPixelsRef.current = { generation: pic.generation, pixels: loadOriginalPixels(pic) };
          }
          original = await originalPixelsRef.current.pixels;
          if (!active || requestId !== requestIdRef.current) return;
          drawModified(canvasRef.current!, original, removedPixelsRef.current!, pic.width, pic.height, direction, drawnCountRef.current);
        }
        while (active && requestId === requestIdRef.current && drawnCountRef.current !== desiredCount) {
          const gap = Math.abs(desiredCount - drawnCountRef.current);
          const stepsThisFrame = Math.min(12, Math.max(1, Math.ceil(gap / 20)));
          let steps = 0;
          while (steps < stepsThisFrame && drawnCountRef.current !== desiredCount) {
            if (!active || requestId !== requestIdRef.current) return;
            const current = drawnCountRef.current;
            if (current < desiredCount) {
              const next = current + 1;
              let pixels = cache.get(next);
              if (!pixels) {
                const batch = await api.seam_batch(direction, next, 64, pic.generation);
                if (!active || requestId !== requestIdRef.current) return;
                batch.seams.forEach((seam, index) => cache.set(batch.first + index, seam));
                pixels = cache.get(next);
                if (!pixels) throw new Error('The requested seam was not returned.');
              }
              if (mode === 'highlight') paintSeam(canvasRef.current!, pixels, pic.width, true);
              else for (const pixel of pixels) removedPixelsRef.current![pixel] = 1;
              drawnCountRef.current = next;
            } else {
              const pixels = cache.get(current);
              if (!pixels) throw new Error('A calculated seam is missing from the cache.');
              if (mode === 'highlight') paintSeam(canvasRef.current!, pixels, pic.width, false);
              else for (const pixel of pixels) removedPixelsRef.current![pixel] = 0;
              drawnCountRef.current = current - 1;
            }
            steps += 1;
          }
          if (mode === 'modify') drawModified(canvasRef.current!, original!, removedPixelsRef.current!, pic.width, pic.height, direction, drawnCountRef.current);
          setDisplayedSize(fullSize - drawnCountRef.current);
          await nextFrame();
        }
        if (!active || requestId !== requestIdRef.current) return;
        await api.select_preview(direction, target, requestId, pic.generation, mode);
        if (!active || requestId !== requestIdRef.current) return;
        setPreviewFailed(false);
        setSettledId(requestId);
        setMessage(mode === 'modify' ? 'Modified image preview ready.' : `${direction === 'width' ? 'Vertical' : 'Horizontal'} seam preview ready.`);
      } catch (reason) {
        if (!active || requestId !== requestIdRef.current) return;
        setError(reason instanceof Error ? reason.message : String(reason));
        setPreviewFailed(true);
        setSettledId(requestId);
      }
    };
    void run();
    return () => { active = false; };
  }, [ready, pic, mode, direction, target, requestId]);

  function requestPreview() {
    // Incrementing the ID invalidates any earlier async bridge response.
    requestIdRef.current += 1;
    setRequestId(requestIdRef.current);
    setProgress(null);
    setPreviewFailed(false);
    setError('');
  }

  async function action(run: (api: Api) => Promise<void>) {
    if (!window.pywebview?.api || busy) return;
    setBusy(true);
    setError('');
    try { await run(window.pywebview.api); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }

  const open = () => action(async api => {
    const image = await api.open_image();
    if (!image) return;
    requestIdRef.current += 1;
    const maskCanvas = maskCanvasRef.current;
    if (maskCanvas) maskCanvas.getContext('2d')?.clearRect(0, 0, maskCanvas.width, maskCanvas.height);
    paintingRef.current = null;
    setPic(image);
    setDirection('width');
    setTarget(image.width);
    setDisplayedSize(image.width);
    setRequestId(0);
    setSettledId(0);
    setPreviewFailed(false);
    setProgress(null);
    setCompare(false);
    setComparison(50);
    setBrushTool('off');
    setMaskCount({ protected: 0, removed: 0 });
    setMessage('Image loaded. Both seam orders are calculating in the background.');
  });

  function chooseBrush(next: BrushTool) {
    if (!pic || busy) return;
    if (brushTool === next) { setBrushTool('off'); return; }
    // Paint in original-image coordinates, where the guidance mask is defined.
    setBrushTool(next);
    setCompare(false);
    setMode('highlight');
    setTarget(maximum);
    requestPreview();
  }

  function maskPoint(event: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = maskCanvasRef.current;
    if (!canvas || !pic) return null;
    const rect = canvas.getBoundingClientRect();
    const scale = Math.min(rect.width / pic.width, rect.height / pic.height);
    const x = (event.clientX - rect.left - (rect.width - pic.width * scale) / 2) / scale;
    const y = (event.clientY - rect.top - (rect.height - pic.height * scale) / 2) / scale;
    return x >= 0 && y >= 0 && x < pic.width && y < pic.height ? { x, y } : null;
  }

  function paintMask(from: { x: number; y: number }, to: { x: number; y: number }) {
    const canvas = maskCanvasRef.current;
    const context = canvas?.getContext('2d');
    if (!context || brushTool === 'off') return;
    const distance = Math.hypot(to.x - from.x, to.y - from.y);
    const steps = Math.max(1, Math.ceil(distance / Math.max(1, brushSize / 3)));
    context.save();
    for (let step = 0; step <= steps; step++) {
      const x = from.x + (to.x - from.x) * step / steps;
      const y = from.y + (to.y - from.y) * step / steps;
      context.beginPath();
      context.arc(x, y, brushSize / 2, 0, Math.PI * 2);
      context.globalCompositeOperation = 'destination-out';
      context.fill();
      if (brushTool !== 'erase') {
        context.globalCompositeOperation = 'source-over';
        context.fillStyle = brushTool === 'protect' ? '#35d985' : '#ec455c';
        context.fill();
      }
    }
    context.restore();
  }

  async function commitMask(before: ImageData) {
    const canvas = maskCanvasRef.current;
    if (!canvas || !pic || !window.pywebview?.api) return;
    setBusy(true);
    setError('');
    try {
      const result = await window.pywebview.api.set_mask(canvas.toDataURL('image/png'), pic.generation);
      requestIdRef.current += 1;
      setPic(current => current ? { ...current, generation: result.generation } : current);
      setTarget(maximum);
      setRequestId(0);
      setSettledId(0);
      setProgress(null);
      setPreviewFailed(false);
      setMaskCount({ protected: result.protected, removed: result.removed });
      setMessage('Brush guidance updated. Both seam directions are recalculating.');
    } catch (reason) {
      canvas.getContext('2d')?.putImageData(before, 0, 0);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally { setBusy(false); }
  }

  function startPainting(event: React.PointerEvent<HTMLCanvasElement>) {
    if (brushTool === 'off' || busy || event.button !== 0) return;
    const point = maskPoint(event);
    const canvas = maskCanvasRef.current;
    const context = canvas?.getContext('2d');
    if (!point || !canvas || !context) return;
    canvas.setPointerCapture(event.pointerId);
    paintingRef.current = { pointerId: event.pointerId, ...point, changed: true,
      before: context.getImageData(0, 0, canvas.width, canvas.height) };
    paintMask(point, point);
  }

  function continuePainting(event: React.PointerEvent<HTMLCanvasElement>) {
    const stroke = paintingRef.current;
    if (!stroke || stroke.pointerId !== event.pointerId) return;
    const point = maskPoint(event);
    if (!point) return;
    paintMask(stroke, point);
    stroke.x = point.x;
    stroke.y = point.y;
  }

  function finishPainting(event: React.PointerEvent<HTMLCanvasElement>) {
    const stroke = paintingRef.current;
    if (!stroke || stroke.pointerId !== event.pointerId) return;
    continuePainting(event);
    paintingRef.current = null;
    const canvas = maskCanvasRef.current;
    if (canvas?.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    if (stroke.changed) void commitMask(stroke.before);
  }

  function clearMask() {
    const canvas = maskCanvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context || busy || !pic) return;
    const before = context.getImageData(0, 0, canvas.width, canvas.height);
    context.clearRect(0, 0, canvas.width, canvas.height);
    void commitMask(before);
  }

  const chooseDirection = (next: Direction) => {
    if (!pic || busy) return;
    setDirection(next);
    setTarget(next === 'width' ? pic.width : pic.height);
    requestPreview();
  };

  const chooseMode = (next: Mode) => {
    if (!pic || busy || next === mode) return;
    if (next === 'modify') setBrushTool('off');
    setMode(next);
    requestPreview();
  };

  const changeTarget = (next: number) => {
    if (!pic || busy || next === target) return;
    setBrushTool('off');
    setTarget(next);
    requestPreview();
  };

  const reset = () => {
    if (!pic || busy) return;
    setTarget(maximum);
    requestPreview();
    setMessage('Returning to the original image.');
  };

  function handleTabKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    const next = event.key === 'ArrowLeft' ? 'workspace' : event.key === 'ArrowRight' ? 'info' : null;
    if (!next) return;
    event.preventDefault();
    setActiveTab(next);
    document.getElementById(`${next}-tab`)?.focus();
  }

  return <div className="app">
    <header><div className="brand"><span className="mark">▧</span><div><strong>Content-Aware Resizer</strong><small>SEAM EXPLORER</small></div></div>
      <nav className="app-tabs" role="tablist" aria-label="Application pages" onKeyDown={handleTabKeyDown}>
        <button id="workspace-tab" role="tab" aria-controls="workspace-panel" aria-selected={activeTab === 'workspace'} tabIndex={activeTab === 'workspace' ? 0 : -1} onClick={() => setActiveTab('workspace')}>Workspace</button>
        <button id="info-tab" role="tab" aria-controls="info-panel" aria-selected={activeTab === 'info'} tabIndex={activeTab === 'info' ? 0 : -1} onClick={() => setActiveTab('info')}>Info</button>
      </nav>
      <div className="toolbar"><button disabled={!ready || busy} onClick={open}>Open image</button>
        <button className="primary" disabled={!pic || busy || pending || previewFailed} onClick={() => action(async api => { const path = await api.save_image(); if (path) setMessage('Image saved.'); })}>Save image ↗</button></div>
    </header>
    <main id="workspace-panel" role="tabpanel" aria-labelledby="workspace-tab" hidden={activeTab !== 'workspace'}><section className="workspace">
      <div className="canvasbar"><span>{pic?.name ?? 'Your workspace'}</span><div className="canvasbar-actions">
        <button className={comparing ? 'compare-toggle selected' : 'compare-toggle'} aria-pressed={comparing} disabled={!pic || mode !== 'modify'} onClick={() => setCompare(value => !value)}>Compare with original</button>
        <label>View <select value={zoom} onChange={event => setZoom(event.target.value)}><option value="fit">Fit to window</option><option value="1">100%</option><option value="0.5">50%</option><option value="0.25">25%</option></select></label>
      </div></div>
      <div className={'canvas ' + (zoom === 'fit' ? 'fit' : 'actual')} aria-busy={busy || pending}>
        {pic ? <div className={'image-stage' + (comparing ? ' comparing' : '')} style={zoom === 'fit' ? {} : { width: (comparing ? pic.width : previewWidth) * Number(zoom), height: (comparing ? pic.height : previewHeight) * Number(zoom) }}>
          {(mode === 'highlight' || comparing) && <img className="base-image" alt="Original image" src={pic.preview} />}
          <canvas className="seam-overlay" aria-hidden={mode === 'highlight'} role={mode === 'modify' ? 'img' : undefined} aria-label={mode === 'modify' ? 'Modified image preview' : undefined} ref={canvasRef} width={pic.width} height={pic.height} style={comparing ? { clipPath: `inset(0 ${100 - comparison}% 0 0)` } : undefined} />
          <canvas className={'mask-overlay' + (brushTool !== 'off' ? ' painting' : '')} aria-label="Paint seam guidance" ref={maskCanvasRef} width={pic.width} height={pic.height} style={{ display: mode === 'highlight' ? undefined : 'none' }} onPointerDown={startPainting} onPointerMove={continuePainting} onPointerUp={finishPainting} onPointerCancel={finishPainting} />
          {comparing && <div className="comparison-divider" style={{ left: `${comparison}%` }} aria-hidden="true" />}
        </div>
          : <div className="empty"><span className="emptyicon">▧</span><h1>A new perspective<br />on your images.</h1><p>Explore the seams that shape an image.<br />Everything stays on your computer.</p><button className="primary" disabled={!ready || busy} onClick={open}>Choose an image</button><small>PNG · JPEG · WEBP · BMP · TIFF</small></div>}
      </div>
      {comparing && <div className="comparison-control"><span>Modified</span><input aria-label="Before and after comparison" type="range" min="0" max="100" value={comparison} onChange={event => setComparison(Number(event.target.value))} /><span>Original</span></div>}
    </section>
    <aside><div className="eyebrow">IMAGE TOOLS</div><h2>Explore the seams</h2><p className="intro">Both seam directions calculate once in the background as soon as an image loads.</p>
      <div className="dimensions"><span>Original dimensions</span><strong>{pic ? `${pic.width} × ${pic.height}` : '— × —'} <small>px</small></strong></div>
      <div className="field">Mode</div>
      <div className="mode-toggle" role="group" aria-label="Image mode">
        <button aria-pressed={mode === 'highlight'} disabled={!pic || busy} className={mode === 'highlight' ? 'selected' : ''} onClick={() => chooseMode('highlight')}>Highlight seams</button>
        <button aria-pressed={mode === 'modify'} disabled={!pic || busy} className={mode === 'modify' ? 'selected' : ''} onClick={() => chooseMode('modify')}>Modify image</button>
      </div>
      <div className="field">Adjust one direction at a time</div>
      <div className="direction-toggle" role="group" aria-label="Dimension to adjust">
        <button aria-pressed={direction === 'width'} disabled={!pic || busy} className={direction === 'width' ? 'selected' : ''} onClick={() => chooseDirection('width')}>Width</button>
        <button aria-pressed={direction === 'height'} disabled={!pic || busy} className={direction === 'height' ? 'selected' : ''} onClick={() => chooseDirection('height')}>Height</button>
      </div>
      <TargetControl direction={direction} maximum={maximum || 1} value={target} disabled={!pic || busy} onChange={changeTarget} />
      {pending && <div className="hint progress" role="status">{waitingForCalculation && progress ? `Calculating ${direction} seams: ${progress.computed} of ${progress.total}` : `${mode === 'modify' ? 'Resized' : 'Highlighted'} ${direction}: ${displayedSize} px → ${target} px`}</div>}
      <button className="full quiet" disabled={!pic || busy} onClick={reset}>Restore original</button>
      <div className="brush-panel"><div className="field">Guide the seams</div><p>Paint on the original image. Green protects detail; pink favors removal.</p>
        <div className="brush-toggle" role="group" aria-label="Brush tool">
          <button aria-pressed={brushTool === 'protect'} disabled={!pic || busy} className={brushTool === 'protect' ? 'selected' : ''} onClick={() => chooseBrush('protect')}>Protect</button>
          <button aria-pressed={brushTool === 'remove'} disabled={!pic || busy} className={brushTool === 'remove' ? 'selected' : ''} onClick={() => chooseBrush('remove')}>Remove</button>
          <button aria-pressed={brushTool === 'erase'} disabled={!pic || busy} className={brushTool === 'erase' ? 'selected' : ''} onClick={() => chooseBrush('erase')}>Erase</button>
        </div>
        <label className="brush-size">Brush size <input aria-label="Brush size" type="range" min="2" max="80" value={brushSize} disabled={!pic || busy} onChange={event => setBrushSize(Number(event.target.value))} /><span>{brushSize}px</span></label>
        <div className="mask-count">Protected: {maskCount.protected} px · Removal: {maskCount.removed} px</div>
        <button className="full quiet" disabled={!pic || busy || (!maskCount.protected && !maskCount.removed)} onClick={clearMask}>Clear guidance</button>
      </div>
      <div className="note"><span className="dot" /> {mode === 'modify' ? 'Modify mode' : 'Highlight mode'}<p>{mode === 'modify' ? 'Seams are removed from the selected dimension. Saving exports the resized image.' : 'Red marks show the selected seam direction. Saving exports the original-size image with seams highlighted.'} The other direction is calculated simultaneously.</p></div>
    </aside></main>
    <InfoView hidden={activeTab !== 'info'} />
    <footer><span role={error ? 'alert' : 'status'} className={error ? 'error' : ''}>{error || (!ready ? 'Connecting to the desktop app…' : busy ? 'Working…' : pending ? (waitingForCalculation ? 'Waiting for the next seam to calculate…' : mode === 'modify' ? 'Updating resized image…' : 'Updating highlighted seams…') : message)}</span><span>LOCAL PROCESSING</span></footer>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
