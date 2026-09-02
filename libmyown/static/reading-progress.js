(function () {
  "use strict";

  const STORAGE_KEY = "archive-reading";
  const MIN_PROGRESS = 0.02;
  const DONE_PROGRESS = 0.98;
  const SAVE_MS = 250;

  function readStore() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function writeStore(store) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
  }

  function getEntry(slug) {
    return readStore()[slug] || null;
  }

  function setEntry(slug, entry) {
    const store = readStore();
    store[slug] = entry;
    writeStore(store);
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function ringSvg(progress, size) {
    const stroke = 2;
    const radius = (size - stroke) / 2;
    const center = size / 2;
    const length = 2 * Math.PI * radius;
    const offset = length * (1 - clamp(progress, 0, 1));
    return (
      '<svg class="read-ring" width="' +
      size +
      '" height="' +
      size +
      '" viewBox="0 0 ' +
      size +
      " " +
      size +
      '" aria-hidden="true">' +
      '<circle class="read-ring-track" cx="' +
      center +
      '" cy="' +
      center +
      '" r="' +
      radius +
      '" />' +
      '<circle class="read-ring-fill" cx="' +
      center +
      '" cy="' +
      center +
      '" r="' +
      radius +
      '" stroke-dasharray="' +
      length +
      '" stroke-dashoffset="' +
      offset +
      '" />' +
      "</svg>"
    );
  }

  function progressLabel(progress) {
    return Math.round(clamp(progress, 0, 1) * 100) + "% read";
  }

  function measureProgress(body) {
    const rect = body.getBoundingClientRect();
    const viewport = window.innerHeight;
    const total = Math.max(body.offsetHeight - viewport * 0.35, 1);
    const seen = window.scrollY + viewport * 0.35 - (window.scrollY + rect.top);
    return clamp(seen / total, 0, 1);
  }

  function scrollTargetForProgress(body, progress) {
    const rect = body.getBoundingClientRect();
    const bodyTop = window.scrollY + rect.top;
    const total = Math.max(body.offsetHeight - window.innerHeight * 0.35, 1);
    return bodyTop + clamp(progress, 0, 1) * total;
  }

  function scrollToTop() {
    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth";
    window.scrollTo({ top: 0, behavior });
  }

  function scrollToProgress(body, progress) {
    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth";
    window.scrollTo({ top: scrollTargetForProgress(body, progress), behavior });
  }

  function shouldShowProgress(progress) {
    return progress >= MIN_PROGRESS && progress < DONE_PROGRESS;
  }

  function canResume(saved, current) {
    if (!saved || !shouldShowProgress(saved.progress)) {
      return false;
    }
    return saved.progress > current + 0.03;
  }

  function updateRing(el, progress) {
    el.innerHTML = ringSvg(progress, Number(el.dataset.size || 18));
  }

  function decorateTitleResume(article, slug, body) {
    const title = article.querySelector(".work-title");
    if (!title || title.querySelector(".read-resume-wrap")) {
      return null;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "read-resume";
    button.hidden = true;
    button.innerHTML = '<span class="read-resume-ring" data-size="18"></span>';

    const wrap = document.createElement("span");
    wrap.className = "read-resume-wrap";
    wrap.appendChild(button);
    const showHint = !title.querySelector(".badge.flag");
    if (showHint) {
      const hint = document.createElement("span");
      hint.className = "read-resume-hint";
      hint.setAttribute("aria-hidden", "true");
      hint.innerHTML =
        '<span class="read-resume-hint-arrows" aria-hidden="true">←</span>' +
        '<span class="read-resume-hint-text">Resume</span>';
      wrap.appendChild(hint);
    }

    const titleText = title.querySelector(".work-title-text");
    const anchor = titleText || title;
    anchor.insertAdjacentElement("afterend", wrap);

    const ring = button.querySelector(".read-resume-ring");
    button.addEventListener("click", function () {
      const entry = getEntry(slug);
      if (!entry) {
        return;
      }
      scrollToProgress(body, entry.progress);
    });

    return { button, ring, wrap };
  }

  function playResumeHint(wrap) {
    if (!wrap || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return;
    }
    const hint = wrap.querySelector(".read-resume-hint");
    if (!hint || hint.classList.contains("is-playing")) {
      return;
    }
    hint.classList.add("is-playing");
    hint.addEventListener("animationend", function (event) {
      if (event.animationName !== "read-resume-hint-fade") {
        return;
      }
      hint.remove();
    });
  }

  function decorateIndexLinks() {
    document.querySelectorAll("[data-read-slug]").forEach(function (link) {
      if (link.closest(".work")) {
        return;
      }
      const slug = link.dataset.readSlug;
      const entry = getEntry(slug);
      if (!entry || !shouldShowProgress(entry.progress)) {
        return;
      }

      const wrap = document.createElement("a");
      wrap.className = "read-index-mark read-index-mark--active";
      wrap.href = link.getAttribute("href") + "#resume";
      wrap.title = "Resume (" + progressLabel(entry.progress) + ")";
      wrap.setAttribute("aria-label", "Resume " + link.textContent.trim());
      wrap.innerHTML = ringSvg(entry.progress, 14);
      link.insertAdjacentElement("afterend", wrap);
    });
  }

  function initWorkPage() {
    const article = document.querySelector("article.work[data-read-slug]");
    if (!article) {
      return;
    }

    const slug = article.dataset.readSlug;
    const body = article.querySelector(".work-body");
    const aside = article.querySelector(".read-aside");
    const pin = article.querySelector(".read-pin");
    const pinRing = article.querySelector(".read-pin-ring");
    if (!slug || !body || !aside || !pin || !pinRing) {
      return;
    }

    pinRing.dataset.size = "34";
    aside.hidden = false;

    const resume = decorateTitleResume(article, slug, body);
    let saveTimer = null;

    function paint(scrollProgress) {
      const saved = getEntry(slug);
      const savedProgress = saved ? saved.progress : 0;
      const resumable = canResume(saved, scrollProgress);

      updateRing(pinRing, scrollProgress);
      pin.classList.toggle("read-pin--saved", resumable);
      pin.setAttribute(
        "aria-label",
        resumable ? "Back to top (saved place at " + progressLabel(savedProgress) + ")" : "Back to top"
      );

      if (resume) {
        if (resumable) {
          resume.button.hidden = false;
          resume.button.classList.add("read-resume--active");
          resume.ring.innerHTML = ringSvg(savedProgress, 18);
          resume.button.title = "Resume (" + progressLabel(savedProgress) + ")";
          resume.button.setAttribute(
            "aria-label",
            "Resume reading at " + progressLabel(savedProgress)
          );
        } else if (shouldShowProgress(scrollProgress)) {
          resume.button.hidden = false;
          resume.button.classList.remove("read-resume--active");
          resume.ring.innerHTML = ringSvg(scrollProgress, 18);
          resume.button.title = "Resume (" + progressLabel(scrollProgress) + ")";
          resume.button.setAttribute(
            "aria-label",
            "Resume reading at " + progressLabel(scrollProgress)
          );
        } else {
          resume.button.hidden = true;
          resume.button.classList.remove("read-resume--active");
        }
      }
    }

    function persist(progress) {
      if (progress < MIN_PROGRESS) {
        return;
      }
      setEntry(slug, {
        progress: progress,
        updated: Date.now(),
      });
    }

    function onScroll() {
      const progress = measureProgress(body);
      paint(progress);
      if (saveTimer) {
        clearTimeout(saveTimer);
      }
      saveTimer = window.setTimeout(function () {
        persist(progress);
      }, SAVE_MS);
    }

    pin.addEventListener("click", scrollToTop);

    const saved = getEntry(slug);
    const initialProgress = measureProgress(body);
    paint(initialProgress);

    if (
      resume &&
      canResume(saved, initialProgress) &&
      location.hash !== "#resume"
    ) {
      window.requestAnimationFrame(function () {
        playResumeHint(resume.wrap);
      });
    }

    if (location.hash === "#resume" && saved) {
      window.requestAnimationFrame(function () {
        scrollToProgress(body, saved.progress);
        history.replaceState(null, "", location.pathname + location.search);
      });
    }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    onScroll();
  }

  function init() {
    decorateIndexLinks();
    initWorkPage();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
