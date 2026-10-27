/** Browser quirks the player adapts to. */

let volumeWritable: boolean | null = null;

/**
 * iOS and iPadOS ignore `volume` on media elements (the hardware buttons own it): write a
 * value to a probe element and read it back. Computed once.
 */
export function isVolumeWritable(): boolean {
  if (volumeWritable === null) {
    try {
      const probe = document.createElement("audio");
      probe.volume = 0.5;
      volumeWritable = probe.volume === 0.5;
    } catch {
      volumeWritable = false;
    }
  }
  return volumeWritable;
}

/** iPhone, iPod and iPad (which reports itself as a Mac with a touch screen). */
export function isAppleMobile(): boolean {
  if (typeof navigator === "undefined") return false;
  const agent = navigator.userAgent;
  return (
    /iPad|iPhone|iPod/.test(agent) || (agent.includes("Macintosh") && navigator.maxTouchPoints > 1)
  );
}
