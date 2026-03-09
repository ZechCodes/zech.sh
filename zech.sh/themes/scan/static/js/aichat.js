// Service worker registration
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
}

// AI.CHAT — Real-time chat client with SSE notifications
(function () {
  "use strict";

  var messagesEl = document.getElementById("aichatMessages");
  var form = document.getElementById("aichatForm");
  var input = document.getElementById("aichatInput");
  if (!messagesEl || !form || !input) return;

  var channelId = form.getAttribute("data-channel-id") || "";

  // Keep SSE alive for reliable real-time; push always sent separately
  if (window.__skriftNotifications) {
    window.__skriftNotifications.configure({
      persistConnection: true,
      statusIndicator: {
        enabled: true,
        labels: { connected: "", suspended: "", connecting: "", disconnected: "" },
      },
    });
  }

  // Suppress push notifications when this chat is visible
  if (window.__skriftPush) {
    window.__skriftPush.onFilter(function (payload) {
      if (payload.tag && payload.tag.indexOf(channelId) !== -1) {
        return { cancel: true };
      }
      return payload;
    });
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

  // Scroll to linked message (from notification click) or bottom
  var linkedMsgId = window.location.hash.match(/^#msg-(.+)$/);
  if (linkedMsgId) {
    var linkedEl = messagesEl.querySelector('[data-message-id="' + linkedMsgId[1] + '"]');
    if (linkedEl) {
      linkedEl.scrollIntoView({ block: "center" });
      linkedEl.classList.add("aichat-msg-highlight");
      setTimeout(function () {
        linkedEl.classList.remove("aichat-msg-highlight");
      }, 5000);
    } else {
      window.scrollTo(0, document.body.scrollHeight);
    }
  } else {
    window.scrollTo(0, document.body.scrollHeight);
  }

  // ---------------------------------------------------------------------------
  // Load older messages
  // ---------------------------------------------------------------------------

  var loadMoreBtn = document.getElementById("aichatLoadMore");

  function prependMessages(messages) {
    var scrollBottom = document.body.scrollHeight - window.scrollY;
    // Find the first existing message to insert before
    var refNode = messagesEl.querySelector(".aichat-msg");

    for (var i = 0; i < messages.length; i++) {
      var m = messages[i];
      var div = document.createElement("div");
      div.className = "aichat-msg aichat-msg-" + m.sender;
      div.setAttribute("data-message-id", m.id);

      var senderEl = document.createElement("div");
      senderEl.className = "aichat-msg-sender";
      senderEl.textContent = m.sender.toUpperCase();
      div.appendChild(senderEl);

      var contentEl = document.createElement("div");
      contentEl.className = "aichat-msg-content";
      contentEl.innerHTML = renderMarkdown(m.content);
      div.appendChild(contentEl);

      if (m.sender === "user") {
        var readEl = document.createElement("div");
        readEl.className = "aichat-msg-read" + (m.read_by_claude_at ? " is-read" : "");
        readEl.textContent = "\u2713";
        div.appendChild(readEl);
      }

      // Insert before the first existing message (after load-more button)
      messagesEl.insertBefore(div, refNode);
    }

    // Preserve scroll position
    window.scrollTo(0, document.body.scrollHeight - scrollBottom);
  }

  if (loadMoreBtn) {
    loadMoreBtn.addEventListener("click", function () {
      var firstMsgEl = messagesEl.querySelector(".aichat-msg");
      if (!firstMsgEl) return;
      var beforeId = firstMsgEl.getAttribute("data-message-id");
      if (!beforeId) return;

      loadMoreBtn.disabled = true;
      loadMoreBtn.textContent = "Loading...";

      fetch("/c/" + channelId + "/messages?before=" + encodeURIComponent(beforeId))
        .then(function (res) {
          if (!res.ok) throw new Error("Load failed");
          return res.json();
        })
        .then(function (data) {
          prependMessages(data.messages);
          if (!data.has_more) {
            loadMoreBtn.remove();
            loadMoreBtn = null;
          } else {
            loadMoreBtn.disabled = false;
            loadMoreBtn.textContent = "Load older messages";
          }
        })
        .catch(function (err) {
          console.error("Load older messages error:", err);
          loadMoreBtn.disabled = false;
          loadMoreBtn.textContent = "Load older messages";
        });
    });
  }

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
    fetch("/c/" + channelId + "/send", {
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
  var bottomEl = document.getElementById("aichatBottom");
  if (bottomEl) {
    bottomEl.insertBefore(toolIndicator, form);
  }

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

    // Filter by channel if set
    if (channelId && d.channel_id && d.channel_id !== channelId) return;

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

  // ---------------------------------------------------------------------------
  // Channel settings modal
  // ---------------------------------------------------------------------------

  var editBtn = document.getElementById("aichatEditBtn");
  var modal = document.getElementById("aichatModal");
  var renameInput = document.getElementById("aichatRenameInput");
  var saveBtn = document.getElementById("aichatSaveBtn");
  var regenBtn = document.getElementById("aichatRegenBtn");
  var cancelBtn = document.getElementById("aichatCancelBtn");
  var tokenDisplay = document.getElementById("aichatNewToken");
  var tokenValue = document.getElementById("aichatTokenValue");
  var logoEl = document.querySelector(".logo");
  var channelNameEl = document.getElementById("channelName") || logoEl;

  if (editBtn && modal) {
    editBtn.addEventListener("click", function () {
      modal.classList.add("is-active");
      tokenDisplay.classList.add("is-hidden");
      renameInput.focus();
    });

    cancelBtn.addEventListener("click", function () {
      modal.classList.remove("is-active");
    });

    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.classList.remove("is-active");
    });

    var csrfToken = form.getAttribute("data-csrf") || "";

    saveBtn.addEventListener("click", function () {
      var newName = renameInput.value.trim();
      if (!newName) return;
      saveBtn.disabled = true;

      fetch("/channels/" + channelId + "/update", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ name: newName }),
      })
        .then(function (res) {
          if (!res.ok) throw new Error("Update failed");
          return res.json();
        })
        .then(function (data) {
          var brandText = "< " + data.channel.name;
          if (logoEl) {
            logoEl.textContent = brandText;
            logoEl.setAttribute("data-text", brandText);
          }
          document.title = data.channel.name + " — AI.CHAT";
          modal.classList.remove("is-active");
        })
        .catch(function (err) {
          console.error("Rename error:", err);
        })
        .finally(function () {
          saveBtn.disabled = false;
        });
    });

    regenBtn.addEventListener("click", function () {
      if (!confirm("Regenerate key pair? The old token will stop working.")) return;
      regenBtn.disabled = true;

      fetch("/channels/" + channelId + "/update", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ name: renameInput.value.trim(), regenerate_key: true }),
      })
        .then(function (res) {
          if (!res.ok) throw new Error("Regenerate failed");
          return res.json();
        })
        .then(function (data) {
          if (data.channel) {
            var brandText = "< " + data.channel.name;
            if (logoEl) {
              logoEl.textContent = brandText;
              logoEl.setAttribute("data-text", brandText);
            }
            document.title = data.channel.name + " — AI.CHAT";
          }
          if (data.token) {
            tokenValue.textContent = data.token;
            tokenDisplay.classList.remove("is-hidden");
          }
        })
        .catch(function (err) {
          console.error("Regenerate error:", err);
        })
        .finally(function () {
          regenBtn.disabled = false;
        });
    });
  }
})();
