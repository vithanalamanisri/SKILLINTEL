/* ═══════════════════════════════════════════════════════════════════════════
   SKILLINTEL · GLOBAL SCRIPT (v5.0 · FINAL)
   ─────────────────────────────────────────────────────────────────────────
   One file. Every page. Every behavior.

   Handles:
     · Theme toggle (light / dark) — persisted across pages
     · Sidebar toggle (mobile)
     · Live clock in header
     · KPI number count-up animation
     · Progress bar animation
     · Console branding

   Used by:
     · frontend/index.html                (landing)
     · frontend/auth/*.html               (login + registers)
     · frontend/employer/*.html
     · frontend/government/*.html
     · frontend/provider/*.html
     · frontend/trainee/*.html

   Global functions exposed on window:
     · toggleTheme()          — flip light/dark mode
     · toggleLandingTheme()   — alias for landing page
     · toggleSidebar()        — open/close mobile sidebar
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
  'use strict';

  /* ═══════════════════════════════════════════════════════════════
     0. CONSTANTS
     ═══════════════════════════════════════════════════════════════ */
  var STORAGE_KEY   = 'skillintel-theme';
  var DEFAULT_THEME = 'light';

  /* ═══════════════════════════════════════════════════════════════
     1. THEME SYSTEM
     ═══════════════════════════════════════════════════════════════ */

  /**
   * Safely read the saved theme from localStorage.
   * Returns 'light' if anything fails.
   */
  function getSavedTheme() {
    try {
      var saved = localStorage.getItem(STORAGE_KEY);
      if (saved === 'dark' || saved === 'light') return saved;
    } catch (e) {
      /* localStorage blocked — ignore */
    }
    return DEFAULT_THEME;
  }

  /**
   * Apply a theme:
   *   · Sets data-theme on <html>
   *   · Saves to localStorage
   *   · Updates every toggle icon on the page
   *   · Updates button aria-label + title
   */
  function applyTheme(theme) {
    // Normalize
    theme = (theme === 'dark') ? 'dark' : 'light';

    // Set attribute on <html>
    document.documentElement.setAttribute('data-theme', theme);

    // Persist
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (e) { /* ignore */ }

    // Update every toggle icon
    var icons = document.querySelectorAll(
      '.theme-toggle i, .theme-toggle-float i, [data-theme-icon] i'
    );
    icons.forEach(function (icon) {
      icon.className = theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
    });

    // Update button labels
    var buttons = document.querySelectorAll('.theme-toggle, .theme-toggle-float');
    buttons.forEach(function (btn) {
      var label = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
      btn.setAttribute('title', label);
      btn.setAttribute('aria-label', label);
    });
  }

  /**
   * Flip the current theme.
   * Exposed globally so onclick="toggleTheme()" works.
   */
  window.toggleTheme = function () {
    var current = document.documentElement.getAttribute('data-theme') || 'light';
    applyTheme(current === 'dark' ? 'light' : 'dark');
  };

  /**
   * Alias used by the landing page (index.html).
   * Both functions do the exact same thing.
   */
  window.toggleLandingTheme = window.toggleTheme;

  /**
   * Force a specific theme from anywhere.
   * Example: setTheme('dark')
   */
  window.setTheme = applyTheme;

  /* Apply on page load — this runs IMMEDIATELY, before DOMContentLoaded,
     so there's no "flash of light" on page load. */
  applyTheme(getSavedTheme());

  /* ═══════════════════════════════════════════════════════════════
     2. SIDEBAR (mobile toggle)
     ═══════════════════════════════════════════════════════════════ */

  window.toggleSidebar = function () {
    var sb = document.querySelector('.sidebar');
    if (sb) sb.classList.toggle('open');
  };

  /* Close sidebar when clicking outside (mobile only) */
  document.addEventListener('click', function (e) {
    if (window.innerWidth > 900) return;

    var sb = document.querySelector('.sidebar');
    var tg = document.querySelector('.menu-toggle');
    if (!sb || !sb.classList.contains('open')) return;
    if (sb.contains(e.target)) return;
    if (tg && tg.contains(e.target)) return;

    sb.classList.remove('open');
  });

  /* Close sidebar on Escape key (mobile) */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var sb = document.querySelector('.sidebar');
    if (sb && sb.classList.contains('open')) {
      sb.classList.remove('open');
    }
  });

  /* ═══════════════════════════════════════════════════════════════
     3. LIVE CLOCK
     ═══════════════════════════════════════════════════════════════ */

  document.addEventListener('DOMContentLoaded', function () {
    var el = document.getElementById('liveDate');
    if (!el) return;

    function update() {
      try {
        el.textContent = new Date().toLocaleString('en-IN', {
          day: '2-digit',
          month: 'short',
          year: 'numeric',
          hour: '2-digit',
          minute: '2-digit'
        });
      } catch (e) {
        el.textContent = new Date().toLocaleString();
      }
    }

    update();
    setInterval(update, 60000); // refresh every minute
  });

  /* ═══════════════════════════════════════════════════════════════
     4. PROGRESS BAR ANIMATION
     ═══════════════════════════════════════════════════════════════ */

  window.addEventListener('load', function () {
    document.querySelectorAll('[data-width]').forEach(function (el) {
      // Small delay so the transition looks smooth
      requestAnimationFrame(function () {
        el.style.width = el.dataset.width;
      });
    });
  });

  /* ═══════════════════════════════════════════════════════════════
     5. KPI NUMBER COUNT-UP ANIMATION
     ═══════════════════════════════════════════════════════════════ */

  document.addEventListener('DOMContentLoaded', function () {
    var elements = document.querySelectorAll(
      '.kpi-value[data-target], .stat-card h3[data-target]'
    );

    elements.forEach(function (el) {
      var target = parseFloat(el.dataset.target);
      if (isNaN(target)) return;

      var suffix   = el.dataset.suffix   || '';
      var decimals = parseInt(el.dataset.decimals || '0', 10);
      var duration = parseInt(el.dataset.duration || '1200', 10);
      var startTime = null;

      function step(ts) {
        if (!startTime) startTime = ts;
        var p = Math.min((ts - startTime) / duration, 1);
        var eased = 1 - Math.pow(1 - p, 3); // ease-out cubic
        var value = (target * eased).toFixed(decimals) + suffix;

        // Replace only the first text node (preserves child <span class="kpi-unit">)
        var firstChild = el.firstChild;
        if (firstChild && firstChild.nodeType === 3) {
          firstChild.textContent = value;
        } else {
          el.insertBefore(document.createTextNode(value), el.firstChild);
        }

        if (p < 1) requestAnimationFrame(step);
      }

      requestAnimationFrame(step);
    });
  });

  /* ═══════════════════════════════════════════════════════════════
     6. AUTO-ACTIVE MENU LINK
     ─────────────────────────────────────────────────────────────
     If you don't manually add class="active" to the current menu
     link, this script will do it based on the current filename.
     ═══════════════════════════════════════════════════════════════ */

  document.addEventListener('DOMContentLoaded', function () {
    var path = window.location.pathname;
    var currentFile = path.substring(path.lastIndexOf('/') + 1) || 'dashboard.html';

    document.querySelectorAll('.menu a').forEach(function (link) {
      var href = link.getAttribute('href') || '';
      var linkFile = href.substring(href.lastIndexOf('/') + 1);

      // Only set active if no link already has it
      var anyActive = document.querySelector('.menu a.active');
      if (!anyActive && linkFile === currentFile) {
        link.classList.add('active');
      }
    });
  });

  /* ═══════════════════════════════════════════════════════════════
     7. FADE-IN ANIMATION FOR CARDS
     ═══════════════════════════════════════════════════════════════ */

  document.addEventListener('DOMContentLoaded', function () {
    // Only run if the page has fade-in-able elements
    var targets = document.querySelectorAll(
      '.stat-card, .kpi-card, .card, .welcome, .welcome-banner'
    );
    if (!targets.length) return;

    // Use IntersectionObserver for smooth entrance (only if supported)
    if ('IntersectionObserver' in window) {
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.style.opacity = '1';
            entry.target.style.transform = 'translateY(0)';
            observer.unobserve(entry.target);
          }
        });
      }, { threshold: 0.05 });

      targets.forEach(function (el, i) {
        el.style.opacity = '0';
        el.style.transform = 'translateY(8px)';
        el.style.transition = 'opacity 0.4s cubic-bezier(0.16,1,0.3,1), transform 0.4s cubic-bezier(0.16,1,0.3,1)';
        el.style.transitionDelay = Math.min(i * 30, 240) + 'ms';
        observer.observe(el);
      });
    }
  });

  /* ═══════════════════════════════════════════════════════════════
     8. CONSOLE BRANDING (dev-friendly)
     ═══════════════════════════════════════════════════════════════ */

  console.log(
    '%cSKILLINTEL%c  ·  AI-Powered Skill Training Outcome Intelligence',
    'background:linear-gradient(135deg,#2563EB,#7C3AED);color:#fff;padding:6px 12px;border-radius:6px;font-weight:800;font-size:13px;',
    'color:#64748B;font-size:12px;margin-left:8px;font-weight:500;'
  );
  console.log(
    '%cSIH 26135 · Theme system ready · toggleTheme() · toggleSidebar()',
    'color:#94A3B8;font-size:11px;'
  );

  /* ═══════════════════════════════════════════════════════════════
     9. LISTEN FOR CROSS-TAB THEME CHANGES
     ─────────────────────────────────────────────────────────────
     If the user toggles the theme in one tab, other tabs update too.
     ═══════════════════════════════════════════════════════════════ */

  window.addEventListener('storage', function (e) {
    if (e.key === STORAGE_KEY && e.newValue) {
      applyTheme(e.newValue);
    }
  });

})();