document.addEventListener('DOMContentLoaded', function() {
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

    // Sidebar toggle
    var sidebarToggle = document.querySelector('.sidebar-toggle');
    var sidebar = document.getElementById('scanSidebar');
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', function() {
            var expanded = sidebar.classList.toggle('is-open');
            sidebarToggle.setAttribute('aria-expanded', expanded);
        });
        // Close sidebar when clicking outside on mobile
        document.addEventListener('click', function(e) {
            if (sidebar.classList.contains('is-open') &&
                !sidebar.contains(e.target) &&
                !sidebarToggle.contains(e.target)) {
                sidebar.classList.remove('is-open');
                sidebarToggle.setAttribute('aria-expanded', 'false');
            }
        });
    }
});
