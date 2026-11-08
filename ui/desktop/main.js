// Strinova Star 桌面外壳：起后端（Python）、等就绪、装界面。
// 环境变量（调试用）：STRINOVA_PORT / STRINOVA_PYTHON / STRINOVA_SHOT / STRINOVA_QUERY / STRINOVA_LOG
const { app, BrowserWindow, ipcMain, screen, dialog } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const PORT = Number(process.env.STRINOVA_PORT || 8790);
const URL = 'http://127.0.0.1:' + PORT + '/';
const DATA = app.isPackaged ? path.join(path.dirname(process.execPath), 'data') : path.join(ROOT, 'output');
const LOG = process.env.STRINOVA_LOG || path.join(DATA, 'main.log');

function log(msg) {
  const line = new Date().toISOString() + ' ' + msg + '\n';
  try { fs.mkdirSync(path.dirname(LOG), { recursive: true }); fs.appendFileSync(LOG, line); } catch (_err) {}
  try { process.stdout.write(line); } catch (_err) {}
}

process.on('uncaughtException', (err) => { log('uncaughtException ' + (err && err.stack)); });
process.on('unhandledRejection', (err) => { log('unhandledRejection ' + (err && err.stack)); });

function findPython() {
  if (process.env.STRINOVA_PYTHON) return process.env.STRINOVA_PYTHON;
  const own = path.join(ROOT, 'python', 'python.exe');
  if (fs.existsSync(own)) return own;
  const local = path.join(ROOT, '.venv', 'Scripts', 'python.exe');
  if (fs.existsSync(local)) return local;
  return 'python';
}

const PYTHON = findPython();
let backend = null;
let window = null;

function startBackend() {
  try { fs.mkdirSync(DATA, { recursive: true }); } catch (err) { log('建数据目录失败 ' + err.message); }
  const script = path.join(ROOT, 'src', 'app.py');
  log('start backend python=' + PYTHON + ' script=' + script + ' cwd=' + ROOT + ' data=' + DATA + ' packaged=' + app.isPackaged);
  if (!fs.existsSync(script)) log('缺脚本 ' + script);
  const child = spawn(PYTHON, [script, '--no-open', '--port', String(PORT)], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env: Object.assign({}, process.env, { STRINOVA_OUTPUT: DATA, PYTHONIOENCODING: 'utf-8' }),
  });
  log('spawned pid=' + child.pid);
  child.stdout.on('data', (chunk) => log('[out] ' + String(chunk).trim()));
  child.stderr.on('data', (chunk) => log('[err] ' + String(chunk).trim()));
  child.on('exit', (code, signal) => {
    log('backend exit code=' + code + ' signal=' + signal);
    backend = null;
    if (!app.isQuitting) { log('因后端退出而退出'); app.quit(); }
  });
  child.on('error', (err) => { log('backend 起不来 ' + err.message); });
  return child;
}

async function waitForBackend(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(URL + 'api/state');
      if (res.ok) return true;
    } catch (_err) { /* 还没起来 */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

async function createWindow() {
  // 屏幕可能是 125%/150% 缩放，先按可用工作区夹一下，别让窗口跑到屏幕外
  const work = screen.getPrimaryDisplay().workAreaSize;
  const width = Math.max(960, Math.min(1380, work.width - 60));
  const height = Math.max(620, Math.min(920, work.height - 60));
  window = new BrowserWindow({
    width, height,
    minWidth: Math.min(1080, work.width - 40), minHeight: Math.min(720, work.height - 40),
    center: true,
    show: false, backgroundColor: '#f3f1f5', title: 'Strinova Star',
    autoHideMenuBar: true,
    // 无边框：标题栏由页面自绘，窗口按钮走 preload 的 IPC
    frame: false, titleBarStyle: 'hidden', roundedCorners: true, hasShadow: true,
    webPreferences: {
      contextIsolation: true, nodeIntegration: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });
  window.once('ready-to-show', () => { log('ready-to-show'); window.show(); window.focus(); });
  window.webContents.on('did-fail-load', (_e, code, desc, url) => log('did-fail-load ' + code + ' ' + desc + ' ' + url));
  window.webContents.on('render-process-gone', (_e, details) => log('render gone ' + JSON.stringify(details)));
  try {
    await window.loadURL(URL);
    log('page loaded');
  } catch (err) {
    log('loadURL 失败 ' + err.message);
  }
  if (!window.isVisible()) { window.show(); window.focus(); }
}

app.whenReady().then(async () => {
  log('app ready electron=' + process.versions.electron);
  backend = startBackend();
  const ready = await waitForBackend();
  log('waitForBackend=' + ready);
  if (!ready) { log('后端没起来，退出'); app.quit(); return; }
  await createWindow();
  log('window created');
}).catch((err) => { log('whenReady 异常 ' + err.stack); app.quit(); });


// ---- 窗口按钮（页面里三颗自绘按钮发过来） ----
// 选文件夹：走 Electron 原生对话框，父窗口就是主窗口，一定在最前面
ipcMain.handle('win:pick-folder', async (event, initial) => {
  const parent = BrowserWindow.fromWebContents(event.sender) || window;
  const result = await dialog.showOpenDialog(parent, {
    title: '选录像文件夹',
    defaultPath: initial || undefined,
    properties: ['openDirectory', 'createDirectory'],
  });
  if (result.canceled || !result.filePaths.length) return '';
  return result.filePaths[0];
});
ipcMain.on('win:minimize', () => { if (window) window.minimize(); });
ipcMain.on('win:toggle-maximize', () => {
  if (!window) return;
  if (window.isMaximized()) window.unmaximize(); else window.maximize();
});
ipcMain.on('win:close', () => { if (window) window.close(); });
app.on('window-all-closed', () => { log('window-all-closed'); app.isQuitting = true; app.quit(); });
app.on('before-quit', () => { log('before-quit'); app.isQuitting = true; if (backend) { backend.kill(); backend = null; } });
