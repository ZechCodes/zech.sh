// CHAT AGENT: Conversational AI with tools, thinking display, and streaming
(function () {
  "use strict";

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
  var chatClear = document.getElementById("chatClear");
  var statusArea = document.getElementById("chatStatusArea");
  var thinkingPulse = document.getElementById("chatThinkingPulse");
  var statusText = document.getElementById("chatStatusText");
  var toolEvents = document.getElementById("chatToolEvents");

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

  // Auto-scroll to bottom on load
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

  // ---------------------------------------------------------------------------
  // UI helpers
  // ---------------------------------------------------------------------------

  function setStreaming(active) {
    isStreaming = active;
    chatSend.disabled = active;
    statusArea.hidden = !active;
    if (active) {
      statusText.textContent = "THINKING";
      toolEvents.innerHTML = "";
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
      '<div class="chat-msg-content"></div>';
    chatMessages.appendChild(msg);
    return msg.querySelector(".chat-msg-content");
  }

  function addToolEvent(tool, text, isRunning) {
    var ev = document.createElement("div");
    ev.className = "chat-tool-event" + (isRunning ? " is-running" : " is-done");

    var iconLabel = tool === "web_search" ? "SEARCH" : "FETCH";
    ev.innerHTML =
      '<span class="chat-tool-event-icon">' + iconLabel + "</span>" +
      '<span class="chat-tool-event-text">' + escapeHtml(text) + "</span>";
    toolEvents.appendChild(ev);

    // Scroll status area into view
    statusArea.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return ev;
  }

  function addCompactNotice(removed, summaryTokens) {
    var notice = document.createElement("div");
    notice.className = "chat-compact-notice";
    notice.textContent = "// MEMORY COMPACTED — " + removed + " messages summarized";
    chatMessages.appendChild(notice);
  }

  // ---------------------------------------------------------------------------
  // Send message & stream response
  // ---------------------------------------------------------------------------

  function sendMessage(text) {
    if (!text.trim() || isStreaming) return;

    addUserMessage(text);
    setStreaming(true);

    var contentEl = createAssistantMessage();
    var buffer = "";
    var currentToolEvent = null;

    fetch("/chat/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ message: text }),
    })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        var reader = response.body.getReader();
        var decoder = new TextDecoder();
        var sseBuffer = "";

        function processSSE(line) {
          if (!line.trim()) return;

          if (line.startsWith("event:")) {
            processSSE._currentEvent = line.slice(6).trim();
            return;
          }

          if (line.startsWith("data:")) {
            var eventType = processSSE._currentEvent || "message";
            var dataStr = line.slice(5).trim();
            processSSE._currentEvent = null;

            if (eventType === "thinking") {
              statusText.textContent = "THINKING";
              thinkingPulse.style.display = "flex";
            } else if (eventType === "tool_start") {
              var toolData = JSON.parse(dataStr);
              var toolText =
                toolData.tool === "web_search"
                  ? 'Searching "' + (toolData.args.query || "") + '"'
                  : "Opening " + (toolData.args.url || "");
              statusText.textContent = toolData.tool === "web_search" ? "SEARCHING" : "FETCHING";
              currentToolEvent = addToolEvent(toolData.tool, toolText, true);
            } else if (eventType === "tool_done") {
              var doneData = JSON.parse(dataStr);
              if (currentToolEvent) {
                currentToolEvent.classList.remove("is-running");
                currentToolEvent.classList.add("is-done");
                currentToolEvent.querySelector(".chat-tool-event-text").textContent = doneData.summary;
              }
              statusText.textContent = "THINKING";
              currentToolEvent = null;
            } else if (eventType === "text") {
              thinkingPulse.style.display = "none";
              statusText.textContent = "RESPONDING";
              var textData = JSON.parse(dataStr);
              buffer += textData.text;
              contentEl.innerHTML = renderMarkdown(buffer);
              scrollToBottom();
            } else if (eventType === "compact") {
              var compactData = JSON.parse(dataStr);
              addCompactNotice(compactData.removed_messages, compactData.summary_tokens);
            } else if (eventType === "done") {
              setStreaming(false);
              scrollToBottom();
            } else if (eventType === "error") {
              setStreaming(false);
              var errData = {};
              try { errData = JSON.parse(dataStr); } catch (_) {}
              contentEl.innerHTML =
                '<p style="color: #ff6b6b;">Error: ' +
                escapeHtml(errData.error || "Connection lost") +
                "</p>";
              scrollToBottom();
            }
          }
        }
        processSSE._currentEvent = null;

        function pump() {
          return reader.read().then(function (result) {
            if (result.done) {
              if (isStreaming) setStreaming(false);
              return;
            }

            sseBuffer += decoder.decode(result.value, { stream: true });
            var lines = sseBuffer.split("\n");
            sseBuffer = lines.pop(); // Keep incomplete line

            lines.forEach(processSSE);
            return pump();
          });
        }

        return pump();
      })
      .catch(function (err) {
        setStreaming(false);
        contentEl.innerHTML =
          '<p style="color: #ff6b6b;">Error: ' + escapeHtml(err.message) + "</p>";
        scrollToBottom();
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

  // Enter to send, Shift+Enter for newline
  chatInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      chatForm.dispatchEvent(new Event("submit"));
    }
  });

  // ---------------------------------------------------------------------------
  // Clear chat
  // ---------------------------------------------------------------------------

  if (chatClear) {
    chatClear.addEventListener("click", function () {
      fetch("/chat/clear", {
        method: "POST",
        credentials: "same-origin",
      })
        .then(function (r) {
          if (r.ok) window.location.reload();
        })
        .catch(function () {
          window.location.reload();
        });
    });
  }

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
})();
