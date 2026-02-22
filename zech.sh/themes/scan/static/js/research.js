// SCAN: Research mode — pipeline stages + streamed AI response via SSE
(function () {
  "use strict";

  var scriptEl = document.currentScript;
  var query = scriptEl && scriptEl.getAttribute("data-query");
  if (!query) return;

  var responseEl = document.getElementById("researchResponse");
  var cursorEl = document.getElementById("researchCursor");
  var pipelineEl = document.getElementById("researchPipeline");
  var pipelineStatus = document.getElementById("pipelineStatus");
  var pipelineDetails = document.getElementById("pipelineDetails");
  var buffer = "";
  var receivedFirstText = false;

  var stageLabels = {
    researching: "RESEARCHING",
    responding: "GENERATING",
  };

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function addDetail(label, text) {
    var item = document.createElement("div");
    item.className = "pipeline-detail";
    item.innerHTML =
      '<span class="pipeline-label">' + escapeHtml(label) + ":</span> " + escapeHtml(text);
    pipelineDetails.appendChild(item);
  }

  // Simple markdown → HTML
  function renderMarkdown(md) {
    var html = md
      .replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
        return '<pre class="research-code"><code>' + escapeHtml(code.trim()) + "</code></pre>";
      })
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/^### (.+)$/gm, "<h4>$1</h4>")
      .replace(/^## (.+)$/gm, "<h3>$1</h3>")
      .replace(/^# (.+)$/gm, "<h2>$1</h2>")
      .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/^[-*] (.+)$/gm, "<li>$1</li>")
      .replace(/^\d+\. (.+)$/gm, "<li>$1</li>")
      .replace(/\n\n+/g, "</p><p>")
      .replace(/\n/g, "<br>");

    html = html.replace(/((?:<li>.*?<\/li>(?:<br>)?)+)/g, "<ul>$1</ul>");
    html = html.replace(/<ul>([\s\S]*?)<\/ul>/g, function (_, inner) {
      return "<ul>" + inner.replace(/<br>/g, "") + "</ul>";
    });

    return "<p>" + html + "</p>";
  }

  function connectStream(extraContext) {
    var streamUrl = "/research/stream?q=" + encodeURIComponent(query);
    if (extraContext) {
      streamUrl += "&context=" + encodeURIComponent(extraContext);
    }
    var es = new EventSource(streamUrl);

    // Pipeline stage updates
    es.addEventListener("stage", function (e) {
      var data = JSON.parse(e.data);
      var label = stageLabels[data.stage] || data.stage.toUpperCase();
      pipelineStatus.textContent = label;
      pipelineStatus.className = "pipeline-status visible";
    });

    // Pipeline detail items
    es.addEventListener("detail", function (e) {
      var data = JSON.parse(e.data);
      if (data.type === "research") {
        addDetail("RESEARCH", data.topic);
      } else if (data.type === "search") {
        addDetail("SEARCH", data.query);
      } else if (data.type === "fetch") {
        addDetail("READING", data.url);
      } else if (data.type === "result") {
        addDetail("FOUND", data.summary);
      }
    });

    // Clarification — evaluator needs user input
    es.addEventListener("clarification", function (e) {
      es.close();
      pipelineStatus.className = "pipeline-status";
      var data = JSON.parse(e.data);
      var container = document.createElement("div");
      container.className = "pipeline-clarification";
      data.questions.forEach(function (question) {
        var qEl = document.createElement("div");
        qEl.className = "clarification-question";
        qEl.textContent = question;
        container.appendChild(qEl);
      });
      var form = document.createElement("form");
      form.className = "clarification-form";
      var input = document.createElement("input");
      input.type = "text";
      input.className = "scan-input clarification-input";
      input.placeholder = "Provide details...";
      input.autofocus = true;
      form.appendChild(input);
      container.appendChild(form);
      pipelineDetails.appendChild(container);
      input.focus();
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var answer = input.value.trim();
        if (!answer) return;
        container.remove();
        addDetail("YOU", answer);
        connectStream(answer);
      });
    });

    // Streamed response text
    es.addEventListener("text", function (e) {
      if (!receivedFirstText) {
        receivedFirstText = true;
        pipelineStatus.className = "pipeline-status";
      }
      var data = JSON.parse(e.data);
      buffer += data.text;
      responseEl.innerHTML = renderMarkdown(buffer);
      responseEl.appendChild(cursorEl);
      responseEl.scrollTop = responseEl.scrollHeight;
    });

    es.addEventListener("done", function () {
      es.close();
      cursorEl.remove();
      responseEl.innerHTML = renderMarkdown(buffer);
    });

    es.addEventListener("error", function (e) {
      if (e.data) {
        var data = JSON.parse(e.data);
        responseEl.innerHTML =
          '<p class="research-error">Error: ' + escapeHtml(data.error) + "</p>";
      }
      es.close();
      cursorEl.remove();
      pipelineStatus.className = "pipeline-status";
    });

    es.onerror = function () {
      if (buffer) {
        es.close();
        cursorEl.remove();
        responseEl.innerHTML = renderMarkdown(buffer);
      }
    };
  }

  // Start the initial stream
  connectStream();
})();
