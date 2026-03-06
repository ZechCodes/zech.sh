// CHAT AGENT (scan theme): Notification-driven conversational AI with tools
// Uses Skrift's time-series notification system for resilient streaming.
// Send endpoint: /chat/{chat_id}/message (scan's existing route)
(function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // Script data attributes
  // ---------------------------------------------------------------------------

  var scriptEl = document.currentScript;
  var chatId = scriptEl && scriptEl.getAttribute("data-chat-id");
  var needsStream = scriptEl && scriptEl.getAttribute("data-needs-stream") === "true";
  var lastNotificationAt = scriptEl && scriptEl.getAttribute("data-last-notification-at");

  // Prevent premature notification replay before our listener is ready
  if (needsStream && window.__skriftNotifications) {
    window.__skriftNotifications._disconnect();
  }

  // ---------------------------------------------------------------------------
  // Markdown renderer setup
  // ---------------------------------------------------------------------------

  marked.use({
    gfm: true,
    breaks: true,
    renderer: {
      link: function (token) {
        var href = token.href || "";
        var text = this.parser.parseInline(token.tokens);
        if (/^https?:\/\//i.test(href)) {
          return '<a href="' + encodeURI(href) + '" target="_blank" rel="noopener">' + text + "</a>";
        }
        return text;
      },
      code: function (token) {
        return '<pre><code>' + escapeHtml(token.text || "") + "</code></pre>";
      },
    },
  });

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function renderMarkdown(md) {
    return marked.parse(md);
  }

  // ---------------------------------------------------------------------------
  // DOM refs
  // ---------------------------------------------------------------------------

  var chatMessages = document.getElementById("chatMessages");
  var chatWelcome = document.getElementById("chatWelcome");
  var chatForm = document.getElementById("chatForm");
  var chatInput = document.getElementById("chatInput");
  var chatSend = document.getElementById("chatSend");
  var statusArea = document.getElementById("chatStatusArea");
  var thinkingPulse = document.getElementById("chatThinkingPulse");
  var statusText = document.getElementById("chatStatusText");
  var toolEventsEl = document.getElementById("chatToolEvents");

  if (!chatForm || !chatInput) return;

  // ---------------------------------------------------------------------------
  // Render existing messages as markdown
  // ---------------------------------------------------------------------------

  document.querySelectorAll(".chat-msg-model .chat-msg-content").forEach(function (el) {
    var raw = el.textContent;
    if (raw && raw.trim()) {
      el.innerHTML = renderMarkdown(raw);
    }
  });

  // Make server-rendered pills expandable
  document.querySelectorAll(".chat-tool-pill-header").forEach(function (header) {
    var body = header.nextElementSibling;
    if (body && body.classList.contains("chat-tool-pill-body")) {
      header.addEventListener("click", function () {
        body.hidden = !body.hidden;
        header.parentElement.classList.toggle("is-expanded", !body.hidden);
      });
    }
  });

  chatMessages.scrollTop = chatMessages.scrollHeight;

  // ---------------------------------------------------------------------------
  // Auto-resize textarea
  // ---------------------------------------------------------------------------

  function autoResize() {
    chatInput.style.height = "auto";
    chatInput.style.height = Math.min(chatInput.scrollHeight, 150) + "px";
  }

  chatInput.addEventListener("input", autoResize);

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  var isStreaming = false;
  var memoryNotes = "";

  // ---------------------------------------------------------------------------
  // UI helpers
  // ---------------------------------------------------------------------------

  function setStreaming(active) {
    isStreaming = active;
    chatSend.disabled = active;
    statusArea.hidden = !active;
    if (active) {
      statusText.textContent = "THINKING";
      toolEventsEl.innerHTML = "";
      thinkingPulse.style.display = "flex";
    } else {
      thinkingPulse.style.display = "none";
      statusText.textContent = "";
    }
  }

  function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }

  function addUserMessage(text) {
    if (chatWelcome) {
      chatWelcome.remove();
      chatWelcome = null;
    }
    var msg = document.createElement("div");
    msg.className = "chat-msg chat-msg-user";
    msg.innerHTML =
      '<div class="chat-msg-label">YOU</div>' +
      '<div class="chat-msg-content">' + escapeHtml(text) + "</div>";
    chatMessages.appendChild(msg);
    scrollToBottom();
  }

  function createAssistantMessage() {
    var msg = document.createElement("div");
    msg.className = "chat-msg chat-msg-model";
    msg.innerHTML =
      '<div class="chat-msg-label">AGENT</div>' +
      '<div class="chat-tool-pills"></div>' +
      '<div class="chat-msg-content"></div>';
    chatMessages.appendChild(msg);
    return {
      el: msg,
      pills: msg.querySelector(".chat-tool-pills"),
      content: msg.querySelector(".chat-msg-content"),
    };
  }

  function addToolPill(pillsEl, tool, summary, result) {
    var pill = document.createElement("div");
    pill.className = "chat-tool-pill";
    pill.setAttribute("data-tool", tool);
    var iconLabel = tool === "web_search" ? "SEARCH" : "READ";
    var header = document.createElement("div");
    header.className = "chat-tool-pill-header";
    header.innerHTML =
      '<span class="chat-tool-pill-icon">' + iconLabel + "</span>" +
      '<span class="chat-tool-pill-text">' + escapeHtml(summary) + "</span>";
    pill.appendChild(header);
    if (result) {
      var body = document.createElement("div");
      body.className = "chat-tool-pill-body";
      body.hidden = true;
      body.textContent = result;
      pill.appendChild(body);
      header.style.cursor = "pointer";
      header.addEventListener("click", function () {
        body.hidden = !body.hidden;
        pill.classList.toggle("is-expanded", !body.hidden);
      });
    }
    pillsEl.appendChild(pill);
  }

  function addStatusToolEvent(tool, text, isRunning) {
    var ev = document.createElement("div");
    ev.className = "chat-tool-event" + (isRunning ? " is-running" : " is-done");
    var iconLabel = tool === "web_search" ? "SEARCH" : "READ";
    ev.innerHTML =
      '<span class="chat-tool-event-icon">' + iconLabel + "</span>" +
      '<span class="chat-tool-event-text">' + escapeHtml(text) + "</span>";
    toolEventsEl.appendChild(ev);
    statusArea.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return ev;
  }

  function addCompactNotice(removed) {
    var notice = document.createElement("div");
    notice.className = "chat-compact-notice";
    notice.textContent = "// MEMORY COMPACTED — " + removed + " messages summarized";
    chatMessages.appendChild(notice);
  }

  // ---------------------------------------------------------------------------
  // Notification-driven streaming
  // ---------------------------------------------------------------------------

  var activeMsgEls = null;
  var activeBuffer = "";
  var activeToolEvents = [];
  var activeToolEventEl = null;
  var cleanupListener = null;

  function connectStream() {
    setStreaming(true);

    if (!activeMsgEls) {
      activeMsgEls = createAssistantMessage();
    }
    activeBuffer = "";
    activeToolEvents = [];
    activeToolEventEl = null;

    function handler(e) {
      var data = e.detail;
      if (data.chat_id !== chatId) return;
      e.preventDefault();

      var ntype = data.type;

      if (ntype === "chat:thinking") {
        statusText.textContent = "THINKING";
        thinkingPulse.style.display = "flex";

      } else if (ntype === "chat:tool_start") {
        var toolText =
          data.tool === "web_search"
            ? "Searching '" + (data.args.query || "") + "'"
            : "Reading " + (data.args.url || "");
        statusText.textContent = data.tool === "web_search" ? "SEARCHING" : "READING";
        activeToolEventEl = addStatusToolEvent(data.tool, toolText, true);

      } else if (ntype === "chat:tool_done") {
        if (activeToolEventEl) {
          activeToolEventEl.classList.remove("is-running");
          activeToolEventEl.classList.add("is-done");
          activeToolEventEl.querySelector(".chat-tool-event-text").textContent = data.summary;
        }
        activeToolEvents.push({ tool: data.tool, summary: data.summary, result: data.result || "" });
        statusText.textContent = "THINKING";
        activeToolEventEl = null;

      } else if (ntype === "chat:text") {
        thinkingPulse.style.display = "none";
        statusText.textContent = "RESPONDING";
        activeBuffer += data.text;
        activeMsgEls.content.innerHTML = renderMarkdown(activeBuffer);
        scrollToBottom();

      } else if (ntype === "chat:compact") {
        addCompactNotice(data.removed_messages);

      } else if (ntype === "chat:done") {
        // Render tool pills inline in the message
        for (var i = 0; i < activeToolEvents.length; i++) {
          addToolPill(activeMsgEls.pills, activeToolEvents[i].tool, activeToolEvents[i].summary, activeToolEvents[i].result);
        }
        // Show usage
        if (data.usage && data.usage.input_tokens) {
          var cost = (parseFloat(data.usage.input_cost) + parseFloat(data.usage.output_cost)).toFixed(4);
          var usageEl = document.createElement("div");
          usageEl.className = "chat-usage";
          usageEl.innerHTML =
            '<span class="chat-usage-tokens">' + data.usage.input_tokens + " in / " + data.usage.output_tokens + " out</span>" +
            '<span class="chat-usage-cost">$' + cost + "</span>";
          activeMsgEls.el.appendChild(usageEl);
        }
        // Store notes for next message round-trip
        if (data.notes) {
          memoryNotes = data.notes;
        }
        setStreaming(false);
        scrollToBottom();
        activeMsgEls = null;
        cleanup();

      } else if (ntype === "chat:error") {
        setStreaming(false);
        if (activeMsgEls) {
          activeMsgEls.content.innerHTML =
            '<p style="color: #ff6b6b;">Error: ' +
            escapeHtml(data.error || "Connection lost") +
            "</p>";
        }
        scrollToBottom();
        activeMsgEls = null;
        cleanup();
      }
    }

    document.addEventListener("sk:notification", handler);

    function cleanup() {
      document.removeEventListener("sk:notification", handler);
      cleanupListener = null;
    }
    cleanupListener = cleanup;

    // Reconnect Skrift notifications to trigger replay
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
  // Send message
  // ---------------------------------------------------------------------------

  function sendMessage(text) {
    if (!text.trim() || isStreaming) return;

    addUserMessage(text);
    activeMsgEls = createAssistantMessage();
    setStreaming(true);

    fetch("/chat/" + chatId + "/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ content: text, notes: memoryNotes, tz: Intl.DateTimeFormat().resolvedOptions().timeZone }),
    })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function () {
        // Now connect to notifications to receive the response
        connectStream();
      })
      .catch(function (err) {
        setStreaming(false);
        if (activeMsgEls) {
          activeMsgEls.content.innerHTML =
            '<p style="color: #ff6b6b;">Error: ' + escapeHtml(err.message) + "</p>";
        }
        scrollToBottom();
        activeMsgEls = null;
      });
  }

  // ---------------------------------------------------------------------------
  // Form submission
  // ---------------------------------------------------------------------------

  chatForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = "";
    autoResize();
    sendMessage(text);
  });

  chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      chatForm.dispatchEvent(new Event("submit"));
    }
  });

  // ---------------------------------------------------------------------------
  // Focus input when typing
  // ---------------------------------------------------------------------------

  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.activeElement === chatInput) return;
    if (document.activeElement && document.activeElement.tagName === "INPUT") return;
    if (document.activeElement && document.activeElement.tagName === "TEXTAREA") return;
    if (e.key.length === 1 && !isStreaming) chatInput.focus();
  });

  // ---------------------------------------------------------------------------
  // Start stream on page load if needed (e.g. page refresh during generation)
  // ---------------------------------------------------------------------------

  if (needsStream && chatId) {
    activeMsgEls = createAssistantMessage();
    connectStream();
  }
})();
