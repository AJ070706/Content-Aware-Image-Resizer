/** Original-image pixels used to guide both seam directions. */
export type MaskMark = 0 | 1 | 2;
export type MaskPoint = { x: number; y: number };
export type MaskChange = { index: number; previous: MaskMark };
export type MaskBounds = { left: number; top: number; right: number; bottom: number };

export class BrushMask {
  readonly width: number;
  readonly height: number;
  readonly pixels: Uint8Array;
  protectedCount = 0;
  removedCount = 0;

  constructor(width: number, height: number) {
    this.width = width;
    this.height = height;
    this.pixels = new Uint8Array(width * height);
  }

  /** Rasterize a round, solid stroke once per source pixel, including the end caps. */
  paint(from: MaskPoint, to: MaskPoint, diameter: number, mark: MaskMark): { changes: MaskChange[]; bounds: MaskBounds | null } {
    const radius = diameter / 2;
    const left = Math.max(0, Math.floor(Math.min(from.x, to.x) - radius));
    const top = Math.max(0, Math.floor(Math.min(from.y, to.y) - radius));
    const right = Math.min(this.width, Math.ceil(Math.max(from.x, to.x) + radius));
    const bottom = Math.min(this.height, Math.ceil(Math.max(from.y, to.y) + radius));
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const lengthSquared = dx * dx + dy * dy;
    const changes: MaskChange[] = [];
    let bounds: MaskBounds | null = null;

    for (let y = top; y < bottom; y++) {
      for (let x = left; x < right; x++) {
        const px = x + .5;
        const py = y + .5;
        const projection = lengthSquared ? Math.max(0, Math.min(1, ((px - from.x) * dx + (py - from.y) * dy) / lengthSquared)) : 0;
        const distanceX = px - from.x - projection * dx;
        const distanceY = py - from.y - projection * dy;
        if (distanceX * distanceX + distanceY * distanceY > radius * radius) continue;
        const index = y * this.width + x;
        const previous = this.pixels[index] as MaskMark;
        if (previous === mark) continue;
        this.update(index, mark);
        changes.push({ index, previous });
        if (!bounds) bounds = { left: x, top: y, right: x + 1, bottom: y + 1 };
        else {
          bounds.left = Math.min(bounds.left, x);
          bounds.top = Math.min(bounds.top, y);
          bounds.right = Math.max(bounds.right, x + 1);
          bounds.bottom = Math.max(bounds.bottom, y + 1);
        }
      }
    }
    return { changes, bounds };
  }

  /** Revert the changed pixels after a failed desktop update. */
  restore(changes: MaskChange[]) {
    for (const { index, previous } of changes) this.update(index, previous);
  }

  private update(index: number, mark: MaskMark) {
    const previous = this.pixels[index];
    if (previous === 1) this.protectedCount--;
    else if (previous === 2) this.removedCount--;
    if (mark === 1) this.protectedCount++;
    else if (mark === 2) this.removedCount++;
    this.pixels[index] = mark;
  }
}
