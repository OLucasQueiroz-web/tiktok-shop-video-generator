"use strict";

/**
 * Helpers de arquivo/config usados pela GUI. Equivalente Node das partes de
 * gui.py (Tkinter) que só mexiam com o sistema de arquivos -- nada de
 * OCR/ffmpeg/vídeo aqui, isso continua em Python (ver pipeline.js).
 */

const fs = require("fs");
const path = require("path");

const VIDEO_EXTENSIONS = new Set([".mp4", ".mov", ".avi", ".mkv", ".webm"]);
const IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif"]);

function listFilesWithExt(dir, extSet) {
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .filter((d) => d.isFile() && extSet.has(path.extname(d.name).toLowerCase()))
    .map((d) => d.name)
    .sort((a, b) => a.localeCompare(b));
}

/**
 * { pasta_da_conta: nomeDoArquivo } -- um avatar por conta, sem mais
 * conceito de "perfil". Espelha get_avatar_videos_by_name() de
 * src/file_manager.py: cada vídeo solto direto em avatarsDir é o avatar de
 * UMA conta, identificada pelo nome do arquivo sem extensão.
 */
function listAvatarsByFolder(avatarsDir) {
  const result = {};
  for (const f of listFilesWithExt(avatarsDir, VIDEO_EXTENSIONS)) {
    result[path.basename(f, path.extname(f))] = f;
  }
  return result;
}

/**
 * Define (ou substitui) o vídeo de avatar de uma conta -- copia
 * sourceFilePath pra avatarsDir/<folder><ext>, removendo antes qualquer
 * vídeo existente com esse mesmo nome (outras extensões inclusive), já que
 * é sempre 1 vídeo por conta.
 */
function setAvatarVideo(avatarsDir, folder, sourceFilePath) {
  fs.mkdirSync(avatarsDir, { recursive: true });

  for (const f of listFilesWithExt(avatarsDir, VIDEO_EXTENSIONS)) {
    if (path.basename(f, path.extname(f)) === folder) {
      fs.unlinkSync(path.join(avatarsDir, f));
    }
  }

  const destName = `${folder}${path.extname(sourceFilePath).toLowerCase()}`;
  fs.copyFileSync(sourceFilePath, path.join(avatarsDir, destName));
  return destName;
}

/** Remove o vídeo de avatar de uma conta (qualquer extensão), se existir. */
function removeAvatarVideo(avatarsDir, folder) {
  let removed = false;
  for (const f of listFilesWithExt(avatarsDir, VIDEO_EXTENSIONS)) {
    if (path.basename(f, path.extname(f)) === folder) {
      fs.unlinkSync(path.join(avatarsDir, f));
      removed = true;
    }
  }
  return removed;
}

/** Imagens soltas direto em dir (sem subpastas) -- espelha get_background_images(). */
function getBackgroundImages(dir) {
  return listFilesWithExt(dir, IMAGE_EXTENSIONS).map((f) => path.join(dir, f));
}

/**
 * Agrupa imagens de produto por avatar de destino -- espelha
 * get_images_by_avatar_folder() de src/file_manager.py. Imagens soltas
 * direto em imagesDir ficam na chave "*" (valem para TODOS os avatares);
 * imagens em imagesDir/<avatar_stem>/ só geram vídeo pra aquele avatar.
 * Retorna { "*"?: [caminhos], [avatarStem]?: [caminhos] } (só chaves com
 * pelo menos 1 imagem).
 */
function getImagesByAvatarFolder(imagesDir) {
  const groups = {};
  if (!fs.existsSync(imagesDir)) return groups;

  const loose = listFilesWithExt(imagesDir, IMAGE_EXTENSIONS);
  if (loose.length) groups["*"] = loose.map((f) => path.join(imagesDir, f));

  const subdirs = fs
    .readdirSync(imagesDir, { withFileTypes: true })
    .filter((d) => d.isDirectory() && !d.name.startsWith("_"))
    .sort((a, b) => a.name.localeCompare(b.name));

  for (const dirent of subdirs) {
    const subPath = path.join(imagesDir, dirent.name);
    const imgs = listFilesWithExt(subPath, IMAGE_EXTENSIONS);
    if (imgs.length) groups[dirent.name] = imgs.map((f) => path.join(subPath, f));
  }
  return groups;
}

function copyFilesInto(destDir, filePaths) {
  fs.mkdirSync(destDir, { recursive: true });
  for (const f of filePaths) {
    fs.copyFileSync(f, path.join(destDir, path.basename(f)));
  }
  return filePaths.length;
}

/**
 * Move arquivos selecionados de srcDir para discardDir, sem apagar de vez --
 * espelha _remove_products() de gui.py. srcDir e discardDir são passados
 * separados (em vez de discardDir = srcDir/_descartados) porque srcDir pode
 * ser uma subpasta de avatar (background/avatar1/), mas o descarte fica
 * sempre no _descartados/ de nível raiz do tipo de produto.
 */
function moveToDiscarded(srcDir, discardDir, fileNames) {
  fs.mkdirSync(discardDir, { recursive: true });

  let moved = 0;
  for (const name of fileNames) {
    const src = path.join(srcDir, name);
    if (!fs.existsSync(src)) continue;

    const ext = path.extname(name);
    const stem = path.basename(name, ext);
    let dest = path.join(discardDir, name);
    if (fs.existsSync(dest)) {
      dest = path.join(discardDir, `${stem}_${Math.floor(Date.now() / 1000)}${ext}`);
    }
    try {
      fs.renameSync(src, dest);
    } catch {
      fs.copyFileSync(src, dest);
      fs.unlinkSync(src);
    }
    moved++;
  }
  return moved;
}

/**
 * Chaves da API Groq -- src/product_identifier.py já sabe alternar entre
 * várias (GROQ_API_KEY, GROQ_API_KEY2, GROQ_API_KEY3, ...) quando uma
 * atinge o limite de requisições; aqui só gerenciamos essas variáveis no
 * .env. Nomes não precisam ser contíguos (remover a do meio não quebra as
 * outras -- o Python só lê o que existir e ordena pelo número do sufixo).
 */
const API_KEY_VAR_RE = /^GROQ_API_KEY(\d*)$/;

function envPathFor(baseDir) {
  return path.join(baseDir, ".env");
}

/** [{ varName, order, key }], ordenado como o Python lê (GROQ_API_KEY = ordem 1, depois 2, 3...). */
function listApiKeys(baseDir) {
  const envPath = envPathFor(baseDir);
  if (!fs.existsSync(envPath)) return [];

  const porVar = new Map(); // dedup: última ocorrência vence, como load_dotenv(override=True)
  for (const rawLine of fs.readFileSync(envPath, "utf-8").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;

    const varName = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    const match = API_KEY_VAR_RE.exec(varName);
    if (!match || !value) continue;

    const suffix = match[1];
    porVar.set(varName, { varName, order: suffix ? parseInt(suffix, 10) : 1, key: value });
  }
  return [...porVar.values()].sort((a, b) => a.order - b.order);
}

function hasApiKey(baseDir) {
  return listApiKeys(baseDir).length > 0;
}

/** Primeiro nome de variável livre: GROQ_API_KEY, senão GROQ_API_KEY2, GROQ_API_KEY3... */
function nextApiKeyVarName(baseDir) {
  const usados = new Set(listApiKeys(baseDir).map((k) => k.order));
  let n = 1;
  while (usados.has(n)) n++;
  return n === 1 ? "GROQ_API_KEY" : `GROQ_API_KEY${n}`;
}

function addApiKey(baseDir, key) {
  const varName = nextApiKeyVarName(baseDir);
  fs.appendFileSync(envPathFor(baseDir), `\n${varName}=${key.trim()}\n`, "utf-8");
  return varName;
}

/** Remove só a linha dessa variável -- preserva o resto do .env (outras chaves, GROQ ou não). */
function removeApiKey(baseDir, varName) {
  const envPath = envPathFor(baseDir);
  if (!fs.existsSync(envPath)) return;

  const linhas = fs.readFileSync(envPath, "utf-8").split(/\r?\n/);
  const mantidas = linhas.filter((rawLine) => {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) return true;
    const eq = line.indexOf("=");
    if (eq === -1) return true;
    return line.slice(0, eq).trim() !== varName;
  });
  fs.writeFileSync(envPath, mantidas.join("\n"), "utf-8");
}

/** Só os últimos caracteres visíveis -- a chave nunca precisa reaparecer inteira na interface. */
function maskApiKey(key) {
  if (key.length <= 8) return "••••••••";
  return `${key.slice(0, 4)}${"•".repeat(Math.min(20, key.length - 8))}${key.slice(-4)}`;
}

function readConfig(baseDir) {
  const configPath = path.join(baseDir, "config.json");
  return JSON.parse(fs.readFileSync(configPath, "utf-8"));
}

function writeConfig(baseDir, data) {
  const configPath = path.join(baseDir, "config.json");
  fs.writeFileSync(configPath, JSON.stringify(data, null, 2), "utf-8");
}

function getOutputFolder(baseDir) {
  try {
    return readConfig(baseDir).directories.output;
  } catch {
    return "output";
  }
}

function setOutputFolder(baseDir, folder) {
  const data = readConfig(baseDir);
  data.directories.output = folder;
  writeConfig(baseDir, data);
}

function basenames(paths) {
  return paths.map((p) => path.basename(p));
}

/** true se `value` já é um caminho absoluto dentro de `baseDir` (não precisa ser reancorado). */
function isAnchoredIn(value, baseDir) {
  if (!value) return false;
  return path.normalize(value).toLowerCase().startsWith(path.normalize(baseDir).toLowerCase());
}

/**
 * Corrige, se preciso, os caminhos do config.json pra apontarem pra dentro
 * de `baseDir` -- necessário pro instalador autossuficiente (ver
 * package.json -> "dist:installer"), onde config.json chega com nomes
 * relativos genéricos ("avatar", "background"...) que precisam virar
 * caminhos absolutos ancorados na pasta de instalação de cada máquina.
 *
 * Idempotente e seguro pra rodar sempre: se os caminhos já apontam pra
 * dentro de baseDir (caso normal do setup manual do desenvolvedor, com
 * caminhos absolutos configurados à mão), não mexe em nada. Também
 * preenche ffmpeg_path/ffprobe_path com o ffmpeg embutido em
 * baseDir/ffmpeg/ quando o campo está vazio (o instalador embute os
 * binários ali -- ver installer-assets/).
 *
 * Não usa resolução relativa via cwd de propósito: o .exe do Python, quando
 * "congelado", faz os.chdir() pra pasta DELE MESMO (dist/TikTokShopVideoGeneratorCLI/),
 * não pra raiz do app -- caminho relativo no config.json resolveria no
 * lugar errado. Caminho absoluto elimina essa armadilha de vez.
 */
function ensurePortableConfig(baseDir) {
  let data;
  try {
    data = readConfig(baseDir);
  } catch {
    return; // sem config.json ainda -- nada a corrigir aqui
  }

  let mudou = false;
  const nomesPadrao = {
    avatars: "avatar",
    backgrounds: "background",
    used_images: "imagem-utilizada",
    output: "output",
  };

  data.directories = data.directories || {};
  for (const [chave, nomePadrao] of Object.entries(nomesPadrao)) {
    const atual = data.directories[chave];
    if (!isAnchoredIn(atual, baseDir)) {
      const nomeRelativo = atual && !path.isAbsolute(atual) ? atual : nomePadrao;
      data.directories[chave] = path.join(baseDir, nomeRelativo);
      mudou = true;
    }
  }

  data.database = data.database || {};
  if (!isAnchoredIn(data.database.path, baseDir)) {
    const nomeRelativo = data.database.path && !path.isAbsolute(data.database.path) ? data.database.path : "history.db";
    data.database.path = path.join(baseDir, nomeRelativo);
    mudou = true;
  }

  const ffmpegEmbutido = path.join(baseDir, "ffmpeg", "ffmpeg.exe");
  const ffprobeEmbutido = path.join(baseDir, "ffmpeg", "ffprobe.exe");
  if (!data.ffmpeg_path && fs.existsSync(ffmpegEmbutido)) {
    data.ffmpeg_path = ffmpegEmbutido;
    mudou = true;
  }
  if (!data.ffprobe_path && fs.existsSync(ffprobeEmbutido)) {
    data.ffprobe_path = ffprobeEmbutido;
    mudou = true;
  }

  if (mudou) writeConfig(baseDir, data);
}

module.exports = {
  VIDEO_EXTENSIONS,
  IMAGE_EXTENSIONS,
  listAvatarsByFolder,
  setAvatarVideo,
  removeAvatarVideo,
  getBackgroundImages,
  getImagesByAvatarFolder,
  copyFilesInto,
  moveToDiscarded,
  hasApiKey,
  listApiKeys,
  addApiKey,
  removeApiKey,
  maskApiKey,
  readConfig,
  writeConfig,
  ensurePortableConfig,
  getOutputFolder,
  setOutputFolder,
  basenames,
};
