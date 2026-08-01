(function () {
  const stages = [
    { id: "library", label: "Library", href: "/library", hint: "核对 PDF、MinerU Markdown、标题、作者、摘要、8 个结构化标签和路径。" },
    { id: "discovery", label: "Discovery", href: "/discovery", hint: "删除不相关关键词和论文，确认 20-30 篇候选文献。" },
    { id: "matrix", label: "Matrix", href: "/matrix", hint: "检查每篇文献的固定字段、1000 词主内容和最相关图。" },
    { id: "blueprint", label: "Blueprint", href: "/blueprint", hint: "确认章节、论点、分配论文、图表需求和写作约束。" },
    { id: "sections", label: "Sections", href: "/sections", hint: "检查分章节草稿是否按一段一文献展开，并绑定图候选。" },
    { id: "figures", label: "Figures", href: "/figures", hint: "核对源图是否定位成功，重绘图是否只改风格不改化学内容。" },
    { id: "draft", label: "Draft", href: "/draft", hint: "检查合并初稿的连贯性、图片插入、术语统一和剩余问题。" },
    { id: "final", label: "Final", href: "/final", hint: "最终核对内容、格式、引用、图片和 release report。" },
  ];

  function currentId() {
    const path = location.pathname.replace(/^\/+/, "") || "library";
    return stages.some((s) => s.id === path) ? path : "library";
  }

  function init() {
    const id = currentId();
    document.body.classList.add(`page-${id}`);
    const nav = document.querySelector(".nav");
    if (!nav || document.querySelector(".stage-strip")) return;
    const current = stages.find((s) => s.id === id) || stages[0];
    const strip = document.createElement("div");
    strip.className = "stage-strip";
    strip.innerHTML = `
      <div class="stage-current">
        <div class="stage-kicker">Human Check Stage</div>
        <div class="stage-name">${current.label}</div>
        <div class="stage-hint">${current.hint}</div>
      </div>
      <div class="stage-steps">
        ${stages.map((s, i) => `<a class="stage-step ${s.id === id ? "active" : ""}" data-index="${i + 1}" href="${s.href}">${s.label}</a>`).join("")}
      </div>
    `;
    nav.insertAdjacentElement("afterend", strip);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
