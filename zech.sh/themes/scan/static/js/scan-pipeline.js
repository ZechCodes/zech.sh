// SCAN: Shared pipeline utilities — SSE streaming, markdown, tool-call UI
// Both chat.js and research.js delegate to this module.
window.ScanPipeline = (function () {
  "use strict";

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function faviconUrl(url) {
    try {
      var host = new URL(url).hostname;
      return "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(host) + "&sz=16";
    } catch (_) {
      return "";
    }
  }

  function domainFromUrl(url) {
    try { return new URL(url).hostname; } catch (_) { return ""; }
  }

  function fmtNum(n) {
    return Number(n).toLocaleString();
  }

  function fmtUsage(u) {
    return fmtNum(u.input_tokens) + "/" + fmtNum(u.output_tokens) +
      " ($" + u.input_cost + "/$" + u.output_cost + ")";
  }

  function createFaviconImg(url) {
    var src = faviconUrl(url);
    if (!src) return null;
    var img = document.createElement("img");
    img.className = "tool-favicon";
    img.src = src;
    img.alt = "";
    img.title = domainFromUrl(url);
    img.width = 16;
    img.height = 16;
    return img;
  }

  function truncateUrl(url) {
    try {
      var u = new URL(url);
      var path = u.pathname.length > 30 ? u.pathname.slice(0, 30) + "\u2026" : u.pathname;
      return u.hostname + path;
    } catch (_) {
      return url.length > 60 ? url.slice(0, 60) + "\u2026" : url;
    }
  }

  // ---------------------------------------------------------------------------
  // Markdown renderer
  // ---------------------------------------------------------------------------

  function renderMarkdown(md) {
    var html = md
      .replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
        return '<pre class="research-code"><code>' + escapeHtml(code.trim()) + "</code></pre>";
      })
      .replace(/`([^`]+)`/g, function (_, code) {
        return "<code>" + escapeHtml(code) + "</code>";
      })
      .replace(/^### (.+)$/gm, "<h4>$1</h4>")
      .replace(/^## (.+)$/gm, "<h3>$1</h3>")
      .replace(/^# (.+)$/gm, "<h2>$1</h2>")
      .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (_, text, url) {
        if (/^https?:\/\//i.test(url)) {
          return '<a href="' + encodeURI(url) + '" target="_blank" rel="noopener">' + escapeHtml(text) + '</a>';
        }
        return escapeHtml(text);
      })
      .replace(/^[-*] (.+)$/gm, "<li>$1</li>")
      .replace(/^\d+\. (.+)$/gm, "<li>$1</li>")
      .replace(/\n\n+/g, "</p><p>")
      .replace(/\n/g, "<br>");

    html = html.replace(/((?:<li>.*?<\/li>(?:<br>)?)+)/g, "<ul>$1</ul>");
    html = html.replace(/<ul>([\s\S]*?)<\/ul>/g, function (_, inner) {
      return "<ul>" + inner.replace(/<br>/g, "") + "</ul>";
    });

    return "<p>" + html + "</p>";
  }

  // ---------------------------------------------------------------------------
  // Pipeline factory
  // ---------------------------------------------------------------------------

  function createPipeline(opts) {
    var pipelineDetails = opts.pipelineDetails;
    var pipelineStatus = opts.pipelineStatus;
    var responseEl = opts.responseEl;
    var cursorEl = opts.cursorEl;
    var stageLabels = opts.stageLabels || {
      reasoning: "THINKING",
      researching: "RESEARCHING",
      responding: "GENERATING",
    };

    // Mutable state
    var state = {
      buffer: "",
      receivedFirstText: false,
      toolGroups: [],
      groupsByTopic: {},
      allFetchedUrls: [],
      totalToolCalls: 0,
      usageData: null,
      pipelineItems: [],
      reasoningBuffer: "",
      reasoningBlock: null,
    };

    function reset() {
      state.buffer = "";
      state.receivedFirstText = false;
      state.toolGroups = [];
      state.groupsByTopic = {};
      state.allFetchedUrls = [];
      state.totalToolCalls = 0;
      state.usageData = null;
      state.pipelineItems = [];
      state.reasoningBuffer = "";
      state.reasoningBlock = null;

      pipelineStatus.textContent = "";
      pipelineStatus.className = "pipeline-status";
      pipelineDetails.innerHTML = "";
      responseEl.innerHTML = "";
      responseEl.appendChild(cursorEl);
    }

    // --- Tool group DOM builders ---

    function createToolGroup(topic) {
      var group = document.createElement("div");
      group.className = "tool-group is-running";

      var header = document.createElement("div");
      header.className = "tool-header";

      var chevron = document.createElement("span");
      chevron.className = "tool-chevron";
      var spinner = document.createElement("span");
      spinner.className = "tool-spinner";
      var label = document.createElement("span");
      label.className = "tool-label";
      label.textContent = "Researching";
      var topicEl = document.createElement("span");
      topicEl.className = "tool-topic";
      topicEl.textContent = topic;

      header.appendChild(chevron);
      header.appendChild(spinner);
      header.appendChild(label);
      header.appendChild(topicEl);
      group.appendChild(header);

      var subline = document.createElement("div");
      subline.className = "tool-subline";
      group.appendChild(subline);

      var sources = document.createElement("div");
      sources.className = "tool-sources";
      sources.hidden = true;
      group.appendChild(sources);

      var body = document.createElement("div");
      body.className = "tool-body";
      var children = document.createElement("div");
      children.className = "tool-children";
      body.appendChild(children);
      group.appendChild(body);

      header.addEventListener("click", function () {
        if (!group.classList.contains("is-done")) return;
        group.classList.toggle("is-collapsed");
      });

      var groupObj = {
        el: group,
        header: header,
        label: label,
        spinner: spinner,
        chevron: chevron,
        subline: subline,
        sources: sources,
        body: body,
        childrenEl: children,
        topic: topic,
        fetchedUrls: [],
        runningChildren: [],
      };

      state.toolGroups.push(groupObj);
      state.groupsByTopic[topic] = groupObj;
      pipelineDetails.appendChild(group);
      state.pipelineItems.push(group);
      return groupObj;
    }

    function addChild(group, html, isRunning) {
      var child = document.createElement("div");
      child.className = "tool-child" + (isRunning ? "" : " is-done");
      child.innerHTML = html;
      group.childrenEl.appendChild(child);
      if (isRunning) group.runningChildren.push(child);
      return child;
    }

    function updateSubline(group) {
      var running = group.runningChildren;
      if (running.length === 0) {
        group.subline.innerHTML = "";
        group.subline.hidden = true;
        return;
      }
      var last = running[running.length - 1];
      group.subline.innerHTML = last.innerHTML;
      group.subline.hidden = false;
    }

    function finishChild(group, child, doneHtml) {
      child.innerHTML = doneHtml;
      child.classList.add("is-done");
      var idx = group.runningChildren.indexOf(child);
      if (idx !== -1) group.runningChildren.splice(idx, 1);
      updateSubline(group);
    }

    function collapseGroup(group, numSources) {
      group.el.classList.remove("is-running");
      group.el.classList.add("is-done", "is-collapsed");
      group.spinner.className = "tool-icon-done";
      group.spinner.textContent = "\u2713";
      group.label.textContent = "Researched";
      group.subline.innerHTML = "";
      group.subline.hidden = true;

      if (group.fetchedUrls.length > 0) {
        group.sources.innerHTML = "";
        group.fetchedUrls.slice(0, 6).forEach(function (u) {
          var img = createFaviconImg(u);
          if (img) group.sources.appendChild(img);
        });
        var count = document.createElement("span");
        count.className = "tool-source-count";
        count.textContent = "Read " + numSources + " source" + (numSources !== 1 ? "s" : "");
        group.sources.appendChild(count);
        group.sources.hidden = false;
      }
    }

    function buildSummary() {
      var summaryEl = document.createElement("div");
      summaryEl.className = "tool-summary";
      summaryEl.setAttribute("role", "button");

      var chevron = document.createElement("span");
      chevron.className = "tool-chevron";
      summaryEl.appendChild(chevron);

      var countEl = document.createElement("span");
      countEl.className = "tool-summary-count";
      countEl.textContent = state.totalToolCalls + " tool call" + (state.totalToolCalls !== 1 ? "s" : "");
      summaryEl.appendChild(countEl);

      if (state.allFetchedUrls.length > 0) {
        var favicons = document.createElement("span");
        favicons.className = "tool-summary-favicons";
        var seen = {};
        var fCount = 0;
        state.allFetchedUrls.forEach(function (u) {
          try {
            var host = new URL(u).hostname;
            if (seen[host] || fCount >= 10) return;
            seen[host] = true;
            fCount++;
            var img = createFaviconImg(u);
            if (img) favicons.appendChild(img);
          } catch (_) {}
        });
        summaryEl.appendChild(favicons);

        var srcCount = document.createElement("span");
        srcCount.className = "tool-source-count";
        srcCount.textContent = "Read " + state.allFetchedUrls.length + " source" + (state.allFetchedUrls.length !== 1 ? "s" : "");
        summaryEl.appendChild(srcCount);
      }

      if (state.usageData) {
        var usageSummary = document.createElement("span");
        usageSummary.className = "tool-usage";
        usageSummary.textContent = fmtUsage(state.usageData.total);
        summaryEl.appendChild(usageSummary);
      }

      var summaryBody = document.createElement("div");
      summaryBody.className = "tool-summary-body";
      summaryBody.hidden = true;

      state.pipelineItems.forEach(function (item) {
        summaryBody.appendChild(item.el || item);
      });

      summaryEl.addEventListener("click", function () {
        summaryBody.hidden = !summaryBody.hidden;
        summaryEl.classList.toggle("is-expanded", !summaryBody.hidden);
      });

      pipelineDetails.innerHTML = "";
      pipelineDetails.appendChild(summaryEl);
      pipelineDetails.appendChild(summaryBody);

      if (state.usageData) {
        var usageTotal = document.createElement("div");
        usageTotal.className = "tool-usage-total";
        usageTotal.innerHTML =
          '<div>Research agent: ' + fmtUsage(state.usageData.research) + '</div>' +
          '<div>Extraction agent: ' + fmtUsage(state.usageData.extraction) + '</div>' +
          '<div>Total: ' + fmtUsage(state.usageData.total) + '</div>';
        summaryBody.appendChild(usageTotal);
      }
    }

    function addDetail(label, text) {
      var item = document.createElement("div");
      item.className = "pipeline-detail";
      item.innerHTML =
        '<span class="pipeline-label">' + escapeHtml(label) + ":</span> " + escapeHtml(text);
      pipelineDetails.appendChild(item);
    }

    // --- SSE connection ---

    function connectSSE(streamUrl, callbacks) {
      callbacks = callbacks || {};
      var es = new EventSource(streamUrl);
      var fetchChildren = {};

      es.addEventListener("stage", function (e) {
        var data = JSON.parse(e.data);
        var label = stageLabels[data.stage] || data.stage.toUpperCase();
        pipelineStatus.textContent = label;
        pipelineStatus.className = "pipeline-status visible";
        if (data.stage !== "reasoning" && state.reasoningBlock) {
          state.reasoningBlock.classList.add("is-complete");
          state.reasoningBlock = null;
          state.reasoningBuffer = "";
        }
      });

      es.addEventListener("detail", function (e) {
        var data = JSON.parse(e.data);
        var group = data.topic ? state.groupsByTopic[data.topic] : null;

        if (data.type === "reasoning") {
          if (!state.reasoningBlock) {
            state.reasoningBlock = document.createElement("div");
            state.reasoningBlock.className = "deep-reasoning";
            pipelineDetails.appendChild(state.reasoningBlock);
            state.pipelineItems.push(state.reasoningBlock);
          }
          state.reasoningBuffer += data.text;
          state.reasoningBlock.innerHTML = renderMarkdown(state.reasoningBuffer);

        } else if (data.type === "research") {
          if (state.reasoningBlock) {
            state.reasoningBlock.classList.add("is-complete");
            state.reasoningBlock = null;
            state.reasoningBuffer = "";
          }
          state.totalToolCalls++;
          createToolGroup(data.topic);

        } else if (data.type === "search") {
          state.totalToolCalls++;
          if (group) {
            var html = '<span class="tool-spinner-sm"></span> Searching "' + escapeHtml(data.query) + '"';
            var child = addChild(group, html, true);
            child._searchQuery = data.query;
            updateSubline(group);
          }

        } else if (data.type === "search_done") {
          if (group) {
            var found = null;
            group.runningChildren.forEach(function (c) {
              if (c._searchQuery === data.query) found = c;
            });
            if (found) {
              var doneHtml = '<span class="tool-icon-done">\u2713</span> Searched "' +
                escapeHtml(data.query) + '" \u2014 ' + data.num_results + ' result' +
                (data.num_results !== 1 ? 's' : '');
              finishChild(group, found, doneHtml);
            }
          }

        } else if (data.type === "fetch") {
          state.totalToolCalls++;
          if (group) {
            var fav = createFaviconImg(data.url);
            var favHtml = fav ? fav.outerHTML + " " : "";
            var html = '<span class="tool-spinner-sm"></span> ' + favHtml +
              "Reading " + escapeHtml(truncateUrl(data.url));
            var child = addChild(group, html, true);
            fetchChildren[data.url] = { child: child, group: group };
            updateSubline(group);
          }

        } else if (data.type === "fetch_done") {
          var entry = fetchChildren[data.url];
          if (entry) {
            var child = entry.child;
            var g = entry.group;
            var fav = createFaviconImg(data.url);
            var favHtml = fav ? fav.outerHTML + " " : "";
            var verb = data.failed ? "Failed" : "Read";
            var doneHtml = '<span class="tool-icon-done">' + (data.failed ? "\u2717" : "\u2713") +
              '</span> ' + favHtml + verb + " " + escapeHtml(truncateUrl(data.url));
            finishChild(g, child, doneHtml);
            if (!data.failed) {
              g.fetchedUrls.push(data.url);
              state.allFetchedUrls.push(data.url);
              if (data.usage) {
                var usageSpan = document.createElement("span");
                usageSpan.className = "tool-usage";
                usageSpan.textContent = fmtUsage(data.usage);
                child.appendChild(usageSpan);
              }
              if (data.content) {
                child.classList.add("has-content");
                var contentEl = document.createElement("div");
                contentEl.className = "tool-child-content";
                contentEl.hidden = true;
                contentEl.textContent = data.content;
                child.appendChild(contentEl);
                child.addEventListener("click", function (ev) {
                  ev.stopPropagation();
                  contentEl.hidden = !contentEl.hidden;
                  child.classList.toggle("is-expanded", !contentEl.hidden);
                });
              }
            }
            delete fetchChildren[data.url];
          }

        } else if (data.type === "result") {
          if (group) collapseGroup(group, data.num_sources || 0);

        } else if (data.type === "message") {
          var msgEl = document.createElement("div");
          msgEl.className = "tool-message";
          msgEl.innerHTML = renderMarkdown(data.text || "");
          var msgGroup = group || (state.toolGroups.length > 0 ? state.toolGroups[state.toolGroups.length - 1] : null);
          if (msgGroup) {
            msgGroup.childrenEl.appendChild(msgEl);
          } else {
            pipelineDetails.appendChild(msgEl);
          }

        } else if (data.type === "usage") {
          state.usageData = data;
          var existingSummary = pipelineDetails.querySelector(".tool-summary");
          if (existingSummary) {
            var usageSpan = document.createElement("span");
            usageSpan.className = "tool-usage";
            usageSpan.textContent = fmtUsage(data.total);
            existingSummary.appendChild(usageSpan);
            var summaryBody = pipelineDetails.querySelector(".tool-summary-body");
            if (summaryBody) {
              var usageTotal = document.createElement("div");
              usageTotal.className = "tool-usage-total";
              usageTotal.innerHTML =
                '<div>Research agent: ' + fmtUsage(data.research) + '</div>' +
                '<div>Extraction agent: ' + fmtUsage(data.extraction) + '</div>' +
                '<div>Total: ' + fmtUsage(data.total) + '</div>';
              summaryBody.appendChild(usageTotal);
            }
          }
        }
      });

      es.addEventListener("clarification", function (e) {
        es.close();
        pipelineStatus.className = "pipeline-status";
        var data = JSON.parse(e.data);
        if (callbacks.onClarification) callbacks.onClarification(data.questions);
      });

      es.addEventListener("text", function (e) {
        if (!state.receivedFirstText) {
          state.receivedFirstText = true;
          pipelineStatus.className = "pipeline-status";
          if (state.reasoningBlock) {
            state.reasoningBlock.classList.add("is-complete");
            state.reasoningBlock = null;
            state.reasoningBuffer = "";
          }
          state.toolGroups.forEach(function (g) {
            if (g.el.classList.contains("is-running")) {
              collapseGroup(g, g.fetchedUrls.length);
            }
          });
          if (state.pipelineItems.length > 0) {
            buildSummary();
          }
        }
        var data = JSON.parse(e.data);
        state.buffer += data.text;
        responseEl.innerHTML = renderMarkdown(state.buffer);
        responseEl.appendChild(cursorEl);
        responseEl.scrollTop = responseEl.scrollHeight;
      });

      es.addEventListener("done", function () {
        es.close();
        if (cursorEl.parentNode) cursorEl.remove();
        if (callbacks.onDone) callbacks.onDone();
      });

      es.addEventListener("error", function (e) {
        es.close();
        if (cursorEl.parentNode) cursorEl.remove();
        pipelineStatus.className = "pipeline-status";

        if (e.data) {
          // Server-sent error event
          var data = JSON.parse(e.data);
          responseEl.innerHTML =
            '<p class="research-error">Error: ' + escapeHtml(data.error) + "</p>";
        } else if (state.buffer) {
          // Connection drop with partial results — render what we have
          responseEl.innerHTML = renderMarkdown(state.buffer);
        } else {
          // Connection drop before any response
          responseEl.innerHTML =
            '<p class="research-error">Connection lost. Please try again.</p>';
        }
        if (callbacks.onError) callbacks.onError();
      });

      return es;
    }

    return {
      state: state,
      reset: reset,
      connectSSE: connectSSE,
      buildSummary: buildSummary,
      addDetail: addDetail,
    };
  }

  // Public API
  return {
    escapeHtml: escapeHtml,
    faviconUrl: faviconUrl,
    domainFromUrl: domainFromUrl,
    fmtNum: fmtNum,
    fmtUsage: fmtUsage,
    createFaviconImg: createFaviconImg,
    truncateUrl: truncateUrl,
    renderMarkdown: renderMarkdown,
    createPipeline: createPipeline,
  };
})();
