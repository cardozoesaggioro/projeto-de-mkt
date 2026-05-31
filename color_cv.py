# -*- coding: utf-8 -*-
"""
color_cv.py — O resolvedor de cor que mata a "lama".

Problema clássico: tirar a média dos pixels de várias imagens gera um marrom-
lama confiante e errado. A solução aqui:
  1. Converte tudo para CIELAB (espaço perceptualmente uniforme).
  2. Clusteriza por proximidade perceptual (ΔE, limiar = 18).
  3. Escolhe a cor que RECORRE entre imagens (cobertura), não a mais frequente
     num único banner.
  4. A DISPERSÃO do cluster vira o sinal de confiança: lama = dispersão alta =
     confiança baixa. NUNCA devolvemos uma cor errada com confiança alta.

Stdlib pura (sem numpy). ΔE = distância euclidiana em Lab (CIE76, suficiente
para clustering de paleta).
"""
from __future__ import annotations

from dataclasses import dataclass

RGB = tuple[int, int, int]
LAB = tuple[float, float, float]

CLUSTER_THRESH = 18.0  # ΔE máximo para duas cores caírem no mesmo cluster


# ---------------------------------------------------------------------------
# Conversão sRGB -> CIELAB (D65)
# ---------------------------------------------------------------------------
def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb: RGB) -> LAB:
    """Converte um RGB 0..255 em CIELAB. Determinístico, sem dependências."""
    r, g, b = (_srgb_to_linear(v) for v in rgb)

    # Linear RGB -> XYZ (matriz sRGB D65)
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505

    # Normaliza pelo branco de referência D65
    x, y, z = x / 0.95047, y / 1.00000, z / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)

    fx, fy, fz = f(x), f(y), f(z)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    bb = 200 * (fy - fz)
    return (L, a, bb)


def delta_e(c1: LAB, c2: LAB) -> float:
    """ΔE CIE76 — distância euclidiana em Lab."""
    return ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2 + (c1[2] - c2[2]) ** 2) ** 0.5


def lab_to_rgb_hex(lab: LAB) -> str:
    """Inverte Lab -> RGB -> #hex (para devolver a cor do centroide)."""
    L, a, b = lab
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200

    def finv(t: float) -> float:
        return t ** 3 if t ** 3 > 0.008856 else (t - 16 / 116) / 7.787

    x = finv(fx) * 0.95047
    y = finv(fy) * 1.00000
    z = finv(fz) * 1.08883

    # XYZ -> linear RGB
    r = x * 3.2406 + y * -1.5372 + z * -0.4986
    g = x * -0.9689 + y * 1.8758 + z * 0.0415
    bb = x * 0.0557 + y * -0.2040 + z * 1.0570

    def lin_to_srgb(c: float) -> int:
        c = max(0.0, min(1.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
        return round(max(0.0, min(1.0, c)) * 255)

    return "#{:02X}{:02X}{:02X}".format(lin_to_srgb(r), lin_to_srgb(g), lin_to_srgb(bb))


# ---------------------------------------------------------------------------
# Clustering perceptual
# ---------------------------------------------------------------------------
@dataclass
class ColorCluster:
    centroid: LAB
    members: list[LAB]
    image_ids: set[int]   # de quais imagens vieram os membros (-> cobertura)
    weight: float         # soma dos pesos (proeminência) dos membros

    def recompute_centroid(self) -> None:
        n = len(self.members)
        self.centroid = (
            sum(m[0] for m in self.members) / n,
            sum(m[1] for m in self.members) / n,
            sum(m[2] for m in self.members) / n,
        )

    @property
    def dispersion(self) -> float:
        """Dispersão normalizada [0,1]: ΔE médio ao centroide / limiar."""
        if len(self.members) <= 1:
            return 0.0
        mean_de = sum(delta_e(m, self.centroid) for m in self.members) / len(self.members)
        return min(1.0, mean_de / CLUSTER_THRESH)


@dataclass
class ColorResult:
    hex: str
    dispersion: float   # sinal -> scorer
    coverage: float     # fração de imagens onde a cor recorre -> scorer
    n_images: int
    n_total_images: int
    alternatives: list[str]


def resolve_brand_color(
    images: list[list[tuple[RGB, float]]],
) -> ColorResult | None:
    """
    images: lista de imagens; cada imagem é uma lista de (rgb, peso), onde peso
            é a proeminência da cor naquela imagem (ex.: fração de pixels).
    Retorna a cor de marca que RECORRE entre imagens, com dispersão e cobertura.
    """
    n_total = len(images)
    if n_total == 0:
        return None

    clusters: list[ColorCluster] = []

    # Clustering incremental por proximidade perceptual (ΔE <= limiar)
    for img_id, palette in enumerate(images):
        for rgb, weight in palette:
            lab = rgb_to_lab(rgb)
            best: ColorCluster | None = None
            best_de = CLUSTER_THRESH
            for cl in clusters:
                de = delta_e(lab, cl.centroid)
                if de <= best_de:
                    best, best_de = cl, de
            if best is None:
                clusters.append(ColorCluster(lab, [lab], {img_id}, weight))
            else:
                best.members.append(lab)
                best.image_ids.add(img_id)
                best.weight += weight
                best.recompute_centroid()

    if not clusters:
        return None

    # A cor de marca = a que recorre em MAIS imagens; desempate por peso.
    # (cobertura entre imagens vence frequência num único banner)
    clusters.sort(key=lambda c: (len(c.image_ids), c.weight), reverse=True)
    winner = clusters[0]

    coverage = len(winner.image_ids) / n_total
    alternatives = [lab_to_rgb_hex(c.centroid) for c in clusters[1:4]]

    return ColorResult(
        hex=lab_to_rgb_hex(winner.centroid),
        dispersion=round(winner.dispersion, 4),
        coverage=round(coverage, 4),
        n_images=len(winner.image_ids),
        n_total_images=n_total,
        alternatives=alternatives,
    )


# ---------------------------------------------------------------------------
# Extração de paleta de um ARQUIVO de imagem (CV real, via Pillow)
# ---------------------------------------------------------------------------
def palette_from_image_bytes(
    data: bytes, max_colors: int = 6, resize: int = 160, drop_near_white: bool = True,
) -> list[tuple[RGB, float]]:
    """
    Extrai a paleta dominante de uma imagem (bytes) como [(rgb, peso)], onde o
    peso é a fração de pixels daquela cor. É a entrada para resolve_brand_color.

    Determinístico: redimensiona, quantiza (mediana de cortes) e conta pixels.
    Import do Pillow é PREGUIÇOSO — o módulo segue importável sem a dependência.

    # NOTA: descarta quase-branco/quase-preto por padrão (fundos/texto) para não
    #   poluir a cor de marca; mantém se a imagem for majoritariamente neutra.
    """
    from io import BytesIO

    try:
        from PIL import Image
    except ImportError as exc:  # Pillow ausente -> erro claro (connector trata)
        raise RuntimeError("Pillow não instalado (pip install pillow)") from exc

    img = Image.open(BytesIO(data)).convert("RGB")
    img.thumbnail((resize, resize))
    quant = img.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT)
    pal = quant.getpalette() or []
    counts = quant.getcolors() or []  # [(count, index), ...]
    total = sum(c for c, _ in counts) or 1

    palette: list[tuple[RGB, float]] = []
    for count, idx in counts:
        r, g, b = pal[idx * 3 : idx * 3 + 3]
        if drop_near_white and (min(r, g, b) > 238 or max(r, g, b) < 16):
            continue  # fundo branco / texto preto
        palette.append(((r, g, b), round(count / total, 4)))

    # se filtramos tudo (imagem neutra), devolve as cores cruas mesmo
    if not palette:
        for count, idx in counts:
            r, g, b = pal[idx * 3 : idx * 3 + 3]
            palette.append(((r, g, b), round(count / total, 4)))
    return palette
