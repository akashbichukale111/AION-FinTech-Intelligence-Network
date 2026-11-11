/**
 * PayPal Sonic Brand Audio Engine
 * Pure Web Audio API synthesis - zero external audio files or latency.
 * Recreates the authentic PayPal ascending major chime (G4 -> C5 -> E5 -> G5 -> C6).
 */

class PayPalAudioEngine {
  private ctx: AudioContext | null = null;
  private hasPlayedAutoplay = false;

  private getContext(): AudioContext | null {
    if (typeof window === "undefined") return null;
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (AudioCtx) {
        this.ctx = new AudioCtx();
      }
    }
    if (this.ctx && this.ctx.state === "suspended") {
      void this.ctx.resume();
    }
    return this.ctx;
  }

  /**
   * Plays the official PayPal ascending sonic brand chime:
   * Notes: G4 (392Hz) -> C5 (523.25Hz) -> E5 (659.25Hz) -> G5 (783.99Hz) -> C6 (1046.5Hz)
   */
  public playChime(volume: number = 0.25): void {
    try {
      const ctx = this.getContext();
      if (!ctx) return;

      const now = ctx.currentTime;
      const notes = [
        { freq: 392.00, start: 0.00, dur: 0.14 }, // G4
        { freq: 523.25, start: 0.08, dur: 0.16 }, // C5
        { freq: 659.25, start: 0.16, dur: 0.18 }, // E5
        { freq: 783.99, start: 0.24, dur: 0.28 }, // G5
        { freq: 1046.50, start: 0.32, dur: 0.70 } // C6 (High bell resolve)
      ];

      notes.forEach(({ freq, start, dur }) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();

        // Warm chime mix: sine fundamental + subtle harmonic
        osc.type = "sine";
        osc.frequency.setValueAtTime(freq, now + start);

        // Envelope
        gain.gain.setValueAtTime(0.0001, now + start);
        gain.gain.exponentialRampToValueAtTime(volume, now + start + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + start + dur);

        osc.connect(gain);
        gain.connect(ctx.destination);

        osc.start(now + start);
        osc.stop(now + start + dur + 0.05);
      });
    } catch {
      // Audio autoplay gracefully suppressed if blocked
    }
  }

  /**
   * Automatically attempts to play on page load.
   * If browser autoplay policy suspends it, attaches a one-time user interaction listener.
   */
  public initAutoplay(): void {
    if (this.hasPlayedAutoplay) return;

    const playOnce = () => {
      if (this.hasPlayedAutoplay) return;
      this.hasPlayedAutoplay = true;
      this.playChime(0.22);
      window.removeEventListener("click", playOnce);
      window.removeEventListener("keydown", playOnce);
      window.removeEventListener("pointerdown", playOnce);
    };

    // Attempt direct play
    const ctx = this.getContext();
    if (ctx && ctx.state === "running") {
      this.hasPlayedAutoplay = true;
      this.playChime(0.22);
    } else {
      // Wait for the first user click/touch to chime
      window.addEventListener("click", playOnce, { once: true, passive: true });
      window.addEventListener("keydown", playOnce, { once: true, passive: true });
      window.addEventListener("pointerdown", playOnce, { once: true, passive: true });
    }
  }
}

export const paypalAudio = new PayPalAudioEngine();
