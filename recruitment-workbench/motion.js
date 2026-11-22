/**
 * 动效层（ReactBits 思路，无 React 依赖）
 * - Spotlight Card：指标卡跟随指针的柔光
 * - Count Up：指标数字滚动
 * - Staggered Reveal：页面区块错峰入场
 * 尊重 prefers-reduced-motion：命中时全部跳过。
 */
(() => {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const content = document.getElementById("content");
  if (!content) return;

  const SKIP = new Set(["SCRIPT", "STYLE", "TEMPLATE"]);

  function countUp(root) {
    root.querySelectorAll(".stat strong").forEach((el) => {
      const raw = el.textContent.trim();
      if (!/^\d{1,6}$/.test(raw)) return;
      const target = Number(raw);
      if (target < 2) return;
      const width = el.getBoundingClientRect().width;
      if (width) el.style.minWidth = `${width}px`;
      const started = performance.now();
      const duration = 560;
      const tick = (now) => {
        const progress = Math.min(1, (now - started) / duration);
        const eased = 1 - Math.pow(1 - progress, 3);
        el.textContent = String(Math.round(target * eased));
        if (progress < 1) requestAnimationFrame(tick);
        else el.textContent = raw;
      };
      el.textContent = "0";
      requestAnimationFrame(tick);
    });
  }

  function reveal(root) {
    [...root.children]
      .filter((el) => !SKIP.has(el.tagName))
      .forEach((el, index) => {
        el.classList.remove("enter");
        void el.offsetWidth;
        el.style.animationDelay = `${Math.min(index * 45, 225)}ms`;
        el.classList.add("enter");
      });
  }

  function enhance() {
    if (reduce.matches) return;
    reveal(content);
    countUp(content);
  }

  new MutationObserver(enhance).observe(content, { childList: true });
  document.addEventListener("DOMContentLoaded", enhance, { once: true });

  // Spotlight Card：一次委托，避免给每张卡绑定监听
  document.addEventListener(
    "pointermove",
    (event) => {
      const card = event.target instanceof Element ? event.target.closest(".stat") : null;
      if (!card) return;
      const box = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${((event.clientX - box.left) / box.width) * 100}%`);
      card.style.setProperty("--my", `${((event.clientY - box.top) / box.height) * 100}%`);
    },
    { passive: true }
  );
})();
