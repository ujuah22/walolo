#!/usr/bin/env python3
"""
cazador.py — detector de caídas + cazador de orígenes nuevos para la lista.

USO:
  python cazador.py --estado
      Comprueba el estado de las 7 fuentes (¿cuál está caída?).

  python cazador.py --caza
      Caza reemplazos SOLO para las fuentes caídas.

  python cazador.py --caza --todas
      Caza en TODAS las categorías (modo manual: "quiero orígenes nuevos ya").

  python cazador.py --caza movistar-dof deporte-es
      Caza solo en esas categorías.

Categorías:
  movistar-dof  → Movistar+ / DAZN / Deportes (panel DOF, playready/fairplay)
  deporte-es    → fútbol [ES] por paneles (light-ott, mag7070, kazaktelekom…)
  abiertos      → RTVE, 3cat, Rakuten… (casi nunca caen; listas canónicas)

Salida: informe en  cazador/INFORME_<fecha>.md  (candidatos validados +
la línea exacta a pegar en merge.py).

Motores de búsqueda (en orden):
  1. API búsqueda de repos de GitHub  (sin auth, 10 req/min)
  2. API búsqueda de código de GitHub (si existe GITHUB_TOKEN; en Actions sí)
  3. DuckDuckGo HTML                   (sin JS)
Toda búsqueda es defensiva: un motor que falle no para a los demás.
"""

import argparse
import datetime as _dt
import os
import re
import sys
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
SALIDA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cazador")

# ---------------------------------------------------------------------------
# Las 7 fuentes que consume merge.py (mismas URLs, mismo orden)
# ---------------------------------------------------------------------------
FUENTES = [
    ("movistar-dof", "carmenchica3636/lista_movistar.m3u",
     "https://raw.githubusercontent.com/carmenchica3636/listas-iptv/refs/heads/main/lista_movistar.m3u"),
    ("abiertos",     "Free-TV/IPTV playlist_spain",
     "https://raw.githubusercontent.com/Free-TV/IPTV/refs/heads/master/playlists/playlist_spain.m3u8"),
    ("abiertos",     "apsattv rakuten_es",
     "https://www.apsattv.com/rakuten_es.m3u"),
    ("deporte-es",   "minhtienth15/t04.m3u (dlive)",
     "https://raw.githubusercontent.com/minhtienth15/mynote/refs/heads/main/t04.m3u"),
    ("deporte-es",   "tokoblotongan/88 z2.m3u",
     "https://raw.githubusercontent.com/tokoblotongan/88/main/z2.m3u"),
    ("deporte-es",   "traanhoanglanldt HLKVN/GIAI TRI",
     "https://raw.githubusercontent.com/tranhoanglanldt-cloud/HLKVN/refs/heads/main/GIAI%20TRI"),
    ("movistar-dof", "pastebin hV2z2e9g (PlayReady+FairPlay)",
     "https://pastebin.com/raw/hV2z2e9g"),
]

# Huellas (dominios) y palabras clave para puntuar candidatos por categoría
CATEGORIAS = {
    "movistar-dof": {
        "huellas": ["emisiondof6.com", "stover-wp0.cdn.telefonica.com", "movistarplus"],
        "keywords": ["movistar", "dazn", "playready", "fairplay", "hypermotion", "laliga", "campeones"],
        "consultas": [
            "iptv movistar plus m3u spain",
            "lista movistar plus dazn m3u",
            "movistar playready fairplay m3u",
            "emisiondof6",
        ],
        "huella_codigo": ["emisiondof6.com", "movistarplus.emisiondof6"],
    },
    "deporte-es": {
        "huellas": ["-ott.net", "kazaktelekom.com", "mag7070.eu", "live.php?mac="],
        "keywords": ["champions", "liga de campeones", "mundial", "dazn", "play_token", "┃ES┃"],
        "consultas": [
            "iptv spain football champions m3u",
            "live.php mac play_token m3u",
            "kazaktelekom iptv m3u",
        ],
        "huella_codigo": ["live.php?mac=", "00:1A:79", "-ott.net"],
    },
    "abiertos": {
        "huellas": ["rtvelivestream", "3catdirectes.cat", "apsattv.com"],
        "keywords": ["rtve", "la 1", "antena 3", "clan", "rakuten"],
        "consultas": [
            "iptv es abiertos m3u",
            "lista tv espana m3u 2026",
        ],
        "huella_codigo": ["rtvelivestream.rtve.es"],
    },
}

# Cap de candidatos a validar (no quemar rate limits ni tiempo)
MAX_VALIDACIONES = 25
MAX_BYTES = 4_000_000


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# --estado: comprobar fuentes
# ---------------------------------------------------------------------------
def comprobar_fuentes():
    estado = {}
    for cat, nombre, url in FUENTES:
        try:
            r = requests.get(url, timeout=25, headers=UA, stream=True)
            r.raise_for_status()
            trozo = b"".join(next(r.iter_content(200_000), b"") for _ in range(2))
            canal_txt = trozo.decode("utf-8", "ignore")
            n = canal_txt.count("#EXTINF")
            ok = r.status_code == 200 and n >= 5
            estado[nombre] = (ok, f"HTTP {r.status_code}, ~{n} canales (muestra)")
        except Exception as e:
            estado[nombre] = (False, f"CAÍDA: {type(e).__name__}: {e}")
        time.sleep(1)

    caidas = [f for f in FUENTES if not estado[f[1]][0]]
    log("\n=== ESTADO DE FUENTES ===")
    for cat, nombre, url in FUENTES:
        ok, detalle = estado[nombre]
        log(f"  [{'OK ' if ok else 'CAÍ'}] ({cat}) {nombre}  —  {detalle}")
    log(f"\nCaídas: {len(caidas)} de {len(FUENTES)}")
    return caidas


# ---------------------------------------------------------------------------
# Motores de búsqueda → lista de URLs candidatas
# ---------------------------------------------------------------------------
def _gh(url, params=None):
    r = requests.get(url, headers={**UA, "Accept": "application/vnd.github+json"},
                     params=params, timeout=25)
    return r


# Consultas genéricas por categoría (la búsqueda de repos de GitHub indexa
# nombre/descripción/README, no el contenido; mejor buscar amplio y filtrar
# después por CONTENIDO, que es lo que ya hace validar()).
CONSULTAS_GENERICAS = {
    "movistar-dof": ["iptv movistar m3u", "iptv spain m3u", "movistar plus iptv"],
    "deporte-es": ["iptv football spain m3u", "iptv es m3u", "futbol iptv m3u"],
    "abiertos": ["iptv spain m3u", "iptv es m3u"],
}


def buscar_github_repos(cat):
    """API de búsqueda de REPOS (sin auth). Devuelve URLs raw candidatas."""
    out = []
    cfg = CATEGORIAS[cat]
    consultas = list(dict.fromkeys(CONSULTAS_GENERICAS.get(cat, []) + cfg["consultas"]))
    for q in consultas:
        try:
            r = _gh("https://api.github.com/search/repositories",
                    {"q": q, "per_page": 10, "sort": "updated"})
            if r.status_code == 403:
                log(f"  [github-repos] rate limit en '{q}', salto")
                time.sleep(8)
                continue
            r.raise_for_status()
            for repo in r.json().get("items", []):
                nombre = repo.get("full_name", "")
                pushed = repo.get("pushed_at", "")[:10]
                if not nombre or pushed < _dt.datetime.now(_dt.timezone.utc).replace(
                        year=_dt.datetime.now(_dt.timezone.utc).year - 1).strftime("%Y-%m-%d"):
                    continue  # muy antiguo
                # enumerar archivos .m3u del repo (hasta 3)
                try:
                    tr = _gh(f"https://api.github.com/repos/{nombre}/git/trees/HEAD").json()
                    ficheros = [t["path"] for t in tr.get("tree", [])
                               if t.get("type") == "blob"
                               and re.search(r"\.(m3u8?|txt)$", t["path"], re.I)
                               and "logo" not in t["path"].lower()]
                    branch = "main"
                    for f in ficheros[:3]:
                        out.append(f"https://raw.githubusercontent.com/{nombre}/{branch}/{f}")
                except Exception:
                    pass
        except Exception as e:
            log(f"  [github-repos] fallo en '{q}': {e}")
        time.sleep(7)  # 10 req/min sin auth
    return out


def buscar_github_codigo(cat):
    """API de búsqueda de CÓDIGO (necesita GITHUB_TOKEN; en Actions está)."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        log("  [github-codigo] sin GITHUB_TOKEN, se omite")
        return []
    out = []
    cfg = CATEGORIAS[cat]
    for huella in cfg["huella_codigo"][:2]:
        try:
            r = requests.get(
                "https://api.github.com/search/code",
                headers={**UA, "Accept": "application/vnd.github+json",
                         "Authorization": f"Bearer {token}"},
                params={"q": huella, "per_page": 20}, timeout=25)
            if r.status_code != 200:
                log(f"  [github-codigo] HTTP {r.status_code} en '{huella}', sigo")
                continue
            for hit in r.json().get("items", []):
                repo = hit.get("repository", {}).get("full_name", "")
                path = hit.get("path", "")
                if repo and path and re.search(r"\.(m3u8?|txt)$", path, re.I):
                    out.append(f"https://raw.githubusercontent.com/{repo}/HEAD/{path}")
        except Exception as e:
            log(f"  [github-codigo] fallo en '{huella}': {e}")
        time.sleep(2)
    return out


def _ddg_links(query):
    r = requests.get("https://html.duckduckgo.com/html/",
                     params={"q": query}, headers=UA, timeout=25)
    r.raise_for_status()
    links = re.findall(r'uddg=([^&"]+)', r.text)
    import urllib.parse
    return [urllib.parse.unquote(l) for l in links][:10]


def buscar_duckduckgo(cat):
    out = []
    for q in CATEGORIAS[cat]["consultas"]:
        try:
            for url in _ddg_links(f'"{q}" m3u'):
                out.append(url)
        except Exception as e:
            log(f"  [ddg] fallo en '{q}': {e}")
        time.sleep(2)
    return out


def a_raw_github(url):
    """github.com/owner/repo/blob/... → raw.githubusercontent.com/..."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/(.+)$", url)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return None


def pareceria_de_url(url, cat):
    """0 = descartar, 1 = quizá, 2 = parece lista."""
    if not url:
        return 0
    if re.search(r"\.(m3u8?|txt)(\?|$)", url, re.I) or "/raw/" in url or "pastebin.com/raw" in url:
        return 2
    if a_raw_github(url):
        return 2
    if "github.com" in url and re.search(r"/blob/.*\.(m3u8?|txt)$", url, re.I):
        return 2
    if "pastebin.com" in url and "/raw/" not in url:
        return 1  # podría ser una página pastebin; se intenta /raw/
    return 0


# ---------------------------------------------------------------------------
# Validación de candidatos
# ---------------------------------------------------------------------------
def validar(url, cat):
    try:
        r = requests.get(url, timeout=25, headers=UA)
        if r.status_code != 200:
            return None
        texto = r.text[:MAX_BYTES]
        n = texto.count("#EXTINF")
        if n < 20:
            return None
        t = texto.lower()
        cfg = CATEGORIAS[cat]
        fp = [h for h in cfg["huellas"] if h.lower() in t]
        kw = [k for k in cfg["keywords"] if k.lower() in t]
        score = 3 * len(fp) + 2 * len(kw)
        if score == 0:
            return None
        return {
            "url": url, "canales": n, "score": score,
            "huellas": fp, "keywords": kw,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Caza por categoría
# ---------------------------------------------------------------------------
def cazar(cat):
    log(f"\n### Caza: {cat}")
    candidatas = []
    candidatas += buscar_github_repos(cat)
    candidatas += buscar_github_codigo(cat)
    candidatas += buscar_duckduckgo(cat)

    # normalizar (blob→raw, pastebin→raw) y deduplicar
    vistas = set()
    unicas = []
    for u in candidatas:
        raw = a_raw_github(u)
        if raw:
            u = raw
        m = re.match(r"https://pastebin\.com/([A-Za-z0-9]+)$", u)
        if m:
            u = f"https://pastebin.com/raw/{m.group(1)}"
        if u in vistas or pareceria_de_url(u, cat) == 0:
            continue
        vistas.add(u)
        unicas.append(u)
    log(f"  candidatas únicas: {len(unicas)}")

    resultados = []
    intentos = 0
    for u in unicas:
        if len(resultados) >= 10 or intentos >= MAX_VALIDACIONES:
            break
        intentos += 1
        res = validar(u, cat)
        if res:
            resultados.append(res)
            log(f"  ✓ {u}  → {res['canales']} canales, score {res['score']}, huellas={res['huellas']}")
    resultados.sort(key=lambda r: -r["score"])
    return resultados[:10]


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------
def escribir_informe(cazas):
    os.makedirs(SALIDA_DIR, exist_ok=True)
    marca = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = os.path.join(SALIDA_DIR, f"INFORME_{marca}.md")
    lineas = [
        f"# Informe de caza de orígenes — {_dt.datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        "Candidatos **validados** (descargados y puntuados). Puntuación: 3 por huella",
        "de dominio conocida + 2 por keyword relevante. Ordenado de mejor a peor.",
        "",
        "## Cómo aplicar un candidato",
        "1. Pruébalo en tu reproductor (3-5 canales, incluidos los de fútbol).",
        "2. Si va bien, añade su URL a `merge.py` (sección `URLS` u otra, según categoría):",
        "   ```python",
        '   URLS = ["<URL_DEL_CANDIDATO>", ...]',
        "   ```",
        "3. Commit + push. La lista se regenera en el siguiente run (≤ 1 h) o al instante",
        "   con `.venv/bin/python merge.py`.",
        "4. Vigila 24-48 h: que los canales sigan vivos (los tokens de lista nueva",
        "   suelen durar poco si el origen es débil).",
        "",
    ]
    for cat, resultados in cazas.items():
        lineas.append(f"## Categoría: {cat} — {len(resultados)} candidatos")
        lineas.append("")
        if not resultados:
            lineas.append("_Sin candidatos válidos. Opciones: reintentar en unas horas,")
            lineas.append("buscar a mano (ver GUIA-CAIDAS.md, sección 'caza manual') o usar")
            lineas.append("el espejo `upstream/` mientras dure._")
            lineas.append("")
            continue
        for i, r in enumerate(resultados, 1):
            lineas += [
                f"### {i}. score {r['score']} — {r['canales']} canales",
                f"- URL: `{r['url']}`",
                f"- Huellas: {', '.join(r['huellas']) or '—'}",
                f"- Keywords: {', '.join(r['keywords']) or '—'}",
                "",
            ]
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas))
    log(f"\nInforme escrito: {ruta}")
    return ruta


def main():
    ap = argparse.ArgumentParser(description="Cazador de orígenes IPTV")
    ap.add_argument("--estado", action="store_true", help="solo comprueba el estado de las fuentes")
    ap.add_argument("--caza", action="store_true", help="caza reemplazos")
    ap.add_argument("--todas", action="store_true", help="con --caza: todas las categorías aunque nada esté caído")
    ap.add_argument("cats", nargs="*", help="categorías concretas: movistar-dof deporte-es abiertos")
    a = ap.parse_args()

    if not (a.estado or a.caza):
        ap.print_help()
        sys.exit(2)

    caidas = []
    if a.estado or a.caza:
        caidas = comprobar_fuentes()

    if a.estado and not a.caza:
        return

    if a.caza:
        if a.cats:
            cats = [c for c in a.cats if c in CATEGORIAS]
        elif a.todas:
            cats = list(CATEGORIAS)
        else:
            cats = sorted({c for (c, _, _) in caidas})
            if not cats:
                log("\nNada está caído. Para cazar a todas las categorías: --caza --todas")
                return
        log(f"\nCategorías a cazar: {cats}")
        cazas = {}
        for cat in cats:
            cazas[cat] = cazar(cat)
            time.sleep(3)
        escribir_informe(cazas)


if __name__ == "__main__":
    main()
