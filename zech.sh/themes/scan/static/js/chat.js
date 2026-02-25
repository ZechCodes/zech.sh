// SCAN: Multi-turn chat with hierarchical tool-call UI + notification-driven pipeline
// Depends on scan-pipeline.js (loaded before this script).
(function () {
  "use strict";

  var SP = window.ScanPipeline;
  var scriptEl = document.currentScript;
  var chatId = scriptEl && scriptEl.getAttribute("data-chat-id");
  var chatMode = scriptEl && scriptEl.getAttribute("data-chat-mode");
  var chatTitle = scriptEl && scriptEl.getAttribute("data-chat-title");
  var needsStream = scriptEl && scriptEl.getAttribute("data-needs-stream") === "true";
  var lastNotificationAt = scriptEl && scriptEl.getAttribute("data-last-notification-at");
  if (!chatId) return;

  // Prevent premature notification replay — Skrift auto-connects and would
  // dispatch queued sk:notification events before our listener is attached.
  // We disconnect now and reconnect inside connectStream() after the listener is ready.
  if (needsStream && window.__skriftNotifications) {
    window.__skriftNotifications._disconnect();
  }

  var chatMessages = document.getElementById("chatMessages");
  var activeTurn = document.getElementById("activeTurn");
  var responseEl = document.getElementById("researchResponse");
  var cursorEl = document.getElementById("researchCursor");
  var pipelineStatus = document.getElementById("pipelineStatus");
  var pipelineDetails = document.getElementById("pipelineDetails");
  var followupForm = document.getElementById("chatFollowup");
  var followupInput = followupForm ? followupForm.querySelector("[name=q]") : null;

  var pipeline = SP.createPipeline({
    pipelineDetails: pipelineDetails,
    pipelineStatus: pipelineStatus,
    responseEl: responseEl,
    cursorEl: cursorEl,
  });

  // ---------------------------------------------------------------------------
  // Response action helpers (copy / download)
  // ---------------------------------------------------------------------------

  function slugify(text) {
    return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }

  function createResponseActions(markdown) {
    var row = document.createElement("div");
    row.className = "response-actions";

    // Copy button
    var copyBtn = document.createElement("button");
    copyBtn.className = "response-action-btn";
    copyBtn.title = "Copy as Markdown";
    copyBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    copyBtn.addEventListener("click", function () {
      navigator.clipboard.writeText(markdown).then(function () {
        var fb = document.createElement("span");
        fb.className = "response-actions-feedback";
        fb.textContent = "Copied!";
        row.appendChild(fb);
        setTimeout(function () { fb.remove(); }, 1500);
      });
    });
    row.appendChild(copyBtn);

    // Download button
    var dlBtn = document.createElement("button");
    dlBtn.className = "response-action-btn";
    dlBtn.title = "Download as Markdown";
    dlBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
    dlBtn.addEventListener("click", function () {
      var filename = (chatTitle ? slugify(chatTitle) : "research") + ".md";
      var blob = new Blob([markdown], { type: "text/markdown" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    });
    row.appendChild(dlBtn);

    return row;
  }

  // ---------------------------------------------------------------------------
  // Render past assistant messages (events summary)
  // ---------------------------------------------------------------------------

  function renderPastEvents(container) {
    var raw = container.getAttribute("data-events");
    if (!raw || raw === "[]") return;

    var events;
    try { events = JSON.parse(raw); } catch (_) { return; }

    var pastToolCalls = 0;
    var pastUrls = [];
    var pastUsage = null;
    var currentReasoningText = "";
    var groups = {};
    var itemOrder = [];
    var lastTopic = "";

    events.forEach(function (ev) {
      if (ev.type !== "detail") return;
      var dt = ev.detail_type;
      var topic = ev.topic || "";

      if (dt === "reasoning") {
        currentReasoningText += ev.text || "";
      } else if (dt === "research") {
        if (currentReasoningText) {
          itemOrder.push({ type: "reasoning", text: currentReasoningText });
          currentReasoningText = "";
        }
        lastTopic = topic;
        itemOrder.push({ type: "research", topic: topic });
        pastToolCalls++;
        if (topic && !groups[topic]) {
          groups[topic] = { topic: topic, searches: [], fetches: [], urls: [], numSources: 0 };
        }
      } else if (dt === "search") {
        pastToolCalls++;
        if (groups[topic]) groups[topic].searches.push({ query: ev.query, numResults: null });
      } else if (dt === "search_done") {
        if (groups[topic]) {
          var s = groups[topic].searches;
          for (var i = s.length - 1; i >= 0; i--) {
            if (s[i].query === ev.query) { s[i].numResults = ev.num_results; break; }
          }
        }
      } else if (dt === "fetch") {
        pastToolCalls++;
        if (groups[topic]) groups[topic].fetches.push({ url: ev.url, failed: false, content: null, usage: null });
      } else if (dt === "fetch_done") {
        if (groups[topic]) {
          var f = groups[topic].fetches;
          for (var i = f.length - 1; i >= 0; i--) {
            if (f[i].url === ev.url) {
              f[i].failed = !!ev.failed;
              f[i].content = ev.content || null;
              f[i].usage = ev.usage || null;
              break;
            }
          }
          if (!ev.failed && ev.url) {
            groups[topic].urls.push(ev.url);
            pastUrls.push(ev.url);
          }
        }
      } else if (dt === "result") {
        if (groups[topic]) groups[topic].numSources = ev.num_sources || 0;
      } else if (dt === "usage") {
        if (currentReasoningText) {
          itemOrder.push({ type: "reasoning", text: currentReasoningText });
          currentReasoningText = "";
        }
        pastUsage = ev;
      } else if (dt === "message") {
        var msgTopic = topic || lastTopic;
        if (msgTopic && groups[msgTopic]) {
          if (!groups[msgTopic].messages) groups[msgTopic].messages = [];
          groups[msgTopic].messages.push(ev.text || "");
        }
      }
    });

    if (pastToolCalls === 0) return;

    // Build summary header
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
          var img = SP.createFaviconImg(u);
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
      usageSpan.textContent = SP.fmtUsage(pastUsage.total);
      summary.appendChild(usageSpan);
    }

    // Build collapsible body
    var summaryBody = document.createElement("div");
    summaryBody.className = "tool-summary-body";
    summaryBody.hidden = true;

    itemOrder.forEach(function (item) {
      if (item.type === "reasoning") {
        var reasonEl = document.createElement("div");
        reasonEl.className = "deep-reasoning is-complete";
        reasonEl.innerHTML = SP.renderMarkdown(item.text);
        summaryBody.appendChild(reasonEl);
        return;
      }

      var g = groups[item.topic];
      if (!g) return;

      var groupEl = document.createElement("div");
      groupEl.className = "tool-group is-done is-collapsed";

      var header = document.createElement("div");
      header.className = "tool-header";
      var gChevron = document.createElement("span");
      gChevron.className = "tool-chevron";
      var icon = document.createElement("span");
      icon.className = "tool-icon-done";
      icon.textContent = "\u2713";
      var label = document.createElement("span");
      label.className = "tool-label";
      label.textContent = "Researched";
      var topicEl = document.createElement("span");
      topicEl.className = "tool-topic";
      topicEl.textContent = g.topic;

      header.appendChild(gChevron);
      header.appendChild(icon);
      header.appendChild(label);
      header.appendChild(topicEl);
      groupEl.appendChild(header);

      if (g.urls.length > 0) {
        var sources = document.createElement("div");
        sources.className = "tool-sources";
        g.urls.slice(0, 6).forEach(function (u) {
          var img = SP.createFaviconImg(u);
          if (img) sources.appendChild(img);
        });
        var sc = document.createElement("span");
        sc.className = "tool-source-count";
        sc.textContent = "Read " + g.numSources + " source" + (g.numSources !== 1 ? "s" : "");
        sources.appendChild(sc);
        groupEl.appendChild(sources);
      }

      var body = document.createElement("div");
      body.className = "tool-body";
      var childrenEl = document.createElement("div");
      childrenEl.className = "tool-children";

      g.searches.forEach(function (s) {
        var child = document.createElement("div");
        child.className = "tool-child is-done";
        child.innerHTML = '<span class="tool-icon-done">\u2713</span> Searched "' +
          SP.escapeHtml(s.query) + '"' +
          (s.numResults !== null ? " \u2014 " + s.numResults + " result" + (s.numResults !== 1 ? "s" : "") : "");
        childrenEl.appendChild(child);
      });

      g.fetches.forEach(function (f) {
        var child = document.createElement("div");
        child.className = "tool-child is-done";
        var fav = SP.createFaviconImg(f.url);
        var favHtml = fav ? fav.outerHTML + " " : "";
        var verb = f.failed ? "Failed" : "Read";
        var iconChar = f.failed ? "\u2717" : "\u2713";
        child.innerHTML = '<span class="tool-icon-done">' + iconChar + '</span> ' +
          favHtml + verb + " " + SP.escapeHtml(SP.truncateUrl(f.url));
        if (f.usage) {
          var uSpan = document.createElement("span");
          uSpan.className = "tool-usage";
          uSpan.textContent = SP.fmtUsage(f.usage);
          child.appendChild(uSpan);
        }
        if (f.content) {
          child.classList.add("has-content");
          var contentEl = document.createElement("div");
          contentEl.className = "tool-child-content";
          contentEl.hidden = true;
          contentEl.textContent = f.content;
          child.appendChild(contentEl);
          child.addEventListener("click", function (ev) {
            ev.stopPropagation();
            contentEl.hidden = !contentEl.hidden;
            child.classList.toggle("is-expanded", !contentEl.hidden);
          });
        }
        childrenEl.appendChild(child);
      });

      if (g.messages) {
        g.messages.forEach(function (text) {
          var msgEl = document.createElement("div");
          msgEl.className = "tool-message";
          msgEl.innerHTML = SP.renderMarkdown(text);
          childrenEl.appendChild(msgEl);
        });
      }

      body.appendChild(childrenEl);
      groupEl.appendChild(body);

      header.addEventListener("click", function () {
        groupEl.classList.toggle("is-collapsed");
      });

      summaryBody.appendChild(groupEl);
    });

    if (pastUsage) {
      var usageTotal = document.createElement("div");
      usageTotal.className = "tool-usage-total";
      var parts = [];
      if (pastUsage.research) parts.push("Research agent: " + SP.fmtUsage(pastUsage.research));
      if (pastUsage.extraction) parts.push("Extraction agent: " + SP.fmtUsage(pastUsage.extraction));
      if (pastUsage.total) parts.push("Total: " + SP.fmtUsage(pastUsage.total));
      usageTotal.innerHTML = parts.map(function (p) { return "<div>" + p + "</div>"; }).join("");
      summaryBody.appendChild(usageTotal);
    }

    summary.addEventListener("click", function () {
      summaryBody.hidden = !summaryBody.hidden;
      summary.classList.toggle("is-expanded", !summaryBody.hidden);
    });

    container.appendChild(summary);
    container.appendChild(summaryBody);
  }

  function renderPastResponse(container) {
    var rawMarkdown = container.textContent;
    if (rawMarkdown) {
      container.innerHTML = SP.renderMarkdown(rawMarkdown);
      container.parentNode.appendChild(createResponseActions(rawMarkdown));
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
    if (data.research) parts.push("Research agent: " + SP.fmtUsage(data.research));
    if (data.extraction) parts.push("Extraction agent: " + SP.fmtUsage(data.extraction));
    parts.push("Total: " + SP.fmtUsage(data.total));
    container.innerHTML = parts.map(function (p) { return "<div>" + p + "</div>"; }).join("");
  }

  // Initialize past messages
  document.querySelectorAll(".chat-assistant-events").forEach(renderPastEvents);
  document.querySelectorAll(".chat-assistant-response").forEach(renderPastResponse);
  document.querySelectorAll(".chat-assistant-usage").forEach(renderPastUsage);

  // ---------------------------------------------------------------------------
  // Finalize: move active turn content into chat history
  // ---------------------------------------------------------------------------

  function finalizeCurrentTurn() {
    var turn = document.createElement("div");
    turn.className = "chat-turn";

    if (pipelineDetails.children.length > 0) {
      var eventsContainer = document.createElement("div");
      eventsContainer.className = "chat-assistant-events";
      while (pipelineDetails.firstChild) {
        eventsContainer.appendChild(pipelineDetails.firstChild);
      }
      turn.appendChild(eventsContainer);
    }

    var responseClone = document.createElement("div");
    responseClone.className = "chat-assistant-response";
    responseClone.innerHTML = SP.renderMarkdown(pipeline.state.buffer);
    turn.appendChild(responseClone);
    turn.appendChild(createResponseActions(pipeline.state.buffer));

    chatMessages.appendChild(turn);
    activeTurn.hidden = true;
    window.scrollTo(0, document.body.scrollHeight);
  }

  // ---------------------------------------------------------------------------
  // Clarification UI helper
  // ---------------------------------------------------------------------------

  function showClarification(questions, reconnect) {
    var container = document.createElement("div");
    container.className = "pipeline-clarification";
    questions.forEach(function (question) {
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
      pipeline.addDetail("YOU", answer);
      reconnect(answer);
    });
  }

  // ---------------------------------------------------------------------------
  // Notification-driven pipeline connection
  // ---------------------------------------------------------------------------

  function connectStream() {
    pipeline.reset();
    activeTurn.hidden = false;

    pipeline.connectNotifications(chatId, {
      onDone: function () { finalizeCurrentTurn(); },
      onClarification: function (questions) {
        showClarification(questions, function (answer) {
          sendMessage(answer);
        });
      },
      onError: function () {
        if (pipeline.state.buffer) finalizeCurrentTurn();
      },
    });

    // Listener is ready — reconnect Skrift to trigger replay of queued notifications.
    // Setting lastSeen to the chat's cursor ensures we replay from the right point;
    // if null (pipeline in-progress), Skrift flushes all queued notifications.
    var sn = window.__skriftNotifications;
    if (sn) {
      sn.configure({ persistConnection: true });
      if (lastNotificationAt) {
        sn.lastSeen = parseFloat(lastNotificationAt);
      }
      sn._disconnect();
      sn._connect();
    }
  }

  // ---------------------------------------------------------------------------
  // Follow-up form handler
  // ---------------------------------------------------------------------------

  var userTz = "";
  try { userTz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (_) {}

  function sendMessage(content) {
    fetch("/chat/" + chatId + "/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ content: content, tz: userTz }),
    })
    .then(function (r) {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    })
    .then(function () { connectStream(); })
    .catch(function (err) {
      activeTurn.hidden = false;
      responseEl.innerHTML =
        '<p class="research-error">Error: ' + SP.escapeHtml(err.message) + "</p>";
    });
  }

  if (followupForm) {
    followupForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var q = followupInput.value.trim();
      if (!q) return;
      followupInput.value = "";

      var turn = document.createElement("div");
      turn.className = "chat-turn";
      var queryEl = document.createElement("div");
      queryEl.className = "chat-user-query";
      queryEl.textContent = q;
      turn.appendChild(queryEl);
      chatMessages.appendChild(turn);

      sendMessage(q);
    });
  }

  // ---------------------------------------------------------------------------
  // Start initial stream if needed
  // ---------------------------------------------------------------------------

  var lastTurn = chatMessages.querySelector(".chat-turn:last-child");
  if (lastTurn) {
    var nav = document.querySelector(".zech-topnav");
    var offset = nav ? nav.offsetHeight + 16 : 0;
    var top = lastTurn.getBoundingClientRect().top + window.scrollY - offset;
    window.scrollTo({ top: top, behavior: "smooth" });
  }

  if (needsStream) connectStream();

  if (followupInput) {
    document.addEventListener("keydown", function (e) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (document.activeElement === followupInput) return;
      if (document.activeElement && document.activeElement.tagName === "INPUT") return;
      if (e.key.length === 1) followupInput.focus();
    });
  }
})();
