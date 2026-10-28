/** A late older read may acknowledge only the progress events it began with. */
export class CanvasProgressTracker {
  private requested = 0;
  private consumed = 0;
  notify(): void { this.requested += 1; }
  beginRead(): number { return this.requested; }
  completeRead(token: number): boolean {
    this.consumed = Math.max(this.consumed, Math.min(token, this.requested));
    return this.requested > this.consumed;
  }
}
