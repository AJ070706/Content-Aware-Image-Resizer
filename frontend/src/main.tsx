import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

type Picture = { name: string; width: number; height: number; preview: string; generation: number };
type SeamBatch = { first: number; seams: number[][] };
type Progress = { computed: number; total: number; done: boolean; error: string | null };
type Direction = 'width' | 'height';
type Mode = 'highlight' | 'modify';
type Api = {
  open_image: () => Promise<Picture | null>;
  reset: () => Promise<Picture>;
  seam_batch: (direction: Direction, first: number, limit: number, generation: number) => Promise<SeamBatch>;
  select_preview: (direction: Direction, target: number, requestId: number, generation: number) => Promise<{ target: number }>;
  preview_progress: (direction: Direction) => Promise<Progress>;
  save_image: () => Promise<string | null>;
};
declare global { interface Window { pywebview?: { api: Api } } }

type TargetControlProps = {
  direction: Direction;
  maximum: number;
  value: number;
  disabled: boolean;
  onChange: (value: number) => void;
};

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
    {direction === 'width' && <input className="target-slider" aria-label="Target width slider" type="range" min="1" max={maximum} step="1" value={value} disabled={disabled} onChange={event => onChange(Number(event.target.value))} />}
    <div className="hint">{disabled ? 'Load an image to choose a target size.' : `Choose between 1 and ${maximum} pixels.`}</div>
  </div>;
}

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

function nextFrame() {
  return new Promise<void>(resolve => window.requestAnimationFrame(() => resolve()));
}

function App() {
  const [ready, setReady] = useState(!!window.pywebview?.api);
  const [busy, setBusy] = useState(false);
  const [pic, setPic] = useState<Picture | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawnCountRef = useRef(0);
  const imageGenerationRef = useRef<number | null>(null);
  const seamCacheRef = useRef<Record<Direction, Map<number, number[]>>>({ width: new Map(), height: new Map() });
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

  useLayoutEffect(() => {
    if (!pic || !canvasRef.current) return;
    const canvas = canvasRef.current;
    canvas.width = pic.width;
    canvas.height = pic.height;
    drawnCountRef.current = 0;
    setDisplayedSize(direction === 'width' ? pic.width : pic.height);
    if (imageGenerationRef.current !== pic.generation) {
      seamCacheRef.current = { width: new Map(), height: new Map() };
      imageGenerationRef.current = pic.generation;
    }
  }, [pic?.generation, direction]);

  useEffect(() => {
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
    if (!ready || !pic || mode !== 'highlight' || requestId === 0) return;
    let active = true;
    const api = window.pywebview!.api;
    const cache = seamCacheRef.current[direction];
    const desiredCount = (direction === 'width' ? pic.width : pic.height) - target;
    const fullSize = direction === 'width' ? pic.width : pic.height;
    const run = async () => {
      try {
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
              paintSeam(canvasRef.current!, pixels, pic.width, true);
              drawnCountRef.current = next;
            } else {
              const pixels = cache.get(current);
              if (!pixels) throw new Error('A highlighted seam is missing from the cache.');
              paintSeam(canvasRef.current!, pixels, pic.width, false);
              drawnCountRef.current = current - 1;
            }
            steps += 1;
          }
          setDisplayedSize(fullSize - drawnCountRef.current);
          await nextFrame();
        }
        if (!active || requestId !== requestIdRef.current) return;
        await api.select_preview(direction, target, requestId, pic.generation);
        if (!active || requestId !== requestIdRef.current) return;
        setPreviewFailed(false);
        setSettledId(requestId);
        setMessage(`${direction === 'width' ? 'Vertical' : 'Horizontal'} seam preview ready.`);
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
    setPic(image);
    setDirection('width');
    setTarget(image.width);
    setDisplayedSize(image.width);
    setRequestId(0);
    setSettledId(0);
    setPreviewFailed(false);
    setProgress(null);
    setMessage('Image loaded. Both seam orders are calculating in the background.');
  });

  const chooseDirection = (next: Direction) => {
    if (!pic || busy) return;
    setDirection(next);
    setTarget(next === 'width' ? pic.width : pic.height);
    requestPreview();
  };

  const changeTarget = (next: number) => {
    if (!pic || busy || next === target) return;
    setTarget(next);
    requestPreview();
  };

  const reset = () => {
    if (!pic || busy) return;
    setTarget(maximum);
    requestPreview();
    setMessage('Returning to the original image.');
  };

  return <div className="app">
    <header><div className="brand"><span className="mark">▧</span><div><strong>ImageResizer</strong><small>SEAM EXPLORER</small></div></div>
      <div className="toolbar"><button disabled={!ready || busy} onClick={open}>Open image</button>
        <button className="primary" disabled={!pic || busy || pending || previewFailed} onClick={() => action(async api => { const path = await api.save_image(); if (path) setMessage('Image saved.'); })}>Save image ↗</button></div>
    </header>
    <main><section className="workspace">
      <div className="canvasbar"><span>{pic?.name ?? 'Your workspace'}</span><label>View <select value={zoom} onChange={event => setZoom(event.target.value)}><option value="fit">Fit to window</option><option value="1">100%</option><option value="0.5">50%</option><option value="0.25">25%</option></select></label></div>
      <div className={'canvas ' + (zoom === 'fit' ? 'fit' : 'actual')} aria-busy={busy || pending}>
        {pic ? <div className="image-stage" style={zoom === 'fit' ? {} : { width: pic.width * Number(zoom), height: pic.height * Number(zoom) }}>
          <img className="base-image" alt="Image preview" src={pic.preview} />
          <canvas className="seam-overlay" aria-hidden="true" ref={canvasRef} width={pic.width} height={pic.height} />
        </div>
          : <div className="empty"><span className="emptyicon">▧</span><h1>A new perspective<br />on your images.</h1><p>Explore the seams that shape an image.<br />Everything stays on your computer.</p><button className="primary" disabled={!ready || busy} onClick={open}>Choose an image</button><small>PNG · JPEG · WEBP · BMP · TIFF</small></div>}
      </div>
    </section>
    <aside><div className="eyebrow">IMAGE TOOLS</div><h2>Explore the seams</h2><p className="intro">Both seam directions calculate once in the background as soon as an image loads.</p>
      <div className="dimensions"><span>Original dimensions</span><strong>{pic ? `${pic.width} × ${pic.height}` : '— × —'} <small>px</small></strong></div>
      <div className="field">Mode</div>
      <div className="mode-toggle" role="group" aria-label="Image mode">
        <button aria-pressed={mode === 'highlight'} className={mode === 'highlight' ? 'selected' : ''} onClick={() => setMode('highlight')}>Highlight seams</button>
        <button disabled title="Modify image is coming later">Modify image</button>
      </div>
      <div className="field">Adjust one direction at a time</div>
      <div className="direction-toggle" role="group" aria-label="Dimension to adjust">
        <button aria-pressed={direction === 'width'} disabled={!pic || busy} className={direction === 'width' ? 'selected' : ''} onClick={() => chooseDirection('width')}>Width</button>
        <button aria-pressed={direction === 'height'} disabled={!pic || busy} className={direction === 'height' ? 'selected' : ''} onClick={() => chooseDirection('height')}>Height</button>
      </div>
      <TargetControl direction={direction} maximum={maximum || 1} value={target} disabled={!pic || busy} onChange={changeTarget} />
      {pending && <div className="hint progress" role="status">{waitingForCalculation && progress ? `Calculating ${direction} seams: ${progress.computed} of ${progress.total}` : `Highlighted ${direction}: ${displayedSize} px → ${target} px`}</div>}
      <button className="full quiet" disabled={!pic || busy} onClick={reset}>Restore original</button>
      <div className="note"><span className="dot" /> Preview mode<p>Red marks show the selected seam direction. The other direction is calculated simultaneously. Saving exports the original-size image with seams highlighted.</p></div>
    </aside></main>
    <footer><span role={error ? 'alert' : 'status'} className={error ? 'error' : ''}>{error || (!ready ? 'Connecting to the desktop app…' : busy ? 'Working…' : pending ? (waitingForCalculation ? 'Waiting for the next seam to calculate…' : 'Updating highlighted seams…') : message)}</span><span>LOCAL PROCESSING</span></footer>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
