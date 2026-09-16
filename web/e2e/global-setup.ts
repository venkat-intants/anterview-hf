// Before any spec: check the stack is up, then provision this run's tenant.
//
// A spec that fails because data_gateway is not running reads like a product
// bug, so this fails first, with the command that fixes it.

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { API_URL, TENANT_FILE, WEB_ROOT, WEB_URL } from './support/env';

async function reachable(url: string): Promise<boolean> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(5_000) });
    return res.ok;
  } catch {
    return false;
  }
}

function pythonFor(repo: string): string {
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON;
  const venv = path.join(repo, 'services', 'data_gateway', '.venv');
  return process.platform === 'win32'
    ? path.join(venv, 'Scripts', 'python.exe')
    : path.join(venv, 'bin', 'python');
}

export default async function globalSetup(): Promise<void> {
  const missing: string[] = [];
  if (!(await reachable(`${API_URL}/health/live`))) {
    missing.push(
      `data_gateway is not answering at ${API_URL}.\n` +
        '    cd services/data_gateway && .venv/Scripts/python -m uvicorn app.main:app --port 8002',
    );
  }
  if (!(await reachable(WEB_URL))) {
    missing.push(`The web app is not answering at ${WEB_URL}.\n    cd web && npm run dev`);
  }
  if (missing.length) {
    throw new Error(`The e2e suite needs the local stack running:\n\n  ${missing.join('\n\n  ')}\n`);
  }

  const webRoot = WEB_ROOT;
  const repo = path.resolve(webRoot, '..');
  const python = pythonFor(repo);
  if (!fs.existsSync(python)) {
    throw new Error(`No Python found at ${python}. Set E2E_PYTHON to data_gateway's virtualenv python.`);
  }

  const result = spawnSync(
    python,
    [path.join(webRoot, 'e2e', 'support', 'provision_tenant.py'), TENANT_FILE],
    { encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' } },
  );
  if (result.status !== 0) {
    throw new Error(
      `Provisioning the e2e tenant failed (exit ${result.status}).\n${result.stderr || result.stdout}`,
    );
  }
}
