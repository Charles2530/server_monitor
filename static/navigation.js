"use strict";

(() => {
  const pages = {
    overview: ["资源总览", "OVERVIEW", "查看任务配置、标准集群余量、总卡数与节点健康。"],
    tokenhub: ["TokenHub", "AI API RESOURCES", "余额、今日消费与花费趋势，API 密钥和账号额度集中查看。"],
    tasks: ["运行任务", "RUNNING TASKS", "选择任务查看 CPU、GPU、内存与显存；支持停止 ACP 任务。"],
    acp: ["ACP 任务", "CREATE ACP TASK", window.MonitorSite?.id === "sensecore" ? "先选 AFS 分区，再选集群和卡数，预览镜像与挂载后创建。" : "配置任务并预览创建；可指定集群，默认按优先规则选择。"],
    history: ["任务历史", "TASK HISTORY", "查看 charles 已成功、失败或停止的任务，并跳转云控制台。"],
    storage: ["存储管理", "AFS STORAGE", "查看 AFS 存储容量、分区与 charles 任务的挂载路径。"],
    images: ["镜像管理", "IMAGE MANAGEMENT", "按当前云站点查看镜像仓库、版本信息与镜像传递方向。"],
    waiting: ["队列管理", "QUEUES", "查看所有用户的等待任务，以及运行中 ACP 任务的 GPU 利用率排名。"],
  };
  const aliases = { main: "overview", "tasks-section": "tasks", "acp-section": "acp", "waiting-section": "waiting", clusters: "overview", "clusters-section": "overview" };
  const mobile = window.MonitorTaskContext.mobile, nav = document.querySelector(".sidebar nav");
  const more = document.createElement("details"), summary = document.createElement("summary"), menu = document.createElement("div");
  more.id = "mobile-nav-more"; summary.textContent = "••• 更多"; summary.setAttribute("aria-label", "更多页面");
  const primary = ["overview", "tasks", "tokenhub", "waiting"];
  for (const link of [...nav.querySelectorAll(".nav-item")]) if (!primary.includes(link.hash.slice(1))) menu.append(link);
  for (const route of primary) nav.append(nav.querySelector(`[href="#${route}"]`));
  more.append(summary, menu); nav.append(more);
  const arrange = () => { more.open = !mobile.matches; };
  mobile.addEventListener("change", arrange); arrange();
  const closeMore = (focus = false) => { if (mobile.matches && more.open) { more.open = false; if (focus) summary.focus(); } };
  document.addEventListener("pointerdown", event => { if (!more.contains(event.target)) closeMore(); });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && mobile.matches && more.open) { event.preventDefault(); closeMore(true); } });
  menu.addEventListener("click", event => { if (event.target.closest("a")) closeMore(true); });
  function renderPage() {
    const fragment = location.hash.slice(1) || (window.MonitorTaskContext?.mobile.matches ? "tasks" : "overview");
    const route = Object.hasOwn(pages, fragment) ? fragment : Object.hasOwn(aliases, fragment) ? aliases[fragment] : "overview";
    if (location.hash.slice(1) !== route) history.replaceState(null, "", `#${route}`);
    for (const panel of document.querySelectorAll(".workspace-view")) panel.hidden = panel.dataset.page !== route;
    for (const link of document.querySelectorAll(".nav-item")) {
      const active = link.getAttribute("href") === `#${route}`;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    }
    summary.classList.toggle("active", !primary.includes(route));
    closeMore(more.contains(document.activeElement));
    const [title, eyebrow, description] = pages[route];
    const refreshLabel = route === "acp" ? "重新读取 ACP 创建配置" : route === "waiting" ? "刷新等待任务与运行利用率" : `刷新${title}`;
    document.getElementById("refresh-button").title = refreshLabel;
    document.getElementById("refresh-button").setAttribute("aria-label", refreshLabel);
    document.getElementById("page-title").textContent = title;
    document.getElementById("page-eyebrow").textContent = `WORKSPACE / ${eyebrow}`;
    document.getElementById("page-description").textContent = description;
    document.getElementById("status-banner").hidden = route === "tokenhub";
    document.getElementById("connect-button").hidden = route === "tokenhub";
    document.querySelector(".heading-tag[data-site-label]").textContent = route === "tokenhub" ? "AI API" : window.MonitorSite.id;
    document.title = "iLab Server Monitor";
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    document.dispatchEvent(new CustomEvent("workspace-page", { detail: route }));
  }
  document.querySelector(".skip-link").addEventListener("click", (event) => {
    event.preventDefault(); document.getElementById("main").focus();
  });
  document.querySelector('[href="#clusters-section"].summary-card')?.addEventListener("click", event => {
    event.preventDefault(); document.getElementById("clusters-section").scrollIntoView({behavior: "instant", block: "start"});
    document.querySelector("#clusters-section .table-scroll").focus({preventScroll: true});
  });
  for (const button of document.querySelectorAll("[data-queue-target]")) button.addEventListener("click", () => {
    const section = document.getElementById(button.dataset.queueTarget);
    section.scrollIntoView({behavior: "instant", block: "start"});
    section.querySelector(".table-scroll").focus({preventScroll: true});
  });
  window.addEventListener("hashchange", renderPage);
  renderPage();
})();
