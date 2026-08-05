const STORAGE_KEY = "mosaic-pathway-prototype-v1";
const profileFields = ["parent_name", "child_name", "child_age", "interests", "learning_needs", "leave_behind", "preserve", "add", "values"];
const state = loadState();
let resourceCount = 0;

const devProfile = {
  parent_name: "Bob Jones",
  child_name: "Sam Jones",
  child_age: "10",
  interests: "sports, gaming",
  learning_needs: "our family speaks spanish",
  leave_behind: "stress",
  preserve: "friendships",
  add: "more social skills",
  values: "autonomy",
};

function setStatus(message, mode) {
  const status = document.getElementById("status");
  status.textContent = message;
  status.dataset.mode = mode;
}

function setKnowledgeBaseStatus(source, count = 0) {
  const indicator = document.getElementById("database-status");
  if (source === "supabase") {
    indicator.textContent = `Knowledge base: Supabase · ${count} resources`;
    indicator.dataset.source = "supabase";
  } else if (source === "csv") {
    indicator.textContent = `Knowledge base: Local CSV · ${count} resources`;
    indicator.dataset.source = "csv";
  } else {
    indicator.textContent = "Knowledge base: Unavailable";
    indicator.dataset.source = "error";
  }
}

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
  const age = Number.parseInt(profile.child_age, 10);
  profile.child_age = Number.isInteger(age) && age >= 0 && age <= 30 ? age : profile.child_age;
  return profile;
}

function hydrateProfile() {
  for (const field of profileFields) {
    const value = state.profile[field];
    document.getElementById(field).value = Array.isArray(value) ? value.join(", ") : value ?? "";
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

function isWebUrl(value) {
  return /^https?:\/\//i.test(value || "");
}

function appendCitationText(parent, text, sources = []) {
  const sourceMap = sources instanceof Map
    ? sources
    : new Map(sources.map(source => [source.id, source]));
  const citationPattern = /\[?(R\d{3})\]?/g;
  let cursor = 0;
  for (const match of text.matchAll(citationPattern)) {
    parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
    const resource = sourceMap.get(match[1]);
    const link = document.createElement("a");
    link.className = "resource-citation";
    link.textContent = match[0];
    link.href = resource && isWebUrl(resource.source_url)
      ? resource.source_url
      : `#source-${match[1]}`;
    if (resource && isWebUrl(resource.source_url)) {
      link.target = "_blank";
      link.rel = "noreferrer";
    }
    parent.appendChild(link);
    cursor = match.index + match[0].length;
  }
  parent.appendChild(document.createTextNode(text.slice(cursor)));
}

function addMessage(role, text, save = true, sources = []) {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = role === "assistant" ? "M" : "You";
  const content = document.createElement("div");
  for (const paragraph of text.split(/\n\n+/)) {
    const p = document.createElement("p");
    appendCitationText(p, paragraph, sources);
    content.appendChild(p);
  }
  article.append(avatar, content);
  document.getElementById("messages").appendChild(article);
  article.scrollIntoView({ behavior: "smooth", block: "nearest" });
  if (save) {
    state.history.push({ role, content: text, sources });
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
    const article = card.querySelector(".source-card");
    article.id = `source-${source.id}`;
    const sourceId = card.querySelector(".source-id");
    sourceId.textContent = source.id;
    sourceId.href = isWebUrl(source.source_url) ? source.source_url : `#source-${source.id}`;
    card.querySelector(".source-type").textContent = source.content_type;
    const link = card.querySelector(".source-title");
    link.textContent = source.title;
    link.href = isWebUrl(source.source_url) ? source.source_url : `#source-${source.id}`;
    card.querySelector(".source-summary").textContent = source.summary;
    container.appendChild(card);
  }
}

function setBusy(button, busy, label) {
  if (!button.dataset.originalLabel) button.dataset.originalLabel = button.textContent;
  button.disabled = busy;
  button.textContent = busy ? label : button.dataset.originalLabel;
}

let pathwayProgressTimers = [];

function clearPathwayProgressTimers() {
  for (const timer of pathwayProgressTimers) window.clearTimeout(timer);
  pathwayProgressTimers = [];
}

function setPathwayStage(label, target, duration = 600) {
  const stage = document.getElementById("loading-stage");
  const progress = document.querySelector(".loading-progress");
  const fill = document.querySelector(".loading-progress-fill");
  const reducedMotion = globalThis.matchMedia("(prefers-reduced-motion: reduce)").matches;

  stage.classList.remove("is-visible");
  const textDelay = reducedMotion ? 0 : 180;
  const stageTimer = window.setTimeout(() => {
    stage.textContent = label;
    stage.classList.add("is-visible");
  }, textDelay);
  pathwayProgressTimers.push(stageTimer);

  fill.style.transitionDuration = `${reducedMotion ? 0 : duration}ms`;
  fill.style.width = `${target}%`;
  progress.setAttribute("aria-valuenow", String(target));
}

function beginPathwayLoading() {
  const overlay = document.getElementById("pathway-loading");
  const stage = document.getElementById("loading-stage");
  const progress = document.querySelector(".loading-progress");
  const fill = document.querySelector(".loading-progress-fill");
  clearPathwayProgressTimers();
  document.body.classList.add("is-pathway-loading");
  overlay.hidden = false;
  overlay.setAttribute("aria-hidden", "false");
  stage.textContent = "Preparing family information";
  stage.classList.add("is-visible");
  fill.style.transitionDuration = "0ms";
  fill.style.width = "0%";
  progress.setAttribute("aria-valuenow", "0");

  const initialProgressTimer = window.setTimeout(() => {
    fill.style.transitionDuration = globalThis.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "0ms"
      : "850ms";
    fill.style.width = "12%";
    progress.setAttribute("aria-valuenow", "12");
  }, 60);
  pathwayProgressTimers.push(initialProgressTimer);

  const stages = [
    { delay: 1000, label: "Finding relevant Mosaic resources", target: 30, duration: 1800 },
    { delay: 3000, label: "Writing your personalized two-week plan", target: 80, duration: 45000 },
    { delay: 48000, label: "Checking the activities and recommendations", target: 92, duration: 12000 },
    { delay: 60000, label: "Formatting your pathway document", target: 97, duration: 18000 },
  ];
  for (const item of stages) {
    const timer = window.setTimeout(
      () => setPathwayStage(item.label, item.target, item.duration),
      item.delay,
    );
    pathwayProgressTimers.push(timer);
  }
}

function hidePathwayLoading() {
  const overlay = document.getElementById("pathway-loading");
  clearPathwayProgressTimers();
  document.body.classList.remove("is-pathway-loading");
  overlay.hidden = true;
  overlay.setAttribute("aria-hidden", "true");
}

async function finishPathwayLoading(success) {
  clearPathwayProgressTimers();
  if (!success) {
    hidePathwayLoading();
    return;
  }
  setPathwayStage("Your pathway is ready", 100, 450);
  await new Promise(resolve => window.setTimeout(resolve, 650));
  hidePathwayLoading();
}

document.getElementById("profile-form").addEventListener("input", () => {
  persistProfile();
  updateProfileCompletion({ invalidateConfirmation: true });
});

document.getElementById("dev-override").addEventListener("click", () => {
  for (const field of profileFields) {
    document.getElementById(field).value = devProfile[field];
  }
  persistProfile();
  updateProfileCompletion({ invalidateConfirmation: true });
  document.getElementById("enter-details").focus();
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
    addMessage("assistant", result.message, true, result.sources);
    renderSources(result.sources);
    document.getElementById("sources-panel").open = true;
    if (result.mode === "claude") {
      setStatus(`Claude active · response grounded in ${result.sources.length} sources`, "claude");
    } else {
      setStatus(`Demo fallback · retrieval active · ${resourceCount} resources`, "demo");
    }
  } catch (error) {
    pending.remove();
    addMessage("assistant", `I couldn’t complete that request. ${error.message}`, false);
  } finally {
    setBusy(button, false);
    input.focus();
  }
});

let pathwayCitationSourceMap = new Map();

function appendTextElement(parent, tag, text, className = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  appendCitationText(element, String(text ?? ""), pathwayCitationSourceMap);
  parent.appendChild(element);
  return element;
}

function stripAudiencePrefix(text, fullName, firstName) {
  const names = [...new Set([fullName, firstName].filter(Boolean))]
    .map(name => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!names.length) return text;
  return String(text).replace(
    new RegExp(`^\\s*For\\s+(?:${names.join("|")})\\s*:\\s*`, "i"),
    "",
  );
}

function createPlanPage(pageNumber, label) {
  const page = document.createElement("section");
  page.className = "pathway-page";

  const header = document.createElement("header");
  header.className = "plan-header";
  const logo = document.querySelector(".brand-logo").cloneNode(true);
  logo.className = "plan-logo";
  header.appendChild(logo);
  appendTextElement(header, "p", label, "plan-kicker");
  page.appendChild(header);

  const footer = document.createElement("footer");
  footer.className = "plan-footer";
  appendTextElement(footer, "span", "Mosaic · Independent Meaningful Learning");
  appendTextElement(footer, "span", `${pageNumber} / 3`);
  page.appendChild(footer);
  return page;
}

function addResourceCard(parent, resource) {
  const card = document.createElement("article");
  card.className = "plan-resource";
  const hasWebUrl = isWebUrl(resource.source_url);
  const title = document.createElement(hasWebUrl ? "a" : "strong");
  title.textContent = resource.title;
  if (hasWebUrl) {
    title.href = resource.source_url;
    title.target = "_blank";
    title.rel = "noreferrer";
  }
  card.appendChild(title);
  appendTextElement(card, "p", resource.why_it_fits);
  const metadata = document.createElement("small");
  const reference = document.createElement("a");
  reference.className = "resource-reference";
  reference.textContent = resource.id;
  reference.href = hasWebUrl ? resource.source_url : "#sources-panel";
  if (hasWebUrl) {
    reference.target = "_blank";
    reference.rel = "noreferrer";
  }
  metadata.append(reference, document.createTextNode(` · ${resource.content_type || "Mosaic resource"}`));
  card.appendChild(metadata);
  parent.appendChild(card);
}

function renderPathway(pathway) {
  const sheet = document.getElementById("pathway-sheet");
  sheet.replaceChildren();
  const citationSources = pathway.citation_sources || [
    ...(pathway.resources || []),
    ...(pathway.community ? [pathway.community] : []),
  ];
  pathwayCitationSourceMap = new Map(
    citationSources.map(source => [source.id, source]),
  );
  const family = pathway.family;
  const childFirstName = family.child_first_name || family.child_name.split(/\s+/)[0];
  const parentFirstName = family.parent_first_name || family.parent_name.split(/\s+/)[0];
  const ageText = family.child_age === null ? "" : `, age ${family.child_age}`;

  const welcomePage = createPlanPage(1, "Your starting point");
  const welcomeContent = document.createElement("div");
  welcomeContent.className = "plan-content welcome-page";
  appendTextElement(welcomeContent, "h1", "Your Mosaic Two-Week Plan");
  appendTextElement(
    welcomeContent,
    "p",
    `Made for the ${family.family_name} — ${family.child_name}${ageText}`,
    "plan-subtitle",
  );
  const welcomePanel = document.createElement("section");
  welcomePanel.className = "plan-panel plan-panel-welcome";
  appendTextElement(welcomePanel, "p", "Family Welcome", "plan-section-label");
  appendTextElement(welcomePanel, "p", pathway.family_welcome, "plan-lead");
  welcomeContent.appendChild(welcomePanel);

  if (pathway.learner_support.show) {
    const support = document.createElement("section");
    support.className = "plan-panel support-panel";
    appendTextElement(support, "h2", pathway.learner_support.heading);
    appendTextElement(support, "p", pathway.learner_support.message);
    welcomeContent.appendChild(support);
  }

  const welcomeGrid = document.createElement("div");
  welcomeGrid.className = "welcome-grid";
  const guide = document.createElement("section");
  guide.className = "plan-panel guide-panel";
  appendTextElement(guide, "h2", "Book Your Guide");
  appendTextElement(guide, "p", pathway.guide_preparation);
  appendTextElement(guide, "span", "Bring these observations to a Mosaic Guide conversation.", "plan-action-label");
  welcomeGrid.appendChild(guide);

  const community = document.createElement("section");
  community.className = "plan-panel community-panel";
  appendTextElement(community, "h2", "Join Your Community");
  addResourceCard(community, pathway.community);
  welcomeGrid.appendChild(community);
  welcomeContent.appendChild(welcomeGrid);

  const howItWorks = document.createElement("section");
  howItWorks.className = "how-it-works";
  appendTextElement(howItWorks, "h2", "How This Works");
  const steps = document.createElement("ol");
  for (const step of [
    "Treat each day as a suggested activity, not an assignment.",
    "Notice energy, connection, and questions more than completion.",
    "Change the plan whenever your family's experience suggests a better next step.",
  ]) appendTextElement(steps, "li", step);
  howItWorks.appendChild(steps);
  welcomeContent.appendChild(howItWorks);
  welcomePage.insertBefore(welcomeContent, welcomePage.querySelector(".plan-footer"));
  sheet.appendChild(welcomePage);

  const weeksPage = createPlanPage(2, "Your two-week rhythm");
  const weeksContent = document.createElement("div");
  weeksContent.className = "plan-content weeks-page";
  appendTextElement(weeksContent, "h1", `${family.family_name.replace(/ family$/i, "")}’s Two Weeks`);
  appendTextElement(weeksContent, "p", "Ten gentle suggested activities, shaped by what your family shared.", "plan-subtitle");
  for (const week of pathway.weeks) {
    const weekSection = document.createElement("section");
    weekSection.className = "week-section";
    const weekHeading = document.createElement("div");
    weekHeading.className = "week-heading";
    appendTextElement(weekHeading, "span", `Week ${week.week_number}`, "week-number");
    appendTextElement(weekHeading, "h2", week.theme);
    appendTextElement(weekHeading, "p", week.introduction);
    weekSection.appendChild(weekHeading);
    const dayGrid = document.createElement("div");
    dayGrid.className = "day-grid";
    for (const day of week.days) {
      const card = document.createElement("article");
      card.className = "day-card";
      appendTextElement(card, "span", `Day ${day.day}`, "day-number");
      appendTextElement(card, "h3", day.title);
      appendTextElement(card, "p", `For ${childFirstName}`, "person-label child-label");
      appendTextElement(
        card,
        "p",
        stripAudiencePrefix(day.child_activity, family.child_name, childFirstName),
        "child-activity",
      );
      appendTextElement(card, "p", `For ${parentFirstName}`, "parent-label");
      appendTextElement(
        card,
        "p",
        stripAudiencePrefix(day.parent_prompt, family.parent_name, parentFirstName),
        "parent-prompt",
      );
      dayGrid.appendChild(card);
    }
    weekSection.appendChild(dayGrid);
    weeksContent.appendChild(weekSection);
  }
  weeksPage.insertBefore(weeksContent, weeksPage.querySelector(".plan-footer"));
  sheet.appendChild(weeksPage);

  const nextPage = createPlanPage(3, "Resources for what comes next");
  const nextContent = document.createElement("div");
  nextContent.className = "plan-content next-page";
  appendTextElement(nextContent, "h1", "Keep Going");
  appendTextElement(nextContent, "p", "A few places to explore, reflect, and connect after the plan begins.", "plan-subtitle");
  const resourcesGrid = document.createElement("div");
  resourcesGrid.className = "keep-going-grid";
  for (const [section, heading] of [["watch_explore", "Watch & Explore"], ["reading_corner", "Reading Corner"]]) {
    const resourceSection = document.createElement("section");
    resourceSection.className = "plan-panel resource-group";
    appendTextElement(resourceSection, "h2", heading);
    pathway.resources.filter(resource => resource.section === section).forEach(resource => addResourceCard(resourceSection, resource));
    resourcesGrid.appendChild(resourceSection);
  }
  nextContent.appendChild(resourcesGrid);

  const wobbleSection = document.createElement("section");
  wobbleSection.className = "wobble-section";
  appendTextElement(wobbleSection, "h2", "When It Wobbles");
  const wobbleGrid = document.createElement("div");
  wobbleGrid.className = "wobble-grid";
  for (const wobble of pathway.when_it_wobbles) {
    const card = document.createElement("article");
    appendTextElement(card, "h3", wobble.moment);
    appendTextElement(card, "p", wobble.response);
    wobbleGrid.appendChild(card);
  }
  wobbleSection.appendChild(wobbleGrid);
  nextContent.appendChild(wobbleSection);

  const nextSteps = document.createElement("section");
  nextSteps.className = "plan-panel next-steps-panel";
  appendTextElement(nextSteps, "p", "What Comes Next", "plan-section-label");
  appendTextElement(nextSteps, "p", pathway.what_comes_next, "plan-lead");
  nextContent.appendChild(nextSteps);
  nextPage.insertBefore(nextContent, nextPage.querySelector(".plan-footer"));
  sheet.appendChild(nextPage);

  const pathwayWrap = document.getElementById("pathway-wrap");
  const feedback = document.getElementById("feedback-form");
  const feedbackStart = feedback.getBoundingClientRect();
  pathwayWrap.hidden = false;
  pathwayWrap.insertAdjacentElement("afterend", feedback);
  feedback.classList.add("is-pathway-position");

  if (!globalThis.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const feedbackFinish = feedback.getBoundingClientRect();
    feedback.animate(
      [
        { opacity: 0, transform: `translateX(${feedbackStart.left - feedbackFinish.left}px) translateY(18px)` },
        { opacity: 1, transform: "translateX(0) translateY(0)" },
      ],
      { duration: 650, easing: "cubic-bezier(.22, .8, .25, 1)" },
    );
  }

  pathwayWrap.scrollIntoView({ behavior: "smooth" });
}

document.getElementById("generate-pathway").addEventListener("click", async event => {
  const button = event.currentTarget;
  let pathwayCreated = false;
  setBusy(button, true, "Creating…");
  beginPathwayLoading();
  try {
    const result = await api("/api/pathway", {
      profile: state.profile,
      history: state.history,
    });
    renderPathway(result.pathway);
    pathwayCreated = true;
    if (result.mode === "claude") {
      setStatus("Claude active · personalized pathway generated", "claude");
    } else {
      setStatus(`Demo fallback · pathway template · ${resourceCount} resources`, "demo");
    }
  } catch (error) {
    addMessage("assistant", `I couldn’t create the pathway. ${error.message}`, false);
  } finally {
    await finishPathwayLoading(pathwayCreated);
    setBusy(button, false);
  }
});

document.getElementById("print-pathway").addEventListener("click", () => window.print());

document.getElementById("delete-data").addEventListener("click", async () => {
  await api("/api/delete", { session_id: state.sessionId }).catch(() => null);
  localStorage.removeItem(STORAGE_KEY);
  location.reload();
});

let selectedFeedbackUsefulness = null;
const feedbackForm = document.getElementById("feedback-form");
const feedbackSubmit = document.getElementById("feedback-submit");

async function resetFeedbackForm() {
  const startHeight = feedbackForm.getBoundingClientRect().height;
  selectedFeedbackUsefulness = null;
  for (const choice of document.querySelectorAll(".feedback-choice")) {
    choice.setAttribute("aria-pressed", "false");
  }
  document.getElementById("feedback-notes").value = "";
  document.getElementById("feedback-details").hidden = true;
  document.getElementById("feedback-status").textContent = "";
  feedbackSubmit.disabled = true;

  if (globalThis.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const endHeight = feedbackForm.getBoundingClientRect().height;
  const animation = feedbackForm.animate(
    [
      { height: `${startHeight}px` },
      { height: `${endHeight}px` },
    ],
    { duration: 450, easing: "cubic-bezier(.22, .8, .25, 1)" },
  );
  await animation.finished.catch(() => null);
}

async function showFeedbackConfirmation(message) {
  const overlay = document.getElementById("feedback-confirmation");
  document.getElementById("feedback-confirmation-message").textContent = message;
  overlay.hidden = false;
  overlay.classList.remove("is-visible", "is-hiding");
  requestAnimationFrame(() => overlay.classList.add("is-visible"));
  await new Promise(resolve => window.setTimeout(resolve, 4000));
  overlay.classList.add("is-hiding");
  const fadeDuration = globalThis.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 450;
  await Promise.all([
    new Promise(resolve => window.setTimeout(resolve, fadeDuration)),
    resetFeedbackForm(),
  ]);
  overlay.hidden = true;
  overlay.classList.remove("is-visible", "is-hiding");
}

document.querySelectorAll(".feedback-choice").forEach(button => {
  button.addEventListener("click", () => {
    selectedFeedbackUsefulness = button.dataset.useful === "true";
    for (const choice of document.querySelectorAll(".feedback-choice")) {
      choice.setAttribute("aria-pressed", String(choice === button));
    }
    document.getElementById("feedback-details").hidden = false;
    feedbackSubmit.disabled = false;
    document.getElementById("feedback-status").textContent = "";
  });
});

feedbackForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (selectedFeedbackUsefulness === null) return;
  const notes = document.getElementById("feedback-notes").value.trim();
  const status = document.getElementById("feedback-status");
  setBusy(feedbackSubmit, true, "Submitting…");
  try {
    const result = await api("/api/feedback", {
      session_id: state.sessionId,
      useful: selectedFeedbackUsefulness,
      notes,
    });
    const message = result.storage === "supabase"
      ? "Thank you. Your feedback was sent to Mosaic staff."
      : "Thank you. Your feedback was saved to the local fallback database.";
    setBusy(feedbackSubmit, false);
    await showFeedbackConfirmation(message);
  } catch (error) {
    setBusy(feedbackSubmit, false);
    status.textContent = error.message;
  }
});

async function initialize() {
  hydrateProfile();
  updateProfileCompletion();
  for (const message of state.history) addMessage(message.role, message.content, false, message.sources || []);
  try {
    const health = await fetch("/api/health").then(response => response.json());
    resourceCount = health.resource_count;
    setKnowledgeBaseStatus(health.knowledge_base_source, resourceCount);
    console.info(`[Mosaic RAG] Knowledge base source: ${health.knowledge_base_source}`);
    console.info(`[Mosaic RAG] Resources available: ${resourceCount}`);
    if (health.mode === "claude") {
      setStatus("Claude connected · RAG active", "claude");
    } else {
      setStatus("Demo mode · retrieval active", "demo");
    }
  } catch {
    console.error("[Mosaic RAG] Could not read knowledge-base status from the server.");
    setKnowledgeBaseStatus("error");
    setStatus("Server unavailable", "error");
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
