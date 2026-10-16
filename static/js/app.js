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
    playSuccessChime();
    updatePatronDisplay();
    renderSessionTray();
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

// Process Item (Issue / Return / Renew)
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

    if (!resp.ok) {
      playBeep(400, 0.3);
      alert(data.message || data.detail || "Transaction failed.");
      return;
    }

    playSuccessChime();

    // Update active patron stats
    if (data.patron) {
      activePatron = data.patron;
      updatePatronDisplay();
    }

    // Add to session tray
    sessionItems.unshift({
      mode: currentMode,
      barcode: barcode,
      title: data.title || barcode,
      due_date: data.due_date || (currentMode === "RETURN" ? "Returned" : "14 Days"),
      security: currentMode === "ISSUE" ? "DISARMED (AFI 0x07)" : "ARMED (AFI 0xC2)",
      securityClass: currentMode === "ISSUE" ? "disarmed" : "armed"
    });

    renderSessionTray();
    document.getElementById("inputBookBarcode").value = "";
    document.getElementById("inputBookBarcode").focus();

  } catch (err) {
    console.error("Circulation error:", err);
    alert("Network error processing transaction.");
  }
}

// Render Session Tray Table
function renderSessionTray() {
  const container = document.getElementById("trayItemsList");
  const countBadge = document.getElementById("trayCountBadge");

  countBadge.textContent = `${sessionItems.length} Item(s)`;

  if (sessionItems.length === 0) {
    container.innerHTML = `
      <div class="tray-empty-state">
        <span class="empty-icon">📖</span>
        <p>No items processed yet in this session. Scan a book barcode above.</p>
      </div>`;
    return;
  }

  container.innerHTML = sessionItems.map(item => `
    <div class="tray-item-row">
      <div class="item-main-info">
        <h5>${item.title}</h5>
        <p>Barcode: <strong>${item.barcode}</strong> | Action: <strong>${item.mode}</strong> | Due: ${item.due_date}</p>
      </div>
      <div class="item-status-badges">
        <span class="eas-badge ${item.securityClass}">
          ${item.security}
        </span>
      </div>
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

// Thermal Slip Modal Populate
function openReceiptModal() {
  if (!activePatron) return;

  document.getElementById("recPatronName").textContent = activePatron.name || "Patron";
  document.getElementById("recPatronId").textContent = activePatron.patron_id || "NFSU";
  document.getElementById("recDate").textContent = new Date().toLocaleString();

  const container = document.getElementById("receiptItemsList");
  if (sessionItems.length === 0) {
    container.innerHTML = `<p>No items in current session.</p>`;
  } else {
    container.innerHTML = sessionItems.map(item => `
      <div style="margin-bottom: 6px;">
        <div><strong>[${item.mode}]</strong> ${item.title}</div>
        <div style="font-size: 0.8rem;">Barcode: ${item.barcode} | Due: ${item.due_date}</div>
      </div>
    `).join("");
  }

  openModal("modalPrintSlip");
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

// Event Bindings
document.addEventListener("DOMContentLoaded", () => {
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

  // Scanner Action
  document.getElementById("btnExecuteScan").addEventListener("click", () => {
    const barcode = document.getElementById("inputBookBarcode").value;
    processItem(barcode);
  });

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

  // Print Slip
  document.getElementById("btnPrintSlip").addEventListener("click", openReceiptModal);
  document.getElementById("btnCloseReceiptModal").addEventListener("click", () => closeModal("modalPrintSlip"));
  document.getElementById("btnCloseSlipBtn").addEventListener("click", () => closeModal("modalPrintSlip"));

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
      playSuccessChime();
      updatePatronDisplay();
      renderSessionTray();
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
      playSuccessChime();
      updatePatronDisplay();
      renderSessionTray();
      showScreen("screenCirculation");
    } catch (err) {
      console.error(err);
      alert("Face verification failed. Please try again or tap card.");
    }
  });
});
