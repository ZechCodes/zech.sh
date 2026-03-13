// AichatCrypto — pure crypto module for E2E encryption (no DOM, no fetch)
window.AichatCrypto = (function () {
  var naclReady = typeof nacl !== "undefined" && typeof nacl.util !== "undefined";
  var channelKey = null; // Uint8Array once set
  var pendingRekeys = {}; // requestId → transport key Uint8Array

  // --- localStorage persistence ---

  function getStoredKey() {
    var b64 = localStorage.getItem("aichat:device_master_key");
    if (b64 && naclReady) {
      try { return nacl.util.decodeBase64(b64); } catch (e) {}
    }
    return null;
  }

  function persistKey(keyBytes) {
    if (naclReady) {
      localStorage.setItem("aichat:device_master_key", nacl.util.encodeBase64(keyBytes));
    }
  }

  // Load key from localStorage on init
  channelKey = getStoredKey();

  // --- Core crypto ---

  function encrypt(plaintext) {
    if (!channelKey || !naclReady) return null;
    try {
      var msg = nacl.util.decodeUTF8(plaintext);
      var nonce = nacl.randomBytes(24);
      var ct = nacl.secretbox(msg, nonce, channelKey);
      return {
        encrypted_payload: nacl.util.encodeBase64(ct),
        nonce: nacl.util.encodeBase64(nonce),
      };
    } catch (e) {
      console.warn("E2E: encrypt failed", e);
      return null;
    }
  }

  function decrypt(ciphertextB64, nonceB64) {
    if (!channelKey || !naclReady) return null;
    try {
      var ct = nacl.util.decodeBase64(ciphertextB64);
      var nonce = nacl.util.decodeBase64(nonceB64);
      var plain = nacl.secretbox.open(ct, nonce, channelKey);
      if (!plain) return null;
      return nacl.util.encodeUTF8(plain);
    } catch (e) {
      console.warn("E2E: decrypt failed", e);
      return null;
    }
  }

  function decryptEvent(d) {
    // Decrypt aichat:message events
    if (d.encrypted_payload && d.nonce) {
      var plain = decrypt(d.encrypted_payload, d.nonce);
      if (plain) {
        try {
          var payload = JSON.parse(plain);
          d.content = payload.content || "";
          d.attachments = payload.attachments || [];
        } catch (e) {
          d.content = plain;
        }
        d._decrypted = true;
      } else if (channelKey) {
        d.content = "";
        d._pendingDecrypt = true;
        d._keyMismatch = true;
      } else {
        d.content = "";
        d._pendingDecrypt = true;
      }
    }
    // Decrypt tool descriptions
    if (d.encrypted_description && d.description_nonce) {
      var desc = decrypt(d.encrypted_description, d.description_nonce);
      if (!desc && channelKey) d._keyMismatch = true;
      d.description = desc || "[encrypted]";
    }
    return d;
  }

  // --- Key exchange (rekey) ---

  function initiateRekey(deviceX25519PublicB64) {
    if (!naclReady || !deviceX25519PublicB64) {
      return Promise.reject(new Error("nacl not ready or no device public key"));
    }

    var browserKP = nacl.box.keyPair();
    var devicePub = nacl.util.decodeBase64(deviceX25519PublicB64);
    var sharedSecret = nacl.scalarMult(browserKP.secretKey, devicePub);

    var salt = new TextEncoder().encode("aichat-device-key");
    var info = new TextEncoder().encode("v1");

    return crypto.subtle.importKey("raw", sharedSecret, "HKDF", false, ["deriveBits"])
      .then(function (keyMaterial) {
        return crypto.subtle.deriveBits(
          { name: "HKDF", hash: "SHA-256", salt: salt, info: info },
          keyMaterial, 256
        );
      })
      .then(function (derived) {
        var transportKey = new Uint8Array(derived);
        var requestId = "rk-" + Date.now() + "-" + Math.random().toString(36).substr(2, 6);

        pendingRekeys[requestId] = transportKey;
        console.log("E2E: derived transport key from ECDH (request_id=" + requestId + ")");

        return {
          requestId: requestId,
          browserPublicB64: nacl.util.encodeBase64(browserKP.publicKey),
        };
      });
  }

  function completeRekey(requestId, encryptedKeyB64, nonceB64) {
    var transportKey = pendingRekeys[requestId];
    if (!transportKey || !naclReady) {
      return { success: false, error: "no pending rekey for request_id=" + requestId };
    }
    delete pendingRekeys[requestId];

    if (!encryptedKeyB64 || !nonceB64) {
      return { success: false, error: "missing encrypted_key or nonce" };
    }

    try {
      var ek_ct = nacl.util.decodeBase64(encryptedKeyB64);
      var ek_nonce = nacl.util.decodeBase64(nonceB64);
      var unwrapped = nacl.secretbox.open(ek_ct, ek_nonce, transportKey);
      if (!unwrapped) {
        return { success: false, error: "unwrap returned null — transport key mismatch" };
      }
      var encKeyB64 = nacl.util.encodeUTF8(unwrapped);
      var encKey = nacl.util.decodeBase64(encKeyB64);
      return { success: true, encryptionKey: encKey };
    } catch (ex) {
      return { success: false, error: "unwrap exception: " + ex.message };
    }
  }

  function cancelRekey(requestId) {
    delete pendingRekeys[requestId];
  }

  // --- Key management ---

  function setKey(keyBytes) {
    channelKey = keyBytes;
    persistKey(keyBytes);
  }

  function clearKey() {
    localStorage.removeItem("aichat:device_master_key");
    channelKey = null;
  }

  function hasKey() {
    return !!channelKey;
  }

  function isReady() {
    return naclReady;
  }

  return {
    encrypt: encrypt,
    decrypt: decrypt,
    decryptEvent: decryptEvent,
    initiateRekey: initiateRekey,
    completeRekey: completeRekey,
    cancelRekey: cancelRekey,
    setKey: setKey,
    clearKey: clearKey,
    hasKey: hasKey,
    isReady: isReady,
  };
})();
