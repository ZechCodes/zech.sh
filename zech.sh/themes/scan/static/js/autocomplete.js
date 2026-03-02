(function() {
    'use strict';

    var DEBOUNCE_MS = 250;
    var MIN_CHARS = 2;

    var configs = [
        {
            selector: '.scan-form:not(#chatFollowup):not(#searchBar) [name=q]',
            getMode: function() { return localStorage.getItem('scanMode') || 'launch'; }
        },
        {
            selector: '#searchBar [name=q]',
            getMode: function() { return 'search'; }
        },
        {
            selector: '#chatFollowup [name=q]',
            getMode: function() { return 'discover'; }
        }
    ];

    function initAutocomplete(input, getMode) {
        var dropdown = document.createElement('div');
        dropdown.className = 'scan-suggest-dropdown';
        dropdown.setAttribute('role', 'listbox');
        dropdown.hidden = true;

        // Position dropdown inside .scan-form-row if available, else input's parent
        var anchor = input.closest('.scan-form-row') || input.parentNode;
        anchor.style.position = 'relative';
        anchor.appendChild(dropdown);

        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');

        var timer = null;
        var controller = null;
        var selectedIdx = -1;

        function clearDropdown() {
            dropdown.innerHTML = '';
            dropdown.hidden = true;
            selectedIdx = -1;
            input.setAttribute('aria-expanded', 'false');
        }

        function showSuggestions(items) {
            dropdown.innerHTML = '';
            if (!items || items.length === 0) {
                clearDropdown();
                return;
            }
            items.forEach(function(text, i) {
                var item = document.createElement('div');
                item.className = 'scan-suggest-item';
                item.setAttribute('role', 'option');
                item.textContent = text;
                item.dataset.index = i;

                item.addEventListener('mousedown', function(e) {
                    e.preventDefault(); // prevent blur before selection
                    selectSuggestion(text, true);
                });

                item.addEventListener('mouseenter', function() {
                    setSelected(i);
                });

                dropdown.appendChild(item);
            });
            dropdown.hidden = false;
            selectedIdx = -1;
            input.setAttribute('aria-expanded', 'true');
        }

        function setSelected(idx) {
            var items = dropdown.querySelectorAll('.scan-suggest-item');
            items.forEach(function(el) { el.classList.remove('is-selected'); });
            selectedIdx = idx;
            if (idx >= 0 && idx < items.length) {
                items[idx].classList.add('is-selected');
            }
        }

        function selectSuggestion(text, submit) {
            input.value = text;
            clearDropdown();
            // Trigger input event for textarea auto-grow in scan.js
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.focus();
            if (submit) {
                var form = input.closest('form');
                if (form) form.requestSubmit();
            }
        }

        function fetchSuggestions() {
            var q = input.value.trim();
            if (q.length < MIN_CHARS) {
                clearDropdown();
                return;
            }

            if (controller) controller.abort();
            controller = new AbortController();

            var mode = getMode();
            var url = '/suggest?q=' + encodeURIComponent(q) + '&mode=' + encodeURIComponent(mode);

            fetch(url, {
                signal: controller.signal,
                credentials: 'same-origin'
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                showSuggestions(data.suggestions || []);
            })
            .catch(function(err) {
                if (err.name !== 'AbortError') clearDropdown();
            });
        }

        input.addEventListener('input', function() {
            clearTimeout(timer);
            timer = setTimeout(fetchSuggestions, DEBOUNCE_MS);
        });

        input.addEventListener('keydown', function(e) {
            if (dropdown.hidden) return;

            var items = dropdown.querySelectorAll('.scan-suggest-item');
            var count = items.length;
            if (count === 0) return;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                setSelected(selectedIdx < count - 1 ? selectedIdx + 1 : 0);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                setSelected(selectedIdx > 0 ? selectedIdx - 1 : count - 1);
            } else if (e.key === 'Enter' || e.key === 'Tab') {
                if (selectedIdx >= 0 && selectedIdx < count) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    selectSuggestion(items[selectedIdx].textContent, e.key === 'Enter');
                }
            } else if (e.key === 'Escape') {
                clearDropdown();
            }
        }, true);

        input.addEventListener('blur', function() {
            // Small delay to allow mousedown on items to fire first
            setTimeout(clearDropdown, 150);
        });

        input.addEventListener('focus', function() {
            // Re-show if we already have suggestions and input has text
            if (input.value.trim().length >= MIN_CHARS && dropdown.children.length > 0) {
                dropdown.hidden = false;
                input.setAttribute('aria-expanded', 'true');
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        configs.forEach(function(cfg) {
            var input = document.querySelector(cfg.selector);
            if (input) initAutocomplete(input, cfg.getMode);
        });
    });
})();
