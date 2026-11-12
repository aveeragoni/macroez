// Shared by every page: headline word animation, fade-ins, card tilt, and release info from GitHub.
const REPO = "aveeragoni/macroez";
const LATEST_EXE = `https://github.com/${REPO}/releases/latest/download/MacroEZ.exe`;

document.querySelectorAll(".year").forEach((el) => { el.textContent = new Date().getFullYear(); });

// Headings: wrap each word so the words can rise in one after another
document.querySelectorAll(".split").forEach((heading) => {
  let i = 0;
  for (const node of [...heading.childNodes]) {
    if (node.nodeType === Node.TEXT_NODE) {
      const frag = document.createDocumentFragment();
      node.textContent.split(/(\s+)/).forEach((part) => {
        if (!part) return;
        if (/^\s+$/.test(part)) { frag.append(part); return; }
        const word = document.createElement("span");
        word.className = "word"; word.style.setProperty("--i", i++); word.textContent = part;
        word.setAttribute("aria-hidden", "true");
        frag.append(word);
      });
      node.replaceWith(frag);
    } else if (node.nodeType === Node.ELEMENT_NODE) {
      node.classList.add("word"); node.style.setProperty("--i", i++);
    }
  }
});

// Fade things in as they scroll into view
const io = "IntersectionObserver" in window && new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (entry.isIntersecting) { entry.target.classList.add("in"); io.unobserve(entry.target); }
  }
}, { threshold: 0.15, rootMargin: "0px 0px -40px 0px" });
function startReveals(root = document) {
  root.querySelectorAll(".reveal:not(.in)").forEach((el) => {
    // anything already on screen starts now; the rest waits until it is scrolled to
    if (!io || el.getBoundingClientRect().top < innerHeight * 0.92) el.classList.add("in");
    else io.observe(el);
  });
}
startReveals();

// Cards lean slightly toward the mouse
if (matchMedia("(hover: hover) and (prefers-reduced-motion: no-preference)").matches) {
  document.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${e.clientX - r.left}px`);
      card.style.setProperty("--my", `${e.clientY - r.top}px`);
      const rx = ((e.clientY - r.top) / r.height - 0.5) * -6, ry = ((e.clientX - r.left) / r.width - 0.5) * 6;
      card.style.transform = `perspective(700px) rotateX(${rx}deg) rotateY(${ry}deg) translateY(-4px)`;
    });
    card.addEventListener("pointerleave", () => { card.style.transform = ""; });
  });
}

// ---- Release info, read from GitHub so a new release shows up here by itself
let releases;
function loadReleases() {
  // html+json gives each release's notes already turned into safe HTML by GitHub
  releases ??= fetch(`https://api.github.com/repos/${REPO}/releases?per_page=50`,
    { headers: { Accept: "application/vnd.github.html+json" } })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status))))
    .then((list) => list.filter((r) => !r.draft));
  return releases;
}
const exeOf = (release) => release.assets.find((a) => a.name.toLowerCase() === "macroez.exe");
const megabytes = (bytes) => `${Math.round(bytes / 1048576)} MB`;  // same number Windows shows
const day = (iso) => new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });

// Download page: version, size and date next to the button
const dlMeta = document.getElementById("dl-meta");
if (dlMeta) {
  loadReleases().then((list) => {
    const latest = list.find((r) => !r.prerelease && exeOf(r));
    if (!latest) return;
    dlMeta.textContent = `Version ${latest.tag_name.replace(/^v/i, "")} · ${megabytes(exeOf(latest).size)} · ` +
      `released ${day(latest.published_at)}`;
    document.querySelectorAll("[data-latest-version]").forEach((el) => {
      el.textContent = latest.tag_name.replace(/^v/i, "");
    });
  }).catch(() => {});
}

// Versions page: every release with its notes and its own download
const versionList = document.getElementById("version-list");
if (versionList) {
  loadReleases().then((list) => {
    versionList.replaceChildren();
    if (!list.length) throw new Error("none");
    const latestId = (list.find((r) => !r.prerelease) || {}).id;
    list.forEach((release, n) => {
      const card = document.createElement("article");
      card.className = "release reveal";
      card.style.setProperty("--delay", `${Math.min(n, 4) * 70}ms`);
      const head = document.createElement("header");
      const title = document.createElement("h2");
      title.textContent = `Version ${release.tag_name.replace(/^v/i, "")}`;
      head.append(title);
      if (release.id === latestId) {
        const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = "Latest";
        head.append(badge);
      } else if (release.prerelease) {
        const badge = document.createElement("span"); badge.className = "badge badge-muted"; badge.textContent = "Preview";
        head.append(badge);
      }
      const when = document.createElement("time");
      when.dateTime = release.published_at; when.textContent = day(release.published_at);
      head.append(when);
      const notes = document.createElement("div");
      notes.className = "notes";
      notes.innerHTML = release.body_html || "<p>No notes for this version.</p>";
      notes.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
      card.append(head, notes);
      const exe = exeOf(release);
      if (exe) {
        const get = document.createElement("a");
        get.className = release.id === latestId ? "btn btn-small" : "btn btn-small btn-ghost";
        get.href = release.id === latestId ? "/download/" : exe.browser_download_url;
        get.textContent = release.id === latestId ? "Download" : `Download ${release.tag_name} (${megabytes(exe.size)})`;
        card.append(get);
      }
      versionList.append(card);
    });
    startReveals(versionList);
  }).catch(() => {
    versionList.innerHTML = `<p class="muted">Couldn't load the version list right now.
      <a href="https://github.com/${REPO}/releases">See every version on GitHub</a>.</p>`;
  });
}

// ---- Macro presets, from presets/index.json (made when a shared preset is approved)
const presetList = document.getElementById("preset-list");
if (presetList) {
  const MODE_NAMES = { image: "Image", classic: "Classic", hybrid: "Hybrid", reaction: "Reaction", autoclick: "Autoclicker" };
  const search = document.getElementById("preset-search");
  const chips = document.querySelectorAll("#preset-modes .chip");
  let presets = [], mode = "";
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const safeId = (id) => /^[a-z0-9][a-z0-9-]{1,79}$/.test(id || "");
  function render() {
    const words = (search.value || "").toLowerCase().split(/\s+/).filter(Boolean);
    const shown = presets.filter((p) => (!mode || p.mode === mode) &&
      words.every((w) => `${p.name} ${p.description} ${p.author}`.toLowerCase().includes(w)));
    presetList.replaceChildren();
    if (!shown.length) {
      presetList.append(el("p", "muted", presets.length ? "No presets match that." : "No presets yet. Be the first to share one!"));
      return;
    }
    shown.forEach((p, n) => {
      const card = el("article", "card preset reveal");
      card.id = p.id;
      card.style.setProperty("--delay", `${Math.min(n, 6) * 50}ms`);
      const top = el("div", "preset-top");
      top.append(el("span", "tag", MODE_NAMES[p.mode] || "Macro"));
      if (p.author) top.append(el("span", "muted", `by ${p.author}`));
      card.append(top, el("h3", "", p.name || p.id), el("p", "", p.description || "No description."));
      const facts = [];
      if (p.items) facts.push(`${p.items} ${p.mode === "classic" || p.mode === "hybrid" ? "steps" : "items"}`);
      if (p.images) facts.push(`${p.images} image${p.images === 1 ? "" : "s"}`);
      if (facts.length) card.append(el("p", "preset-facts muted", facts.join(" · ")));
      (Array.isArray(p.warnings) ? p.warnings : []).slice(0, 4).forEach((w) => card.append(el("p", "preset-warn", `⚠ ${w}`)));
      const actions = el("div", "preset-actions");
      const open = el("a", "btn btn-small", "Open in MacroEZ");
      open.href = `macroez://preset/${p.id}`;
      const file = el("a", "btn btn-small btn-ghost", "Download");
      file.href = `/presets/${p.id}/preset.zip`;
      file.setAttribute("download", `${(p.name || p.id).replace(/[^\w -]+/g, "")}.macroez.zip`);
      actions.append(open, file);
      card.append(actions);
      presetList.append(card);
    });
    startReveals(presetList);
    if (location.hash && safeId(location.hash.slice(1))) {
      const target = document.getElementById(location.hash.slice(1));
      if (target) { target.classList.add("highlight"); target.scrollIntoView({ block: "center" }); }
    }
  }
  fetch("/presets/index.json", { cache: "no-cache" }).then((r) => r.json()).then((data) => {
    presets = (data.presets || []).filter((p) => p && safeId(p.id) && MODE_NAMES[p.mode]);
    render();
  }).catch(() => {
    presetList.replaceChildren(el("p", "muted", "Couldn't load presets right now. Try again in a minute."));
  });
  search.addEventListener("input", render);
  chips.forEach((chip) => chip.addEventListener("click", () => {
    mode = chip.dataset.mode;
    chips.forEach((c) => c.classList.toggle("on", c === chip));
    render();
  }));
}
