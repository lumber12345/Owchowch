const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { Readable } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const extractZip = require('extract-zip');

const PYTHON_VERSION = '3.13.16';
const ZIP_NAME = `python-${PYTHON_VERSION}-embed-amd64.zip`;
const DOWNLOAD_URL = `https://www.python.org/ftp/python/${PYTHON_VERSION}/${ZIP_NAME}`;
const SHA256 = '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297';
const DESKTOP_DIR = __dirname;
const CACHE_DIR = path.join(DESKTOP_DIR, '.cache');
const ZIP_PATH = path.join(CACHE_DIR, ZIP_NAME);
const RUNTIME_DIR = path.join(DESKTOP_DIR, 'python-runtime');
const MARKER = path.join(RUNTIME_DIR, '.python-runtime-version');

async function sha256(filePath) {
  const hash = crypto.createHash('sha256');
  await pipeline(fs.createReadStream(filePath), hash);
  return hash.digest('hex');
}

async function downloadPython() {
  await fs.promises.mkdir(CACHE_DIR, { recursive: true });
  if (fs.existsSync(ZIP_PATH) && (await sha256(ZIP_PATH)) === SHA256) return;

  await fs.promises.rm(ZIP_PATH, { force: true });
  console.log(`Downloading the official Python ${PYTHON_VERSION} Windows embeddable runtime…`);
  const response = await fetch(DOWNLOAD_URL, { redirect: 'follow' });
  if (!response.ok || !response.body) {
    throw new Error(`Python download failed: HTTP ${response.status}`);
  }
  await pipeline(Readable.fromWeb(response.body), fs.createWriteStream(ZIP_PATH));
  const actualHash = await sha256(ZIP_PATH);
  if (actualHash !== SHA256) {
    await fs.promises.rm(ZIP_PATH, { force: true });
    throw new Error(`Python runtime checksum mismatch: ${actualHash}`);
  }
}

async function main() {
  const markerText = `${PYTHON_VERSION} ${SHA256}`;
  if (fs.existsSync(MARKER) && (await fs.promises.readFile(MARKER, 'utf8')).trim() === markerText) {
    console.log(`Python ${PYTHON_VERSION} runtime is already prepared.`);
    return;
  }

  await downloadPython();
  await fs.promises.rm(RUNTIME_DIR, { recursive: true, force: true });
  await fs.promises.mkdir(RUNTIME_DIR, { recursive: true });
  await extractZip(ZIP_PATH, { dir: RUNTIME_DIR });

  const pythonExe = path.join(RUNTIME_DIR, 'python.exe');
  const stdlibZip = path.join(RUNTIME_DIR, `python${PYTHON_VERSION.split('.').slice(0, 2).join('')}.zip`);
  if (!fs.existsSync(pythonExe) || !fs.existsSync(stdlibZip)) {
    throw new Error('The downloaded embeddable Python package is missing required runtime files.');
  }
  await fs.promises.writeFile(MARKER, markerText, 'utf8');
  console.log(`Prepared Python ${PYTHON_VERSION} for the Windows installer.`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
