import os
import time
import json
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import logging
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class HotmartDownloader:
    def __init__(
        self,
        download_dir="aulas_hotmart",
        headless=False,
        quality="best",
        ssl_ignore=False,
        custom_ca_path=None,
        ffmpeg_path=None,
        next_lesson_xpath=None,
        play_xpath=None,
        login_user_xpath=None,
        login_pass_xpath=None,
        username=None,
        password=None,
        video_domain="",
    ):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)
        self.driver = None
        self.headless = headless
        self.quality = quality
        self.screenshots_dir = self.download_dir / "screenshots"
        self.screenshots_dir.mkdir(exist_ok=True)
        self.ssl_ignore = ssl_ignore
        self.custom_ca_path = custom_ca_path if custom_ca_path else None
        self.ffmpeg_path = self._resolve_ffmpeg_path(ffmpeg_path)
        self.next_lesson_xpath = next_lesson_xpath
        self.play_xpath = play_xpath
        self.login_user_xpath = login_user_xpath
        self.login_pass_xpath = login_pass_xpath
        self.username = username
        self.password = password
        self.video_domain = video_domain

    def _resolve_ffmpeg_path(self, provided_path):
        if provided_path:
            ffmpeg_candidate = Path(provided_path).expanduser().resolve()
            if ffmpeg_candidate.exists():
                return ffmpeg_candidate
            logger.warning("⚠ ffmpeg informado não encontrado, tentando detectar automaticamente.")

        script_dir = Path(__file__).resolve().parent
        default_ffmpeg = script_dir / "ffmpeg.exe"
        if default_ffmpeg.exists():
            logger.info("✓ ffmpeg detectado automaticamente na pasta do projeto.")
            return default_ffmpeg

        return None

    def setup_driver(self):
        chrome_options = Options()

        if self.headless:
            chrome_options.add_argument("--headless")

        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--allow-running-insecure-content")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--start-maximized")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

        chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL', 'browser': 'ALL'})

        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            logger.info("✓ Chrome driver iniciado")
            return True
        except Exception as e:
            logger.error(f"✗ Erro ao iniciar Chrome: {str(e)}")
            return False

    def save_screenshot(self, name):
        try:
            screenshot_path = self.screenshots_dir / f"{name}_{int(time.time())}.png"
            self.driver.save_screenshot(str(screenshot_path))
        except:
            pass

    def manual_login(self, login_url, timeout=300):
        logger.info("=" * 70)
        logger.info("LOGIN MANUAL - Você tem 5 minutos")
        logger.info("=" * 70)

        self.driver.get(login_url)
        self.save_screenshot("01_login")

        if all([self.username, self.password, self.login_user_xpath, self.login_pass_xpath]):
            try:
                from selenium.webdriver.common.keys import Keys
                user_field = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, self.login_user_xpath))
                )
                user_field.clear()
                user_field.send_keys(self.username)
                pass_field = self.driver.find_element(By.XPATH, self.login_pass_xpath)
                pass_field.clear()
                pass_field.send_keys(self.password)
                pass_field.send_keys(Keys.RETURN)
                logger.info("✓ Credenciais preenchidas automaticamente")
            except Exception as e:
                logger.warning(f"⚠ Auto-preenchimento falhou, faça login manualmente: {e}")

        start_time = time.time()

        while time.time() - start_time < timeout:
            current_url = self.driver.current_url

            if "login" not in current_url.lower() or "club" in current_url.lower() or "content" in current_url.lower():
                logger.info("✓ Login detectado!")
                self.save_screenshot("02_logged")
                time.sleep(3)
                self.driver.minimize_window()
                return True

            time.sleep(1)

        logger.error("✗ Timeout no login")
        return False

    def sanitize_filename(self, filename):
        filename = re.sub(r'[<>:"/\\|?*]', '', filename)
        filename = re.sub(r'\s+', ' ', filename)
        filename = filename.strip('. ')
        if len(filename) > 200:
            filename = filename[:200]
        return filename

    def get_lesson_title(self):
        try:
            title_element = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, '//*[@id="base-structure"]/div/div/div[1]/section/div[2]/h1'))
            )
            title = title_element.text.strip()

            if title:
                logger.info(f"✓ Título: {title}")
                return self.sanitize_filename(title)
        except Exception as e:
            logger.warning(f"Título não encontrado")

        return None

    def extract_video_urls_from_logs(self):
        video_urls = []

        try:
            logs = self.driver.get_log('performance')

            for log in logs:
                try:
                    message = json.loads(log['message'])['message']

                    if message['method'] == 'Network.responseReceived':
                        response = message['params']['response']
                        url = response['url']

                        # Se video_domain estiver vazio, aceita qualquer domínio
                        domain_ok = (not self.video_domain) or (self.video_domain in url)
                        if all([
                            domain_ok,
                            '.m3u8' in url,
                            'thumbnail' not in url.lower(),
                            'embed' not in url.lower(),
                            '.jpeg' not in url.lower(),
                            '.jpg' not in url.lower(),
                            '.png' not in url.lower(),
                        ]):
                            if url not in video_urls:
                                video_urls.append(url)
                                logger.debug(f"✓ Vídeo: {url[:60]}...")

                except (KeyError, json.JSONDecodeError):
                    continue

        except Exception as e:
            logger.error(f"Erro ao extrair URLs: {str(e)}")

        return video_urls

    def click_play_button(self):
        play_selectors = [
            (By.XPATH, '/html/body/div[2]/div[1]/button'),
            (By.XPATH, '/html/body/div[2]/div[1]/div[4]/div[2]'),
            (By.CLASS_NAME, "vjs-big-play-button"),
            (By.CSS_SELECTOR, "button[aria-label*='play' i]"),
            (By.CSS_SELECTOR, ".video-js button"),
            (By.XPATH, "//button[contains(@class, 'play')]"),
            (By.XPATH, "//button[contains(@class, 'vjs')]"),
        ]
        if self.play_xpath:
            play_selectors.insert(0, (By.XPATH, self.play_xpath))

        for by, selector in play_selectors:
            try:
                play_button = WebDriverWait(self.driver, 1.5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                self.driver.execute_script("arguments[0].scrollIntoView(true);", play_button)
                time.sleep(1)
                play_button.click()
                logger.info("▶ Play clicado")
                return True
            except:
                continue

        # Tenta clicar no player diretamente
        try:
            video_player = self.driver.find_element(By.CSS_SELECTOR, ".video-js, video")
            self.driver.execute_script("arguments[0].scrollIntoView(true);", video_player)
            time.sleep(1)
            video_player.click()
            logger.info("▶ Player clicado")
            return True
        except:
            pass

        # Procura dentro de iframes
        try:
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            logger.info(f"🔍 Procurando play em {len(iframes)} iframe(s)...")
            for iframe in iframes:
                try:
                    self.driver.switch_to.frame(iframe)
                    for by, selector in play_selectors:
                        try:
                            play_button = WebDriverWait(self.driver, 1.5).until(
                                EC.element_to_be_clickable((by, selector))
                            )
                            self.driver.execute_script("arguments[0].scrollIntoView(true);", play_button)
                            time.sleep(0.5)
                            play_button.click()
                            logger.info("▶ Play clicado (iframe)")
                            self.driver.switch_to.default_content()
                            return True
                        except:
                            continue
                    self.driver.switch_to.default_content()
                except:
                    self.driver.switch_to.default_content()
                    continue
        except Exception:
            try:
                self.driver.switch_to.default_content()
            except:
                pass

        logger.warning("⚠ Não foi possível clicar no play")
        return False

    def navigate_to_lesson(self, lesson_url=None):
        if lesson_url:
            logger.info(f"Acessando aula...")
            self.driver.get(lesson_url)
            time.sleep(3)

        self.save_screenshot("lesson_page")

        # Se já tem iframe Panda Vídeo não precisa clicar no play
        if not self.extract_panda_video_url():
            self.click_play_button()
            time.sleep(4)
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)

    def download_with_ytdlp(self, video_url, filename):
        try:
            import yt_dlp

            output_path = self.download_dir / filename

            if output_path.exists():
                logger.info(f"✓ Já existe: {filename}")
                return True

            logger.info(f"📥 Baixando: {filename}")

            ydl_opts = {
                'outtmpl': str(output_path.with_suffix('').with_suffix('.%(ext)s')),
                'format': self.quality,
                'quiet': False,
                'no_warnings': False,
                'retries': 10,
                'fragment_retries': 10,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': self.driver.current_url
                },
                'concurrent_fragment_downloads': 4,
                'merge_output_format': 'mp4',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }],
            }

            if self.ffmpeg_path:
                ydl_opts['ffmpeg_location'] = str(self.ffmpeg_path)

            if self.ssl_ignore:
                ydl_opts['nocheckcertificate'] = True
            elif self.custom_ca_path:
                ydl_opts['ca_certs'] = self.custom_ca_path

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)

                if info:
                    duration = info.get('duration', 0)
                    filesize = info.get('filesize', 0) or info.get('filesize_approx', 0)

                    if duration:
                        logger.info(f"✓ Duração: {duration // 60}m {duration % 60}s")
                    if filesize:
                        logger.info(f"✓ Tamanho: {filesize / (1024*1024):.1f} MB")

            logger.info(f"✓ Download concluído!")
            return True

        except Exception as e:
            logger.error(f"✗ Erro: {str(e)}")
            return False

    def extract_panda_video_url(self):
        """Constrói a URL HLS do Panda Vídeo a partir do src do iframe."""
        try:
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            for iframe in iframes:
                src = iframe.get_attribute("src") or ""
                if "pandavideo" not in src:
                    continue
                vid_match = re.search(r'[?&]v=([a-f0-9-]+)', src)
                dom_match = re.search(r'https://(player-vz-[^/]+)', src)
                if vid_match and dom_match:
                    video_id = vid_match.group(1)
                    cdn_host = dom_match.group(1).replace("player-vz-", "b-vz-")
                    hls_url  = f"https://{cdn_host}/{video_id}/playlist.m3u8"
                    logger.info(f"✓ Panda Vídeo HLS: {hls_url}")
                    return hls_url
        except Exception as e:
            logger.warning(f"⚠ Erro ao extrair URL Panda Vídeo: {e}")
        return None

    def extract_youtube_url(self):
        """Extrai a URL do YouTube a partir do src de um iframe embed."""
        try:
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            for iframe in iframes:
                src = iframe.get_attribute("src") or ""
                if "youtube" not in src and "youtu.be" not in src:
                    continue
                match = re.search(r'(?:embed/|youtu\.be/)([A-Za-z0-9_-]{11})', src)
                if match:
                    video_id = match.group(1)
                    yt_url = f"https://www.youtube.com/watch?v={video_id}"
                    logger.info(f"✓ YouTube detectado: {yt_url}")
                    return yt_url
        except Exception as e:
            logger.warning(f"⚠ Erro ao extrair URL YouTube: {e}")
        return None

    def process_single_lesson(self, lesson_number, navigate_url=None):
        logger.info(f"\n{'='*70}")
        logger.info(f"AULA {lesson_number}")
        logger.info(f"{'='*70}")

        self.navigate_to_lesson(navigate_url)

        lesson_title = self.get_lesson_title()

        # Detecta tipo de player: Panda Vídeo → YouTube → logs HLS
        panda_url = self.extract_panda_video_url()
        yt_url    = self.extract_youtube_url()
        if panda_url:
            video_urls = [panda_url]
        elif yt_url:
            video_urls = [yt_url]
        else:
            video_urls = self.extract_video_urls_from_logs()

        logger.info(f"URLs de vídeo encontradas: {len(video_urls)}")

        if not video_urls:
            logger.warning("✗ Nenhum vídeo encontrado")
            logger.info("Tentando clicar no play novamente...")
            self.click_play_button()
            time.sleep(10)
            panda_url = self.extract_panda_video_url()
            yt_url    = self.extract_youtube_url()
            if panda_url:
                video_urls = [panda_url]
            elif yt_url:
                video_urls = [yt_url]
            else:
                video_urls = self.extract_video_urls_from_logs()
            logger.info(f"Nova tentativa: {len(video_urls)} URLs")

        if not video_urls:
            logger.error("✗ Falha ao encontrar vídeo")
            return False

        # Prioriza master.m3u8
        video_urls.sort(key=lambda url: 0 if 'master' in url else 1)

        if lesson_title:
            filename = f"{lesson_number:03d} - {lesson_title}.mp4"
        else:
            filename = f"aula_{lesson_number:03d}.mp4"

        for idx, video_url in enumerate(video_urls[:2]):
            logger.info(f"Tentativa {idx+1}")

            if self.download_with_ytdlp(video_url, filename):
                return True

            time.sleep(2)

        logger.error(f"✗ Falha na aula {lesson_number}")
        return False

    def click_next_lesson_button(self):
        next_selectors = [
            (By.CSS_SELECTOR, '.action-btn.ms-2.a-next.px-3'),
        ]
        if self.next_lesson_xpath:
            next_selectors.append((By.XPATH, self.next_lesson_xpath))
        next_selectors += [
            (By.XPATH, '/html/body/div[1]/div/hc-structure-2_11_18/hc-structure-content-2_11_18/hc-container-2_11_18/main/div/div/div/section/div[1]/div[2]/hc-tooltip-2_11_18/a'),
            (By.XPATH, '//hc-tooltip-2_11_18/a'),
            (By.XPATH, '//hc-tooltip-2_11_18//a'),
        ]

        try:
            self.driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1)
        except Exception:
            pass

        for by, selector in next_selectors:
            try:
                next_button = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((by, selector))
                )
                self.driver.execute_script("arguments[0].scrollIntoView(true);", next_button)
                time.sleep(1)
                try:
                    next_button.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", next_button)
                logger.info("➡ Próxima aula clicada")
                time.sleep(8)
                return True
            except TimeoutException:
                logger.debug(f"Seletor não encontrado: {selector[:60]}")
                continue
            except Exception as e:
                logger.warning(f"⚠ Erro ao clicar ({selector[:40]}): {str(e)}")
                continue

        logger.info("✓ Não há mais aulas (botão não encontrado)")
        return False

    def process_course_auto(self, login_url, first_lesson_url):
        try:
            if not self.setup_driver():
                return

            if not self.manual_login(login_url, timeout=300):
                logger.error("Falha no login")
                return

            logger.info(f"\n{'='*70}")
            logger.info(f"MODO AUTOMÁTICO - Navegação entre aulas")
            logger.info(f"{'='*70}\n")

            successful = 0
            failed = 0
            failed_lessons = []
            lesson_number = 1

            if self.process_single_lesson(lesson_number, navigate_url=first_lesson_url):
                successful += 1
            else:
                failed += 1
                failed_lessons.append(lesson_number)

            while True:
                lesson_number += 1

                logger.info(f"\n⏸ Aguardando 3s...")
                time.sleep(3)

                if not self.click_next_lesson_button():
                    logger.info("✓ Todas as aulas foram processadas")
                    break

                if self.process_single_lesson(lesson_number, navigate_url=None):
                    successful += 1
                else:
                    failed += 1
                    failed_lessons.append(lesson_number)

                if lesson_number >= 200:
                    logger.warning("⚠ Limite de 200 aulas atingido")
                    break

            logger.info(f"\n{'='*70}")
            logger.info(f"RESUMO FINAL")
            logger.info(f"{'='*70}")
            logger.info(f"✓ Sucesso: {successful} aulas")
            logger.info(f"✗ Falhas: {failed} aulas")
            logger.info(f"📁 Pasta: {self.download_dir.absolute()}")

            if failed_lessons:
                logger.info(f"\n⚠ Aulas que falharam:")
                for lesson_num in failed_lessons:
                    logger.info(f"  - Aula {lesson_num}")

            logger.info(f"{'='*70}\n")

        except KeyboardInterrupt:
            logger.info("\n⚠ Interrompido pelo usuário")

        except Exception as e:
            logger.error(f"✗ Erro: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

        finally:
            if self.driver:
                self.driver.quit()


def main():
    print("""
    ╔════════════════════════════════════════════════════════════╗
    ║         HOTMART VIDEO DOWNLOADER v3.0                      ║
    ║         Navegação automática entre aulas                   ║
    ╚════════════════════════════════════════════════════════════╝
    """)

    LOGIN_URL = input("URL de login (Enter = padrão): ").strip()
    if not LOGIN_URL:
        LOGIN_URL = "https://app-vlc.hotmart.com/login"

    FIRST_LESSON_URL = input("URL da PRIMEIRA aula: ").strip()

    if not FIRST_LESSON_URL:
        print("❌ URL da primeira aula é obrigatória!")
        return

    DOWNLOAD_DIR = input("Pasta (Enter = 'aulas'): ").strip()
    if not DOWNLOAD_DIR:
        DOWNLOAD_DIR = "aulas"

    print(f"\n✓ Configurações:")
    print(f"  - Primeira aula: {FIRST_LESSON_URL[:60]}...")
    print(f"  - Destino: {DOWNLOAD_DIR}")
    print(f"  - Modo: Automático (navegação entre aulas)")
    print(f"\n{'='*60}\n")

    custom_ca = input("Caminho do certificado corporativo (Enter = nenhum): ").strip()
    if custom_ca:
        custom_ca = os.path.abspath(custom_ca)
        if not os.path.exists(custom_ca):
            print("⚠ Certificado não encontrado, ignorando caminho informado.")
            custom_ca = None

    ssl_ignore_input = input("Ignorar verificação SSL do yt-dlp? (s/N): ").strip().lower()
    ssl_ignore = ssl_ignore_input == "s"

    ffmpeg_custom = input("Caminho do ffmpeg (Enter = detectar): ").strip()
    if not ffmpeg_custom:
        ffmpeg_custom = None

    downloader = HotmartDownloader(
        download_dir=DOWNLOAD_DIR,
        headless=False,
        quality="best",
        ssl_ignore=ssl_ignore,
        custom_ca_path=custom_ca,
        ffmpeg_path=ffmpeg_custom,
    )
    downloader.process_course_auto(LOGIN_URL, FIRST_LESSON_URL)


if __name__ == "__main__":
    main()
