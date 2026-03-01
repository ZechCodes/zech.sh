document.addEventListener('DOMContentLoaded', function() {
    // Auto-grow textareas — fallback for browsers without field-sizing: content
    document.querySelectorAll('textarea.scan-input-auto').forEach(function(ta) {
        function resize() {
            ta.style.height = 'auto';
            ta.style.height = ta.scrollHeight + 'px';
        }
        ta.addEventListener('input', resize);
        // Enter submits, Shift+Enter inserts newline
        ta.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                ta.closest('form').requestSubmit();
            }
        });
    });

    // Mode dropdown
    var modeLabels = { launch: 'LAUNCH', discover: 'DISCOVER', deep: 'DEEP', search: 'SEARCH' };
    var currentMode = localStorage.getItem('scanMode') || 'launch';
    if (!modeLabels[currentMode]) currentMode = 'launch';

    var modeSplit = document.querySelector('.scan-mode-split');
    var modeLabel = document.querySelector('.scan-mode-label');
    var modeToggle = document.querySelector('.scan-mode-toggle');
    var modeMenu = document.querySelector('.scan-mode-menu');

    function setMode(mode) {
        currentMode = mode;
        localStorage.setItem('scanMode', mode);
        if (modeLabel) modeLabel.textContent = modeLabels[mode] || 'LAUNCH';
        if (modeMenu) {
            modeMenu.querySelectorAll('.scan-mode-option').forEach(function(opt) {
                opt.classList.toggle('active', opt.dataset.mode === mode);
            });
        }
    }

    setMode(currentMode);

    if (modeToggle && modeMenu && modeSplit) {
        modeToggle.addEventListener('click', function(e) {
            e.stopPropagation();
            var isHidden = modeMenu.hidden;
            modeMenu.hidden = !isHidden;
            modeSplit.classList.toggle('is-open', isHidden);
        });

        modeMenu.querySelectorAll('.scan-mode-option').forEach(function(opt) {
            opt.addEventListener('click', function(e) {
                e.stopPropagation();
                setMode(this.dataset.mode);
                modeMenu.hidden = true;
                modeSplit.classList.remove('is-open');
            });
        });

        document.addEventListener('click', function() {
            modeMenu.hidden = true;
            modeSplit.classList.remove('is-open');
        });

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && !modeMenu.hidden) {
                modeMenu.hidden = true;
                modeSplit.classList.remove('is-open');
            }
        });
    }

    // Search form — fetch classification then navigate via JS to avoid CSP form-action restriction
    var form = document.querySelector('.scan-form:not(#chatFollowup):not(#searchBar)');
    if (form) {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            var q = this.querySelector('[name=q]').value.trim();
            if (!q) return;
            var url = '/search?q=' + encodeURIComponent(q);
            if (currentMode && currentMode !== 'launch') {
                url += '&mode=' + currentMode;
            }
            // Search mode renders server-side — navigate directly
            if (currentMode === 'search') {
                window.location.href = url;
                return;
            }
            fetch(url, {
                headers: { 'Accept': 'application/json' },
                credentials: 'same-origin'
            })
            .then(function(r) {
                if (!r.ok) throw new Error(r.status);
                return r.json();
            })
            .then(function(data) {
                if (data.url) {
                    window.location.href = data.url;
                } else {
                    // Classifier returned SEARCH — navigate to results page
                    window.location.href = '/search?q=' + encodeURIComponent(q) + '&mode=search';
                }
            })
            .catch(function() {
                window.location.href = '/search?q=' + encodeURIComponent(q);
            });
        });
    }

});
