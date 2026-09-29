"use strict";

(() => {
  const pages = {
    overview: ["资源总览", "OVERVIEW", "查看任务配置、标准集群余量、总卡数与节点健康。"],
    tasks: ["运行任务", "RUNNING TASKS", "选择任务查看 CPU、GPU、内存与显存；支持停止 ACP 任务。"],
    acp: ["ACP 任务", "CREATE ACP TASK", window.MonitorSite?.id === "sensecore" ? "先选 AFS 分区，再选集群和卡数，预览镜像与挂载后创建。" : "配置任务并预览创建；可指定集群，默认按优先规则选择。"],
    waiting: ["等待队列", "WAITING QUEUE", "查看当前工作空间所有用户等待与启动中的 CCI、ACP 任务。"],
  };
  const aliases = { main: "overview", "tasks-section": "tasks", "acp-section": "acp", "waiting-section": "waiting", clusters: "overview", "clusters-section": "overview" };
  function renderPage() {
    const fragment = location.hash.slice(1);
    const route = Object.hasOwn(pages, fragment) ? fragment : Object.hasOwn(aliases, fragment) ? aliases[fragment] : "overview";
    if (fragment !== route) history.replaceState(null, "", `#${route}`);
    for (const panel of document.querySelectorAll(".workspace-view")) panel.hidden = panel.dataset.page !== route;
    for (const link of document.querySelectorAll(".nav-item")) {
      const active = link.getAttribute("href") === `#${route}`;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    }
    const [title, eyebrow, description] = pages[route];
    document.getElementById("page-title").textContent = title;
    document.getElementById("page-eyebrow").textContent = `WORKSPACE / ${eyebrow}`;
    document.getElementById("page-description").textContent = description;
    document.title = `${title} · Charles Cloud Monitor V2`;
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    document.dispatchEvent(new CustomEvent("workspace-page", { detail: route }));
  }
  document.querySelector(".skip-link").addEventListener("click", (event) => {
    event.preventDefault(); document.getElementById("main").focus();
  });
  window.addEventListener("hashchange", renderPage);
  renderPage();
})();
