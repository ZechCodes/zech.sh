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

      // Device action buttons
      var actions = document.createElement("span");
      actions.className = "aichat-sidebar-device-actions";

      var newBtn = document.createElement("button");
      newBtn.className = "aichat-sidebar-device-btn";
      newBtn.textContent = "+ NEW";
      newBtn.title = "New task";
      newBtn.setAttribute("data-device-id", device.id);
      newBtn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        openNewTaskModal(ev.currentTarget.getAttribute("data-device-id"));
      });
      actions.appendChild(newBtn);

      var editBtn = document.createElement("button");
      editBtn.className = "aichat-sidebar-device-btn";
      editBtn.innerHTML = "&#9998;";
      editBtn.title = "Edit device";
      editBtn.setAttribute("data-device-id", device.id);
      editBtn.setAttribute("data-device-name", device.name);
      editBtn.addEventListener("click", function (ev) {
        ev.stopPropagation();
        var btn = ev.currentTarget;
        openEditDeviceModal(btn.getAttribute("data-device-id"), btn.getAttribute("data-device-name"));
      });
      actions.appendChild(editBtn);

      deviceHeader.appendChild(actions);

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

  // --- New Task modal ---
  var newTaskModal = document.getElementById("aichatNewTaskModal");
  var newTaskInput = document.getElementById("aichatNewTaskInput");
  var newTaskAgent = document.getElementById("aichatNewTaskAgent");
  var newTaskCreate = document.getElementById("aichatNewTaskCreate");
  var newTaskCancel = document.getElementById("aichatNewTaskCancel");
  var newTaskDeviceId = null;

  function openNewTaskModal(deviceId) {
    newTaskDeviceId = deviceId;
    if (newTaskInput) newTaskInput.value = "";
    if (newTaskAgent) newTaskAgent.value = "claude";
    if (newTaskModal) newTaskModal.classList.add("is-active");
    if (newTaskInput) newTaskInput.focus();
  }

  if (newTaskCancel) {
    newTaskCancel.addEventListener("click", function () {
      newTaskModal.classList.remove("is-active");
    });
  }
  if (newTaskInput) {
    newTaskInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); newTaskCreate.click(); }
      if (e.key === "Escape") { newTaskModal.classList.remove("is-active"); }
    });
  }
  if (newTaskCreate) {
    newTaskCreate.addEventListener("click", function () {
      var name = newTaskInput.value.trim() || "New Task";
      var agentType = newTaskAgent ? newTaskAgent.value : "claude";
      newTaskCreate.disabled = true;
      fetch("/api/user-devices/" + newTaskDeviceId + "/workers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, agent_type: agentType }),
      })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.ok && data.channel) {
          window.location.href = "/c/" + data.channel.id;
        } else {
          alert("Error: " + (data.error || "unknown"));
          newTaskCreate.disabled = false;
        }
      })
      .catch(function () {
        alert("Failed to create task");
        newTaskCreate.disabled = false;
      });
    });
  }

  // --- Edit Device modal ---
  var editDeviceModal = document.getElementById("aichatEditDeviceModal");
  var deviceNameInput = document.getElementById("aichatDeviceNameInput");
  var deviceSave = document.getElementById("aichatDeviceSave");
  var deviceCancel = document.getElementById("aichatDeviceCancel");
  var deviceDelete = document.getElementById("aichatDeviceDelete");
  var editDeviceId = null;

  function openEditDeviceModal(deviceId, deviceName) {
    editDeviceId = deviceId;
    if (deviceNameInput) deviceNameInput.value = deviceName;
    if (editDeviceModal) editDeviceModal.classList.add("is-active");
    if (deviceNameInput) deviceNameInput.focus();
  }

  if (deviceCancel) {
    deviceCancel.addEventListener("click", function () {
      editDeviceModal.classList.remove("is-active");
    });
  }
  if (deviceNameInput) {
    deviceNameInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); deviceSave.click(); }
      if (e.key === "Escape") { editDeviceModal.classList.remove("is-active"); }
    });
  }
  if (deviceSave) {
    deviceSave.addEventListener("click", function () {
      var name = deviceNameInput.value.trim();
      if (!name) return;
      deviceSave.disabled = true;
      fetch("/api/user-devices/" + editDeviceId, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name }),
      })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.ok) {
          window.location.reload();
        } else {
          alert("Error: " + (data.error || "unknown"));
          deviceSave.disabled = false;
        }
      })
      .catch(function () {
        alert("Failed to update device");
        deviceSave.disabled = false;
      });
    });
  }
  if (deviceDelete) {
    deviceDelete.addEventListener("click", function () {
      if (!confirm("Delete this device? Its channels will be unassigned.")) return;
      deviceDelete.disabled = true;
      fetch("/api/user-devices/" + editDeviceId, {
        method: "DELETE",
      })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (data.ok) {
          window.location.href = "/";
        } else {
          alert("Error: " + (data.error || "unknown"));
          deviceDelete.disabled = false;
        }
      })
      .catch(function () {
        alert("Failed to delete device");
        deviceDelete.disabled = false;
      });
    });
  }

  // Realtime updates for other channels
  var pulseTimers = {};
  var toolPulseReady = false;
  document.addEventListener("sk:notification", function (e) {
    var d = e.detail;
    if (!d || !d.channel_id) return;

    if (d.type === "aichat:message" && (d.sender === "claude" || d.sender === "codex") && d.channel_id !== channelId) {
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
  // E2E Encryption: decrypt incoming messages, encrypt outgoing
  // ---------------------------------------------------------------------------

  var e2e = (function () {
    var cryptoConfig = window.__aichatCrypto || {};

    // --- Key mismatch UI ---
    var keyMismatchShown = false;
    var keyWarningOverlay = document.getElementById("aichatKeyWarningOverlay");

    function showKeyMismatchWarning() {
      if (keyMismatchShown) return;
      keyMismatchShown = true;
      if (keyWarningOverlay) keyWarningOverlay.removeAttribute("hidden");
      var btn = document.getElementById("aichatKeyWarningRekey");
      if (btn) {
        btn.addEventListener("click", function () {
          btn.disabled = true;
          var status = document.getElementById("aichatKeyWarningStatus");
          if (status) status.textContent = "Requesting new key\u2026";
          manualRekey();
        });
      }
    }

    function dismissKeyMismatchWarning() {
      keyMismatchShown = false;
      if (keyWarningOverlay) keyWarningOverlay.setAttribute("hidden", "");
    }

    // Wraps AichatCrypto.decryptEvent with key-mismatch UI
    function decryptEventWithUI(d) {
      AichatCrypto.decryptEvent(d);
      if (d._keyMismatch) showKeyMismatchWarning();
      return d;
    }

    // Called after a channel key becomes available — decrypt pending messages
    // and request history from device to replace page-load "[encrypted]" text.
    function onKeyReady() {
      // 1. Decrypt real-time messages that arrived before the key was ready
      var pendingEls = document.querySelectorAll("[data-encrypted-payload]");
      for (var i = 0; i < pendingEls.length; i++) {
        decryptMessageElement(pendingEls[i]);
      }
      if (pendingEls.length) console.log("E2E: decrypted " + pendingEls.length + " buffered messages");

      // 2. Delegate to receive layer if available
      if (typeof receiveLayer !== "undefined") {
        receiveLayer.onKeyReady();
      } else {
        // Fallback: request history directly
        requestHistoryForEncrypted();
      }
    }

    function onKeyEstablished(keyBytes) {
      AichatCrypto.setKey(keyBytes);
      api.enabled = true;
      hideRekeyBanner();
      dismissKeyMismatchWarning();
      onKeyReady();
    }

    function decryptMessageElement(el) {
      var plain = AichatCrypto.decrypt(el.getAttribute("data-encrypted-payload"), el.getAttribute("data-nonce"));
      if (!plain) {
        if (AichatCrypto.hasKey()) showKeyMismatchWarning();
        return false;
      }
      try {
        var payload = JSON.parse(plain);
        var contentEl = el.querySelector(".aichat-msg-content");
        if (contentEl) contentEl.innerHTML = renderMarkdown(payload.content || "");
        if (payload.attachments && payload.attachments.length) {
          var imgContainer = createImageElements(payload.attachments);
          if (imgContainer) {
            var existingImgs = el.querySelector(".aichat-msg-images");
            if (existingImgs) existingImgs.remove();
            var senderEl = el.querySelector(".aichat-msg-sender");
            if (senderEl && senderEl.nextSibling) {
              el.insertBefore(imgContainer, senderEl.nextSibling);
            } else if (contentEl) {
              el.insertBefore(imgContainer, contentEl);
            }
          }
        }
      } catch (ex) {
        var contentEl2 = el.querySelector(".aichat-msg-content");
        if (contentEl2) contentEl2.innerHTML = renderMarkdown(plain);
      }
      el.removeAttribute("data-encrypted-payload");
      el.removeAttribute("data-nonce");
      return true;
    }

    function requestHistoryForEncrypted() {
      if (!AichatCrypto.hasKey()) return;
      var encEls = document.querySelectorAll('.aichat-msg-content[data-raw="[encrypted]"]');
      var encToolMarkers = document.querySelectorAll(".aichat-encrypted-tools-marker");
      if (!encEls.length && !encToolMarkers.length) return;

      console.log("E2E: requesting history from device for " + encEls.length + " encrypted messages" +
        (encToolMarkers.length ? " + " + encToolMarkers.length + " encrypted tool entries" : ""));

      var requestId = "hist-" + Date.now() + "-" + Math.random().toString(36).substr(2, 6);
      if (!window.__aichatPendingHistory) window.__aichatPendingHistory = {};

      var timeoutId = setTimeout(function () {
        delete window.__aichatPendingHistory[requestId];
        console.warn("E2E: history request timed out — device may be offline");
      }, 15000);

      window.__aichatPendingHistory[requestId] = function (resp) {
        clearTimeout(timeoutId);
        var messages = resp.messages || [];
        var count = 0;
        for (var j = 0; j < messages.length; j++) {
          var m = messages[j];
          if (m.encrypted_payload && m.nonce) {
            var plain = AichatCrypto.decrypt(m.encrypted_payload, m.nonce);
            if (plain) {
              var histContent, histAttachments;
              try {
                var payload = JSON.parse(plain);
                histContent = payload.content || "";
                histAttachments = payload.attachments || [];
              } catch (ex) {
                histContent = plain;
                histAttachments = [];
              }

              // Try receiveLayer first (for pending entries not yet in DOM)
              if (typeof receiveLayer !== "undefined" && receiveLayer.applyHistoryMessage(m.id, histContent, histAttachments)) {
                count++;
                continue;
              }

              // Fall back to patching existing DOM elements
              var target = document.querySelector('[data-message-id="' + m.id + '"] .aichat-msg-content');
              if (target) {
                target.innerHTML = renderMarkdown(histContent);
                target.removeAttribute("data-raw");
                if (histAttachments.length) {
                  var msgDiv = target.closest("[data-message-id]");
                  if (msgDiv) {
                    var imgContainer = createImageElements(histAttachments);
                    if (imgContainer) {
                      var existingImgs = msgDiv.querySelector(".aichat-msg-images");
                      if (existingImgs) existingImgs.remove();
                      msgDiv.insertBefore(imgContainer, target);
                    }
                  }
                }
                count++;
              }
            }
          }
        }
        console.log("E2E: decrypted " + count + " historical messages via device relay");

        // Detect key mismatch: we got encrypted messages but couldn't decrypt any
        var encryptedCount = 0;
        for (var ec = 0; ec < messages.length; ec++) {
          if (messages[ec].encrypted_payload && messages[ec].nonce && messages[ec].sender !== "tools") encryptedCount++;
        }
        if (encryptedCount > 0 && count === 0 && AichatCrypto.hasKey()) {
          showKeyMismatchWarning();
        }

        // Handle tool messages from history
        var toolEntries = [];
        for (var k = 0; k < messages.length; k++) {
          var tm = messages[k];
          if (tm.sender !== "tools") continue;
          var toolContent = null;
          if (tm.encrypted_payload && tm.nonce) {
            var rawTool = AichatCrypto.decrypt(tm.encrypted_payload, tm.nonce);
            if (rawTool) {
              // Device wraps as {content, attachments} before encrypting
              try {
                var wrapper = JSON.parse(rawTool);
                toolContent = wrapper.content || rawTool;
              } catch (ex) {
                toolContent = rawTool;
              }
            }
          } else if (tm.content) {
            toolContent = tm.content;
          }
          if (!toolContent) continue;

          // Device stores tool content as JSON: {status, tool, description}
          try {
            var toolData = JSON.parse(toolContent);
            if (toolData.description) toolEntries.push(toolData.description);
          } catch (ex) {
            // Might be raw text (legacy format)
            if (toolContent !== "[encrypted]") toolEntries.push(toolContent);
          }
        }

        if (toolEntries.length > 0) {
          clearToolPanel();
          for (var t = 0; t < toolEntries.length; t++) {
            addToolToPanel(toolEntries[t]);
          }
          finalizeToolPanel();
          console.log("E2E: hydrated " + toolEntries.length + " tool entries from device history");
        }
      };

      // Collect specific message IDs the browser needs decrypted
      var messageIds = [];
      for (var ei = 0; ei < encEls.length; ei++) {
        var msgDiv = encEls[ei].closest("[data-message-id]");
        if (msgDiv) messageIds.push(msgDiv.getAttribute("data-message-id"));
      }

      var csrfTok = form ? (form.getAttribute("data-csrf") || "") : "";
      fetch("/c/" + channelId + "/request-history", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfTok },
        body: JSON.stringify({ request_id: requestId, limit: 200, message_ids: messageIds }),
      }).catch(function (err) {
        clearTimeout(timeoutId);
        delete window.__aichatPendingHistory[requestId];
        console.warn("E2E: history request failed", err);
      });
    }

    // --- Rekey banner UI ---
    var rekeyBanner = document.getElementById("aichatRekeyBanner");
    var rekeyBtn = document.getElementById("aichatRekeyBtn");
    var rekeyStatus = document.getElementById("aichatRekeyStatus");

    function showRekeyBanner() {
      if (rekeyBanner) rekeyBanner.removeAttribute("hidden");
    }

    function hideRekeyBanner() {
      if (rekeyBanner) rekeyBanner.setAttribute("hidden", "");
    }

    // Request key exchange with device
    function doRekey() {
      if (!AichatCrypto.isReady()) return;
      var devicePubB64 = cryptoConfig.deviceX25519Public;
      if (!devicePubB64) return;

      if (rekeyBtn) rekeyBtn.disabled = true;
      if (rekeyStatus) rekeyStatus.textContent = "Requesting...";

      console.log("E2E: initiating key exchange with device");

      AichatCrypto.initiateRekey(devicePubB64)
        .then(function (result) {
          // POST rekey request so device can derive the same transport key
          fetch("/c/" + channelId + "/rekey", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": csrfToken,
            },
            body: JSON.stringify({ browser_x25519_public: result.browserPublicB64, request_id: result.requestId }),
          }).then(function (resp) {
            if (resp.ok) {
              console.log("E2E: rekey request sent — waiting for device response");
              if (rekeyStatus) rekeyStatus.textContent = "Waiting for device...";
            } else {
              console.warn("E2E: rekey request failed", resp.status);
              if (rekeyStatus) rekeyStatus.textContent = "Failed (" + resp.status + ")";
              if (rekeyBtn) rekeyBtn.disabled = false;
              AichatCrypto.cancelRekey(result.requestId);
            }
          }).catch(function (err) {
            console.warn("E2E: rekey request error", err);
            if (rekeyStatus) rekeyStatus.textContent = "Error — try again";
            if (rekeyBtn) rekeyBtn.disabled = false;
            AichatCrypto.cancelRekey(result.requestId);
          });
        })
        .catch(function (err) {
          console.warn("E2E: key derivation failed", err);
          if (rekeyStatus) rekeyStatus.textContent = "Key derivation failed";
          if (rekeyBtn) rekeyBtn.disabled = false;
        });
    }

    if (rekeyBtn) {
      rekeyBtn.addEventListener("click", doRekey);
    }

    // Auto-rekey if E2E is needed but we have no key
    function checkRekeyNeeded() {
      if (AichatCrypto.hasKey() || !AichatCrypto.isReady()) return;
      var devicePubB64 = cryptoConfig.deviceX25519Public;
      if (!devicePubB64) return; // Device doesn't support E2E
      showRekeyBanner();
      doRekey(); // Auto-trigger instead of waiting for button click
    }
    setTimeout(checkRekeyNeeded, 0);

    // Manual rekey: clear cached key and request fresh keys from device
    function manualRekey() {
      console.log("E2E: manual rekey requested — clearing encryption key");
      AichatCrypto.clearKey();
      api.enabled = false;
      showRekeyBanner();
      var rekeyText = document.getElementById("aichatRekeyText");
      if (rekeyText) rekeyText.textContent = "Re-keying\u2026";
      if (rekeyStatus) rekeyStatus.textContent = "";
      doRekey();
    }

    var api = {
      enabled: AichatCrypto.hasKey(),
      decrypt: AichatCrypto.decrypt,
      encrypt: AichatCrypto.encrypt,
      decryptEvent: decryptEventWithUI,
      setChannelKey: onKeyEstablished,
      requestHistoryForEncrypted: requestHistoryForEncrypted,
      decryptMessageElement: decryptMessageElement,
      manualRekey: manualRekey,
      hideRekeyBanner: hideRekeyBanner,
      onKeyReady: onKeyReady,
    };

    // If key was loaded from localStorage at init, decrypt page-load messages
    if (AichatCrypto.hasKey()) {
      setTimeout(onKeyReady, 0);
    }

    return api;
  })();

  // ---------------------------------------------------------------------------
  // Receive layer: buffers, decrypts, and delivers messages
  // ---------------------------------------------------------------------------

  var receiveLayer = (function () {
    // Notifications awaiting their content-relay
    var pending = {};  // messageId -> { sender, attachments, timestamp }

    // Content-relays that arrived before their notification
    var relayBuffer = {};  // messageId -> { content, attachments, timestamp }

    // Optimistic user messages awaiting server confirmation
    var optimisticQueue = [];  // [{ content, attachments, timestamp }]

    // Dedup: prevent rendering the same message twice
    var rendered = {};  // messageId -> true

    // Interaction waiting for its content-relay
    var pendingInteraction = null;

    // Whether messages arrived while tab was hidden
    var missedWhileHidden = false;

    function handleMessage(d) {
      // Dedup check
      if (d.message_id && rendered[d.message_id]) return;

      // Events render immediately (no encryption)
      if (d.sender === "event") {
        if (d.message_id) rendered[d.message_id] = true;
        appendEventDivider(d.content, d.message_id);
        if (d.content === "plan:enter") { isPlanning = true; wantsPlanning = true; }
        else if (d.content === "plan:exit") { isPlanning = false; wantsPlanning = false; }
        if (modeToggle) modeToggle.classList.toggle("is-planning", wantsPlanning);
        return;
      }

      // User messages: reconcile with optimistic renders
      if (d.sender === "user") {
        if (d.message_id) rendered[d.message_id] = true;
        optimisticQueue.shift();
        var optimistic = messagesEl.querySelector('.aichat-msg-user:not([data-message-id])');
        if (optimistic) optimistic.setAttribute("data-message-id", d.message_id);
        return;
      }

      // Agent messages: finalize tool panel
      if (d.sender === "claude" || d.sender === "codex") {
        finalizeToolPanel();
      }

      // Check if content-relay arrived first
      var buffered = relayBuffer[d.message_id];
      if (buffered) {
        delete relayBuffer[d.message_id];
        rendered[d.message_id] = true;
        appendMessage(d.sender, buffered.content, d.message_id, buffered.attachments);
        return;
      }

      // Content available (decrypted inline or plaintext)
      if (d.content) {
        rendered[d.message_id] = true;
        appendMessage(d.sender, d.content, d.message_id, d.attachments);
        return;
      }

      // Encrypted, relay hasn't arrived yet — buffer without rendering
      pending[d.message_id] = {
        sender: d.sender,
        attachments: d.attachments,
        timestamp: Date.now(),
      };
    }

    function handleContentRelay(d) {
      var ct = d.content_type || "message";

      if (d.encrypted_payload && d.nonce) {
        var relayPlain = e2e.decrypt(d.encrypted_payload, d.nonce);
        if (!relayPlain) {
          console.warn("E2E: content-relay decrypt failed (key not ready?)");
          return;
        }

        if (ct === "message" && d.message_id) {
          var relayContent, relayAttachments;
          try {
            var relayPayload = JSON.parse(relayPlain);
            relayContent = relayPayload.content || "";
            relayAttachments = relayPayload.attachments || [];
          } catch (ex) {
            relayContent = relayPlain;
            relayAttachments = [];
          }

          // Check if notification is waiting for this content
          var entry = pending[d.message_id];
          if (entry) {
            delete pending[d.message_id];
            rendered[d.message_id] = true;
            appendMessage(entry.sender, relayContent, d.message_id, relayAttachments);
            return;
          }

          // Check if message element already exists in DOM
          var relayEl = document.querySelector('[data-message-id="' + d.message_id + '"]');
          if (relayEl) {
            applyContentRelay(d.message_id, relayContent, relayAttachments);
            return;
          }

          // Buffer for when notification arrives
          relayBuffer[d.message_id] = {
            content: relayContent,
            attachments: relayAttachments,
            timestamp: Date.now(),
          };
        } else if (ct === "tool") {
          addToolToPanel(relayPlain);
        } else if (ct === "interaction") {
          try {
            var intPayload = JSON.parse(relayPlain);
            if (pendingInteraction) {
              pendingInteraction.content = intPayload.content || "";
              pendingInteraction.options = intPayload.options || [];
              showInteraction(pendingInteraction);
              pendingInteraction = null;
            }
          } catch (ex) {
            console.warn("E2E: failed to parse interaction relay", ex);
          }
        }
      }
    }

    function handleTool(d) {
      if (d.status === "active") {
        if (d.description) addToolToPanel(d.description);
      } else if (d.status === "idle") {
        finalizeToolPanel();
      }
    }

    function handleInteraction(d) {
      // Decrypt interaction content if needed
      if (d.encrypted_payload && d.nonce) {
        var plain = e2e.decrypt(d.encrypted_payload, d.nonce);
        if (plain) {
          try {
            var payload = JSON.parse(plain);
            d.content = payload.content || d.content;
            d.options = payload.options || d.options;
          } catch (ex) { d.content = plain; }
        }
      }
      // If metadata-only (content coming via relay), stash for later
      if (!d.content && d.interaction_id) {
        pendingInteraction = d;
      } else {
        showInteraction(d);
      }
    }

    function handleRead(d) {
      markAsRead(d.message_ids || []);
    }

    function registerOptimistic(content, attachments) {
      optimisticQueue.push({ content: content, attachments: attachments, timestamp: Date.now() });
    }

    function reconcileOptimistic() {
      // Remove all untagged optimistic user messages
      var untagged = messagesEl.querySelectorAll('.aichat-msg-user:not([data-message-id])');
      for (var i = 0; i < untagged.length; i++) {
        untagged[i].remove();
      }
      optimisticQueue = [];
    }

    function flushMissed() {
      if (!missedWhileHidden) return;
      missedWhileHidden = false;

      var lastId = getLastMessageId();
      if (!lastId || !channelId) return;

      var url = "/c/" + channelId + "/messages?after=" + encodeURIComponent(lastId);
      fetch(url, { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var messages = data.messages || [];
          var hasUserMessages = false;

          for (var i = 0; i < messages.length; i++) {
            var m = messages[i];
            if (rendered[m.id]) continue;
            if (document.querySelector('[data-message-id="' + m.id + '"]')) continue;

            if (m.sender === "event") {
              rendered[m.id] = true;
              appendEventDivider(m.content, m.id);
            } else if (m.content && m.content !== "[encrypted]") {
              rendered[m.id] = true;
              appendMessage(m.sender, m.content, m.id, m.attachments);
              if (m.sender === "user") hasUserMessages = true;
            } else {
              // Encrypted — add to pending, don't render
              pending[m.id] = { sender: m.sender, attachments: m.attachments || [], timestamp: Date.now() };
            }
          }

          if (hasUserMessages) reconcileOptimistic();

          // Request history from device for encrypted messages
          if (Object.keys(pending).length > 0) {
            e2e.requestHistoryForEncrypted();
          }
        })
        .catch(function (err) {
          console.warn("Failed to fetch missed messages:", err);
        });
    }

    function onKeyReady() {
      // Decrypt real-time messages that arrived before the key was ready
      var els = document.querySelectorAll("[data-encrypted-payload]");
      for (var i = 0; i < els.length; i++) {
        if (e2e.decryptMessageElement) e2e.decryptMessageElement(els[i]);
      }

      // Request history from device to replace "[encrypted]" page-load messages
      e2e.requestHistoryForEncrypted();
    }

    // Bridge: resolve pending entries from history responses
    function applyHistoryMessage(id, content, attachments) {
      var entry = pending[id];
      if (entry) {
        delete pending[id];
        rendered[id] = true;
        appendMessage(entry.sender, content, id, attachments);
        return true;
      }
      return false;
    }

    // Stale entry cleanup (every 15 seconds)
    setInterval(function () {
      var now = Date.now();
      var STALE_MS = 30000;
      var needsHistory = false;

      Object.keys(pending).forEach(function (id) {
        if (now - pending[id].timestamp > STALE_MS) {
          needsHistory = true;
        }
      });

      Object.keys(relayBuffer).forEach(function (id) {
        if (now - relayBuffer[id].timestamp > STALE_MS) {
          delete relayBuffer[id];
        }
      });

      if (needsHistory) e2e.requestHistoryForEncrypted();
    }, 15000);

    return {
      handleMessage: handleMessage,
      handleContentRelay: handleContentRelay,
      handleTool: handleTool,
      handleInteraction: handleInteraction,
      handleRead: handleRead,
      registerOptimistic: registerOptimistic,
      flushMissed: flushMissed,
      onKeyReady: onKeyReady,
      applyHistoryMessage: applyHistoryMessage,
      get missedWhileHidden() { return missedWhileHidden; },
      set missedWhileHidden(val) { missedWhileHidden = val; },
    };
  })();

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

      if (e2e.enabled) {
        // E2E: request older messages from device via relay
        var requestId = "older-" + Date.now() + "-" + Math.random().toString(36).substr(2, 6);
        if (!window.__aichatPendingHistory) window.__aichatPendingHistory = {};

        var timeoutId = setTimeout(function () {
          delete window.__aichatPendingHistory[requestId];
          loadMoreBtn.disabled = false;
          loadMoreBtn.textContent = "Load older messages";
          console.warn("E2E: load-older request timed out");
        }, 15000);

        window.__aichatPendingHistory[requestId] = function (resp) {
          clearTimeout(timeoutId);
          var messages = (resp.messages || []).map(function (m) {
            if (m.encrypted_payload && m.nonce) {
              var plain = e2e.decrypt(m.encrypted_payload, m.nonce);
              if (plain) {
                try {
                  var payload = JSON.parse(plain);
                  m.content = payload.content || "";
                  m.attachments = payload.attachments || [];
                } catch (ex) { m.content = plain; }
              }
            }
            return m;
          });
          prependMessages(messages);
          if (!resp.has_more) {
            loadMoreBtn.remove();
            loadMoreBtn = null;
          } else {
            loadMoreBtn.disabled = false;
            loadMoreBtn.textContent = "Load older messages";
          }
        };

        var csrfTok = form ? (form.getAttribute("data-csrf") || "") : "";
        fetch("/c/" + channelId + "/request-history", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfTok },
          body: JSON.stringify({ request_id: requestId, before: beforeId, limit: 100 }),
        }).catch(function (err) {
          clearTimeout(timeoutId);
          delete window.__aichatPendingHistory[requestId];
          console.error("Load older messages error:", err);
          loadMoreBtn.disabled = false;
          loadMoreBtn.textContent = "Load older messages";
        });
      } else {
        // Non-E2E: fetch directly from server
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
      }
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

  // Drag-and-drop images onto the chat form
  (function () {
    var bottomEl = document.getElementById("aichatBottom");
    if (!bottomEl) return;

    // Create overlay
    var overlay = document.createElement("div");
    overlay.className = "aichat-drop-overlay";
    overlay.textContent = "Upload Images";
    bottomEl.appendChild(overlay);

    var dragCounter = 0;

    bottomEl.addEventListener("dragenter", function (e) {
      e.preventDefault();
      dragCounter++;
      bottomEl.classList.add("aichat-dragover");
    });

    bottomEl.addEventListener("dragover", function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    });

    bottomEl.addEventListener("dragleave", function (e) {
      dragCounter--;
      if (dragCounter <= 0) {
        dragCounter = 0;
        bottomEl.classList.remove("aichat-dragover");
      }
    });

    bottomEl.addEventListener("drop", function (e) {
      e.preventDefault();
      dragCounter = 0;
      bottomEl.classList.remove("aichat-dragover");
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

    // E2E: encrypt outgoing message
    if (e2e.enabled) {
      var plainPayload = JSON.stringify({ content: content, attachments: payload.attachments || [] });
      var encrypted = e2e.encrypt(plainPayload);
      if (encrypted) {
        payload = {
          encrypted_payload: encrypted.encrypted_payload,
          nonce: encrypted.nonce,
        };
      }
    }

    // Optimistically render the user's message (E2E: notification will be metadata-only)
    appendMessage("user", content, null, payload.attachments || pendingAttachments);
    receiveLayer.registerOptimistic(content, payload.attachments || pendingAttachments);

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
    if (!atBottom && (sender === "claude" || sender === "codex") && !newMsgDividerInserted) {
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

    if ((sender === "claude" || sender === "codex") && messageId && readObserver) {
      div.setAttribute("data-unread", "1");
      readObserver.observe(div);
    }

    if (atBottom) {
      // Scroll so the top of the new message is at the top of the viewport
      var msgTop = div.getBoundingClientRect().top + window.scrollY;
      var maxScroll = document.body.scrollHeight - window.innerHeight;
      window.scrollTo({ top: Math.min(msgTop - 8, maxScroll), behavior: "instant" });
    } else if (sender === "claude" || sender === "codex") {
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

  function renderOutput(description) {
    var lines = description.split("\n");
    var header = lines[0].substring(7); // strip "output:" prefix
    var container = document.createElement("div");
    container.className = "aichat-tool-output";

    var headerEl = document.createElement("div");
    headerEl.className = "aichat-tool-output-header";
    headerEl.textContent = header;
    container.appendChild(headerEl);

    var pre = document.createElement("pre");
    pre.className = "aichat-tool-output-content";
    pre.textContent = lines.slice(1).join("\n");
    container.appendChild(pre);
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

    // Render edit diffs and tool output with formatting
    if (description.indexOf("diff:") === 0) {
      item.appendChild(renderDiff(description));
    } else if (description.indexOf("output:") === 0) {
      item.appendChild(renderOutput(description));
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

  function clearToolPanel() {
    if (!toolPanelContent) return;
    toolPanelContent.innerHTML = "";
  }

  // Hydrate tool panel from persisted tool messages
  (function () {
    var toolDataEls = document.querySelectorAll("script.aichat-tool-data");
    for (var i = 0; i < toolDataEls.length; i++) {
      try {
        var content = JSON.parse(toolDataEls[i].textContent);
        var lines = content.split("\n");
        // Reassemble multi-line descriptions: diffs start with "diff:" and
        // continue with +/- lines; outputs start with "output:" and continue
        // until the next known prefix; everything else is standalone.
        var buf = null;
        var bufType = null; // "diff" or "output"
        for (var j = 0; j < lines.length; j++) {
          var line = lines[j];
          if (!line) continue;
          var isPrefix = line.indexOf("diff:") === 0 || line.indexOf("output:") === 0;
          if (isPrefix) {
            if (buf) addToolToPanel(buf);
            buf = line;
            bufType = line.indexOf("diff:") === 0 ? "diff" : "output";
          } else if (buf && bufType === "diff" && (line.charAt(0) === "+" || line.charAt(0) === "-")) {
            buf += "\n" + line;
          } else if (buf && bufType === "output") {
            buf += "\n" + line;
          } else {
            if (buf) { addToolToPanel(buf); buf = null; bufType = null; }
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
  // Notification handler (thin dispatcher → receiveLayer)
  // ---------------------------------------------------------------------------

  function applyContentRelay(msgId, content, attachments) {
    var el = document.querySelector('[data-message-id="' + msgId + '"] .aichat-msg-content');
    if (el) {
      el.innerHTML = renderMarkdown(content || "");
      el.removeAttribute("data-raw");
      if (attachments && attachments.length) {
        var msgDiv = el.closest("[data-message-id]");
        if (msgDiv) {
          var imgContainer = createImageElements(attachments);
          if (imgContainer) {
            var existingImgs = msgDiv.querySelector(".aichat-msg-images");
            if (existingImgs) existingImgs.remove();
            msgDiv.insertBefore(imgContainer, el);
          }
        }
      }
    }
  }

  function getLastMessageId() {
    var msgs = messagesEl.querySelectorAll(".aichat-msg[data-message-id]");
    if (!msgs.length) return null;
    return msgs[msgs.length - 1].getAttribute("data-message-id");
  }

  // ---------------------------------------------------------------------------
  // Visibility: skip SSE rendering when hidden, flush on focus
  // ---------------------------------------------------------------------------

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) {
      receiveLayer.flushMissed();
    }
  });

  // ---------------------------------------------------------------------------
  // SSE notification dispatcher
  // ---------------------------------------------------------------------------

  document.addEventListener("sk:notification", function (e) {
    var d = e.detail;
    if (!d) return;

    // Filter by channel
    if (channelId && d.channel_id && d.channel_id !== channelId) return;

    // Buffer when tab is hidden
    if (document.hidden && (d.type === "aichat:message" || d.type === "aichat:content-relay")) {
      receiveLayer.missedWhileHidden = true;
      return;
    }

    // Decrypt any inline encrypted fields
    d = e2e.decryptEvent(d);

    switch (d.type) {
      case "aichat:message":
        receiveLayer.handleMessage(d);
        break;
      case "aichat:content-relay":
        receiveLayer.handleContentRelay(d);
        break;
      case "aichat:tool":
        receiveLayer.handleTool(d);
        break;
      case "aichat:interaction":
        receiveLayer.handleInteraction(d);
        break;
      case "aichat:read":
        receiveLayer.handleRead(d);
        break;
      case "aichat:history-response":
        var reqId = d.request_id;
        if (reqId && window.__aichatPendingHistory && window.__aichatPendingHistory[reqId]) {
          window.__aichatPendingHistory[reqId](d);
          delete window.__aichatPendingHistory[reqId];
        }
        break;
      case "aichat:rekey-response":
        var result = AichatCrypto.completeRekey(d.request_id, d.encrypted_key, d.nonce);
        if (result.success) {
          e2e.setChannelKey(result.encryptionKey);
          console.log("E2E: unwrapped encryption key from device");
        } else {
          console.warn("E2E: rekey failed —", result.error);
        }
        break;
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

    var manualRekeyBtn = document.getElementById("aichatManualRekeyBtn");
    if (manualRekeyBtn) {
      manualRekeyBtn.addEventListener("click", function () {
        manualRekeyBtn.disabled = true;
        manualRekeyBtn.textContent = "RE-KEYING\u2026";
        modal.classList.remove("is-active");
        e2e.manualRekey();
      });
    }
  }
})();
