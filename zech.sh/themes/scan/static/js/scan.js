document.addEventListener('DOMContentLoaded', function() {
    // Search form — fetch classification then navigate via JS to avoid CSP form-action restriction
    var form = document.querySelector('.scan-form');
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
                    window.location.href = '/search?q=' + encodeURIComponent(q);
                } else {
                    window.location.href = data.url;
                }
            })
            .catch(function() {
                // Fallback: navigate directly
                window.location.href = '/search?q=' + encodeURIComponent(q);
            });
        });
    }
});
