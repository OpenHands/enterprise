import { useEffect, useRef } from "react";

/**
 * Drifting circle-pixel field with pointer ripples.
 * Matches the customer-center PixelBlast treatment: gray dots, slow noise,
 * and a ring that opens where the pointer moves.
 */
export function PixelField() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return undefined;
    }
    const context = canvas.getContext("2d");
    if (!context) {
      return undefined;
    }

    const pointer = { x: -9999, y: -9999, born: 0 };
    const reduceMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    let frame = 0;
    let running = true;

    const resize = () => {
      const { width, height } = canvas.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 1.25);
      canvas.width = Math.max(1, Math.floor(width * dpr));
      canvas.height = Math.max(1, Math.floor(height * dpr));
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const hash = (x: number, y: number) => {
      const n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
      return n - Math.floor(n);
    };

    const noise = (x: number, y: number) => {
      const x0 = Math.floor(x);
      const y0 = Math.floor(y);
      const fx = x - x0;
      const fy = y - y0;
      const sx = fx * fx * (3 - 2 * fx);
      const sy = fy * fy * (3 - 2 * fy);
      const n00 = hash(x0, y0);
      const n10 = hash(x0 + 1, y0);
      const n01 = hash(x0, y0 + 1);
      const n11 = hash(x0 + 1, y0 + 1);
      return (
        n00 * (1 - sx) * (1 - sy) +
        n10 * sx * (1 - sy) +
        n01 * (1 - sx) * sy +
        n11 * sx * sy
      );
    };

    const draw = (time: number) => {
      if (!running) {
        return;
      }
      const { width, height } = canvas.getBoundingClientRect();
      context.clearRect(0, 0, width, height);

      const spacing = 8;
      const t = reduceMotion ? 0 : time * 0.00022;
      const age = reduceMotion ? 0 : (time - pointer.born) * 0.001;
      const wave = 0.75 * age;

      for (let y = spacing / 2; y < height; y += spacing) {
        for (let x = spacing / 2; x < width; x += spacing) {
          const nx = (x / width) * 2;
          const ny = y / height;
          const field =
            noise(nx * 2 + t, ny * 2) * 0.65 +
            noise(nx * 4 - t * 0.6, ny * 4 + t) * 0.35;
          const dist = Math.hypot(x - pointer.x, y - pointer.y);
          const ring = Math.exp(-((dist / 28 - wave) ** 2) / 0.35);
          const atten = Math.exp(-age) * Math.exp(-dist / 220);
          const feed = field + ring * atten * 1.4;
          if (feed > 0.52) {
            const edge = Math.min(x, y, width - x, height - y) / 28;
            const alpha = Math.min(1, Math.max(0, feed)) * Math.min(1, edge);
            context.fillStyle = `rgba(72, 72, 72, ${0.7 + alpha * 0.3})`;
            context.beginPath();
            context.arc(x, y, 1.7 + Math.min(feed, 1) * 0.8, 0, Math.PI * 2);
            context.fill();
          }
        }
      }

      if (!reduceMotion) {
        frame = window.requestAnimationFrame(draw);
      }
    };

    const onMove = (event: PointerEvent) => {
      const bounds = canvas.getBoundingClientRect();
      pointer.x = event.clientX - bounds.left;
      pointer.y = event.clientY - bounds.top;
      pointer.born = performance.now();
    };

    resize();
    frame = window.requestAnimationFrame(draw);
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    canvas.addEventListener("pointermove", onMove);

    return () => {
      running = false;
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      canvas.removeEventListener("pointermove", onMove);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="absolute inset-0 h-full w-full"
    />
  );
}
