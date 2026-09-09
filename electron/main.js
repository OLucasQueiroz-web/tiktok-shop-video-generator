"use strict";

const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const core = require("./core");
const pipeline = require("./pipeline");
const accountsStore = require("./tiktok_accounts");
// tiktok_login.js NÃO é require()ado aqui de propósito -- ele depende do
// puppeteer-extra, e o build empacotado (electron-builder) exclui
// node_modules do app.asar (ver package.json -> build.files). O login roda
// num processo `node` separado (tiktok_login_cli.js -- ver spawnNode() mais
// abaixo), apontando pra pasta real do projeto no disco -- mesmo padrão do
// tiktok_uploader.js.

function resolveBaseDir() {
  // Dois builds empacotados diferentes, dois layouts diferentes:
  //
  //  - Build "dir" (npm run dist, uso do desenvolvedor -- ver atalho da área
  //    de trabalho): usa asar, e fica em <projeto>/release/win-unpacked/<exe>
  //    -- sobem-se 2 níveis pra voltar à raiz do projeto de verdade, onde
  //    moram avatar/, background/, config.json etc. O .exe não pode ser
  //    movido pra fora dessa pasta -- é um "atalho" pro mesmo projeto, não
  //    standalone.
  //
  //  - Instalador autossuficiente (npm run dist:installer, pra mandar pra
  //    outras pessoas -- ver electron-builder.installer.json): SEM asar de
  //    propósito (login/upload rodam num processo separado, que não enxerga
  //    dentro de um .asar -- ver comentário acima). Tudo
  //    (electron/, node_modules/, config.json, ffmpeg/, o Python
  //    empacotado) já vem dentro de resources/app/, um nível acima de
  //    electron/ -- mesma conta do modo dev, sem precisar subir mais níveis.
  //
  // A diferença dá pra detectar em tempo de execução: __dirname só contém
  // ".asar" no primeiro caso.
  if (app.isPackaged && __dirname.includes(".asar")) {
    return path.join(path.dirname(app.getPath("exe")), "..", "..");
  }
  return path.join(__dirname, "..");
}

const baseDir = resolveBaseDir();
core.ensurePortableConfig(baseDir);

let mainWindow = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 900,
    height: 820,
    minWidth: 760,
    minHeight: 620,
    title: "TikTok Shop -- Gerador de Vídeos",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

// -- Estado inicial ----------------------------------------------------------

function buildState() {
  return {
    apiKeys: publicApiKeys(),
    outputFolder: core.getOutputFolder(baseDir),
    accounts: publicAccounts(),
    ...refreshPending(),
  };
}

ipcMain.handle("app:init", () => buildState());

// -- Chaves da API Groq --------------------------------------------------------
// Suporta quantas o usuário quiser: src/product_identifier.py já alterna
// entre GROQ_API_KEY, GROQ_API_KEY2, GROQ_API_KEY3... quando uma delas
// bate no limite de requisições.

/** Nunca manda a chave inteira pro renderer -- só o suficiente pra reconhecer qual é qual. */
function publicApiKeys() {
  return core.listApiKeys(baseDir).map((k) => ({ varName: k.varName, masked: core.maskApiKey(k.key) }));
}

ipcMain.handle("apiKeys:list", () => ({ apiKeys: publicApiKeys() }));

ipcMain.handle("apiKeys:add", (_event, key) => {
  const trimmed = (key || "").trim();
  if (!trimmed) throw new Error("Cole a chave da API Groq antes de adicionar.");
  core.addApiKey(baseDir, trimmed);
  return { apiKeys: publicApiKeys() };
});

ipcMain.handle("apiKeys:remove", async (_event, varName) => {
  const { response } = await dialog.showMessageBox(mainWindow, {
    type: "question",
    buttons: ["Cancelar", "Remover"],
    defaultId: 1,
    cancelId: 0,
    title: "Remover chave",
    message: "Remover essa chave da API Groq?",
    detail: "Ela para de ser usada na identificação de produtos por foto imediatamente.",
  });
  if (response !== 1) return { apiKeys: publicApiKeys() };

  core.removeApiKey(baseDir, varName);
  return { apiKeys: publicApiKeys() };
});

// -- Avatares (um vídeo por conta) -----------------------------------------------

const AVATAR_DIR = () => path.join(baseDir, "avatar");

/** Escolhe UM vídeo e o define como avatar da conta -- substitui o anterior, se houver (1 por conta). */
ipcMain.handle("avatar:setVideo", async (_event, accountId) => {
  const conta = accountsStore.findAccountById(accountsStore.loadAccounts(baseDir), accountId);
  if (!conta) throw new Error("Conta não encontrada.");
  if (!conta.folder) throw new Error(`A ${conta.label} não tem pasta definida -- edite a conta primeiro.`);

  const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
    title: `Escolher vídeo de avatar para ${conta.label}`,
    properties: ["openFile"],
    filters: [
      { name: "Vídeos", extensions: ["mp4", "mov", "avi", "mkv", "webm"] },
      { name: "Todos os arquivos", extensions: ["*"] },
    ],
  });
  if (canceled || filePaths.length === 0) return { changed: false, accounts: publicAccounts() };

  core.setAvatarVideo(AVATAR_DIR(), conta.folder, filePaths[0]);
  return { changed: true, accounts: publicAccounts() };
});

ipcMain.handle("avatar:removeVideo", async (_event, accountId) => {
  const conta = accountsStore.findAccountById(accountsStore.loadAccounts(baseDir), accountId);
  if (!conta) throw new Error("Conta não encontrada.");

  const { response } = await dialog.showMessageBox(mainWindow, {
    type: "question",
    buttons: ["Cancelar", "Remover"],
    defaultId: 1,
    cancelId: 0,
    title: "Remover avatar",
    message: `Remover o vídeo de avatar da ${conta.label}?`,
    detail: "O arquivo é apagado -- essa conta não vai mais gerar vídeos até você adicionar outro.",
  });
  if (response !== 1) return { changed: false, accounts: publicAccounts() };

  core.removeAvatarVideo(AVATAR_DIR(), conta.folder);
  return { changed: true, accounts: publicAccounts() };
});

// -- Produtos -------------------------------------------------------------------

const PRODUCT_DIR = () => path.join(baseDir, "background");

/** { pasta_da_conta: [nomes de arquivo] } -- espelha background/<pasta>/ no disco. */
function groupedPending() {
  const groups = core.getImagesByAvatarFolder(PRODUCT_DIR());
  const result = {};
  for (const [group, filePaths] of Object.entries(groups)) {
    if (group === "*") continue; // imagem solta sem conta -- não é mais um destino válido
    result[group] = core.basenames(filePaths);
  }
  return result;
}

function refreshPending() {
  return { standardProducts: groupedPending() };
}

ipcMain.handle("products:add", async (_event, folder) => {
  if (!folder) throw new Error("Escolha a conta antes de adicionar produtos.");

  const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
    title: "Escolher imagem(ns) de produto",
    properties: ["openFile", "multiSelections"],
    filters: [
      { name: "Imagens", extensions: ["jpg", "jpeg", "png", "webp", "bmp", "tiff", "gif"] },
      { name: "Todos os arquivos", extensions: ["*"] },
    ],
  });
  if (canceled || filePaths.length === 0) return { added: 0, ...refreshPending() };

  core.copyFilesInto(path.join(PRODUCT_DIR(), folder), filePaths);
  return { added: filePaths.length, ...refreshPending() };
});

ipcMain.handle("products:remove", async (_event, folder, fileNames) => {
  if (!fileNames || fileNames.length === 0) return { moved: 0, ...refreshPending() };

  const { response } = await dialog.showMessageBox(mainWindow, {
    type: "question",
    buttons: ["Cancelar", "Remover"],
    defaultId: 1,
    cancelId: 0,
    title: "Remover produto(s)",
    message: `Remover ${fileNames.length} produto(s) da fila de geração?`,
    detail: `${fileNames.join("\n")}\n\nAs imagens vão para 'background/_descartados/' -- não são apagadas de vez.`,
  });
  if (response !== 1) return { moved: 0, ...refreshPending() };

  const groupDir = path.join(PRODUCT_DIR(), folder);
  const discardDir = path.join(PRODUCT_DIR(), "_descartados");
  const moved = core.moveToDiscarded(groupDir, discardDir, fileNames);
  return { moved, ...refreshPending() };
});

// -- Pasta de saída ---------------------------------------------------------------

ipcMain.handle("output:choose", async () => {
  const { canceled, filePaths } = await dialog.showOpenDialog(mainWindow, {
    title: "Escolher pasta de saída dos vídeos",
    properties: ["openDirectory"],
  });
  if (canceled || filePaths.length === 0) return { outputFolder: core.getOutputFolder(baseDir) };

  core.setOutputFolder(baseDir, filePaths[0]);
  return { outputFolder: filePaths[0] };
});

// -- Pipeline (dry-run / gerar) -----------------------------------------------------

ipcMain.handle("pipeline:run", async (event, dryRun, autoPost) => {
  const sender = event.sender;
  const code = await pipeline.runPipeline(
    baseDir,
    dryRun,
    (line) => sender.send("pipeline:log", line),
    (prog) => sender.send("pipeline:progress", prog),
    autoPost
  );
  return { code, ...refreshPending() };
});

// -- Histórico ----------------------------------------------------------------------

ipcMain.handle("history:get", async () => pipeline.getHistory(baseDir));

// -- Contas do TikTok ----------------------------------------------------------------

/**
 * Versão das contas que pode ir pro renderer -- nunca guardamos senha, então
 * não há nada a esconder aqui. Já vem com o status do avatar (um vídeo por
 * conta -- ver core.listAvatarsByFolder()) pra a aba "Contas e avatares" não
 * precisar de outra chamada.
 */
function publicAccounts() {
  const avatares = core.listAvatarsByFolder(AVATAR_DIR());
  return accountsStore.loadAccounts(baseDir).map((acc) => ({
    id: acc.id,
    label: acc.label,
    email: acc.email || "",
    folder: acc.folder || "",
    loggedIn: !!acc.loggedIn,
    lastLoginAt: acc.lastLoginAt || null,
    profileDir: accountsStore.profileDirFor(acc),
    avatarFile: avatares[acc.folder] || null,
  }));
}

function mutateAccounts(fn) {
  const lista = accountsStore.loadAccounts(baseDir);
  const resultado = fn(lista);
  accountsStore.saveAccounts(baseDir, lista);
  return resultado;
}

ipcMain.handle("accounts:list", () => ({ accounts: publicAccounts() }));

/** Cria ou atualiza uma conta. `id` vazio = conta nova (ganha o próximo "contaN"). */
ipcMain.handle("accounts:save", (_event, dados) => {
  const label = (dados.label || "").trim();
  const email = (dados.email || "").trim();
  const folder = accountsStore.sanitizeFolder(dados.folder);

  if (!email) throw new Error("Informe o e-mail (ou nome de usuário) da conta.");
  if (!folder) throw new Error("Escolha a pasta de vídeos dessa conta (ex: avatar2).");

  mutateAccounts((lista) => {
    const conflito = lista.find(
      (a) => a.id !== dados.id && accountsStore.sanitizeFolder(a.folder).toLowerCase() === folder.toLowerCase()
    );
    if (conflito) {
      throw new Error(`A pasta "${folder}" já está vinculada à ${conflito.label}. Use uma pasta por conta.`);
    }

    const existente = dados.id ? accountsStore.findAccountById(lista, dados.id) : null;
    if (existente) {
      existente.label = label || existente.label;
      existente.email = email;
      existente.folder = folder;
      return existente;
    }

    const id = accountsStore.nextAccountId(lista);
    const nova = {
      id,
      label: label || `Conta ${id.replace("conta", "")}`,
      email,
      folder,
      profileDirName: accountsStore.suggestProfileDirName(id, folder),
      loggedIn: false,
      lastLoginAt: null,
    };
    lista.push(nova);
    return nova;
  });

  return { accounts: publicAccounts() };
});

ipcMain.handle("accounts:remove", async (_event, id) => {
  const lista = accountsStore.loadAccounts(baseDir);
  const conta = accountsStore.findAccountById(lista, id);
  if (!conta) return { accounts: publicAccounts() };

  const { response } = await dialog.showMessageBox(mainWindow, {
    type: "question",
    buttons: ["Cancelar", "Remover"],
    defaultId: 1,
    cancelId: 0,
    title: "Remover conta",
    message: `Remover a ${conta.label} do app?`,
    detail:
      `A sessão salva do Chrome (${accountsStore.profileDirFor(conta)}) NÃO é apagada -- ` +
      `use "Sair da conta" antes se quiser limpar o login também.`,
  });
  if (response !== 1) return { accounts: publicAccounts() };

  mutateAccounts((atual) => {
    const idx = atual.findIndex((a) => a.id === id);
    if (idx >= 0) atual.splice(idx, 1);
  });
  return { accounts: publicAccounts() };
});

/** Apaga o perfil do Chrome da conta -- derruba a sessão, exige login de novo. */
ipcMain.handle("accounts:logout", async (_event, id) => {
  const conta = accountsStore.findAccountById(accountsStore.loadAccounts(baseDir), id);
  if (!conta) throw new Error("Conta não encontrada.");
  const profileDir = accountsStore.profileDirFor(conta);

  const { response } = await dialog.showMessageBox(mainWindow, {
    type: "warning",
    buttons: ["Cancelar", "Sair da conta"],
    defaultId: 0,
    cancelId: 0,
    title: "Sair da conta",
    message: `Apagar a sessão salva da ${conta.label}?`,
    detail: `A pasta de perfil do Chrome será removida:\n${profileDir}\n\nVocê precisará logar de novo pelo app.`,
  });
  if (response !== 1) return { accounts: publicAccounts() };

  fs.rmSync(profileDir, { recursive: true, force: true });
  mutateAccounts((lista) => {
    const alvo = accountsStore.findAccountById(lista, id);
    if (alvo) {
      alvo.loggedIn = false;
      alvo.lastLoginAt = null;
    }
  });
  return { accounts: publicAccounts() };
});

function loginCliPath() {
  return path.join(baseDir, "electron", "tiktok_login_cli.js");
}

/**
 * Node.js a usar pra rodar tiktok_login_cli.js/tiktok_uploader.js: o
 * bundled em baseDir/node/node.exe (instalador autossuficiente -- ver
 * installer-assets/node/), ou o "node" do PATH do sistema (build "dir" de
 * desenvolvimento, onde o Node já está instalado por quem desenvolve).
 *
 * NÃO usa o Node embutido do próprio Electron (via ELECTRON_RUN_AS_NODE)
 * de propósito: o Electron 33 embute Node 20.18, que não consegue fazer
 * require() do build ESM-only do puppeteer-core sem flag experimental --
 * e mesmo com a flag, esbarra num bug de estouro de pilha na cadeia
 * puppeteer-extra -> puppeteer-core nessa combinação específica de
 * versões. Um Node de verdade (qualquer versão atual) não tem esse
 * problema -- daí embutir um binário real em vez de reusar o do Electron.
 */
function resolveNodeExe() {
  const bundled = path.join(baseDir, "node", "node.exe");
  return fs.existsSync(bundled) ? bundled : "node";
}

/** Roda um script .js como se fosse `node script.js args...` (ver resolveNodeExe()). */
function spawnNode(args, opts = {}) {
  return spawn(resolveNodeExe(), args, opts);
}

/** Faz o parse incremental do protocolo de linhas do tiktok_login_cli.js (ver esse arquivo). */
function makeLoginLineReader({ onLog, onResult }) {
  let buffer = "";
  return (chunk) => {
    buffer += chunk.toString("utf-8");
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop();
    for (const line of lines) {
      if (line.startsWith("__LOG__:")) onLog(line.slice("__LOG__:".length));
      else if (line.startsWith("__RESULT__:")) {
        try {
          onResult(JSON.parse(line.slice("__RESULT__:".length)));
        } catch {
          /* linha corrompida -- ignora, o processo ainda pode terminar OK sem resultado estruturado */
        }
      }
    }
  };
}

// Só um login por vez -- duas janelas de Chrome abertas ao mesmo tempo
// deixaria a interface ambígua sobre qual conta está logando.
let loginChild = null;
let loginCancelRequested = false;

function sendLoginEvent(sender, canal, payload) {
  if (!sender.isDestroyed()) sender.send(canal, payload);
}

/**
 * Abre o Chrome no perfil da conta e espera o usuário logar na mão -- ver
 * tiktok_login_cli.js/tiktok_login.js. A janela some sozinha assim que o
 * cookie de sessão aparece; o app só fica de olho, sem tentar preencher nada.
 */
ipcMain.handle("accounts:login", (event, id) => {
  if (loginChild) throw new Error("Já tem um login em andamento -- termine ou cancele o outro primeiro.");

  const conta = accountsStore.findAccountById(accountsStore.loadAccounts(baseDir), id);
  if (!conta) throw new Error("Conta não encontrada.");

  const sender = event.sender;
  loginCancelRequested = false;

  return new Promise((resolve, reject) => {
    const child = spawnNode([loginCliPath(), "login", id], { cwd: baseDir, windowsHide: true });
    loginChild = child;

    let resultado = null;
    let stderrBuf = "";
    const onData = makeLoginLineReader({
      onLog: (mensagem) => sendLoginEvent(sender, "accounts:login:log", { id, message: mensagem }),
      onResult: (dados) => {
        resultado = dados;
      },
    });

    child.stdout.on("data", onData);
    child.stderr.on("data", (d) => {
      stderrBuf += d.toString("utf-8");
    });

    child.on("error", (err) => {
      loginChild = null;
      reject(new Error(`Não consegui iniciar o processo de login ("${resolveNodeExe()}"): ${err.message}`));
    });

    child.on("close", (code) => {
      loginChild = null;

      if (loginCancelRequested) {
        reject(new Error("Login cancelado."));
        return;
      }
      if (code !== 0 || !resultado) {
        reject(new Error(stderrBuf.trim() || `O processo de login terminou com código ${code}.`));
        return;
      }

      mutateAccounts((lista) => {
        const alvo = accountsStore.findAccountById(lista, id);
        if (alvo) {
          alvo.loggedIn = true;
          alvo.lastLoginAt = new Date().toISOString();
        }
      });
      resolve({ ok: true, alreadyLoggedIn: !!resultado.alreadyLoggedIn, accounts: publicAccounts() });
    });
  });
});

ipcMain.handle("accounts:login:cancel", () => {
  if (!loginChild) return { ok: true };
  loginCancelRequested = true;
  loginChild.kill(); // SIGTERM -- tiktok_login_cli.js fecha o Chrome antes de sair
  return { ok: true };
});

/** Abre o perfil da conta, confere se a sessão do TikTok ainda vale e fecha. */
ipcMain.handle("accounts:verify", (_event, id) => {
  const conta = accountsStore.findAccountById(accountsStore.loadAccounts(baseDir), id);
  if (!conta) throw new Error("Conta não encontrada.");

  return new Promise((resolve, reject) => {
    const child = spawnNode([loginCliPath(), "verify", id], { cwd: baseDir, windowsHide: true });

    let resultado = null;
    let stderrBuf = "";
    child.stdout.on(
      "data",
      makeLoginLineReader({
        onLog: () => {},
        onResult: (dados) => {
          resultado = dados;
        },
      })
    );
    child.stderr.on("data", (d) => {
      stderrBuf += d.toString("utf-8");
    });

    child.on("error", (err) => {
      reject(new Error(`Não consegui verificar a sessão ("${resolveNodeExe()}"): ${err.message}`));
    });

    child.on("close", (code) => {
      if (code !== 0 || !resultado) {
        reject(new Error(stderrBuf.trim() || `O processo de verificação terminou com código ${code}.`));
        return;
      }

      const logado = !!resultado.loggedIn;
      mutateAccounts((lista) => {
        const alvo = accountsStore.findAccountById(lista, id);
        if (alvo) {
          alvo.loggedIn = logado;
          if (!logado) alvo.lastLoginAt = null;
        }
      });
      resolve({ loggedIn: logado, accounts: publicAccounts() });
    });
  });
});

/**
 * Varre os perfis do Chrome no disco e importa como conta qualquer um que
 * já esteja logado (ou existente) mas não cadastrado -- login feito à mão
 * antes do cadastro de contas existir. Rodado sozinho a cada abertura do
 * app (ver renderer.js -> init()); sem perfis órfãos, o script nem chega a
 * abrir o Chrome, então é rápido depois da primeira vez.
 */
ipcMain.handle("accounts:discover", (event) => {
  const sender = event.sender;

  return new Promise((resolve, reject) => {
    const child = spawnNode([loginCliPath(), "discover"], { cwd: baseDir, windowsHide: true });

    let resultado = null;
    let stderrBuf = "";
    child.stdout.on(
      "data",
      makeLoginLineReader({
        onLog: (mensagem) => sendLoginEvent(sender, "accounts:login:log", { id: null, message: mensagem }),
        onResult: (dados) => {
          resultado = dados;
        },
      })
    );
    child.stderr.on("data", (d) => {
      stderrBuf += d.toString("utf-8");
    });

    child.on("error", (err) => {
      reject(new Error(`Não consegui detectar contas já logadas ("${resolveNodeExe()}"): ${err.message}`));
    });

    child.on("close", (code) => {
      if (code !== 0 || !resultado) {
        reject(new Error(stderrBuf.trim() || `A detecção de contas terminou com código ${code}.`));
        return;
      }
      resolve({ imported: resultado.imported || [], accounts: publicAccounts() });
    });
  });
});

/** Abre a pasta de saída da conta no Explorer -- confere onde os vídeos caem. */
ipcMain.handle("accounts:openFolder", async (_event, id) => {
  const conta = accountsStore.findAccountById(accountsStore.loadAccounts(baseDir), id);
  if (!conta) throw new Error("Conta não encontrada.");

  const outputRoot = core.getOutputFolder(baseDir);
  const raiz = path.isAbsolute(outputRoot) ? outputRoot : path.join(baseDir, outputRoot);

  // Os vídeos do dia ficam em output/<DD-MM>/<pasta da conta>; se a pasta de
  // hoje ainda não existe (nada foi gerado), abre a raiz de saída.
  const agora = new Date();
  const hoje = `${String(agora.getDate()).padStart(2, "0")}-${String(agora.getMonth() + 1).padStart(2, "0")}`;
  const doDia = path.join(raiz, hoje, conta.folder || "");
  const dir = fs.existsSync(doDia) ? doDia : raiz;

  fs.mkdirSync(dir, { recursive: true });
  await shell.openPath(dir);
  return { ok: true, path: dir };
});
