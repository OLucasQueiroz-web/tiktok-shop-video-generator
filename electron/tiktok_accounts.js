"use strict";

/**
 * Cadastro das contas do TikTok usadas para postar.
 *
 * Cada conta ("conta1", "conta2", ...) tem:
 *   - id            identificador estável, usado no nome do perfil do Chrome
 *   - label         nome exibido na interface ("Conta 1")
 *   - email         e-mail/usuário da conta -- só identificação (o login é
 *                   feito à mão pelo usuário na janela do Chrome, ver
 *                   tiktok_login.js; a senha nunca é pedida nem guardada)
 *   - folder        pasta de saída de onde essa conta posta os vídeos --
 *                   output/<DD-MM>/<folder>/ (normalmente o nome do avatar,
 *                   ex: "avatar2"). É o que amarra vídeo -> conta.
 *   - profileDirName  pasta do perfil do Chrome dessa conta, dentro de
 *                   %LOCALAPPDATA%\TikTokShopAutomation\
 *   - loggedIn / lastLoginAt  status da última verificação/login feito pelo app
 *
 * O arquivo fica em <projeto>/tiktok-accounts.json. Se não existir, o
 * uploader cai no comportamento antigo (perfil chrome-profile-<avatar>),
 * então projetos que já rodavam antes desse cadastro continuam funcionando
 * sem migração.
 */

const fs = require("fs");
const path = require("path");

const ACCOUNTS_FILENAME = "tiktok-accounts.json";
const PROFILE_ROOT_NAME = "TikTokShopAutomation";

/** Raiz do projeto quando o chamador não informa (electron/ -> ..). */
function defaultBaseDir() {
  return path.join(__dirname, "..");
}

function accountsFilePath(baseDir = defaultBaseDir()) {
  return path.join(baseDir, ACCOUNTS_FILENAME);
}

function loadAccounts(baseDir = defaultBaseDir()) {
  const file = accountsFilePath(baseDir);
  if (!fs.existsSync(file)) return [];
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf-8"));
    const list = Array.isArray(parsed) ? parsed : parsed && parsed.accounts;
    return Array.isArray(list) ? list : [];
  } catch (err) {
    // Arquivo corrompido não pode derrubar o app inteiro -- trata como vazio
    // e deixa o rastro no log pra dar pra recuperar manualmente.
    console.error(`[tiktok_accounts] ${ACCOUNTS_FILENAME} inválido (${err.message}) -- tratando como vazio.`);
    return [];
  }
}

function saveAccounts(baseDir, accounts) {
  fs.writeFileSync(accountsFilePath(baseDir), JSON.stringify({ accounts }, null, 2), "utf-8");
  return accounts;
}

/** %LOCALAPPDATA%\TikTokShopAutomation -- fora do projeto (que fica em OneDrive). */
function profilesRoot() {
  const base = process.env.LOCALAPPDATA || path.join(process.env.USERPROFILE || ".", "AppData", "Local");
  return path.join(base, PROFILE_ROOT_NAME);
}

/** Nome do perfil usado antes do cadastro de contas existir (por avatar). */
function legacyProfileDirName(folder) {
  return `chrome-profile-${folder}`;
}

function profileDirNameFor(account) {
  return account.profileDirName || `chrome-profile-${account.id}`;
}

function profileDirFor(account) {
  return path.join(profilesRoot(), profileDirNameFor(account));
}

/**
 * Perfil sugerido para uma conta nova: se já existe um perfil legado com
 * sessão salva para essa pasta (chrome-profile-avatar2, por exemplo),
 * reaproveita -- assim quem já tinha logado manualmente não precisa logar
 * de novo só porque cadastrou a conta na interface.
 */
function suggestProfileDirName(id, folder) {
  if (folder) {
    const legacy = legacyProfileDirName(folder);
    if (fs.existsSync(path.join(profilesRoot(), legacy))) return legacy;
  }
  return `chrome-profile-${id}`;
}

/** Próximo id livre no padrão conta1, conta2, ... */
function nextAccountId(accounts) {
  let maior = 0;
  for (const acc of accounts) {
    const m = /^conta(\d+)$/.exec(acc.id || "");
    if (m) maior = Math.max(maior, parseInt(m[1], 10));
  }
  return `conta${maior + 1}`;
}

function findAccountById(accounts, id) {
  return accounts.find((a) => a.id === id) || null;
}

/** Conta responsável pela pasta de saída informada (output/<DD-MM>/<folder>). */
function findAccountByFolder(accounts, folder) {
  if (!folder) return null;
  const alvo = String(folder).trim().toLowerCase();
  return accounts.find((a) => String(a.folder || "").trim().toLowerCase() === alvo) || null;
}

/**
 * Perfil do Chrome a usar para postar os vídeos de uma pasta: o da conta
 * cadastrada para ela, ou o perfil legado por avatar se não houver cadastro.
 */
function resolveProfileDirForFolder(folder, baseDir = defaultBaseDir()) {
  const account = findAccountByFolder(loadAccounts(baseDir), folder);
  if (account) return profileDirFor(account);
  return path.join(profilesRoot(), legacyProfileDirName(folder));
}

/**
 * Perfis do Chrome que existem no disco (%LOCALAPPDATA%\...\chrome-profile-*)
 * mas não pertencem a nenhuma conta cadastrada -- normalmente sessões
 * antigas de antes do cadastro de contas existir (login feito à mão via
 * abrir-tiktok.bat, ou pelo próprio uploader na primeira execução).
 * Usado por tiktok_login_cli.js pra importar essas contas automaticamente
 * na inicialização, com a sessão já verificada.
 */
function findUnclaimedProfileDirs(baseDir = defaultBaseDir()) {
  const root = profilesRoot();
  if (!fs.existsSync(root)) return [];

  const emUso = new Set(loadAccounts(baseDir).map((a) => profileDirNameFor(a)));
  const prefixo = "chrome-profile-";

  return fs
    .readdirSync(root, { withFileTypes: true })
    .filter((d) => d.isDirectory() && d.name.startsWith(prefixo) && !emUso.has(d.name))
    .map((d) => ({
      profileDirName: d.name,
      suggestedFolder: d.name.slice(prefixo.length),
    }));
}

/** Normaliza o nome de pasta digitado pelo usuário (vira caminho no disco). */
function sanitizeFolder(name) {
  return String(name || "")
    .trim()
    .replace(/[<>:"/\|?*\x00-\x1f]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

module.exports = {
  ACCOUNTS_FILENAME,
  accountsFilePath,
  loadAccounts,
  saveAccounts,
  profilesRoot,
  profileDirFor,
  profileDirNameFor,
  legacyProfileDirName,
  suggestProfileDirName,
  nextAccountId,
  findAccountById,
  findAccountByFolder,
  findUnclaimedProfileDirs,
  resolveProfileDirForFolder,
  sanitizeFolder,
};
