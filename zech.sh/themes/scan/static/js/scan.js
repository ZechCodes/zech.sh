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

    // Search form — fetch classification then navigate via JS to avoid CSP form-action restriction
    var form = document.querySelector('.scan-form:not(#chatFollowup)');
    if (form) {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            var q = this.querySelector('[name=q]').value.trim();
            if (!q) return;
            fetch('/search?q=' + encodeURIComponent(q), {
                headers: { 'Accept': 'application/json' },
                credentials: 'same-origin'
            })
            .then(function(r) {
                if (!r.ok) throw new Error(r.status);
                return r.json();
            })
            .then(function(data) {
                if (data.type === 'research') {
                    // Server now returns /chat/{id} URL
                    window.location.href = data.url;
                } else {
                    window.location.href = data.url;
                }
            })
            .catch(function() {
                window.location.href = '/search?q=' + encodeURIComponent(q);
            });
        });
    }

});
