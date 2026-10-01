// Staging testbed bootstrap + health check for Immich (immich.md §10.5).
//
// A fresh staging DB has no users and no libraries, so Immich never indexes the
// photo slice the 30-day sync maintains — staging silently stays empty. This
// script, run daily by the immich-staging-bootstrap CronJob, makes that state
// impossible to miss. Every step is idempotent:
//
//   1. No admin yet → create one (credentials from immich-staging-admin).
//   2. Log in as that admin; fail if the account isn't an admin.
//   3. No library over the slice → create one with prod's import paths.
//   4. Start a library scan, so the day's sync is indexed.
//   5. Health: if the library is more than a day old and still has 0 assets,
//      exit 1. The Job fails and the existing KubeJobFailed alert fires.
//
// Runs on the immich-server image's own Node, so it needs no extra image. Only
// the global fetch API is used.

const API = process.env.IMMICH_API_URL; // e.g. http://immich-server:2283/api
const EMAIL = process.env.ADMIN_EMAIL;
const PASSWORD = process.env.ADMIN_PASSWORD;
const NAME = process.env.ADMIN_NAME || 'Staging Admin';
const LIBRARY_NAME = process.env.LIBRARY_NAME || 'Staging photo slice';
const IMPORT_PATHS = (process.env.IMPORT_PATHS || '').split(',').filter(Boolean).sort();
const EMPTY_GRACE_HOURS = Number(process.env.EMPTY_GRACE_HOURS || 24);

const log = (msg) => console.log(`[bootstrap] ${msg}`);
const fail = (msg) => {
  console.error(`[bootstrap] FAIL: ${msg}`);
  process.exit(1);
};

async function api(method, path, { body, token } = {}) {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: {
      'content-type': 'application/json',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) fail(`${method} ${path} -> ${res.status} ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

// The server may still be starting (or migrating) when the Job runs.
async function waitForServer() {
  for (let i = 1; i <= 30; i++) {
    try {
      const res = await fetch(`${API}/server/ping`);
      if (res.ok) return;
    } catch {
      // not up yet
    }
    log(`server not ready (attempt ${i}/30), retrying in 10s`);
    await new Promise((r) => setTimeout(r, 10_000));
  }
  fail('server never answered /server/ping');
}

if (!API || !EMAIL || !PASSWORD || IMPORT_PATHS.length === 0) {
  fail('IMMICH_API_URL, ADMIN_EMAIL, ADMIN_PASSWORD and IMPORT_PATHS are required');
}

await waitForServer();

const config = await api('GET', '/server/config');
if (!config.isInitialized) {
  await api('POST', '/auth/admin-sign-up', { body: { email: EMAIL, name: NAME, password: PASSWORD } });
  log(`created admin ${EMAIL}`);
}

const login = await api('POST', '/auth/login', { body: { email: EMAIL, password: PASSWORD } });
if (!login.isAdmin) fail(`${EMAIL} logged in but is not an admin`);
const token = login.accessToken;

const libraries = await api('GET', '/libraries', { token });
const same = (l) => [...l.importPaths].sort().join(',') === IMPORT_PATHS.join(',');
let library = libraries.find(same);
if (!library) {
  library = await api('POST', '/libraries', {
    token,
    body: { ownerId: login.userId, name: LIBRARY_NAME, importPaths: IMPORT_PATHS },
  });
  log(`created library ${library.id} over ${IMPORT_PATHS.join(', ')}`);
}

await api('POST', `/libraries/${library.id}/scan`, { token });
log(`scan queued for library ${library.id}`);

const ageHours = (Date.now() - Date.parse(library.createdAt)) / 3_600_000;
log(`library has ${library.assetCount} assets (created ${ageHours.toFixed(1)}h ago)`);
if (library.assetCount === 0 && ageHours > EMPTY_GRACE_HOURS) {
  fail(`library ${library.id} still has 0 assets ${ageHours.toFixed(0)}h after creation — staging is not a usable rehearsal`);
}
log('ok');
