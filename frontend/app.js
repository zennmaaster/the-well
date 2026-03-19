/* ============================================================
   The Well — frontend app
   ============================================================ */

const WS_URL = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let tallies = { agree: 0, nuanced: 0, disagree: 0 };
let activeFrameId = null;

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const wsStatus       = document.getElementById("ws-status");
const frameDomain    = document.getElementById("frame-domain");
const noFrame        = document.getElementById("no-frame");
const frameBody      = document.getElementById("frame-body");
const frameClaim     = document.getElementById("frame-claim");
const frameEvidence  = document.getElementById("frame-evidence");
const commitFeedHdr  = document.getElementById("commit-feed-header");
const commitFeed     = document.getElementById("commit-feed");
const tallyAgree     = document.getElementById("tally-agree");
const tallyNuanced   = document.getElementById("tally-nuanced");
const tallyDisagree  = document.getElementById("tally-disagree");
const narrativesEmpty = document.getElementById("narratives-empty");
const narrativesFeed  = document.getElementById("narratives-feed");
const dinerEmpty     = document.getElementById("diner-empty");
const dinerFeed      = document.getElementById("diner-feed");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setWsStatus(connected) {
  if (connected) {
    wsStatus.textContent = "● connected";
    wsStatus.className = "ws-status ws-connected";
  } else {
    wsStatus.textContent = "● disconnected";
    wsStatus.className = "ws-status ws-disconnected";
  }
}

function updateTallies() {
  tallyAgree.textContent    = `▲ ${tallies.agree} agree`;
  tallyNuanced.textContent  = `◆ ${tallies.nuanced} nuanced`;
  tallyDisagree.textContent = `▼ ${tallies.disagree} disagree`;
}

function resetTallies() {
  tallies = { agree: 0, nuanced: 0, disagree: 0 };
  updateTallies();
}

function positionSymbol(pos) {
  return pos === "agree" ? "▲" : pos === "disagree" ? "▼" : "◆";
}

function timeAgo(isoString) {
  const diff = Math.floor((Date.now() - new Date(isoString)) / 1000);
  if (diff < 60)  return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

// ---------------------------------------------------------------------------
// Frame rendering
// ---------------------------------------------------------------------------

function showFrame(frame) {
  activeFrameId = frame.id;
  frameClaim.textContent    = frame.claim;
  frameEvidence.textContent = frame.evidence || "—";
  frameDomain.textContent   = frame.domain || "";

  noFrame.classList.add("hidden");
  frameBody.classList.remove("hidden");
  commitFeedHdr.classList.remove("hidden");
}

function clearCommitFeed() {
  commitFeed.innerHTML = "";
  resetTallies();
}

function prependCommit(commit) {
  const pos = commit.position;
  if (tallies[pos] !== undefined) {
    tallies[pos]++;
    updateTallies();
  }

  const li = document.createElement("li");
  li.className = `commit-item ${pos}`;
  li.innerHTML = `
    <div class="commit-meta">
      <span class="commit-agent">${escHtml(commit.agent_id)}</span>
      <span class="commit-cohort">${escHtml(commit.cohort || "")}</span>
      <span class="commit-position ${pos}">${positionSymbol(pos)} ${pos}</span>
    </div>
    <p class="commit-reasoning">${escHtml(commit.reasoning || "")}</p>
  `;
  commitFeed.prepend(li);
}

// ---------------------------------------------------------------------------
// Narratives rendering
// ---------------------------------------------------------------------------

function prependNarrative(data) {
  narrativesEmpty.classList.add("hidden");

  const card = document.createElement("div");
  card.className = "narrative-card";
  card.innerHTML = `
    <span class="narrative-domain">${escHtml(data.domain || "")}</span>
    <p class="narrative-claim">${escHtml(data.claim || "")}</p>
    <p class="narrative-text">${escHtml(data.narrative || "")}</p>
    <div class="narrative-meta">
      <span>▲ ${data.agree_count ?? 0} agree</span>
      <span>◆ ${data.nuanced_count ?? 0} nuanced</span>
      <span>▼ ${data.disagree_count ?? 0} disagree</span>
      ${data.cohort_tension ? `<span>⚡ ${escHtml(data.cohort_tension)}</span>` : ""}
    </div>
  `;
  narrativesFeed.prepend(card);
}

// ---------------------------------------------------------------------------
// Diner rendering
// ---------------------------------------------------------------------------

function prependDinerItem(checkin) {
  dinerEmpty.classList.add("hidden");

  const li = document.createElement("li");
  li.className = "diner-item";
  li.innerHTML = `
    <div>
      <span class="diner-agent">${escHtml(checkin.agent_id)}</span>
      <span class="diner-cohort">${escHtml(checkin.cohort || "")}</span>
    </div>
    <p class="diner-summary">${escHtml(checkin.human_summary || checkin.task_description || "")}</p>
    <p class="diner-opt">optimised for: ${escHtml(checkin.optimized_for || "")}</p>
  `;
  dinerFeed.prepend(li);
}

// ---------------------------------------------------------------------------
// XSS guard
// ---------------------------------------------------------------------------

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

function connectWs() {
  const ws = new WebSocket(WS_URL);

  ws.addEventListener("open", () => setWsStatus(true));

  ws.addEventListener("message", (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }

    switch (msg.type) {
      case "new_frame": {
        clearCommitFeed();
        showFrame(msg.frame);
        break;
      }
      case "new_commit": {
        // only show commits for the currently active frame
        if (msg.commit && msg.commit.frame_id === activeFrameId) {
          prependCommit(msg.commit);
        }
        break;
      }
      case "translation": {
        prependNarrative(msg.data);
        break;
      }
      case "new_checkin": {
        prependDinerItem(msg.checkin);
        break;
      }
    }
  });

  ws.addEventListener("close", () => {
    setWsStatus(false);
    // reconnect after 3 s
    setTimeout(connectWs, 3000);
  });

  ws.addEventListener("error", () => {
    ws.close();
  });
}

// ---------------------------------------------------------------------------
// Initial page load — hydrate from REST
// ---------------------------------------------------------------------------

async function loadInitialState() {
  // Active frame
  try {
    const r = await fetch("/api/frames/active");
    if (r.ok) {
      const frame = await r.json();
      showFrame(frame);

      // Load existing commits for this frame
      if (frame.commits && frame.commits.length) {
        for (const c of [...frame.commits].reverse()) {
          prependCommit(c);
        }
      }
    }
  } catch (e) {
    console.warn("Could not load active frame:", e);
  }

  // Last N narratives (translations)
  try {
    const r = await fetch("/api/frames?limit=10");
    if (r.ok) {
      const frames = await r.json();
      for (const frame of frames) {
        if (frame.translation) {
          prependNarrative({
            domain:        frame.domain,
            claim:         frame.claim,
            narrative:     frame.translation.narrative,
            agree_count:   frame.translation.agree_count,
            nuanced_count: frame.translation.nuanced_count,
            disagree_count: frame.translation.disagree_count,
            cohort_tension: frame.translation.cohort_tension,
          });
        }
      }
    }
  } catch (e) {
    console.warn("Could not load narratives:", e);
  }

  // Diner check-ins
  try {
    const r = await fetch("/api/agents/checkins?limit=20");
    if (r.ok) {
      const checkins = await r.json();
      for (const c of [...checkins].reverse()) {
        prependDinerItem(c);
      }
    }
  } catch (e) {
    console.warn("Could not load diner check-ins:", e);
  }
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

loadInitialState();
connectWs();
