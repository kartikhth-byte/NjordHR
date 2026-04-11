const path = require("path");
const { BrowserWindow } = require("electron");

function createSplashWindow() {
  const splash = new BrowserWindow({
    width: 520,
    height: 280,
    frame: false,
    resizable: false,
    movable: true,
    show: false,
    center: true,
    backgroundColor: "#f4f7fb",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  splash.loadFile(path.join(__dirname, "..", "renderer", "splash.html"));
  splash.once("ready-to-show", () => splash.show());
  return splash;
}

function createErrorWindow(details) {
  const errorWindow = new BrowserWindow({
    width: 760,
    height: 620,
    show: false,
    resizable: true,
    minimizable: false,
    backgroundColor: "#ffffff",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  errorWindow.loadFile(path.join(__dirname, "..", "renderer", "error.html"), {
    query: { details: JSON.stringify(details || { message: "Startup failed." }) }
  });
  errorWindow.once("ready-to-show", () => errorWindow.show());
  return errorWindow;
}

function createMainWindow(browserUrl, preloadPath) {
  const mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1280,
    minHeight: 760,
    show: false,
    backgroundColor: "#f3f4f6",
    autoHideMenuBar: true,
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  mainWindow.loadURL(browserUrl);
  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    mainWindow.focus();
  });
  return mainWindow;
}

module.exports = {
  createSplashWindow,
  createErrorWindow,
  createMainWindow
};
