// FUSION: Scroll reveal + Storm glitch triggers
(function() {
  document.addEventListener('DOMContentLoaded', function() {

    // Scroll reveal via IntersectionObserver
    const observer = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) {
        if (e.isIntersecting) e.target.classList.add('visible');
      });
    }, { threshold: 0.15 });

    document.querySelectorAll('.zech-stat, .zech-card').forEach(function(el, i) {
      el.style.transitionDelay = (i % 4) * 0.12 + 's';
      observer.observe(el);
    });

    // Storm glitch: aggressive, frequent, with double-hit
    const glitchEl = document.querySelector('.zech-glitch');
    if (!glitchEl) return;

    function triggerGlitch() {
      const x = (Math.random() - 0.5) * 24;
      const skew = (Math.random() - 0.5) * 16;
      glitchEl.style.transform = 'translateX(' + x + 'px) skewX(' + skew + 'deg)';

      // Double-hit 50% of the time
      if (Math.random() > 0.5) {
        setTimeout(function() {
          const x2 = (Math.random() - 0.5) * 16;
          const skew2 = (Math.random() - 0.5) * 10;
          glitchEl.style.transform = 'translateX(' + x2 + 'px) skewX(' + skew2 + 'deg)';
        }, 50);
      }

      setTimeout(function() { glitchEl.style.transform = ''; }, 120);
      setTimeout(triggerGlitch, 1500 + Math.random() * 1500);
    }

    setTimeout(triggerGlitch, 1500);
  });
})();
