"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  init: () => ipcRenderer.invoke("app:init"),
  listApiKeys: () => ipcRenderer.invoke("apiKeys:list"),
  addApiKey: (key) => ipcRenderer.invoke("apiKeys:add", key),
  removeApiKey: (varName) => ipcRenderer.invoke("apiKeys:remove", varName),
  setAvatarVideo: (accountId) => ipcRenderer.invoke("avatar:setVideo", accountId),
  removeAvatarVideo: (accountId) => ipcRenderer.invoke("avatar:removeVideo", accountId),
  addProducts: (folder) => ipcRenderer.invoke("products:add", folder),
  removeProducts: (folder, fileNames) => ipcRenderer.invoke("products:remove", folder, fileNames),
  chooseOutputFolder: () => ipcRenderer.invoke("output:choose"),
  runPipeline: (dryRun, autoPost) => ipcRenderer.invoke("pipeline:run", dryRun, autoPost),
  getHistory: () => ipcRenderer.invoke("history:get"),

  // -- Contas do TikTok --
  listAccounts: () => ipcRenderer.invoke("accounts:list"),
  saveAccount: (dados) => ipcRenderer.invoke("accounts:save", dados),
  removeAccount: (id) => ipcRenderer.invoke("accounts:remove", id),
  logoutAccount: (id) => ipcRenderer.invoke("accounts:logout", id),
  verifyAccount: (id) => ipcRenderer.invoke("accounts:verify", id),
  openAccountFolder: (id) => ipcRenderer.invoke("accounts:openFolder", id),
  discoverAccounts: () => ipcRenderer.invoke("accounts:discover"),
  loginAccount: (id) => ipcRenderer.invoke("accounts:login", id),
  cancelLogin: () => ipcRenderer.invoke("accounts:login:cancel"),
  onLoginLog: (callback) => ipcRenderer.on("accounts:login:log", (_event, data) => callback(data)),
  onPipelineLog: (callback) => ipcRenderer.on("pipeline:log", (_event, line) => callback(line)),
  onPipelineProgress: (callback) => ipcRenderer.on("pipeline:progress", (_event, data) => callback(data)),
  removeAllPipelineListeners: () => {
    ipcRenderer.removeAllListeners("pipeline:log");
    ipcRenderer.removeAllListeners("pipeline:progress");
  },
});
