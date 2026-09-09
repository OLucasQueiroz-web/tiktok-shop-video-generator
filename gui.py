#!/usr/bin/env python3
"""
TikTok Shop Video Generator -- Interface gráfica (Tkinter)
=============================================================
Camada de interface por cima da mesma lógica de geração usada pelo CLI
(main.py). Não duplica nada de composição/renderização de vídeo -- só
chama as mesmas funções (run_pipeline, get_avatar_profiles, etc.).
"""

import sys
import os

# Em build "--windowed" (sem console) o PyInstaller deixa sys.stdout/stderr
# como None -- qualquer print()/logging que dependa deles quebraria o app
# ao abrir. Precisa vir antes de qualquer outro import que possa escrever
# no console (main.py, bibliotecas de terceiros, etc.).
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import json
import logging
import queue
import shutil
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

import main as core

VIDEO_EXTENSIONS = [("Vídeos", "*.mp4 *.mov *.avi *.mkv *.webm"), ("Todos os arquivos", "*.*")]
IMAGE_EXTENSIONS = [("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp *.tiff *.gif"), ("Todos os arquivos", "*.*")]


class QueueHandler(logging.Handler):
    """Handler de logging que empilha mensagens formatadas numa Queue thread-safe."""

    def __init__(self, log_queue: "queue.Queue[str]") -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        self.log_queue.put(self.format(record))


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.base_dir = core._resolve_base_dir()
        self.config_path = str(self.base_dir / "config.json")
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self._busy = False

        root.title("TikTok Shop -- Gerador de Vídeos")
        root.geometry("780x680")
        root.minsize(680, 560)

        self._setup_logging()
        self._build_widgets()
        self._refresh_profiles()
        self._refresh_pending_counts()
        self._refresh_output_label()
        self.root.after(100, self._poll_log_queue)

    # -- Logging -----------------------------------------------------------

    def _setup_logging(self) -> None:
        handler = QueueHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-7s] %(message)s", "%H:%M:%S"))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
        self.logger = logging.getLogger("gui")

    def _poll_log_queue(self) -> None:
        try:
            while True:
                line = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    # -- Construção da UI ----------------------------------------------------

    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 6}

        # -- Chave da API ------------------------------------------------
        key_frame = ttk.LabelFrame(self.root, text="Chave da API Groq")
        key_frame.pack(fill="x", **pad)
        self._build_key_section(key_frame)

        # -- Perfis e avatares --------------------------------------------
        avatar_frame = ttk.LabelFrame(self.root, text="Perfis e avatares")
        avatar_frame.pack(fill="x", **pad)
        self._build_avatar_section(avatar_frame)

        # -- Produtos -------------------------------------------------------
        product_frame = ttk.LabelFrame(self.root, text="Produtos")
        product_frame.pack(fill="x", **pad)
        self._build_product_section(product_frame)

        # -- Pasta de saída ---------------------------------------------
        output_frame = ttk.LabelFrame(self.root, text="Pasta de saída dos vídeos")
        output_frame.pack(fill="x", **pad)
        self._build_output_section(output_frame)

        # -- Ações ----------------------------------------------------------
        action_frame = ttk.LabelFrame(self.root, text="Ações")
        action_frame.pack(fill="x", **pad)
        self._build_action_section(action_frame)

        # -- Log ------------------------------------------------------------
        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = scrolledtext.ScrolledText(log_frame, state="disabled", height=14, wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_key_section(self, parent: ttk.LabelFrame) -> None:
        self.key_status_var = tk.StringVar()
        self.key_entry_var = tk.StringVar()

        ttk.Label(parent, textvariable=self.key_status_var).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.key_entry = ttk.Entry(parent, textvariable=self.key_entry_var, width=46, show="*")
        self.key_action_btn = ttk.Button(parent, text="Salvar chave")
        self.key_entry.grid(row=0, column=1, padx=4, pady=6)
        self.key_action_btn.grid(row=0, column=2, padx=4, pady=6)
        self._refresh_key_section()

    def _refresh_key_section(self) -> None:
        """Chave já configurada -> some o campo e mostra 'Trocar chave' (que
        reabre o campo pra digitar uma nova). Sem chave -> campo + 'Salvar
        chave' direto."""
        if core.has_api_key(self.base_dir):
            self.key_status_var.set("Chave configurada ✓")
            self.key_entry.grid_remove()
            self.key_action_btn.configure(text="Trocar chave", command=self._on_show_key_entry)
        else:
            self.key_status_var.set(
                "Nenhuma chave configurada -- cole abaixo (crie grátis em console.groq.com):"
            )
            self.key_entry.grid()
            self.key_action_btn.configure(text="Salvar chave", command=self._on_save_key)

    def _on_show_key_entry(self) -> None:
        self.key_status_var.set("Cole a nova chave abaixo:")
        self.key_entry.grid()
        self.key_action_btn.configure(text="Salvar chave", command=self._on_save_key)

    def _on_save_key(self) -> None:
        key = self.key_entry_var.get().strip()
        if not key:
            messagebox.showwarning("Chave vazia", "Cole a chave da API Groq antes de salvar.")
            return
        core.save_api_key(self.base_dir, key)
        self.key_entry_var.set("")
        self._refresh_key_section()
        self.logger.info("Chave da API Groq salva.")

    def _build_avatar_section(self, parent: ttk.LabelFrame) -> None:
        ttk.Label(parent, text="Perfil:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(parent, textvariable=self.profile_var, width=24)
        self.profile_combo.grid(row=0, column=1, padx=4, pady=6, sticky="w")
        ttk.Label(parent, text="(digite um nome novo pra criar outro perfil)").grid(
            row=0, column=2, sticky="w", padx=4
        )
        ttk.Button(parent, text="Adicionar vídeo(s) de avatar...", command=self._on_add_avatar).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6)
        )

    def _refresh_profiles(self) -> None:
        try:
            profiles = list(core.get_avatar_profiles(str(self.base_dir / "avatar")).keys())
        except FileNotFoundError:
            profiles = []
        self.profile_combo["values"] = profiles
        if profiles and not self.profile_var.get():
            self.profile_var.set(profiles[0])

    def _on_add_avatar(self) -> None:
        profile = self.profile_var.get().strip()
        if not profile:
            messagebox.showwarning("Perfil vazio", "Digite ou escolha um nome de perfil primeiro.")
            return
        files = filedialog.askopenfilenames(title="Escolher vídeo(s) de avatar", filetypes=VIDEO_EXTENSIONS)
        if not files:
            return
        dest_dir = self.base_dir / "avatar" / profile
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest_dir / Path(f).name)
        self.logger.info("Adicionado(s) %d vídeo(s) de avatar ao perfil '%s'.", len(files), profile)
        self._refresh_profiles()

    def _build_product_section(self, parent: ttk.LabelFrame) -> None:
        ttk.Button(parent, text="Adicionar produto (padrão)...", command=self._on_add_standard_product).grid(
            row=0, column=0, padx=8, pady=6, sticky="w"
        )
        ttk.Button(parent, text="Adicionar produto viral...", command=self._on_add_viral_product).grid(
            row=0, column=1, padx=8, pady=6, sticky="w"
        )
        ttk.Label(
            parent,
            text="Padrão = 1 vídeo por avatar.  Viral = combinação completa (produto × avatar × 5 estilos).",
            foreground="#555",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))

        # -- Listas de produtos pendentes, com opção de remover (produto --
        # adicionado por engano não precisa mais ser apagado manualmente na
        # pasta -- remover aqui move a imagem pra "_descartados/", não
        # apaga de vez).
        list_frame = ttk.Frame(parent)
        list_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 4))
        list_frame.columnconfigure(0, weight=1)
        list_frame.columnconfigure(1, weight=1)

        std_col = ttk.Frame(list_frame)
        std_col.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        ttk.Label(std_col, text="Pendentes (padrão):").pack(anchor="w")
        self.standard_listbox = tk.Listbox(std_col, height=5, selectmode="extended", exportselection=False)
        self.standard_listbox.pack(fill="both", expand=True)
        ttk.Button(std_col, text="Remover selecionado(s)", command=self._on_remove_standard).pack(
            anchor="w", pady=(4, 0)
        )

        viral_col = ttk.Frame(list_frame)
        viral_col.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        ttk.Label(viral_col, text="Pendentes (viral):").pack(anchor="w")
        self.viral_listbox = tk.Listbox(viral_col, height=5, selectmode="extended", exportselection=False)
        self.viral_listbox.pack(fill="both", expand=True)
        ttk.Button(viral_col, text="Remover selecionado(s)", command=self._on_remove_viral).pack(
            anchor="w", pady=(4, 0)
        )

        self.pending_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.pending_var).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 6)
        )

        self._standard_files: list[Path] = []
        self._viral_files: list[Path] = []

    def _list_products(self, dest_subdir: str) -> "list[Path]":
        try:
            return [Path(p) for p in core.get_background_images(str(self.base_dir / dest_subdir))]
        except Exception:
            return []

    def _refresh_pending_counts(self) -> None:
        self._standard_files = self._list_products("background")
        self._viral_files = self._list_products("produtos-virais")

        self.standard_listbox.delete(0, "end")
        for p in self._standard_files:
            self.standard_listbox.insert("end", p.name)

        self.viral_listbox.delete(0, "end")
        for p in self._viral_files:
            self.viral_listbox.insert("end", p.name)

        self.pending_var.set(
            f"Pendentes agora: {len(self._standard_files)} produto(s) padrão | "
            f"{len(self._viral_files)} produto(s) viral"
        )

    def _add_products(self, dest_subdir: str) -> None:
        files = filedialog.askopenfilenames(title="Escolher imagem(ns) de produto", filetypes=IMAGE_EXTENSIONS)
        if not files:
            return
        dest_dir = self.base_dir / dest_subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dest_dir / Path(f).name)
        self.logger.info("Adicionada(s) %d imagem(ns) em '%s'.", len(files), dest_subdir)
        self._refresh_pending_counts()

    def _on_add_standard_product(self) -> None:
        self._add_products("background")

    def _on_add_viral_product(self) -> None:
        self._add_products("produtos-virais")

    def _remove_products(self, listbox: tk.Listbox, files: "list[Path]", dest_subdir: str) -> None:
        selection = listbox.curselection()
        if not selection:
            messagebox.showinfo("Nada selecionado", "Selecione ao menos um produto na lista antes de remover.")
            return
        selected_files = [files[i] for i in selection]
        names = "\n".join(f.name for f in selected_files)
        if not messagebox.askyesno(
            "Remover produto(s)",
            f"Remover {len(selected_files)} produto(s) da fila de geração?\n\n{names}\n\n"
            f"As imagens vão para '{dest_subdir}/_descartados/' -- não são apagadas de vez.",
        ):
            return

        discard_dir = self.base_dir / dest_subdir / "_descartados"
        discard_dir.mkdir(parents=True, exist_ok=True)
        moved = 0
        for f in selected_files:
            try:
                dest = discard_dir / f.name
                if dest.exists():
                    dest = discard_dir / f"{f.stem}_{int(time.time())}{f.suffix}"
                shutil.move(str(f), str(dest))
                moved += 1
            except Exception as exc:
                self.logger.warning("Não foi possível remover '%s': %s", f.name, exc)

        self.logger.info(
            "Removido(s) %d produto(s) de '%s' (movidos para _descartados/).", moved, dest_subdir
        )
        self._refresh_pending_counts()

    def _on_remove_standard(self) -> None:
        self._remove_products(self.standard_listbox, self._standard_files, "background")

    def _on_remove_viral(self) -> None:
        self._remove_products(self.viral_listbox, self._viral_files, "produtos-virais")

    def _build_output_section(self, parent: ttk.LabelFrame) -> None:
        self.output_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.output_var).grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Button(parent, text="Escolher pasta...", command=self._on_choose_output).grid(
            row=0, column=1, padx=8, pady=6
        )

    def _refresh_output_label(self) -> None:
        try:
            data = self._read_config_dict()
            self.output_var.set(f"Pasta atual: {data['directories']['output']}")
        except Exception:
            self.output_var.set("Pasta atual: output")

    def _read_config_dict(self) -> dict:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_config_dict(self, data: dict) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _on_choose_output(self) -> None:
        folder = filedialog.askdirectory(title="Escolher pasta de saída dos vídeos")
        if not folder:
            return
        try:
            data = self._read_config_dict()
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível ler config.json: {exc}")
            return
        data["directories"]["output"] = folder
        self._write_config_dict(data)
        self._refresh_output_label()
        self.logger.info("Pasta de saída alterada para: %s", folder)

    def _build_action_section(self, parent: ttk.LabelFrame) -> None:
        self.dry_run_btn = ttk.Button(parent, text="Testar (dry-run)", command=lambda: self._start_pipeline(True))
        self.dry_run_btn.grid(row=0, column=0, padx=8, pady=8)

        self.generate_btn = ttk.Button(parent, text="Gerar vídeos", command=lambda: self._start_pipeline(False))
        self.generate_btn.grid(row=0, column=1, padx=8, pady=8)

        self.history_btn = ttk.Button(parent, text="Ver histórico", command=self._open_history_window)
        self.history_btn.grid(row=0, column=2, padx=8, pady=8)

        self.progress = ttk.Progressbar(parent, mode="indeterminate", length=200)
        self.progress.grid(row=0, column=3, padx=8, pady=8, sticky="ew")
        parent.columnconfigure(3, weight=1)

    # -- Pipeline (dry-run / gerar) em thread separada -----------------------

    def _start_pipeline(self, dry_run: bool) -> None:
        if self._busy:
            messagebox.showinfo("Aguarde", "Já tem uma operação em andamento.")
            return
        self._busy = True
        self.dry_run_btn.configure(state="disabled")
        self.generate_btn.configure(state="disabled")
        self.progress.start(12)
        threading.Thread(target=self._run_pipeline_worker, args=(dry_run,), daemon=True).start()

    def _run_pipeline_worker(self, dry_run: bool) -> None:
        try:
            success, failed = core.run_pipeline(self.config_path, dry_run, self.logger)
            if not dry_run:
                self.logger.info("Concluído -- OK: %d | Falhas: %d", success, failed)
        except Exception as exc:
            self.logger.error("Falhou: %s", exc)
            self.root.after(0, lambda: messagebox.showerror("Erro", str(exc)))
        finally:
            self.root.after(0, self._on_pipeline_done)

    def _on_pipeline_done(self) -> None:
        self._busy = False
        self.progress.stop()
        self.dry_run_btn.configure(state="normal")
        self.generate_btn.configure(state="normal")
        self._refresh_pending_counts()
        self._refresh_profiles()

    # -- Histórico ------------------------------------------------------------

    def _open_history_window(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Histórico de vídeos gerados")
        win.geometry("820x420")

        columns = ("data", "tipo", "perfil", "avatar", "estilo", "status", "produto")
        tree = ttk.Treeview(win, columns=columns, show="headings")
        for col, width in zip(columns, (70, 70, 110, 90, 130, 70, 260)):
            tree.heading(col, text=col.capitalize())
            tree.column(col, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=8, pady=8)

        summary_var = tk.StringVar()
        ttk.Label(win, textvariable=summary_var).pack(anchor="w", padx=8, pady=(0, 8))

        try:
            data = self._read_config_dict()
            db_path = str(self.base_dir / data.get("database", {}).get("path", "history.db"))
            core.init_db(db_path)  # garante a tabela antes de consultar (banco pode nunca ter sido usado)
            rows = core.fetch_history(db_path, limit=200)
            counts = core.summary_counts(db_path)
        except Exception as exc:
            summary_var.set(f"Erro ao ler histórico: {exc}")
            return

        for row in rows:
            tree.insert("", "end", values=(
                row["day_label"], row["video_type"], row["profile"], row["avatar_name"],
                row["animation_style"] or "-", row["status"], row["product_name"] or "-",
            ))
        summary_var.set(f"Total: {counts['total']}  |  por tipo: {counts['by_type']}  |  por status: {counts['by_status']}")


def main() -> None:
    # Garante que os caminhos relativos do config.json (avatar/, background/,
    # output/, history.db...) resolvem a partir da pasta do .exe/gui.py,
    # independente de onde o processo foi lançado (Explorer, atalho, etc.).
    os.chdir(core._resolve_base_dir())
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
