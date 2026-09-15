// ============================================================
// SkillBridge · api.js
// Safe wrapper around fetch that:
//   - ALWAYS sends cookies (credentials: 'include')
//   - NEVER throws — always returns { ok, data, status }
//   - Works across all pages (login, register, dashboards)
// ============================================================

(function (global) {

    // ------------------------------------------------------------
    // CORE: apiCall(url, opts)
    // ------------------------------------------------------------
    async function apiCall(url, opts) {
        opts = opts || {};
        const method = (opts.method || 'GET').toUpperCase();
        const headers = Object.assign(
            { 'Accept': 'application/json' },
            opts.headers || {}
        );

        let body = opts.body;
        if (body && typeof body === 'object' && !(body instanceof FormData)) {
            headers['Content-Type'] = 'application/json';
            body = JSON.stringify(body);
        }

        try {
            const res = await fetch(url, {
                method: method,
                headers: headers,
                body: body,
                credentials: 'include',      // ← CRITICAL: sends session cookie
                cache: 'no-store',
            });

            // Try to parse JSON, fall back to text
            let data = null;
            const text = await res.text();
            if (text) {
                try { data = JSON.parse(text); }
                catch (e) { data = { raw: text }; }
            }

            // Success = 2xx
            if (res.ok) {
                return { ok: true, status: res.status, data: data };
            }

            // Non-2xx → still return data so UI can show the error
            return {
                ok: false,
                status: res.status,
                data: data || { error: 'HTTP ' + res.status },
            };

        } catch (err) {
            console.warn('[apiCall] network error:', url, err && err.message);
            return {
                ok: false,
                status: 0,
                data: { error: 'Network error — cannot reach server' },
            };
        }
    }

    // ------------------------------------------------------------
    // CONVENIENCE: apiGet / apiPost / apiPut / apiDelete
    // ------------------------------------------------------------
    function apiGet(url) {
        return apiCall(url, { method: 'GET' });
    }
    function apiPost(url, body) {
        return apiCall(url, { method: 'POST', body: body });
    }
    function apiPut(url, body) {
        return apiCall(url, { method: 'PUT', body: body });
    }
    function apiDelete(url) {
        return apiCall(url, { method: 'DELETE' });
    }

    // ------------------------------------------------------------
    // SESSION HELPERS
    // ------------------------------------------------------------
    async function getCurrentUser() {
        const res = await apiGet('/api/auth/me');
        if (res.ok && res.data) return res.data;
        return null;
    }

    function clearSession() {
        try {
            ['isLoggedIn','userRole','userId','userName','userEmail',
             'currentTraineeEmail','currentEmployerEmail','currentProviderEmail',
             'currentGovtOfficialId','govtOfficerName','govtAccessLevel']
                .forEach(function (k) { localStorage.removeItem(k); });
        } catch (e) {}
    }

    async function logout() {
        try { await apiPost('/api/auth/logout', {}); } catch (e) {}
        clearSession();
        window.location.href = '../auth/login.html';
    }

    // ------------------------------------------------------------
    // EXPORT TO GLOBAL SCOPE
    // ------------------------------------------------------------
    global.apiCall = apiCall;
    global.apiGet = apiGet;
    global.apiPost = apiPost;
    global.apiPut = apiPut;
    global.apiDelete = apiDelete;
    global.getCurrentUser = getCurrentUser;
    global.clearSession = clearSession;
    global.logout = logout;

    console.log('✅ api.js loaded — cookies enabled, safe wrapper active');

})(window);