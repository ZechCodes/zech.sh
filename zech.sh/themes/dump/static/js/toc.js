/* ==========================================================================
   TOC — Table of Contents for blog posts
   ========================================================================== */

(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var content = document.querySelector('.dump-article-content');
    var body = document.querySelector('.dump-article-body');
    var list = document.querySelector('.dump-toc-list');
    var sidebar = document.querySelector('.dump-toc-sidebar');

    if (!content || !body || !list || !sidebar) return;

    var headings = content.querySelectorAll('h2, h3');
    if (headings.length < 2) {
      sidebar.hidden = true;
      return;
    }

    // Activate grid layout
    body.classList.add('has-toc');

    // Build TOC entries
    var slugCounts = {};
    var tocItems = [];

    headings.forEach(function (heading) {
      var text = heading.textContent.trim();
      var slug = text
        .toLowerCase()
        .replace(/[^\w\s-]/g, '')
        .replace(/\s+/g, '-')
        .replace(/-+/g, '-')
        .replace(/^-|-$/g, '');

      if (slugCounts[slug]) {
        slugCounts[slug]++;
        slug = slug + '-' + slugCounts[slug];
      } else {
        slugCounts[slug] = 1;
      }

      heading.id = slug;

      var li = document.createElement('li');
      li.className = 'dump-toc-item' + (heading.tagName === 'H3' ? ' dump-toc-h3' : '');

      var a = document.createElement('a');
      a.href = '#' + slug;
      a.className = 'dump-toc-link';
      a.textContent = text;

      li.appendChild(a);
      list.appendChild(li);
      tocItems.push({ heading: heading, link: a });
    });

    // Scroll-spy
    var ticking = false;

    function updateActive() {
      var scrollTop = window.scrollY || document.documentElement.scrollTop;
      var threshold = scrollTop + 100;
      var active = null;

      for (var i = 0; i < tocItems.length; i++) {
        if (tocItems[i].heading.offsetTop <= threshold) {
          active = tocItems[i];
        } else {
          break;
        }
      }

      tocItems.forEach(function (item) {
        item.link.classList.remove('toc-active');
      });
      if (active) {
        active.link.classList.add('toc-active');
      }
      ticking = false;
    }

    window.addEventListener('scroll', function () {
      if (!ticking) {
        requestAnimationFrame(updateActive);
        ticking = true;
      }
    }, { passive: true });

    updateActive();

    // Accordion toggle
    var toggle = document.querySelector('.dump-toc-toggle');
    var nav = document.querySelector('.dump-toc');

    if (toggle && nav) {
      toggle.addEventListener('click', function () {
        var expanded = toggle.getAttribute('aria-expanded') === 'true';
        toggle.setAttribute('aria-expanded', String(!expanded));
        nav.classList.toggle('toc-open');
      });
    }

    // Smooth scroll on TOC link click
    list.addEventListener('click', function (e) {
      var link = e.target.closest('.dump-toc-link');
      if (!link) return;
      e.preventDefault();

      var id = link.getAttribute('href').slice(1);
      var target = document.getElementById(id);
      if (!target) return;

      window.scrollTo({
        top: target.offsetTop - 80,
        behavior: 'smooth'
      });

      history.replaceState(null, '', '#' + id);

      // Collapse accordion on mobile
      if (toggle && nav && nav.classList.contains('toc-open')) {
        toggle.setAttribute('aria-expanded', 'false');
        nav.classList.remove('toc-open');
      }
    });

    // Handle initial hash
    if (window.location.hash) {
      var hashTarget = document.getElementById(window.location.hash.slice(1));
      if (hashTarget) {
        setTimeout(function () {
          window.scrollTo({
            top: hashTarget.offsetTop - 80,
            behavior: 'smooth'
          });
        }, 100);
      }
    }
  });
})();
