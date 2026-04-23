/* Recebimento SAP — utilitários JS compartilhados
   - Toasts com stack (rec.toast, rec.success, rec.warning, rec.error)
   - Open/close de modais (.rec-modal-overlay)
   - Helpers: fmtDate, escapeHtml, debounce, fetchJson
*/
(function (global) {
  "use strict";

  function ensureStack() {
    var stack = document.getElementById("rec-toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "rec-toast-stack";
      stack.className = "rec-toast-stack";
      stack.setAttribute("aria-live", "polite");
      stack.setAttribute("aria-atomic", "true");
      document.body.appendChild(stack);
    }
    return stack;
  }

  var ICONS = {
    info:    "fa-circle-info",
    success: "fa-circle-check",
    warning: "fa-triangle-exclamation",
    danger:  "fa-circle-exclamation",
  };

  function toast(message, opts) {
    opts = opts || {};
    var kind = opts.kind || "info";
    var title = opts.title || null;
    var timeout = opts.timeout == null ? 3800 : opts.timeout;
    var stack = ensureStack();
    var el = document.createElement("div");
    el.className = "rec-toast rec-toast--" + (kind === "info" ? "" : kind);
    el.innerHTML =
      '<i class="fas ' + (ICONS[kind] || ICONS.info) + '"></i>' +
      '<div style="flex:1 1 auto; min-width:0;">' +
      (title ? "<strong>" + escapeHtml(title) + "</strong>" : "") +
      '<div>' + escapeHtml(String(message || "")) + "</div>" +
      "</div>";
    stack.appendChild(el);
    if (timeout > 0) {
      setTimeout(function () {
        el.classList.add("is-leaving");
        setTimeout(function () { el.remove(); }, 200);
      }, timeout);
    }
    el.addEventListener("click", function () {
      el.classList.add("is-leaving");
      setTimeout(function () { el.remove(); }, 180);
    });
    return el;
  }

  function escapeHtml(str) {
    if (str == null) return "";
    return String(str)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function debounce(fn, wait) {
    var t;
    return function () {
      var a = arguments, ctx = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(ctx, a); }, wait || 200);
    };
  }

  function openModal(idOrEl) {
    var el = typeof idOrEl === "string" ? document.getElementById(idOrEl) : idOrEl;
    if (!el) return;
    el.classList.add("is-open");
    document.body.style.overflow = "hidden";
    var first = el.querySelector("[data-autofocus], input, textarea, select, button");
    if (first) { try { first.focus(); } catch (e) {} }
  }

  function closeModal(idOrEl) {
    var el = typeof idOrEl === "string" ? document.getElementById(idOrEl) : idOrEl;
    if (!el) return;
    el.classList.remove("is-open");
    var stillOpen = document.querySelector(".rec-modal-overlay.is-open");
    if (!stillOpen) document.body.style.overflow = "";
  }

  // Delegação: clique no overlay (fora do .rec-modal) e [data-rec-close] fecha.
  document.addEventListener("click", function (ev) {
    var target = ev.target;
    if (!(target instanceof Element)) return;
    if (target.matches(".rec-modal-overlay.is-open")) {
      closeModal(target);
      return;
    }
    var closer = target.closest("[data-rec-close]");
    if (closer) {
      var overlay = closer.closest(".rec-modal-overlay");
      if (overlay) closeModal(overlay);
    }
  });

  // ESC fecha o modal topo da pilha.
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") return;
    var openList = document.querySelectorAll(".rec-modal-overlay.is-open");
    if (!openList.length) return;
    closeModal(openList[openList.length - 1]);
  });

  async function fetchJson(url, options) {
    var resp = await fetch(url, options || {});
    var ct = resp.headers.get("content-type") || "";
    var data = null;
    if (ct.indexOf("application/json") !== -1) {
      try { data = await resp.json(); } catch (e) { data = null; }
    } else {
      try { data = { _raw: await resp.text() }; } catch (e) { data = null; }
    }
    if (!resp.ok) {
      var err = new Error((data && data.error) || ("Erro " + resp.status));
      err.status = resp.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function fmtDate(iso) {
    if (!iso) return "";
    try {
      var d = typeof iso === "string" ? new Date(iso) : iso;
      if (isNaN(d.getTime())) return String(iso);
      return d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    } catch (e) { return String(iso); }
  }

  var api = {
    toast: toast,
    info:    function (m, o) { return toast(m, Object.assign({ kind: "info" },    o || {})); },
    success: function (m, o) { return toast(m, Object.assign({ kind: "success" }, o || {})); },
    warning: function (m, o) { return toast(m, Object.assign({ kind: "warning" }, o || {})); },
    error:   function (m, o) { return toast(m, Object.assign({ kind: "danger" },  o || {})); },
    openModal: openModal,
    closeModal: closeModal,
    escapeHtml: escapeHtml,
    debounce: debounce,
    fetchJson: fetchJson,
    fmtDate: fmtDate,
  };
  global.rec = api;
})(window);
