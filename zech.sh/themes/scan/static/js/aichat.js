// AI.CHAT — Real-time chat client with SSE notifications
(function () {
  "use strict";

  var messagesEl = document.getElementById("aichatMessages");
  var form = document.getElementById("aichatForm");
  var input = document.getElementById("aichatInput");
  if (!messagesEl || !form || !input) return;

  // Keep notification connection alive
  if (window.__skriftNotifications) {
    window.__skriftNotifications.configure({ persistConnection: true });
  }

  // ---------------------------------------------------------------------------
  // Markdown rendering
  // ---------------------------------------------------------------------------

  var markedAvailable = typeof marked !== "undefined";
  if (markedAvailable) {
    marked.setOptions({ breaks: true, gfm: true });
  }

  function renderMarkdown(text) {
    if (!markedAvailable) return text;
    return marked.parse(text);
  }

  // Render existing messages as markdown on load
  var existingContents = messagesEl.querySelectorAll(".aichat-msg-content[data-raw]");
  for (var i = 0; i < existingContents.length; i++) {
    var raw = existingContents[i].getAttribute("data-raw");
    existingContents[i].innerHTML = renderMarkdown(raw);
  }

  // Scroll to bottom on load
  window.scrollTo(0, document.body.scrollHeight);

  // ---------------------------------------------------------------------------
  // Auto-resize textarea
  // ---------------------------------------------------------------------------

  input.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 120) + "px";
  });

  // ---------------------------------------------------------------------------
  // Send message
  // ---------------------------------------------------------------------------

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var content = input.value.trim();
    if (!content) return;

    var btn = form.querySelector(".aichat-send");
    btn.disabled = true;
    input.value = "";
    input.style.height = "auto";

    var csrfToken = form.getAttribute("data-csrf") || "";
    fetch("/send", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ content: content }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Send failed");
        return res.json();
      })
      .catch(function (err) {
        console.error("Send error:", err);
      })
      .finally(function () {
        btn.disabled = false;
        input.focus();
      });
  });

  // Submit on Enter (Shift+Enter for newline)
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.dispatchEvent(new Event("submit"));
    }
  });

  // ---------------------------------------------------------------------------
  // Real-time notifications
  // ---------------------------------------------------------------------------

  function appendMessage(sender, content, messageId) {
    var div = document.createElement("div");
    div.className = "aichat-msg aichat-msg-" + sender;
    if (messageId) div.setAttribute("data-message-id", messageId);

    var senderEl = document.createElement("div");
    senderEl.className = "aichat-msg-sender";
    senderEl.textContent = sender.toUpperCase();
    div.appendChild(senderEl);

    var contentEl = document.createElement("div");
    contentEl.className = "aichat-msg-content";
    contentEl.innerHTML = renderMarkdown(content);
    div.appendChild(contentEl);

    if (sender === "user") {
      var readEl = document.createElement("div");
      readEl.className = "aichat-msg-read";
      readEl.textContent = "\u2713";
      div.appendChild(readEl);
    }

    messagesEl.appendChild(div);
    window.scrollTo(0, document.body.scrollHeight);
  }

  function markAsRead(messageIds) {
    for (var i = 0; i < messageIds.length; i++) {
      var el = messagesEl.querySelector(
        '[data-message-id="' + messageIds[i] + '"] .aichat-msg-read'
      );
      if (el) el.classList.add("is-read");
    }
  }

  // ---------------------------------------------------------------------------
  // Tool use indicator
  // ---------------------------------------------------------------------------

  var toolIndicator = document.createElement("div");
  toolIndicator.className = "aichat-tool-indicator";
  toolIndicator.innerHTML =
    '<span class="aichat-tool-pulse"></span>' +
    '<span class="aichat-tool-text"></span>' +
    '<span class="aichat-tool-timer"></span>';
  form.parentNode.insertBefore(toolIndicator, form);

  var toolTextEl = toolIndicator.querySelector(".aichat-tool-text");
  var toolTimerEl = toolIndicator.querySelector(".aichat-tool-timer");
  var toolStartTime = 0;
  var toolTimerInterval = null;
  var toolHideTimeout = null;

  function showToolIndicator(description) {
    if (toolHideTimeout) {
      clearTimeout(toolHideTimeout);
      toolHideTimeout = null;
    }
    toolIndicator.classList.add("is-active");
    toolTextEl.textContent = description;
    toolTimerEl.textContent = "";
    toolStartTime = Date.now();

    if (toolTimerInterval) clearInterval(toolTimerInterval);
    toolTimerInterval = setInterval(function () {
      var elapsed = Math.floor((Date.now() - toolStartTime) / 1000);
      if (elapsed >= 30) {
        toolTimerEl.textContent = elapsed + "s";
      }
    }, 1000);
  }

  function hideToolIndicator() {
    if (toolTimerInterval) {
      clearInterval(toolTimerInterval);
      toolTimerInterval = null;
    }
    // Brief delay before hiding so it doesn't flicker between tools
    toolHideTimeout = setTimeout(function () {
      toolIndicator.classList.remove("is-active");
      toolTextEl.textContent = "";
      toolTimerEl.textContent = "";
    }, 500);
  }

  // ---------------------------------------------------------------------------
  // Notification handler
  // ---------------------------------------------------------------------------

  document.addEventListener("sk:notification", function (e) {
    var d = e.detail;
    if (!d) return;

    if (d.type === "aichat:message") {
      appendMessage(d.sender, d.content, d.message_id);
    } else if (d.type === "aichat:read") {
      markAsRead(d.message_ids || []);
    } else if (d.type === "aichat:tool") {
      if (d.status === "active") {
        showToolIndicator(d.description || "Working...");
      } else {
        hideToolIndicator();
      }
    }
  });
})();
