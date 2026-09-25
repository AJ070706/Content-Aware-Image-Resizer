import React from 'react';

type InfoViewProps = { hidden: boolean };

// A seam has one pixel in each row and can shift by at most one column.
const exampleSeam = [2, 2, 3, 3, 2, 1];

/** In-app guide to the controls and the exact energy model used by the C++ engine. */
export function InfoView({ hidden }: InfoViewProps) {
  return <section className="info-view" id="info-panel" role="tabpanel" aria-labelledby="info-tab" hidden={hidden}>
    <div className="info-content">
      <div className="info-hero">
        <p className="eyebrow">THE METHOD BEHIND THE RESIZE</p>
        <h1>Content-aware resizing through seam carving.</h1>
        <p>Instead of squeezing every pixel, the app finds connected paths that pass through lower-detail areas and removes those paths one at a time. The result changes an image’s dimensions while keeping the remaining pixels at their original color and scale.</p>
      </div>

      <div className="info-columns">
        <section className="info-card" aria-labelledby="how-to-use">
          <h2 id="how-to-use">How to use it</h2>
          <ol>
            <li><strong>Open an image.</strong> Width and height seam orders start calculating in the background.</li>
            <li><strong>Choose a mode.</strong> Highlight seams marks pixels red; Modify image removes them in the preview.</li>
            <li><strong>Choose seam quality.</strong> Classic prefers low-detail pixels. Forward energy also considers the new edges that removal would create. Switching quality recalculates both seam directions.</li>
            <li><strong>Guide the seams if needed.</strong> Paint green over detail to protect or pink over areas to favor for removal. Erase or clear marks to change your guidance. Each changed stroke restarts both background calculations.</li>
            <li><strong>Choose width or height.</strong> Enter a target size or move the slider to shrink or enlarge up to twice the original size. You can adjust one dimension at a time.</li>
            <li><strong>Compare and save.</strong> In Modify mode, turn on Compare with original and move the before/after slider. Highlight saves an original-size PNG with removal or insertion paths marked; Modify saves the resized PNG. Restore original returns the target to its starting size.</li>
          </ol>
        </section>
        <section className="info-card seam-example" aria-labelledby="seam-example-title">
          <h2 id="seam-example-title">What is a seam?</h2>
          <div className="seam-grid" role="img" aria-label="A vertical seam through six rows, with one red pixel per row and no jumps larger than one column">
            {exampleSeam.flatMap((seam, row) => Array.from({ length: 7 }, (_, column) =>
              <span key={`${row}-${column}`} className={column === seam ? 'seam-cell selected' : 'seam-cell'} />))}
          </div>
          <p>A vertical seam crosses the image from top to bottom, touching one pixel in each row. Adjacent pixels in the path are in the same or neighboring columns. Horizontal seams follow the equivalent rule from left to right.</p>
        </section>
      </div>

      <section className="info-algorithm" aria-labelledby="algorithm-title">
        <p className="eyebrow">UNDER THE HOOD</p>
        <h2 id="algorithm-title">How this implementation picks a seam</h2>
        <div className="algorithm-steps">
          <article><span className="step-number">01</span><h3>Score a candidate seam</h3><p>Classic scores each pixel from absolute RGB differences between its left and right neighbors and its upper and lower neighbors. Forward energy scores the difference between neighbors that would meet after removal, plus a direction-dependent cost for the path entering that pixel. Edge neighbors are clamped to the image boundary. Protection marks add a large cost; removal marks subtract a large cost.</p></article>
          <article><span className="step-number">02</span><h3>Find the least-cost path</h3><p>Dynamic programming finds the cheapest reachable path through the rows, including the entry cost in Forward energy mode. The engine backtracks from the cheapest bottom pixel to find a vertical seam. For height, it runs the same calculation on a transposed image.</p></article>
          <article><span className="step-number">03</span><h3>Remove or insert</h3><p>After finding a seam, the engine scores the smaller image again and records the path in original-image coordinates. Shrinking removes those pixels. Enlarging inserts new pixels beside distinct recorded paths, blending each with a neighboring pixel. The slider can move through this cached order in either direction.</p></article>
        </div>
      </section>

      <section className="info-limits" aria-labelledby="limits-title">
        <h2 id="limits-title">What to expect</h2>
        <p>Both directions calculate independently from the original image, so the app applies only the selected width or height adjustment. A single insertion pass can enlarge that dimension to twice its original size. A one-pixel dimension has no distinct neighbor to blend with. Forward energy may reduce visible edge breaks, but neither mode guarantees a natural result. Brush guidance changes seam costs but does not guarantee an object will be kept or fully removed, especially if every possible seam crosses it. There is no automatic subject detection. Ties choose the leftmost available path.</p>
      </section>
    </div>
  </section>;
}
