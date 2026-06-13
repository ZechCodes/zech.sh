document.addEventListener('DOMContentLoaded', function() {
    // User menu toggle
    var toggle = document.querySelector('.zech-user-toggle');
    var menu = document.querySelector('.zech-user-menu');
    if (toggle && menu) {
        toggle.addEventListener('click', function(e) {
            e.stopPropagation();
            menu.classList.toggle('open');
        });
        document.addEventListener('click', function() {
            menu.classList.remove('open');
        });
    }

    // Flash dismissal
    document.querySelectorAll('.sk-flash-dismiss').forEach(function(btn) {
        btn.addEventListener('click', function() {
            this.closest('.sk-flash').remove();
        });
    });

    // Mobile nav toggle
    var navToggle = document.querySelector('.zech-nav-toggle');
    var navRight = document.querySelector('.zech-topnav-right');
    if (navToggle && navRight) {
        navToggle.addEventListener('click', function() {
            navToggle.classList.toggle('open');
            navRight.classList.toggle('open');
            navToggle.setAttribute('aria-expanded',
                navToggle.classList.contains('open') ? 'true' : 'false');
        });
    }

    // Back to top button
    var backTop = document.querySelector('.dump-back-top');
    if (backTop) {
        var ticking = false;
        window.addEventListener('scroll', function() {
            if (!ticking) {
                requestAnimationFrame(function() {
                    backTop.classList.toggle('visible', window.scrollY > 400);
                    ticking = false;
                });
                ticking = true;
            }
        }, { passive: true });

        backTop.addEventListener('click', function() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }
});
