// Live background of the home screen = NetworkMapView + GlobeRenderer on Android:
// the same NASA Blue Marble texture on an orthographic globe (view 35°N 15°E),
// one turn per 5 minutes, light from the upper left, Frankfurt server marker
// and small flights moving over the globe's surface. Purely decorative.
(function () {
  const LAT0 = 35 * Math.PI / 180, LON0 = 15 * Math.PI / 180;
  const RAD_PER_MS = (2 * Math.PI) / 300000;
  const SERVER = [50.11, 8.68];
  // top-view airliner silhouette (half outline, mirrored) — same points as NetworkMapView.planePath
  const PLANE = [[9.0, 0.0], [6.5, 0.9], [2.5, 1.3], [-0.5, 5.8], [-2.2, 5.9], [-0.8, 1.5], [-5.2, 1.3], [-6.8, 3.4], [-8.0, 3.4], [-7.4, 1.0], [-8.6, 0.6]];
  const IMG_CX = 0.5, IMG_CY = 0.5023, IMG_RX = 0.5, IMG_W = 1080, IMG_H = 2160;

  const VS = `attribute vec2 p; varying vec2 v; void main(){ v=p; gl_Position=vec4(p,0.,1.); }`;
  const FS = `precision highp float; varying vec2 v; uniform sampler2D tex; uniform float rot; uniform float alpha;
    uniform vec2 c; uniform float r; uniform vec2 res;
    const float PI=3.14159265; const float LAT0=${LAT0.toFixed(6)}; const float LON0=${LON0.toFixed(6)};
    void main(){
      vec2 px = vec2((v.x*.5+.5)*res.x, (v.y*.5+.5)*res.y);
      vec2 q = (px - c) / r; float rho = length(q);
      if (rho > 1.0) discard;
      float z = sqrt(max(0., 1. - rho*rho));
      float lat = asin(clamp(z*sin(LAT0) + q.y*cos(LAT0), -1., 1.));
      float lon = LON0 + rot + atan(q.x, z*cos(LAT0) - q.y*sin(LAT0));
      float u = fract((lon + PI) / (2.*PI)); float t = (PI/2. - lat) / PI;
      vec3 col = texture2D(tex, vec2(u, t)).rgb;
      vec3 L = normalize(vec3(-0.42, 0.48, 0.77));
      float d = max(dot(vec3(q, z), L), 0.);
      float shade = 0.30 + 0.85 * d;
      col *= shade;
      col += vec3(0.10, 0.22, 0.42) * pow(1. - z, 3.0) * 0.9; // atmosphere at the rim
      float edge = smoothstep(1.0, 0.985, rho);
      gl_FragColor = vec4(col, alpha * edge);
    }`;

  class Globe {
    constructor(stage) {
      this.stage = stage;
      this.gl = null;
      this.cv = document.createElement('canvas'); this.cv.className = 'globe';
      this.fx = document.createElement('canvas'); this.fx.className = 'globe';
      stage.prepend(this.fx); stage.prepend(this.cv);
      stage.style.background = '#050B14 url(assets/world_map.png) no-repeat';
      this.overlay = new Image(); this.overlay.src = 'assets/globe_overlay.png';
      this.flights = []; this.nextSpawn = 0; this.shownAt = 0; this.running = false;
      this.rot0 = 0; this.t0 = performance.now();
      this.initGL();
      this.ro = new ResizeObserver(() => this.layout()); this.ro.observe(stage);
      this.layout();
      for (let i = 0; i < 5; i++) this.spawn(performance.now(), Math.random());
    }
    initGL() {
      const gl = this.cv.getContext('webgl', { premultipliedAlpha: false, alpha: true, antialias: true });
      if (!gl) return;
      const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
      const pr = gl.createProgram();
      gl.attachShader(pr, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(pr);
      if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) return;
      gl.useProgram(pr);
      const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
      const loc = gl.getAttribLocation(pr, 'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
      this.u = {}; for (const n of ['rot', 'alpha', 'c', 'r', 'res', 'tex']) this.u[n] = gl.getUniformLocation(pr, n);
      const tex = gl.createTexture(); const img = new Image();
      img.onload = () => {
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, img);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        this.texReady = true; this.shownAt = performance.now();
      };
      img.src = 'assets/globe_texture.png';
      gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      this.gl = gl;
    }
    layout() {
      const w = this.stage.clientWidth, h = this.stage.clientHeight, dpr = window.devicePixelRatio || 1;
      this.w = w; this.h = h; this.dpr = dpr;
      for (const c of [this.cv, this.fx]) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
      this.landscape = false; // owner: keep the big full-window globe in every window shape
      if (this.landscape) {
        // desktop: free-standing globe in deep space, connect orb sits on its north edge (as on the phone)
        // globe sits in the area above the bottom stats strip; the orb (on its north edge) must fit above it
        const avail = h - (this.reserveBottom || 190);
        this.r = Math.max(120, Math.min((avail - 14) / 2.375, w * 0.30));
        this.cx = w * 0.5; this.cy = 14 + this.r * 1.375;
        this.stage.style.background = `radial-gradient(circle at ${this.cx}px ${this.cy}px, rgba(18,58,102,.55) 0%, rgba(5,11,20,0) ${Math.round(this.r * 1.9)}px), #050B14`;
      } else {
        this.stage.style.background = '#050B14 url(assets/world_map.png) no-repeat';
        const s = Math.max(w / IMG_W, h / IMG_H);
        const dx = (w - IMG_W * s) / 2, dy = (h - IMG_H * s) / 2;
        this.stage.style.backgroundSize = `${IMG_W * s}px ${IMG_H * s}px`;
        this.stage.style.backgroundPosition = `${dx}px ${dy}px`;
        this.cx = dx + IMG_CX * IMG_W * s; this.cy = dy + IMG_CY * IMG_H * s; this.r = IMG_RX * IMG_W * s;
      }
      if (this.onLayout) this.onLayout(this);
    }
    rot(now) { return this.rot0 + (now - this.t0) * RAD_PER_MS; }
    project(latDeg, lonDeg, rot) {
      const p = latDeg * Math.PI / 180, l = lonDeg * Math.PI / 180 - (LON0 + rot);
      const cosc = Math.sin(LAT0) * Math.sin(p) + Math.cos(LAT0) * Math.cos(p) * Math.cos(l);
      const x = Math.cos(p) * Math.sin(l);
      const y = Math.cos(LAT0) * Math.sin(p) - Math.sin(LAT0) * Math.cos(p) * Math.cos(l);
      return { x: this.cx + x * this.r, y: this.cy - y * this.r, vis: cosc };
    }
    spawn(now, progress = 0) {
      const rot = this.rot(now);
      const centerLon = (LON0 + rot) * 180 / Math.PI;
      const rnd = (a, b) => a + Math.random() * (b - a);
      const pick = () => [rnd(-10, 70), centerLon + rnd(-70, 70)];
      const hot = Math.random() < 0.3;
      const a = pick(); const b = hot ? SERVER.slice() : pick();
      this.flights.push({ a, b, hot, start: now - progress * 16000, dur: rnd(15000, 24000), scale: rnd(1.0, 1.35) });
    }
    slerp(a, b, t) {
      const toV = ([lat, lon]) => { const p = lat * Math.PI / 180, l = lon * Math.PI / 180; return [Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p)]; };
      const A = toV(a), B = toV(b);
      const dot = Math.min(1, Math.max(-1, A[0] * B[0] + A[1] * B[1] + A[2] * B[2]));
      const om = Math.acos(dot); if (om < 1e-6) return a;
      const s0 = Math.sin((1 - t) * om) / Math.sin(om), s1 = Math.sin(t * om) / Math.sin(om);
      const v = [A[0] * s0 + B[0] * s1, A[1] * s0 + B[1] * s1, A[2] * s0 + B[2] * s1];
      return [Math.asin(v[2]) * 180 / Math.PI, Math.atan2(v[1], v[0]) * 180 / Math.PI];
    }
    start() { if (this.running) return; this.running = true; const loop = (t) => { if (!this.running) return; this.frame(t); this.raf = requestAnimationFrame(loop); }; this.raf = requestAnimationFrame(loop); }
    stop() { this.running = false; cancelAnimationFrame(this.raf); }
    frame(now) {
      const rot = this.rot(now);
      const gl = this.gl;
      if (gl && this.texReady) {
        gl.viewport(0, 0, this.cv.width, this.cv.height);
        gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
        const d = this.dpr;
        gl.uniform1f(this.u.rot, rot);
        gl.uniform1f(this.u.alpha, Math.min(1, (now - this.shownAt) / 700));
        gl.uniform2f(this.u.c, this.cx * d, (this.h - this.cy) * d);
        gl.uniform1f(this.u.r, this.r * d);
        gl.uniform2f(this.u.res, this.cv.width, this.cv.height);
        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      }
      const g = this.fx.getContext('2d');
      g.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      g.clearRect(0, 0, this.w, this.h);
      if (this.landscape) {
        if (!this.stars || this.starsW !== this.w) { this.starsW = this.w; this.stars = Array.from({ length: 140 }, () => [Math.random() * this.w, Math.random() * this.h, Math.random() * 1.2 + .2, Math.random() * 6.28]); }
        for (const [x, y, sz, ph] of this.stars) { g.globalAlpha = .25 + .35 * (0.5 + 0.5 * Math.sin(now / 1700 + ph)); g.fillStyle = '#CFE8FF'; g.fillRect(x, y, sz, sz); }
        g.globalAlpha = 1;
      }
      if (this.overlay.complete) { g.globalAlpha = 0.22; g.drawImage(this.overlay, this.cx - this.r, this.cy - this.r, this.r * 2, this.r * 2); g.globalAlpha = 1; }
      // flights
      if (now > this.nextSpawn && this.flights.length < 7) { this.spawn(now); this.nextSpawn = now + 900 + Math.random() * 900; }
      this.flights = this.flights.filter(f => now - f.start < f.dur);
      for (const f of this.flights) {
        const t = (now - f.start) / f.dur;
        const fade = Math.min(1, t * 6, (1 - t) * 6);
        const at = (tt) => this.project(...this.slerp(f.a, f.b, Math.max(0, Math.min(1, tt))), rot);
        const h = at(t);
        if (h.vis <= 0.05) continue;
        const vf = fade * Math.min(1, h.vis * 4);
        // contrail: dark pass first, then light pass (as on Android)
        for (const dark of [true, false]) {
          let prev = h;
          for (let j = 1; j <= 14; j++) {
            const tt = t - 0.22 * j / 14; if (tt <= 0) break;
            const q = at(tt); if (q.vis <= 0.05) break;
            const k = 1 - j / 14, maxA = dark ? 95 / 255 : 165 / 255;
            g.strokeStyle = dark ? `rgba(4,18,31,${maxA * k * k * vf})` : (f.hot ? `rgba(52,200,240,${maxA * k * k * vf})` : `rgba(175,212,230,${maxA * k * k * vf})`);
            g.lineWidth = (dark ? 2.6 : 1.7) * k * f.scale; g.lineCap = 'round';
            g.beginPath(); g.moveTo(prev.x, prev.y); g.lineTo(q.x, q.y); g.stroke();
            prev = q;
          }
        }
        const ah = at(t + 0.012);
        const ang = Math.atan2(ah.y - h.y, ah.x - h.x);
        g.save(); g.translate(h.x, h.y); g.rotate(ang); g.scale(0.78 * f.scale, 0.78 * f.scale);
        g.globalAlpha = 55 / 255 * vf; g.fillStyle = f.hot ? '#34C8F0' : '#EAF6FC';
        g.beginPath(); g.arc(0, 0, 7.5, 0, Math.PI * 2); g.fill();
        g.beginPath(); g.moveTo(PLANE[0][0], PLANE[0][1]);
        for (let i = 1; i < PLANE.length; i++) g.lineTo(PLANE[i][0], PLANE[i][1]);
        for (let i = PLANE.length - 1; i >= 0; i--) g.lineTo(PLANE[i][0], -PLANE[i][1]);
        g.closePath();
        g.globalAlpha = 190 / 255 * vf; g.lineWidth = 2.6; g.lineJoin = 'round'; g.strokeStyle = '#04121F'; g.stroke();
        g.globalAlpha = vf; g.fillStyle = f.hot ? '#CCF3FF' : '#EAF6FC'; g.fill();
        g.restore(); g.globalAlpha = 1;
      }
      // server marker (Frankfurt)
      const m = this.project(SERVER[0], SERVER[1], rot);
      if (m.vis > 0) {
        const pulse = (now % 2400) / 2400;
        g.globalAlpha = Math.min(1, m.vis * 3);
        g.beginPath(); g.arc(m.x, m.y, 8 + pulse * 18, 0, Math.PI * 2);
        g.fillStyle = `rgba(52,200,240,${0.35 * (1 - pulse)})`; g.fill();
        g.beginPath(); g.arc(m.x, m.y + 1, 8, 0, Math.PI * 2); g.fillStyle = 'rgba(0,0,0,.67)'; g.fill();
        g.beginPath(); g.arc(m.x, m.y, 7, 0, Math.PI * 2); g.fillStyle = '#111111'; g.fill();
        g.beginPath(); g.arc(m.x, m.y, 5, 0, Math.PI * 2); g.fillStyle = '#DD0000'; g.fill();
        g.beginPath(); g.arc(m.x, m.y, 3, 0, Math.PI * 2); g.fillStyle = '#FFCE00'; g.fill();
        g.beginPath(); g.arc(m.x, m.y, 1.3, 0, Math.PI * 2); g.fillStyle = '#FFFFFF'; g.fill();
        g.globalAlpha = 1;
      }
    }
  }

  // SpeedGraphView: last 60 seconds of download (cyan) and upload (violet).
  // Smooth version: redrawn every animation frame. Between two samples (1 s apart) the
  // whole curve glides left continuously, the newest value eases in, and the vertical
  // scale follows the peak gently — no more one-step-per-second jumps.
  class SpeedGraph {
    // Constant-velocity "streaming" chart (like Smoothie/Grafana live charts): a virtual clock
    // moves the curve left at a steady speed that does NOT depend on when samples arrive.
    // Each sample k sits at slot k; the clock runs ~1.2 slots behind the newest sample, so a
    // new point is always placed off-screen before it scrolls into view. If samples come a bit
    // early/late, the speed is nudged by at most ±4% — never a visible jump.
    constructor(canvas) {
      this.cv = canvas; this.n = 60; this.samples = []; this.k = 0; this.vt = 0; this.lastFrame = 0;
      this.max = 64 * 1024; this.pw = 0; this.ph = 0; this.interval = 1000;
      const loop = (t) => { this.frame(t); requestAnimationFrame(loop); };
      requestAnimationFrame(loop);
    }
    add(d, u) {
      if (!this.samples.length) this.vt = -1.2;           // first point starts just beyond the edge
      this.samples.push({ k: this.k++, d, u });
      if (this.samples.length > this.n + 4) this.samples.shift();
    }
    clear() { this.samples = []; this.k = 0; this.vt = 0; }
    draw() {}
    frame(now) {
      const dt = this.lastFrame ? Math.min(100, now - this.lastFrame) : 16;
      this.lastFrame = now;
      if (this.samples.length) {
        // window was hidden/minimised (rAF paused) or the PC slept while samples kept coming:
        // the clock fell far behind and every visible point scrolled out of the kept buffer, so
        // the wave vanished for good. Re-anchor the clock right behind the newest sample.
        if ((this.k - 1) - this.vt > 4) { this.vt = this.k - 1 - 1.7; this.rate = 1; }
        const lag = (this.k - 1) - this.vt;                  // slots between clock and newest sample
        const target = 1 + Math.max(-0.04, Math.min(0.04, (lag - 1.7) * 0.03));
        this.rate = (this.rate || 1) + (target - (this.rate || 1)) * Math.min(1, dt / 3000); // speed itself glides
        this.vt += (dt / this.interval) * this.rate;
        if (this.vt > this.k - 1 + 0.2) this.vt = this.k - 1 + 0.2;   // never run past the data
      }
      const cv = this.cv;
      if (!cv.isConnected || cv.offsetParent === null) return;
      const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
      if (!w || !h) return;
      if (w !== this.pw || h !== this.ph) { cv.width = w * dpr; cv.height = h * dpr; this.pw = w; this.ph = h; }
      const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
      g.strokeStyle = 'rgba(255,255,255,.1)'; g.lineWidth = 1;
      for (let i = 1; i < 4; i++) { const y = Math.round(h * i / 4) + .5; g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
      const step = w / (this.n - 1);
      const xOf = (k) => w - (this.vt - k) * step;
      const vis = this.samples.filter(s => xOf(s.k) > -2 * step);
      const dv = vis.map(s => s.d), uv = vis.map(s => s.u);
      const target = Math.max(64 * 1024, ...dv, ...uv) * 1.15;
      this.max += (target - this.max) * Math.min(1, dt / 400);
      const max = this.max, top = 6;
      const ptsOf = (key) => vis.map(s => [xOf(s.k), h - Math.min(1, s[key] / max) * (h - top)]);
      const path = (arr) => (arr === dv ? ptsOf('d') : ptsOf('u'));
      const stroke = (pts, color, width, glow) => {
        if (pts.length < 2) return;
        g.beginPath(); g.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) { const [x0, y0] = pts[i - 1], [x1, y1] = pts[i]; const mx = (x0 + x1) / 2; g.bezierCurveTo(mx, y0, mx, y1, x1, y1); }
        g.strokeStyle = color; g.lineWidth = width; g.lineJoin = 'round';
        if (glow) { g.shadowColor = glow; g.shadowBlur = 10; }
        g.stroke(); g.shadowBlur = 0; return true;
      };
      const dp = path(dv);
      if (dp.length > 1) {
        g.beginPath(); g.moveTo(dp[0][0], h); g.lineTo(dp[0][0], dp[0][1]);
        for (let i = 1; i < dp.length; i++) { const [x0, y0] = dp[i - 1], [x1, y1] = dp[i]; const mx = (x0 + x1) / 2; g.bezierCurveTo(mx, y0, mx, y1, x1, y1); }
        g.lineTo(dp[dp.length - 1][0], h); g.closePath();
        const gr = g.createLinearGradient(0, top, 0, h); gr.addColorStop(0, 'rgba(52,200,240,.4)'); gr.addColorStop(1, 'rgba(52,200,240,0)');
        g.fillStyle = gr; g.fill();
      }
      stroke(dp, '#8EE9FF', 2.2, '#34C8F0');
      stroke(path(uv), '#9E7BFF', 1.8);
    }
  }
  window.HermesGlobe = Globe;
  window.SpeedGraph = SpeedGraph;
})();
