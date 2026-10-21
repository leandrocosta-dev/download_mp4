"""
Downloader de cursos da Alura.

Uso:
    python alura.py <url_de_qualquer_atividade_do_curso> [pasta_destino]

Fluxo: usa o Chrome aberto (você faz login uma vez), começa na primeira aula do curso e
segue o botão "Avançar" de aula em aula (passando de módulo e de curso), baixando cada vídeo em:
    <destino>/<curso>/<NN - Aula>.mp4
"""
import itertools
import json
import logging
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BASE = "https://cursos.alura.com.br"

# Lê seções e atividades da página do curso, na ordem em que aparecem.
JS_STRUCTURE = """
const out = [];
const links = [...document.querySelectorAll('a[href*="/task/"]')];
const seen = new Set();
for (const a of links) {
  const m = a.getAttribute('href').match(/\\/(?:course|classpage)\\/([^/]+)\\/task\\/(\\d+)/);
  if (!m || seen.has(m[2])) continue;
  seen.add(m[2]);
  const sec = a.closest('.courseSectionList-section, li.course-list__item, section, li');
  let secTitle = '';
  if (sec) {
    const h = sec.querySelector('.courseSectionList-sectionTitle, h2, h3, .course-list__section-title');
    secTitle = h ? h.textContent : '';
  }
  const title = (a.querySelector('.courseSectionList-sectionTitle, .task-menu-nav-item-title, span') || a).textContent;
  out.push({slug: m[1], id: m[2], section: secTitle.trim(), title: title.trim(), href: a.href});
}
return out;
"""

# Busca o JSON de vídeo da atividade usando a sessão logada do navegador.
JS_FETCH_VIDEO = """
const done = arguments[arguments.length - 1];
fetch(arguments[0], {credentials: 'include'})
  .then(r => r.ok ? r.json() : null)
  .then(j => done(j))
  .catch(() => done(null));
"""


def clean(name, fallback="sem titulo"):
    name = re.sub(r"\s+", " ", re.sub(r'[<>:"/\\|?*\n\r\t]', " ", name or "")).strip(". ")
    return (name or fallback)[:120]


DEBUG_PORT = 9222
PROFILE_DIR = Path(__file__).resolve().parent / ".chrome_profile_alura"
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def port_open():
    try:
        socket.create_connection(("127.0.0.1", DEBUG_PORT), 0.5).close()
        return True
    except OSError:
        return False


def setup_driver():
    """Conecta a um Chrome que fica aberto entre execuções (o login da Alura é cookie de sessão,
    some se o Chrome fechar). Se não houver um aberto, abre um com perfil próprio."""
    if not port_open():
        chrome = next((p for p in CHROME_PATHS if Path(p).exists()), None)
        if not chrome:
            raise RuntimeError("Chrome não encontrado")
        subprocess.Popen(
            [chrome, f"--remote-debugging-port={DEBUG_PORT}", f"--user-data-dir={PROFILE_DIR}",
             "--start-maximized", f"{BASE}/loginForm"],
            creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        )
        for _ in range(30):
            if port_open():
                break
            time.sleep(0.5)
    opts = Options()
    opts.debugger_address = f"127.0.0.1:{DEBUG_PORT}"
    driver = webdriver.Chrome(options=opts)
    driver.set_script_timeout(30)
    return driver


def is_login(url):
    """True só para as telas de login da Alura (o nome de um curso pode conter "login")."""
    from urllib.parse import urlparse
    return bool(re.match(r"^/(app/)?login", urlparse(url).path.lower()))


def wait_login(driver, timeout=300):
    driver.get(f"{BASE}/loginForm")
    logger.info("Faça login no Chrome que abriu (você tem 5 minutos)...")
    end = time.time() + timeout
    while time.time() < end:
        url = driver.current_url
        if "alura.com.br" in url and not is_login(url):
            time.sleep(2)
            return True
        time.sleep(1)
    return False


def wait_relogin(driver, timeout=600):
    """Espera o usuário logar de novo sem navegar para outra página."""
    from urllib.parse import parse_qs, urlparse
    end = time.time() + timeout
    last_try = time.time()
    while time.time() < end:
        url = driver.current_url
        if not is_login(url):
            time.sleep(2)
            return True
        # o login feito em outra janela vale para todas: recarrega a página de destino para sair da tela de login
        if time.time() - last_try > 5:
            last_try = time.time()
            back = parse_qs(urlparse(url).query).get("urlAfterLogin", [None])[0]
            if back:
                driver.get(back)
        time.sleep(1)
    return False


def course_slug(url):
    m = re.search(r"/(?:classpage|course)/([^/?#]+)", url)
    return m.group(1) if m else None


def read_structure(driver, slug, start_url):
    best = []
    for url in (start_url, f"{BASE}/course/{slug}"):
        driver.get(url)
        time.sleep(5)
        items = [i for i in driver.execute_script(JS_STRUCTURE) if i["slug"] == slug]
        logger.info(f"{len(items)} atividades em {url}")
        if len(items) > len(best):
            best = items
        # a página /course/ só mostra a primeira aula; a lista completa fica na classpage
        if len(items) == 1 and "/classpage/" not in url:
            first = f"{BASE}/classpage/{slug}/task/{items[0]['id']}"
            driver.get(first)
            time.sleep(5)
            items = [i for i in driver.execute_script(JS_STRUCTURE) if i["slug"] == slug]
            logger.info(f"{len(items)} atividades em {first}")
            if len(items) > len(best):
                best = items
    return best


def video_sources(driver, slug, task_id):
    """Retorna lista de URLs de vídeo (melhor primeiro) ou [] se a atividade não tiver vídeo."""
    data = driver.execute_async_script(JS_FETCH_VIDEO, f"{BASE}/course/{slug}/task/{task_id}/video")
    urls = []
    if isinstance(data, list):
        # prefere maior qualidade; aceita campos "link"/"mp4"/"hls"
        order = {"hd": 0, "full-hd": 0, "sd": 1, "low": 2}
        data = sorted(data, key=lambda d: order.get(str(d.get("quality", "")).lower(), 3))
        for d in data:
            for k in ("mp4", "link", "hls", "url"):
                if d.get(k):
                    urls.append(d[k])
    elif isinstance(data, dict):
        for k in ("mp4", "link", "hls", "url"):
            if data.get(k):
                urls.append(data[k])
    return urls


def dom_video_sources(driver, href):
    """Fallback: abre a aula e lê o src da tag <video> (alguns cursos não têm o endpoint /video)."""
    driver.get(href)
    for _ in range(10):
        time.sleep(1)
        srcs = driver.execute_script(
            "return [...document.querySelectorAll('video, video source')]"
            ".map(e => e.currentSrc || e.src).filter(s => s && s.startsWith('http'))"
        )
        if srcs:
            return list(dict.fromkeys(srcs))
    return []


def download_direct(url, target):
    """Baixa um mp4 direto (sem yt-dlp), confere o tamanho e só então renomeia."""
    import urllib.request

    part = target.with_name(target.name + ".part")
    req = urllib.request.Request(url, headers={"Referer": BASE + "/", "User-Agent": "Mozilla/5.0"})
    done = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
    if (total and done != total) or done == 0:
        part.unlink(missing_ok=True)
        raise IOError(f"download incompleto ({done}/{total} bytes)")
    part.replace(target)


def download(url, target, ffmpeg):
    if ".m3u8" not in url:
        return download_direct(url, target)
    import yt_dlp

    opts = {
        "outtmpl": str(target.with_suffix("")) + ".%(ext)s",
        "format": "best",
        "merge_output_format": "mp4",
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "http_headers": {"Referer": BASE + "/", "User-Agent": "Mozilla/5.0"},
    }
    if ffmpeg:
        opts["ffmpeg_location"] = str(ffmpeg)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


TASK_RE = re.compile(r"/(?:classpage|course)/([^/?#]+)/task/(\d+)")

# título da aula: "<seção ou curso> > <aula> | Alura ..." -> "<aula>"
JS_TITLE = """
const t = document.title.split(' | ')[0];
const i = t.indexOf('>');
return (i >= 0 ? t.slice(i + 1) : t).trim();
"""

JS_CLICK_NEXT = """
const b = [...document.querySelectorAll('button, a')]
  .find(e => /^\\s*avan[cç]ar\\s*$/i.test(e.textContent || ''));
if (!b) return false;
b.click();
return true;
"""


def first_task_url(driver, slug):
    driver.get(f"{BASE}/course/{slug}")
    time.sleep(4)
    items = [i for i in driver.execute_script(JS_STRUCTURE) if i["slug"] == slug]
    return items[0]["href"] if items else None


JS_VIDEO_HINT = """
return !!document.querySelector(
  'video, .video-js, iframe[src*="player"], iframe[src*="vimeo"], iframe[src*="youtube"], iframe[src*="panda"]');
"""
JS_READ_SRCS = """
return [...document.querySelectorAll('video, video source')]
  .map(e => e.currentSrc || e.src).filter(s => s && s.startsWith('http'));
"""


def page_video_sources(driver, wait=15):
    """Lê o src da tag <video> da aula aberta. Desiste cedo se a página não tem player (aula de texto)."""
    t0 = time.time()
    while time.time() - t0 < wait:
        srcs = driver.execute_script(JS_READ_SRCS)
        if srcs:
            return list(dict.fromkeys(srcs))
        if time.time() - t0 > 2.5 and not driver.execute_script(JS_VIDEO_HINT):
            return []
        time.sleep(0.4)
    return []


def click_next(driver):
    """Clica em "Avançar" e espera a aula carregar. Retorna False se não houver próxima."""
    before = driver.current_url
    if not driver.execute_script(JS_CLICK_NEXT):
        return False
    for _ in range(50):
        time.sleep(0.3)
        if driver.current_url != before:
            break
    else:
        return False
    for _ in range(40):
        if driver.execute_script("return document.readyState") == "complete":
            break
        time.sleep(0.2)
    time.sleep(0.5)
    return True


class Downloads:
    """Downloads em paralelo. A fila é limitada para as URLs assinadas (válidas por 2h) não vencerem esperando."""

    def __init__(self, workers, ffmpeg):
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.slots = threading.Semaphore(workers * 3)
        self.ffmpeg = ffmpeg

    def submit(self, urls, target, label):
        self.slots.acquire()
        fut = self.pool.submit(self._run, urls, target, label)
        fut.add_done_callback(lambda _f: self.slots.release())
        return fut

    def _run(self, urls, target, label):
        for u in urls:
            for attempt in range(3):
                try:
                    download(u, target, self.ffmpeg)
                    if target.exists() and target.stat().st_size > 0:
                        logger.info(f"✓ {label}")
                        return True
                except Exception as e:
                    logger.warning(f"{label}: tentativa {attempt + 1} falhou ({str(e)[:80]})")
                time.sleep(2 * (attempt + 1))
        logger.error(f"✗ FALHOU: {label}")
        return False


def crawl(driver, start_url, dest, dl, only_slug=None):
    """Segue o "Avançar" enfileirando o download de cada vídeo. Com only_slug, para ao sair desse curso.
    Retorna {"finished": bool, "dir": Path, "entries": [...]}; finished só é True se chegou ao fim do curso."""
    driver.get(start_url)
    time.sleep(4)
    visited, entries = set(), []
    cur_slug, n, finished, course_dir = None, 0, False, None
    while len(visited) < 1000:
        url = driver.current_url
        if is_login(url):
            logger.warning("Sessão expirou. Faça login no Chrome aberto (sem usar a conta em outro navegador)...")
            if not wait_relogin(driver):
                logger.error("Timeout esperando novo login")
                break
            continue
        m = TASK_RE.search(url)
        if not m:
            logger.info(f"Fim: a Alura saiu das aulas ({url})")
            finished = True
            break
        slug, tid = m.groups()
        if only_slug and slug != only_slug:
            logger.info(f"Fim do curso {only_slug}: o 'Avançar' foi para outro curso")
            finished = True
            break
        if tid in visited:
            logger.info("Fim: voltou para uma aula já visitada")
            finished = True
            break
        visited.add(tid)
        if slug != cur_slug:
            cur_slug, n = slug, 0
            course_dir = dest / clean(slug)
            course_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"=== Curso: {slug} ===")
        n += 1
        title = clean(re.sub(r"^\d+\.\s*", "", driver.execute_script(JS_TITLE)))
        target = course_dir / f"{n:02d} - {title}.mp4"
        entry = {"n": n, "id": tid, "title": title, "status": None}
        entries.append(entry)

        if list(course_dir.glob(f"{n:02d} - *.mp4")):
            entry["status"] = "exists"
        else:
            try:
                urls = video_sources(driver, slug, tid) or page_video_sources(driver)
            except Exception as e:
                logger.warning(f"[{slug} #{n}] erro ao buscar vídeo: {e}")
                urls = []
            if not urls:
                entry["status"] = "no_video"
            else:
                entry["future"] = dl.submit(urls, target, f"[{slug} #{n}] {title}")

        moved = click_next(driver)
        # às vezes o "Avançar" cai em /task/luri (chat da IA, sem id numérico): volta e tenta de novo
        for _ in range(4):
            if not moved or TASK_RE.search(driver.current_url) or "/task/" not in driver.current_url:
                break
            logger.warning(f"[{slug} #{n}] 'Avançar' foi para {driver.current_url}; tentando de novo")
            driver.get(url)
            time.sleep(3)
            moved = click_next(driver)
        if not moved:
            logger.info("Fim: não há botão 'Avançar' (ou ele não mudou de aula)")
            finished = True
            break
    return {"finished": finished, "dir": course_dir, "entries": entries}


JS_CARDS = """
return [...document.querySelectorAll('a[href*="/course/"]')].map(a => ({
  h: a.href, lines: (a.innerText||'').split('\\n').map(s => s.trim()).filter(Boolean)
}));
"""
LEVELS = ("Básico", "Intermediário", "Avançado")
SKIP_CATEGORIES = re.compile(r"INGL[ÊE]S|ESPANHOL", re.I)  # cursos de idiomas ficam para depois


def catalog_courses(driver):
    """Rola o catálogo até o fim e devolve [(slug, tema, texto)] sem "em breve" e sem idiomas."""
    driver.get(f"{BASE}/app/catalog")
    time.sleep(6)
    last, stable = 0, 0
    for _ in range(800):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.8)
        count = len({x["h"] for x in driver.execute_script(JS_CARDS)})
        if count == last:
            stable += 1
            if stable >= 6:
                break
        else:
            stable, last = 0, count
    cards, seen = [], set()
    for x in driver.execute_script(JS_CARDS):
        m = re.match(r"^https://cursos\.alura\.com\.br/course/([^/?#]+)$", x["h"])
        lines = x["lines"]
        if not m or not lines or lines[0].upper() != "CURSO" or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        text = " ".join(lines)
        # o tema é a linha logo após o nível (ex.: Intermediário -> DADOS)
        category = next((lines[i + 1] for i, l in enumerate(lines[:-1]) if l in LEVELS), "OUTROS")
        if "[EM BREVE]" in text or SKIP_CATEGORIES.search(category) or "ingles" in m.group(1):
            continue
        cards.append((m.group(1), category, text))
    return cards


def settle(open_courses, done, progress_file, wait=False):
    """Fecha os cursos cujos downloads terminaram: grava o manifesto e marca o curso como concluído."""
    for c in list(open_courses):
        futs = [e["future"] for e in c["entries"] if e.get("future")]
        if not wait and not all(f.done() for f in futs):
            continue
        for e in c["entries"]:
            f = e.pop("future", None)
            if f is not None:
                e["status"] = "downloaded" if f.result() else "failed"
        failed = [e for e in c["entries"] if e["status"] == "failed"]
        if c["dir"]:
            (c["dir"] / "_manifest.json").write_text(
                json.dumps(c["entries"], ensure_ascii=False, indent=1), encoding="utf-8")
        if c["finished"] and not failed:
            done.add(c["slug"])
            progress_file.write_text(json.dumps(sorted(done), indent=1), encoding="utf-8")
        else:
            logger.warning(f"Curso {c['slug']} NÃO marcado como concluído "
                           f"(finished={c['finished']}, falhas={len(failed)}); será refeito na próxima execução")
        open_courses.remove(c)


def pick_root(roots, category, slug, min_free_gb=20):
    """Escolhe o disco do curso: o que já tem a pasta dele (retomada) ou o primeiro com espaço livre."""
    import shutil
    for r in roots:
        if (r / clean(category) / clean(slug)).exists():
            return r
    for r in roots:
        r.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(r.anchor or str(r)).free / 1e9 >= min_free_gb:
            return r
    return None


def run_catalog(driver, roots, dl, refresh=False, themes=None, first=None, tabs=1):
    """roots: lista de pastas de destino em ordem de preferência (ao encher uma, usa a próxima)."""
    import shutil
    dest = roots[0]  # guarda o cache do catálogo e o progresso
    dest.mkdir(parents=True, exist_ok=True)
    progress_file = dest / "_progress.json"
    catalog_file = dest / "_catalog.json"
    done = set(json.loads(progress_file.read_text(encoding="utf-8"))) if progress_file.exists() else set()

    if catalog_file.exists() and not refresh:
        courses = [tuple(c) for c in json.loads(catalog_file.read_text(encoding="utf-8"))]
        logger.info(f"Catálogo lido do cache ({catalog_file.name}); use --refresh para reler do site")
    else:
        courses = catalog_courses(driver)
        catalog_file.write_text(json.dumps(courses, ensure_ascii=False, indent=1), encoding="utf-8")
    if themes:
        courses = [c for c in courses if c[1].upper() in themes]
    pending = [c for c in courses if c[0] not in done]
    # um tema por vez; o tema de --first (prefixo do nome) vem antes, depois os demais em ordem alfabética
    pending.sort(key=lambda c: (0 if first and c[1].upper().startswith(first.upper()) else 1, c[1]))
    logger.info(f"Catálogo: {len(courses)} cursos; {len(done)} já concluídos; {len(pending)} a baixar")

    open_courses, lock = [], threading.Lock()
    queue = iter(enumerate(pending, 1))

    def worker(drv, own_window):
        nonlocal queue
        drv.switch_to.window(own_window)
        while True:
            with lock:
                item = next(queue, None)
            if item is None:
                return
            i, (slug, category, text) = item
            root = pick_root(roots, category, slug)
            if root is None:
                logger.error("Todos os discos de destino estão com menos de 20 GB livres. Parando.")
                return
            free_gb = shutil.disk_usage(root.anchor or str(root)).free / 1e9
            logger.info(f"##### [{i}/{len(pending)}] {category} / {slug} | {root} | livre: {free_gb:.0f} GB")
            try:
                start = first_task_url(drv, slug)
                if not start:
                    logger.warning(f"Sem acesso/aula inicial não encontrada: {slug}")
                    continue
                # pasta do tema: <destino>/<TEMA>/<curso>/
                res = crawl(drv, start, root / clean(category), dl, only_slug=slug)
            except Exception as e:
                logger.error(f"Erro no curso {slug}: {e}")
                if own_window not in drv.window_handles:  # a janela foi fechada: abre outra e refaz o curso
                    own_window = drv.switch_to.new_window("window") or drv.current_window_handle
                    own_window = drv.current_window_handle
                    logger.warning("Janela fechada; abri uma nova")
                    with lock:
                        queue = itertools.chain([item], queue)
                continue
            with lock:
                open_courses.append({"slug": slug, **res})
                settle(open_courses, done, progress_file)

    # cada aba (janela) tem seu próprio driver; todas usam o mesmo Chrome e a mesma sessão logada
    drivers = [(driver, driver.current_window_handle)]
    extra = []
    for _ in range(max(tabs, 1) - 1):
        d2 = setup_driver()
        d2.switch_to.new_window("window")
        extra.append(d2)
        drivers.append((d2, d2.current_window_handle))
    threads = [threading.Thread(target=worker, args=dw, daemon=True) for dw in drivers]
    logger.info(f"{len(threads)} abas navegando em paralelo")
    for t in threads:
        t.start()
        time.sleep(3)
    for t in threads:
        t.join()
    with lock:
        settle(open_courses, done, progress_file, wait=True)
    for d2 in extra:
        try:
            d2.close()
        finally:
            d2.service.stop()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = {a.split("=", 1)[0]: (a.split("=", 1)[1] if "=" in a else True) for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return
    catalog = "catalog" in args[0]
    slug = course_slug(args[0])
    if not slug and not catalog:
        print("Não consegui identificar o curso na URL.")
        return
    roots = [Path(p.strip()) for p in (args[1] if len(args) > 1 else "Alura").split(",")]
    dest = roots[0]
    ffmpeg = Path(__file__).resolve().parent / "ffmpeg.exe"
    ffmpeg = ffmpeg if ffmpeg.exists() else None
    dl = Downloads(int(opts.get("--workers", 4)), ffmpeg)
    themes = {t.strip().upper() for t in str(opts["--themes"]).split(",")} if "--themes" in opts else None

    driver = setup_driver()
    driver.switch_to.new_window("tab")  # janela própria: as antigas podem estar paradas na tela de login
    try:
        # abre direto uma página da Alura (nunca /loginForm: visitar essa página derruba a sessão)
        driver.get(f"{BASE}/app/catalog" if catalog else f"{BASE}/course/{slug}")
        time.sleep(4)
        if is_login(driver.current_url):
            logger.info("Faça login no Chrome aberto (você tem 5 minutos)...")
            if not wait_relogin(driver, 300):
                logger.error("Timeout no login")
                return
        if catalog:
            run_catalog(driver, roots, dl, refresh="--refresh" in opts, themes=themes,
                        first=opts.get("--first"), tabs=int(opts.get("--tabs", 1)))
            return
        start = first_task_url(driver, slug)
        if not start:
            logger.error("Não achei a primeira aula do curso (layout mudou ou sem acesso).")
            return
        res = crawl(driver, start, dest, dl)
        done = set()
        settle([{"slug": slug, **res}], done, dest / "_progress_single.json", wait=True)
        logger.info(f"Pasta: {dest.resolve()}")
    finally:
        dl.pool.shutdown(wait=True)
        # não fecha o Chrome: ele guarda o login (cookie de sessão) para a próxima execução
        driver.service.stop()


if __name__ == "__main__":
    main()
