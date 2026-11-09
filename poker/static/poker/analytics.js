(function () {
  var data = JSON.parse(document.getElementById("bankroll-data").textContent);
  var svg = document.getElementById("bankroll-chart");
  var tip = document.getElementById("chart-tip");
  var sel = document.getElementById("tournament");
  var showField = document.getElementById("show-field");
  var NS = "http://www.w3.org/2000/svg";
  var W = 800, H = 340, L = 64, R = 16, T = 16, B = 34;
  var defaultTip = tip.textContent;

  function el(name, attrs, text, parent) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    (parent || svg).appendChild(e);
    return e;
  }
  function titled(e, label, tipEl, fallback) {
    e.addEventListener("mouseenter", function () { tipEl.textContent = label; });
    e.addEventListener("mouseleave", function () { tipEl.textContent = fallback; });
    var title = document.createElementNS(NS, "title");
    title.textContent = label;
    e.appendChild(title);
  }
  function money(n) { return "$" + Math.round(n).toLocaleString("en-US"); }
  function ticks(lo, hi, n) {
    var step = (hi - lo) / n, out = [];
    for (var i = 0; i <= n; i++) out.push(lo + step * i);
    return out;
  }

  function draw() {
    var t = data[+sel.value];
    var mode = document.querySelector("input[name=xmode]:checked").value;
    svg.innerHTML = "";
    // Start the line at the opening stack, then one point per hand.
    var pts = t.points.map(function (p, i) {
      var ms = p.time ? Date.parse(p.time) : null;
      return { p: p, x: mode === "time" ? ms : i + 1, y: p.after };
    });
    if (mode === "time") pts = pts.filter(function (q) { return q.x != null; });
    if (!pts.length) { drawRanking(t); return; }
    var field = showField.checked;
    var fpts = field ? pts.filter(function (q) { return q.p.field; }) : [];
    var xs = pts.map(function (q) { return q.x; }), ys = pts.map(function (q) { return q.y; });
    fpts.forEach(function (q) { ys.push(q.p.field.p25, q.p.field.p75, q.p.field.mean); });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs);
    if (x0 === x1) { x0 -= 1; x1 += 1; }
    var y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    var pad = Math.max((y1 - y0) * 0.1, 500);
    y0 -= pad; y1 += pad;
    function sx(v) { return L + (v - x0) / (x1 - x0) * (W - L - R); }
    function sy(v) { return H - B - (v - y0) / (y1 - y0) * (H - T - B); }

    ticks(y0, y1, 4).forEach(function (v) {
      el("line", { x1: L, x2: W - R, y1: sy(v), y2: sy(v), "class": "grid" });
      el("text", { x: L - 6, y: sy(v) + 4, "text-anchor": "end" }, money(v));
    });
    if (mode === "hand") {
      var every = Math.ceil(pts.length / 10);
      pts.forEach(function (q, i) {
        if (i % every === 0) el("text", { x: sx(q.x), y: H - B + 16, "text-anchor": "middle" }, "#" + (i + 1));
      });
    } else {
      ticks(x0, x1, 4).forEach(function (v) {
        el("text", { x: sx(v), y: H - B + 16, "text-anchor": "middle" },
          new Date(v).toLocaleDateString("en-US", { month: "short", day: "numeric" }));
      });
    }

    // Table-change events.
    pts.forEach(function (q) {
      if (!q.p.table_change) return;
      var x = sx(q.x);
      el("line", { x1: x, x2: x, y1: T, y2: H - B, "class": "event" });
      el("text", { x: x + 4, y: T + 10, "class": "event" }, "→ " + q.p.table);
    });
    // First table label.
    el("text", { x: L + 4, y: T + 10, "class": "event" }, t.points[0].table);

    // Table stacks: 25th-75th percentile band, median, and average.
    if (fpts.length) {
      var top = fpts.map(function (q) { return sx(q.x) + "," + sy(q.p.field.p75); });
      var bot = fpts.slice().reverse().map(function (q) { return sx(q.x) + "," + sy(q.p.field.p25); });
      el("polygon", { "class": "band", points: top.concat(bot).join(" ") });
      ["p50", "mean"].forEach(function (k) {
        el("polyline", {
          "class": k === "mean" ? "mean" : "median",
          points: fpts.map(function (q) { return sx(q.x) + "," + sy(q.p.field[k]); }).join(" "),
        });
      });
    }

    el("polyline", {
      "class": "line",
      points: pts.map(function (q) { return sx(q.x) + "," + sy(q.y); }).join(" "),
    });
    pts.forEach(function (q) {
      var c = el("circle", { cx: sx(q.x), cy: sy(q.y), r: 4, "class": "dot" });
      var label = "Hand #" + q.p.number + " at " + q.p.table + ": " + money(q.p.after);
      var f = q.p.field;
      if (f) label += " · table of " + f.n + ": avg " + money(f.mean) + ", median " + money(f.p50) +
        ", 25–75%ile " + money(f.p25) + "–" + money(f.p75);
      titled(c, label, tip, defaultTip);
    });
    drawRanking(t);
  }

  var rsvg = document.getElementById("ranking-chart");
  var rtip = document.getElementById("ranking-tip");
  function drawRanking(t) {
    var rows = t.ranking || [];
    rsvg.innerHTML = "";
    rtip.textContent = "";
    if (!rows.length) return;
    var RL = 150, RR = 90, RT = 8, ROW = 22, RW = 800;
    var RH = RT + rows.length * ROW + 22;
    rsvg.setAttribute("viewBox", "0 0 " + RW + " " + RH);
    var max = Math.max.apply(null, rows.map(function (r) { return r.stack; })) || 1;
    function bx(v) { return RL + v / max * (RW - RL - RR); }
    var live = rows.filter(function (r) { return !r.out; });
    var avg = live.reduce(function (a, r) { return a + r.stack; }, 0) / (live.length || 1);
    var me = rows.filter(function (r) { return r.me; })[0];
    var fallback = me ? "You are #" + me.rank + " of " + rows.length + " (" + live.length + " still in) with " +
      money(me.stack) + "." : live.length + " of " + rows.length + " players still in.";
    rtip.textContent = fallback;
    var ax = bx(avg), yb = RT + rows.length * ROW;
    el("line", { x1: ax, x2: ax, y1: RT, y2: yb, "class": "avg" }, null, rsvg);
    el("text", { x: ax, y: yb + 14, "text-anchor": "middle", "class": "avg" }, "avg " + money(avg), rsvg);
    rows.forEach(function (r, i) {
      var y = RT + i * ROW;
      var g = el("g", { "class": "row" + (r.out ? " out" : "") }, null, rsvg);
      el("text", { x: RL - 8, y: y + 15, "text-anchor": "end", "class": r.me ? "me" : "" },
        r.rank + ". " + r.player, g);
      el("rect", { x: RL, y: y + 3, width: Math.max(bx(r.stack) - RL, 1), height: ROW - 6, rx: 3,
        "class": "bar" + (r.me ? " me" : "") }, null, g);
      el("text", { x: bx(r.stack) + 6, y: y + 15, "class": "val" + (r.me ? " me" : "") },
        r.out ? "out" : money(r.stack), g);
      var when = r.time ? new Date(r.time).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "";
      titled(g, "#" + r.rank + " " + r.player + ": " + money(r.stack) + " as of " + r.table + " hand #" +
        r.number + (when ? " (" + when + ")" : ""), rtip, fallback);
    });
  }

  // Optional ?t=<index>&x=hand|time preselects the view (bookmarkable).
  var q = new URLSearchParams(location.search);
  if (q.has("t") && data[+q.get("t")]) sel.value = q.get("t");
  if (q.get("x") === "time") document.querySelector("input[name=xmode][value=time]").checked = true;

  sel.addEventListener("change", draw);
  showField.addEventListener("change", draw);
  Array.prototype.forEach.call(document.querySelectorAll("input[name=xmode]"), function (r) {
    r.addEventListener("change", draw);
  });
  draw();
})();
