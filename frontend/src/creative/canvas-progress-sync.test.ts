import { describe, expect, it } from 'vitest';
import { CanvasProgressTracker } from './canvas-progress-sync';

describe('canvas progress read coordination', () => {
  it('does not consume a final-result event with a read begun before that result existed', () => {
    const tracker = new CanvasProgressTracker(); tracker.notify();
    const videosReady = tracker.beginRead(); tracker.notify();
    expect(tracker.completeRead(videosReady)).toBe(true);
    expect(tracker.completeRead(tracker.beginRead())).toBe(false);
  });
  it('does not regress after a newer read already acknowledged the final result', () => {
    const tracker = new CanvasProgressTracker(); tracker.notify(); const first = tracker.beginRead();
    tracker.notify(); expect(tracker.completeRead(tracker.beginRead())).toBe(false);
    expect(tracker.completeRead(first)).toBe(false);
  });
  it('keeps a failed read pending until a later successful read consumes it', () => {
    const tracker = new CanvasProgressTracker(); const old = tracker.beginRead(); tracker.notify();
    tracker.beginRead(); // The request fails: there is deliberately no completion.
    expect(tracker.completeRead(old)).toBe(true);
    expect(tracker.completeRead(tracker.beginRead())).toBe(false);
  });
});
