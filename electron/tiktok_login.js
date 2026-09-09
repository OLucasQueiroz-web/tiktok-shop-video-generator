"use strict";

/**
 * Login manual numa conta do TikTok, dentro do perfil dedicado do Chrome
 * daquela conta (ver tiktok_accounts.js).
 *
 * Fluxo: o app abre o Chrome real na tela de login do TikTok e o usuário
 * loga na mão (usuário/e-mail, senha, código de verificação, CAPTCHA -- tudo
 * na própria janela, sem a automação tentar adivinhar nada). O app só fica
 * de olho no cookie `sessionid` daquele perfil; assim que ele aparece, o
 * login é considerado concluído e a janela fecha sozinha. Isso evita ter que
 * reconhecer os inúmeros formatos de tela que o TikTok pode mostrar
 * (código por e-mail/SMS, CAPTCHA, verificação por app, etc.) -- o usuário
 * já sabe lidar com qualquer um deles.
 *
 * Uma sessão de login por vez (LoginSession). O consumidor (main.js) recebe
 * eventos via onEvent: { type: "log", message }.
 */

const { launchChrome, hasTikTokSession } = require("./chrome_profile");
const accounts = require("./tiktok_accounts");

const LOGIN_URL = "https://www.tiktok.com/login";
const MAX_WAIT_MS = 15 * 60 * 1000; // teto generoso pro usuário logar na mão
const POLL_MS = 1500;

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

class LoginSession {
  /**
   * @param {object} opts
   * @param {object} opts.account   conta do tiktok_accounts.js
   * @param {(evento: object) => void} opts.onEvent  eventos: { type: "log", message }
   */
  constructor({ account, onEvent }) {
    this.account = account;
    this.onEvent = onEvent || (() => {});
    this.browser = null;
    this.page = null;
    this.cancelled = false;
    this._fechadoPeloUsuario = false;
  }

  log(mensagem) {
    this.onEvent({ type: "log", message: mensagem });
  }

  async cancel() {
    this.cancelled = true;
    await this.closeBrowser();
  }

  async closeBrowser() {
    if (!this.browser) return;
    const browser = this.browser;
    this.browser = null;
    this.page = null;
    await browser.close().catch(() => {});
  }

  /**
   * Abre o Chrome no perfil da conta, navega pro login e espera o usuário
   * logar na mão -- resolve assim que o cookie de sessão aparecer. Se o
   * usuário fechar a janela do Chrome sem logar, lança erro claro em vez de
   * ficar esperando pra sempre. Lança também em cancelamento ou timeout.
   */
  async start() {
    const userDataDir = accounts.profileDirFor(this.account);
    this.log(`Abrindo o Chrome no perfil da ${this.account.label}...`);
    this.browser = await launchChrome(userDataDir);
    this.browser.on("disconnected", () => {
      this._fechadoPeloUsuario = !this.cancelled;
    });

    const [page] = await this.browser.pages();
    this.page = page;

    await this.page.goto(LOGIN_URL, { waitUntil: "networkidle2", timeout: 60000 });

    if (await hasTikTokSession(this.page)) {
      this.log("Esse perfil já estava logado -- nada a fazer.");
      await this.closeBrowser();
      return { alreadyLoggedIn: true };
    }

    this.log("Faça o login normalmente na janela do Chrome que abriu. Assim que terminar, eu detecto sozinho.");

    const limite = Date.now() + MAX_WAIT_MS;
    while (Date.now() < limite) {
      if (this.cancelled) throw new Error("Login cancelado.");

      if (this._fechadoPeloUsuario) {
        throw new Error("A janela do Chrome foi fechada antes do login terminar.");
      }

      const logado = await hasTikTokSession(this.page).catch(() => false);
      if (logado) {
        this.log("Login detectado -- sessão salva nesse perfil do Chrome.");
        await this.closeBrowser();
        return { alreadyLoggedIn: false };
      }

      await sleep(POLL_MS);
    }

    throw new Error("Tempo esgotado esperando o login (15 min).");
  }
}

/**
 * Abre o perfil da conta, checa se a sessão do TikTok ainda vale e fecha.
 * Usado pelo botão "Verificar sessão" da interface.
 */
async function checkSession(account) {
  const browser = await launchChrome(accounts.profileDirFor(account));
  try {
    const [page] = await browser.pages();
    await page.goto("https://www.tiktok.com/", { waitUntil: "domcontentloaded", timeout: 60000 });
    await sleep(2500);
    return await hasTikTokSession(page);
  } finally {
    await browser.close().catch(() => {});
  }
}

module.exports = { LoginSession, checkSession };
