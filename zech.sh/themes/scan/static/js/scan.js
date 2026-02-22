document.addEventListener('DOMContentLoaded', function() {
    // Search form — fetch classification then navigate via JS to avoid CSP form-action restriction
    var form = document.querySelector('.scan-form');
    if (form) {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            var q = this.querySelector('[name=q]').value.trim();
            if (!q) return;
            fetch('/search?q=' + encodeURIComponent(q), {
                headers: { 'Accept': 'application/json' }
            })
            .then(function(r) { return r.json(); })
            .then(function(data) { window.location.href = data.url; });
        });
    }
});
