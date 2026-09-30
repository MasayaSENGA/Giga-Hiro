/* ギガひろ store assets - shared runtime.
   1. waits for fonts and images,
   2. places rings on the exact pixel boxes found by render_assets.py (build/geometry.js),
   3. runs the page's own window.layout(H) hook (callouts, wires ...),
   4. self-checks text (no wrapping, inside canvas / column) and block overlaps,
   then sets window.__READY = true and window.__ISSUES = [...] for render_assets.py. */
(function () {
  "use strict";
  const GEOM = window.GEOM || {};
  const issues = [];
  const SVGNS = "http://www.w3.org/2000/svg";

  function canvasEl() { return document.querySelector(".canvas"); }

  // rect relative to the canvas (CSS px)
  function rel(r) {
    const c = canvasEl().getBoundingClientRect();
    return { x: r.left - c.left, y: r.top - c.top, w: r.width, h: r.height,
             r: r.right - c.left, b: r.bottom - c.top,
             cx: r.left - c.left + r.width / 2, cy: r.top - c.top + r.height / 2 };
  }
  function rectOf(el) { return rel(el.getBoundingClientRect()); }

  // absolute (canvas) rect of a named pixel box inside a figure's image
  function boxRect(fig, name) {
    const g = GEOM[fig.dataset.geom];
    if (!g) throw new Error("no geometry for " + fig.dataset.geom);
    const b = g.boxes[name];
    if (!b) throw new Error("no box " + name + " in " + fig.dataset.geom);
    const img = fig.querySelector("img");
    const ir = img.getBoundingClientRect();
    const s = ir.width / g.w;
    return rel({ left: ir.left + b[0] * s, top: ir.top + b[1] * s,
                 right: ir.left + b[2] * s, bottom: ir.top + b[3] * s,
                 width: (b[2] - b[0]) * s, height: (b[3] - b[1]) * s });
  }

  function placeRings() {
    document.querySelectorAll("[data-geom]").forEach((fig) => {
      const g = GEOM[fig.dataset.geom];
      if (!g) { issues.push("missing geometry: " + fig.dataset.geom); return; }
      const img = fig.querySelector("img");
      const s = img.clientWidth / g.w;
      const ratio = Math.abs(img.clientHeight / img.clientWidth - g.h / g.w);
      if (ratio > 0.01) issues.push("aspect ratio changed: " + fig.dataset.geom);
      // s = final-image px per source px; above 1 the capture would be enlarged (blurry)
      if (s > 1 + 1e-6) issues.push("capture enlarged beyond its own pixels: " + fig.dataset.geom + " s=" + s.toFixed(3));
      issues.push("note: " + fig.dataset.geom + " scale " + s.toFixed(3));
      fig.querySelectorAll(".ring").forEach((ring) => {
        const b = g.boxes[ring.dataset.box];
        if (!b) { issues.push("missing box " + ring.dataset.box); return; }
        const pad = parseFloat(ring.dataset.pad || "5");
        const bw = parseFloat(getComputedStyle(ring).borderTopWidth) || 0;
        ring.style.left = (img.offsetLeft + b[0] * s - pad - bw) + "px";
        ring.style.top = (img.offsetTop + b[1] * s - pad - bw) + "px";
        ring.style.width = ((b[2] - b[0]) * s + 2 * (pad + bw)) + "px";
        ring.style.height = ((b[3] - b[1]) * s + 2 * (pad + bw)) + "px";
      });
    });
  }

  // ---- wires (SVG connectors) ----
  function wiresSvg() {
    let svg = document.querySelector("svg.wires");
    if (!svg) {
      svg = document.createElementNS(SVGNS, "svg");
      svg.setAttribute("class", "wires");
      const c = canvasEl();
      svg.setAttribute("width", c.clientWidth);
      svg.setAttribute("height", c.clientHeight);
      svg.setAttribute("viewBox", `0 0 ${c.clientWidth} ${c.clientHeight}`);
      c.appendChild(svg);
    }
    return svg;
  }
  function path(d, opts = {}) {
    const svg = wiresSvg();
    const p = document.createElementNS(SVGNS, "path");
    p.setAttribute("d", d);
    p.setAttribute("fill", "none");
    p.setAttribute("stroke", opts.color || "#f59e0b");
    p.setAttribute("stroke-width", opts.width || 3);
    p.setAttribute("stroke-linecap", "round");
    p.setAttribute("stroke-linejoin", "round");
    if (opts.dash) p.setAttribute("stroke-dasharray", opts.dash);
    svg.appendChild(p);
    return p;
  }
  function dot(x, y, r = 4.5, color = "#f59e0b") {
    const svg = wiresSvg();
    const c = document.createElementNS(SVGNS, "circle");
    c.setAttribute("cx", x); c.setAttribute("cy", y); c.setAttribute("r", r);
    c.setAttribute("fill", color);
    c.setAttribute("stroke", "#fff"); c.setAttribute("stroke-width", 2);
    svg.appendChild(c);
  }
  // arrow head at (x,y) pointing along angle (radians)
  function head(x, y, ang, opts = {}) {
    const L = opts.size || 14, W = (opts.size || 14) * 0.62;
    const bx = x - L * Math.cos(ang), by = y - L * Math.sin(ang);
    const nx = -Math.sin(ang), ny = Math.cos(ang);
    const d = `M ${x} ${y} L ${bx + nx * W} ${by + ny * W} L ${bx - nx * W} ${by - ny * W} Z`;
    const svg = wiresSvg();
    const p = document.createElementNS(SVGNS, "path");
    p.setAttribute("d", d);
    p.setAttribute("fill", opts.color || "#f59e0b");
    p.setAttribute("stroke", opts.color || "#f59e0b");
    p.setAttribute("stroke-width", 2);
    p.setAttribute("stroke-linejoin", "round");
    svg.appendChild(p);
  }
  // direction in which a path arrives at its last point. Points that coincide with the
  // end are skipped: for a cubic whose c1 equals p1 the end tangent is p1 - c0 (then
  // p1 - p0), never atan2(0, 0) = 0 (which would turn the head to the right).
  function endAngle(pts) {
    const p1 = pts[pts.length - 1];
    for (let i = pts.length - 2; i >= 0; i--) {
      const q = pts[i];
      if (Math.hypot(p1.x - q.x, p1.y - q.y) > 1e-6) return Math.atan2(p1.y - q.y, p1.x - q.x);
    }
    throw new Error("arrow has zero length");
  }
  // cubic bezier arrow from p0 to p1 with control points c0,c1; head at p1.
  // Returns the head angle (radians, 90deg = straight down) and the shaft end.
  function curveArrow(p0, c0, c1, p1, opts = {}) {
    const inset = (opts.size || 14) * 0.7;
    const ang = endAngle([p0, c0, c1, p1]);
    const ex = p1.x - inset * Math.cos(ang), ey = p1.y - inset * Math.sin(ang);
    path(`M ${p0.x} ${p0.y} C ${c0.x} ${c0.y}, ${c1.x} ${c1.y}, ${ex} ${ey}`, opts);
    head(p1.x, p1.y, ang, opts);
    return { ang, end: { x: ex, y: ey } };
  }
  // straight arrow from p0 to p1; the head points along p0 -> p1
  function lineArrow(p0, p1, opts = {}) {
    const inset = (opts.size || 14) * 0.7;
    const ang = endAngle([p0, p1]);
    const ex = p1.x - inset * Math.cos(ang), ey = p1.y - inset * Math.sin(ang);
    path(`M ${p0.x} ${p0.y} L ${ex} ${ey}`, opts);
    head(p1.x, p1.y, ang, opts);
    return { ang, end: { x: ex, y: ey } };
  }
  function deg(rad) { return rad * 180 / Math.PI; }

  // ---- self checks ----
  function textRects(node) {
    const range = document.createRange();
    range.selectNodeContents(node);
    return [...range.getClientRects()].filter((r) => r.width > 0.5 && r.height > 0.5);
  }
  function checkText() {
    const c = canvasEl();
    const cr = c.getBoundingClientRect();
    const walker = document.createTreeWalker(c, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      const t = n.textContent.trim();
      if (!t) continue;
      const el = n.parentElement;
      if (el.closest("svg")) continue;
      const rects = textRects(n);
      if (!rects.length) continue;
      const tops = new Set(rects.map((r) => Math.round(r.top / 4)));
      if (tops.size > 1) issues.push("text wraps onto several lines: " + t);
      for (const r of rects) {
        if (r.left < cr.left - 0.5 || r.right > cr.right + 0.5 || r.top < cr.top - 0.5 || r.bottom > cr.bottom + 0.5) {
          issues.push("text outside canvas: " + t);
          break;
        }
      }
      const col = el.closest("[data-col]");
      if (col) {
        const colR = col.getBoundingClientRect();
        const lim = colR.left + parseFloat(col.dataset.col || colR.width);
        for (const r of rects) {
          if (r.right > lim + 0.5) { issues.push(`text wider than column (${Math.round(r.right - colR.left)} > ${col.dataset.col}): ${t}`); break; }
        }
      }
      // text must not be clipped by an overflow:hidden ancestor
      let a = el;
      while (a && a !== c) {
        const cs = getComputedStyle(a);
        if (cs.overflow === "hidden" || cs.overflowX === "hidden") {
          const ar = a.getBoundingClientRect();
          for (const r of rects) {
            if (r.right > ar.right + 0.5 || r.left < ar.left - 0.5 || r.bottom > ar.bottom + 0.5 || r.top < ar.top - 0.5) {
              issues.push("text clipped by container: " + t);
              break;
            }
          }
        }
        a = a.parentElement;
      }
    }
  }
  function checkBlocks() {
    const blocks = [...document.querySelectorAll("[data-block]")];
    const c = canvasEl().getBoundingClientRect();
    const rs = blocks.map((b) => [b, b.getBoundingClientRect()]);
    for (const [b, r] of rs) {
      if (r.left < c.left - 0.5 || r.top < c.top - 0.5 || r.right > c.right + 0.5 || r.bottom > c.bottom + 0.5)
        issues.push("block outside canvas: " + b.dataset.block);
    }
    for (let i = 0; i < rs.length; i++) {
      for (let j = i + 1; j < rs.length; j++) {
        const [a, ra] = rs[i], [b, rb] = rs[j];
        const ox = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
        const oy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
        if (ox > 0.5 && oy > 0.5) issues.push(`blocks overlap: ${a.dataset.block} / ${b.dataset.block}`);
      }
    }
  }
  function checkFonts() {
    [...document.fonts].forEach((f) => {
      if (f.status !== "loaded") issues.push("font not loaded: " + f.family + " (" + f.status + ")");
    });
    if (![...document.fonts].some((f) => f.status === "loaded")) issues.push("no web font loaded");
  }

  window.H = { rectOf, boxRect, path, dot, head, curveArrow, lineArrow, deg, issues };

  async function run() {
    try {
      await document.fonts.load('800 56px "GH Sans"', "ギガひろ");
      await document.fonts.load('500 26px "GH Sans"', "ギガひろ");
      await document.fonts.ready;
      const imgs = [...document.images];
      await Promise.all(imgs.map((i) => (i.complete ? Promise.resolve() : new Promise((r) => { i.onload = i.onerror = r; }))));
      await Promise.all(imgs.map((i) => (i.decode ? i.decode().catch(() => {}) : null)));
      imgs.forEach((i) => { if (!i.naturalWidth) issues.push("image failed: " + i.getAttribute("src")); });
      checkFonts();
      placeRings();
      if (typeof window.layout === "function") await window.layout(window.H);
      checkText();
      checkBlocks();
    } catch (e) {
      issues.push("exception: " + (e && e.stack || e));
    }
    window.__ISSUES = issues.filter((m) => !m.startsWith("note:"));
    window.__NOTES = issues.filter((m) => m.startsWith("note:"));
    window.__READY = true;
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", run);
  else run();
})();
