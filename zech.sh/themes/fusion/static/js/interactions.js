// FUSION: Scroll reveal + Storm glitch + Flash dismissal + User menu
(function() {
  document.addEventListener('DOMContentLoaded', function() {

    // Scroll reveal via IntersectionObserver
    var observer = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) {
        if (e.isIntersecting) e.target.classList.add('visible');
      });
    }, { threshold: 0.15 });

    document.querySelectorAll('.zech-stat, .zech-card').forEach(function(el, i) {
      el.style.transitionDelay = (i % 4) * 0.12 + 's';
      observer.observe(el);
    });

    // Hero tag separators: show // only when on same line as adjacent text
    var seps = document.querySelectorAll('.zech-hero-sep');
    function updateSeps() {
      seps.forEach(function(sep) {
        var prev = sep.previousElementSibling;
        if (!prev) return;
        var sameLine = prev.getBoundingClientRect().top === sep.getBoundingClientRect().top;
        sep.style.visibility = sameLine ? 'visible' : 'hidden';
        sep.style.width = sameLine ? 'auto' : '0';
      });
    }
    if (seps.length) {
      updateSeps();
      window.addEventListener('resize', updateSeps);
    }

    // Storm glitch: aggressive, frequent, with double-hit
    var glitchEl = document.querySelector('.zech-glitch');
    if (glitchEl) {
      function triggerGlitch() {
        var x = (Math.random() - 0.5) * 24;
        var skew = (Math.random() - 0.5) * 16;
        glitchEl.style.transform = 'translateX(' + x + 'px) skewX(' + skew + 'deg)';

        if (Math.random() > 0.5) {
          setTimeout(function() {
            var x2 = (Math.random() - 0.5) * 16;
            var skew2 = (Math.random() - 0.5) * 10;
            glitchEl.style.transform = 'translateX(' + x2 + 'px) skewX(' + skew2 + 'deg)';
          }, 50);
        }

        setTimeout(function() { glitchEl.style.transform = ''; }, 120);
        setTimeout(triggerGlitch, 1500 + Math.random() * 1500);
      }
      setTimeout(triggerGlitch, 1500);
    }

    // Flash message dismissal
    document.querySelectorAll('.sk-flash-dismiss').forEach(function(btn) {
      btn.addEventListener('click', function() {
        btn.closest('.sk-flash').remove();
      });
    });
    document.querySelectorAll('.sk-flash-success[data-dismissible]').forEach(function(flash) {
      setTimeout(function() {
        flash.style.opacity = '0';
        setTimeout(function() { flash.remove(); }, 300);
      }, 5000);
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

    // User menu toggle
    var menu = document.querySelector('.zech-user-menu');
    if (menu) {
      var toggle = menu.querySelector('.zech-user-toggle');
      toggle.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        menu.classList.toggle('open');
      });
      menu.addEventListener('click', function(e) {
        e.stopPropagation();
      });
      document.addEventListener('click', function() {
        menu.classList.remove('open');
      });
    }

    // SSE notification status — hook into Skrift's sk:notification-status event
    var statusDot = document.querySelector('.zech-user-dropdown-status .zech-status-dot');
    var statusLabel = document.querySelector('.zech-user-dropdown-status');
    if (statusDot && statusLabel) {
      var statusMap = {
        connected:    { cls: 'connected',    text: 'CONNECTED' },
        connecting:   { cls: 'connecting',   text: 'CONNECTING' },
        reconnecting: { cls: 'connecting',   text: 'OFFLINE' },
        suspended:    { cls: 'suspended',    text: 'SUSPENDED' },
        disconnected: { cls: 'disconnected', text: 'DISCONNECTED' }
      };

      document.addEventListener('sk:notification-status', function(e) {
        var info = statusMap[e.detail.status] || statusMap.disconnected;
        statusDot.className = 'zech-status-dot zech-status-' + info.cls;
        // Update text while preserving the dot element
        var dot = statusLabel.querySelector('.zech-status-dot');
        statusLabel.textContent = '';
        statusLabel.appendChild(dot);
        statusLabel.appendChild(document.createTextNode(info.text));
      });
    }

  });
})();
