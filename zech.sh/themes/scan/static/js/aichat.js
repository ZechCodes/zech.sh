// Service worker registration
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});
}

// AI.CHAT — Real-time chat client with SSE notifications

// ---------------------------------------------------------------------------
// Global: channel ID from form (empty string when on home page)
// ---------------------------------------------------------------------------
var __aichatChannelId = (function () {
  var form = document.getElementById("aichatForm");
  return form ? (form.getAttribute("data-channel-id") || "") : "";
})();

// ---------------------------------------------------------------------------
// SSE: keep alive for reliable real-time
// ---------------------------------------------------------------------------
(function () {
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
  if (window.__skriftPush && __aichatChannelId) {
    window.__skriftPush.onFilter(function (payload) {
      if (payload.tag && payload.tag.indexOf(__aichatChannelId) !== -1) {
        return { cancel: true };
      }
      return payload;
    });
  }
})();

// ---------------------------------------------------------------------------
// Sidebar: devices & channels with realtime unread
// ---------------------------------------------------------------------------
(function () {
  var sidebar = document.getElementById("aichatSidebar");
  if (!sidebar) return;

  var channelId = __aichatChannelId;
  var sidebarToggle = document.getElementById("aichatSidebarToggle");
  var sidebarBackdrop = document.getElementById("aichatSidebarBackdrop");
  var sidebarBadge = document.getElementById("aichatSidebarBadge");
  var totalUnread = 0;

  function updateToggleBadge() {
    if (!sidebarBadge) return;
    if (totalUnread > 0) {
      sidebarBadge.textContent = totalUnread;
      sidebarBadge.classList.remove("is-hidden");
    } else {
      sidebarBadge.classList.add("is-hidden");
    }
  }

  function openSidebar() {
    sidebar.classList.add("is-open");
    if (sidebarBackdrop) sidebarBackdrop.classList.add("is-active");
  }

  function closeSidebar() {
    sidebar.classList.remove("is-open");
    if (sidebarBackdrop) sidebarBackdrop.classList.remove("is-active");
  }

  if (sidebarToggle) {
    sidebarToggle.addEventListener("click", function () {
      if (sidebar.classList.contains("is-open")) {
        closeSidebar();
      } else {
        openSidebar();
      }
    });
  }

  if (sidebarBackdrop) {
    sidebarBackdrop.addEventListener("click", closeSidebar);
  }

  fetch("/api/sidebar")
    .then(function (res) {
      if (!res.ok) throw new Error("Sidebar fetch failed");
      return res.json();
    })
    .then(function (data) {
      renderSidebar(data);
      // Initialize total unread from all other channels
      var counts = data.unread_counts || {};
      totalUnread = 0;
      for (var cid in counts) {
        if (cid !== channelId) totalUnread += counts[cid];
      }
      updateToggleBadge();
      // Delay enabling tool pulse to skip SSE replay of old events
      setTimeout(function () { toolPulseReady = true; }, 2000);
    })
    .catch(function (err) {
      console.error("Sidebar error:", err);
    });

  function renderSidebar(data) {
    sidebar.innerHTML = "";

    // Header with link back to home
    var header = document.createElement("div");
    header.className = "aichat-sidebar-header";
    var headerLink = document.createElement("a");
    headerLink.href = "/";
    headerLink.textContent = "AI.CHAT";
    header.appendChild(headerLink);
    sidebar.appendChild(header);

    var devicesContainer = document.createElement("div");
    devicesContainer.className = "aichat-sidebar-devices";

    var devices = data.devices || [];
    for (var i = 0; i < devices.length; i++) {
      var device = devices[i];
      var deviceEl = document.createElement("div");
      deviceEl.className = "aichat-sidebar-device";

      // Device header
      var deviceHeader = document.createElement("div");
      deviceHeader.className = "aichat-sidebar-device-header";

      var dot = document.createElement("span");
      dot.className = "aichat-sidebar-device-dot" + (device.status === "online" ? " is-online" : "");
      deviceHeader.appendChild(dot);

      var name = document.createElement("span");
      name.className = "aichat-sidebar-device-name";
      name.textContent = device.name;
      deviceHeader.appendChild(name);

      deviceEl.appendChild(deviceHeader);

      // Channels
      var channels = (data.device_channels || {})[device.id] || [];
      if (channels.length) {
        var channelsEl = document.createElement("div");
        channelsEl.className = "aichat-sidebar-channels";

        for (var j = 0; j < channels.length; j++) {
          var ch = channels[j];
          var link = document.createElement("a");
          link.className = "aichat-sidebar-channel";
          link.href = "/c/" + ch.id;
          if (ch.id === channelId) {
            link.classList.add("is-active");
          }

          var chPulse = document.createElement("span");
          chPulse.className = "aichat-sidebar-channel-pulse";
          chPulse.setAttribute("data-sidebar-pulse", ch.id);
          link.appendChild(chPulse);

          var chName = document.createElement("span");
          chName.className = "aichat-sidebar-channel-name";
          chName.textContent = ch.name;
          link.appendChild(chName);

          var unreadCount = (data.unread_counts || {})[ch.id] || 0;
          // Don't show unread for current channel
          if (ch.id === channelId) unreadCount = 0;
          var badge = document.createElement("span");
          badge.className = "aichat-sidebar-unread" + (unreadCount === 0 ? " is-hidden" : "");
          badge.setAttribute("data-sidebar-unread", ch.id);
          badge.textContent = unreadCount;
          link.appendChild(badge);

          // Close sidebar on channel click (mobile)
          link.addEventListener("click", closeSidebar);

          channelsEl.appendChild(link);
        }

        deviceEl.appendChild(channelsEl);
      }

      devicesContainer.appendChild(deviceEl);
    }

    sidebar.appendChild(devicesContainer);
  }

  // Realtime updates for other channels
  var pulseTimers = {};
  var toolPulseReady = false;
  document.addEventListener("sk:notification", function (e) {
    var d = e.detail;
    if (!d || !d.channel_id) return;

    if (d.type === "aichat:message" && d.sender === "claude" && d.channel_id !== channelId) {
      var badge = document.querySelector('[data-sidebar-unread="' + d.channel_id + '"]');
      if (badge) {
        var count = parseInt(badge.textContent || "0", 10) + 1;
        badge.textContent = count;
        badge.classList.remove("is-hidden");
      }
      totalUnread++;
      updateToggleBadge();
    }

    if (d.type === "aichat:tool" && toolPulseReady) {
      var pulse = document.querySelector('[data-sidebar-pulse="' + d.channel_id + '"]');
      if (!pulse) return;
      if (pulseTimers[d.channel_id]) {
        clearTimeout(pulseTimers[d.channel_id]);
        pulseTimers[d.channel_id] = null;
      }
      if (d.status === "active") {
        pulse.classList.add("is-working");
      } else {
        // Delay hiding to avoid flicker between tool calls
        pulseTimers[d.channel_id] = setTimeout(function () {
          pulse.classList.remove("is-working");
        }, 2000);
      }
    }
  });
})();

// ---------------------------------------------------------------------------
// Chat: messages, form, tools (only when a channel is selected)
// ---------------------------------------------------------------------------
(function () {
  "use strict";

  var messagesEl = document.getElementById("aichatMessages");
  var form = document.getElementById("aichatForm");
  var input = document.getElementById("aichatInput");
  if (!messagesEl || !form || !input) return;

  var channelId = __aichatChannelId;
  var csrfToken = form.getAttribute("data-csrf") || "";

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

  // Scroll to linked message, new messages divider, or bottom
  var linkedMsgId = window.location.hash.match(/^#msg-(.+)$/);
  var newMessagesDivider = document.getElementById("aichatNewMessagesDivider");
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
  } else if (newMessagesDivider) {
    // Scroll so the new messages divider is near the top of the viewport
    var dividerTop = newMessagesDivider.getBoundingClientRect().top + window.scrollY;
    window.scrollTo({ top: dividerTop - 60, behavior: "instant" });
  } else {
    window.scrollTo({ top: document.body.scrollHeight, behavior: "instant" });
  }

  // ---------------------------------------------------------------------------
  // New messages tracking & scroll management
  // ---------------------------------------------------------------------------

  var newMsgCount = 0;
  var newMsgBtn = null;
  var newMsgDividerInserted = false;
  var pendingReadIds = [];
  var readFlushTimer = null;

  // Create floating "New Messages" button (centered on chat section)
  var sectionEl = messagesEl.closest(".aichat-section");
  (function () {
    newMsgBtn = document.createElement("button");
    newMsgBtn.className = "aichat-new-messages-btn is-hidden";
    newMsgBtn.type = "button";
    document.body.appendChild(newMsgBtn);

    function positionBtn() {
      if (!sectionEl || !newMsgBtn) return;
      var rect = sectionEl.getBoundingClientRect();
      var center = rect.left + rect.width / 2;
      var btnWidth = newMsgBtn.offsetWidth || 120;
      newMsgBtn.style.left = (center - btnWidth / 2) + "px";
    }

    newMsgBtn.addEventListener("click", function () {
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
      hideNewMsgBtn();
    });

    window.addEventListener("scroll", function () {
      if (isNearBottom(80)) hideNewMsgBtn();
    }, { passive: true });

    window.addEventListener("resize", positionBtn, { passive: true });
    // Position on first show (handled in showNewMsgBtn)
  })();

  function showNewMsgBtn() {
    if (!newMsgBtn) return;
    newMsgBtn.textContent = newMsgCount + " New Message" + (newMsgCount !== 1 ? "s" : "");
    newMsgBtn.classList.remove("is-hidden");
    // Center on chat section
    if (sectionEl) {
      var rect = sectionEl.getBoundingClientRect();
      var center = rect.left + rect.width / 2;
      var btnWidth = newMsgBtn.offsetWidth || 120;
      newMsgBtn.style.left = (center - btnWidth / 2) + "px";
    }
  }

  function hideNewMsgBtn() {
    if (!newMsgBtn) return;
    newMsgBtn.classList.add("is-hidden");
    newMsgCount = 0;
    // Remove the real-time "New Messages" divider when user scrolls to bottom
    if (newMsgDividerInserted) {
      var rtDivider = document.getElementById("aichatNewMessagesDividerRT");
      if (rtDivider) rtDivider.remove();
      newMsgDividerInserted = false;
    }
  }

  // IntersectionObserver to mark Claude messages as read when visible
  function flushReadIds() {
    if (!pendingReadIds.length) return;
    var ids = pendingReadIds.slice();
    pendingReadIds = [];
    fetch("/c/" + channelId + "/mark-read", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({ message_ids: ids }),
    }).catch(function () { /* best effort */ });

    // Decrement sidebar unread for this channel
    var badge = document.querySelector('[data-sidebar-unread="' + channelId + '"]');
    if (badge) {
      var count = Math.max(0, parseInt(badge.textContent || "0", 10) - ids.length);
      badge.textContent = count;
      if (count === 0) badge.classList.add("is-hidden");
    }
  }

  function scheduleReadFlush() {
    if (readFlushTimer) return;
    readFlushTimer = setTimeout(function () {
      readFlushTimer = null;
      flushReadIds();
    }, 500);
  }

  var readObserver = null;
  if (channelId && window.IntersectionObserver) {
    readObserver = new IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        var entry = entries[i];
        if (!entry.isIntersecting) continue;
        // Only mark as read when the bottom of the message is visible
        var bottomVisible = entry.rootBounds &&
          entry.boundingClientRect.bottom <= entry.rootBounds.bottom + 2;
        if (!bottomVisible) continue;
        var el = entry.target;
        var msgId = el.getAttribute("data-message-id");
        if (msgId) {
          pendingReadIds.push(msgId);
          scheduleReadFlush();
        }
        readObserver.unobserve(el);
        el.removeAttribute("data-unread");
      }
    }, { threshold: [0, 0.5, 1] });

    // Observe existing unread messages from page load
    var unreadEls = messagesEl.querySelectorAll("[data-unread]");
    for (var i = 0; i < unreadEls.length; i++) {
      readObserver.observe(unreadEls[i]);
    }
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

  // Paste images from clipboard
  if (input) {
    input.addEventListener("paste", function (e) {
      var items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      for (var i = 0; i < items.length; i++) {
        if (items[i].type.startsWith("image/")) {
          var file = items[i].getAsFile();
          if (file) uploadFile(file);
        }
      }
    });
  }

  // Drag-and-drop images anywhere on the chat area
  (function () {
    var section = messagesEl.closest(".aichat-section");
    if (!section) return;

    section.addEventListener("dragover", function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      section.classList.add("aichat-dragover");
    });

    section.addEventListener("dragleave", function (e) {
      // Only remove if leaving the section entirely
      if (!section.contains(e.relatedTarget)) {
        section.classList.remove("aichat-dragover");
      }
    });

    section.addEventListener("drop", function (e) {
      e.preventDefault();
      section.classList.remove("aichat-dragover");
      var files = e.dataTransfer.files;
      if (!files || !files.length) return;
      for (var i = 0; i < files.length; i++) {
        if (files[i].type.startsWith("image/")) {
          uploadFile(files[i]);
        }
      }
    });
  })();

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
    var atBottom = isNearBottom();

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

    // Insert "New Messages" divider before first unseen message when scrolled up
    if (!atBottom && sender === "claude" && !newMsgDividerInserted) {
      var rtDivider = document.createElement("div");
      rtDivider.className = "aichat-event-divider aichat-new-messages-divider";
      rtDivider.id = "aichatNewMessagesDividerRT";
      var rtLabel = document.createElement("span");
      rtLabel.className = "aichat-event-label";
      rtLabel.textContent = "New Messages";
      rtDivider.appendChild(rtLabel);
      messagesEl.appendChild(rtDivider);
      newMsgDividerInserted = true;
    }

    messagesEl.appendChild(div);

    if (sender === "claude" && messageId && readObserver) {
      div.setAttribute("data-unread", "1");
      readObserver.observe(div);
    }

    if (atBottom) {
      // Scroll so the top of the new message is at the top of the viewport
      var msgTop = div.getBoundingClientRect().top + window.scrollY;
      var maxScroll = document.body.scrollHeight - window.innerHeight;
      window.scrollTo({ top: Math.min(msgTop - 8, maxScroll), behavior: "instant" });
    } else if (sender === "claude") {
      // User has scrolled up — show floating button
      newMsgCount++;
      showNewMsgBtn();
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
      if (line.charAt(0) === "-" || line.charAt(0) === "+") {
        lineEl.textContent = line.substring(1);
      } else {
        lineEl.textContent = line;
      }
      container.appendChild(lineEl);
    }
    return container;
  }

  // "View New Activity" button for tool panel
  var toolNewActivityBtn = null;
  if (toolPanelContent) {
    toolNewActivityBtn = document.createElement("button");
    toolNewActivityBtn.className = "aichat-tool-new-activity-btn is-hidden";
    toolNewActivityBtn.type = "button";
    toolNewActivityBtn.textContent = "View New Activity";
    toolPanel.appendChild(toolNewActivityBtn);

    toolNewActivityBtn.addEventListener("click", function () {
      toolPanelContent.scrollTop = toolPanelContent.scrollHeight;
      toolNewActivityBtn.classList.add("is-hidden");
    });

    toolPanelContent.addEventListener("scroll", function () {
      var nearBottom = (toolPanelContent.scrollHeight - toolPanelContent.clientHeight - toolPanelContent.scrollTop) <= 30;
      if (nearBottom) toolNewActivityBtn.classList.add("is-hidden");
    }, { passive: true });
  }

  function isToolPanelNearBottom() {
    if (!toolPanelContent) return true;
    return (toolPanelContent.scrollHeight - toolPanelContent.clientHeight - toolPanelContent.scrollTop) <= 30;
  }

  function addToolToPanel(description) {
    if (!toolPanelContent) return;

    // Deduplicate rapid updates
    var lastItem = toolPanelContent.lastElementChild;
    if (lastItem && lastItem.textContent === description) return;

    var atBottom = isToolPanelNearBottom();

    var item = document.createElement("div");
    item.className = "aichat-tool-panel-item";

    // Render edit diffs with syntax highlighting
    if (description.indexOf("diff:") === 0) {
      item.appendChild(renderDiff(description));
    } else {
      item.textContent = description;
    }

    toolPanelContent.appendChild(item);

    if (atBottom) {
      toolPanelContent.scrollTop = toolPanelContent.scrollHeight;
    } else if (toolNewActivityBtn) {
      toolNewActivityBtn.classList.remove("is-hidden");
    }

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

  // Hydrate tool panel from persisted tool messages
  (function () {
    var toolDataEls = document.querySelectorAll("script.aichat-tool-data");
    for (var i = 0; i < toolDataEls.length; i++) {
      try {
        var content = JSON.parse(toolDataEls[i].textContent);
        var lines = content.split("\n");
        // Reassemble multi-line descriptions: diffs start with "diff:" and
        // continue with +/- lines; everything else is a standalone description.
        var buf = null;
        for (var j = 0; j < lines.length; j++) {
          var line = lines[j];
          if (!line) continue;
          if (line.indexOf("diff:") === 0) {
            if (buf) addToolToPanel(buf);
            buf = line;
          } else if (buf && (line.charAt(0) === "+" || line.charAt(0) === "-")) {
            buf += "\n" + line;
          } else {
            if (buf) { addToolToPanel(buf); buf = null; }
            addToolToPanel(line);
          }
        }
        if (buf) addToolToPanel(buf);
      } catch (e) { /* skip malformed */ }
    }
    // Reset to idle state after hydration
    if (toolDataEls.length > 0) finalizeToolPanel();
  })();

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
  var cancelBtn = document.getElementById("aichatCancelBtn");
  var archiveBtn = document.getElementById("aichatArchiveBtn");
  var restartBtn = document.getElementById("aichatRestartBtn");
  var logoEl = document.querySelector(".logo");

  if (editBtn && modal) {
    editBtn.addEventListener("click", function () {
      modal.classList.add("is-active");
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

    if (archiveBtn) {
      archiveBtn.addEventListener("click", function () {
        if (!confirm("Archive this channel? The worker will be stopped.")) return;
        archiveBtn.disabled = true;

        fetch("/channels/" + channelId + "/archive", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
          },
          body: JSON.stringify({ archived: true }),
        })
          .then(function (res) {
            if (!res.ok) throw new Error("Archive failed");
            window.location.href = "/";
          })
          .catch(function (err) {
            console.error("Archive error:", err);
            archiveBtn.disabled = false;
          });
      });
    }

    if (restartBtn) {
      restartBtn.addEventListener("click", function () {
        if (!confirm("Restart the worker for this channel?")) return;
        restartBtn.disabled = true;
        var deviceId = restartBtn.getAttribute("data-device-id");

        fetch("/api/user-devices/" + deviceId + "/workers/" + channelId + "/restart", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken,
          },
          body: JSON.stringify({}),
        })
          .then(function (res) {
            if (!res.ok) throw new Error("Restart failed");
            modal.classList.remove("is-active");
          })
          .catch(function (err) {
            console.error("Restart error:", err);
          })
          .finally(function () {
            restartBtn.disabled = false;
          });
      });
    }
  }
})();
