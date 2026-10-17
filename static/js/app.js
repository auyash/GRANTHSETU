/**
 * NFSU Library Self-Service Kiosk - Client Application
 * Connects to FastAPI Backend with Koha 26 SIP2 Integration
 */

// Global State
let activePatron = null;
let currentMode = "ISSUE"; // "ISSUE" | "RETURN" | "RENEW"
let sessionItems = [];
let idleTimer = null;
let secondsRemaining = 30;
let audioCtx = null;
let alarmInterval = null;

// Audio Synthesizer (Web Audio API)
function initAudio() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  }
}

function playBeep(freq = 1200, duration = 0.12) {
  try {
    initAudio();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
    gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + duration);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start();
    osc.stop(audioCtx.currentTime + duration);
  } catch (e) {
    console.warn("Audio warning:", e);
  }
}

function playSuccessChime() {
  try {
    initAudio();
    const now = audioCtx.currentTime;
    [523.25, 659.25, 783.99, 1046.5].forEach((freq, idx) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(freq, now + idx * 0.08);
      gain.gain.setValueAtTime(0.18, now + idx * 0.08);
      gain.gain.exponentialRampToValueAtTime(0.01, now + idx * 0.08 + 0.3);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(now + idx * 0.08);
      osc.stop(now + idx * 0.08 + 0.3);
    });
  } catch (e) {
    console.warn("Audio warning:", e);
  }
}

function playSecurityAlarmSiren(durationMs = 4000) {
  try {
    initAudio();
    if (alarmInterval) clearInterval(alarmInterval);

    let highTone = false;
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = "sawtooth";
    gain.gain.setValueAtTime(0.28, audioCtx.currentTime);
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start();

    alarmInterval = setInterval(() => {
      osc.frequency.setValueAtTime(highTone ? 1300 : 750, audioCtx.currentTime);
      highTone = !highTone;
    }, 220);

    setTimeout(() => {
      if (alarmInterval) {
        clearInterval(alarmInterval);
        alarmInterval = null;
      }
      try {
        osc.stop();
        gain.disconnect();
      } catch (err) {}
    }, durationMs);
  } catch (e) {
    console.warn("Audio warning:", e);
  }
}

// Inactivity Auto-Logout Timer (30s)
function resetIdleTimer() {
  secondsRemaining = 30;
  if (!activePatron) return;

  const timerDisplay = document.getElementById("sessionTimerText");
  if (timerDisplay) timerDisplay.textContent = `${secondsRemaining}s`;

  if (idleTimer) clearInterval(idleTimer);
  idleTimer = setInterval(() => {
    secondsRemaining--;
    if (timerDisplay) timerDisplay.textContent = `${secondsRemaining}s`;

    if (secondsRemaining <= 0) {
      clearInterval(idleTimer);
      idleTimer = null;
      alert("Session expired due to inactivity. Returning to home screen.");
      endSession();
    }
  }, 1000);
}

// Global user interaction listener to reset timer
["click", "keydown", "touchstart"].forEach(evt => {
  window.addEventListener(evt, () => {
    if (activePatron) resetIdleTimer();
  });
});

// UI Navigation
function showScreen(screenId) {
  document.querySelectorAll(".kiosk-screen").forEach(s => s.classList.remove("active"));
  const target = document.getElementById(screenId);
  if (target) target.classList.add("active");

  const timerBox = document.getElementById("sessionTimerContainer");
  if (screenId === "screenCirculation") {
    if (timerBox) timerBox.style.display = "flex";
    resetIdleTimer();
  } else {
    if (timerBox) timerBox.style.display = "none";
    if (idleTimer) clearInterval(idleTimer);
  }
}

function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.add("open");
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) modal.classList.remove("open");
}

// Patron Authentication Handlers
async function authenticatePatron(patronId, method = "CARD") {
  try {
    playBeep(900, 0.1);
    const resp = await fetch("/api/auth/patron", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patron_id: patronId, method: method })
    });
    const data = await resp.json();

    if (!resp.ok) {
      alert(data.detail || data.message || "Patron authentication failed.");
      return;
    }

    activePatron = data.patron;
    sessionItems = [];
    sessionStorage.removeItem("granthsetu_session_items");
    playSuccessChime();
    updatePatronDisplay();
    await refreshTray();
    renderSessionHistory();
    showScreen("screenCirculation");
  } catch (err) {
    console.error("Auth error:", err);
    alert("Could not communicate with kiosk server.");
  }
}

// Update Patron Display & 2-Book Borrowing Limit Badge
function updatePatronDisplay() {
  if (!activePatron) return;

  document.getElementById("displayPatronName").textContent = activePatron.name || "NFSU Patron";
  document.getElementById("displayPatronId").textContent = `ID: ${activePatron.patron_id} | ${activePatron.auth_method || 'CARD'}`;

  const chargedCount = activePatron.charged_count || 0;
  const limitBadge = document.getElementById("limitBadge");
  const limitText = document.getElementById("limitText");
  const limitNotice = document.getElementById("limitNotice");
  const limitBanner = document.getElementById("limitAlertBanner");

  limitText.textContent = `Borrowed: ${chargedCount} / 2 Books`;

  if (chargedCount >= 2) {
    limitBadge.className = "limit-badge danger";
    limitNotice.textContent = "0 Slots Available (Limit Reached)";
    limitBanner.style.display = "flex";
  } else if (chargedCount === 1) {
    limitBadge.className = "limit-badge warning";
    limitNotice.textContent = "1 Slot Available";
    limitBanner.style.display = "none";
  } else {
    limitBadge.className = "limit-badge";
    limitNotice.textContent = "2 Slots Available";
    limitBanner.style.display = "none";
  }

  // Adjust placeholder & label based on current mode
  setCirculationMode(currentMode);
}

// Circulation Mode Switcher (ISSUE, RETURN, RENEW)
function setCirculationMode(mode) {
  currentMode = mode;
  document.querySelectorAll(".circ-tab").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.mode === mode);
  });

  const scanLabel = document.getElementById("scanInputLabel");
  const scanBtn = document.getElementById("btnExecuteScan");
  const trayTitle = document.getElementById("trayTitle");

  if (mode === "ISSUE") {
    scanLabel.textContent = "Scan book barcode to ISSUE (Check out):";
    scanBtn.textContent = "Issue Item";
    trayTitle.textContent = "Items Issued This Session";
    // Check if limit is reached
    if (activePatron && activePatron.charged_count >= 2) {
      scanBtn.disabled = true;
      scanBtn.style.opacity = "0.5";
      scanBtn.style.cursor = "not-allowed";
    } else {
      scanBtn.disabled = false;
      scanBtn.style.opacity = "1";
      scanBtn.style.cursor = "pointer";
    }
  } else if (mode === "RETURN") {
    scanLabel.textContent = "Scan book barcode to RETURN (Drop off):";
    scanBtn.textContent = "Return Item";
    trayTitle.textContent = "Items Returned This Session";
    scanBtn.disabled = false;
    scanBtn.style.opacity = "1";
    scanBtn.style.cursor = "pointer";
  } else if (mode === "RENEW") {
    scanLabel.textContent = "Scan book barcode to RENEW (Extend due date):";
    scanBtn.textContent = "Renew Item";
    trayTitle.textContent = "Items Renewed This Session";
    scanBtn.disabled = false;
    scanBtn.style.opacity = "1";
    scanBtn.style.cursor = "pointer";
  }
}

// Operator Reconciliation Modal Handler
function openReconciliationModal(data) {
  playSecurityAlarmSiren(1500);
  const titleEl = document.getElementById("reconciliationTitle");
  const detailEl = document.getElementById("reconciliationDetail");
  const metaEl = document.getElementById("reconciliationMeta");
  const stepsEl = document.getElementById("reconciliationSteps");

  if (titleEl) titleEl.textContent = data.error_code === "UNKNOWN_AFTER_SEND" ? "Network Ambiguity Detected (UNKNOWN_AFTER_SEND)" : "Circulation Failure";
  if (detailEl) detailEl.textContent = data.message || "A network interruption occurred. Safe circulation rules engaged.";
  if (metaEl) {
    metaEl.innerHTML = `
      <strong>Patron ID:</strong> ${activePatron?.patron_id || 'N/A'} &nbsp;|&nbsp;
      <strong>Item:</strong> ${data.item_id || data.barcode || 'N/A'} &nbsp;|&nbsp;
      <strong>Security State:</strong> ${data.rfid_security || 'REMAINS ARMED (AFI 0xC2)'}
    `;
  }
  if (stepsEl && data.reconciliation_instructions && data.reconciliation_instructions.length > 0) {
    stepsEl.innerHTML = data.reconciliation_instructions.map(step => `<li>${step}</li>`).join("");
  }
  openModal("modalReconciliation");
}

// Process Item (Instant Issue / Return / Renew)
async function processItem(barcode) {
  if (!barcode) {
    alert("Please scan or enter a book barcode.");
    return;
  }

  barcode = barcode.trim();
  playBeep(1400, 0.1);

  let endpoint = "/api/circulation/checkout";
  if (currentMode === "RETURN") endpoint = "/api/circulation/checkin";
  if (currentMode === "RENEW") endpoint = "/api/circulation/renew";

  try {
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ barcode: barcode })
    });
    const data = await resp.json();

    if (!resp.ok || !data.success) {
      playBeep(400, 0.3);
      if (data.error_code === "UNKNOWN_AFTER_SEND" || data.reconciliation_instructions) {
        openReconciliationModal(data);
        return;
      }
      alert(data.message || data.detail || "Transaction failed.");
      return;
    }

    playSuccessChime();

    // Update active patron stats
    if (data.patron) {
      activePatron = data.patron;
      updatePatronDisplay();
    }

    // Add to session history
    sessionItems.unshift({
      mode: currentMode,
      barcode: barcode,
      title: data.title || barcode,
      due_date: data.due_date || (currentMode === "RETURN" ? "Returned" : "14 Days"),
      security: currentMode === "ISSUE" ? "DISARMED (AFI 0x07)" : "ARMED (AFI 0xC2)",
      securityClass: currentMode === "ISSUE" ? "disarmed" : "armed"
    });
    saveSessionTray();
    renderSessionHistory();
    await refreshTray();

    document.getElementById("inputBookBarcode").value = "";
    document.getElementById("inputBookBarcode").focus();

  } catch (err) {
    console.error("Circulation error:", err);
    alert("Network error processing transaction.");
  }
}

// Add Item to Pre-Checkout Tray
async function addToTray(barcode) {
  if (!barcode) {
    alert("Please scan or enter a book barcode.");
    return;
  }
  barcode = barcode.trim();
  playBeep(1200, 0.08);

  try {
    const resp = await fetch("/api/tray/add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ barcode: barcode })
    });
    const data = await resp.json();

    if (!resp.ok || !data.success) {
      playBeep(400, 0.25);
      alert(data.message || data.detail || "Could not add item to tray.");
      return;
    }

    playBeep(1500, 0.1);
    document.getElementById("inputBookBarcode").value = "";
    document.getElementById("inputBookBarcode").focus();
    await refreshTray();
  } catch (err) {
    console.error("Tray add error:", err);
    alert("Network error communicating with kiosk tray service.");
  }
}

// Remove Item from Tray
async function removeFromTray(barcode) {
  try {
    await fetch("/api/tray/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ barcode: barcode })
    });
    await refreshTray();
  } catch (err) {
    console.error("Tray remove error:", err);
  }
}

// Clear Entire Tray
async function clearTray() {
  try {
    await fetch("/api/tray/clear", { method: "POST" });
    await refreshTray();
  } catch (err) {
    console.error("Tray clear error:", err);
  }
}

// Batch Checkout Entire Tray
async function checkoutTray() {
  playBeep(1300, 0.1);
  try {
    const resp = await fetch("/api/tray/checkout", { method: "POST" });
    const data = await resp.json();

    const unknownResult = (data.results || []).find(r => r.error_code === "UNKNOWN_AFTER_SEND" || r.reconciliation_instructions);
    if (unknownResult) {
      openReconciliationModal(unknownResult);
    }

    if (data.patron) {
      activePatron = data.patron;
      updatePatronDisplay();
    }

    (data.results || []).forEach(res => {
      if (res.success) {
        sessionItems.unshift({
          mode: "ISSUE",
          barcode: res.item_id || res.title,
          title: res.title || res.item_id,
          due_date: res.due_date || "14 Days",
          security: "DISARMED (AFI 0x07)",
          securityClass: "disarmed"
        });
      }
    });

    saveSessionTray();
    renderSessionHistory();
    await refreshTray();

    if (data.success) {
      playSuccessChime();
      alert(`Successfully checked out ${data.processed_count} book(s) from tray!`);
    } else if (!unknownResult) {
      playBeep(400, 0.25);
      alert(`Tray checkout stopped: ${data.results?.[data.results.length - 1]?.message || "Some items could not be processed."}`);
    }
  } catch (err) {
    console.error("Tray checkout error:", err);
    alert("Network error processing tray checkout.");
  }
}

// Refresh and Render Pre-Checkout Tray
async function refreshTray() {
  try {
    const resp = await fetch("/api/tray");
    if (!resp.ok) return;
    const data = await resp.json();
    const tray = data.tray || [];
    const countBadge = document.getElementById("trayCountBadge");
    const listContainer = document.getElementById("trayItemsList");
    const btnCheckout = document.getElementById("btnCheckoutTray");

    if (countBadge) countBadge.textContent = `${tray.length} in Tray`;
    if (btnCheckout) btnCheckout.disabled = (tray.length === 0);

    if (listContainer) {
      if (tray.length === 0) {
        listContainer.innerHTML = `
          <div class="tray-empty-state">
            <span class="empty-icon">📖</span>
            <p>Tray is currently empty. Click "Add to Tray" or scan items to stage them for simultaneous checkout (Max 2 books).</p>
          </div>`;
      } else {
        listContainer.innerHTML = tray.map(item => `
          <div class="tray-item-row">
            <div class="item-main-info">
              <h5>${item.title || item.barcode}</h5>
              <p>Barcode: <strong>${item.barcode}</strong> | Ready for Checkout</p>
            </div>
            <div class="tray-item-actions">
              <button class="btn-remove-tray-item" data-barcode="${item.barcode}">Remove ✕</button>
            </div>
          </div>
        `).join("");

        listContainer.querySelectorAll(".btn-remove-tray-item").forEach(btn => {
          btn.addEventListener("click", () => {
            removeFromTray(btn.dataset.barcode);
          });
        });
      }
    }
  } catch (err) {
    console.warn("Could not refresh tray:", err);
  }
}

// Persist Session History to SessionStorage
function saveSessionTray() {
  try {
    sessionStorage.setItem("granthsetu_session_items", JSON.stringify(sessionItems));
  } catch (e) {
    console.warn("Could not save session items to sessionStorage", e);
  }
}

// Render Completed Session Transactions History
function renderSessionHistory() {
  const container = document.getElementById("historyItemsList");
  const countBadge = document.getElementById("historyCountBadge");
  if (!container || !countBadge) return;

  countBadge.textContent = `${sessionItems.length} Completed`;

  if (sessionItems.length === 0) {
    container.innerHTML = `<p class="history-empty-text">No confirmed transactions completed in this session yet.</p>`;
    return;
  }

  container.innerHTML = sessionItems.map(item => `
    <div class="history-item-row">
      <div>
        <h6>${item.title}</h6>
        <p>Barcode: <strong>${item.barcode}</strong> | Action: <strong>${item.mode}</strong> | Due: ${item.due_date}</p>
      </div>
      <span class="eas-badge ${item.securityClass}">
        ${item.security}
      </span>
    </div>
  `).join("");
}

// End Session
async function endSession() {
  try {
    await fetch("/api/session/end", { method: "POST" });
  } catch (e) {}
  activePatron = null;
  sessionItems = [];
  sessionStorage.removeItem("granthsetu_session_items");
  if (idleTimer) clearInterval(idleTimer);
  showScreen("screenHome");
}

// Exit Gate Simulator
async function testExitGate(barcode) {
  const visualZone = document.getElementById("gateVisualZone");
  const statusScreen = document.getElementById("gateStatusScreen");
  const lightLeft = document.getElementById("gateLightLeft");
  const lightRight = document.getElementById("gateLightRight");
  const stateIcon = document.getElementById("gateStateIcon");
  const heading = document.getElementById("gateStateHeading");
  const message = document.getElementById("gateStateMessage");

  // Reset
  lightLeft.className = "gate-sensor-light";
  lightRight.className = "gate-sensor-light";
  statusScreen.className = "gate-status-screen";

  try {
    const resp = await fetch("/api/gate/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ barcode: barcode })
    });
    const data = await resp.json();

    if (data.alarm_triggered) {
      // 🚨 ALARM TRIGGERED!
      lightLeft.className = "gate-sensor-light red";
      lightRight.className = "gate-sensor-light red";
      statusScreen.className = "gate-status-screen alarm";
      stateIcon.textContent = "🚨";
      heading.textContent = "SECURITY BREACH DETECTED!";
      heading.style.color = "#FF8585";
      message.textContent = `Item ${barcode} is ARMED (AFI 0xC2). Unauthorized removal from library!`;
      playSecurityAlarmSiren(3500);
    } else {
      // ✅ SAFE PASSAGE!
      lightLeft.className = "gate-sensor-light green";
      lightRight.className = "gate-sensor-light green";
      statusScreen.className = "gate-status-screen safe";
      stateIcon.textContent = "✅";
      heading.textContent = "SAFE PASSAGE VERIFIED";
      heading.style.color = "#2EC4B6";
      message.textContent = `Item ${barcode} is DISARMED (AFI 0x07). Legitimate checkout confirmed.`;
      playSuccessChime();
    }
  } catch (err) {
    console.error("Gate test error:", err);
  }
}

// Security Audit Trail Fetch
async function loadSecurityAudit() {
  try {
    const resp = await fetch("/api/security/audit");
    const data = await resp.json();

    const tbody = document.getElementById("auditLogTbody");
    if (!data.audit_trail || data.audit_trail.length === 0) {
      tbody.innerHTML = `<tr><td colspan="5">No audit events recorded yet.</td></tr>`;
      return;
    }

    tbody.innerHTML = data.audit_trail.map(row => `
      <tr>
        <td>${row.timestamp.split("T")[1].substring(0, 8)}</td>
        <td><strong>${row.category}</strong></td>
        <td>${row.action}</td>
        <td>${row.details}</td>
        <td><code>${row.hash}</code></td>
      </tr>
    `).join("");
  } catch (err) {
    console.error("Audit load error:", err);
  }
}

// System Status Monitor (Live vs Mock indicator)
let currentKohaMode = "LIVE";

async function updateSystemStatus() {
  try {
    const resp = await fetch("/api/kiosk/status");
    if (!resp.ok) return;
    const data = await resp.json();
    const koha = data.koha_status || {};
    currentKohaMode = (koha.mode || "LIVE").toUpperCase();

    const dot = document.getElementById("kohaStatusDot");
    const text = document.getElementById("kohaStatusText");
    const badge = document.getElementById("kohaModeBadge");
    const sealText = document.querySelector(".koha-verified-seal span:last-child");

    if (currentKohaMode === "LIVE") {
      if (koha.connected) {
        if (dot) dot.className = "status-indicator live";
        if (text) text.textContent = `Koha 26 SIP2: Connected (${koha.server_ip}:${koha.server_port})`;
        if (badge) {
          badge.textContent = "STAGING";
          badge.className = "koha-mode-badge live";
        }
        if (sealText) sealText.textContent = "All transactions validated through Koha SIP2 in real time";
      } else {
        if (dot) dot.className = "status-indicator error";
        if (text) text.textContent = `Koha 26 SIP2: Disconnected (${koha.last_error || 'Offline'})`;
        if (badge) {
          badge.textContent = "DISCONNECTED";
          badge.className = "koha-mode-badge error";
        }
        if (sealText) sealText.textContent = "Koha server unreachable - Live circulation halted for safety";
      }
    } else {
      // Explicit MOCK Mode
      if (dot) dot.className = "status-indicator warning";
      if (text) text.textContent = "Koha SIP2: Simulation Mode (Offline Mock Engine)";
      if (badge) {
        badge.textContent = "DEMO MODE";
        badge.className = "koha-mode-badge warning";
      }
      if (sealText) sealText.textContent = "Demo Mode - Offline simulation only (Not recorded on live Koha)";
    }
  } catch (err) {
    console.warn("Could not query system status:", err);
  }
}

// Thermal Slip Modal Populate
function openReceiptModal() {
  if (!activePatron) return;

  document.getElementById("recPatronName").textContent = activePatron.name || "Patron";
  document.getElementById("recPatronId").textContent = activePatron.patron_id || "NFSU";
  document.getElementById("recDate").textContent = new Date().toLocaleString();

  const container = document.getElementById("receiptItemsList");
  const modeWatermark = currentKohaMode === "MOCK"
    ? `<div style="color: #b7094c; font-weight: 800; font-size: 0.8rem; text-align: center; border: 1px dashed #b7094c; padding: 4px; margin-bottom: 8px;">*** SIMULATED RECORD (OFFLINE DEMO) ***</div>`
    : `<div style="color: #2a9d8f; font-weight: 800; font-size: 0.8rem; text-align: center; border: 1px solid #2a9d8f; padding: 4px; margin-bottom: 8px;">*** OFFICIAL KOHA 26 RECORD ***</div>`;

  if (sessionItems.length === 0) {
    container.innerHTML = `${modeWatermark}<p>No items in current session.</p>`;
  } else {
    container.innerHTML = modeWatermark + sessionItems.map(item => `
      <div style="margin-bottom: 6px;">
        <div><strong>[${item.mode}]</strong> ${item.title}</div>
        <div style="font-size: 0.8rem;">Barcode: ${item.barcode} | Due: ${item.due_date}</div>
      </div>
    `).join("");
  }

  openModal("modalPrintSlip");
}

// Download Official PDF Circulation Slip
function downloadPdfReceipt() {
  if (!activePatron) {
    alert("Please authenticate patron first.");
    return;
  }
  const url = `/api/circulation/receipt/pdf?mode=${encodeURIComponent(currentMode)}`;
  window.open(url, "_blank");
}

// Load Hardware Abstraction Layer (HAL) Diagnostics
async function loadHardwareStatus() {
  try {
    const resp = await fetch("/api/hardware/status");
    if (!resp.ok) return;
    const data = await resp.json();
    const topbarText = document.getElementById("hardwareTopbarText");
    const grid = document.getElementById("hardwareCardsGrid");

    const isSim = data.is_simulated;
    if (topbarText) {
      topbarText.textContent = isSim ? "HAL: Simulated" : "HAL: Physical";
    }

    if (grid) {
      grid.innerHTML = (data.devices || []).map(dev => {
        const isConnected = dev.status === "CONNECTED";
        const isSimDev = dev.status === "SIMULATED";
        const badgeClass = isConnected ? "connected" : (isSimDev ? "simulated" : "error");
        const details = dev.details || {};
        const iface = details.interface || "Standard";
        const portInfo = details.port ? ` (${details.port})` : "";
        const caps = details.supported_symbologies || details.standards || (details.frequency ? [details.frequency, "ISO 15693", "ISO 28560"] : []);
        return `
          <div class="hw-device-card ${badgeClass}">
            <div class="hw-card-top">
              <span class="hw-device-name">${dev.name || dev.device_type.replace(/_/g, ' ')}</span>
              <div class="hw-badges">
                <span class="hw-status-badge ${badgeClass}">${dev.status}</span>
              </div>
            </div>
            <div class="hw-driver-info">
              <strong>Interface / Model:</strong> ${iface}${portInfo}<br>
              <strong>Driver Adapter:</strong> <code>${dev.adapter_class || 'BaseHardwareAdapter'}</code> (Simulation: ${dev.is_simulated ? 'Active' : 'Physical Hardware Connected'})
            </div>
            <div class="hw-capabilities">
              ${caps.map(f => `<span class="hw-cap-chip">${f}</span>`).join("")}
            </div>
          </div>
        `;
      }).join("");
    }
  } catch (err) {
    console.warn("Could not load hardware status:", err);
  }
}

// Face Authentication Modal & Camera Stream
let videoStream = null;

async function startCamera() {
  try {
    const video = document.getElementById("webcamVideo");
    videoStream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
    video.srcObject = videoStream;
  } catch (err) {
    console.warn("Webcam not accessible, fallback simulator ready:", err);
  }
}

function stopCamera() {
  if (videoStream) {
    videoStream.getTracks().forEach(track => track.stop());
    videoStream = null;
  }
}

// Recover Session Tray & State after browser refresh
async function recoverSessionIfActive() {
  try {
    const resp = await fetch("/api/kiosk/status");
    if (!resp.ok) return;
    const data = await resp.json();
    if (data && data.active_patron) {
      activePatron = data.active_patron;
      try {
        const saved = sessionStorage.getItem("granthsetu_session_items");
        sessionItems = saved ? JSON.parse(saved) : [];
      } catch (e) {
        sessionItems = [];
      }
      updatePatronDisplay();
      await refreshTray();
      renderSessionHistory();
      showScreen("screenCirculation");
      resetIdleTimer();
    } else {
      sessionStorage.removeItem("granthsetu_session_items");
    }
  } catch (e) {
    console.debug("Session recovery check failed:", e);
  }
}

// Event Bindings
document.addEventListener("DOMContentLoaded", () => {
  // Initialize status polling, hardware diagnostics & recover active session
  updateSystemStatus();
  recoverSessionIfActive();
  loadHardwareStatus();
  setInterval(updateSystemStatus, 6000);

  // Action cards on Home Screen
  document.getElementById("btnActionIssue").addEventListener("click", () => {
    currentMode = "ISSUE";
    authenticatePatron("NFSU2024001", "QUICK_START");
  });

  document.getElementById("btnActionReturn").addEventListener("click", () => {
    currentMode = "RETURN";
    authenticatePatron("NFSU2024001", "QUICK_START");
  });

  document.getElementById("btnActionRenew").addEventListener("click", () => {
    currentMode = "RENEW";
    authenticatePatron("NFSU2024001", "QUICK_START");
  });

  // Topbar Buttons
  document.getElementById("btnSecurityAudit").addEventListener("click", () => {
    loadSecurityAudit();
    openModal("modalSecurityAudit");
  });

  document.getElementById("btnCloseAuditModal").addEventListener("click", () => {
    closeModal("modalSecurityAudit");
  });

  document.getElementById("btnGateSimulator").addEventListener("click", () => {
    openModal("modalGateSimulator");
  });

  document.getElementById("btnCloseGateModal").addEventListener("click", () => {
    closeModal("modalGateSimulator");
  });

  // Hardware Status Modal
  const btnHw = document.getElementById("btnHardwareStatus");
  if (btnHw) {
    btnHw.addEventListener("click", () => {
      loadHardwareStatus();
      openModal("modalHardwareStatus");
    });
  }
  const btnRefreshHw = document.getElementById("btnRefreshHardware");
  if (btnRefreshHw) btnRefreshHw.addEventListener("click", loadHardwareStatus);
  const btnCloseHw = document.getElementById("btnCloseHardwareModal");
  if (btnCloseHw) btnCloseHw.addEventListener("click", () => closeModal("modalHardwareStatus"));
  const btnCloseHwBtn = document.getElementById("btnCloseHardwareModalBtn");
  if (btnCloseHwBtn) btnCloseHwBtn.addEventListener("click", () => closeModal("modalHardwareStatus"));

  // Operator Reconciliation Modal
  const btnCloseReconcile = document.getElementById("btnCloseReconciliationModal");
  if (btnCloseReconcile) btnCloseReconcile.addEventListener("click", () => closeModal("modalReconciliation"));
  const btnAckReconcile = document.getElementById("btnAcknowledgeReconciliation");
  if (btnAckReconcile) btnAckReconcile.addEventListener("click", () => closeModal("modalReconciliation"));

  // Auth Tabs
  document.getElementById("tabFaceAuth").addEventListener("click", () => {
    startCamera();
    openModal("modalFaceAuth");
  });

  document.getElementById("btnCloseFaceModal").addEventListener("click", () => {
    stopCamera();
    closeModal("modalFaceAuth");
  });

  document.getElementById("tabCardAuth").addEventListener("click", () => {
    const cardId = prompt("Tap your MIFARE card or enter Smart Card UID:", "CARD_E3A59102");
    if (cardId) authenticatePatron(cardId, "MIFARE_CARD");
  });

  document.getElementById("tabBarcodeAuth").addEventListener("click", () => {
    const barcode = prompt("Scan Patron ID Barcode:", "NFSU2024002");
    if (barcode) authenticatePatron(barcode, "PATRON_BARCODE");
  });

  // Demonstration Quick-Scan Chips
  document.querySelectorAll(".dock-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      const patronId = chip.dataset.id;
      authenticatePatron(patronId, "DEMO_DOCK");
    });
  });

  // Circulation Mode Tabs
  document.querySelectorAll(".circ-tab").forEach(tab => {
    tab.addEventListener("click", () => {
      setCirculationMode(tab.dataset.mode);
    });
  });

  // Scanner & Tray Actions
  const btnAddToTray = document.getElementById("btnAddToTray");
  if (btnAddToTray) {
    btnAddToTray.addEventListener("click", () => {
      const barcode = document.getElementById("inputBookBarcode").value;
      addToTray(barcode);
    });
  }

  const btnExecuteScan = document.getElementById("btnExecuteScan");
  if (btnExecuteScan) {
    btnExecuteScan.addEventListener("click", () => {
      const barcode = document.getElementById("inputBookBarcode").value;
      processItem(barcode);
    });
  }

  const btnCheckoutTray = document.getElementById("btnCheckoutTray");
  if (btnCheckoutTray) {
    btnCheckoutTray.addEventListener("click", checkoutTray);
  }

  const btnClearTray = document.getElementById("btnClearTray");
  if (btnClearTray) {
    btnClearTray.addEventListener("click", clearTray);
  }

  document.getElementById("inputBookBarcode").addEventListener("keypress", (e) => {
    if (e.key === "Enter") {
      processItem(e.target.value);
    }
  });

  // Sample Book Chips
  document.querySelectorAll(".book-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      const barcode = chip.dataset.barcode;
      document.getElementById("inputBookBarcode").value = barcode;
      processItem(barcode);
    });
  });

  // Quick Switch to Return
  document.getElementById("btnQuickSwitchReturn").addEventListener("click", () => {
    setCirculationMode("RETURN");
  });

  // End Session Buttons
  document.getElementById("btnEndSession").addEventListener("click", endSession);
  document.getElementById("btnFinishSession").addEventListener("click", endSession);

  // Print Slips (Thermal & Digital PDF)
  document.getElementById("btnPrintSlip").addEventListener("click", openReceiptModal);
  document.getElementById("btnCloseReceiptModal").addEventListener("click", () => closeModal("modalPrintSlip"));
  document.getElementById("btnCloseSlipBtn").addEventListener("click", () => closeModal("modalPrintSlip"));

  const btnDownloadPdf = document.getElementById("btnDownloadPdfSlip");
  if (btnDownloadPdf) {
    btnDownloadPdf.addEventListener("click", downloadPdfReceipt);
  }

  // Gate Simulator Actions
  document.getElementById("btnPassThroughGate").addEventListener("click", () => {
    const bc = document.getElementById("gateTestBarcode").value;
    testExitGate(bc);
  });

  document.getElementById("btnTestDisarmedBook").addEventListener("click", () => {
    // BK-FOR-101 is disarmed
    document.getElementById("gateTestBarcode").value = "BK-FOR-101";
    testExitGate("BK-FOR-101");
  });

  document.getElementById("btnTestArmedBook").addEventListener("click", () => {
    // BK-TOX-501 is armed on shelf
    document.getElementById("gateTestBarcode").value = "BK-TOX-501";
    testExitGate("BK-TOX-501");
  });

  // Face Modal Actions
  document.getElementById("btnSimulateFace").addEventListener("click", async () => {
    try {
      const resp = await fetch("/api/auth/face", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ simulate_liveness: true, mock_choice: "FACE_01" })
      });
      const data = await resp.json();
      stopCamera();
      closeModal("modalFaceAuth");
      activePatron = data.patron_session.patron;
      sessionItems = [];
      sessionStorage.removeItem("granthsetu_session_items");
      playSuccessChime();
      updatePatronDisplay();
      await refreshTray();
      renderSessionHistory();
      showScreen("screenCirculation");
    } catch (err) {
      console.error(err);
    }
  });

  document.getElementById("btnVerifyFaceLive").addEventListener("click", async () => {
    const video = document.getElementById("webcamVideo");
    let base64 = null;
    try {
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth || 640;
      canvas.height = video.videoHeight || 480;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      base64 = canvas.toDataURL("image/jpeg", 0.7).split(",")[1];
    } catch (e) {}

    try {
      const resp = await fetch("/api/auth/face", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_base64: base64, simulate_liveness: true })
      });
      const data = await resp.json();
      stopCamera();
      closeModal("modalFaceAuth");
      activePatron = data.patron_session.patron;
      sessionItems = [];
      sessionStorage.removeItem("granthsetu_session_items");
      playSuccessChime();
      updatePatronDisplay();
      await refreshTray();
      renderSessionHistory();
      showScreen("screenCirculation");
    } catch (err) {
      console.error(err);
      alert("Face verification failed. Please try again or tap card.");
    }
  });
});
