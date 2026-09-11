"use strict";

/**
 * Automação de upload no TikTok Studio via Puppeteer, no Chrome REAL do
 * usuário (não o Chromium empacotado do Puppeteer).
 *
 * Porta para Puppeteer do fluxo que antes rodava no Power Automate Desktop
 * (ver automacao_pa.txt): pra cada avatar (avatar2, avatar3), abre os vídeos
 * pendentes em output/{dd-mm}/{avatar}, envia, define hashtags, vincula o
 * produto (com matching fuzzy pelo nome do arquivo), preenche o CTA "Compre
 * Aqui" e publica. Sem agendamento -- publica imediatamente, replicando o
 * comportamento atual do PAD (o bloco de agendar data/hora estava
 * DESATIVADO por lá).
 *
 * Perfil dedicado por conta: a partir do Chrome 136 o Google bloqueia
 * --remote-debugging-port/--remote-debugging-pipe quando o user-data-dir é o
 * perfil padrão do usuário (proteção contra automação "sequestrar" a sessão
 * logada real). Por isso cada conta tem seu próprio perfil dedicado, fora
 * da pasta do projeto (que fica em OneDrive), em
 * %LOCALAPPDATA%\TikTokShopAutomation\. Qual perfil vai com qual pasta de
 * vídeos sai do cadastro de contas da interface (tiktok-accounts.json, ver
 * tiktok_accounts.js); sem cadastro, cai no perfil legado por avatar
 * (chrome-profile-{avatar}) e o login é feito na mão na primeira execução.
 */

const fs = require("fs");
const path = require("path");

const { launchChrome, resolveChromePath } = require("./chrome_profile");
const accountsStore = require("./tiktok_accounts");
const { getOutputFolder } = require("./core");

const UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video";
const LOGIN_WAIT_TIMEOUT_MS = 5 * 60 * 1000; // até 5 min pra login manual na primeira vez

const AVATARS = ["avatar2", "avatar3"];
const MAX_VIDEOS_PER_RUN = 30; // mesmo limite de segurança do PAD (index > 30 -> EXIT LOOP)
const CAPTION_HASHTAGS = "#tiktokshop #promoção #oferta";
const PURCHASE_BUTTON_TEXT = "Compre Aqui";

// A UI do TikTok Studio muda de idioma por conta -- confirmado em teste real
// que uma conta carrega em inglês mesmo com o resto (legenda, produto) em
// português. Todo texto de botão precisa aceitar as duas variantes.
const NEXT_TEXTS = ["Avançar", "Next"];
const ADD_TEXTS = ["Adicionar", "Add"];
const CANCEL_TEXTS = ["Cancelar", "Cancel"];
const DISCARD_TEXTS = ["Descartar", "Discard"];
const NOT_NOW_TEXTS = ["Agora não", "Not now"];
const PUBLISH_TEXTS = ["Publicar", "Post"];
const EXIT_TEXTS = ["Sair", "Log out", "Exit"];
const PRODUCT_LINK_STEP_TEXTS = ["Produtos", "Products"];
const ADD_PRODUCT_LINKS_HEADING_TEXTS = ["Adicionar links de produto", "Add product links"];
const STOCK_HEADER_TEXTS = ["Stock", "Estoque"];

// Antes um caminho fixo dentro da pasta do projeto (que fica em OneDrive) --
// gerar dezenas de vídeos ali fazia o OneDrive sincronizar tudo em paralelo
// com a automação rodando, sobrecarregando disco/CPU o suficiente pro Chrome
// ficar lento demais pra responder ao protocolo do Puppeteer (ver
// forceCloseBrowser). Agora lê de config.json (mesma fonte que o Python e a
// GUI usam -- ver core.js/getOutputFolder) em vez de duplicar o caminho.
function outputBaseDir() {
  return getOutputFolder(path.join(__dirname, ".."));
}
const USED_VIDEOS_DIR = "C:\\Users\\lucas\\OneDrive\\onedrive\\Documentos\\upload videos\\utilizados";

/**
 * Perfil dedicado da pasta de vídeos -- o da conta cadastrada para ela
 * (tiktok-accounts.json), ou o perfil legado por avatar se não houver
 * cadastro. Fica fora da pasta do projeto (que está em OneDrive).
 */
function resolveProfileDir(avatar) {
  return accountsStore.resolveProfileDirForFolder(avatar, path.join(__dirname, ".."));
}

/** Nome amigável da conta dessa pasta, só pra deixar o log claro. */
function describeAccount(avatar) {
  const account = accountsStore.findAccountByFolder(
    accountsStore.loadAccounts(path.join(__dirname, "..")),
    avatar
  );
  return account ? `${account.label}${account.email ? ` <${account.email}>` : ""}` : "sem conta cadastrada";
}

async function launchBrowser(avatar) {
  const userDataDir = resolveProfileDir(avatar);
  console.log(
    `[tiktok_uploader] [${avatar}] Abrindo Chrome com perfil dedicado em ${userDataDir} (${describeAccount(avatar)})`
  );
  return launchChrome(userDataDir);
}

/**
 * Fecha o browser sem arriscar travar pra sempre: browser.close() também
 * depende do Chrome responder ao protocolo, então se o Chrome já estiver
 * "surdo" (ver protocolTimeout em chrome_profile.js -- foi exatamente esse
 * sintoma que causou os "Runtime.callFunctionOn timed out" em produção) o
 * close() ficaria pendurado também. Dá um prazo curto pro close educado e,
 * se estourar, mata o processo do Chrome na marra -- garante que não sobra
 * chrome.exe zumbi consumindo memória/CPU pra próxima tentativa.
 */
async function forceCloseBrowser(browser) {
  try {
    await Promise.race([
      browser.close(),
      new Promise((_, reject) => setTimeout(() => reject(new Error("close timeout")), 8000)),
    ]);
  } catch (_) {
    try {
      browser.process()?.kill("SIGKILL");
    } catch (_) {
      /* ignore */
    }
  }
}

/** Roda uma tarefa best-effort (screenshot, descartar rascunho) com prazo curto -- não deixa a limpeza pendurar tanto quanto a falha original quando o Chrome já está travado. */
async function withShortTimeout(promise, ms = 8000) {
  return Promise.race([
    promise,
    new Promise((resolve) => setTimeout(resolve, ms)),
  ]);
}

/** dd-mm de hoje, no mesmo formato das pastas de output (ex: "04-09"). */
function todayFolderName() {
  const now = new Date();
  const dd = String(now.getDate()).padStart(2, "0");
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  return `${dd}-${mm}`;
}

/** Remove aspas nas pontas e o sufixo de data (_dd-mm ou _dd-mm-aaaa) do nome do arquivo. */
function productNameFromFilename(filename) {
  const semExtensao = filename.replace(/\.mp4$/i, "");
  const semAspas = semExtensao.trim().replace(/^"+|"+$/g, "").trim();
  return semAspas.replace(/_\d{1,2}-\d{1,2}(-\d{2,4})?\s*$/, "").trim();
}

function getPendingVideos(dateFolder, avatar) {
  const dir = path.join(outputBaseDir(), dateFolder, avatar);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.toLowerCase().endsWith(".mp4"))
    .map((f) => path.join(dir, f));
}

function moveToUsedFolder(videoPath) {
  fs.mkdirSync(USED_VIDEOS_DIR, { recursive: true });
  const dest = path.join(USED_VIDEOS_DIR, path.basename(videoPath));
  if (fs.existsSync(dest)) {
    console.log(`[tiktok_uploader] Já existe em "utilizados", mantendo original: ${dest}`);
    return;
  }
  try {
    fs.renameSync(videoPath, dest);
  } catch (err) {
    if (err.code === "EXDEV") {
      fs.copyFileSync(videoPath, dest);
      fs.unlinkSync(videoPath);
    } else {
      throw err;
    }
  }
  console.log(`[tiktok_uploader] Movido para utilizados: ${dest}`);
}

/** Clica um botão pelo texto visível (TikTok Studio não expõe seletores estáveis). Aceita 1 texto ou array (PT/EN). */
async function clickButtonByText(page, textOrTexts, { timeout = 5000 } = {}) {
  const textos = Array.isArray(textOrTexts) ? textOrTexts : [textOrTexts];
  const handle = await page
    .waitForFunction(
      (textos) =>
        Array.from(document.querySelectorAll("button")).find(
          (b) => b.textContent && textos.includes(b.textContent.trim())
        ) || null,
      { timeout },
      textos
    )
    .catch(() => null);
  if (!handle) return false;
  const el = handle.asElement();
  if (!el) return false;
  await el.click();
  return true;
}

/** Espera qualquer um dos textos informados aparecer na página (PT/EN). */
async function waitForPageText(page, textOrTexts, { timeout = 60000 } = {}) {
  const textos = Array.isArray(textOrTexts) ? textOrTexts : [textOrTexts];
  return page.waitForFunction(
    (textos) => textos.some((t) => document.body.innerText.includes(t)),
    { timeout },
    textos
  );
}

/**
 * Clica o botão de ação de um modal (o que não é "Cancelar"), tentando por
 * texto conhecido (PT/EN) e, se não achar, pelo único botão restante depois
 * de excluir os de cancelar -- assim funciona mesmo se o idioma mudar.
 *
 * Usa waitForFunction (polling) em vez de um evaluate único: o React monta
 * o modal de forma assíncrona, então um sleep fixo seguido de uma checagem
 * pontual pode rodar ANTES do botão existir mesmo com o modal já visível na
 * tela -- foi exatamente essa corrida que causou um "container não
 * encontrado" com o modal aberto no screenshot de debug.
 */
async function clickModalActionButton(page, { containerSelector, buttonsSelector = "button", candidateTexts, excludeTexts = CANCEL_TEXTS, timeout = 10000 }) {
  const handle = await page
    .waitForFunction(
      (containerSelector, buttonsSelector, candidateTexts, excludeTexts) => {
        const container = document.querySelector(containerSelector);
        if (!container) return null;
        const buttons = Array.from(container.querySelectorAll(buttonsSelector));
        if (buttons.length === 0) return null;

        let alvo = buttons.find((b) => candidateTexts.includes(b.textContent.trim()));
        if (!alvo) {
          const restantes = buttons.filter((b) => !excludeTexts.includes(b.textContent.trim()));
          if (restantes.length === 1) alvo = restantes[0];
        }
        return alvo || null;
      },
      { timeout, polling: 200 },
      containerSelector,
      buttonsSelector,
      candidateTexts,
      excludeTexts
    )
    .catch(() => null);

  if (!handle) {
    const diagnostico = await page
      .evaluate(
        (containerSelector, buttonsSelector) => {
          const container = document.querySelector(containerSelector);
          if (!container) return `container "${containerSelector}" não encontrado`;
          const buttons = Array.from(container.querySelectorAll(buttonsSelector));
          return `container encontrado, textos disponíveis: [${buttons.map((b) => b.textContent.trim()).join(", ")}]`;
        },
        containerSelector,
        buttonsSelector
      )
      .catch(() => "não consegui diagnosticar o estado da página");
    return `ERRO: botão de ação não encontrado em "${containerSelector}" após ${timeout}ms (${diagnostico})`;
  }

  const el = handle.asElement();
  if (!el) return `ERRO: handle inválido em "${containerSelector}"`;
  const textoClicado = await page.evaluate((b) => b.textContent.trim(), el).catch(() => "?");
  await el.click();
  return `OK: clicou em "${textoClicado}"`;
}

async function pageContainsText(page, text) {
  return page.evaluate((t) => document.body.innerText.includes(t), text);
}

/** Descarta o aviso "Um vídeo que você estava editando não foi salvo", se aparecer. */
/**
 * Descarta qualquer aviso de "vídeo/rascunho não salvo" pendurado no início
 * da tela de upload. Roda em loop (até MAX_DRAFT_DISCARD_ROUNDS vezes) em
 * vez de tentar só 2 cliques fixos -- quando um vídeo anterior falha no meio
 * do fluxo e o browser é fechado à força (ver runForAvatar/CLI), pode
 * sobrar mais de um rascunho/confirmação pendurada no servidor do TikTok, e
 * cada uma aparece como um prompt separado na próxima vez que a tela de
 * upload é aberta.
 */
async function dismissUnsavedDraftIfPresent(page) {
  const MAX_DRAFT_DISCARD_ROUNDS = 10;
  let descartouAlgum = false;

  for (let i = 0; i < MAX_DRAFT_DISCARD_ROUNDS; i++) {
    const clicked = await clickButtonByText(page, DISCARD_TEXTS, { timeout: i === 0 ? 4000 : 2500 });
    if (!clicked) break;
    descartouAlgum = true;
    console.log(`[tiktok_uploader] Aviso de rascunho pendurado encontrado, descartando (rodada ${i + 1})...`);
    await sleep(500);
  }

  if (!descartouAlgum) {
    console.log("[tiktok_uploader] Nenhum aviso de rascunho não salvo encontrado.");
    return false;
  }
  console.log("[tiktok_uploader] Rascunho(s) pendurado(s) descartado(s).");
  return true;
}

/**
 * Tenta descartar o rascunho atual depois de um vídeo falhar no meio do
 * fluxo, ANTES de fechar o browser -- evita deixar o rascunho pendurado no
 * servidor do TikTok, que confunde a próxima tentativa (ver
 * dismissUnsavedDraftIfPresent). Melhor esforço: nunca lança erro.
 */
async function bestEffortDiscardDraft(page) {
  try {
    for (let i = 0; i < 3; i++) {
      const clicked = await clickButtonByText(page, DISCARD_TEXTS, { timeout: 2500 });
      if (!clicked) break;
      await sleep(500);
    }
  } catch (_) {
    /* melhor esforço -- ignora falhas aqui */
  }
}

async function saveDebugScreenshot(page, baseDir, label) {
  const dir = path.join(baseDir, "output", "_uploader-debug");
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${label}_${Date.now()}.png`);
  await page.screenshot({ path: file });
  console.log(`[tiktok_uploader] Screenshot salvo em ${file}`);
  return file;
}

/**
 * Abre a página de upload (esperando login manual se for a primeira vez),
 * descarta rascunho pendente (se houver) e envia o arquivo de vídeo via CDP.
 */
async function openUploadPage(browser, videoPath, { baseDir = process.cwd() } = {}) {
  if (!fs.existsSync(videoPath)) {
    throw new Error(`Vídeo não encontrado: ${videoPath}`);
  }

  const [page] = await browser.pages();
  console.log(`[tiktok_uploader] Abrindo ${UPLOAD_URL} ...`);
  await page.goto(UPLOAD_URL, { waitUntil: "networkidle2", timeout: 60000 });

  console.log(
    "[tiktok_uploader] Se aparecer tela de login, faça manualmente na janela do Chrome " +
      "que abriu -- vou aguardar até 5 minutos pela tela de upload."
  );
  await page.waitForSelector('input[type="file"]', { timeout: LOGIN_WAIT_TIMEOUT_MS });

  await dismissUnsavedDraftIfPresent(page);

  const fileInputHandle = await page.waitForSelector('input[type="file"]', { timeout: 15000 });
  console.log(`[tiktok_uploader] Enviando arquivo: ${videoPath}`);
  await fileInputHandle.uploadFile(videoPath);

  // Confirma que o upload terminou de processar (equivalente ao "Enviado" do PAD;
  // em contas com a UI em inglês o texto vira "Uploaded").
  await waitForPageText(page, ["Enviado", "Uploaded"], { timeout: 120000 }).catch(() => {
    console.log('[tiktok_uploader] Aviso: não vi o texto "Enviado"/"Uploaded" -- seguindo mesmo assim.');
  });
  const left = await page
    .waitForSelector(".public-DraftEditor-content", { timeout: 60000 })
    .catch(() => null);

  if (!left) {
    console.log(
      "[tiktok_uploader] Aviso: não consegui confirmar que o upload avançou (a tela pode " +
        "ainda estar processando)."
    );
  } else {
    console.log("[tiktok_uploader] Vídeo enviado, TikTok está processando.");
  }

  return page;
}

/** Limpa o editor de legenda (Draft.js) e digita as hashtags fixas. */
async function setCaptionHashtags(page, texto) {
  return page.evaluate(async (texto) => {
    const editor = document.querySelector(".public-DraftEditor-content");
    if (!editor) return "ERRO: editor não encontrado";

    editor.focus();

    let tentativas = 0;
    const maxTentativas = 300;
    while (editor.textContent.length > 0 && tentativas < maxTentativas) {
      editor.dispatchEvent(
        new InputEvent("beforeinput", { inputType: "deleteContentBackward", bubbles: true, cancelable: true })
      );
      editor.dispatchEvent(
        new InputEvent("input", { inputType: "deleteContentBackward", bubbles: true, cancelable: true })
      );
      tentativas++;
      await new Promise((r) => setTimeout(r, 15));
    }
    if (tentativas >= maxTentativas) {
      return `AVISO: limite de apagar atingido. Texto restante: "${editor.textContent}"`;
    }

    editor.focus();
    document.execCommand("insertText", false, texto);
    await new Promise((r) => setTimeout(r, 100));

    const textoFinal = editor.textContent.trim();
    return textoFinal.includes(texto.slice(0, 15))
      ? `OK: legenda definida. Texto atual: "${textoFinal}"`
      : `FALHOU: texto atual ficou "${textoFinal}"`;
  }, texto);
}

/** Clica em "Adicionar"/"Add" no bloco de âncora de link de produto. */
async function clickAddAnchor(page) {
  return clickModalActionButton(page, {
    containerSelector: ".anchor-tag-container",
    candidateTexts: ADD_TEXTS,
    excludeTexts: [],
  });
}

/**
 * Clica em "Adicionar"/"Add" e confirma que abriu o modal certo (o de
 * "Add link" / "Link type: Products"). Vários modais diferentes do TikTok
 * Studio reaproveitam a mesma classe genérica ".common-modal-body" -- se um
 * vídeo anterior falhou no meio do fluxo e deixou um rascunho pendurado no
 * servidor, pode aparecer um modal de confirmação de descarte no lugar do
 * modal de produto esperado. Se isso acontecer, fecha esse modal (sem
 * descartar o vídeo atual) e tenta clicar em Add de novo.
 */
async function openAddLinkModal(page) {
  const MAX_TENTATIVAS = 3;
  let ultimoResultado = "ERRO: não tentou clicar em Add";

  for (let tentativa = 1; tentativa <= MAX_TENTATIVAS; tentativa++) {
    await humanClickPause();
    ultimoResultado = await clickAddAnchor(page);
    if (ultimoResultado.startsWith("ERRO")) return ultimoResultado;

    // 8s (era 4s): num lote grande, com o Python gerando vídeo (ffmpeg) em
    // paralelo, a máquina fica ocupada e o modal certo pode só renderizar o
    // texto "Products" um pouco mais devagar -- um timeout curto aqui
    // confundia "ainda carregando" com "modal errado" e disparava um
    // fecha-e-tenta-de-novo desnecessário em ~96% dos vídeos de um lote real
    // (sempre recuperava na tentativa seguinte, mas era retrabalho à toa).
    const abriuModalCerto = await waitForPageText(page, PRODUCT_LINK_STEP_TEXTS, { timeout: 8000 })
      .then(() => true)
      .catch(() => false);
    if (abriuModalCerto) return ultimoResultado;

    console.log(
      `[tiktok_uploader]   Modal inesperado depois de clicar em Add (tentativa ${tentativa}/${MAX_TENTATIVAS}) -- fechando e tentando de novo.`
    );
    const fechouSemDescartar = await clickButtonByText(page, NOT_NOW_TEXTS, { timeout: 3000 });
    if (!fechouSemDescartar) {
      await clickButtonByText(page, CANCEL_TEXTS, { timeout: 3000 }).catch(() => {});
    }
    await randomSleep(1000, 2000);
  }

  return `ERRO: não consegui abrir o modal "Add link" depois de ${MAX_TENTATIVAS} tentativas (último resultado do clique: ${ultimoResultado})`;
}

/** Clica "Avançar"/"Next" dentro do corpo do modal (.common-modal-body). */
async function clickAvancarModalBody(page) {
  return clickModalActionButton(page, {
    containerSelector: ".common-modal-body",
    buttonsSelector: ".button-group button",
    candidateTexts: NEXT_TEXTS,
    excludeTexts: CANCEL_TEXTS,
  });
}

/** Clica o botão de ação (não-cancelar) no rodapé do modal (.common-modal-footer). */
async function clickModalFooterButton(page, candidateTexts) {
  return clickModalActionButton(page, {
    containerSelector: ".common-modal-footer",
    candidateTexts,
    excludeTexts: CANCEL_TEXTS,
  });
}

/** Busca o produto pelo nome no campo de busca de vínculo de produto. */
async function searchProduct(page, nomeProduto) {
  return page.evaluate((nomeProduto) => {
    const campo = document.querySelector(".product-search-input-container input.TUXTextInputCore-input");
    if (!campo) return "ERRO: campo de busca não encontrado";

    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    nativeInputValueSetter.call(campo, nomeProduto);
    campo.dispatchEvent(new Event("input", { bubbles: true }));
    campo.dispatchEvent(new Event("change", { bubbles: true }));

    const icone = document.querySelector(".product-search-icon");
    if (!icone) return "ERRO: ícone de busca não encontrado";
    icone.click();
    return `OK: busca disparada para "${nomeProduto}"`;
  }, nomeProduto);
}

/**
 * Seleciona o produto na tabela de resultados. Tenta, em ordem: nome exato,
 * substring, similaridade geral (>=75% das palavras) e, por fim,
 * similaridade só das 6 primeiras palavras (>=85%) -- mesmo algoritmo de
 * fallback do PAD, pra lidar com nomes de produto truncados/alterados.
 *
 * Antes de clicar no radio, lê a linha (<tr>) inteira do produto encontrado
 * e confere a coluna "Stock"/"Estoque" pelo cabeçalho da tabela (não por uma
 * classe fixa, que pode não existir). Se o estoque for 0, NÃO seleciona o
 * produto -- devolve status "no_stock" pro chamador pular esse vídeo.
 *
 * Retorna { status: "ok"|"no_stock"|"not_found"|"ambiguous", message, stock, rowText }.
 */
async function selectProductFuzzy(page, nomeComData) {
  return page.evaluate(async (nomeComData, STOCK_HEADER_TEXTS) => {
    function normalizar(texto) {
      return texto.trim().replace(/\s+/g, " ");
    }
    function limparNomeProduto(nomeComData) {
      return nomeComData
        .trim()
        .replace(/^"+|"+$/g, "")
        .trim()
        .replace(/_\d{1,2}-\d{1,2}(-\d{2,4})?\s*$/, "")
        .trim();
    }
    function calcularSimilaridade(nomeArquivo, nomeProduto) {
      const palavrasArquivo = nomeArquivo.toLowerCase().split(/\s+/).filter((p) => p.length > 2);
      const palavrasProduto = nomeProduto.toLowerCase().split(/\s+/).filter((p) => p.length > 2);
      const setProduto = new Set(palavrasProduto);
      const acertos = palavrasArquivo.filter((p) => setProduto.has(p)).length;
      return acertos / palavrasArquivo.length;
    }
    function calcularSimilaridadePrefixo(nomeArquivo, nomeProduto, numPalavras) {
      const palavrasArquivo = nomeArquivo.toLowerCase().split(/\s+/).filter((p) => p.length > 2).slice(0, numPalavras);
      const palavrasProduto = nomeProduto.toLowerCase().split(/\s+/).filter((p) => p.length > 2);
      const setProduto = new Set(palavrasProduto);
      if (palavrasArquivo.length === 0) return 0;
      const acertos = palavrasArquivo.filter((p) => setProduto.has(p)).length;
      return acertos / palavrasArquivo.length;
    }

    const nomeProduto = limparNomeProduto(nomeComData);
    const nomeAlvo = normalizar(nomeProduto);

    const table = document.querySelector(".product-table-container table");
    if (!table) return "ERRO: tabela de produtos não encontrada";
    const rows = table.querySelectorAll("tbody tr");

    let candidatas = Array.from(rows).filter((row) => {
      const span = row.querySelector(".product-name");
      return span && normalizar(span.textContent) === nomeAlvo;
    });

    if (candidatas.length === 0) {
      candidatas = Array.from(rows).filter((row) => {
        const span = row.querySelector(".product-name");
        return span && normalizar(span.textContent).includes(nomeAlvo);
      });
    }

    if (candidatas.length === 0) {
      const scores = Array.from(rows)
        .map((row) => {
          const span = row.querySelector(".product-name");
          const nomeCompleto = span ? normalizar(span.textContent) : "";
          return { row, nomeCompleto, score: calcularSimilaridade(nomeAlvo, nomeCompleto) };
        })
        .filter((item) => item.score >= 0.75)
        .sort((a, b) => b.score - a.score);
      if (scores.length > 0) {
        candidatas = [scores[0].row];
        console.warn(
          `AVISO: usado match por similaridade geral (${(scores[0].score * 100).toFixed(0)}%). ` +
            `Nome buscado: "${nomeProduto}" | Produto escolhido: "${scores[0].nomeCompleto}"`
        );
      }
    }

    if (candidatas.length === 0) {
      const NUM_PALAVRAS_PREFIXO = 6;
      const LIMIAR_PREFIXO = 0.85;
      const scoresPrefixo = Array.from(rows)
        .map((row) => {
          const span = row.querySelector(".product-name");
          const nomeCompleto = span ? normalizar(span.textContent) : "";
          return { row, nomeCompleto, score: calcularSimilaridadePrefixo(nomeAlvo, nomeCompleto, NUM_PALAVRAS_PREFIXO) };
        })
        .filter((item) => item.score >= LIMIAR_PREFIXO)
        .sort((a, b) => b.score - a.score);
      if (scoresPrefixo.length > 0) {
        candidatas = [scoresPrefixo[0].row];
        console.warn(
          `AVISO: usado match por PREFIXO (${(scoresPrefixo[0].score * 100).toFixed(0)}% das ${NUM_PALAVRAS_PREFIXO} primeiras palavras). ` +
            `Nome buscado: "${nomeProduto}" | Produto escolhido: "${scoresPrefixo[0].nomeCompleto}"`
        );
      }
    }

    if (candidatas.length === 0) {
      return {
        status: "not_found",
        message: `ERRO: produto "${nomeProduto}" não encontrado (nem exato, nem parcial, nem por similaridade, nem por prefixo)`,
      };
    }
    if (candidatas.length > 1) {
      const nomes = candidatas.map((row) => row.querySelector(".product-name")?.textContent.trim());
      return {
        status: "ambiguous",
        message: `ERRO: "${nomeProduto}" corresponde a MAIS DE UM produto, ambíguo. Candidatos: ${nomes.join(" | ")}`,
      };
    }

    const linhaAlvo = candidatas[0];
    const nomeSelecionado = linhaAlvo.querySelector(".product-name")?.textContent.trim();
    const celulas = Array.from(linhaAlvo.querySelectorAll("td")).map((td) => td.textContent.trim());
    const rowText = celulas.join(" | ");

    // Acha a coluna de estoque pelo cabeçalho (mais confiável que uma classe
    // fixa, que pode não existir ou mudar).
    const headerCells = Array.from(table.querySelectorAll("thead th"));
    const stockIdx = headerCells.findIndex((th) => STOCK_HEADER_TEXTS.includes(th.textContent.trim()));

    let stock = null;
    if (stockIdx >= 0) {
      const bodyCells = Array.from(linhaAlvo.querySelectorAll("td"));
      const stockCellText = bodyCells[stockIdx] ? bodyCells[stockIdx].textContent.trim() : "";
      const match = stockCellText.replace(/[^\d]/g, "");
      if (match !== "") stock = parseInt(match, 10);
    }

    if (stock === 0) {
      return {
        status: "no_stock",
        stock: 0,
        rowText,
        message: `SEM ESTOQUE: produto "${nomeSelecionado}" com estoque 0 -- linha completa: [${rowText}]`,
      };
    }

    const radio = linhaAlvo.querySelector('input[type="radio"]');
    if (!radio) return { status: "not_found", message: "ERRO: radio button não encontrado na linha do produto" };

    radio.click();
    if (!radio.checked) {
      // Clique ocasionalmente não "pega" (visto 1x num lote de 76) -- espera
      // um instante e tenta mais uma vez antes de desistir.
      await new Promise((r) => setTimeout(r, 400));
      radio.click();
    }

    if (!radio.checked) {
      return { status: "not_found", message: "FALHOU: clique não marcou o radio (tentei 2x)" };
    }

    return {
      status: "ok",
      stock,
      rowText,
      message:
        `OK: produto selecionado: "${nomeSelecionado}" (busca original: "${nomeProduto}")` +
        (stock === null ? " -- aviso: não consegui identificar a coluna de estoque" : ` | estoque: ${stock}`),
    };
  }, nomeComData, STOCK_HEADER_TEXTS);
}

/** Preenche o campo de texto do botão de compra (não clica em nada). */
async function fillPurchaseText(page, texto) {
  return page.evaluate((texto) => {
    const campo = document.querySelector('input.TUXTextInputCore-input[aria-describedby$="_description"]');
    if (!campo) return "ERRO: campo de texto não encontrado";

    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    nativeInputValueSetter.call(campo, texto);
    campo.dispatchEvent(new Event("input", { bubbles: true }));
    campo.dispatchEvent(new Event("change", { bubbles: true }));

    return campo.value === texto ? `OK: campo preenchido com "${campo.value}"` : `FALHOU: valor ficou "${campo.value}"`;
  }, texto);
}

/** Clica no botão primário grande com um dos textos informados (ex: Publicar/Post). */
async function clickPrimaryLargeButton(page, candidateTexts) {
  return page.evaluate((candidateTexts) => {
    const buttons = document.querySelectorAll(".Button__root--type-primary.Button__root--size-large");
    const btn = Array.from(buttons).find((b) => candidateTexts.includes(b.textContent.trim()));
    if (!btn) return `ERRO: botão não encontrado (esperava um de: ${candidateTexts.join(", ")})`;
    const disabled = btn.disabled || btn.getAttribute("aria-disabled") === "true";
    if (disabled) return `ERRO: botão "${btn.textContent.trim()}" está desabilitado -- algum campo obrigatório pode estar faltando`;
    const textoClicado = btn.textContent.trim();
    btn.click();
    return `OK: clicou em "${textoClicado}"`;
  }, candidateTexts);
}

/**
 * Espera o botão de publicar ficar habilitado -- mais robusto do que tentar
 * casar a mensagem de "checagem concluída" (que muda de texto por idioma):
 * o TikTok mantém o botão desabilitado enquanto as checagens de copyright/
 * diretrizes rodam, e habilita assim que terminam (ou quando são puladas
 * por limite diário de verificações).
 */
async function waitForPublishButtonEnabled(page, { candidateTexts = PUBLISH_TEXTS, timeout = 3 * 60 * 1000 } = {}) {
  return page.waitForFunction(
    (candidateTexts) => {
      const buttons = document.querySelectorAll(".Button__root--type-primary.Button__root--size-large");
      const btn = Array.from(buttons).find((b) => candidateTexts.includes(b.textContent.trim()));
      if (!btn) return false;
      return !(btn.disabled || btn.getAttribute("aria-disabled") === "true");
    },
    { timeout, polling: 1000 },
    candidateTexts
  );
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Pausa com duração aleatória entre minMs e maxMs -- evita timing robótico fixo. */
function randomSleep(minMs, maxMs) {
  const ms = Math.round(minMs + Math.random() * (maxMs - minMs));
  return sleep(ms);
}

/** Pequena pausa de "reação humana" antes de clicar em algo (mouse/leitura). */
function humanClickPause() {
  return randomSleep(400, 1100);
}

/**
 * Loga o resultado de um passo (string "OK: ..." / "ERRO: ...") e lança se
 * for erro -- pra falhar rápido e com mensagem clara em vez de seguir cego
 * até um timeout genérico bem mais tarde (foi assim que um clique de modal
 * que falhou silenciosamente só deu erro 20s depois, num passo seguinte).
 */
function logStep(result) {
  console.log(`[tiktok_uploader]   ${result}`);
  if (typeof result === "string" && result.startsWith("ERRO")) {
    throw new Error(result);
  }
  return result;
}

/**
 * Processa um vídeo do início ao fim: upload, legenda/hashtags, vínculo de
 * produto (com checagem de estoque -- pula o vídeo se for 0), CTA de compra
 * e publicação. Não agenda -- publica imediatamente. Com `dryRun: true`,
 * faz tudo até preencher o CTA e para antes de publicar (descarta o
 * rascunho no final em vez de postar) -- útil pra testar sem publicar.
 */
async function processVideo(browser, videoPath, { baseDir = process.cwd(), dryRun = false } = {}) {
  const nomeArquivo = path.basename(videoPath);
  console.log(`\n[tiktok_uploader] === Processando: ${nomeArquivo}${dryRun ? " [DRY-RUN]" : ""} ===`);

  const page = await openUploadPage(browser, videoPath, { baseDir });

  console.log("[tiktok_uploader] Definindo legenda/hashtags...");
  console.log(`[tiktok_uploader]   ${await setCaptionHashtags(page, CAPTION_HASHTAGS)}`);
  await waitForPageText(page, "#oferta", { timeout: 15000 }).catch(() => {});

  console.log("[tiktok_uploader] Abrindo vínculo de produto...");
  logStep(await openAddLinkModal(page));
  await randomSleep(1500, 2800);
  await humanClickPause();
  logStep(await clickAvancarModalBody(page));

  await waitForPageText(page, ADD_PRODUCT_LINKS_HEADING_TEXTS, { timeout: 20000 });
  await randomSleep(1500, 2800);

  const nomeProduto = productNameFromFilename(nomeArquivo);
  console.log(`[tiktok_uploader] Buscando produto: "${nomeProduto}"`);
  await humanClickPause();
  logStep(await searchProduct(page, nomeProduto));
  await randomSleep(4000, 6500);

  const selecao = await selectProductFuzzy(page, nomeArquivo);
  console.log(`[tiktok_uploader]   ${selecao.message}`);
  if (selecao.status === "not_found" || selecao.status === "ambiguous") {
    throw new Error(`Falha ao selecionar produto: ${selecao.message}`);
  }
  if (selecao.status === "no_stock") {
    console.log(`[tiktok_uploader] Produto sem estoque -- pulando este vídeo (não publica, não move pra utilizados).`);
    await clickButtonByText(page, DISCARD_TEXTS, { timeout: 5000 }).catch(() => {});
    await clickButtonByText(page, DISCARD_TEXTS, { timeout: 3000 }).catch(() => {});
    await saveDebugScreenshot(page, baseDir, `sem-estoque_${nomeArquivo.replace(/[^a-z0-9]/gi, "_").slice(0, 40)}`);
    console.log(`[tiktok_uploader] === Pulado (sem estoque): ${nomeArquivo} ===`);
    return { status: "skipped_no_stock" };
  }

  // Só marca como "usado" depois de confirmar que o produto tem estoque e o
  // fluxo vai seguir até o fim.
  moveToUsedFolder(videoPath);

  await humanClickPause();
  logStep(await clickModalFooterButton(page, NEXT_TEXTS));
  // Espera o campo do CTA aparecer (mais confiável que casar o texto do
  // título da tela, que muda por idioma).
  await page.waitForSelector('input.TUXTextInputCore-input[aria-describedby$="_description"]', { timeout: 20000 });
  await randomSleep(2200, 3800);

  console.log("[tiktok_uploader] Preenchendo CTA de compra...");
  logStep(await fillPurchaseText(page, PURCHASE_BUTTON_TEXT));
  await humanClickPause();
  logStep(await clickModalFooterButton(page, ADD_TEXTS));

  if (dryRun) {
    await randomSleep(2200, 3800);
    console.log("[tiktok_uploader] [DRY-RUN] Parando antes de publicar -- descartando rascunho.");
    await saveDebugScreenshot(page, baseDir, `dry-run_${nomeArquivo.replace(/[^a-z0-9]/gi, "_").slice(0, 40)}`);
    await clickButtonByText(page, DISCARD_TEXTS, { timeout: 5000 }).catch(() => {});
    await clickButtonByText(page, DISCARD_TEXTS, { timeout: 3000 }).catch(() => {});
    console.log(`[tiktok_uploader] === [DRY-RUN] Concluído (não publicado): ${nomeArquivo} ===`);
    return { status: "dry_run_ok" };
  }

  // Sem agendamento -- publica direto (bloco de agendar estava desativado no PAD).
  await randomSleep(4000, 6500);

  console.log("[tiktok_uploader] Aguardando checagens (copyright/diretrizes) liberarem o botão de publicar...");
  await waitForPublishButtonEnabled(page, { timeout: 3 * 60 * 1000 });
  await randomSleep(4000, 6500);
  await humanClickPause();
  logStep(await clickPrimaryLargeButton(page, PUBLISH_TEXTS));

  await randomSleep(1500, 2800);
  await clickButtonByText(page, EXIT_TEXTS, { timeout: 3000 }).catch(() => {});

  await saveDebugScreenshot(page, baseDir, `publicado_${nomeArquivo.replace(/[^a-z0-9]/gi, "_").slice(0, 40)}`);
  console.log(`[tiktok_uploader] === Concluído: ${nomeArquivo} ===`);
  return { status: "published" };
}

/** Roda a fila de vídeos pendentes de um avatar, do início ao fim (1 browser). */
async function runForAvatar(avatar, dateFolder, { baseDir = process.cwd(), dryRun = false } = {}) {
  const videos = getPendingVideos(dateFolder, avatar);
  if (videos.length === 0) {
    console.log(`[tiktok_uploader] [${avatar}] Nenhum vídeo pendente em output/${dateFolder}/${avatar}.`);
    return;
  }
  console.log(`[tiktok_uploader] [${avatar}] ${videos.length} vídeo(s) pendente(s).`);

  let browser = await launchBrowser(avatar);
  let index = 0;
  try {
    for (const videoPath of videos) {
      if (index >= MAX_VIDEOS_PER_RUN) {
        console.log(`[tiktok_uploader] [${avatar}] Limite de ${MAX_VIDEOS_PER_RUN} vídeos por execução atingido.`);
        break;
      }
      try {
        await processVideo(browser, videoPath, { baseDir, dryRun });
      } catch (err) {
        console.error(`[tiktok_uploader] [${avatar}] Falhou em "${path.basename(videoPath)}": ${err.message}`);
        await withShortTimeout(
          (async () => {
            const [page] = await browser.pages();
            await saveDebugScreenshot(page, baseDir, "erro").catch(() => {});
            await bestEffortDiscardDraft(page);
          })().catch(() => {})
        );
        // Um Chrome que travou o suficiente pra estourar o protocolTimeout
        // raramente volta a responder sozinho -- insistir na mesma instância
        // só repetiria a mesma falha nos próximos vídeos da fila. Reabre do
        // zero antes de seguir.
        await forceCloseBrowser(browser);
        browser = await launchBrowser(avatar);
      }
      index++;
    }
  } finally {
    await forceCloseBrowser(browser);
  }
}

module.exports = {
  resolveChromePath,
  resolveProfileDir,
  launchBrowser,
  dismissUnsavedDraftIfPresent,
  openUploadPage,
  productNameFromFilename,
  getPendingVideos,
  moveToUsedFolder,
  setCaptionHashtags,
  selectProductFuzzy,
  processVideo,
  runForAvatar,
};

if (require.main === module) {
  (async () => {
    const args = process.argv.slice(2);
    const dryRun = args.includes("--dry-run");

    // Modo arquivo único: posta só esse vídeo e sai. Usado pelo main.py pra
    // postar cada vídeo já gerado, um de cada vez, depois que TODOS os
    // vídeos (de todos os avatares) já tiverem sido gerados.
    const fileIdx = args.indexOf("--file");
    if (fileIdx >= 0) {
      const filePath = args[fileIdx + 1];
      // Qualquer nome de pasta serve (as contas cadastradas na interface podem
      // usar nomes fora da lista fixa AVATARS) -- o avatar é o primeiro
      // argumento posicional que não é flag nem o caminho do arquivo.
      const avatar = args.find((a, i) => !a.startsWith("--") && i !== fileIdx + 1);
      if (!filePath || !avatar) {
        console.error('Uso: node electron/tiktok_uploader.js --file "<caminho-do-video>" <avatar> [--dry-run]');
        process.exit(1);
      }
      const baseDir = path.join(__dirname, "..");
      console.log(`[tiktok_uploader] Modo arquivo único: "${filePath}" (${avatar})${dryRun ? " | DRY-RUN" : ""}`);

      // Retry com navegador novo: um Chrome que trava a ponto de estourar o
      // protocolTimeout (ver chrome_profile.js) normalmente não volta a
      // responder sozinho -- insistir na MESMA instância só repete a mesma
      // falha (foi o que aconteceu em produção: 3 vídeos seguidos falhando
      // igual pro mesmo avatar). Fecha na marra e tenta de novo do zero antes
      // de desistir de vez.
      const MAX_ATTEMPTS = 2;
      let lastErr = null;
      for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
        const browser = await launchBrowser(avatar);
        try {
          const resultado = await processVideo(browser, filePath, { baseDir, dryRun });
          console.log(`[tiktok_uploader] Resultado: ${resultado.status}`);
          lastErr = null;
          await forceCloseBrowser(browser);
          break;
        } catch (err) {
          lastErr = err;
          console.error(`[tiktok_uploader] Falhou (tentativa ${attempt}/${MAX_ATTEMPTS}): ${err.message}`);
          await withShortTimeout(
            (async () => {
              const [page] = await browser.pages();
              await saveDebugScreenshot(page, baseDir, "erro-single").catch(() => {});
              await bestEffortDiscardDraft(page);
            })().catch(() => {})
          );
          await forceCloseBrowser(browser);
          if (attempt < MAX_ATTEMPTS) {
            console.log("[tiktok_uploader] Tentando de novo com um navegador novo...");
            await sleep(3000);
          }
        }
      }
      if (lastErr) {
        console.error(`[tiktok_uploader] Falhou definitivamente após ${MAX_ATTEMPTS} tentativa(s): ${lastErr.message}`);
        process.exitCode = 1;
      }
      return;
    }

    const dateArg = args.find((a) => /^\d{1,2}-\d{1,2}$/.test(a));
    const baseDir = path.join(__dirname, "..");

    // Sem avatar informado, roda as pastas das contas cadastradas na
    // interface; se não houver cadastro nenhum, cai na lista fixa antiga.
    const contasCadastradas = accountsStore
      .loadAccounts(baseDir)
      .map((a) => a.folder)
      .filter(Boolean);
    const avatarArgs = args.filter((a) => !a.startsWith("--") && a !== dateArg);
    const avatares =
      avatarArgs.length > 0 ? avatarArgs : contasCadastradas.length > 0 ? contasCadastradas : AVATARS;
    const dateFolder = dateArg || todayFolderName();

    console.log(`[tiktok_uploader] Data: ${dateFolder} | Avatares: ${avatares.join(", ")}${dryRun ? " | DRY-RUN (não publica)" : ""}`);

    for (const avatar of avatares) {
      try {
        await runForAvatar(avatar, dateFolder, { baseDir, dryRun });
      } catch (err) {
        console.error(`[tiktok_uploader] [${avatar}] Erro fatal: ${err.message}`);
      }
    }
    console.log("[tiktok_uploader] Finalizado.");
  })();
}
