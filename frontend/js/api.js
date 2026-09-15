// ============================================================
// SKILLINTEL API CLIENT
// Lets every page talk to the Flask backend at http://127.0.0.1:5000
// ============================================================

// Auto-detect backend URL:
// - If page is served by Flask → use same origin (empty string)
// - If page is opened as file:// → use localhost:5000
const API_BASE = (window.location.protocol === 'file:')
    ? 'http://127.0.0.1:5000'
    : '';

// Simple fetch wrapper
async function apiCall(path, options = {}) {
    const url = API_BASE + path;
    const opts = {
        method: options.method || 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',  // Send cookies to backend
        ...options
    };
    if (opts.body && typeof opts.body === 'object') {
        opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(url, opts);
    let data;
    try { data = await res.json(); } catch (e) { data = {}; }
    return { ok: res.ok, status: res.status, data };
}

// Check if backend is reachable
async function apiHealth() {
    try {
        const r = await apiCall('/api/system/health');
        return r.ok ? r.data : null;
    } catch (e) {
        return null;
    }
}