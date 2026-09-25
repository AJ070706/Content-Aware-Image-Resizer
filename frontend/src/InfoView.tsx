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
        <p>Instead of stretching every pixel, the app finds low-cost connected paths called seams. Shrinking removes them; enlarging inserts blended pixels beside distinct seams. The unremoved original pixels keep their colors.</p>
      </div>

      <div className="info-columns">
        <section className="info-card" aria-labelledby="how-to-use">
          <h2 id="how-to-use">How to use it</h2>
          <ol>
            <li><strong>Open an image.</strong> Width and height seam orders start calculating in the background.</li>
            <li><strong>Choose a mode.</strong> Highlight seams marks the chosen paths red; Modify image removes or inserts pixels in the preview.</li>
            <li><strong>Choose seam quality.</strong> Classic prefers low-detail pixels. Forward energy also considers the new edges that removal would create. Switching quality recalculates both seam directions.</li>
            <li><strong>Guide the seams if needed.</strong> In Highlight mode, paint green over detail to protect or pink over areas to favor for removal. The same guidance affects Modify mode and both directions. Each changed stroke restarts both background calculations without changing your target.</li>
            <li><strong>Choose width or height.</strong> Enter a target size or move the slider to shrink or enlarge up to twice the original size. Switching direction starts the newly selected dimension at its original size while both background calculations continue.</li>
            <li><strong>Inspect the energy map.</strong> Use the workspace toggle to see the costs behind the first seam for your chosen direction and quality. Red marks that seam. Turn the map off to resume your previous view and target.</li>
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

      <section className="info-algorithm" aria-labelledby="transitions-title">
        <p className="eyebrow">WORKFLOW</p>
        <h2 id="transitions-title">How controls work together</h2>
        <ul className="transition-list">
          <li><strong>Highlight ↔ Modify:</strong> The direction, target, seam quality, and painted guidance stay in place. Paint only in Highlight, where the original image provides stable brush coordinates. Use “Edit guidance in Highlight mode” from Modify; its preview will update when you return.</li>
          <li><strong>Width ↔ Height:</strong> The new direction begins at its original size. Any preview still drawing for the previous direction is replaced. Both seam orders continue calculating and the guidance applies to both.</li>
          <li><strong>Classic ↔ Forward:</strong> Keep the current direction, target, mode, and guidance. Both orders recalculate under the selected quality; the preview catches up as seams become available.</li>
          <li><strong>Energy map:</strong> Temporarily show first-seam costs on the original image. Editing and saving pause in this view; closing it restores the selected mode, target, comparison, and brush tool.</li>
          <li><strong>Open another image:</strong> Return to the Workspace in Highlight at the new image’s original width and fit-to-window zoom, with no painted guidance, comparison, or energy map. Seam quality remains selected.</li>
        </ul>
      </section>

      <section className="info-algorithm" aria-labelledby="algorithm-title">
        <p className="eyebrow">UNDER THE HOOD</p>
        <h2 id="algorithm-title">How this implementation picks a seam</h2>
        <div className="algorithm-steps">
          <article><span className="step-number">01</span><h3>Score a candidate seam</h3><p>Classic scores each pixel from absolute RGB differences between its left and right neighbors and its upper and lower neighbors. Forward energy scores the difference between neighbors that would meet after removal, plus a direction-dependent cost for the path entering that pixel. Edge neighbors are clamped to the image boundary. Protection marks add a large cost; removal marks subtract a large cost.</p></article>
          <article><span className="step-number">02</span><h3>Find the least-cost path</h3><p>Dynamic programming finds the cheapest reachable path through the rows, including the entry cost in Forward energy mode. The engine backtracks from the cheapest bottom pixel to find a vertical seam. For height, it runs the same calculation on a transposed image.</p></article>
          <article><span className="step-number">03</span><h3>Remove or insert</h3><p>After finding a seam, the engine scores the smaller image again and records the path in original-image coordinates. Shrinking removes those pixels. Enlarging inserts new pixels beside distinct recorded paths, blending each with a neighboring pixel. The slider can move through this cached order in either direction.</p></article>
        </div>
      </section>

      <section className="info-algorithm" aria-labelledby="energy-title">
        <p className="eyebrow">TWO WAYS TO SCORE A PATH</p>
        <h2 id="energy-title">Classic and Forward energy</h2>
        <div className="algorithm-steps">
          <article><span className="step-number">CLASSIC</span><h3>Preserve existing contrast</h3><p>At each pixel, add the absolute RGB difference between its left and right neighbors to the difference between its upper and lower neighbors. A low score means little local contrast. The least-cost connected path is removed first. This is also called backward energy.</p></article>
          <article><span className="step-number">FORWARD</span><h3>Consider the new edge</h3><p>Start with the RGB difference between left and right neighbors, which would meet after removal. If the path enters diagonally, add the difference between the upper neighbor and the side neighbor exposed by that move. A straight entry adds nothing extra. This can avoid some breaks that Classic energy overlooks.</p></article>
          <article><span className="step-number">BOTH MODES</span><h3>Follow the cheapest connected route</h3><p>Dynamic programming accumulates the cheapest cost to reach every pixel from the preceding row. Green guidance adds a large penalty; pink subtracts one. The cheapest bottom endpoint gives the first vertical seam. Height uses the same calculation on a transposed image.</p></article>
        </div>
      </section>

      <section className="info-algorithm" aria-labelledby="map-title">
        <p className="eyebrow">SEE THE DECISION</p>
        <h2 id="map-title">Reading the energy map</h2>
        <p>The map displays the <strong>cumulative minimum path cost</strong> to reach each pixel in the original image, using the selected quality mode and brush guidance. Blue is lower cost, orange is higher cost, and red is the first selected seam. Colors are scaled separately within each row for width or each column for height, so compare positions along that row or column rather than colors across the whole image. Forward costs depend on how the path enters a pixel, so this cumulative view is more faithful than a simple pixel-brightness map. After each removal the image changes and the engine recalculates costs; the map does not show later seams.</p>
      </section>

      <section className="info-algorithm" aria-labelledby="enlarge-title">
        <p className="eyebrow">ADDING PIXELS</p>
        <h2 id="enlarge-title">How enlargement works</h2>
        <p>To enlarge, the engine repeatedly finds and removes low-cost seams on a <strong>temporary shrinking copy</strong>, recording each path’s coordinates in the original image. This keeps the paths distinct. It then starts from the untouched original and inserts one pixel beside each selected path pixel. The inserted RGB and alpha values average that source pixel with the neighboring original pixel; at the far edge it uses the neighbor on the other side. A one-pixel-wide dimension has no different neighbor, so that pixel is duplicated. Horizontal enlargement uses the same process after transposing the image. A single pass can add at most one pixel per original pixel, so the limit is 2× in the selected dimension. Blending softens duplication but cannot invent detail that was absent from the source.</p>
      </section>

      <section className="info-limits" aria-labelledby="limits-title">
        <h2 id="limits-title">What to expect</h2>
        <p>Both directions calculate independently from the original image, so the app applies only the selected width or height adjustment. A single insertion pass can enlarge that dimension to twice its original size. A one-pixel dimension has no distinct neighbor to blend with. Forward energy may reduce visible edge breaks, but neither mode guarantees a natural result. Brush guidance changes seam costs but does not guarantee an object will be kept or fully removed, especially if every possible seam crosses it. There is no automatic subject detection. Ties choose the leftmost available path.</p>
      </section>
    </div>
  </section>;
}
