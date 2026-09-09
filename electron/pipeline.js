"use strict";

/**
 * Ponte com o core Python (main.py + src/) -- toda a lógica de vídeo/OCR/
 * ffmpeg continua lá; aqui só spawnamos o processo e traduzimos stdout em
 * eventos pro renderer (linhas "__PROG__:" viram progresso, o resto vira log).
 */

const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const HISTORY_JSON_PREFIX = "__HISTORY_JSON__:";
const PROGRESS_PREFIX = "__PROG__:";

/**
 * Prioridade: `python main.py` do código-fonte -> exe empacotado (build via
 * PyInstaller, console-mode) como fallback só se o Python não estiver
 * disponível no PATH. O exe empacotado é gerado manualmente (ver
 * TikTokShopVideoGeneratorCLI.spec) e fica desatualizado em relação ao
 * código-fonte sempre que main.py/src/ mudam sem um rebuild -- rodar direto
 * do código-fonte garante que a automação em uso é sempre a mais recente.
 *
 * Sempre passa --config com caminho ABSOLUTO: o .exe empacotado, quando
 * "frozen", faz os.chdir() pra própria pasta (ver _resolve_base_dir()/main()
 * em main.py) -- então tanto o "config.json" default quanto qualquer
 * diretório relativo dentro dele deixariam de apontar pro projeto.
 * config.json já guarda os diretórios como caminhos absolutos por esse
 * motivo (ver config.json).
 */
function resolvePythonInvocation(baseDir) {
  const configArg = ["--config", path.join(baseDir, "config.json")];
  const pythonCmd = process.platform === "win32" ? "python" : "python3";
  const mainPy = path.join(baseDir, "main.py");
  if (fs.existsSync(mainPy)) {
    return { cmd: pythonCmd, baseArgs: [mainPy, ...configArg] };
  }
  const packagedExe = path.join(baseDir, "dist", "TikTokShopVideoGeneratorCLI", "TikTokShopVideoGeneratorCLI.exe");
  return { cmd: packagedExe, baseArgs: configArg };
}

function makeLineSplitter(onLine) {
  let buffer = "";
  return (chunk) => {
    buffer += chunk.toString("utf-8");
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop();
    for (const line of lines) {
      if (line) onLine(line);
    }
  };
}

/**
 * Roda o pipeline (dry-run ou geração real). onLog/onProgress são chamados
 * em tempo real conforme as linhas chegam; a promise resolve com o código
 * de saída do processo ao final.
 *
 * Com autoPost=true (ignorado em dry-run), a postagem no TikTok Studio
 * (electron/tiktok_uploader.js) só começa depois que TODOS os vídeos (de
 * todos os avatares) já tiverem sido gerados -- ver --auto-post em main.py
 * e _run_standard_flow()/_post_video().
 */
function runPipeline(baseDir, dryRun, onLog, onProgress, autoPost = false) {
  return new Promise((resolve, reject) => {
    const { cmd, baseArgs } = resolvePythonInvocation(baseDir);
    // main.py liga auto-post por padrão -- passa explicitamente pra qualquer
    // lado (--auto-post/--no-auto-post) conforme o checkbox da UI, em vez de
    // só omitir a flag quando desmarcado (o que deixaria o padrão do CLI
    // "vazar" e postar mesmo com o checkbox desligado).
    const args = [
      ...baseArgs,
      ...(dryRun ? ["--dry-run"] : []),
      ...(dryRun ? [] : [autoPost ? "--auto-post" : "--no-auto-post"]),
    ];
    // TIKTOK_NODE_EXE/TIKTOK_ELECTRON_DIR: com auto-post, o Python chama o
    // uploader Node (_post_video() em main.py) -- passamos o Node bundled
    // (baseDir/node/node.exe, ver installer-assets/node/) quando existir
    // (instalador autossuficiente), senão main.py cai pro "node" do PATH
    // (build "dir" de desenvolvimento, onde o Node já está instalado por
    // quem desenvolve). NÃO usa o Node embutido do Electron (ELECTRON_RUN_
    // AS_NODE) de propósito -- ver spawnNode()/resolveNodeExe() em main.js
    // pro motivo (Node antigo demais pro puppeteer-core atual, ESM-only).
    const bundledNodeExe = path.join(baseDir, "node", "node.exe");
    const child = spawn(cmd, args, {
      cwd: baseDir,
      windowsHide: true,
      env: {
        ...process.env,
        ...(fs.existsSync(bundledNodeExe) ? { TIKTOK_NODE_EXE: bundledNodeExe } : {}),
        TIKTOK_ELECTRON_DIR: path.join(baseDir, "electron"),
      },
    });

    const splitter = makeLineSplitter((line) => {
      if (line.startsWith(PROGRESS_PREFIX)) {
        onProgress(line.slice(PROGRESS_PREFIX.length));
      } else {
        onLog(line);
      }
    });

    child.stdout.on("data", splitter);
    child.stderr.on("data", splitter);
    child.on("error", (err) => reject(new Error(`Não foi possível iniciar o Python ("${cmd}"): ${err.message}`)));
    child.on("close", (code) => resolve(code));
  });
}

/** Busca o histórico via `main.py --history --json` -- reaproveita a mesma leitura SQLite do core, sem duplicar SQL em JS. */
function getHistory(baseDir) {
  return new Promise((resolve, reject) => {
    const { cmd, baseArgs } = resolvePythonInvocation(baseDir);
    const child = spawn(cmd, [...baseArgs, "--history", "--json"], { cwd: baseDir, windowsHide: true });

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => (stdout += d.toString("utf-8")));
    child.stderr.on("data", (d) => (stderr += d.toString("utf-8")));
    child.on("error", (err) => reject(new Error(`Não foi possível iniciar o Python ("${cmd}"): ${err.message}`)));
    child.on("close", (code) => {
      const dataLine = stdout.split(/\r?\n/).find((l) => l.startsWith(HISTORY_JSON_PREFIX));
      if (!dataLine) {
        reject(new Error(stderr.trim() || `Não foi possível ler o histórico (código ${code}).`));
        return;
      }
      try {
        resolve(JSON.parse(dataLine.slice(HISTORY_JSON_PREFIX.length)));
      } catch (e) {
        reject(new Error(`Falha ao interpretar histórico: ${e.message}`));
      }
    });
  });
}

module.exports = { runPipeline, getHistory };
