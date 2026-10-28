"""One SVG template for every tier -> SVG, PDF (svglib + reportlab) and PNG (pypdfium2).

The design is a portrait mobile ticket, 720 x 1280 user units. The PDF page has the same
proportions (540 x 960 pt) and the PNG is 1080 x 1920 px, a common phone screen.

Only the standard PDF fonts (Helvetica, Courier) are used, so nothing is downloaded and the PDF
needs no embedded font. Each line of text is its own <text> element, measured with reportlab's
font metrics (svglib mis-measures mixed <tspan> runs). Outputs are reproducible: the PDF is
written in reportlab's invariant mode (fixed dates and document id), and PNG files carry no
timestamp.

Crests: if `synthetic/crests/<club_id>.svg` exists (task S-11) it is used, otherwise a simple
placeholder shield is drawn with the club's initials in its home-kit colours. In the SVG file a
crest is embedded as an <image> with an SVG data URI, which keeps its ids and styles apart from the
ticket's. svglib cannot read SVG data URIs, so for the PDF each crest is converted on its own and
placed in the drawing at the same box. The statseat logo is a PNG: a data URI in the SVG, an image in the PDF.
"""

from __future__ import annotations

import base64
import io
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

import pypdfium2 as pdfium
import segno
from reportlab.graphics import renderPDF
from PIL import Image
from reportlab.graphics.shapes import Group, Image as DrawingImage
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from svglib.svglib import svg2rlg

from .registry import DISCLAIMER, SECTOR_TYPE_LABEL, TICKETS_VERSION, Club, MatchContext, Ticket

W, H = 720, 1280
PX_TO_PT = 0.75  # svglib: 1 user unit = 1 px = 0.75 pt
PNG_SCALE = 2.0  # 540 x 960 pt page -> 1080 x 1920 px
PNG_SIZE = (round(W * PX_TO_PT * PNG_SCALE), round(H * PX_TO_PT * PNG_SCALE))

SANS = "Helvetica, Arial, sans-serif"
MONO = "Courier, 'Courier New', monospace"

INK = "#0F1A2B"  # header and statseat box
LOGO_FILE = Path(__file__).resolve().parents[3] / "web" / "public" / "icon-512.png"
LOGO_SIZE, LOGO_XY = 52, (68, 990)
PAPER = "#FBF9F4"
TEXT = "#121826"
MUTED = "#5B6372"
LINE = "#DDD7CA"
ON_DARK = "#FFFFFF"
ON_DARK_MUTED = "#AEB7C6"

CARD = 16  # frame width around the card
RADIUS = 32
RIBBON_Y, RIBBON_H = 548, 60
PERFORATION_Y, NOTCH = 938, 16  # ticket-stub line and the half-circle bites at its ends
CREST_BOXES = {"home": (120, 170, 140, 160), "away": (460, 170, 140, 160)}  # x, y, w, h
QR_X, QR_Y, QR_SIZE = 466, 978, 198  # white tile; the quiet zone is inside it
QR_BORDER = 4  # modules of quiet zone


@dataclass(frozen=True)
class Palette:
    frame: tuple[tuple[float, str], ...]  # diagonal frame gradient
    ribbon: tuple[tuple[float, str], ...]  # horizontal ribbon gradient
    ribbon_text: str
    on_dark: str  # accent text on the dark header and box


PALETTES = {
    "gold": Palette(((0, "#7A5A12"), (0.3, "#C9972E"), (0.5, "#F7E7B0"), (0.7, "#D2A43D"), (1, "#8A6416")),
                    ((0, "#B8862B"), (0.5, "#F7E7B0"), (1, "#B8862B")), "#2A1E05", "#E9C46A"),
    "silver": Palette(((0, "#5F666E"), (0.3, "#AEB4BB"), (0.5, "#F4F6F8"), (0.7, "#B9BFC6"), (1, "#6C737B")),
                      ((0, "#A3AAB2"), (0.5, "#F4F6F8"), (1, "#A3AAB2")), "#161B22", "#D3D8DE"),
}

# Shield of the placeholder crest, in a 100 x 120 box.
SHIELD = "M0 0H100V58C100 92 74 110 50 120C26 110 0 92 0 58Z"

logging.getLogger("svglib.svglib").setLevel(logging.ERROR)  # unknown attributes such as aria-labelledby


# --- Text helpers ---------------------------------------------------------------------


def font_name(bold: bool = False, mono: bool = False) -> str:
    return ("Courier" if mono else "Helvetica") + ("-Bold" if bold else "")


def fit(text: str, size: float, max_width: float, bold: bool = False, mono: bool = False, minimum: float = 10) -> float:
    """The largest font size <= size at which `text` fits in max_width."""
    while size > minimum and stringWidth(text, font_name(bold, mono), size) > max_width:
        size -= 0.5
    return size


def text(x: float, y: float, s: str, size: float, fill: str, bold: bool = False, mono: bool = False,
         anchor: str = "start") -> str:
    weight = ' font-weight="bold"' if bold else ""
    anchor_attr = f' text-anchor="{anchor}"' if anchor != "start" else ""
    return (f'<text x="{x:g}" y="{y:g}" font-family="{MONO if mono else SANS}"{weight} font-size="{size:g}" '
            f'fill="{fill}"{anchor_attr}>{escape(s)}</text>')


def runs(cx: float, y: float, parts: list[tuple[str, float, str, bool]], gap: float) -> str:
    """Several runs (text, size, fill, bold) on one line, `gap` apart, centred on cx as a whole.

    Gaps are geometry, not spaces: SVG renderers collapse leading and repeated spaces.
    """
    widths = [stringWidth(t, font_name(b), s) for t, s, _, b in parts]
    x = cx - (sum(widths) + gap * (len(parts) - 1)) / 2
    out = []
    for (t, s, fill, b), w in zip(parts, widths):
        out.append(text(round(x, 2), y, t, s, fill, bold=b))
        x += w + gap
    return "".join(out)


def runs_width(parts: list[tuple[str, float, str, bool]], gap: float) -> float:
    return sum(stringWidth(t, font_name(b), s) for t, s, _, b in parts) + gap * (len(parts) - 1)


def gradient(gid: str, stops: tuple[tuple[float, str], ...], x2: float, y2: float) -> str:
    """A linear gradient over the bounding box of the shape (svglib and browsers agree on this mode)."""
    body = "".join(f'<stop offset="{o:g}" stop-color="{c}"/>' for o, c in stops)
    return f'<linearGradient id="{gid}" x1="0" y1="0" x2="{x2:g}" y2="{y2:g}">{body}</linearGradient>'


def paper_path() -> str:
    """The light lower part of the card: rounded bottom corners and two half-circle bites at the
    perforation, so the frame shows through them in every renderer."""
    left, right, bottom = CARD, W - CARD, H - CARD
    top, bite = PERFORATION_Y - NOTCH, PERFORATION_Y + NOTCH
    return (f"M{left} {RIBBON_Y}H{right}V{top}A{NOTCH} {NOTCH} 0 0 0 {right} {bite}"
            f"V{bottom - RADIUS}A{RADIUS} {RADIUS} 0 0 1 {right - RADIUS} {bottom}H{left + RADIUS}"
            f"A{RADIUS} {RADIUS} 0 0 1 {left} {bottom - RADIUS}V{bite}A{NOTCH} {NOTCH} 0 0 0 {left} {top}Z")


# --- Parts of the ticket ---------------------------------------------------------------


def crest_file(club: Club, crests_dir: Path | None) -> Path | None:
    if crests_dir is None:
        return None
    path = crests_dir / f"{club.id}.svg"
    return path if path.is_file() else None


def placeholder_crest(club: Club, box: tuple[float, float, float, float]) -> str:
    """A plain shield in the club's home-kit colours with its initials and founding year."""
    x, y, w, h = box
    scale = min(w / 100, h / 120) * 0.92
    sw, sh = 100 * scale, 120 * scale
    left, top = x + (w - sw) / 2, y + (h - sh) / 2
    cx = left + sw / 2
    initials_size = fit(club.initials, 46, sw * 0.7, bold=True)
    return (f'<g transform="translate({left:.2f} {top:.2f}) scale({scale:.4f})">'
            f'<path d="{SHIELD}" fill="{club.accent}"/>'
            f'<path d="{SHIELD}" transform="translate(6 6) scale(0.88)" fill="{club.shirt}"/></g>'
            + text(round(cx, 2), round(top + sh * 0.50, 2), club.initials, initials_size, club.accent, bold=True,
                   anchor="middle")
            + text(round(cx, 2), round(top + sh * 0.70, 2), str(club.founded), 14, club.accent, bold=True,
                   anchor="middle"))


def crest(club: Club, box: tuple[float, float, float, float], crests_dir: Path | None, for_pdf: bool) -> str:
    path = crest_file(club, crests_dir)
    if path is None:
        return placeholder_crest(club, box)
    if for_pdf:
        return ""  # placed into the PDF drawing by render_pdf
    x, y, w, h = box
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return (f'<image x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="xMidYMid meet" '
            f'href="data:image/svg+xml;base64,{data}"/>')


def qr_content(code: str, qr_url: str | None) -> str:
    return f"{qr_url}?ticket={quote(code)}" if qr_url else code


def qr_symbol(content: str) -> segno.QRCode:
    return segno.make(content, error="m", micro=False, boost_error=False)


def qr_geometry(qr: segno.QRCode) -> tuple[float, float, float]:
    """(x, y, module size) of the first module of the quiet zone, in SVG units."""
    modules = len(qr.matrix) + 2 * QR_BORDER
    return QR_X, QR_Y, QR_SIZE / modules


def qr_path(qr: segno.QRCode) -> str:
    """The dark modules as one path of horizontal runs (one path, so no seams between modules)."""
    x0, y0, m = qr_geometry(qr)
    d = []
    for r, row in enumerate(qr.matrix):
        c = 0
        while c < len(row):
            if row[c]:
                start = c
                while c < len(row) and row[c]:
                    c += 1
                x, y, w = x0 + (start + QR_BORDER) * m, y0 + (r + QR_BORDER) * m, (c - start) * m
                d.append(f"M{x:.2f} {y:.2f}h{w:.2f}v{m:.2f}h{-w:.2f}z")
            else:
                c += 1
    return f'<path d="{"".join(d)}" fill="{INK}"/>'


def logo_png() -> bytes:
    """statseat's logo (web/public/icon-512.png) at 4x the ticket size, on the box colour, so no transparency is
    needed in the PDF."""
    logo = Image.open(LOGO_FILE).convert("RGBA").resize((LOGO_SIZE * 4, LOGO_SIZE * 4), Image.LANCZOS)
    tile = Image.new("RGBA", logo.size, INK)
    tile.alpha_composite(logo)
    out = io.BytesIO()
    tile.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def logo_icon(x: float, y: float, for_pdf: bool) -> str:
    if for_pdf:
        return ""  # placed into the PDF drawing by render_pdf
    data = base64.b64encode(logo_png()).decode("ascii")
    return f'<image x="{x}" y="{y}" width="{LOGO_SIZE}" height="{LOGO_SIZE}" href="data:image/png;base64,{data}"/>'


def team_name(club: Club, cx: float) -> str:
    short_size = fit(club.short_name, 30, 300, bold=True)
    out = text(cx, 372, club.short_name, short_size, ON_DARK, bold=True, anchor="middle")
    if club.rest:
        out += text(cx, 402, club.rest, fit(club.rest, 21, 300), ON_DARK_MUTED, anchor="middle")
    return out


# --- The ticket -------------------------------------------------------------------------


def ticket_svg(ctx: MatchContext, ticket: Ticket, qr_text: str, crests_dir: Path | None, for_pdf: bool = False) -> str:
    tier = ticket.tier
    pal = PALETTES[tier.accent]
    seat = ticket.seat
    qr = qr_symbol(qr_text)
    cx = W / 2
    title = f"statseat ticket {ticket.ticket_id}"
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
        'aria-labelledby="title desc">\n',
        f'<title id="title">{escape(title)}</title>\n<desc id="desc">{escape(ticket.alt)}</desc>\n',
        "<defs>",
        gradient("frame", pal.frame, 1, 1),
        gradient("tier", pal.frame, 1, 1),
        gradient("ribbon", pal.ribbon, 1, 0),
        "</defs>\n",
        # Frame and card
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="url(#frame)"/>',
        # the dark top of the card; its bottom corners are hidden under the ribbon and the paper
        f'<rect x="{CARD}" y="{CARD}" width="{W - 2 * CARD}" height="{RIBBON_Y + RIBBON_H + RADIUS - CARD}" '
        f'rx="{RADIUS}" fill="{INK}"/>',
        f'<path d="{paper_path()}" fill="{PAPER}"/>\n',
    ]

    # Header: competition, match name, crests, teams, date and place
    comp = f"{ctx.competition.upper()} · MATCHDAY {ctx.matchday}"
    parts += [
        text(cx, 80, comp, fit(comp, 21, 620, bold=True), pal.on_dark, bold=True, anchor="middle"),
        text(cx, 138, ctx.match_name, fit(ctx.match_name, 50, 630, bold=True), ON_DARK, bold=True, anchor="middle"),
        crest(ctx.home, CREST_BOXES["home"], crests_dir, for_pdf),
        crest(ctx.away, CREST_BOXES["away"], crests_dir, for_pdf),
        text(cx, 264, "v", 34, ON_DARK_MUTED, bold=True, anchor="middle"),
        team_name(ctx.home, 190),
        team_name(ctx.away, 530),
        f'<rect x="64" y="432" width="592" height="1.5" fill="{ON_DARK}" fill-opacity="0.18"/>',
    ]
    when = f"{ctx.date_text} · Kick-off {ctx.time_text}"
    parts.append(text(cx, 476, when, fit(when, 25, 630, bold=True), ON_DARK, bold=True, anchor="middle"))
    size = 19.0
    place = lambda s: [(ctx.stadium, s, ON_DARK, True), ("·", s, ON_DARK_MUTED, False), (ctx.address, s, ON_DARK_MUTED, False)]  # noqa: E731
    while size > 12 and runs_width(place(size), size * 0.5) > 630:
        size -= 0.5
    parts.append(runs(cx, 512, place(size), size * 0.5))
    parts.append("\n")

    # Tier ribbon across the card
    label = tier.label.upper()
    parts += [
        f'<rect x="0" y="{RIBBON_Y}" width="{W}" height="{RIBBON_H}" fill="url(#ribbon)"/>',
        f'<rect x="0" y="{RIBBON_Y + RIBBON_H}" width="{W}" height="5" fill="#000000" fill-opacity="0.10"/>',
        text(cx, RIBBON_Y + 40, label, fit(label, 27, 620, bold=True), pal.ribbon_text, bold=True, anchor="middle"),
        "\n",
    ]

    # Seat block
    type_label = SECTOR_TYPE_LABEL.get(seat.sector_type, seat.sector_type.replace("_", " ").title())
    stand_note = f"{seat.side.title()} side" + (f" · {type_label}" if seat.sector_type != "home" else "")
    parts += [
        text(56, 662, "GATE", 15, MUTED, bold=True),
        text(200, 662, "STAND", 15, MUTED, bold=True),
        text(56, 728, seat.gate, 66, TEXT, bold=True),
        text(200, 726, seat.stand, fit(seat.stand, 42, 464, bold=True), TEXT, bold=True),
        text(200, 760, stand_note, fit(stand_note, 18, 464), MUTED),
    ]
    if ctx.gates_open_text:
        parts.append(text(56, 760, f"Opens {ctx.gates_open_text}", 18, MUTED))
    parts.append(f'<rect x="56" y="784" width="608" height="2" fill="{LINE}"/>')
    for x, lab, value in ((56, "SECTOR", seat.sector), (264, "ROW", str(seat.row)), (472, "SEAT", str(seat.seat))):
        parts.append(text(x, 826, lab, 15, MUTED, bold=True))
        parts.append(text(x, 896, value, fit(value, 70, 190, bold=True), TEXT, bold=True))
    parts.append("\n")

    # Perforation between the seat block and the statseat box (the bites are in the paper shape)
    parts.append(f'<path d="M{CARD + NOTCH + 20} {PERFORATION_Y}H{W - CARD - NOTCH - 20}" stroke="{LINE}" '
                 'stroke-width="2" stroke-dasharray="10 8" fill="none"/>\n')

    # statseat box: app name, code and QR code
    devices = "valid on several devices" if tier.multi_device else "valid on one device"
    hint = f"Scan or type it in the app · {devices}"
    code_size = fit(ticket.code, 26, 372, bold=True, mono=True)
    parts += [
        '<rect x="38" y="962" width="644" height="230" rx="26" fill="url(#tier)"/>',
        f'<rect x="40" y="964" width="640" height="226" rx="24" fill="{INK}"/>',
        logo_icon(*LOGO_XY, for_pdf),
        text(134, 1031, "statseat", 42, ON_DARK, bold=True),
        text(68, 1072, "Every stat from your seat", 17, ON_DARK_MUTED),
        text(68, 1112, "TICKET CODE", 13, pal.on_dark, bold=True),
        text(68, 1144, ticket.code, code_size, ON_DARK, bold=True, mono=True),
        text(68, 1172, hint, fit(hint, 14.5, 372), ON_DARK_MUTED),
        f'<rect x="{QR_X}" y="{QR_Y}" width="{QR_SIZE}" height="{QR_SIZE}" rx="10" fill="#FFFFFF"/>',
        qr_path(qr),
        "\n",
    ]

    # Footer
    first, _, second = DISCLAIMER.partition(". ")
    parts += [
        text(cx, 1226, first + ".", 15, MUTED, anchor="middle"),
        text(cx, 1248, second, 15, MUTED, anchor="middle"),
        "\n</svg>\n",
    ]
    return "".join(parts)


def svg_texts(svg: str) -> str:
    """Every human-readable string of an SVG (title, description, text), for the blocklist check."""
    root = ET.fromstring(svg.encode("utf-8"))
    return "\n".join("".join(el.itertext()) for el in root.iter()
                     if el.tag.rsplit("}", 1)[-1] in ("text", "title", "desc"))


def render_pdf(svg_for_pdf: str, crests: list[tuple[Path, tuple[float, float, float, float]]], title: str,
               subject: str) -> bytes:
    drawing = svg2rlg(io.BytesIO(svg_for_pdf.encode("utf-8")))
    for path, (x, y, w, h) in crests:
        art = svg2rlg(str(path), external_reference_root=str(path.parent))
        if art is None or not art.width or not art.height:
            raise ValueError(f"cannot read crest {path}")
        bw, bh = w * PX_TO_PT, h * PX_TO_PT
        s = min(bw / art.width, bh / art.height)
        group = Group(*art.contents)
        group.transform = (s, 0, 0, s, x * PX_TO_PT + (bw - art.width * s) / 2,
                           (H - y - h) * PX_TO_PT + (bh - art.height * s) / 2)
        drawing.add(group)
    x, y = LOGO_XY
    side = LOGO_SIZE * PX_TO_PT
    drawing.add(DrawingImage(x * PX_TO_PT, (H - y - LOGO_SIZE) * PX_TO_PT, side, side,
                             Image.open(io.BytesIO(logo_png()))))
    out = io.BytesIO()
    canvas = Canvas(out, pagesize=(drawing.width, drawing.height), invariant=1, pageCompression=1)
    canvas.setTitle(title)
    canvas.setAuthor("InsideTheGame synthetic data generator")
    canvas.setSubject(subject)
    canvas.setCreator(f"insidethegame_gen.tickets {TICKETS_VERSION}")
    renderPDF.draw(drawing, canvas, 0, 0)
    canvas.showPage()
    canvas.save()
    return out.getvalue()


def render_png(pdf: bytes) -> bytes:
    doc = pdfium.PdfDocument(pdf)
    try:
        page = doc[0]
        image = page.render(scale=PNG_SCALE).to_pil()
        page.close()
    finally:
        doc.close()
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_ticket(ctx: MatchContext, ticket: Ticket, qr_text: str, crests_dir: Path | None) -> dict[str, bytes]:
    """SVG, PDF and PNG bytes of one ticket."""
    svg = ticket_svg(ctx, ticket, qr_text, crests_dir)
    svg_for_pdf = ticket_svg(ctx, ticket, qr_text, crests_dir, for_pdf=True)
    crests = [(p, CREST_BOXES[side]) for side, club in (("home", ctx.home), ("away", ctx.away))
              if (p := crest_file(club, crests_dir)) is not None]
    pdf = render_pdf(svg_for_pdf, crests, f"statseat ticket {ticket.ticket_id}", ticket.alt)
    return {"svg": svg.encode("utf-8"), "pdf": pdf, "png": render_png(pdf)}
