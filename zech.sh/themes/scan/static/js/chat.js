// SCAN: Multi-turn chat with hierarchical tool-call UI + streamed AI response via SSE
(function () {
  "use strict";

  var scriptEl = document.currentScript;
  var chatId = scriptEl && scriptEl.getAttribute("data-chat-id");
  var needsStream = scriptEl && scriptEl.getAttribute("data-needs-stream") === "true";
  if (!chatId) return;

  var chatMessages = document.getElementById("chatMessages");
  var activeTurn = document.getElementById("activeTurn");
  var responseEl = document.getElementById("researchResponse");
  var cursorEl = document.getElementById("researchCursor");
  var pipelineEl = document.getElementById("researchPipeline");
  var pipelineStatus = document.getElementById("pipelineStatus");
  var pipelineDetails = document.getElementById("pipelineDetails");
  var followupForm = document.getElementById("chatFollowup");
  var followupInput = followupForm ? followupForm.querySelector("[name=q]") : null;

  // Current stream state
  var buffer = "";
  var receivedFirstText = false;
  var toolGroups = [];
  var groupsByTopic = {};
  var allFetchedUrls = [];
  var totalToolCalls = 0;
  var usageData = null;

  var stageLabels = {
    researching: "RESEARCHING",
    responding: "GENERATING",
  };

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function faviconUrl(url) {
    try {
      var host = new URL(url).hostname;
      return "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(host) + "&sz=16";
    } catch (_) {
      return "";
    }
  }

  function domainFromUrl(url) {
    try { return new URL(url).hostname; } catch (_) { return ""; }
  }

  function fmtNum(n) {
    return Number(n).toLocaleString();
  }

  function fmtUsage(u) {
    return fmtNum(u.input_tokens) + "/" + fmtNum(u.output_tokens) +
      " ($" + u.input_cost + "/$" + u.output_cost + ")";
  }

  function createFaviconImg(url) {
    var src = faviconUrl(url);
    if (!src) return null;
    var img = document.createElement("img");
    img.className = "tool-favicon";
    img.src = src;
    img.alt = "";
    img.title = domainFromUrl(url);
    img.width = 16;
    img.height = 16;
    return img;
  }

  function truncateUrl(url) {
    try {
      var u = new URL(url);
      var path = u.pathname.length > 30 ? u.pathname.slice(0, 30) + "\u2026" : u.pathname;
      return u.hostname + path;
    } catch (_) {
      return url.length > 60 ? url.slice(0, 60) + "\u2026" : url;
    }
  }

  // ---------------------------------------------------------------------------
  // Markdown renderer
  // ---------------------------------------------------------------------------

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

  // ---------------------------------------------------------------------------
  // Render past assistant messages (events summary)
  // ---------------------------------------------------------------------------

  function renderPastEvents(container) {
    var raw = container.getAttribute("data-events");
    if (!raw || raw === "[]") return;

    var events;
    try { events = JSON.parse(raw); } catch (_) { return; }

    // Count tool calls and collect URLs from past events
    var pastToolCalls = 0;
    var pastUrls = [];
    var pastUsage = null;

    events.forEach(function (ev) {
      if (ev.type === "detail") {
        var dt = ev.detail_type;
        if (dt === "research" || dt === "search" || dt === "fetch") pastToolCalls++;
        if (dt === "fetch_done" && ev.url && !ev.failed) pastUrls.push(ev.url);
        if (dt === "usage") pastUsage = ev;
      }
    });

    if (pastToolCalls === 0) return;

    var summary = document.createElement("div");
    summary.className = "tool-summary";

    var chevron = document.createElement("span");
    chevron.className = "tool-chevron";
    summary.appendChild(chevron);

    var count = document.createElement("span");
    count.className = "tool-summary-count";
    count.textContent = pastToolCalls + " tool call" + (pastToolCalls !== 1 ? "s" : "");
    summary.appendChild(count);

    if (pastUrls.length > 0) {
      var favicons = document.createElement("span");
      favicons.className = "tool-summary-favicons";
      var seen = {};
      var fCount = 0;
      pastUrls.forEach(function (u) {
        try {
          var host = new URL(u).hostname;
          if (seen[host] || fCount >= 10) return;
          seen[host] = true;
          fCount++;
          var img = createFaviconImg(u);
          if (img) favicons.appendChild(img);
        } catch (_) {}
      });
      summary.appendChild(favicons);

      var srcCount = document.createElement("span");
      srcCount.className = "tool-source-count";
      srcCount.textContent = "Read " + pastUrls.length + " source" + (pastUrls.length !== 1 ? "s" : "");
      summary.appendChild(srcCount);
    }

    if (pastUsage && pastUsage.total) {
      var usageSpan = document.createElement("span");
      usageSpan.className = "tool-usage";
      usageSpan.textContent = fmtUsage(pastUsage.total);
      summary.appendChild(usageSpan);
    }

    summary.addEventListener("click", function () {
      summary.classList.toggle("is-expanded");
    });

    container.appendChild(summary);
  }

  function renderPastResponse(container) {
    var text = container.textContent;
    if (text) {
      container.innerHTML = renderMarkdown(text);
    }
  }

  function renderPastUsage(container) {
    var raw = container.getAttribute("data-usage");
    if (!raw || raw === "{}") { container.remove(); return; }
    var data;
    try { data = JSON.parse(raw); } catch (_) { container.remove(); return; }
    if (!data.total) { container.remove(); return; }
    container.className = "tool-usage-total";
    var parts = [];
    if (data.research) parts.push("Research agent: " + fmtUsage(data.research));
    if (data.extraction) parts.push("Extraction agent: " + fmtUsage(data.extraction));
    parts.push("Total: " + fmtUsage(data.total));
    container.innerHTML = parts.map(function (p) { return "<div>" + p + "</div>"; }).join("");
  }

  // Initialize past messages
  document.querySelectorAll(".chat-assistant-events").forEach(renderPastEvents);
  document.querySelectorAll(".chat-assistant-response").forEach(renderPastResponse);
  document.querySelectorAll(".chat-assistant-usage").forEach(renderPastUsage);

  // ---------------------------------------------------------------------------
  // DOM builders for streaming (same as research.js)
  // ---------------------------------------------------------------------------

  function createToolGroup(topic) {
    var group = document.createElement("div");
    group.className = "tool-group is-running";

    var header = document.createElement("div");
    header.className = "tool-header";

    var chevron = document.createElement("span");
    chevron.className = "tool-chevron";

    var spinner = document.createElement("span");
    spinner.className = "tool-spinner";

    var label = document.createElement("span");
    label.className = "tool-label";
    label.textContent = "Researching";

    var topicEl = document.createElement("span");
    topicEl.className = "tool-topic";
    topicEl.textContent = topic;

    header.appendChild(chevron);
    header.appendChild(spinner);
    header.appendChild(label);
    header.appendChild(topicEl);
    group.appendChild(header);

    var subline = document.createElement("div");
    subline.className = "tool-subline";
    group.appendChild(subline);

    var sources = document.createElement("div");
    sources.className = "tool-sources";
    sources.hidden = true;
    group.appendChild(sources);

    var body = document.createElement("div");
    body.className = "tool-body";

    var children = document.createElement("div");
    children.className = "tool-children";
    body.appendChild(children);
    group.appendChild(body);

    header.addEventListener("click", function () {
      if (!group.classList.contains("is-done")) return;
      group.classList.toggle("is-collapsed");
    });

    var groupObj = {
      el: group,
      header: header,
      label: label,
      spinner: spinner,
      chevron: chevron,
      subline: subline,
      sources: sources,
      body: body,
      childrenEl: children,
      topic: topic,
      fetchedUrls: [],
      runningChildren: [],
    };

    toolGroups.push(groupObj);
    groupsByTopic[topic] = groupObj;
    pipelineDetails.appendChild(group);
    return groupObj;
  }

  function addChild(group, html, isRunning) {
    var child = document.createElement("div");
    child.className = "tool-child" + (isRunning ? "" : " is-done");
    child.innerHTML = html;
    group.childrenEl.appendChild(child);
    if (isRunning) {
      group.runningChildren.push(child);
    }
    return child;
  }

  function updateSubline(group) {
    var running = group.runningChildren;
    if (running.length === 0) {
      group.subline.innerHTML = "";
      group.subline.hidden = true;
      return;
    }
    var last = running[running.length - 1];
    group.subline.innerHTML = last.innerHTML;
    group.subline.hidden = false;
  }

  function finishChild(group, child, doneHtml) {
    child.innerHTML = doneHtml;
    child.classList.add("is-done");
    var idx = group.runningChildren.indexOf(child);
    if (idx !== -1) group.runningChildren.splice(idx, 1);
    updateSubline(group);
  }

  function collapseGroup(group, numSources) {
    group.el.classList.remove("is-running");
    group.el.classList.add("is-done", "is-collapsed");

    group.spinner.className = "tool-icon-done";
    group.spinner.textContent = "\u2713";
    group.label.textContent = "Researched";

    group.subline.innerHTML = "";
    group.subline.hidden = true;

    if (group.fetchedUrls.length > 0) {
      group.sources.innerHTML = "";
      var shown = group.fetchedUrls.slice(0, 6);
      shown.forEach(function (u) {
        var img = createFaviconImg(u);
        if (img) group.sources.appendChild(img);
      });
      var count = document.createElement("span");
      count.className = "tool-source-count";
      count.textContent = "Read " + numSources + " source" + (numSources !== 1 ? "s" : "");
      group.sources.appendChild(count);
      group.sources.hidden = false;
    }
  }

  function buildSummary() {
    var summaryEl = document.createElement("div");
    summaryEl.className = "tool-summary";
    summaryEl.setAttribute("role", "button");

    var chevron = document.createElement("span");
    chevron.className = "tool-chevron";
    summaryEl.appendChild(chevron);

    var countEl = document.createElement("span");
    countEl.className = "tool-summary-count";
    countEl.textContent = totalToolCalls + " tool call" + (totalToolCalls !== 1 ? "s" : "");
    summaryEl.appendChild(countEl);

    if (allFetchedUrls.length > 0) {
      var favicons = document.createElement("span");
      favicons.className = "tool-summary-favicons";
      var seen = {};
      var count = 0;
      allFetchedUrls.forEach(function (u) {
        try {
          var host = new URL(u).hostname;
          if (seen[host] || count >= 10) return;
          seen[host] = true;
          count++;
          var img = createFaviconImg(u);
          if (img) favicons.appendChild(img);
        } catch (_) {}
      });
      summaryEl.appendChild(favicons);

      var srcCount = document.createElement("span");
      srcCount.className = "tool-source-count";
      srcCount.textContent = "Read " + allFetchedUrls.length + " source" + (allFetchedUrls.length !== 1 ? "s" : "");
      summaryEl.appendChild(srcCount);
    }

    if (usageData) {
      var usageSummary = document.createElement("span");
      usageSummary.className = "tool-usage";
      usageSummary.textContent = fmtUsage(usageData.total);
      summaryEl.appendChild(usageSummary);
    }

    var summaryBody = document.createElement("div");
    summaryBody.className = "tool-summary-body";
    summaryBody.hidden = true;

    toolGroups.forEach(function (g) {
      summaryBody.appendChild(g.el);
    });

    summaryEl.addEventListener("click", function () {
      summaryBody.hidden = !summaryBody.hidden;
      summaryEl.classList.toggle("is-expanded", !summaryBody.hidden);
    });

    pipelineDetails.innerHTML = "";
    pipelineDetails.appendChild(summaryEl);
    pipelineDetails.appendChild(summaryBody);

    if (usageData) {
      var usageTotal = document.createElement("div");
      usageTotal.className = "tool-usage-total";
      usageTotal.innerHTML =
        '<div>Research agent: ' + fmtUsage(usageData.research) + '</div>' +
        '<div>Extraction agent: ' + fmtUsage(usageData.extraction) + '</div>' +
        '<div>Total: ' + fmtUsage(usageData.total) + '</div>';
      summaryBody.appendChild(usageTotal);
    }
  }

  function addDetail(label, text) {
    var item = document.createElement("div");
    item.className = "pipeline-detail";
    item.innerHTML =
      '<span class="pipeline-label">' + escapeHtml(label) + ":</span> " + escapeHtml(text);
    pipelineDetails.appendChild(item);
  }

  // ---------------------------------------------------------------------------
  // Reset stream state for a new turn
  // ---------------------------------------------------------------------------

  function resetStreamState() {
    buffer = "";
    receivedFirstText = false;
    toolGroups = [];
    groupsByTopic = {};
    allFetchedUrls = [];
    totalToolCalls = 0;
    usageData = null;

    pipelineStatus.textContent = "";
    pipelineStatus.className = "pipeline-status";
    pipelineDetails.innerHTML = "";
    responseEl.innerHTML = "";
    responseEl.appendChild(cursorEl);
    activeTurn.hidden = false;
  }

  // ---------------------------------------------------------------------------
  // Finalize: move active turn content into chat history
  // ---------------------------------------------------------------------------

  function finalizeCurrentTurn() {
    var turn = document.createElement("div");
    turn.className = "chat-turn";

    // Move pipeline summary
    if (pipelineDetails.children.length > 0) {
      var eventsContainer = document.createElement("div");
      eventsContainer.className = "chat-assistant-events";
      while (pipelineDetails.firstChild) {
        eventsContainer.appendChild(pipelineDetails.firstChild);
      }
      turn.appendChild(eventsContainer);
    }

    // Move response
    var responseClone = document.createElement("div");
    responseClone.className = "chat-assistant-response";
    responseClone.innerHTML = renderMarkdown(buffer);
    turn.appendChild(responseClone);

    chatMessages.appendChild(turn);
    activeTurn.hidden = true;

    // Scroll to bottom
    window.scrollTo(0, document.body.scrollHeight);
  }

  // ---------------------------------------------------------------------------
  // SSE connection
  // ---------------------------------------------------------------------------

  function connectStream(extraContext) {
    resetStreamState();

    var streamUrl = "/chat/" + chatId + "/stream";
    if (extraContext) {
      streamUrl += "?context=" + encodeURIComponent(extraContext);
    }
    var es = new EventSource(streamUrl);

    var fetchChildren = {};

    es.addEventListener("stage", function (e) {
      var data = JSON.parse(e.data);
      var label = stageLabels[data.stage] || data.stage.toUpperCase();
      pipelineStatus.textContent = label;
      pipelineStatus.className = "pipeline-status visible";
    });

    es.addEventListener("detail", function (e) {
      var data = JSON.parse(e.data);
      var group = data.topic ? groupsByTopic[data.topic] : null;

      if (data.type === "research") {
        totalToolCalls++;
        createToolGroup(data.topic);

      } else if (data.type === "search") {
        totalToolCalls++;
        if (group) {
          var html = '<span class="tool-spinner-sm"></span> Searching "' + escapeHtml(data.query) + '"';
          var child = addChild(group, html, true);
          child._searchQuery = data.query;
          updateSubline(group);
        }

      } else if (data.type === "search_done") {
        if (group) {
          var found = null;
          group.runningChildren.forEach(function (c) {
            if (c._searchQuery === data.query) found = c;
          });
          if (found) {
            var doneHtml = '<span class="tool-icon-done">\u2713</span> Searched "' +
              escapeHtml(data.query) + '" \u2014 ' + data.num_results + ' result' +
              (data.num_results !== 1 ? 's' : '');
            finishChild(group, found, doneHtml);
          }
        }

      } else if (data.type === "fetch") {
        totalToolCalls++;
        if (group) {
          var fav = createFaviconImg(data.url);
          var favHtml = fav ? fav.outerHTML + " " : "";
          var html = '<span class="tool-spinner-sm"></span> ' + favHtml +
            "Reading " + escapeHtml(truncateUrl(data.url));
          var child = addChild(group, html, true);
          fetchChildren[data.url] = { child: child, group: group };
          updateSubline(group);
        }

      } else if (data.type === "fetch_done") {
        var entry = fetchChildren[data.url];
        if (entry) {
          var child = entry.child;
          var g = entry.group;
          var fav = createFaviconImg(data.url);
          var favHtml = fav ? fav.outerHTML + " " : "";
          var verb = data.failed ? "Failed" : "Read";
          var doneHtml = '<span class="tool-icon-done">' + (data.failed ? "\u2717" : "\u2713") +
            '</span> ' + favHtml + verb + " " + escapeHtml(truncateUrl(data.url));
          finishChild(g, child, doneHtml);
          if (!data.failed) {
            g.fetchedUrls.push(data.url);
            allFetchedUrls.push(data.url);
            if (data.usage) {
              var usageSpan = document.createElement("span");
              usageSpan.className = "tool-usage";
              usageSpan.textContent = fmtUsage(data.usage);
              child.appendChild(usageSpan);
            }
            if (data.content) {
              child.classList.add("has-content");
              var contentEl = document.createElement("div");
              contentEl.className = "tool-child-content";
              contentEl.hidden = true;
              contentEl.textContent = data.content;
              child.appendChild(contentEl);
              child.addEventListener("click", function (ev) {
                ev.stopPropagation();
                contentEl.hidden = !contentEl.hidden;
                child.classList.toggle("is-expanded", !contentEl.hidden);
              });
            }
          }
          delete fetchChildren[data.url];
        }

      } else if (data.type === "result") {
        if (group) {
          collapseGroup(group, data.num_sources || 0);
        }

      } else if (data.type === "usage") {
        usageData = data;
        var existingSummary = pipelineDetails.querySelector(".tool-summary");
        if (existingSummary) {
          var usageSpan = document.createElement("span");
          usageSpan.className = "tool-usage";
          usageSpan.textContent = fmtUsage(data.total);
          existingSummary.appendChild(usageSpan);

          var summaryBody = pipelineDetails.querySelector(".tool-summary-body");
          if (summaryBody) {
            var usageTotal = document.createElement("div");
            usageTotal.className = "tool-usage-total";
            usageTotal.innerHTML =
              '<div>Research agent: ' + fmtUsage(data.research) + '</div>' +
              '<div>Extraction agent: ' + fmtUsage(data.extraction) + '</div>' +
              '<div>Total: ' + fmtUsage(data.total) + '</div>';
            summaryBody.appendChild(usageTotal);
          }
        }
      }
    });

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

    es.addEventListener("text", function (e) {
      if (!receivedFirstText) {
        receivedFirstText = true;
        pipelineStatus.className = "pipeline-status";
        toolGroups.forEach(function (g) {
          if (g.el.classList.contains("is-running")) {
            collapseGroup(g, g.fetchedUrls.length);
          }
        });
        if (toolGroups.length > 0) {
          buildSummary();
        }
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
      finalizeCurrentTurn();
      if (followupInput) followupInput.focus();
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
        finalizeCurrentTurn();
      }
    };
  }

  // ---------------------------------------------------------------------------
  // Follow-up form handler
  // ---------------------------------------------------------------------------

  if (followupForm) {
    followupForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var q = followupInput.value.trim();
      if (!q) return;
      followupInput.value = "";

      // Add user message to chat display
      var turn = document.createElement("div");
      turn.className = "chat-turn";
      var queryEl = document.createElement("div");
      queryEl.className = "chat-user-query";
      queryEl.textContent = q;
      turn.appendChild(queryEl);
      chatMessages.appendChild(turn);

      // POST the message to create a user message in DB
      fetch("/chat/" + chatId + "/message", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ content: q }),
      })
      .then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      })
      .then(function () {
        // Start streaming the response
        connectStream();
      })
      .catch(function (err) {
        activeTurn.hidden = false;
        responseEl.innerHTML =
          '<p class="research-error">Error: ' + escapeHtml(err.message) + "</p>";
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Start initial stream if needed
  // ---------------------------------------------------------------------------

  if (needsStream) {
    // Scroll to the last user message
    var lastQuery = chatMessages.querySelector(".chat-turn:last-child .chat-user-query");
    if (lastQuery) lastQuery.scrollIntoView({ behavior: "smooth", block: "start" });
    connectStream();
  } else {
    // Scroll to bottom of existing conversation
    window.scrollTo(0, document.body.scrollHeight);
  }
})();
