// SCAN: Research mode — single-query streaming via SSE
// Depends on scan-pipeline.js (loaded before this script).
(function () {
  "use strict";

  var SP = window.ScanPipeline;
  var scriptEl = document.currentScript;
  var query = scriptEl && scriptEl.getAttribute("data-query");
  if (!query) return;

  var responseEl = document.getElementById("researchResponse");
  var cursorEl = document.getElementById("researchCursor");
  var pipelineStatus = document.getElementById("pipelineStatus");
  var pipelineDetails = document.getElementById("pipelineDetails");

  var pipeline = SP.createPipeline({
    pipelineDetails: pipelineDetails,
    pipelineStatus: pipelineStatus,
    responseEl: responseEl,
    cursorEl: cursorEl,
    stageLabels: { researching: "RESEARCHING", responding: "GENERATING" },
  });

  function connectStream(extraContext) {
    var streamUrl = "/research/stream?q=" + encodeURIComponent(query);
    if (extraContext) streamUrl += "&context=" + encodeURIComponent(extraContext);

    pipeline.connectSSE(streamUrl, {
      onDone: function () {
        responseEl.innerHTML = SP.renderMarkdown(pipeline.state.buffer);
      },
      onClarification: function (questions) {
        var container = document.createElement("div");
        container.className = "pipeline-clarification";
        questions.forEach(function (question) {
          var qEl = document.createElement("div");
          qEl.className = "clarification-question";
          qEl.textContent = question;
          container.appendChild(qEl);
        });
        var form = document.createElement("form");
        form.className = "clarification-form";
        var input = document.createElement("input");
        input.type = "text";
        input.className = "scan-input clarification-input";
        input.placeholder = "Provide details...";
        input.autofocus = true;
        form.appendChild(input);
        container.appendChild(form);
        pipelineDetails.appendChild(container);
        input.focus();
        form.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var answer = input.value.trim();
          if (!answer) return;
          container.remove();
          pipeline.addDetail("YOU", answer);
          connectStream(answer);
        });
      },
    });
  }

  connectStream();
})();
