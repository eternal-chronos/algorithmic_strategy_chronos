"""Paleta del panel de backtest.

Estos tokens son la única fuente de color: `dashboard.py` los inyecta como
variables CSS y como objeto JS, de modo que el HTML y los gráficos de Plotly
comparten paleta sin duplicar hexadecimales.

Paleta validada para daltonismo (ΔE CVD 24.7 en el par categórico usado) sobre
la superficie clara `#fcfcfb`. El panel se genera en modo claro: es un fichero
local de análisis, no una página que herede el tema del visor.
"""

from __future__ import annotations

# --- Tokens de color --------------------------------------------------------

SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

# Categóricos, en orden fijo. Nunca se ciclan.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")

# Un tono más fuera de la serie categórica, para las capas que necesitan
# distinguir una tercera cosa sin repetir el azul, el naranja o el verde: el
# módulo de estructura lleva un color por temporalidad.
VIOLET = "#6f4bd8"

# Los tonos de lo que dibuja el PROPIETARIO a mano encima del gráfico y no ha
# pintado el motor: sus recuadros y sus líneas. Quedan fuera de la serie
# categórica y del violeta de las temporalidades a propósito, para que no se
# confundan con ninguna capa calculada, y entre ellos van a hues separados
# (318°, 190° y 70°) para poder distinguirse.
MAGENTA = "#c0359a"
CYAN = "#0b8fa8"
OLIVE = "#7c8b12"

# Dos más de la mano para las líneas de SESIÓN —el alto y el bajo de Asia, el
# alto y el bajo de Londres—: cada sesión lleva el suyo y el alto y el bajo de
# la misma sesión comparten tono, que lo que los separa es el nombre. El marrón
# (28°) es oscuro y apagado para no confundirse con el naranja vivo del marco de
# H4, y el púrpura (286°) cae entre el violeta del Diario y el magenta.
BROWN = "#8a5a2b"
PURPLE = "#9440c8"

# Dos del MOTOR para el OB y el FVG que marca dentro del ID (K.1). Rellenos con
# transparencia, así que van saturados y oscuros: el ámbar (40°) queda lejos
# del naranja vivo del marco de H4 y del marrón de Asia, y el índigo (234°) es
# más oscuro y más azul que el azul de la serie. Ninguno lo usa la mano —sus
# recuadros son magenta y cian— para que un OB del motor no se confunda nunca
# con uno que ha marcado el propietario.
AMBER = "#cf8a00"
INDIGO = "#3d47b5"

# Polaridad (ganancia / pérdida): pareja divergente azul ↔ rojo con gris neutro.
POSITIVE = "#2a78d6"
NEGATIVE = "#d03b3b"
NEUTRAL = "#f0efec"

# Escala divergente para el mapa de calor mensual.
DIVERGING_SCALE = [
    [0.0, "#8f2323"],
    [0.25, "#d03b3b"],
    [0.5, NEUTRAL],
    [0.75, "#3987e5"],
    [1.0, "#184f95"],
]

FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'
