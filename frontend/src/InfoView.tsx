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
            <li><strong>Choose width or height.</strong> Enter a target size or move the slider. You can adjust one dimension at a time.</li>
            <li><strong>Save the PNG.</strong> Highlight saves an original-size marked image; Modify saves the resized image. Restore original returns the target to its starting size.</li>
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
          <article><span className="step-number">01</span><h3>Score local detail</h3><p>Each pixel gets a backward-energy score: the sum of absolute RGB differences between its left and right neighbors and between its upper and lower neighbors. Edge neighbors are clamped to the image boundary. Strong color changes tend to cost more.</p></article>
          <article><span className="step-number">02</span><h3>Find the least-cost path</h3><p>Dynamic programming adds each pixel’s energy to the cheapest reachable pixel in the previous row. The engine backtracks from the cheapest bottom pixel to find a minimum-energy vertical seam. For height, it runs the same calculation on a transposed image.</p></article>
          <article><span className="step-number">03</span><h3>Remove and repeat</h3><p>After removing one seam, the engine scores the smaller image again. It records every removed pixel in original-image coordinates, so the slider can move forward or backward through the cached order without finding those seams again.</p></article>
        </div>
      </section>

      <section className="info-limits" aria-labelledby="limits-title">
        <h2 id="limits-title">What to expect</h2>
        <p>Both directions calculate independently from the original image, so the app applies only the selected width or height adjustment. It can shrink but cannot enlarge an image. This is local RGB-difference energy, with no subject detection or protected-region mask; a low-energy seam can still cross something you care about. Ties choose the leftmost available path.</p>
      </section>
    </div>
  </section>;
}
