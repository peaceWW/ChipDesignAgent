const agentState = {
  mode: "ask",
  history: [],
};

function initMermaid() {
  if (!window.mermaid) return;
  window.mermaid.initialize({
    startOnLoad: false,
    theme: "neutral",
    securityLevel: "loose",
  });
  window.mermaid.run({ querySelector: ".mermaid" });
}

function initAgent() {
  const drawer = document.getElementById("agent-drawer");
  const mask = document.getElementById("drawer-mask");
  const openBtn = document.getElementById("open-agent");
  const closeBtn = document.getElementById("close-agent");
  const form = document.getElementById("agent-form");
  const input = document.getElementById("agent-input");
  const log = document.getElementById("agent-log");
  if (!drawer || !form) return;

  const toggle = (show) => {
    drawer.hidden = !show;
    mask.hidden = !show;
  };
  openBtn?.addEventListener("click", () => toggle(true));
  closeBtn?.addEventListener("click", () => toggle(false));
  mask?.addEventListener("click", () => toggle(false));

  document.querySelectorAll("#agent-modes button").forEach((btn) => {
    btn.addEventListener("click", () => {
      agentState.mode = btn.dataset.mode;
      document.querySelectorAll("#agent-modes button").forEach((b) => b.classList.toggle("on", b === btn));
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    appendBubble(log, "user", message);
    input.value = "";
    appendBubble(log, "bot", "正在检索教材…");
    try {
      const res = await fetch("/api/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          mode: agentState.mode,
          history: agentState.history,
        }),
      });
      const data = await res.json();
      log.lastChild?.remove();
      const cites = (data.citations || [])
        .map((c) => `<div class="cite">出处：${c.part_title} / <a href="/chapter/${c.chapter_id}">${c.title}</a></div>`)
        .join("");
      appendBubble(log, "bot", escapeHtml(data.answer || "没有生成回答") + cites, true);
      agentState.history.push({ role: "user", content: message });
      agentState.history.push({ role: "assistant", content: data.answer || "" });
    } catch (err) {
      log.lastChild?.remove();
      appendBubble(log, "bot", "请求失败，请确认本地服务仍在运行。");
    }
  });
}

function appendBubble(log, who, html, asHtml = false) {
  const div = document.createElement("div");
  div.className = `bubble ${who}`;
  if (asHtml) div.innerHTML = html.replaceAll("\n", "<br>");
  else div.textContent = html;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function initQuiz() {
  const root = document.getElementById("quiz");
  const button = document.getElementById("submit-quiz");
  if (!root || !button) return;
  button.addEventListener("click", async () => {
    const answers = {};
    root.querySelectorAll("fieldset.q").forEach((box) => {
      const picked = box.querySelector("input:checked");
      if (picked) answers[box.dataset.id] = Number(picked.value);
    });
    const res = await fetch("/api/quiz/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter_id: root.dataset.chapter, answers }),
    });
    const data = await res.json();
    const byId = Object.fromEntries((data.detail || []).map((d) => [d.id, d]));
    root.querySelectorAll("fieldset.q").forEach((box) => {
      const result = byId[box.dataset.id];
      box.classList.remove("ok", "bad");
      const feedback = box.querySelector(".feedback");
      if (!result) return;
      box.classList.add(result.correct ? "ok" : "bad");
      feedback.hidden = false;
      feedback.textContent = (result.correct ? "回答正确。" : "回答有误。") + (result.explanation || "");
    });
    const summary = document.getElementById("quiz-summary");
    summary.hidden = false;
    summary.textContent = `本次得分 ${data.score} / ${data.total}`;
  });
}

function persistLocalProgress() {
  fetch("/api/progress")
    .then((res) => res.json())
    .then((data) => {
      localStorage.setItem("chip-design-progress", JSON.stringify(data));
    })
    .catch(() => {});
}

document.addEventListener("DOMContentLoaded", () => {
  initMermaid();
  initAgent();
  initQuiz();
  persistLocalProgress();
});
