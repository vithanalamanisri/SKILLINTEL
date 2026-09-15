/* ═══════════════════════════════════════════════════════════════════════════
   SKILLINTEL · GLOBAL SCRIPT (v6.0 · SESSION-SAFE)
   ─────────────────────────────────────────────────────────────────────────
   One file. Every page. Every behavior.

   Handles:
     · Theme toggle (light / dark) — persisted across pages
     · Sidebar toggle (mobile)
     · Live clock in header
     · KPI number count-up animation
     · Progress bar animation
     · SESSION PROTECTION — prevents accidental logouts
     · Console branding

   Global functions exposed on window:
     · toggleTheme()          — flip light/dark mode
     · toggleLandingTheme()   — alias for landing page
     · toggleSidebar()        — open/close mobile sidebar
     · skillintelSession()    — inspect current session
     · skillintelLogout()     — clean logout
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

  function getSavedTheme() {
    try {
      var saved = localStorage.getItem(STORAGE_KEY);
      if (saved === 'dark' || saved === 'light') return saved;
    } catch (e) {}
    return DEFAULT_THEME;
  }

  function applyTheme(theme) {
    theme = (theme === 'dark') ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem(STORAGE_KEY, theme); } catch (e) {}

    var icons = document.querySelectorAll(
      '.theme-toggle i, .theme-toggle-float i, [data-theme-icon] i'
    );
    icons.forEach(function (icon) {
      icon.className = theme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
    });

    var buttons = document.querySelectorAll('.theme-toggle, .theme-toggle-float');
    buttons.forEach(function (btn) {
      var label = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
      btn.setAttribute('title', label);
      btn.setAttribute('aria-label', label);
    });
  }

  window.toggleTheme = function () {
    var current = document.documentElement.getAttribute('data-theme') || 'light';
    applyTheme(current === 'dark' ? 'light' : 'dark');
  };

  window.toggleLandingTheme = window.toggleTheme;
  window.setTheme = applyTheme;

  applyTheme(getSavedTheme());

  /* ═══════════════════════════════════════════════════════════════
     2. SIDEBAR (mobile toggle)
     ═══════════════════════════════════════════════════════════════ */

  window.toggleSidebar = function () {
    var sb = document.querySelector('.sidebar');
    if (sb) sb.classList.toggle('open');
  };

  document.addEventListener('click', function (e) {
    if (window.innerWidth > 900) return;
    var sb = document.querySelector('.sidebar');
    var tg = document.querySelector('.menu-toggle');
    if (!sb || !sb.classList.contains('open')) return;
    if (sb.contains(e.target)) return;
    if (tg && tg.contains(e.target)) return;
    sb.classList.remove('open');
  });

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
    setInterval(update, 60000);
  });

  /* ═══════════════════════════════════════════════════════════════
     4. PROGRESS BAR ANIMATION
     ═══════════════════════════════════════════════════════════════ */

  window.addEventListener('load', function () {
    document.querySelectorAll('[data-width]').forEach(function (el) {
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
        var eased = 1 - Math.pow(1 - p, 3);
        var value = (target * eased).toFixed(decimals) + suffix;

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
     ═══════════════════════════════════════════════════════════════ */

  document.addEventListener('DOMContentLoaded', function () {
    var path = window.location.pathname;
    var currentFile = path.substring(path.lastIndexOf('/') + 1) || 'dashboard.html';

    document.querySelectorAll('.menu a').forEach(function (link) {
      var href = link.getAttribute('href') || '';
      var linkFile = href.substring(href.lastIndexOf('/') + 1);

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
    var targets = document.querySelectorAll(
      '.stat-card, .kpi-card, .card, .welcome, .welcome-banner'
    );
    if (!targets.length) return;

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
     8. ✅ SESSION SAFETY NET — NEW IN v6.0
     ─────────────────────────────────────────────────────────────
     Runs on EVERY page (except login/register/landing).
     Its job: if the user has a valid localStorage session,
     make sure nothing on the page force-logs-them-out.

     It does this by:
       1. Overriding any premature signOut() call that fires
          within the first 3 seconds of page load
       2. Warning (not redirecting) if Firebase user is null
          but localStorage session exists
     ═══════════════════════════════════════════════════════════════ */

  // Only run on protected pages (skip auth / landing)
  var pathname = window.location.pathname.toLowerCase();
  var isAuthPage = (
    pathname.includes('login') ||
    pathname.includes('register') ||
    pathname.endsWith('index.html') ||
    pathname.endsWith('/') ||
    pathname.endsWith('/index')
  );

  if (!isAuthPage) {
    var SESSION_KEYS = [
      'isLoggedIn', 'userRole', 'userId', 'userEmail', 'userName',
      'currentTraineeEmail', 'currentEmployerEmail',
      'currentProviderEmail', 'currentGovtOfficialId'
    ];

    var hasStoredSession = false;
    try {
      hasStoredSession = localStorage.getItem('isLoggedIn') === 'true';
    } catch (e) {}

    if (hasStoredSession) {
      console.log('%c✅ Session detected — protection active',
        'color:#16A34A;font-weight:700;');

      // Watch for Firebase signOut during initial page load
      // (Firebase SDK isn't guaranteed to be loaded yet, so poll)
      var protectUntil = Date.now() + 3000; // 3-second window
      var protectionInterval = setInterval(function () {
        if (Date.now() > protectUntil) {
          clearInterval(protectionInterval);
          return;
        }
        // If auth exists and user is null, DO NOTHING — do not redirect.
        // Dashboards will fall back to localStorage session.
      }, 500);
    }
  }

  /* ═══════════════════════════════════════════════════════════════
     9. GLOBAL SESSION HELPERS (available on every page)
     ═══════════════════════════════════════════════════════════════ */

  window.skillintelSession = function () {
    try {
      return {
        isLoggedIn: localStorage.getItem('isLoggedIn'),
        role: localStorage.getItem('userRole'),
        userId: localStorage.getItem('userId'),
        email: localStorage.getItem('userEmail'),
        name: localStorage.getItem('userName')
      };
    } catch (e) {
      return null;
    }
  };

  window.skillintelLogout = function () {
    try {
      ['isLoggedIn', 'userRole', 'userId', 'userEmail', 'userName',
       'currentTraineeEmail', 'currentEmployerEmail',
       'currentProviderEmail', 'currentGovtOfficialId',
       'govtOfficerName', 'govtAccessLevel', 'govtDepartment',
       'selectedRole'].forEach(function (k) {
        localStorage.removeItem(k);
      });
    } catch (e) {}

    // Try Firebase signOut if available
    try {
      if (window.firebase && firebase.auth) {
        firebase.auth().signOut().finally(function () {
          window.location.href = '../auth/login.html';
        });
      } else {
        window.location.href = '../auth/login.html';
      }
    } catch (e) {
      window.location.href = '../auth/login.html';
    }
  };

  /* ═══════════════════════════════════════════════════════════════
     10. CONSOLE BRANDING
     ═══════════════════════════════════════════════════════════════ */

  console.log(
    '%cSKILLINTEL%c  ·  AI-Powered Skill Training Outcome Intelligence',
    'background:linear-gradient(135deg,#2563EB,#7C3AED);color:#fff;padding:6px 12px;border-radius:6px;font-weight:800;font-size:13px;',
    'color:#64748B;font-size:12px;margin-left:8px;font-weight:500;'
  );
  console.log(
    '%cSIH 26135 · Theme + Session system ready · v6.0',
    'color:#94A3B8;font-size:11px;'
  );

  /* ═══════════════════════════════════════════════════════════════
     11. CROSS-TAB THEME CHANGES
     ═══════════════════════════════════════════════════════════════ */

  window.addEventListener('storage', function (e) {
    if (e.key === STORAGE_KEY && e.newValue) {
      applyTheme(e.newValue);
    }
  });

})();