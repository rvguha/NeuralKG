(function () {
  "use strict";

  var form = document.getElementById("ask-form");
  var question = document.getElementById("question");
  var askButton = document.getElementById("ask-button");
  var examples = document.getElementById("examples");
  var emptyHint = document.getElementById("empty-hint");
  var workingHint = document.getElementById("working-hint");
  var errorBox = document.getElementById("error");
  var trace = document.getElementById("trace");
  var traceList = document.getElementById("trace-list");
  var answer = document.getElementById("answer");
  var walkthrough = document.getElementById("walkthrough");
  var walkthroughBody = document.getElementById("walkthrough-body");
  var walkthroughSummary = document.getElementById("walkthrough-summary");

  var busy = false;
  var elapsed = 0;
  var elapsedTimer = null;
  var startedAt = 0;
  var stages = {};
  var candidates = [];
  var terminal = null;
  var messages = [];
  var chatStore = null;
  var conversationId = null;
  var turnNumber = null;

  var STAGES = [
    ["guardrail", "Budget check"],
    ["discover", "Discovering sources"],
    ["plan", "Planning the query"],
    ["fetch", "Fetching data"],
    ["claim", "Checking claims"],
    ["check", "Checking the evidence"],
    ["synthesize", "Composing the answer"]
  ];

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  try {
    chatStore = new NeuralKGChatHistory.ChatTurnStore();
    conversationId = chatStore.newConversation();
    chatStore.hydrate().catch(function () {});
  } catch (_) {
    chatStore = null;
  }

  function renderExamples() {
    examples.innerHTML = (window.ATLAS_EXAMPLES || []).map(function (group) {
      var items = group.questions || group.queries || [];
      return '<div><div class="example-group-label">' + esc(group.label) + '</div><div class="example-row">' +
        items.map(function (item) {
          var text = typeof item === "string" ? item : item.q;
          return '<button type="button" class="example-chip" data-question="' + esc(text) + '">' + esc(text) + '</button>';
        }).join("") + "</div></div>";
    }).join("");
    Array.prototype.forEach.call(examples.querySelectorAll("[data-question]"), function (button) {
      button.addEventListener("click", function () {
        question.value = button.getAttribute("data-question");
        askButton.disabled = false;
        submit(question.value);
      });
    });
  }
  renderExamples();

  question.addEventListener("input", function () {
    askButton.disabled = busy || !question.value.trim();
  });
  question.addEventListener("focus", function () { question.select(); });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    submit(question.value);
  });

  function resetRun() {
    stages = {};
    candidates = [];
    terminal = null;
    messages = [];
    elapsed = 0;
    startedAt = Date.now();
    trace.hidden = true;
    traceList.innerHTML = "";
    answer.hidden = true;
    answer.innerHTML = "";
    errorBox.hidden = true;
    errorBox.textContent = "";
    walkthrough.hidden = true;
    walkthrough.removeAttribute("open");
    walkthroughBody.innerHTML = "";
    walkthroughSummary.textContent = "";
    examples.hidden = true;
    emptyHint.hidden = true;
    workingHint.hidden = true;
  }

  function setBusy(value) {
    busy = value;
    question.disabled = value;
    askButton.disabled = value || !question.value.trim();
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = null;
    if (value) {
      askButton.textContent = "Asking… 0s";
      elapsedTimer = setInterval(function () {
        elapsed += 1;
        askButton.textContent = "Asking… " + elapsed + "s";
        if (elapsed >= 15) workingHint.hidden = false;
      }, 1000);
    } else {
      askButton.textContent = "Ask";
      workingHint.hidden = true;
    }
  }

  function stage(key, status, note, reasoning) {
    if (!stages[key]) stages[key] = { status: "active", notes: [] };
    stages[key].status = status || stages[key].status;
    if (note && !stages[key].notes.some(function (item) { return item.text === note; })) {
      stages[key].notes.push({ text: note, reasoning: !!reasoning });
    }
    renderTrace();
  }

  function finishActiveStages() {
    Object.keys(stages).forEach(function (key) {
      if (stages[key].status === "active") stages[key].status = "done";
    });
    renderTrace();
  }

  function renderTrace() {
    trace.hidden = false;
    traceList.innerHTML = STAGES.filter(function (definition) { return stages[definition[0]]; }).map(function (definition) {
      var state = stages[definition[0]];
      return '<li class="trace-stage ' + esc(state.status) + '"><span class="trace-dot"></span><div>' +
        '<div class="trace-title">' + esc(definition[1]) +
        (state.status === "active" ? '<span class="mono trace-running">running…</span>' : "") + '</div>' +
        state.notes.map(function (note) {
          return '<div class="trace-note' + (note.reasoning ? ' reasoning' : '') + '">' + esc(note.text) + '</div>';
        }).join("") + '</div></li>';
    }).join("");
  }

  function narrationStage(text) {
    var lower = text.toLowerCase();
    if (lower.indexOf("asking the ard") >= 0 || lower.indexOf("agent finder") >= 0) return "discover";
    if (lower.indexOf("ard summary") >= 0 || lower.indexOf("candidate table") >= 0) return "discover";
    if (lower.indexOf("execution plan") >= 0 || lower.indexOf("initial plan") >= 0 ||
        lower.indexOf("question structure") >= 0 || lower.indexOf("measure & period") >= 0 ||
        lower.indexOf("identifier mapping") >= 0 || lower.indexOf("reading your question") >= 0) return "plan";
    if (lower.indexOf("fetch") >= 0 || lower.indexOf("querying") >= 0 || lower.indexOf("records") >= 0) return "fetch";
    if (lower.indexOf("check") >= 0 || lower.indexOf("verif") >= 0 || lower.indexOf("sentinel") >= 0) return "check";
    if (lower.indexOf("synth") >= 0 || lower.indexOf("compos") >= 0 || lower.indexOf("writing") >= 0) return "synthesize";
    return "plan";
  }

  function cleanNarration(text) {
    return String(text || "").trim().replace(/^(?:\p{Extended_Pictographic}\uFE0F?|[•↪])\s*/u, "");
  }

  function handleMessage(message) {
    messages.push(message);
    var type = message.message_type;
    var content = message.content;
    if (type === "intermediate_message") {
      if (content && typeof content === "object") return;
      var text = cleanNarration(content);
      if (!text || text.charAt(0) === "{") return;
      var key = narrationStage(text);
      if (key === "discover") stage("plan", "done");
      if (text.toLowerCase().indexOf("summary") >= 0) stage(key, "done", text);
      else stage(key, "active", text, key === "plan");
    } else if (type === "result") {
      candidates = Array.isArray(content) ? content : [];
      var top = candidates.slice(0, 3).map(function (item) {
        return (item.name || item.title || item.identifier || "source") + " (" + Number(item.score || 0).toFixed(2) + ")";
      }).join(", ");
      stage("discover", "done", candidates.length + " candidate source" + (candidates.length === 1 ? "" : "s") +
        " considered" + (top ? " — top matches: " + top : ""));
      stage("plan", "active");
    } else if (type === "nlws") {
      terminal = content || {};
      stage("plan", "done");
      stage("fetch", "done", evidenceRows(terminal) + " row" + (evidenceRows(terminal) === 1 ? "" : "s") + " look usable");
      stage("check", "done", "Evidence accepted");
      stage("synthesize", "done", "Done in " + secondsElapsed() + "s");
      finishActiveStages();
      renderAnswer(terminal);
      renderWalkthrough(terminal);
    } else if (type === "error") {
      showError(String(content || "Something went wrong."));
    } else if (type === "end-nlweb-response") {
      finish(terminal ? "completed" : "failed");
    }
  }

  function secondsElapsed() { return Math.max(0, Math.round((Date.now() - startedAt) / 1000)); }

  async function submit(raw) {
    var text = String(raw || "").trim();
    if (!text || busy) return;
    question.value = text;
    resetRun();
    setBusy(true);
    stage("guardrail", "done");
    stage("plan", "active");
    if (chatStore && conversationId) {
      try {
        turnNumber = await chatStore.nextTurnNumber(conversationId);
        await chatStore.beginTurn(conversationId, turnNumber, text, new Date().toISOString());
      } catch (_) { turnNumber = null; }
    }
    var url = "/ask?sse_format=named&debug=true&max_results=8&on_ambiguity=ask&query=" + encodeURIComponent(text);
    if (conversationId) url += "&conversation_id=" + encodeURIComponent(conversationId);
    try {
      var response = await fetch(url);
      if (!response.ok || !response.body) throw new Error(response.statusText || "Stream failed to start");
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";
      var sawEnd = false;
      while (true) {
        var part = await reader.read();
        if (part.done) break;
        buffer += decoder.decode(part.value, { stream: true });
        var match;
        while ((match = buffer.match(/\r\n\r\n|\n\n/))) {
          var frame = buffer.slice(0, match.index);
          buffer = buffer.slice(match.index + match[0].length);
          var payload = frame.split(/\r?\n/).filter(function (line) { return line.indexOf("data:") === 0; })
            .map(function (line) { return line.slice(5).trim(); }).join("\n");
          if (!payload) continue;
          var message;
          try { message = JSON.parse(payload); } catch (_) { continue; }
          handleMessage(message);
          if (message.message_type === "end-nlweb-response") sawEnd = true;
        }
      }
      if (!sawEnd) throw new Error("The connection ended before Atlas finished answering. Try again.");
    } catch (err) {
      showError(err && err.message ? err.message : "Couldn't reach Atlas. Try again in a moment.");
      finish("failed");
    }
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
    stage("check", "blocked", message);
  }

  function finish(status) {
    setBusy(false);
    if (chatStore && conversationId && turnNumber != null) {
      chatStore.finishTurn(conversationId, turnNumber, {
        status: status,
        answer: terminal && terminal.answer || null,
        terminal: terminal,
        candidates: candidates,
        messages: messages,
        error: errorBox.hidden ? null : errorBox.textContent,
        completed_at: new Date().toISOString()
      }).catch(function () {});
    }
  }

  function evidenceRows(data) {
    var body = data && data.data || {};
    if (Array.isArray(body.results)) return body.results.length;
    if (Array.isArray(body.ranking)) return body.ranking.length;
    if (Array.isArray(body.series)) return body.series.length;
    if (Array.isArray(body.interpretations)) return body.interpretations.length;
    return data && data.evidence ? 1 : 0;
  }

  function valueText(value) {
    if (value == null) return "—";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }

  function table(rows) {
    if (!Array.isArray(rows) || !rows.length) return "";
    var normalized = rows.map(function (row) {
      if (row && typeof row === "object") return row;
      return { value: row };
    });
    var columns = Object.keys(normalized[0]).filter(function (key) {
      return normalized[0][key] === null || typeof normalized[0][key] !== "object";
    }).slice(0, 8);
    return '<div class="answer-table-wrap"><table class="answer-table"><thead><tr>' +
      columns.map(function (column) { return "<th>" + esc(column) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + normalized.map(function (row) {
        return "<tr>" + columns.map(function (column) { return "<td>" + esc(valueText(row[column])) + "</td>"; }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>";
  }

  function answerRows(data) {
    var body = data.data || {};
    if (Array.isArray(body.result)) return body.result;
    if (Array.isArray(body.rows)) return body.rows;
    if (Array.isArray(body.ranking)) return body.ranking;
    if (Array.isArray(body.series)) return body.series;
    if (Array.isArray(body.interpretations)) return body.interpretations;
    if (Array.isArray(body.results)) return body.results;
    return [];
  }

  function renderAnswer(data) {
    if (!data.answer) { showError("No answer."); return; }
    var items = data.items || candidates || [];
    var citations = items.slice(0, Math.max(1, Math.min(items.length, 4))).map(function (item) {
      var trust = item.trust || item.tier || "human-reviewed";
      if (["human-reviewed", "machine-confirmed", "unverified"].indexOf(trust) < 0) trust = "human-reviewed";
      var label = { "human-reviewed": "Human-reviewed", "machine-confirmed": "Machine-confirmed", "unverified": "Unverified" }[trust];
      return '<span class="citation" title="' + esc(item.identifier || item.source_id || "") + '"><span class="trust-dot"></span>' +
        esc(item.name || item.title || item.identifier || "Source") + " · " + label + "</span>";
    }).join("");
    var rows = answerRows(data);
    var narrative = data.answer;
    if (rows.length) narrative = narrative.split("\n").filter(function (line) {
      return line.trim().charAt(0) !== "|";
    }).join("\n");
    answer.innerHTML = '<p class="answer-narrative">' + esc(narrative).replace(/\n/g, "<br>") + '</p>' + table(rows) +
      (citations ? '<div class="citation-list">' + citations + '</div>' : '') +
      '<div class="answered-in">Answered in ' + secondsElapsed() + 's</div>';
    answer.hidden = false;
  }

  function section(title, body) {
    return '<section><div class="walk-section-title">' + esc(title) + '</div>' + body + '</section>';
  }

  function renderWalkthrough(data) {
    var items = data.items || candidates || [];
    var attempts = data.attempts || [];
    var used = data.evidence && data.evidence.identifier;
    var sources = items.length ? '<div class="source-pills">' + items.map(function (item) {
      var id = item.identifier || item.source_id || "";
      return '<span class="source-pill' + (id === used ? ' used' : '') + '" title="' + esc(id) + '">' +
        esc(item.name || item.title || id) + ' · ' + Number(item.score || 0).toFixed(2) + (id === used ? ' · used' : '') + '</span>';
    }).join("") + '</div>' : '<div class="trace-note">None matched this question.</div>';
    var rejected = attempts.filter(function (attempt) { return attempt.outcome && attempt.outcome !== "accepted"; });
    var backtracks = rejected.length ? section("Backtracks", rejected.map(function (attempt) {
      return '<div class="trace-note" style="color:var(--danger)">Gave up on <span class="mono">' +
        esc(attempt.identifier || attempt.source || "source") + '</span> — ' + esc(attempt.reason || attempt.outcome) + '</div>';
    }).join("")) : "";
    var queryCard = '<div class="query-card"><div class="mono">' + esc(used || data.shape || "query plan") + '</div>' +
      '<div class="mono trace-note">' + esc(typeof data.plan === "string" ? data.plan : JSON.stringify(data.plan || {})) + '</div>' +
      '<div class="trace-note">' + evidenceRows(data) + ' row' + (evidenceRows(data) === 1 ? '' : 's') + ' · — scanned</div></div>';
    var usage = data.usage || {};
    var discovery = data.discovery_usage || {};
    var cost = Number(usage.cost_usd || 0) + Number(discovery.cost_usd || 0);
    var tokens = Number(usage.total_tokens || 0) + Number(discovery.total_tokens || 0);
    var usageGrid = '<div class="walk-grid"><div><div class="walk-label">Planner</div><div class="mono">' +
      Number(usage.prompt_tokens || 0).toLocaleString() + ' in · ' + Number(usage.completion_tokens || 0).toLocaleString() +
      ' out</div></div><div><div class="walk-label">Synthesis</div><div class="mono">included above</div></div>' +
      '<div><div class="walk-label">BigQuery</div><div class="mono">—</div></div><div><div class="walk-label">Total (est.)</div>' +
      '<div class="mono" style="font-weight:600">$' + cost.toFixed(4) + '</div></div></div>';
    walkthroughBody.innerHTML = section("Sources considered", sources) + backtracks +
      section("Queries executed", queryCard) + section("Token usage & cost", usageGrid);
    walkthroughSummary.textContent = secondsElapsed() + 's · $' + cost.toFixed(4) + ' est.';
    walkthrough.hidden = false;
  }
})();
