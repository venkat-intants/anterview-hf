// Where the stack is, and where this run's tenant is written. One place, so the
// config, the setup and the fixtures cannot disagree.

import path from 'node:path';
import { fileURLToPath } from 'node:url';

// web/package.json is "type": "module", so there is no __dirname here.
const here = path.dirname(fileURLToPath(import.meta.url));

export const WEB_URL = process.env.E2E_WEB_URL ?? 'http://localhost:5174';
export const API_URL = process.env.E2E_API_URL ?? 'http://localhost:8002';

/** Git-ignored. Holds passwords for this run's throwaway local accounts. */
export const TENANT_FILE = path.resolve(here, '..', '.auth', 'tenant.json');

/** web/ — the e2e runner's working directory. */
export const WEB_ROOT = path.resolve(here, '..', '..');
