const { app, BrowserWindow, dialog, shell } = require('electron');
const { spawn } = require('node:child_process');
const net = require('node:net');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const APP_TITLE = 'Owchowch personal war room';
const APP_ID = 'ca.owchowch.personalwarroom';
let mainWindow = null;
let serverProcess = null;
let serverUrl = '';
let quitting = false;

app.setName(APP_TITLE);
app.setAppUserModelId(APP_ID);

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(startApp).catch(async (error) => {
    logDesktopError(error);
    await dialog.showMessageBox({
      type: 'error',
      title: APP_TITLE,
      message: 'The desktop app could not start its local service.',
      detail: String(error?.message || error),
    });
    app.quit();
  });
}

app.on('before-quit', () => {
  quitting = true;
  if (serverProcess && !serverProcess.killed) serverProcess.kill();
});

app.on('window-all-closed', () => {
  app.quit();
});

async function startApp() {
  const port = await startLocalService();
  serverUrl = `http://127.0.0.1:${port}`;
  await createMainWindow();
}

function logDesktopError(error) {
  try {
    const logPath = path.join(app.getPath('userData'), 'desktop-startup.log');
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, `${new Date().toISOString()} ${error?.stack || error}\n`, 'utf8');
  } catch (_) {
    // Do not let a diagnostic write prevent the app's error dialog.
  }
}

function getAppFilesDirectory() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'app')
    : path.resolve(__dirname, '..');
}

function getPythonExecutable() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'python', 'python.exe');
  }
  if (process.env.PYTHON_EXECUTABLE) return process.env.PYTHON_EXECUTABLE;
  return process.platform === 'win32' ? 'python' : 'python3';
}

function canBind(port) {
  return new Promise((resolve) => {
    const probe = net.createServer();
    probe.once('error', () => resolve(false));
    probe.listen(port, '127.0.0.1', () => {
      probe.close(() => resolve(true));
    });
  });
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      probe.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

async function getPersistentPort(portFile) {
  try {
    const saved = Number((await fs.promises.readFile(portFile, 'utf8')).trim());
    if (Number.isInteger(saved) && saved > 1024 && saved < 65536 && await canBind(saved)) return saved;
  } catch (_) {
    // First launch, or a stale port file. A stable free port is selected below.
  }

  const port = await findFreePort();
  await fs.promises.mkdir(path.dirname(portFile), { recursive: true });
  await fs.promises.writeFile(portFile, String(port), 'utf8');
  return port;
}

async function waitForReady(child, readyFile, expectedPort) {
  let spawnError = null;
  child.once('error', (error) => { spawnError = error; });

  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (spawnError) throw spawnError;
    if (child.exitCode !== null) throw new Error(`Local service exited during startup (code ${child.exitCode}).`);
    try {
      const actualPort = Number((await fs.promises.readFile(readyFile, 'utf8')).trim());
      if (Number.isInteger(actualPort) && actualPort === expectedPort) return actualPort;
    } catch (_) {
      // The Python process has not written its readiness file yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('Timed out while starting the local service. Restart the app and try again.');
}

async function startLocalService() {
  const filesDir = getAppFilesDirectory();
  const pythonExe = getPythonExecutable();
  const serverScript = path.join(filesDir, 'app_server.py');
  const userData = app.getPath('userData');
  const portFile = path.join(userData, 'local-server-port.txt');
  const readyFile = path.join(os.tmpdir(), `owchowch-warroom-ready-${process.pid}.txt`);

  if (!fs.existsSync(serverScript)) throw new Error(`App files are missing: ${serverScript}`);
  await fs.promises.rm(readyFile, { force: true });

  let lastError = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const port = await getPersistentPort(portFile);
    const serverLogPath = path.join(userData, 'local-service.log');
    await fs.promises.mkdir(userData, { recursive: true });
    const serverLogFd = fs.openSync(serverLogPath, 'a');
    serverProcess = spawn(pythonExe, [serverScript], {
      cwd: filesDir,
      env: {
        ...process.env,
        HOST: '127.0.0.1',
        PORT: String(port),
        APP_SERVER_READY_FILE: readyFile,
        PYTHONUTF8: '1',
        PYTHONUNBUFFERED: '1',
      },
      windowsHide: true,
      stdio: ['ignore', serverLogFd, serverLogFd],
      detached: false,
    });
    fs.closeSync(serverLogFd);

    serverProcess.once('exit', (code) => {
      if (!quitting && mainWindow && !mainWindow.isDestroyed()) {
        dialog.showErrorBox(APP_TITLE, `The local service stopped unexpectedly (code ${code}). Please restart the app.`);
        app.quit();
      }
    });

    try {
      const readyPort = await waitForReady(serverProcess, readyFile, port);
      await fs.promises.rm(readyFile, { force: true });
      return readyPort;
    } catch (error) {
      lastError = error;
      if (serverProcess && !serverProcess.killed) serverProcess.kill();
      serverProcess = null;
      await fs.promises.rm(readyFile, { force: true });
      await fs.promises.rm(portFile, { force: true });
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
  }
  throw lastError || new Error('Could not start the local service.');
}

async function createMainWindow() {
  const filesDir = getAppFilesDirectory();
  const iconPath = path.join(filesDir, 'icons', 'owchowch.ico');
  mainWindow = new BrowserWindow({
    title: APP_TITLE,
    width: 1320,
    height: 880,
    minWidth: 980,
    minHeight: 680,
    show: false,
    autoHideMenuBar: true,
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    backgroundColor: '#090f15',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.setMenuBarVisibility(false);

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://www.torn.com/')) shell.openExternal(url).catch(() => {});
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(serverUrl)) event.preventDefault();
  });
  mainWindow.once('ready-to-show', () => mainWindow && mainWindow.show());
  mainWindow.on('closed', () => { mainWindow = null; });

  await mainWindow.loadURL(serverUrl);
}
