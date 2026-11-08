// 渲染进程（页面）拿不到 Node，只通过这里暴露几个窗口按钮。
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('strinovaWin', {
  minimize: () => ipcRenderer.send('win:minimize'),
  toggleMaximize: () => ipcRenderer.send('win:toggle-maximize'),
  close: () => ipcRenderer.send('win:close'),
  pickFolder: (initial) => ipcRenderer.invoke('win:pick-folder', initial),
});