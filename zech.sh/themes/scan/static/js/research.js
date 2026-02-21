// SCAN: Research mode — streams AI response via SSE, renders markdown
(function () {
  "use strict";

  var query = window.__researchQuery;
  if (!query) return;

  var responseEl = document.getElementById("researchResponse");
  var cursorEl = document.getElementById("researchCursor");
  var statusEl = document.getElementById("researchStatus");
  var buffer = "";

  // Listen for Skrift tool-use notifications (search status)
  document.addEventListener("sk:notification", function (e) {
    if (e.detail.type !== "research_status") return;
    e.preventDefault(); // suppress default toast
    if (e.detail.status === "searching") {
      statusEl.textContent = "Searching: " + (e.detail.query || "…");
      statusEl.classList.add("visible");
    }
  });

  // Simple markdown → HTML (bold, italic, links, code, headings, lists, paragraphs)
  function renderMarkdown(md) {
    var html = md
      // Code blocks
      .replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
        return '<pre class="research-code"><code>' + escapeHtml(code.trim()) + "</code></pre>";
      })
      // Inline code
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      // Headings
      .replace(/^### (.+)$/gm, "<h4>$1</h4>")
      .replace(/^## (.+)$/gm, "<h3>$1</h3>")
      .replace(/^# (.+)$/gm, "<h2>$1</h2>")
      // Bold + italic
      .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      // Links
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      // Unordered lists
      .replace(/^[-*] (.+)$/gm, "<li>$1</li>")
      // Ordered lists
      .replace(/^\d+\. (.+)$/gm, "<li>$1</li>")
      // Line breaks → paragraphs
      .replace(/\n\n+/g, "</p><p>")
      .replace(/\n/g, "<br>");

    // Wrap consecutive <li> in <ul>
    html = html.replace(/((?:<li>.*?<\/li>(?:<br>)?)+)/g, "<ul>$1</ul>");
    html = html.replace(/<ul>([\s\S]*?)<\/ul>/g, function (_, inner) {
      return "<ul>" + inner.replace(/<br>/g, "") + "</ul>";
    });

    return "<p>" + html + "</p>";
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  // Connect to the research SSE stream
  var url = "/research/stream?q=" + encodeURIComponent(query);
  var es = new EventSource(url);

  es.addEventListener("text", function (e) {
    var data = JSON.parse(e.data);
    buffer += data.text;
    responseEl.innerHTML = renderMarkdown(buffer);
    responseEl.appendChild(cursorEl);
    // Auto-scroll
    responseEl.scrollTop = responseEl.scrollHeight;
  });

  es.addEventListener("done", function () {
    es.close();
    cursorEl.remove();
    statusEl.classList.remove("visible");
    // Final render
    responseEl.innerHTML = renderMarkdown(buffer);
  });

  es.addEventListener("error", function (e) {
    // SSE error event — could be a stream error or connection drop
    if (e.data) {
      var data = JSON.parse(e.data);
      responseEl.innerHTML =
        '<p class="research-error">Error: ' + escapeHtml(data.error) + "</p>";
    }
    es.close();
    cursorEl.remove();
    statusEl.classList.remove("visible");
  });

  es.onerror = function () {
    // Connection error after stream started — if we have content, just finish
    if (buffer) {
      es.close();
      cursorEl.remove();
      statusEl.classList.remove("visible");
      responseEl.innerHTML = renderMarkdown(buffer);
    }
  };
})();
