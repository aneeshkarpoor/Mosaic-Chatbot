const STORAGE_KEY = "mosaic-pathway-prototype-v1";
const profileFields = ["ages", "interests", "learning_needs", "leave_behind", "preserve", "add", "values"];
const state = loadState();

function newSessionId() {
  return globalThis.crypto?.randomUUID?.() || `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function loadState() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return {
      sessionId: saved?.sessionId || newSessionId(),
      history: saved?.history || [],
      profile: saved?.profile || {},
      profileConfirmed: saved?.profileConfirmed === true,
    };
  } catch {
    return { sessionId: newSessionId(), history: [], profile: {}, profileConfirmed: false };
  }
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function readProfile() {
  const profile = {};
  for (const field of profileFields) profile[field] = document.getElementById(field).value.trim();
  profile.ages = (profile.ages.match(/\d+/g) || []).map(Number).filter(age => age >= 0 && age <= 30);
  return profile;
}

function hydrateProfile() {
  for (const field of profileFields) {
    const value = state.profile[field];
    document.getElementById(field).value = Array.isArray(value) ? value.join(", ") : value || "";
  }
}

function persistProfile() {
  state.profile = readProfile();
  saveState();
}

function isProfileComplete() {
  return profileFields.every(field => document.getElementById(field).value.trim().length > 0);
}

function setAssistantEnabled(enabled) {
  const chatInput = document.getElementById("chat-input");
  chatInput.disabled = !enabled;
  chatInput.placeholder = enabled
    ? "For example: How might we begin without creating another rigid schedule?"
    : "Complete and confirm Your family details first";
  document.querySelector("#chat-form button").disabled = !enabled;
  document.getElementById("generate-pathway").disabled = !enabled;
}

function setWorkspaceState(confirmed, { animate = false } = {}) {
  const workspace = document.querySelector(".workspace");
  const intake = document.querySelector(".intake");
  const conversation = document.querySelector(".conversation");
  const start = animate && confirmed ? intake.getBoundingClientRect() : null;

  workspace.classList.toggle("is-confirmed", confirmed);
  conversation.hidden = !confirmed;

  if (!start || globalThis.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const finish = intake.getBoundingClientRect();
  intake.animate(
    [
      { transform: `translateX(${start.left - finish.left}px)` },
      { transform: "translateX(0)" },
    ],
    { duration: 650, easing: "cubic-bezier(.22, .8, .25, 1)" },
  );
  conversation.animate(
    [
      { opacity: 0, transform: "translateX(36px)" },
      { opacity: 1, transform: "translateX(0)" },
    ],
    { duration: 520, delay: 120, easing: "ease-out", fill: "both" },
  );
}

function updateProfileCompletion({ invalidateConfirmation = false, animateWorkspace = false } = {}) {
  for (const field of profileFields) {
    const input = document.getElementById(field);
    input.classList.toggle("is-complete", input.value.trim().length > 0);
  }
  if (invalidateConfirmation) state.profileConfirmed = false;
  const complete = isProfileComplete();
  document.getElementById("enter-details").disabled = !complete;
  document.getElementById("profile-progress").textContent = complete
    ? "All fields are complete. Confirm these details to continue."
    : "Complete all fields to continue.";
  setAssistantEnabled(complete && state.profileConfirmed);
  setWorkspaceState(complete && state.profileConfirmed, { animate: animateWorkspace });
  saveState();
}

function showConfirmation() {
  const toast = document.getElementById("confirmation-toast");
  toast.hidden = false;
  toast.classList.remove("is-showing");
  requestAnimationFrame(() => toast.classList.add("is-showing"));
}

async function api(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "The request could not be completed.");
  return body;
}

function addMessage(role, text, save = true) {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = role === "assistant" ? "M" : "You";
  const content = document.createElement("div");
  for (const paragraph of text.split(/\n\n+/)) {
    const p = document.createElement("p");
    p.textContent = paragraph;
    content.appendChild(p);
  }
  article.append(avatar, content);
  document.getElementById("messages").appendChild(article);
  article.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (save) {
    state.history.push({ role, content: text });
    state.history = state.history.slice(-8);
    saveState();
  }
  return article;
}

function renderSources(sources) {
  const container = document.getElementById("sources");
  container.replaceChildren();
  for (const source of sources) {
    const card = document.getElementById("source-template").content.cloneNode(true);
    card.querySelector(".source-id").textContent = source.id;
    card.querySelector(".source-type").textContent = source.content_type;
    const link = card.querySelector(".source-title");
    link.textContent = source.title;
    link.href = source.source_url;
    card.querySelector(".source-summary").textContent = source.summary;
    container.appendChild(card);
  }
}

function setBusy(button, busy, label) {
  if (!button.dataset.originalLabel) button.dataset.originalLabel = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? label : button.dataset.originalLabel;
}

document.getElementById("profile-form").addEventListener("input", () => {
  persistProfile();
  updateProfileCompletion({ invalidateConfirmation: true });
});

document.getElementById("enter-details").addEventListener("click", () => {
  persistProfile();
  if (!isProfileComplete()) return;
  state.profileConfirmed = true;
  updateProfileCompletion({ animateWorkspace: true });
  showConfirmation();
  const reducedMotion = globalThis.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelector(".workspace").scrollIntoView({
    behavior: reducedMotion ? "auto" : "smooth",
    block: "start",
  });
  window.setTimeout(() => {
    document.getElementById("chat-input").focus({ preventScroll: true });
  }, reducedMotion ? 0 : 700);
});

document.getElementById("confirmation-toast").addEventListener("animationend", event => {
  event.currentTarget.hidden = true;
  event.currentTarget.classList.remove("is-showing");
});

document.getElementById("chat-form").addEventListener("submit", async event => {
  event.preventDefault();
  const input = document.getElementById("chat-input");
  const button = event.currentTarget.querySelector("button");
  const message = input.value.trim();
  if (!message) return;
  addMessage("user", message);
  input.value = "";
  setBusy(button, true, "Thinking…");
  const pending = addMessage("assistant", "Looking through Mosaic’s library…", false);
  try {
    const result = await api("/api/chat", { message, profile: state.profile, history: state.history.slice(0, -1) });
    pending.remove();
    addMessage("assistant", result.message);
    renderSources(result.sources);
    document.getElementById("sources-panel").open = true;
    if (result.mode === "demo") document.getElementById("status").textContent = "Demo mode · retrieval active · Claude key not set";
  } catch (error) {
    pending.remove();
    addMessage("assistant", `I couldn’t complete that request. ${error.message}`, false);
  } finally {
    setBusy(button, false);
    input.focus();
  }
});

function appendTextElement(parent, tag, text, className = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  parent.appendChild(element);
}

function renderPathway(pathway) {
  const sheet = document.getElementById("pathway-sheet");
  sheet.replaceChildren();
  const header = document.createElement("header");
  appendTextElement(header, "p", "Mosaic · Family Learning Pathway", "eyebrow");
  appendTextElement(header, "h1", pathway.title);
  appendTextElement(header, "p", pathway.reflection, "reflection");
  sheet.appendChild(header);

  const rhythmSection = document.createElement("section");
  appendTextElement(rhythmSection, "h2", "A starting rhythm for two weeks");
  const rhythmGrid = document.createElement("div");
  rhythmGrid.className = "rhythm-grid";
  for (const item of pathway.rhythm) {
    const card = document.createElement("article");
    appendTextElement(card, "h3", item.when);
    appendTextElement(card, "p", item.practice);
    appendTextElement(card, "small", item.why_it_fits);
    rhythmGrid.appendChild(card);
  }
  rhythmSection.appendChild(rhythmGrid);
  sheet.appendChild(rhythmSection);

  const recommendations = document.createElement("section");
  appendTextElement(recommendations, "h2", "Resources that may meet you here");
  for (const resource of pathway.resources) {
    const card = document.createElement("article");
    card.className = "path-resource";
    const title = document.createElement("a");
    title.href = resource.source_url;
    title.target = "_blank";
    title.rel = "noreferrer";
    title.textContent = resource.title;
    card.appendChild(title);
    appendTextElement(card, "p", resource.why_it_fits);
    appendTextElement(card, "small", `${resource.id} · ${resource.content_type} · ${resource.age_range}`);
    recommendations.appendChild(card);
  }
  sheet.appendChild(recommendations);

  const community = document.createElement("section");
  community.className = "community-callout";
  appendTextElement(community, "p", "A community connection", "eyebrow");
  const communityLink = document.createElement("a");
  communityLink.href = pathway.community.source_url;
  communityLink.target = "_blank";
  communityLink.rel = "noreferrer";
  communityLink.textContent = pathway.community.title;
  community.appendChild(communityLink);
  appendTextElement(community, "p", pathway.community.why_it_fits);
  sheet.appendChild(community);
  appendTextElement(sheet, "p", pathway.closing_note, "closing-note");

  document.getElementById("pathway-wrap").hidden = false;
  document.getElementById("pathway-wrap").scrollIntoView({ behavior: "smooth" });
}

document.getElementById("generate-pathway").addEventListener("click", async event => {
  const button = event.currentTarget;
  setBusy(button, true, "Creating…");
  try {
    const result = await api("/api/pathway", {
      profile: state.profile,
      history: state.history,
    });
    renderPathway(result.pathway);
    if (result.mode === "demo") document.getElementById("status").textContent = "Demo mode · pathway template + retrieved resources";
  } catch (error) {
    addMessage("assistant", `I couldn’t create the pathway. ${error.message}`, false);
  } finally {
    setBusy(button, false);
  }
});

document.getElementById("print-pathway").addEventListener("click", () => window.print());

document.getElementById("delete-data").addEventListener("click", async () => {
  await api("/api/delete", { session_id: state.sessionId }).catch(() => null);
  localStorage.removeItem(STORAGE_KEY);
  location.reload();
});

document.querySelectorAll("[data-useful]").forEach(button => {
  button.addEventListener("click", async () => {
    const useful = button.dataset.useful === "true";
    const notes = document.getElementById("feedback-notes").value.trim();
    const status = document.getElementById("feedback-status");
    try {
      await api("/api/feedback", { session_id: state.sessionId, useful, notes });
      status.textContent = "Thank you. Your feedback was saved locally for this prototype.";
    } catch (error) {
      status.textContent = error.message;
    }
  });
});

async function initialize() {
  hydrateProfile();
  updateProfileCompletion();
  for (const message of state.history) addMessage(message.role, message.content, false);
  try {
    const health = await fetch("/api/health").then(response => response.json());
    document.getElementById("status").textContent = health.mode === "claude"
      ? `Claude connected · ${health.resource_count} approved resources`
      : `Demo mode · ${health.resource_count} approved resources`;
  } catch {
    document.getElementById("status").textContent = "Server unavailable";
  }
}

function initializeCarousel() {
  const images = Array.from(document.querySelectorAll(".carousel-image"));
  const dots = Array.from(document.querySelectorAll(".carousel-dots span"));
  if (images.length < 2) return;

  let activeIndex = 0;
  window.setInterval(() => {
    images[activeIndex].classList.remove("is-active");
    dots[activeIndex]?.classList.remove("is-active");
    activeIndex = (activeIndex + 1) % images.length;
    images[activeIndex].classList.add("is-active");
    dots[activeIndex]?.classList.add("is-active");
  }, 5000);
}

initializeCarousel();
initialize();
