import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

type Picture = { name: string; width: number; height: number; preview: string };
type Progress = { computed: number; total: number; done: boolean; error: string | null };
type Direction = 'width' | 'height';
type Api = {
  open_image: () => Promise<Picture | null>;
  reset: () => Promise<Picture>;
  preview: (direction: Direction, target: number) => Promise<Picture>;
  preview_progress: (direction: Direction) => Promise<Progress>;
  save_image: () => Promise<string | null>;
};
declare global { interface Window { pywebview?: { api: Api } } }

function App() {
  const [ready, setReady] = useState(!!window.pywebview?.api);
  const [busy, setBusy] = useState(false);
  const [calculating, setCalculating] = useState(false);
  const [pic, setPic] = useState<Picture | null>(null);
  const [direction, setDirection] = useState<Direction>('width');
  const [target, setTarget] = useState('');
  const [progress, setProgress] = useState<Progress | null>(null);
  const [zoom, setZoom] = useState('fit');
  const [message, setMessage] = useState('Open an image to get started.');
  const [error, setError] = useState('');

  useEffect(() => {
    const onReady = () => setReady(true);
    window.addEventListener('pywebviewready', onReady);
    return () => window.removeEventListener('pywebviewready', onReady);
  }, []);

  useEffect(() => {
    if (!calculating || !ready) return;
    let active = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await window.pywebview!.api.preview_progress(direction);
        if (active) setProgress(next);
      } catch { /* The preview request reports errors through the main status. */ }
    }, 150);
    return () => { active = false; window.clearInterval(timer); };
  }, [calculating, direction, ready]);

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
    setPic(image);
    setDirection('width');
    setTarget(String(image.width));
    setProgress(null);
    setMessage('Image loaded. Both seam orders are calculating in the background.');
  });

  const chooseDirection = (next: Direction) => {
    if (!pic || busy) return;
    setDirection(next);
    setTarget(String(next === 'width' ? pic.width : pic.height));
    setProgress(null);
  };

  const maximum = pic ? (direction === 'width' ? pic.width : pic.height) : 0;
  const valid = !!pic && Number.isInteger(Number(target)) && Number(target) >= 1 && Number(target) <= maximum;

  const preview = () => {
    setCalculating(true);
    setProgress(null);
    return action(async api => {
      const image = await api.preview(direction, Number(target));
      setPic(image);
      setMessage(`${direction === 'width' ? 'Vertical' : 'Horizontal'} seam preview ready. Background calculation continues.`);
    }).finally(() => setCalculating(false));
  };

  const reset = () => action(async api => {
    const image = await api.reset();
    setPic(image);
    setTarget(String(direction === 'width' ? image.width : image.height));
    setProgress(null);
    setMessage('Original image restored. Both seam orders continue calculating.');
  });

  return <div className="app">
    <header><div className="brand"><span className="mark">▧</span><div><strong>ImageResizer</strong><small>SEAM EXPLORER</small></div></div>
      <div className="toolbar"><button disabled={!ready || busy} onClick={open}>Open image</button>
        <button className="primary" disabled={!pic || busy} onClick={() => action(async api => { const path = await api.save_image(); if (path) setMessage('Image saved.'); })}>Save image ↗</button></div>
    </header>
    <main><section className="workspace">
      <div className="canvasbar"><span>{pic?.name ?? 'Your workspace'}</span><label>View <select value={zoom} onChange={event => setZoom(event.target.value)}><option value="fit">Fit to window</option><option value="1">100%</option><option value="0.5">50%</option><option value="0.25">25%</option></select></label></div>
      <div className={'canvas ' + (zoom === 'fit' ? 'fit' : 'actual')} aria-busy={busy}>
        {pic ? <img alt="Image preview" src={pic.preview} style={zoom === 'fit' ? {} : { width: pic.width * Number(zoom), maxWidth: 'none', maxHeight: 'none' }} />
          : <div className="empty"><span className="emptyicon">▧</span><h1>A new perspective<br />on your images.</h1><p>Explore the seams that shape an image.<br />Everything stays on your computer.</p><button className="primary" disabled={!ready || busy} onClick={open}>Choose an image</button><small>PNG · JPEG · WEBP · BMP · TIFF</small></div>}
      </div>
    </section>
    <aside><div className="eyebrow">IMAGE TOOLS</div><h2>Explore the seams</h2><p className="intro">Both seam directions calculate once in the background as soon as an image loads.</p>
      <div className="dimensions"><span>Original dimensions</span><strong>{pic ? `${pic.width} × ${pic.height}` : '— × —'} <small>px</small></strong></div>
      <div className="field">Adjust one direction at a time</div>
      <div className="direction-toggle" role="group" aria-label="Dimension to adjust">
        <button aria-pressed={direction === 'width'} disabled={!pic || busy} className={direction === 'width' ? 'selected' : ''} onClick={() => chooseDirection('width')}>Width</button>
        <button aria-pressed={direction === 'height'} disabled={!pic || busy} className={direction === 'height' ? 'selected' : ''} onClick={() => chooseDirection('height')}>Height</button>
      </div>
      <label className="field">Target {direction}<div className="inputwrap"><input aria-label={`Target ${direction}`} type="number" min="1" max={maximum} value={target} disabled={!pic || busy} onChange={event => setTarget(event.target.value)} /><span>px</span></div></label>
      <div className="hint">{pic ? `Choose between 1 and ${maximum} pixels.` : 'Load an image to choose a target size.'}</div>
      <button className="primary full" disabled={!ready || busy || !valid} onClick={preview}>{busy ? 'Waiting for seams…' : 'Highlight seams'}</button>
      {calculating && progress && <div className="hint progress" role="status">Calculating {direction} seams: {progress.computed} of {progress.total}</div>}
      <button className="full quiet" disabled={!pic || busy} onClick={reset}>Restore original</button>
      <div className="note"><span className="dot" /> Preview mode<p>Red marks show the selected seam direction. The other direction is calculated simultaneously. Saving exports the original-size image with seams highlighted.</p></div>
    </aside></main>
    <footer><span role={error ? 'alert' : 'status'} className={error ? 'error' : ''}>{error || (!ready ? 'Connecting to the desktop app…' : busy ? (calculating && progress ? `Waiting for ${progress.computed} of ${progress.total} seams…` : 'Working…') : message)}</span><span>LOCAL PROCESSING</span></footer>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
