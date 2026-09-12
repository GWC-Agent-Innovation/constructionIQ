const $ = (selector) => document.querySelector(selector);
const state = { sessionId: localStorage.getItem("constructiq-session"), busy: false, ready: false, hasModel: false, viewerUrl: null };
let progressTimer = null; // tracked at module scope so a bfcache restore can always clear it

const messages = $("#messages");
const input = $("#message-input");
const sendButton = $("#send-button");
const generationOverlay = $("#generation-overlay");
const generationOverlayImg = $("#generation-overlay-img");
const generationOverlayTitle = $("#generation-overlay-title");
const generationOverlayMessage = $("#generation-overlay-message");
const generationOverlayClose = $("#generation-overlay-close");

function showGenerationOverlay() {
  generationOverlay.classList.remove("hidden", "is-error");
  generationOverlayImg.src = "bot_reaction/working.png?v=4";
  generationOverlayTitle.textContent = "Your home is taking shape.";
  generationOverlayMessage.textContent = "Designing your 3D home… this can take a few minutes.";
  generationOverlayClose.classList.add("hidden");
}

function updateGenerationOverlayProgress(text) {
  generationOverlayMessage.textContent = text;
}

function showGenerationError(message) {
  generationOverlay.classList.remove("hidden");
  generationOverlay.classList.add("is-error");
  generationOverlayImg.src = "bot_reaction/error.png?v=4";
  generationOverlayTitle.textContent = "Something needs attention.";
  generationOverlayMessage.textContent = message;
  generationOverlayClose.classList.remove("hidden");
}

function hideGenerationOverlay() {
  generationOverlay.classList.add("hidden");
  generationOverlay.classList.remove("is-error");
}

generationOverlayClose.addEventListener("click", hideGenerationOverlay);

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

const BOT_REACTIONS = {
  idle: "bot_reaction/Speaking.png?v=4",
  listening: "bot_reaction/listning.png?v=4",
  thinking: "bot_reaction/working.png?v=4",
  speaking: "bot_reaction/Speaking.png?v=4",
  designing: "bot_reaction/working.png?v=4",
};

// Preload all bot reaction images for instant, smooth transitions
Object.values(BOT_REACTIONS).forEach((src) => {
  const img = new Image();
  img.src = src;
});

let speakingTimeout = null;

function assistantState(name, label) {
  document.body.dataset.state = name;
  const studio = $("#studio");
  if (studio) studio.dataset.state = name;
  const sidebar = $("#architect-sidebar");
  if (sidebar) sidebar.dataset.state = name;
  const panel = $(".chat-panel");
  if (panel) panel.dataset.state = name;
  const stateEl = $("#assistant-state");
  if (stateEl) stateEl.textContent = label;
  const sideDetail = $("#sidebar-status-detail");
  if (sideDetail) sideDetail.textContent = label;

  // Dynamically update the bot image to match the state!
  const botImg = $("#sidebar-bot-img");
  if (botImg && BOT_REACTIONS[name]) {
    const targetSrc = BOT_REACTIONS[name];
    if (botImg.getAttribute("src") !== targetSrc) {
      botImg.src = targetSrc;
    }
  }
}

function triggerBotReaction(type) {
  const wrapper = $("#bot-avatar-wrapper");
  if (!wrapper) return;
  if (type === "speaking") {
    wrapper.classList.add("react-speak");
    clearTimeout(speakingTimeout);
    speakingTimeout = setTimeout(() => {
      wrapper.classList.remove("react-speak");
    }, 3800);
  }
}

function addMessage(role, content, isError = false) {
  $("#welcome")?.remove();
  const node = $("#message-template").content.firstElementChild.cloneNode(true);
  node.classList.toggle("user", role === "user");
  node.classList.toggle("error", isError);
  const authorEl = node.querySelector(".message-author");
  if (authorEl) authorEl.textContent = role === "user" ? "You" : "AI Architect";
  const avatarEl = node.querySelector(".message-avatar");
  if (avatarEl && role !== "user") {
    avatarEl.innerHTML = `<img src="bot_reaction/Speaking.png?v=4" alt="AI Architect" />`;
  }
  node.querySelector(".bubble").textContent = content;
  messages.append(node);
  scrollMessagesToEnd();
}

function scrollMessagesToEnd() {
  messages.scrollTo({
    top: messages.scrollHeight,
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
  });
}

let generateCardNode = null;

// The "Generate 3D home" card lives IN the conversation itself - appended as
// its own message once the brief is complete, not pinned to the page as a
// separate banner/button. It's added once and updated in place (moved back
// to the end of the log) rather than duplicated on every reply.
function syncGenerateCard(summaryText) {
  if (!state.ready && !state.viewerUrl) {
    generateCardNode?.remove();
    generateCardNode = null;
    return;
  }
  $("#welcome")?.remove();
  if (!generateCardNode) {
    generateCardNode = $("#generate-card-template").content.firstElementChild.cloneNode(true);
    generateCardNode.querySelector(".generate-card-button").addEventListener("click", handleGenerate3D);
    generateCardNode.querySelector(".saved-model-link").addEventListener("click", event => {
      if (state.busy) event.preventDefault();
    });
  }
  if (summaryText) {
    generateCardNode.querySelector(".generate-card-summary").textContent = summaryText;
  }
  const hasSavedModel = Boolean(state.viewerUrl);
  generateCardNode.querySelector(".eyebrow").textContent = hasSavedModel ? "YOUR 3D HOME IS READY" : "BRIEF COMPLETE";
  generateCardNode.querySelector("p").textContent = hasSavedModel
    ? "Open your saved 3D home to view it or make changes with the editing chat."
    : "We will validate the layout, draw the plan, then build the interactive 3D house.";
  generateCardNode.querySelector(".generate-card-button").classList.toggle("hidden", hasSavedModel);
  const modelLink = generateCardNode.querySelector(".saved-model-link");
  modelLink.classList.toggle("hidden", !hasSavedModel);
  if (hasSavedModel) modelLink.href = state.viewerUrl;
  else modelLink.removeAttribute("href");
  modelLink.setAttribute("aria-disabled", String(state.busy));
  messages.append(generateCardNode); // (re)append: keeps it at the end of the log
  generateCardNode.querySelector(".generate-card-button").disabled = state.busy;
  scrollMessagesToEnd();
}

function setBusy(busy) {
  state.busy = busy;
  sendButton.disabled = busy || !state.sessionId;
  input.disabled = busy || !state.sessionId;
  const cardBtn = generateCardNode?.querySelector(".generate-card-button");
  if (cardBtn) cardBtn.disabled = busy || !state.ready;
  generateCardNode?.querySelector(".saved-model-link").setAttribute("aria-disabled", String(busy));
  $("#new-project").disabled = busy;
  $("#existing-projects-button").disabled = busy;
  $("#existing-projects-list").querySelectorAll("button").forEach(button => { button.disabled = busy; });
  $("#sidebar-new-project-btn") && ($("#sidebar-new-project-btn").disabled = busy);

  $(".chat-panel").setAttribute("aria-busy", String(busy));
  $("#quick-prompts").querySelectorAll("button").forEach(button => { button.disabled = busy || !state.sessionId; });
}

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : data.detail?.issues?.join("\n");
    const error = new Error(detail || "The design service could not complete that request.");
    error.status = response.status;
    throw error;
  }
  return data;
}

function setStudioMode() { $("#studio").className = "studio chat-only"; }

function updateBrief(session) {
  const spec = session.spec || {};
  $("#project-title").textContent = session.project_name || session.session_id;
  document.title = `${session.project_name || session.session_id} · ConstructIQ`;
  state.ready = Boolean(spec.plot_width_ft && spec.plot_length_ft && Number.isInteger(spec.num_bedrooms) && Number.isInteger(spec.num_bathrooms) && (spec.num_floors || 1) === 1);
  const facts = [];
  if (spec.plot_width_ft && spec.plot_length_ft) facts.push(`${spec.plot_width_ft} × ${spec.plot_length_ft} ft`);
  if (Number.isInteger(spec.num_bedrooms)) facts.push(`${spec.num_bedrooms} bedrooms`);
  if (Number.isInteger(spec.num_bathrooms)) facts.push(`${spec.num_bathrooms} bathrooms`);
  if (spec.entrance_facing) facts.push(`${spec.entrance_facing[0].toUpperCase()}${spec.entrance_facing.slice(1)} entrance`);


  // The "Generate 3D home" card is appended to the conversation itself by
  // the caller (right after the assistant's reply bubble), not here - this
  // just remembers the summary text and whether one belongs in the chat.
  state.factsSummary = facts.join(" · ");
}

function showViewer(viewer) {
  state.viewerUrl = viewer || null;
  const link = $("#open-viewer");
  link.classList.toggle("hidden", !viewer);
  if (viewer) link.href = viewer;
  else link.removeAttribute("href");
  state.hasModel = Boolean(viewer);
}

function displaySession(result) {
  state.sessionId = result.session.session_id;
  localStorage.setItem("constructiq-session", state.sessionId);
  updateBrief(result.session);
  setStudioMode();
  messages.replaceChildren();
  generateCardNode = null;
  for (const turn of result.session.history || []) addMessage(turn.role, turn.content);
  if (!result.session.history?.length) {
    addMessage("assistant", "Welcome! What are your plot's width and length in feet?");
  }
  showViewer(result.viewer_url);
  syncGenerateCard(state.factsSummary);
  $("#connection-dot").parentElement.classList.add("online");
  $("#connection-text").textContent = "Design service online";
  assistantState("idle", "Ready to listen");
}

async function startSession() {
  assistantState("thinking", "Preparing your chat");
  const result = await request("/sessions", {
    method: "POST", body: JSON.stringify({ project_name: "My Home" })
  });
  displaySession(result);
}

function closeProjectDropdown() {
  $("#existing-projects-dropdown").classList.add("hidden");
  $("#existing-projects-button").setAttribute("aria-expanded", "false");
}

async function showExistingProjects() {
  if (state.busy) return;
  const dropdown = $("#existing-projects-dropdown");
  if (!dropdown.classList.contains("hidden")) {
    closeProjectDropdown();
    return;
  }
  dropdown.classList.remove("hidden");
  $("#existing-projects-button").setAttribute("aria-expanded", "true");
  const status = $("#existing-projects-status");
  const list = $("#existing-projects-list");
  status.textContent = "Loading saved projects…";
  list.replaceChildren();
  try {
    const result = await request("/sessions");
    status.textContent = result.sessions?.length ? "Select a project to resume its chat." : "No saved projects yet.";
    for (const session of result.sessions || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "project-dropdown-item";
      button.disabled = state.busy;
      if (session.session_id === state.sessionId) button.setAttribute("aria-current", "true");
      const name = document.createElement("strong");
      name.textContent = session.project_name || session.session_id;
      const details = document.createElement("span");
      details.textContent = [session.plot_info, session.rooms_info,
        session.updated_at ? new Date(session.updated_at * 1000).toLocaleString() : null,
        session.session_id === state.sessionId ? "Current project" : null].filter(Boolean).join(" · ");
      button.append(name, details);
      button.addEventListener("click", async () => {
        if (state.busy) return;
        setBusy(true);
        status.textContent = "Opening project…";
        try {
          displaySession(await request(`/sessions/${encodeURIComponent(session.session_id)}`));
          closeProjectDropdown();
        } catch (error) {
          status.textContent = `Could not open project: ${error.message}`;
        } finally {
          setBusy(false);
          if (dropdown.classList.contains("hidden")) input.focus();
        }
      });
      list.appendChild(button);
    }
  } catch (error) {
    status.textContent = `Could not load projects: ${error.message}. Close and reopen to retry.`;
  }
}

$("#existing-projects-button").addEventListener("click", showExistingProjects);
document.addEventListener("click", event => {
  if (!$("#project-switcher").contains(event.target)) closeProjectDropdown();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !$("#existing-projects-dropdown").classList.contains("hidden")) {
    closeProjectDropdown();
    $("#existing-projects-button").focus();
  }
});

async function restoreOrStart() {
  setBusy(true);
  try {
    if (state.sessionId) {
      try {
        displaySession(await request(`/sessions/${state.sessionId}`));
        return;
      } catch (error) {
        if (error.status !== 404) throw error;
        state.sessionId = null;
        localStorage.removeItem("constructiq-session");
      }
    }
    await startSession();
  } catch (error) {
    assistantState("idle", "Unable to connect");
    $("#connection-text").textContent = "Design service unavailable";
    addMessage("assistant", error.message, true);
  } finally {
    setBusy(false);
  }
}
async function sendMessage(text) {
  if (!text.trim() || state.busy || !state.sessionId) return;
  addMessage("user", text.trim());
  input.value = "";
  setBusy(true);
  assistantState("thinking", "Analyzing your requirements…");
  try {
    const result = await request(`/sessions/${state.sessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({ message: text.trim() }),
    });
    updateBrief(result.session);
    showViewer(result.viewer_url);
    assistantState("speaking", "Sharing design thoughts…");
    triggerBotReaction("speaking");
    addMessage("assistant", result.reply);
    syncGenerateCard(state.factsSummary);
    window.setTimeout(() => {
      if (!state.busy && document.body.dataset.state === "speaking") {
        assistantState("idle", "Ready to listen");
      }
    }, 3600);
  } catch (error) {
    assistantState("idle", "Something needs attention");
    addMessage("assistant", error.message, true);
  } finally {
    setBusy(false);
    input.focus();
  }
}

/* --------------------------------------------------------------------------
   Event Listeners & Real-Time Typing Reaction
   -------------------------------------------------------------------------- */
$("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage(input.value);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage(input.value);
  }
});

let typingTimeout = null;
input.addEventListener("input", () => {
  if (state.busy) return;
  if (input.value.trim().length > 0) {
    assistantState("listening", "Listening to you…");
    clearTimeout(typingTimeout);
    typingTimeout = setTimeout(() => {
      if (!state.busy && document.body.dataset.state === "listening") {
        assistantState("idle", "Ready to listen");
      }
    }, 2400);
  } else {
    assistantState("idle", "Ready to listen");
  }
});

$("#quick-prompts").addEventListener("click", (event) => {
  if (event.target.matches("button")) sendMessage(event.target.dataset.prompt);
});

// Interactive 3D Parallax Tilt for Mascot Card
const botCard = $("#bot-card-frame");
if (botCard) {
  window.addEventListener("mousemove", (e) => {
    if (window.innerWidth < 720) return;
    const rect = botCard.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;
    const deltaX = (e.clientX - centerX) / (window.innerWidth / 2);
    const deltaY = (e.clientY - centerY) / (window.innerHeight / 2);
    const rotX = -deltaY * 8;
    const rotY = deltaX * 8;
    botCard.style.transform = `perspective(800px) rotateX(${rotX.toFixed(2)}deg) rotateY(${rotY.toFixed(2)}deg)`;
  });

  botCard.addEventListener("mouseleave", () => {
    botCard.style.transform = "perspective(800px) rotateX(0deg) rotateY(0deg)";
  });

  // Playful poke reaction on click
  botCard.addEventListener("click", () => {
    botCard.classList.add("bot-poked");
    assistantState("speaking", "Hello there! Ready to plan your space.");
    setTimeout(() => {
      botCard.classList.remove("bot-poked");
      if (!state.busy) assistantState("idle", "Ready to listen");
    }, 2000);
  });
}

async function newProject() {
  if (state.busy) return;
  setBusy(true);
  try {
    await startSession();
  } catch (error) {
    addMessage("assistant", error.message, true);
  } finally {
    setBusy(false);
    input.focus();
  }
}
$("#new-project").addEventListener("click", newProject);
$("#sidebar-new-project-btn").addEventListener("click", newProject);
// Generate 3D Home - shown as a blurred overlay on this same page, never a
// separate tab: the bot's "working" image while designing, "error" if it fails.
async function handleGenerate3D() {
  if (state.busy || !state.ready || !state.sessionId) return;
  setBusy(true);
  showGenerationOverlay();
  assistantState("designing", "Designing your 3D home…");
  const started = Date.now();
  progressTimer = window.setInterval(() => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    const progressText = `Designing your home… ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    updateGenerationOverlayProgress(progressText);
    const cardBtn = generateCardNode?.querySelector(".generate-card-button");
    if (cardBtn) cardBtn.textContent = progressText;
  }, 1000);
  const cardBtn = generateCardNode?.querySelector(".generate-card-button");
  if (cardBtn) cardBtn.textContent = "Designing your home…";

  try {
    const result = await request(`/sessions/${state.sessionId}/generate`, { method: "POST", body: JSON.stringify({ build_3d: true }) });
    const viewer = result.viewer_url;
    if (result.warnings?.some(warning => warning.startsWith("3D model build failed:"))) throw new Error(result.warnings.join("\n"));
    if (!viewer) throw new Error(result.warnings?.join("\n") || "The plan was created but the 3D viewer was not returned.");
    const versioned = new URL(viewer, window.location.href);
    versioned.searchParams.set("v", String(result.session.layout.version));
    const viewerUrl = versioned.href;
    showViewer(viewerUrl);
    syncGenerateCard(state.factsSummary);

    // Navigate this same tab straight to the 3D viewer - no new tab, no
    // inline preview to click through first. Clear the busy/overlay state
    // BEFORE navigating: the browser's back-forward cache can freeze this
    // page exactly as it looks at unload and restore it verbatim on a later
    // Back navigation (from the viewer's own Back button, in particular) -
    // without this, that restore would show "Designing your home…" stuck
    // forever, since no code re-runs on a bfcache restore.
    window.clearInterval(progressTimer);
    hideGenerationOverlay();
    setBusy(false);
    window.location.href = viewerUrl;
    return;
  } catch (error) {
    assistantState("idle", "Design needs an adjustment");
    showGenerationError(error.message);
    addMessage("assistant", error.message, true);
    syncGenerateCard(state.factsSummary); // move it back below the error, ready to try again
  } finally {
    window.clearInterval(progressTimer);
    setBusy(false);
    const cardBtnFinal = generateCardNode?.querySelector(".generate-card-button");
    if (cardBtnFinal) cardBtnFinal.textContent = "Generate 3D home ↗";
  }
}

// Theme Toggle & Persistence - Dark Theme Default
let savedTheme = localStorage.getItem("constructiq-theme");
if (!savedTheme || savedTheme === "light") {
  savedTheme = "dark";
  localStorage.setItem("constructiq-theme", "dark");
}
document.documentElement.setAttribute("data-theme", savedTheme);
const themeBtn = $("#theme-toggle");
if (themeBtn) {
  themeBtn.textContent = savedTheme === "dark" ? "☀️ Light" : "🌙 Dark";
  themeBtn.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("constructiq-theme", next);
    themeBtn.textContent = next === "dark" ? "☀️ Light" : "🌙 Dark";
  });
}

// A back/forward navigation - notably the 3D viewer's own Back button - can
// restore this exact page from the browser's cache precisely as it looked
// when we navigated away, without re-running any script. If that happened
// mid-generation (overlay visible, composer disabled), it would otherwise
// stay stuck that way forever, since nothing re-runs to clear it.
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  window.clearInterval(progressTimer);
  hideGenerationOverlay();
  setBusy(false);
  restoreOrStart();
});

// Run Initial Check
restoreOrStart();
