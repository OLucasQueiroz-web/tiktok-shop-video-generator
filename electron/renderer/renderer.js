"use strict";

const state = {
  apiKeys: [], // [{ varName, masked }]
  standardProducts: {}, // { [contaFolder]: [nomes de arquivo] }
  outputFolder: "output",
  accounts: [], // [{ id, label, email, folder, loggedIn, lastLoginAt, profileDir, avatarFile }]
  busy: false,
  loginBusyId: null, // id da conta com login em andamento (null = nenhum)
};

const el = {
  tabs: document.getElementById("tabs"),
  apikeysList: document.getElementById("apikeys-list"),
  apikeyInput: document.getElementById("apikey-input"),
  apikeyAddBtn: document.getElementById("apikey-add-btn"),
  productsByAccount: document.getElementById("products-by-account"),
  outputLabel: document.getElementById("output-label"),
  chooseOutputBtn: document.getElementById("choose-output-btn"),
  dryRunBtn: document.getElementById("dry-run-btn"),
  generateBtn: document.getElementById("generate-btn"),
  autoPostCheckbox: document.getElementById("auto-post-checkbox"),
  historyBtn: document.getElementById("history-btn"),
  progressFill: document.getElementById("progress-fill"),
  progressLabel: document.getElementById("progress-label"),
  log: document.getElementById("log"),
  historyDialog: document.getElementById("history-dialog"),
  historyTableBody: document.querySelector("#history-table tbody"),
  historySummary: document.getElementById("history-summary"),
  historyCloseBtn: document.getElementById("history-close-btn"),
  accountsList: document.getElementById("accounts-list"),
  addAccountBtn: document.getElementById("add-account-btn"),
  accountDialog: document.getElementById("account-dialog"),
  accountDialogTitle: document.getElementById("account-dialog-title"),
  accountLabel: document.getElementById("account-label"),
  accountEmail: document.getElementById("account-email"),
  accountFolder: document.getElementById("account-folder"),
  accountFolderOptions: document.getElementById("account-folder-options"),
  accountSaveBtn: document.getElementById("account-save-btn"),
  accountCancelBtn: document.getElementById("account-cancel-btn"),
};

// -- Abas -----------------------------------------------------------------------

el.tabs.addEventListener("click", (e) => {
  const btn = e.target.closest(".tab-btn");
  if (!btn) return;
  for (const b of el.tabs.querySelectorAll(".tab-btn")) b.classList.toggle("active", b === btn);
  const alvo = btn.dataset.tab;
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.classList.toggle("active", panel.dataset.panel === alvo);
  }
});

// -- Chaves da API Groq (quantas o usuário quiser) -------------------------------

function renderApiKeys() {
  el.apikeysList.innerHTML = "";

  if (state.apiKeys.length === 0) {
    const vazio = document.createElement("p");
    vazio.className = "entity-empty";
    vazio.textContent = "Nenhuma chave cadastrada -- cole uma abaixo pra identificação de produto funcionar.";
    el.apikeysList.appendChild(vazio);
    return;
  }

  state.apiKeys.forEach((k, i) => {
    const row = document.createElement("div");
    row.className = "entity-row";

    const nome = document.createElement("span");
    nome.className = "account-name";
    nome.style.minWidth = "70px";
    nome.textContent = `Chave ${i + 1}`;
    row.appendChild(nome);

    const valor = document.createElement("span");
    valor.className = "apikey-value";
    valor.textContent = k.masked;
    row.appendChild(valor);

    const removerBtn = document.createElement("button");
    removerBtn.textContent = "Remover";
    removerBtn.addEventListener("click", () => removeApiKey(k.varName));
    row.appendChild(removerBtn);

    el.apikeysList.appendChild(row);
  });
}

el.apikeyAddBtn.addEventListener("click", async () => {
  const key = el.apikeyInput.value.trim();
  if (!key) {
    alert("Cole a chave da API Groq antes de adicionar.");
    return;
  }
  try {
    const result = await window.api.addApiKey(key);
    state.apiKeys = result.apiKeys;
    el.apikeyInput.value = "";
    renderApiKeys();
    appendLog(`Chave da API Groq adicionada (${state.apiKeys.length} no total).`);
  } catch (err) {
    alert(err.message);
  }
});

async function removeApiKey(varName) {
  try {
    const result = await window.api.removeApiKey(varName);
    state.apiKeys = result.apiKeys;
    renderApiKeys();
  } catch (err) {
    alert(err.message);
  }
}

// -- Produtos, um card por conta ---------------------------------------------------

/** Um card por conta cadastrada: nome + botão de adicionar + lista de pendentes daquela pasta. */
function renderProducts() {
  el.productsByAccount.innerHTML = "";

  if (state.accounts.length === 0) {
    const vazio = document.createElement("p");
    vazio.className = "hint";
    vazio.textContent = 'Cadastre uma conta na aba "Contas e avatares" primeiro -- produtos são adicionados por conta.';
    el.productsByAccount.appendChild(vazio);
    return;
  }

  for (const acc of state.accounts) {
    const names = state.standardProducts[acc.folder] || [];

    const card = document.createElement("section");
    card.className = "card";

    const h2 = document.createElement("h2");
    h2.textContent = acc.label;
    card.appendChild(h2);

    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = `pasta: ${acc.folder} · ${names.length} produto(s) pendente(s)`;
    card.appendChild(hint);

    const row = document.createElement("div");
    row.className = "row";
    const addBtn = document.createElement("button");
    addBtn.className = "primary";
    addBtn.textContent = "+ Adicionar produto";
    addBtn.addEventListener("click", () => addProducts(acc.folder));
    row.appendChild(addBtn);
    card.appendChild(row);

    if (names.length > 0) {
      const select = document.createElement("select");
      select.multiple = true;
      select.size = Math.min(6, Math.max(2, names.length));
      for (const name of names) {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        select.appendChild(opt);
      }
      card.appendChild(select);

      const removeRow = document.createElement("div");
      removeRow.className = "row";
      const removeBtn = document.createElement("button");
      removeBtn.textContent = "Remover selecionado(s)";
      removeBtn.addEventListener("click", () => removeProducts(acc.folder, select));
      removeRow.appendChild(removeBtn);
      card.appendChild(removeRow);
    }

    el.productsByAccount.appendChild(card);
  }
}

async function addProducts(folder) {
  const result = await window.api.addProducts(folder);
  if (result.added > 0) {
    appendLog(`Adicionada(s) ${result.added} imagem(ns) em 'background/${folder}'.`);
  }
  state.standardProducts = result.standardProducts;
  renderProducts();
}

async function removeProducts(folder, selectEl) {
  const names = Array.from(selectEl.selectedOptions).map((o) => o.value);
  if (names.length === 0) {
    alert("Selecione ao menos um produto na lista antes de remover.");
    return;
  }
  const result = await window.api.removeProducts(folder, names);
  if (result.moved > 0) {
    appendLog(`Removido(s) ${result.moved} produto(s) de 'background/${folder}' (movidos para _descartados/).`);
  }
  state.standardProducts = result.standardProducts;
  renderProducts();
}

// -- Pasta de saída -----------------------------------------------------------------

function renderOutputFolder() {
  el.outputLabel.textContent = `Pasta atual: ${state.outputFolder}`;
}

el.chooseOutputBtn.addEventListener("click", async () => {
  const result = await window.api.chooseOutputFolder();
  if (result.outputFolder !== state.outputFolder) {
    appendLog(`Pasta de saída alterada para: ${result.outputFolder}`);
  }
  state.outputFolder = result.outputFolder;
  renderOutputFolder();
});

// -- Log --------------------------------------------------------------------------

function appendLog(message) {
  const time = new Date().toLocaleTimeString("pt-BR", { hour12: false });
  el.log.textContent += `${time} [INFO   ] ${message}\n`;
  el.log.scrollTop = el.log.scrollHeight;
}

// -- Progresso ----------------------------------------------------------------------

let progressTotal = 0;
let progressDone = 0;

function setIndeterminate(on) {
  el.progressFill.classList.toggle("indeterminate", on);
  if (on) el.progressFill.style.width = "";
}

function setProgress(done, total) {
  progressDone = done;
  progressTotal = total;
  el.progressFill.classList.remove("indeterminate");
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  el.progressFill.style.width = `${pct}%`;
  el.progressLabel.textContent = total > 0 ? `${done}/${total} vídeo(s)` : "";
}

function resetProgress() {
  progressTotal = 0;
  progressDone = 0;
  el.progressFill.style.width = "0%";
  el.progressFill.classList.remove("indeterminate");
  el.progressLabel.textContent = "";
}

function handleProgressEvent(raw) {
  const [tag, ...rest] = raw.split(":");
  switch (tag) {
    case "INIT":
    case "VIRAL_INIT": {
      const total = parseInt(rest[0], 10) || 0;
      setProgress(0, total);
      break;
    }
    case "VIDEO_START": {
      // rest = [index, total, name] -- índice já reflete o vídeo em andamento.
      const total = parseInt(rest[1], 10) || progressTotal;
      const index = parseInt(rest[0], 10) || 1;
      setProgress(index - 1, total);
      break;
    }
    case "VIDEO_DONE":
    case "VIDEO_FAIL": {
      const index = parseInt(rest[0], 10) || progressDone + 1;
      const total = parseInt(rest[1], 10) || progressTotal;
      setProgress(index, total);
      break;
    }
    case "ALL_DONE":
      setProgress(progressTotal, progressTotal || 1);
      break;
    default:
      break;
  }
}

// -- Ações (dry-run / gerar) -----------------------------------------------------------

function setBusy(busy) {
  state.busy = busy;
  el.dryRunBtn.disabled = busy;
  el.generateBtn.disabled = busy;
}

el.dryRunBtn.addEventListener("click", () => startPipeline(true));
el.generateBtn.addEventListener("click", () => startPipeline(false));

async function startPipeline(dryRun) {
  if (state.busy) {
    alert("Já tem uma operação em andamento.");
    return;
  }
  const autoPost = !dryRun && el.autoPostCheckbox.checked;
  setBusy(true);
  resetProgress();
  setIndeterminate(true);
  appendLog(
    dryRun
      ? "Iniciando dry-run..."
      : autoPost
      ? "Iniciando geração de vídeos -- posta tudo no TikTok automaticamente depois que todos forem gerados..."
      : "Iniciando geração de vídeos..."
  );

  try {
    const result = await window.api.runPipeline(dryRun, autoPost);
    state.standardProducts = result.standardProducts;
    renderProducts();
    if (result.code !== 0) {
      appendLog(`Processo terminou com código ${result.code}.`);
    }
  } catch (err) {
    appendLog(`Falhou: ${err.message}`);
    alert(err.message);
  } finally {
    setIndeterminate(false);
    setBusy(false);
  }
}

window.api.onPipelineLog((line) => {
  el.log.textContent += `${line}\n`;
  el.log.scrollTop = el.log.scrollHeight;
});

window.api.onPipelineProgress((raw) => {
  setIndeterminate(false);
  handleProgressEvent(raw);
});

// -- Histórico --------------------------------------------------------------------------

el.historyBtn.addEventListener("click", async () => {
  try {
    const { rows, counts } = await window.api.getHistory();
    renderHistory(rows, counts);
    el.historyDialog.showModal();
  } catch (err) {
    alert(`Erro ao ler histórico: ${err.message}`);
  }
});

el.historyCloseBtn.addEventListener("click", () => el.historyDialog.close());

function renderHistory(rows, counts) {
  el.historyTableBody.innerHTML = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    const statusClass = row.status === "success" ? "status-success" : "status-failed";
    tr.innerHTML = `
      <td>${row.id}</td>
      <td>${row.day_label}</td>
      <td>${row.video_type}</td>
      <td>${row.profile}</td>
      <td>${row.avatar_name}</td>
      <td>${row.animation_style || "-"}</td>
      <td class="${statusClass}">${row.status}</td>
      <td>${row.product_name || "-"}</td>
    `;
    el.historyTableBody.appendChild(tr);
  }
  const byType = Object.entries(counts.by_type || {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "-";
  const byStatus = Object.entries(counts.by_status || {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "-";
  el.historySummary.textContent = `Total: ${counts.total}  |  por tipo: ${byType}  |  por status: ${byStatus}`;
}

// -- Contas do TikTok ---------------------------------------------------------------------

let accountDialogId = null; // null = conta nova; senão, edição

function formatLastLogin(iso) {
  if (!iso) return "nunca";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "nunca" : d.toLocaleString("pt-BR");
}

/** Pastas já usadas por outras contas -- só pra referência ao digitar uma nova. */
function renderAccountFolderOptions() {
  const sugestoes = new Set();
  for (const acc of state.accounts) if (acc.folder) sugestoes.add(acc.folder);

  el.accountFolderOptions.innerHTML = "";
  for (const nome of [...sugestoes].sort((a, b) => a.localeCompare(b))) {
    const opt = document.createElement("option");
    opt.value = nome;
    el.accountFolderOptions.appendChild(opt);
  }
}

function renderAccounts() {
  el.accountsList.innerHTML = "";
  renderAccountFolderOptions();

  if (state.accounts.length === 0) {
    const vazio = document.createElement("p");
    vazio.className = "entity-empty";
    vazio.textContent =
      'Nenhuma conta cadastrada. Clique em "+ Adicionar conta" pra criar a primeira (Conta 1), ' +
      'vincular a pasta de vídeos dela e depois clicar em "Realizar login".';
    el.accountsList.appendChild(vazio);
    renderProducts();
    return;
  }

  for (const acc of state.accounts) {
    const row = document.createElement("div");
    row.className = "entity-row";

    const info = document.createElement("div");
    info.className = "account-info";
    const nome = document.createElement("span");
    nome.className = "account-name";
    nome.textContent = acc.label;
    const meta = document.createElement("span");
    meta.className = "account-meta";
    meta.textContent =
      `${acc.email || "(sem e-mail)"} · pasta: ${acc.folder || "(não definida)"} · ` +
      `avatar: ${acc.avatarFile || "não definido"} · ` +
      `último login: ${formatLastLogin(acc.lastLoginAt)}`;
    info.appendChild(nome);
    info.appendChild(meta);
    row.appendChild(info);

    const badge = document.createElement("span");
    badge.className = `entity-badge ${acc.loggedIn ? "on" : "off"}`;
    badge.textContent = acc.loggedIn ? "sessão ativa" : "sem sessão";
    row.appendChild(badge);

    const actions = document.createElement("div");
    actions.className = "entity-actions";

    const ocupado = state.loginBusyId !== null;
    const logandoEstaConta = state.loginBusyId === acc.id;
    const addBtn = (texto, onClick, { primary = false, disabled = false } = {}) => {
      const b = document.createElement("button");
      b.textContent = texto;
      if (primary) b.className = "primary";
      b.disabled = disabled || (ocupado && !logandoEstaConta);
      b.addEventListener("click", onClick);
      actions.appendChild(b);
      return b;
    };

    if (logandoEstaConta) {
      const aguardando = document.createElement("span");
      aguardando.className = "account-meta";
      aguardando.textContent = "Aguardando você logar na janela do Chrome...";
      actions.appendChild(aguardando);
      addBtn("Cancelar login", () => cancelLogin());
    } else {
      if (acc.loggedIn) {
        const feito = document.createElement("span");
        feito.className = "entity-badge on";
        feito.textContent = "Login realizado";
        actions.appendChild(feito);
      } else {
        addBtn("Realizar login", () => loginAccount(acc), { primary: true });
      }
      addBtn("Verificar sessão", () => verifyAccount(acc));
      addBtn(acc.avatarFile ? "Trocar avatar" : "Adicionar avatar", () => setAvatarVideo(acc));
      if (acc.avatarFile) addBtn("Remover avatar", () => removeAvatarVideo(acc));
      addBtn("Editar", () => openAccountDialog(acc));
      addBtn("Abrir pasta", () => openAccountFolder(acc));
      addBtn("Sair da conta", () => logoutAccount(acc));
      addBtn("Remover", () => removeAccount(acc));
    }

    row.appendChild(actions);
    el.accountsList.appendChild(row);
  }

  renderProducts(); // os cards de produto (aba Produtos) espelham a lista de contas
}

function openAccountDialog(account) {
  accountDialogId = account ? account.id : null;

  const proximoNumero = state.accounts.length + 1;
  el.accountLabel.value = account ? account.label : `Conta ${proximoNumero}`;
  el.accountEmail.value = account ? account.email : "";
  el.accountFolder.value = account ? account.folder : "";
  el.accountDialogTitle.textContent = account ? `Editar ${account.label}` : "Adicionar conta";
  el.accountSaveBtn.textContent = "Salvar";

  renderAccountFolderOptions();
  el.accountDialog.showModal();
  el.accountLabel.focus();
}

el.addAccountBtn.addEventListener("click", () => openAccountDialog(null));
el.accountCancelBtn.addEventListener("click", () => el.accountDialog.close());

el.accountSaveBtn.addEventListener("click", async () => {
  const dados = {
    id: accountDialogId,
    label: el.accountLabel.value,
    email: el.accountEmail.value,
    folder: el.accountFolder.value,
  };
  const eraContaNova = !accountDialogId;

  try {
    const result = await window.api.saveAccount(dados);
    state.accounts = result.accounts;
    el.accountDialog.close();
    renderAccounts();
    appendLog(`Conta salva: ${dados.label} (pasta ${dados.folder}).`);

    // Conta recém-criada: já oferece o login, já que é o próximo passo natural.
    if (eraContaNova) {
      const nova = state.accounts.find((a) => a.email === dados.email.trim() && a.folder === dados.folder.trim());
      if (nova) await loginAccount(nova);
    }
  } catch (err) {
    alert(err.message);
  }
});

/** Abre o Chrome no perfil da conta -- o usuário loga na mão, o app detecta sozinho quando terminar. */
async function loginAccount(account) {
  if (state.loginBusyId) {
    alert("Já tem um login em andamento -- espere terminar ou cancele.");
    return;
  }

  state.loginBusyId = account.id;
  renderAccounts();
  appendLog(`Abrindo o Chrome pra ${account.label} -- faça o login na janela que abrir.`);

  try {
    const result = await window.api.loginAccount(account.id);
    state.accounts = result.accounts;
    appendLog(
      result.alreadyLoggedIn
        ? `${account.label} já estava logada nesse perfil do Chrome.`
        : `${account.label} logada com sucesso -- a sessão ficou salva pro uploader usar.`
    );
  } catch (err) {
    appendLog(`Login da ${account.label} não concluído: ${err.message}`);
  } finally {
    state.loginBusyId = null;
    const atual = await window.api.listAccounts();
    state.accounts = atual.accounts;
    renderAccounts();
  }
}

async function cancelLogin() {
  await window.api.cancelLogin();
  appendLog("Login cancelado.");
}

async function verifyAccount(account) {
  appendLog(`Verificando a sessão da ${account.label}...`);
  try {
    const result = await window.api.verifyAccount(account.id);
    state.accounts = result.accounts;
    renderAccounts();
    appendLog(
      result.loggedIn
        ? `${account.label}: sessão ativa, pode postar.`
        : `${account.label}: sem sessão -- clique em "Entrar" pra logar de novo.`
    );
  } catch (err) {
    alert(err.message);
  }
}

async function logoutAccount(account) {
  try {
    const result = await window.api.logoutAccount(account.id);
    state.accounts = result.accounts;
    renderAccounts();
  } catch (err) {
    alert(err.message);
  }
}

async function removeAccount(account) {
  try {
    const result = await window.api.removeAccount(account.id);
    state.accounts = result.accounts;
    renderAccounts();
  } catch (err) {
    alert(err.message);
  }
}

async function openAccountFolder(account) {
  try {
    const result = await window.api.openAccountFolder(account.id);
    appendLog(`Abrindo ${result.path}`);
  } catch (err) {
    alert(err.message);
  }
}

async function setAvatarVideo(account) {
  try {
    const result = await window.api.setAvatarVideo(account.id);
    if (result.changed) appendLog(`Vídeo de avatar definido para ${account.label}.`);
    state.accounts = result.accounts;
    renderAccounts();
  } catch (err) {
    alert(err.message);
  }
}

async function removeAvatarVideo(account) {
  try {
    const result = await window.api.removeAvatarVideo(account.id);
    if (result.changed) appendLog(`Vídeo de avatar removido de ${account.label}.`);
    state.accounts = result.accounts;
    renderAccounts();
  } catch (err) {
    alert(err.message);
  }
}

window.api.onLoginLog(({ message }) => appendLog(message));

/**
 * Importa como conta qualquer perfil do Chrome já logado (ou existente) que
 * não esteja cadastrado -- sessões de antes do cadastro de contas existir.
 * Roda em segundo plano depois da tela inicial já estar de pé: sem perfis
 * órfãos (o caso normal depois da primeira vez) é praticamente instantâneo.
 */
async function discoverAccounts() {
  try {
    const result = await window.api.discoverAccounts();
    if (result.imported.length > 0) {
      state.accounts = result.accounts;
      renderAccounts();
      appendLog(
        `Detectada(s) ${result.imported.length} conta(s) já logada(s) num perfil do Chrome existente: ` +
          result.imported.map((a) => a.label).join(", ") +
          "."
      );
    }
  } catch (err) {
    appendLog(`Não consegui detectar contas já logadas: ${err.message}`);
  }
}

// -- Inicialização --------------------------------------------------------------------------

async function init() {
  const initial = await window.api.init();
  Object.assign(state, initial);
  renderApiKeys();
  renderProducts();
  renderOutputFolder();
  renderAccounts();
  discoverAccounts();
}

init();
