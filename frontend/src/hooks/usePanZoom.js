import { useCallback, useEffect, useRef, useState } from 'react';

// The world map's native SVG coordinate space (world-atlas 110m projection).
// Pan/zoom manipulate the <svg> viewBox directly - never a CSS/<g> transform
// - so hit-testing on the country <path>/<circle> elements stays exactly
// where it already is.
export const WORLD_VIEWBOX = { x: 0, y: 0, w: 960, h: 421.5 };

// Clamp bounds: can never zoom out past the full world (maxW) or in past
// ~8x (minW = 960 / 8 = 120). Passed as a plain object (not baked into the
// functions) so zoomAt/panBy stay pure and testable against other bounds.
export const ZOOM_BOUNDS = { ...WORLD_VIEWBOX, minW: 120, maxW: WORLD_VIEWBOX.w };

const ZOOM_STEP = 1.4;
const DRAG_THRESHOLD_PX = 4;
const ANIMATION_MS = 160;
const PAN_KEY_FRACTION = 0.08;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

// Pure: zoom `viewBox` by `factor` (>1 = zoom in, <1 = zoom out) while
// keeping the world point (fx, fy) fixed at the same fractional position -
// this is what makes wheel-zoom track the cursor and pinch-zoom track the
// finger midpoint. Exported standalone so the focal-point math is
// unit-testable without a rendered SVG or real pointer events.
export function zoomAt(viewBox, factor, fx, fy, bounds = ZOOM_BOUNDS) {
  const targetW = clamp(viewBox.w / factor, bounds.minW, bounds.maxW);
  const aspect = bounds.h / bounds.w;
  const targetH = targetW * aspect;
  const tx = viewBox.w === 0 ? 0 : (fx - viewBox.x) / viewBox.w;
  const ty = viewBox.h === 0 ? 0 : (fy - viewBox.y) / viewBox.h;
  const x = clamp(fx - tx * targetW, bounds.x, bounds.x + bounds.w - targetW);
  const y = clamp(fy - ty * targetH, bounds.y, bounds.y + bounds.h - targetH);
  return { x, y, w: targetW, h: targetH };
}

// Pure: pan `viewBox` by (dx, dy) world units, clamped so it never leaves
// `bounds` (no dragging the map off into empty space).
export function panBy(viewBox, dx, dy, bounds = ZOOM_BOUNDS) {
  const x = clamp(viewBox.x + dx, bounds.x, bounds.x + bounds.w - viewBox.w);
  const y = clamp(viewBox.y + dy, bounds.y, bounds.y + bounds.h - viewBox.h);
  return { ...viewBox, x, y };
}

function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function easeOutCubic(t) {
  return 1 - (1 - t) ** 3;
}

function pinchState(pointers) {
  const [a, b] = Array.from(pointers.values());
  return {
    distance: Math.hypot(b.x - a.x, b.y - a.y),
    cx: (a.x + b.x) / 2,
    cy: (a.y + b.y) / 2,
  };
}

// Pan/zoom for an inline SVG choropleth. `svgRef` must point at the <svg>
// element - it is used to map client (pointer) coordinates to viewBox
// coordinates and to attach a non-passive wheel listener (React's
// synthetic onWheel is passive by default, so calling preventDefault()
// there cannot stop page scroll).
//
// `config` is an optional `{ viewBox, bounds }` pair, defaulting to the
// world map's own constants - so the original `usePanZoom(svgRef)` call
// site (and its tests) keeps working unchanged. Country view passes its
// own admin-1 viewBox/bounds (see CountryView.js) so the same drag/wheel/
// pinch/keyboard interactions work at any scale.
function usePanZoom(svgRef, config) {
  const {
    viewBox: initialViewBox = WORLD_VIEWBOX,
    bounds = ZOOM_BOUNDS,
    onZoomOutBeyondMin,
    // Flip this false->true in the same render that mounts the svg. The
    // wheel listener attaches via addEventListener (it must, for
    // passive:false), so if the svg mounts in a LATER render than the one
    // that changed this hook's other deps, the attach effect has already
    // run against a null ref and never re-fires - wheel zoom silently dead
    // (the CountryView geometry-vs-coverage load race).
    active = true,
  } = config || {};
  const [viewBox, setViewBox] = useState(initialViewBox);
  // Mirrors `viewBox` but updated eagerly (not via a post-render effect) so
  // synchronous back-to-back calls - e.g. two quick clicks on the zoom
  // button before React re-renders - each read the truly-latest value
  // instead of a stale one.
  const viewBoxRef = useRef(initialViewBox);
  const applyViewBox = useCallback((next) => {
    viewBoxRef.current = next;
    setViewBox(next);
  }, []);

  // Reset to `initialViewBox` whenever its identity changes - true once on
  // mount, and again if a caller swaps in a different config (e.g.
  // CountryView's real viewBox arriving once its lazy-loaded geometry
  // resolves, replacing the placeholder world default). Callers whose
  // config never changes (the world map) see this fire exactly once, a
  // no-op against the state useState already initialized to.
  useEffect(() => {
    applyViewBox(initialViewBox);
  }, [initialViewBox, applyViewBox]);

  const dragRef = useRef(null); // { pointerId, startClientX, startClientY, moved, startViewBox }
  const justDraggedRef = useRef(false);
  const pointersRef = useRef(new Map()); // pointerId -> {x, y}, for pinch
  const pinchRef = useRef(null); // { distance, cx, cy }
  const animationFrameRef = useRef(null);
  // Consecutive zoom-out attempts made while already at minimum zoom (fully
  // zoomed out). Requiring 2 in a row before firing onZoomOutBeyondMin is
  // hysteresis - a single extra wheel notch or button click at the boundary
  // (easy to do by accident) does not eject the user; any zoom-in clears it.
  const zoomOutBeyondMinCountRef = useRef(0);

  const registerZoomOutAttempt = useCallback((prevViewBox) => {
    if (!onZoomOutBeyondMin) return;
    const atMinZoom = prevViewBox.w >= bounds.maxW - 0.01;
    if (!atMinZoom) return;
    zoomOutBeyondMinCountRef.current += 1;
    if (zoomOutBeyondMinCountRef.current >= 2) {
      zoomOutBeyondMinCountRef.current = 0;
      onZoomOutBeyondMin();
    }
  }, [bounds, onZoomOutBeyondMin]);

  const clientToWorld = useCallback((clientX, clientY, vb) => {
    const node = svgRef.current;
    if (!node) return { x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 };
    const rect = node.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return { x: vb.x + vb.w / 2, y: vb.y + vb.h / 2 };
    return {
      x: vb.x + ((clientX - rect.left) / rect.width) * vb.w,
      y: vb.y + ((clientY - rect.top) / rect.height) * vb.h,
    };
  }, [svgRef]);

  const animateTo = useCallback((target) => {
    if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
    if (prefersReducedMotion() || typeof requestAnimationFrame !== 'function') {
      applyViewBox(target);
      return;
    }
    const from = viewBoxRef.current;
    const start = performance.now();
    const step = (now) => {
      const t = clamp((now - start) / ANIMATION_MS, 0, 1);
      const eased = easeOutCubic(t);
      applyViewBox({
        x: from.x + (target.x - from.x) * eased,
        y: from.y + (target.y - from.y) * eased,
        w: from.w + (target.w - from.w) * eased,
        h: from.h + (target.h - from.h) * eased,
      });
      animationFrameRef.current = t < 1 ? requestAnimationFrame(step) : null;
    };
    animationFrameRef.current = requestAnimationFrame(step);
  }, [applyViewBox]);

  useEffect(() => () => {
    if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
  }, []);

  const zoomIn = useCallback(() => {
    zoomOutBeyondMinCountRef.current = 0;
    const prev = viewBoxRef.current;
    animateTo(zoomAt(prev, ZOOM_STEP, prev.x + prev.w / 2, prev.y + prev.h / 2, bounds));
  }, [animateTo, bounds]);

  const zoomOut = useCallback(() => {
    const prev = viewBoxRef.current;
    registerZoomOutAttempt(prev);
    animateTo(zoomAt(prev, 1 / ZOOM_STEP, prev.x + prev.w / 2, prev.y + prev.h / 2, bounds));
  }, [animateTo, bounds, registerZoomOutAttempt]);

  const reset = useCallback(() => animateTo(initialViewBox), [animateTo, initialViewBox]);

  // Eases toward `factor`x zoom centered on a given world point (fx, fy) -
  // unlike zoomIn/zoomOut, which always zoom on the current viewBox center.
  // Used for "double-click a non-drillable country" - zoom in on that
  // country's own centroid rather than wherever the viewport happens to be
  // pointed.
  const zoomToward = useCallback((fx, fy, factor) => {
    const prev = viewBoxRef.current;
    animateTo(zoomAt(prev, factor, fx, fy, bounds));
  }, [animateTo, bounds]);

  // Wheel must preventDefault to stop the page scrolling while over the
  // map. Attached imperatively (not via JSX onWheel) because React treats
  // onWheel as a passive listener by default.
  useEffect(() => {
    if (!active) return undefined;
    const node = svgRef.current;
    if (!node) return undefined;
    const onWheel = (event) => {
      event.preventDefault();
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
      const prev = viewBoxRef.current;
      const isZoomOut = event.deltaY >= 0;
      if (isZoomOut) {
        registerZoomOutAttempt(prev);
      } else {
        zoomOutBeyondMinCountRef.current = 0;
      }
      const factor = isZoomOut ? 1 / ZOOM_STEP : ZOOM_STEP;
      const { x: fx, y: fy } = clientToWorld(event.clientX, event.clientY, prev);
      applyViewBox(zoomAt(prev, factor, fx, fy, bounds));
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    return () => node.removeEventListener('wheel', onWheel);
  }, [svgRef, clientToWorld, applyViewBox, bounds, registerZoomOutAttempt, active]);

  const handlePointerDown = useCallback((event) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    // Do NOT capture the pointer here. Capturing on pointerdown retargets the
    // follow-up click AND dblclick to the <svg> (compat mouse events fire on
    // the capture element, not the country <path> under the cursor), so a
    // plain click never opens the panel and a double-click never drills.
    // Capture is set later, only once an actual drag begins (handlePointerMove).
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointersRef.current.size === 2) {
      // A second finger landed: hand off from single-pointer drag to pinch.
      dragRef.current = null;
      pinchRef.current = pinchState(pointersRef.current);
      return;
    }
    dragRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      moved: false,
      startViewBox: viewBoxRef.current,
    };
  }, []);

  const handlePointerMove = useCallback((event) => {
    if (pointersRef.current.has(event.pointerId)) {
      pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    }

    // Two fingers down: pinch-zoom around their midpoint, incrementally
    // frame to frame. TODO(phase-b): exercised manually only - jsdom has no
    // touch/pointer geometry, so this path carries no automated coverage.
    // Wheel + buttons + drag are the primary, fully-tested interactions; if
    // this ever misbehaves on-device, it is safe to strip without touching
    // the rest of the hook.
    if (pointersRef.current.size === 2 && pinchRef.current) {
      const next = pinchState(pointersRef.current);
      if (pinchRef.current.distance > 0) {
        const prev = viewBoxRef.current;
        const factor = next.distance / pinchRef.current.distance;
        const { x: fx, y: fy } = clientToWorld(next.cx, next.cy, prev);
        applyViewBox(zoomAt(prev, factor, fx, fy, bounds));
      }
      pinchRef.current = next;
      return;
    }

    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const dxClient = event.clientX - drag.startClientX;
    const dyClient = event.clientY - drag.startClientY;
    if (!drag.moved && Math.hypot(dxClient, dyClient) > DRAG_THRESHOLD_PX) {
      drag.moved = true;
      // Now that a real drag is underway, capture the pointer so panning keeps
      // tracking even if the cursor leaves the svg. Safe here (a moved pointer
      // is a drag, not a click), unlike capturing on pointerdown. Best-effort:
      // setPointerCapture throws on a stale pointer id / under jsdom.
      try {
        svgRef.current?.setPointerCapture(event.pointerId);
      } catch {
        /* capture is a nicety; drag still works while the pointer is over the svg */
      }
    }
    if (!drag.moved) return;
    const node = svgRef.current;
    if (!node) return;
    const rect = node.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const dx = -dxClient * (drag.startViewBox.w / rect.width);
    const dy = -dyClient * (drag.startViewBox.h / rect.height);
    applyViewBox(panBy(drag.startViewBox, dx, dy, bounds));
  }, [svgRef, clientToWorld, applyViewBox, bounds]);

  const endPointer = useCallback((event) => {
    pointersRef.current.delete(event.pointerId);
    if (pointersRef.current.size < 2) pinchRef.current = null;
    const drag = dragRef.current;
    if (drag && drag.pointerId === event.pointerId) {
      justDraggedRef.current = drag.moved;
      dragRef.current = null;
    }
  }, []);

  // Suppress the click that follows a drag: capture-phase on the <svg>
  // fires before the target country <path>/<circle>'s own onClick, so a
  // pan never opens the country panel. A clean click (<4px movement)
  // leaves justDraggedRef false and falls through untouched.
  const handleClickCapture = useCallback((event) => {
    if (justDraggedRef.current) {
      event.stopPropagation();
      justDraggedRef.current = false;
    }
  }, []);

  const handleKeyDown = useCallback((event) => {
    switch (event.key) {
      case '+':
      case '=':
        event.preventDefault();
        zoomIn();
        break;
      case '-':
      case '_':
        event.preventDefault();
        zoomOut();
        break;
      case 'ArrowUp':
      case 'ArrowDown':
      case 'ArrowLeft':
      case 'ArrowRight': {
        event.preventDefault();
        const prev = viewBoxRef.current;
        let dx = 0;
        let dy = 0;
        if (event.key === 'ArrowLeft') dx = -prev.w * PAN_KEY_FRACTION;
        if (event.key === 'ArrowRight') dx = prev.w * PAN_KEY_FRACTION;
        if (event.key === 'ArrowUp') dy = -prev.h * PAN_KEY_FRACTION;
        if (event.key === 'ArrowDown') dy = prev.h * PAN_KEY_FRACTION;
        applyViewBox(panBy(prev, dx, dy, bounds));
        break;
      }
      default:
        break;
    }
  }, [zoomIn, zoomOut, applyViewBox, bounds]);

  const isFullView = viewBox.w >= bounds.maxW - 0.01;
  const isMaxZoom = viewBox.w <= bounds.minW + 0.01;

  return {
    viewBox,
    zoomIn,
    zoomOut,
    zoomToward,
    reset,
    canZoomIn: !isMaxZoom,
    canZoomOut: !isFullView,
    handlers: {
      onPointerDown: handlePointerDown,
      onPointerMove: handlePointerMove,
      onPointerUp: endPointer,
      onPointerCancel: endPointer,
      onClickCapture: handleClickCapture,
      onKeyDown: handleKeyDown,
    },
  };
}

export default usePanZoom;
