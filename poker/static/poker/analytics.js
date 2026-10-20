(function () {
  var data = JSON.parse(document.getElementById("bankroll-data").textContent);
  var svg = document.getElementById("bankroll-chart");
  var tip = document.getElementById("chart-tip");
  var sel = document.getElementById("tournament");
  var NS = "http://www.w3.org/2000/svg";
  var W = 800, H = 340, L = 64, R = 16, T = 16, B = 34;
  var defaultTip = tip.textContent;

  function el(name, attrs, text) {
    var e = document.createElementNS(NS, name);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    svg.appendChild(e);
    return e;
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
    if (!pts.length) return;
    var xs = pts.map(function (q) { return q.x; }), ys = pts.map(function (q) { return q.y; });
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

    el("polyline", {
      "class": "line",
      points: pts.map(function (q) { return sx(q.x) + "," + sy(q.y); }).join(" "),
    });
    pts.forEach(function (q) {
      var c = el("circle", { cx: sx(q.x), cy: sy(q.y), r: 4, "class": "dot" });
      var label = "Hand #" + q.p.number + " at " + q.p.table + ": " + money(q.p.after);
      c.addEventListener("mouseenter", function () { tip.textContent = label; });
      c.addEventListener("mouseleave", function () { tip.textContent = defaultTip; });
      var title = document.createElementNS(NS, "title");
      title.textContent = label;
      c.appendChild(title);
    });
  }

  // Optional ?t=<index>&x=hand|time preselects the view (bookmarkable).
  var q = new URLSearchParams(location.search);
  if (q.has("t") && data[+q.get("t")]) sel.value = q.get("t");
  if (q.get("x") === "time") document.querySelector("input[name=xmode][value=time]").checked = true;

  sel.addEventListener("change", draw);
  Array.prototype.forEach.call(document.querySelectorAll("input[name=xmode]"), function (r) {
    r.addEventListener("change", draw);
  });
  draw();
})();
