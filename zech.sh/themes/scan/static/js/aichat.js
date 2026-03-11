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

  function isNearBottom(threshold) {
    return (document.body.scrollHeight - window.innerHeight - window.scrollY) <= (threshold || 50);
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
      window.scrollTo({ top: document.body.scrollHeight, behavior: "instant" });
    }
  } else {
    window.scrollTo({ top: document.body.scrollHeight, behavior: "instant" });
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

  function prependMessages(messages) {
    var scrollBottom = document.body.scrollHeight - window.scrollY;
    // Find the first existing message or working block to insert before
    var refNode = messagesEl.querySelector(".aichat-msg, .aichat-event-divider");

    for (var i = 0; i < messages.length; i++) {
      var m = messages[i];

      if (m.sender === "event") {
        var divider = createEventDivider(m.content, m.id);
        messagesEl.insertBefore(divider, refNode);
        continue;
      }

      if (m.sender === "tools") {
        // Tool messages go to the side panel, not chat flow
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
  // Planning/coding mode toggle
  // ---------------------------------------------------------------------------

  var modeToggle = document.getElementById("aichatModeToggle");
  var isPlanning = false;      // actual server state
  var wantsPlanning = false;   // local toggle state (applied on send)

  // Derive initial state from last event divider in history
  (function () {
    var dividers = messagesEl.querySelectorAll(".aichat-event-divider .aichat-event-label");
    if (dividers.length) {
      var last = dividers[dividers.length - 1].textContent.trim().toLowerCase();
      if (last === "planning") {
        isPlanning = true;
        wantsPlanning = true;
      }
    }
    if (isPlanning && modeToggle) modeToggle.classList.add("is-planning");
  })();

  if (modeToggle) {
    modeToggle.addEventListener("click", function () {
      wantsPlanning = !wantsPlanning;
      modeToggle.classList.toggle("is-planning", wantsPlanning);
    });
  }

  function syncPlanMode(csrfToken) {
    // Fire plan event if local toggle differs from server state
    if (wantsPlanning === isPlanning) return Promise.resolve();
    var eventType = wantsPlanning ? "plan:enter" : "plan:exit";
    return fetch("/c/" + channelId + "/event", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ event_type: eventType }),
    }).then(function (res) {
      if (!res.ok) throw new Error("Event failed");
      isPlanning = wantsPlanning;
    });
  }

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

    // Clear previews and collapse expanded tool panel
    pendingAttachments = [];
    previewArea.innerHTML = "";
    collapseToolPanelToDefault();

    // Sync plan mode before sending the message
    syncPlanMode(csrfToken)
      .catch(function (err) {
        console.error("Plan mode sync error:", err);
      })
      .then(function () {
        return fetch("/c/" + channelId + "/send", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
          },
          body: JSON.stringify(payload),
        });
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
    if (isNearBottom()) {
      window.scrollTo({ top: document.body.scrollHeight, behavior: "instant" });
    }
  }

  function eventLabel(eventType) {
    if (eventType === "plan:enter") return "Planning";
    if (eventType === "plan:exit") return "Done Planning";
    return eventType;
  }

  function createEventDivider(eventType, messageId) {
    var div = document.createElement("div");
    div.className = "aichat-event-divider";
    if (messageId) div.setAttribute("data-message-id", messageId);
    var span = document.createElement("span");
    span.className = "aichat-event-label";
    span.textContent = eventLabel(eventType);
    div.appendChild(span);
    return div;
  }

  function appendEventDivider(eventType, messageId) {
    var div = createEventDivider(eventType, messageId);
    messagesEl.appendChild(div);
    if (isNearBottom()) {
      window.scrollTo({ top: document.body.scrollHeight, behavior: "instant" });
    }
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
  // Tool console (side panel on desktop, inline on mobile)
  // ---------------------------------------------------------------------------

  var toolPanel = document.getElementById("aichatToolPanel");
  var toolPanelContent = document.getElementById("aichatToolPanelContent");
  var toolPanelPulse = document.getElementById("aichatToolPanelPulse");
  var toolPanelLabel = document.getElementById("aichatToolPanelLabel");
  var toolPanelToggle = document.getElementById("aichatToolPanelToggle");
  var toolIsActive = false;
  var isMobileLayout = false;

  // Move tool panel into/out of .aichat-bottom based on viewport width
  var bottomEl = document.getElementById("aichatBottom");
  var layoutEl = toolPanel ? toolPanel.parentElement : null;
  var mobileQuery = window.matchMedia("(max-width: 600px)");

  function positionToolPanel(mq) {
    if (!toolPanel || !bottomEl || !layoutEl) return;
    isMobileLayout = mq.matches;
    if (mq.matches) {
      // Mobile: insert before the form inside .aichat-bottom
      bottomEl.insertBefore(toolPanel, bottomEl.firstChild);
    } else {
      // Desktop/tablet: append to layout container
      layoutEl.appendChild(toolPanel);
      // Reset to default state when leaving mobile
      toolPanel.className = "aichat-tool-panel is-default";
    }
  }

  positionToolPanel(mobileQuery);
  mobileQuery.addEventListener("change", positionToolPanel);

  // Mobile: 3-state toggle (default → collapsed → expanded → default)
  if (toolPanelToggle) {
    toolPanelToggle.addEventListener("click", function () {
      if (!isMobileLayout || !toolPanel) return;
      if (toolPanel.classList.contains("is-default")) {
        toolPanel.classList.remove("is-default");
        toolPanel.classList.add("is-collapsed");
      } else if (toolPanel.classList.contains("is-collapsed")) {
        toolPanel.classList.remove("is-collapsed");
        toolPanel.classList.add("is-expanded");
      } else {
        toolPanel.classList.remove("is-expanded");
        toolPanel.classList.add("is-default");
      }
    });
  }

  function collapseToolPanelToDefault() {
    if (!isMobileLayout || !toolPanel) return;
    if (toolPanel.classList.contains("is-expanded")) {
      toolPanel.classList.remove("is-expanded");
      toolPanel.classList.add("is-default");
    }
  }

  function renderDiff(description) {
    var lines = description.split("\n");
    var header = lines[0].substring(5); // strip "diff:" prefix
    var container = document.createElement("div");
    container.className = "aichat-tool-diff";

    var headerEl = document.createElement("div");
    headerEl.className = "aichat-tool-diff-header";
    headerEl.textContent = header;
    container.appendChild(headerEl);

    for (var i = 1; i < lines.length; i++) {
      var line = lines[i];
      var lineEl = document.createElement("div");
      if (line.charAt(0) === "-") {
        lineEl.className = "aichat-tool-diff-del";
      } else if (line.charAt(0) === "+") {
        lineEl.className = "aichat-tool-diff-add";
      } else {
        lineEl.className = "aichat-tool-diff-ctx";
      }
      lineEl.textContent = line;
      container.appendChild(lineEl);
    }
    return container;
  }

  function addToolToPanel(description) {
    if (!toolPanelContent) return;

    // Deduplicate rapid updates
    var lastItem = toolPanelContent.lastElementChild;
    if (lastItem && lastItem.textContent === description) return;

    var item = document.createElement("div");
    item.className = "aichat-tool-panel-item";

    // Render edit diffs with syntax highlighting
    if (description.indexOf("diff:") === 0) {
      item.appendChild(renderDiff(description));
    } else {
      item.textContent = description;
    }

    toolPanelContent.appendChild(item);
    toolPanelContent.scrollTop = toolPanelContent.scrollHeight;

    // Show active state
    if (!toolIsActive) {
      toolIsActive = true;
      if (toolPanelPulse) toolPanelPulse.classList.remove("is-done");
      if (toolPanelLabel) toolPanelLabel.textContent = "WORKING...";
    }
  }

  function finalizeToolPanel() {
    if (!toolIsActive) return;
    toolIsActive = false;
    if (toolPanelPulse) toolPanelPulse.classList.add("is-done");
    if (toolPanelLabel) toolPanelLabel.textContent = "TOOLS";
  }

  // ---------------------------------------------------------------------------
  // Interaction overlay (plans + questions)
  // ---------------------------------------------------------------------------

  var interactionOverlay = document.getElementById("aichatInteractionOverlay");
  var interactionLabel = document.getElementById("aichatInteractionLabel");
  var interactionContent = document.getElementById("aichatInteractionContent");
  var interactionOptions = document.getElementById("aichatInteractionOptions");
  var interactionInput = document.getElementById("aichatInteractionInput");
  var interactionAcceptBtn = document.getElementById("aichatInteractionAccept");
  var interactionDenyBtn = document.getElementById("aichatInteractionDeny");
  var pendingInteractionId = null;

  function showInteraction(data) {
    if (!interactionOverlay) return;

    pendingInteractionId = data.interaction_id;
    var isQuestion = data.interaction_type === "question";
    var options = data.options || [];

    interactionLabel.textContent = isQuestion ? "QUESTION" : "PLAN";
    interactionContent.innerHTML = renderMarkdown(data.content || "");
    interactionAcceptBtn.textContent = isQuestion ? "ANSWER" : "APPROVE";

    // Render options if provided
    interactionOptions.innerHTML = "";
    if (isQuestion && options.length > 0) {
      interactionOverlay.classList.add("has-options");
      options.forEach(function (opt) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "aichat-interaction-option";
        var label = document.createElement("span");
        label.className = "aichat-interaction-option-label";
        label.textContent = opt.label || opt;
        btn.appendChild(label);
        if (opt.description) {
          var desc = document.createElement("span");
          desc.className = "aichat-interaction-option-desc";
          desc.textContent = opt.description;
          btn.appendChild(desc);
        }
        btn.addEventListener("click", function () {
          interactionInput.value = opt.label || opt;
          sendInteractionResponse("accept");
        });
        interactionOptions.appendChild(btn);
      });
    } else {
      interactionOverlay.classList.remove("has-options");
    }

    if (isQuestion) {
      interactionOverlay.classList.add("is-question");
      interactionInput.value = "";
    } else {
      interactionOverlay.classList.remove("is-question");
    }

    interactionAcceptBtn.disabled = false;
    interactionDenyBtn.disabled = false;
    interactionOverlay.classList.add("is-active");

    if (isQuestion && options.length === 0) {
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
      if (d.sender === "event") {
        appendEventDivider(d.content, d.message_id);
        if (d.content === "plan:enter") { isPlanning = true; wantsPlanning = true; }
        else if (d.content === "plan:exit") { isPlanning = false; wantsPlanning = false; }
        if (modeToggle) modeToggle.classList.toggle("is-planning", wantsPlanning);
      } else {
      if (d.sender === "claude") {
        finalizeToolPanel();
      }
      appendMessage(d.sender, d.content, d.message_id, d.attachments);
      }
    } else if (d.type === "aichat:read") {
      markAsRead(d.message_ids || []);
    } else if (d.type === "aichat:tool") {
      if (d.status === "active") {
        addToolToPanel(d.description || "Working...");
      } else if (d.status === "idle") {
        finalizeToolPanel();
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
