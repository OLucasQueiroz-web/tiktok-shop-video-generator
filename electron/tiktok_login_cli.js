"use strict";

/**
 * CLI que faz o login manual/verificação de sessão de uma conta do TikTok.
 *
 * Roda como processo `node` separado -- nunca é `require()`ado pelo processo
 * principal do Electron (main.js). Motivo: depende do puppeteer-extra, e o
 * build empacotado (electron-builder) exclui node_modules de propósito
 * (ver package.json -> build.files: "!node_modules") pra manter o app.asar
 * enxuto. Isso só funciona porque main.js sempre spawna esse script
 * apontando pra pasta REAL do projeto no disco (baseDir), que tem seu
 * node_modules de verdade -- mesmo padrão já usado por tiktok_uploader.js
 * (invocado via `node` a partir de main.py).
 *
 * Uso:
 *   node tiktok_login_cli.js login    <accountId>
 *   node tiktok_login_cli.js verify   <accountId>
 *   node tiktok_login_cli.js discover
 *
 * "discover" varre os perfis do Chrome no disco procurando sessões que já
 * existem mas não estão cadastradas como conta (login feito à mão antes do
 * cadastro existir), confere se ainda estão logadas e já cadastra cada uma
 * automaticamente em tiktok-accounts.json -- é o que faz essas contas
 * aparecerem na interface sem o usuário precisar re-adicionar cada uma.
 *
 * Protocolo com o processo pai (main.js): cada linha de progresso sai no
 * stdout como "__LOG__:mensagem"; o resultado final sai como uma linha
 * "__RESULT__:<json>" pouco antes do processo terminar com código 0
 * (sucesso). Em erro, a mensagem vai pro stderr e o código de saída é 1.
 * Um SIGTERM do processo pai (usuário clicou em "Cancelar login") fecha o
 * Chrome antes de sair, em vez de deixar o perfil pendurado.
 */

const path = require("path");
const accountsStore = require("./tiktok_accounts");
const { LoginSession, checkSession } = require("./tiktok_login");

const baseDir = path.join(__dirname, "..");

function emitLog(mensagem) {
  console.log(`__LOG__:${mensagem}`);
}

function emitResult(dados) {
  console.log(`__RESULT__:${JSON.stringify(dados)}`);
}

/**
 * Confere cada perfil do Chrome "órfão" (sem conta cadastrada) e, pros que
 * ainda tiverem sessão válida (ou não -- importa de qualquer forma, só
 * marca o status certo), cria a conta correspondente em
 * tiktok-accounts.json. Idempotente: uma vez importado, o perfil vira uma
 * conta normal e não aparece mais como "órfão" nas próximas execuções.
 */
async function discover() {
  const pendentes = accountsStore.findUnclaimedProfileDirs(baseDir);
  if (pendentes.length === 0) {
    emitResult({ imported: [] });
    return;
  }

  const lista = accountsStore.loadAccounts(baseDir);
  const importados = [];

  for (const p of pendentes) {
    emitLog(`Verificando perfil já existente "${p.profileDirName}"...`);
    let logado = false;
    try {
      logado = await checkSession({ profileDirName: p.profileDirName });
    } catch (err) {
      emitLog(`Não consegui checar "${p.profileDirName}" (${err.message}) -- pulando.`);
      continue;
    }

    const id = accountsStore.nextAccountId(lista);
    const conta = {
      id,
      label: `Conta ${p.suggestedFolder}`,
      email: "",
      folder: p.suggestedFolder,
      profileDirName: p.profileDirName,
      loggedIn: logado,
      lastLoginAt: logado ? new Date().toISOString() : null,
    };
    lista.push(conta);
    importados.push({ id, label: conta.label, folder: conta.folder, loggedIn: logado });
    emitLog(
      logado
        ? `"${p.profileDirName}" já estava logado -- importado como "${conta.label}".`
        : `"${p.profileDirName}" existe mas sem sessão ativa -- importado como "${conta.label}" mesmo assim.`
    );
  }

  if (importados.length > 0) accountsStore.saveAccounts(baseDir, lista);
  emitResult({ imported: importados });
}

async function main() {
  const [, , modo, accountId] = process.argv;

  if (modo === "discover") {
    await discover();
    return;
  }

  if (!["login", "verify"].includes(modo) || !accountId) {
    console.error("Uso: node tiktok_login_cli.js <login|verify> <accountId> | discover");
    process.exitCode = 1;
    return;
  }

  const conta = accountsStore.findAccountById(accountsStore.loadAccounts(baseDir), accountId);
  if (!conta) {
    console.error(`Conta "${accountId}" não encontrada.`);
    process.exitCode = 1;
    return;
  }

  if (modo === "verify") {
    const loggedIn = await checkSession(conta);
    emitResult({ loggedIn });
    return;
  }

  const session = new LoginSession({
    account: conta,
    onEvent: (evento) => {
      if (evento.type === "log") emitLog(evento.message);
    },
  });

  process.on("SIGTERM", () => {
    session.cancel().finally(() => process.exit(1));
  });

  const resultado = await session.start();
  emitResult({ alreadyLoggedIn: !!resultado.alreadyLoggedIn });
}

main().catch((err) => {
  console.error(err && err.message ? err.message : String(err));
  process.exitCode = 1;
});
