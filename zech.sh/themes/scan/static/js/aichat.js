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

  // Bind toggle for server-rendered working blocks
  var existingWorkingBlocks = messagesEl.querySelectorAll(".aichat-working");
  for (var i = 0; i < existingWorkingBlocks.length; i++) {
    (function (block) {
      var toggle = block.querySelector(".aichat-working-toggle");
      if (toggle) {
        toggle.addEventListener("click", function () {
          block.classList.toggle("is-expanded");
        });
      }
    })(existingWorkingBlocks[i]);
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
  // Attachment image rendering helper
  // ---------------------------------------------------------------------------

  function createImageElements(attachments) {
    if (!attachments || !attachments.length) return null;
    var container = document.createElement("div");
    container.className = "aichat-msg-images";
    for (var j = 0; j < attachments.length; j++) {
      var att = attachments[j];
      if (att.content_type && att.content_type.indexOf("image/") === 0) {
        var img = document.createElement("img");
        img.className = "aichat-msg-img";
        img.src = att.url;
        img.alt = att.filename || "image";
        img.loading = "lazy";
        img.addEventListener("click", (function (url) {
          return function () { window.open(url, "_blank"); };
        })(att.url));
        container.appendChild(img);
      }
    }
    return container.children.length ? container : null;
  }

  // ---------------------------------------------------------------------------
  // Load older messages
  // ---------------------------------------------------------------------------

  var loadMoreBtn = document.getElementById("aichatLoadMore");

  function createWorkingBlock(content, messageId) {
    var block = document.createElement("div");
    block.className = "aichat-working";
    if (messageId) block.setAttribute("data-message-id", messageId);

    var toggle = document.createElement("div");
    toggle.className = "aichat-working-toggle";
    toggle.innerHTML =
      '<span class="aichat-tool-pulse is-done"></span>' +
      '<span class="aichat-working-label">Tools Used</span>' +
      '<span class="aichat-working-chevron"></span>';
    block.appendChild(toggle);

    var contentEl = document.createElement("div");
    contentEl.className = "aichat-working-content";
    var lines = content.split("\n");
    for (var j = 0; j < lines.length; j++) {
      if (lines[j].trim()) {
        var item = document.createElement("div");
        item.className = "aichat-working-item";
        item.textContent = lines[j];
        contentEl.appendChild(item);
      }
    }
    block.appendChild(contentEl);

    toggle.addEventListener("click", function () {
      block.classList.toggle("is-expanded");
    });

    return block;
  }

  function prependMessages(messages) {
    var scrollBottom = document.body.scrollHeight - window.scrollY;
    // Find the first existing message or working block to insert before
    var refNode = messagesEl.querySelector(".aichat-msg, .aichat-working");

    for (var i = 0; i < messages.length; i++) {
      var m = messages[i];

      if (m.sender === "tools") {
        var block = createWorkingBlock(m.content, m.id);
        messagesEl.insertBefore(block, refNode);
        continue;
      }

      var div = document.createElement("div");
      div.className = "aichat-msg aichat-msg-" + m.sender;
      div.setAttribute("data-message-id", m.id);

      var senderEl = document.createElement("div");
      senderEl.className = "aichat-msg-sender";
      senderEl.textContent = m.sender.toUpperCase();
      div.appendChild(senderEl);

      var imgContainer = createImageElements(m.attachments);
      if (imgContainer) div.appendChild(imgContainer);

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
  // File attachment handling
  // ---------------------------------------------------------------------------

  var attachBtn = document.getElementById("aichatAttachBtn");
  var fileInput = document.getElementById("aichatFileInput");
  var previewArea = document.getElementById("aichatPreviewArea");
  var pendingAttachments = [];

  if (attachBtn && fileInput) {
    attachBtn.addEventListener("click", function () {
      fileInput.click();
    });

    fileInput.addEventListener("change", function () {
      var files = fileInput.files;
      if (!files || !files.length) return;
      for (var i = 0; i < files.length; i++) {
        uploadFile(files[i]);
      }
      fileInput.value = "";
    });
  }

  function uploadFile(file) {
    if (!file.type.startsWith("image/")) return;
    if (file.size > 10 * 1024 * 1024) {
      console.error("File too large:", file.name);
      return;
    }

    var csrfToken = form.getAttribute("data-csrf") || "";
    var formData = new FormData();
    formData.append("file", file);

    // Show uploading preview
    var thumb = document.createElement("div");
    thumb.className = "aichat-preview-thumb is-uploading";
    var thumbImg = document.createElement("img");
    thumbImg.src = URL.createObjectURL(file);
    thumb.appendChild(thumbImg);
    previewArea.appendChild(thumb);

    fetch("/c/" + channelId + "/upload", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      body: formData,
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Upload failed");
        return res.json();
      })
      .then(function (data) {
        thumb.classList.remove("is-uploading");
        var att = {
          asset_id: data.asset_id,
          filename: data.filename,
          content_type: data.content_type,
          url: data.url,
        };
        pendingAttachments.push(att);
        thumb.setAttribute("data-asset-id", data.asset_id);

        var removeBtn = document.createElement("button");
        removeBtn.className = "aichat-preview-remove";
        removeBtn.textContent = "\u00d7";
        removeBtn.addEventListener("click", function () {
          pendingAttachments = pendingAttachments.filter(function (a) {
            return a.asset_id !== data.asset_id;
          });
          thumb.remove();
        });
        thumb.appendChild(removeBtn);
      })
      .catch(function (err) {
        console.error("Upload error:", err);
        thumb.remove();
      });
  }

  // ---------------------------------------------------------------------------
  // Send message
  // ---------------------------------------------------------------------------

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var content = input.value.trim();
    if (!content && !pendingAttachments.length) return;

    var btn = form.querySelector(".aichat-send");
    btn.disabled = true;
    input.value = "";
    input.style.height = "auto";

    var csrfToken = form.getAttribute("data-csrf") || "";
    var payload = { content: content };
    if (pendingAttachments.length) {
      payload.attachments = pendingAttachments;
    }

    // Clear previews
    pendingAttachments = [];
    previewArea.innerHTML = "";

    fetch("/c/" + channelId + "/send", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify(payload),
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

  // Submit on Cmd/Ctrl+Enter
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      form.dispatchEvent(new Event("submit"));
    }
  });

  // ---------------------------------------------------------------------------
  // Real-time notifications
  // ---------------------------------------------------------------------------

  function appendMessage(sender, content, messageId, attachments) {
    var div = document.createElement("div");
    div.className = "aichat-msg aichat-msg-" + sender;
    if (messageId) div.setAttribute("data-message-id", messageId);

    var senderEl = document.createElement("div");
    senderEl.className = "aichat-msg-sender";
    senderEl.textContent = sender.toUpperCase();
    div.appendChild(senderEl);

    var imgContainer = createImageElements(attachments);
    if (imgContainer) div.appendChild(imgContainer);

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
  // Working block (collapsible tool use log)
  // ---------------------------------------------------------------------------

  var activeWorkingEl = null;

  function getOrCreateWorkingBlock() {
    // Reuse the active working block if it exists and is the last child
    if (activeWorkingEl && activeWorkingEl === messagesEl.lastElementChild) {
      return activeWorkingEl;
    }

    // Create a new working block
    var block = document.createElement("div");
    block.className = "aichat-working is-expanded";

    var toggle = document.createElement("div");
    toggle.className = "aichat-working-toggle";
    toggle.innerHTML =
      '<span class="aichat-tool-pulse"></span>' +
      '<span class="aichat-working-label">Working...</span>' +
      '<span class="aichat-working-chevron"></span>';
    block.appendChild(toggle);

    var contentEl = document.createElement("div");
    contentEl.className = "aichat-working-content";
    block.appendChild(contentEl);

    toggle.addEventListener("click", function () {
      block.classList.toggle("is-expanded");
    });

    messagesEl.appendChild(block);
    activeWorkingEl = block;
    window.scrollTo(0, document.body.scrollHeight);
    return block;
  }

  function addToolToWorkingBlock(description) {
    var block = getOrCreateWorkingBlock();
    var contentEl = block.querySelector(".aichat-working-content");

    // Check if this tool is already the last entry (avoid duplicates from rapid updates)
    var items = contentEl.querySelectorAll(".aichat-working-item");
    var lastItem = items.length ? items[items.length - 1] : null;
    if (lastItem && lastItem.textContent === description) return;

    var item = document.createElement("div");
    item.className = "aichat-working-item";
    item.textContent = description;
    contentEl.appendChild(item);

    window.scrollTo(0, document.body.scrollHeight);
  }

  function finalizeWorkingBlock() {
    if (!activeWorkingEl) return;

    // Change label from "Working..." to "Tools Used"
    var label = activeWorkingEl.querySelector(".aichat-working-label");
    if (label) label.textContent = "Tools Used";

    // Stop the pulse animation
    var pulse = activeWorkingEl.querySelector(".aichat-tool-pulse");
    if (pulse) pulse.classList.add("is-done");

    // Collapse it
    activeWorkingEl.classList.remove("is-expanded");

    activeWorkingEl = null;
  }

  // ---------------------------------------------------------------------------
  // Interaction overlay (plans + questions)
  // ---------------------------------------------------------------------------

  var interactionOverlay = document.getElementById("aichatInteractionOverlay");
  var interactionLabel = document.getElementById("aichatInteractionLabel");
  var interactionContent = document.getElementById("aichatInteractionContent");
  var interactionInput = document.getElementById("aichatInteractionInput");
  var interactionAcceptBtn = document.getElementById("aichatInteractionAccept");
  var interactionDenyBtn = document.getElementById("aichatInteractionDeny");
  var pendingInteractionId = null;

  function showInteraction(data) {
    if (!interactionOverlay) return;

    pendingInteractionId = data.interaction_id;
    var isQuestion = data.interaction_type === "question";

    interactionLabel.textContent = isQuestion ? "QUESTION" : "PLAN";
    interactionContent.innerHTML = renderMarkdown(data.content || "");
    interactionAcceptBtn.textContent = isQuestion ? "ANSWER" : "APPROVE";

    if (isQuestion) {
      interactionOverlay.classList.add("is-question");
      interactionInput.value = "";
    } else {
      interactionOverlay.classList.remove("is-question");
    }

    interactionAcceptBtn.disabled = false;
    interactionDenyBtn.disabled = false;
    interactionOverlay.classList.add("is-active");

    if (isQuestion) {
      interactionInput.focus();
    }
  }

  function hideInteraction() {
    if (!interactionOverlay) return;
    interactionOverlay.classList.remove("is-active");
    pendingInteractionId = null;
  }

  function sendInteractionResponse(action) {
    if (!pendingInteractionId) return;

    var csrfToken = form.getAttribute("data-csrf") || "";
    var answer = interactionInput ? interactionInput.value.trim() : "";

    interactionAcceptBtn.disabled = true;
    interactionDenyBtn.disabled = true;

    fetch("/c/" + channelId + "/interaction/" + pendingInteractionId + "/respond", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ action: action, answer: answer }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error("Response failed");
        hideInteraction();
      })
      .catch(function (err) {
        console.error("Interaction response error:", err);
        interactionAcceptBtn.disabled = false;
        interactionDenyBtn.disabled = false;
      });
  }

  if (interactionAcceptBtn) {
    interactionAcceptBtn.addEventListener("click", function () {
      sendInteractionResponse("accept");
    });
  }

  if (interactionDenyBtn) {
    interactionDenyBtn.addEventListener("click", function () {
      sendInteractionResponse("deny");
    });
  }

  // Submit answer on Cmd/Ctrl+Enter in the interaction input
  if (interactionInput) {
    interactionInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        sendInteractionResponse("accept");
      }
    });
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
      if (d.sender === "claude") {
        finalizeWorkingBlock();
      }
      appendMessage(d.sender, d.content, d.message_id, d.attachments);
    } else if (d.type === "aichat:read") {
      markAsRead(d.message_ids || []);
    } else if (d.type === "aichat:tool") {
      if (d.status === "active") {
        addToolToWorkingBlock(d.description || "Working...");
      } else if (d.status === "idle") {
        finalizeWorkingBlock();
      }
    } else if (d.type === "aichat:interaction") {
      showInteraction(d);
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
