import threading
import logging
import tkinter.filedialog as filedialog
import customtkinter as ctk
from app import HotmartDownloader
import app as app_module

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

DEFAULT_LOGIN_URL = "https://app-vlc.hotmart.com/login"
DEFAULT_NEXT_XPATH = (
    "/html/body/div[1]/div/hc-structure-2_11_18"
    "/hc-structure-content-2_11_18/hc-container-2_11_18"
    "/main/div/div/div/section/div[1]/div[2]/hc-tooltip-2_11_18/a"
)


class TextHandler(logging.Handler):
    def __init__(self, textbox):
        super().__init__()
        self.textbox = textbox

    def emit(self, record):
        msg = self.format(record) + "\n"
        self.textbox.after(0, self._append, msg)

    def _append(self, msg):
        self.textbox.configure(state="normal")
        self.textbox.insert("end", msg)
        self.textbox.see("end")
        self.textbox.configure(state="disabled")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Hotmart Video Downloader")
        self.geometry("750x960")
        self.resizable(True, True)
        self.grid_columnconfigure(1, weight=1)
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        row = 0
        ctk.CTkLabel(self, text="URL de Login:").grid(row=row, column=0, sticky="e", **pad)
        self.login_url = ctk.CTkEntry(self, placeholder_text=DEFAULT_LOGIN_URL)
        self.login_url.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

        row += 1
        ctk.CTkLabel(self, text="URL 1ª Aula:").grid(row=row, column=0, sticky="e", **pad)
        self.first_url = ctk.CTkEntry(self, placeholder_text="https://...")
        self.first_url.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

        row += 1
        ctk.CTkLabel(self, text="Pasta destino:").grid(row=row, column=0, sticky="e", **pad)
        self.download_dir = ctk.CTkEntry(self, placeholder_text="aulas")
        self.download_dir.grid(row=row, column=1, sticky="ew", **pad)
        ctk.CTkButton(self, text="...", width=40, command=self._browse_dir).grid(row=row, column=2, padx=(0, 12), pady=6)

        row += 1
        ctk.CTkLabel(self, text="XPath próx. aula:").grid(row=row, column=0, sticky="e", **pad)
        self.next_xpath = ctk.CTkEntry(self, placeholder_text=DEFAULT_NEXT_XPATH)
        self.next_xpath.insert(0, DEFAULT_NEXT_XPATH)
        self.next_xpath.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

        row += 1
        ctk.CTkLabel(self, text="XPath botão play:").grid(row=row, column=0, sticky="e", **pad)
        self.play_xpath = ctk.CTkEntry(self, placeholder_text="(opcional — ex: /html/body/div[2]/div[1]/button)")
        self.play_xpath.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

        row += 1
        ctk.CTkLabel(self, text="XPath campo usuário:").grid(row=row, column=0, sticky="e", **pad)
        self.login_user_xpath = ctk.CTkEntry(self, placeholder_text="(opcional — XPath do input de e-mail/usuário)")
        self.login_user_xpath.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

        row += 1
        ctk.CTkLabel(self, text="XPath campo senha:").grid(row=row, column=0, sticky="e", **pad)
        self.login_pass_xpath = ctk.CTkEntry(self, placeholder_text="(opcional — XPath do input de senha)")
        self.login_pass_xpath.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

        row += 1
        ctk.CTkLabel(self, text="Usuário / E-mail:").grid(row=row, column=0, sticky="e", **pad)
        self.username = ctk.CTkEntry(self, placeholder_text="seu@email.com")
        self.username.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

        row += 1
        ctk.CTkLabel(self, text="Senha:").grid(row=row, column=0, sticky="e", **pad)
        self.password = ctk.CTkEntry(self, placeholder_text="••••••••", show="*")
        self.password.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

        row += 1
        ctk.CTkLabel(self, text="Domínio do vídeo:").grid(row=row, column=0, sticky="e", **pad)
        self.video_domain = ctk.CTkEntry(self, placeholder_text="(vazio = aceita qualquer .m3u8 — ex: pandavideo.com)")
        self.video_domain.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

        row += 1
        ctk.CTkLabel(self, text="Certificado corp.:").grid(row=row, column=0, sticky="e", **pad)
        self.ca_cert = ctk.CTkEntry(self, placeholder_text="(opcional)")
        self.ca_cert.grid(row=row, column=1, sticky="ew", **pad)
        ctk.CTkButton(self, text="...", width=40, command=self._browse_cert).grid(row=row, column=2, padx=(0, 12), pady=6)

        row += 1
        self.ssl_var = ctk.IntVar()
        ctk.CTkCheckBox(self, text="Ignorar verificação SSL", variable=self.ssl_var).grid(
            row=row, column=1, sticky="w", **pad
        )

        row += 1
        ctk.CTkLabel(self, text="ffmpeg:").grid(row=row, column=0, sticky="e", **pad)
        self.ffmpeg_path = ctk.CTkEntry(self, placeholder_text="(detectar automaticamente)")
        self.ffmpeg_path.grid(row=row, column=1, sticky="ew", **pad)
        ctk.CTkButton(self, text="...", width=40, command=self._browse_ffmpeg).grid(row=row, column=2, padx=(0, 12), pady=6)

        row += 1
        self.start_btn = ctk.CTkButton(
            self, text="▶  INICIAR DOWNLOAD", height=42, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start,
        )
        self.start_btn.grid(row=row, column=0, columnspan=3, sticky="ew", padx=12, pady=14)

        row += 1
        self.log_box = ctk.CTkTextbox(self, height=220, font=("Consolas", 11), state="disabled")
        self.log_box.grid(row=row, column=0, columnspan=3, sticky="nsew", padx=12, pady=(0, 12))
        self.grid_rowconfigure(row, weight=1)

    def _browse_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.download_dir.delete(0, "end")
            self.download_dir.insert(0, path)

    def _browse_cert(self):
        path = filedialog.askopenfilename(
            filetypes=[("Certificados", "*.pem *.crt *.cer"), ("Todos", "*.*")]
        )
        if path:
            self.ca_cert.delete(0, "end")
            self.ca_cert.insert(0, path)

    def _browse_ffmpeg(self):
        path = filedialog.askopenfilename(filetypes=[("Executáveis", "*.exe"), ("Todos", "*.*")])
        if path:
            self.ffmpeg_path.delete(0, "end")
            self.ffmpeg_path.insert(0, path)

    def _log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _start(self):
        login_url        = self.login_url.get().strip() or DEFAULT_LOGIN_URL
        first_url        = self.first_url.get().strip()
        dl_dir           = self.download_dir.get().strip() or "aulas"
        next_xpath       = self.next_xpath.get().strip() or DEFAULT_NEXT_XPATH
        play_xpath       = self.play_xpath.get().strip() or None
        login_user_xpath = self.login_user_xpath.get().strip() or None
        login_pass_xpath = self.login_pass_xpath.get().strip() or None
        username         = self.username.get().strip() or None
        password         = self.password.get().strip() or None
        video_domain     = self.video_domain.get().strip()
        ca_cert          = self.ca_cert.get().strip() or None
        ssl_ignore       = bool(self.ssl_var.get())
        ffmpeg           = self.ffmpeg_path.get().strip() or None

        if not first_url:
            self._log("❌ URL da primeira aula é obrigatória!")
            return

        self.start_btn.configure(state="disabled", text="⏳ Executando...")

        handler = TextHandler(self.log_box)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        app_module.logger.addHandler(handler)

        def run():
            try:
                downloader = HotmartDownloader(
                    download_dir=dl_dir,
                    headless=False,
                    quality="best",
                    ssl_ignore=ssl_ignore,
                    custom_ca_path=ca_cert,
                    ffmpeg_path=ffmpeg,
                    next_lesson_xpath=next_xpath,
                    play_xpath=play_xpath,
                    login_user_xpath=login_user_xpath,
                    login_pass_xpath=login_pass_xpath,
                    username=username,
                    password=password,
                    video_domain=video_domain,
                )
                downloader.process_course_auto(login_url, first_url)
            finally:
                app_module.logger.removeHandler(handler)
                self.start_btn.after(
                    0, lambda: self.start_btn.configure(state="normal", text="▶  INICIAR DOWNLOAD")
                )

        threading.Thread(target=run, daemon=True).start()


if __name__ == "__main__":
    app = App()
    app.mainloop()
