"use strict";

/**
 * Chrome REAL do usuário + perfis dedicados, compartilhado entre o uploader
 * (tiktok_uploader.js) e o login de contas (tiktok_login.js).
 *
 * A partir do Chrome 136 o Google bloqueia --remote-debugging-port quando o
 * user-data-dir é o perfil padrão do usuário -- por isso cada conta tem seu
 * próprio perfil dedicado, fora da pasta do projeto (que fica em OneDrive).
 * Ver tiktok_accounts.js para onde esses perfis moram.
 */

const fs = require("fs");

const puppeteer = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");
puppeteer.use(StealthPlugin());

const CHROME_PATHS = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
];

function resolveChromePath() {
  const found = CHROME_PATHS.find((p) => fs.existsSync(p));
  if (!found) {
    throw new Error(
      `Chrome não encontrado em nenhum dos caminhos esperados: ${CHROME_PATHS.join(", ")}`
    );
  }
  return found;
}

/** Abre o Chrome real com um user-data-dir dedicado (cria a pasta se não existir). */
async function launchChrome(userDataDir) {
  fs.mkdirSync(userDataDir, { recursive: true });
  return puppeteer.launch({
    executablePath: resolveChromePath(),
    userDataDir,
    headless: false,
    defaultViewport: null,
  });
}

/**
 * true se o perfil tem sessão ativa no TikTok. O cookie `sessionid` é o
 * sinal mais confiável -- a URL sozinha engana (a home do tiktok.com carrega
 * igual logado e deslogado).
 */
async function hasTikTokSession(page) {
  const cookies = await page.cookies("https://www.tiktok.com").catch(() => []);
  return cookies.some((c) => c.name === "sessionid" && c.value);
}

module.exports = { CHROME_PATHS, resolveChromePath, launchChrome, hasTikTokSession };
