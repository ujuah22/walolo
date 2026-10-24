import requests
import hashlib
import os
import re
from collections import Counter

# ---------------------------------------------------------------------------
# FUENTES
# ---------------------------------------------------------------------------

URLS = [
    "https://raw.githubusercontent.com/carmenchica3636/listas-iptv/refs/heads/main/lista_movistar.m3u",
    "https://raw.githubusercontent.com/Free-TV/IPTV/refs/heads/master/playlists/playlist_spain.m3u8",
    "https://www.apsattv.com/rakuten_es.m3u",
    "https://raw.githubusercontent.com/minhtienth15/mynote/main/t04.m3u",
]

# Esta lista solo conserva canales marcados como España (┃ES┃),
# y SÍ se le aplican las BLOCK_WORDS igual que a las demás.
URL_ES_ONLY = [
    "https://raw.githubusercontent.com/tokoblotongan/88/main/z2.m3u",
]

# Esta lista solo conserva canales cuyo NOMBRE termina en "ES" (como
# palabra suelta, ej. "TVE ES", no "Deportes") o que EMPIEZAN por
# "MOVISTAR". El resto de canales de esta fuente se descarta.
# También pasa por BLOCK_WORDS igual que las demás.
URL_ES_SUFIJO_O_MOVISTAR = [
    "https://raw.githubusercontent.com/tranhoanglanldt-cloud/HLKVN/refs/heads/main/GIAI%20TRI",
]

# Esta lista se procesa SIEMPRE la última, para que sus canales queden al
# final de lista.m3u (después de los de las demás fuentes). Pasa por
# BLOCK_WORDS y por la categorización igual que todas las demás.
URL_ULTIMA = [
    "https://pastebin.com/raw/hV2z2e9g",
]

EPG_URL = "http://143.47.50.252:5000/getEPG"

# ---------------------------------------------------------------------------
# PALABRAS BLOQUEADAS
# ---------------------------------------------------------------------------
# Se aplican a TODAS las fuentes por igual, sin excepciones.
# La coincidencia es por PALABRA COMPLETA (word boundaries), no por
# substring, para evitar falsos positivos como:
#   "star"  dentro de "movistar"   -> NO coincide
#   "sport" dentro de "eurosport"  -> NO coincide
#   "nl"    dentro de "online"     -> NO coincide

BLOCK_WORDS = [
    "portugal",
    "polsat",
    "israel",
    "méxico",
    "mexico",
    "izzigo",
    "now tv",
    "viasport",
    "cmore",
    "suecia",
    "noruega",
    "allente",
    "vodafone ru",
    "arena sport",
    "bein sports",
    "sports world",
    "worldcup club",
    "canal+",
    "france",
    "usa",
    "uk",
    "cbs",
    "afrique",
    "poland",
    "brasil",
    "brazil",
    "cosmote",
    "canada",
    "chicago",
    "cz",
    "sk",
    "cyprus",
    "bar de",
    "denmark",
    "bulgaria",
    "dubai",
    "italy",
    "nl",
    "argentina",
    "espnews",
    "mx",
    "fox sports",
    "fox",
    "fanduel",
    "network",
    "serbia",
    "hgtv",
    "russia",
    "redzone",
    "ngc",
    "nbcny",
    "prima",
    "netherland",
    "austria",
    "sky sport",
    "sony",
    "starz",
    "nz",
    "croatia",
    "sportsnet",
    "supersport",
    "germany",
    "spectrum",
    "ssc sport",
    "hindi",
    "star",
    "tsn",
    "pk",
    "chile",
    "travel",
    "turkey",
    "tennis",
    "sport live",
    "viaplay",
    "sports",
    "wdr",
    "yes",
    "ziggo",
    "zdf",
    "18+",
    "sportv",
    "digi",
    "5sport4k",
    "asia",
    "tv us",
    "channel",
    "🇧🇷",
    "fancode",
    "fast+",
    "rookie",
    "stingray",
    "that's",
    "reuters",
    "collective",
    "vevo pop",
    "vivir con",
    "billiards",
    "league",
    "nba tv",
    "boxing",
    "sportdigital",
    "24/7",
    "mma",
    "soccer",
    "echl",
    "alkass one",
    "israe",
    "sweden",
    "c more",
    "romania",
    "dstv",
    "america",
    "greece",
    "globo",
    "philadelphia",
    "sport",
    "oxygen",
    "football",
    "hockey",
    "tv4",
    "vice",
    "willow",
    "vodafone",
    "3sat",
    "univision",
    "trt",
    "tvp",
    "tv1",
    "tv2",
    "kabel",
    "smartbank",
    "fussball",
    "bein",
    "match!",
    "SkySport",
    "CosmoteSportUHD4K",
    "tnt",
    "DSPORTS",
    "Swimming",
    "USHL",
    "QMJHL",
    "OHL",
    "AHL",
    "NLL",
    "Gymnastics",
    "College",
    "WWE",
    "Volleyball",
    "Ice",
    "NHL",
    "QMWHL",
    "NBA | NBA",
    "NCAAW",
    "Basketball",
    "Cricket",
    "DADDYLIVE",
    "https://easyproxy-latest-9tiz.onrender.com",
    "TE24",
    "Squirrel",
    "ZiggoSportUHD",
    "NESN_UHD",
    "sky",
    "skysports",
]

# Convertimos una sola vez todas las palabras a formato comparable y
# compilamos un único patrón con límites de palabra (\b) para evitar
# falsos positivos por coincidencia parcial dentro de otra palabra.


def _patron_para_palabra(palabra):
    """Construye el fragmento de regex para una palabra bloqueada,
    añadiendo \\b solo en los extremos que empiezan/terminan en un
    carácter alfanumérico (para no romper palabras como '18+' o
    'match!', que tienen símbolos en el borde)."""
    prefijo = r"\b" if palabra[0].isalnum() else ""
    sufijo = r"\b" if palabra[-1].isalnum() else ""
    return prefijo + re.escape(palabra) + sufijo


BLOCK_WORDS_NORMALIZADAS = sorted(
    {palabra.strip().casefold() for palabra in BLOCK_WORDS if palabra.strip()},
    key=len,
    reverse=True,
)

PATRON_BLOCK_WORDS = re.compile(
    "|".join(_patron_para_palabra(palabra) for palabra in BLOCK_WORDS_NORMALIZADAS)
)

# Marcas que identifican un canal como "España / Movistar+" dentro de las
# fuentes marcadas como URL_ES_ONLY. Se acepta si contiene [ES], ┃ES┃ o M+
# (por ejemplo "M+ ESTRENOS 1 HD" o "M+ LIGA DE CAMPEONES 1 HD").
PATRON_ES_ONLY = re.compile(
    r"\[es\]|┃es┃|" + re.escape("m+"),
    re.IGNORECASE,
)

# Patrón para la regla: nombre de canal que TERMINA en "ES" como
# palabra suelta (con un límite de palabra antes, para no colar cosas
# como "Deportes", que también termina en "es" pero pegado a la palabra).
PATRON_TERMINA_EN_ES = re.compile(r"\bES\s*$", re.IGNORECASE)

contador = Counter()

GRUPOS_PROTEGIDOS = [
    "movistar deportes playready",
]

# Prefijo que se añade al nombre de los grupos protegidos para que
# aparezcan los primeros en la app (antes de "01 ...").
PREFIJO_GRUPO_PRIORITARIO = "00 ⭐ "


# ---------------------------------------------------------------------------
# CATEGORIZACIÓN
# ---------------------------------------------------------------------------

def _sin_urls(extinf):
    """Quita los atributos cuyo valor es una URL (tvg-logo, group-logo,
    catchup-source...). Esas rutas suelen contener palabras como
    "movistar" que describen la imagen o el servidor, no el canal."""
    return re.sub(r'[\w-]+="https?://[^"]*"', "", extinf, flags=re.IGNORECASE)


# "movistar" al inicio de palabra, o "m+" suelto (no pegado a otra letra o
# número, para no coger cosas como "Premium+" o "Crime+").
PATRON_MOVISTAR = re.compile(r"\bmovistar|(?<![a-z0-9])m\+")


def _es_movistar(extinf):
    """Un canal es Movistar+ solo si su NOMBRE (o su tvg-name) lo indica,
    no por lo que aparezca en otros atributos (ids, logos, grupos...)."""
    nombres = [_nombre_canal(extinf).casefold()]
    tvg_name = re.search(r'tvg-name="([^"]*)"', extinf, re.IGNORECASE)
    if tvg_name:
        nombres.append(tvg_name.group(1).casefold())
    return any(PATRON_MOVISTAR.search(n) for n in nombres)


def obtener_categoria(extinf):
    texto = _sin_urls(extinf).casefold()

    # DAZN
    if "dazn" in texto:
        return "03 🔥 DAZN"

    # MOVISTAR (solo por el nombre del canal)
    if _es_movistar(extinf):
        return "04 ⭐ Movistar+"

    # FÚTBOL
    if any(
        palabra in texto
        for palabra in [
            "laliga",
            "la liga",
            "champions",
            "liga de campeones",
            "premier league",
            "bundesliga",
            "serie a",
            "ligue 1",
            "uefa",
            "realmadrid",
            "real madrid",
            "hypermotion",
            "copa del rey",
            "futbol",
            "fútbol",
        ]
    ):
        return "02 ⚽ Fútbol"

    # MOTOR
    if any(
        palabra in texto
        for palabra in [
            "formula 1",
            "fórmula 1",
            "f1",
            "motogp",
            "moto gp",
        ]
    ):
        return "05 🏎️ Motor"

    # DEPORTES
    if any(
        palabra in texto
        for palabra in [
            "eurosport",
            "golf",
            "tennis",
            "tenis",
            "nba",
            "nfl",
            "sport",
            "deporte",
            "vamos",
        ]
    ):
        return "06 🎾 Deportes"

    # CINE
    if any(
        palabra in texto
        for palabra in [
            "cine",
            "movie",
            "movies",
            "cinema",
            "tcm",
        ]
    ):
        return "07 🎬 Cine"

    # SERIES
    if any(
        palabra in texto
        for palabra in [
            "series",
            "axn",
            "fox",
            "warner",
            "syfy",
            "amc",
        ]
    ):
        return "08 📺 Series"

    # DOCUMENTALES
    if any(
        palabra in texto
        for palabra in [
            "discovery",
            "history",
            "historia",
            "nat geo",
            "national geographic",
            "odisea",
        ]
    ):
        return "09 📚 Documentales"

    # INFANTIL
    if any(
        palabra in texto
        for palabra in [
            "disney",
            "nick",
            "nickelodeon",
            "boing",
            "cartoon",
            "clan",
        ]
    ):
        return "10 🧒 Infantil"

    # NOTICIAS
    if any(
        palabra in texto
        for palabra in [
            "cnn",
            "24h",
            "24 horas",
            "euronews",
            "bbc news",
            "news",
        ]
    ):
        return "11 📰 Noticias"

    # MÚSICA
    if any(
        palabra in texto
        for palabra in [
            "mtv",
            "music",
            "música",
            "musica",
            "sol musica",
            "sol música",
            "hit tv",
            "mezzo",
        ]
    ):
        return "12 🎵 Música"

    # GENERALISTAS
    if any(
        palabra in texto
        for palabra in [
            "la 1",
            "la1",
            "la 2",
            "la2",
            "antena 3",
            "telecinco",
            "cuatro",
            "la sexta",
            "lasexta",
            "trece",
            "canal sur",
            "telemadrid",
        ]
    ):
        return "01 📺 Generalistas"

    return "99 📦 Otros"


def limpiar_y_categorizar(extinf):
    grupo_original = re.search(
        r'group-title="([^"]*)"',
        extinf,
        re.IGNORECASE,
    )

    if grupo_original:
        nombre_grupo = grupo_original.group(1).strip()
        if nombre_grupo.casefold() in {
            grupo.casefold()
            for grupo in GRUPOS_PROTEGIDOS
        }:
            # Grupo protegido: se conserva el nombre original, pero con
            # un prefijo "00" para que la app lo muestre el primero.
            nuevo_nombre = PREFIJO_GRUPO_PRIORITARIO + nombre_grupo
            contador[nuevo_nombre] += 1
            return re.sub(
                r'group-title="[^"]*"',
                lambda m: f'group-title="{nuevo_nombre}"',
                extinf,
                count=1,
                flags=re.IGNORECASE,
            )

    # Eliminar la categoría original
    extinf = re.sub(
        r'\s*group-title="[^"]*"',
        "",
        extinf,
        flags=re.IGNORECASE,
    )

    categoria = obtener_categoria(extinf)
    contador[categoria] += 1

    if categoria == "99 📦 Otros":
        print(f"SIN CLASIFICAR -> {extinf}")

    pos = extinf.rfind(",")
    if pos != -1:
        extinf = (
            extinf[:pos]
            + f' group-title="{categoria}"'
            + extinf[pos:]
        )

    return extinf


def _es_prioritario(extinf):
    """True si la línea EXTINF pertenece a un grupo protegido que ya
    lleva el prefijo prioritario en su group-title."""
    return f'group-title="{PREFIJO_GRUPO_PRIORITARIO}' in extinf


def _quitar_urls_de_atributos(extinf):
    """Elimina el contenido de tvg-logo y catchup-source antes de aplicar
    cualquier filtro de palabras. Estas URLs a veces reutilizan logos e
    imágenes de otros países/canales (ej. una carpeta "USA/Fox Network/"
    para un logo genérico de "M+ Estrenos"), y si se dejan en el texto
    provocan bloqueos falsos por palabras como "usa", "fox" o "network"
    que en realidad no describen el canal, sino la ruta de una imagen."""
    texto = re.sub(r'tvg-logo="[^"]*"', '', extinf, flags=re.IGNORECASE)
    texto = re.sub(r'catchup-source="[^"]*"', '', texto, flags=re.IGNORECASE)
    return texto


def contiene_palabra_bloqueada(extinf):
    """Comprueba la línea EXTINF (nombre + atributos: group-title,
    tvg-id, tvg-name...) buscando alguna BLOCK_WORD como PALABRA COMPLETA.
    Esto evita falsos positivos como 'star' dentro de 'movistar' o 'sport'
    dentro de 'eurosport'. Las URLs de tvg-logo/catchup-source se excluyen
    primero para no bloquear canales por culpa de un logo mal etiquetado."""
    texto_normalizado = _quitar_urls_de_atributos(extinf).casefold()
    coincidencia = PATRON_BLOCK_WORDS.search(texto_normalizado)
    return coincidencia.group(0) if coincidencia else None


def _nombre_canal(extinf):
    """Devuelve el nombre visible del canal: el texto que va después de
    la última coma de la línea #EXTINF."""
    pos = extinf.rfind(",")
    if pos == -1:
        return extinf.strip()
    return extinf[pos + 1:].strip()


def cumple_es_o_movistar(extinf):
    """Para las fuentes de URL_ES_SUFIJO_O_MOVISTAR: se acepta el canal
    si su nombre termina en "ES" como palabra suelta (ej. "TVE ES", no
    "Deportes") o si el nombre empieza por "MOVISTAR"."""
    nombre = _nombre_canal(extinf)
    if PATRON_TERMINA_EN_ES.search(nombre):
        return True
    if nombre.strip().casefold().startswith("movistar"):
        return True
    return False


# ---------------------------------------------------------------------------
# PROCESO PRINCIPAL
# ---------------------------------------------------------------------------

cabecera = f'#EXTM3U url-tvg="{EPG_URL}"'

# Los canales de grupos protegidos (movistar deportes playready) van a
# una lista aparte para escribirlos los PRIMEROS en lista.m3u.
salida_prioritaria = []
salida = []
destino = salida

def descargar(url, indice):
    """Descarga la fuente de la red y actualiza el ESPEJO local (upstream/).
    Si la red falla y ya existe un espejo anterior en el repo, se usa el
    espejo: así el sistema sigue funcionando aunque la fuente original
    desaparezca (con la ultima copia que tengamos)."""
    espejo = os.path.join("upstream", f"fuente_{indice:02d}.m3u")
    try:
        respuesta = requests.get(url, timeout=30)
        respuesta.raise_for_status()
        contenido = respuesta.text
    except Exception as error_red:
        if os.path.exists(espejo):
            with open(espejo, "r", encoding="utf-8") as archivo:
                contenido = archivo.read()
            print(f"  [aviso] red fallida ({error_red}); usando espejo {espejo}")
        else:
            raise
    else:
        os.makedirs("upstream", exist_ok=True)
        with open(espejo, "w", encoding="utf-8") as archivo:
            archivo.write(contenido)
    return contenido


for indice, url in enumerate(URLS + URL_ES_ONLY + URL_ES_SUFIJO_O_MOVISTAR + URL_ULTIMA):
    try:
        print()
        print(f"Descargando: {url}")
        contenido = descargar(url, indice)
        print(
            f"Bytes descargados: {len(contenido)}"
        )

        lineas = contenido.splitlines()
        if (
            lineas
            and lineas[0].lstrip("\ufeff").startswith("#EXTM3U")
        ):
            lineas = lineas[1:]

        omitir = False

        for linea in lineas:
            linea = linea.strip()
            if not linea:
                continue

            # Las etiquetas #EXTGRP (usadas por algunas fuentes, p.ej.
            # GIAI TRI) sustituyen al group-title en muchos reproductores
            # y generaban categorías fantasma (como "SPORTS") que no
            # pasaban por BLOCK_WORDS ni por la categorización. Se
            # descartan siempre, para todas las fuentes.
            if linea.startswith("#EXTGRP"):
                continue

            if linea.startswith("#EXTINF"):
                texto = _quitar_urls_de_atributos(linea).casefold()

                # Si la fuente es "solo España", primero comprobamos la
                # marca ┃ES┃; si no la lleva, se descarta directamente.
                if url in URL_ES_ONLY:
                    es_valido = bool(PATRON_ES_ONLY.search(texto))

                    if not es_valido:
                        omitir = True
                        print(
                            f"BLOQUEADO POR NO SER [ES]/┃ES┃/M+ -> {linea}"
                        )
                        continue

                # Si la fuente solo admite canales acabados en "ES" o que
                # empiecen por "MOVISTAR" (p.ej. GIAI TRI), el resto se
                # descarta directamente.
                if url in URL_ES_SUFIJO_O_MOVISTAR:
                    if not cumple_es_o_movistar(linea):
                        omitir = True
                        print(
                            "BLOQUEADO POR NO ACABAR EN 'ES' NI EMPEZAR "
                            f"POR 'MOVISTAR' -> {linea}"
                        )
                        continue

                # A partir de aquí, TODAS las fuentes pasan por el mismo
                # filtro de BLOCK_WORDS, sin excepciones.
                palabra_detectada = contiene_palabra_bloqueada(linea)
                omitir = palabra_detectada is not None
                if omitir:
                    print(
                        "BLOQUEADO "
                        f"[{palabra_detectada}] -> {linea}"
                    )
                else:
                    linea = limpiar_y_categorizar(linea)
                    destino = (
                        salida_prioritaria
                        if _es_prioritario(linea)
                        else salida
                    )

            if not omitir:
                destino.append(linea)

    except requests.RequestException as error:
        print(
            f"Error descargando {url}: {error}"
        )
    except Exception as error:
        print(
            f"Error procesando {url}: {error}"
        )

# Cabecera + canales prioritarios + resto de canales
contenido_final = "\n".join(
    [cabecera] + salida_prioritaria + salida
) + "\n"

# Freno de seguridad: si TODAS las fuentes fallan, NO sobreescribir la lista
# anterior con una vacía.
total_canales = len(salida_prioritaria) + len(salida)
if total_canales == 0:
    print("ERROR: 0 canales procesados; se conserva la lista anterior")
    raise SystemExit(1)

md5 = hashlib.md5(
    contenido_final.encode("utf-8")
).hexdigest()

with open(
    "lista.m3u",
    "w",
    encoding="utf-8",
) as archivo:
    archivo.write(contenido_final)

# ---------------------------------------------------------------------------
# LISTAS POR PLATAFORMA
# ---------------------------------------------------------------------------
# Cada canal se emite en varias "familias" (DRM/formato) y cada plataforma
# solo reproduce algunas:
#
#   playready -> Android (algunas apps) y PC (Windows)
#   clearkey  -> Android y PC
#   dash      -> Android y PC (mejor esfuerzo)
#   fairplay  -> SOLO iOS
#   plain     -> en todas (HLS sin DRM)
#
# Por eso se generan:
#   lista-ios.m3u        -> fairplay + plain
#   lista-android-pc.m3u -> playready + clearkey + dash + plain
#   lista.m3u            -> maestra (todo)


def _familia_de_bloque(lineas_bloque):
    texto = "\n".join(lineas_bloque)
    url = next(
        (l for l in reversed(lineas_bloque) if l.startswith("http")),
        "",
    )
    if "license_type=playready" in texto or "pry-movistarplus" in url:
        return "playready"
    if (
        "license_type=org.w3.clearkey" in texto
        or "dash-movistarplus" in url
        or "widevine" in texto
    ):
        return "clearkey"
    if "license_type=fairplay" in texto or "hlsfp-movistarplus" in url:
        return "fairplay"
    if url.endswith(".mpd") or ".isml" in url:
        return "dash"
    return "plain"


def _partir_en_bloques(lineas):
    """Parte la lista de lineas en bloques por canal: cada bloque empieza
    por #EXTINF e incluye sus lineas de propiedades y la URL hasta la
    proxima #EXTINF."""
    bloques = []
    actual = None
    for linea in lineas:
        if linea.startswith("#EXTINF"):
            if actual is not None:
                bloques.append(actual)
            actual = [linea]
        elif actual is not None:
            actual.append(linea)
    if actual is not None:
        bloques.append(actual)
    return bloques


def _escribir_lista(ruta, bloques):
    lineas = [cabecera]
    for bloque in bloques:
        lineas.extend(bloque)
    with open(ruta, "w", encoding="utf-8") as archivo:
        archivo.write("\n".join(lineas) + "\n")


bloques = _partir_en_bloques(salida_prioritaria + salida)
clases = [_familia_de_bloque(b) for b in bloques]
familias = Counter(clases)

familias_ios = {"fairplay", "plain"}
familias_android_pc = {"playready", "clearkey", "dash", "plain"}

bloques_ios = [b for b, c in zip(bloques, clases) if c in familias_ios]
bloques_android_pc = [b for b, c in zip(bloques, clases) if c in familias_android_pc]

_escribir_lista("lista-ios.m3u", bloques_ios)
_escribir_lista("lista-android-pc.m3u", bloques_android_pc)

print()
print("=" * 60)
print("RESUMEN DE CATEGORÍAS")
print("=" * 60)
for categoria, total in sorted(contador.items()):
    print(f"{categoria}: {total}")
print("=" * 60)
print("FAMILIAS (formato/DRM)")
for familia, total in sorted(familias.items()):
    print(f"{familia}: {total}")
print("=" * 60)
print(f"Canales procesados: {sum(contador.values())}")
print(f"MD5: {md5}")
print(f"lista.m3u (maestra): {sum(contador.values())} canales")
print(f"lista-ios.m3u: {len(bloques_ios)} canales")
print(f"lista-android-pc.m3u: {len(bloques_android_pc)} canales")
print("Listas creadas correctamente")
print("=" * 60)
