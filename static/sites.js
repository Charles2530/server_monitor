"use strict";

(() => {
  const sites = ["maoshanwang", "sensecore"];
  const url = new URL(location.href);
  const requested = url.searchParams.get("site");
  const id = requested === "ssh" && ["/terminal", "/ide"].includes(location.pathname) ? "ssh" : sites.includes(requested) ? requested : "maoshanwang";
  // Bind every request to this document's site. Switching loads a fresh document.
  const key = (name) => id === "maoshanwang" ? name : `${name}.${id}`;
  let writes = 0;
  const controls = () => document.querySelectorAll("[data-site-switch]");
  async function siteFetch(path, options = {}) {
    const headers = new Headers(options.headers);
    headers.set("X-Monitor-Site", id);
    const writing = options.method === "POST" && /\/(create|stop|rename)$/.test(path);
    if (writing) { writes += 1; controls().forEach((button) => { button.disabled = true; }); }
    try { return await fetch(path, { ...options, headers }); }
    finally {
      if (writing) { writes -= 1; controls().forEach((button) => { button.disabled = writes > 0; }); }
    }
  }
  window.MonitorSite = Object.freeze({ id, key, fetch: siteFetch });
  const label = document.getElementById("current-site");
  if (label) label.textContent = id;
  const target = sites[(sites.indexOf(id) + 1) % sites.length];
  controls().forEach((button) => {
    button.title = `切换到 ${target}`;
    button.setAttribute("aria-label", `切换到 ${target}`);
    button.addEventListener("click", () => {
      if (writes) return;
      const destination = new URL(location.href);
      destination.searchParams.set("site", target);
      destination.searchParams.delete("task_id");
      destination.searchParams.delete("task_view");
      location.assign(destination.href);
    });
  });
  document.querySelectorAll("[data-site-label]").forEach((node) => { node.textContent = id; });
  const policy = document.getElementById("acp-cluster-policy");
  if (policy && id === "sensecore") policy.textContent = "当前 AFS 分区内自动选集群：模板集群优先，按配额与挂载兼容性选择";
})();
