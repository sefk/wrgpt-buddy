// Poll each in-progress analysis and redraw its steps; reload once it finishes.
(function () {
  const boxes = document.querySelectorAll("[data-progress-url]");
  if (!boxes.length) return;

  function fmt(s) {
    s = Math.max(0, Math.round(s));
    return s < 60 ? s + "s" : Math.floor(s / 60) + "m " + (s % 60) + "s";
  }

  function detail(st) {
    if (st.status === "done") return (st.detail || "done") + (st.seconds != null ? " · " + st.seconds + "s" : "");
    if (st.status === "error") return "failed" + (st.seconds != null ? " · " + st.seconds + "s" : "");
    if (st.status === "skipped") return "skipped";
    if (st.status === "running" && st.started) return "running… " + fmt((Date.now() - Date.parse(st.started)) / 1000);
    return "";
  }

  function draw(box, data) {
    const head = box.querySelector(".p-head");
    if (head) head.textContent = data.status === "pending"
      ? "Queued" + (data.ahead ? " · " + data.ahead + " ahead" : "")
      : "Analyzing…";
    const ol = box.querySelector(".steps");
    if (!ol || !data.steps.length) return;
    ol.innerHTML = "";
    for (const st of data.steps) {
      const li = document.createElement("li");
      li.className = "st-" + st.status;
      li.dataset.started = st.started || "";
      li.innerHTML = '<span class="st-icon" aria-hidden="true"></span><span class="st-label"></span><span class="st-detail"></span>';
      li.querySelector(".st-label").textContent = st.label;
      li.querySelector(".st-detail").textContent = detail(st);
      li._st = st;
      ol.appendChild(li);
    }
  }

  async function poll(box) {
    try {
      const r = await fetch(box.dataset.progressUrl, { headers: { Accept: "application/json" } });
      if (!r.ok) return;
      const data = await r.json();
      if (data.status === "done" || data.status === "error") { location.reload(); return; }
      draw(box, data);
    } catch (e) { /* offline or server restarting; try again next tick */ }
  }

  // Running timers tick every second; the server is polled every 3 seconds.
  setInterval(() => {
    document.querySelectorAll(".steps li.st-running").forEach((li) => {
      const d = li.querySelector(".st-detail");
      if (li.dataset.started && d) d.textContent = "running… " + fmt((Date.now() - Date.parse(li.dataset.started)) / 1000);
    });
  }, 1000);
  boxes.forEach((box) => { poll(box); setInterval(() => poll(box), 3000); });
})();
