// ============================================================
// PROVIDER BOOTSTRAP — FAST
// Parallel fetch + session cache. Skips subcollection (you have none).
// ============================================================

const firebaseConfig = {
    apiKey: "AIzaSyDTA9U6UEzOhNy7lDKuQ-AFBMocHf-gtU8",
    authDomain: "skillbridge-70863.firebaseapp.com",
    projectId: "skillbridge-70863",
    storageBucket: "skillbridge-70863.firebasestorage.app",
    messagingSenderId: "912671486056",
    appId: "1:912671486056:web:6f2dc11ab2c6042480cbdd"
};

firebase.initializeApp(firebaseConfig);
const auth = firebase.auth();
const db   = firebase.firestore();

// Session cache keys
const K_AUTH     = 'pn_auth';
const K_PROVIDER = 'pn_provider';
const K_TRAINEES = 'pn_trainees';

const TTL = {
    AUTH:     30 * 60 * 1000,   // 30 min
    PROVIDER: 10 * 60 * 1000,   // 10 min
    TRAINEES: 60 * 1000         // 60 sec
};

function cGet(k, ttl) {
    try {
        const raw = sessionStorage.getItem(k);
        if (!raw) return null;
        const { t, v } = JSON.parse(raw);
        if (Date.now() - t > ttl) { sessionStorage.removeItem(k); return null; }
        return v;
    } catch { return null; }
}
function cSet(k, v) {
    try { sessionStorage.setItem(k, JSON.stringify({ t: Date.now(), v })); } catch {}
}
function cClear(k) { try { sessionStorage.removeItem(k); } catch {} }

let _user = null;
let _profile = null;
let _provider = null;

// ─────────────────────────────────────────────────────────
// 1. AUTH — uses cached result, otherwise verifies with Firebase
// ─────────────────────────────────────────────────────────
async function fastAuth() {
    // Try cache first
    const cached = cGet(K_AUTH, TTL.AUTH);
    if (cached?.uid && auth.currentUser?.uid === cached.uid) {
        _user    = auth.currentUser;
        _profile = cached.profile || null;
        console.log('⚡ Auth from cache');
        return _user;
    }

    return new Promise((resolve, reject) => {
        const unsub = auth.onAuthStateChanged(async fbUser => {
            unsub();
            if (!fbUser) {
                window.location.href = '../auth/login.html?role=provider';
                reject('no-auth');
                return;
            }
            _user = fbUser;

            // ⚡ Parallel: fetch users/{uid} AND providers/{uid} at same time
            const [userSnap, provSnap] = await Promise.all([
                db.collection('users').doc(fbUser.uid).get().catch(() => null),
                db.collection('providers').doc(fbUser.uid).get().catch(() => null)
            ]);

            const userData = userSnap?.exists ? userSnap.data() : {};
            const provData = provSnap?.exists ? provSnap.data() : {};

            if (!userData.role && !provData.orgName) {
                window.location.href = '../auth/login.html?role=provider';
                reject('no-profile');
                return;
            }

            const role = userData.role || 'provider';
            if (role !== 'provider') {
                await auth.signOut();
                window.location.href = '../auth/login.html?role=provider';
                reject('wrong-role');
                return;
            }

            _profile = userData;

            // Merge into provider data
            const merged = { ...userData, ...provData };
            _provider = {
                uid: fbUser.uid,
                providerId: merged.providerCode || merged.providerId || ('PRV' + fbUser.uid.substring(0,6).toUpperCase()),
                orgName: merged.orgName || merged.organizationName || merged.centreName || fbUser.displayName || 'Your Training Centre',
                contactPerson: merged.repName || merged.contactPerson || merged.ownerName || fbUser.displayName || 'Provider',
                email: merged.email || fbUser.email || '',
                mobile: merged.mobile || merged.repMobile || merged.phone || 'N/A',
                state: merged.state || 'N/A',
                district: merged.district || 'N/A',
                status: merged.status || 'Active'
            };

            cSet(K_AUTH, { uid: fbUser.uid, profile: userData });
            cSet(K_PROVIDER, _provider);

            console.log('✅ Auth + provider fetched in parallel');
            resolve(fbUser);
        });
    });
}

// ─────────────────────────────────────────────────────────
// 2. PROVIDER — cached
// ─────────────────────────────────────────────────────────
async function fastProvider() {
    if (_provider) return _provider;

    const cached = cGet(K_PROVIDER, TTL.PROVIDER);
    if (cached?.uid === _user?.uid) {
        _provider = cached;
        console.log('⚡ Provider from cache');
        return cached;
    }

    // Should have been fetched during fastAuth — fallback
    const snap = await db.collection('providers').doc(_user.uid).get();
    const data = snap.exists ? snap.data() : {};

    _provider = {
        uid: _user.uid,
        providerId: data.providerCode || data.providerId || ('PRV' + _user.uid.substring(0,6).toUpperCase()),
        orgName: data.orgName || data.organizationName || 'Your Training Centre',
        contactPerson: data.contactPerson || data.repName || 'Provider',
        email: data.email || _user.email || '',
        mobile: data.mobile || 'N/A',
        state: data.state || 'N/A',
        district: data.district || 'N/A',
        status: data.status || 'Active'
    };
    cSet(K_PROVIDER, _provider);
    return _provider;
}

// ─────────────────────────────────────────────────────────
// 3. TRAINEES — ONE query, no wasted subcollection call
// ─────────────────────────────────────────────────────────
async function fastTrainees(forceRefresh = false) {
    if (!_user) return { trainees: [], isSample: true, totalDocs: 0 };

    // Cache check
    if (!forceRefresh) {
        const cached = cGet(K_TRAINEES, TTL.TRAINEES);
        if (cached?.uid === _user.uid) {
            console.log(`⚡ Trainees from cache (${cached.trainees.length})`);
            return { trainees: cached.trainees, isSample: false, totalDocs: cached.totalDocs, fromCache: true };
        }
    }

    // ⚡ ONE single query — you confirmed all 16 docs use providerUid
    let docs = [];
    try {
        const snap = await db.collection('trainees')
            .where('providerUid', '==', _user.uid)
            .limit(200)
            .get();
        snap.forEach(d => docs.push({ _id: d.id, ...d.data() }));
    } catch (e) {
        console.warn('Trainee query failed:', e.message);
    }

    if (docs.length === 0) {
        console.log('❌ No trainees matched — check Firestore data');
        return { trainees: [], isSample: true, totalDocs: 0 };
    }

    const COLORS = ['#2563eb','#7c3aed','#16a34a','#d97706','#dc2626','#0d9488'];

    const mapped = docs.map((t, i) => {
        // Employment block — normalize
        const emp = t.employment || {};
        const empStatus = String(emp.status || t.employmentStatus || '').toLowerCase();

        let status = 'pending';
        if (empStatus.includes('self')) status = 'self';
        else if (empStatus.includes('employ') && !empStatus.includes('unemploy')) status = 'employed';
        else if (empStatus.includes('unemploy') || empStatus.includes('looking')) status = 'unemployed';

        return {
            id: t.traineeCode || t.enrollmentId || ('TRN' + i),
            uid: t.uid || t._id,
            name: t.fullName || 'Trainee',
            email: t.email || '',
            mobile: t.mobile || 'N/A',
            course: t.skillTrade || t.trainingProgram || 'Training',
            provider: t.trainingProvider || _provider?.orgName || 'N/A',
            batch: t.batchId || 'N/A',
            registrationDate: (t.registeredAt || t.createdAt || '').toString().split('T')[0] || 'N/A',
            trainingProgress: Number(t.trainingProgress) || 0,
            certificationStatus: t.completionDate ? 'Completed' : 'Pending',
            company: emp.companyName || 'N/A',
            jobRole: emp.jobRole || t.preferredRole || 'N/A',
            currentIncome: Number(emp.currentIncome) || Number(t.income) || 0,
            employmentStatus: emp.status || t.employmentStatus || 'Not Updated',
            status: status,
            avatar: (t.fullName || 'T').charAt(0).toUpperCase(),
            color: COLORS[i % COLORS.length],
            retention: status === 'employed',
            _sample: false
        };
    });

    cSet(K_TRAINEES, { uid: _user.uid, trainees: mapped, totalDocs: docs.length });
    console.log(`✅ Trainees fetched: ${mapped.length}`);
    return { trainees: mapped, isSample: false, totalDocs: docs.length, fromCache: false };
}

function clearAllCache() {
    [K_AUTH, K_PROVIDER, K_TRAINEES].forEach(cClear);
}

window.ProviderNet = {
    auth: fastAuth,
    provider: fastProvider,
    trainees: fastTrainees,
    clearCache: clearAllCache,
    db, auth: auth
};