"""Render the Chrome Web Store images for ギガひろ.

    python store-assets/tools/compose/render_assets.py
    python store-assets/tools/compose/render_assets.py --out-root <dir>      # write elsewhere (store-assets/ untouched)
    python store-assets/tools/compose/render_assets.py --preview-dir <dir>   # also write 640x400 / half-size previews
    python store-assets/tools/compose/render_assets.py --only 03-gigafile.html
    python store-assets/tools/compose/render_assets.py --browser <chrome.exe> --font <NotoSansJP-VF.ttf>
    python store-assets/tools/compose/render_assets.py --tmp-dir <dir>       # temp Chrome profile / 2x captures

Needs Python 3.9+, Pillow, numpy, Google Chrome and the Noto Sans JP variable font
(default C:\\Windows\\Fonts\\NotoSansJP-VF.ttf, or the GIGAHIRO_FONT environment variable).
Outputs, overwritten under --out-root (default: store-assets/):
    screenshots/01-hero.png ... 05-privacy.png                1280x800 RGB
    promo/small-tile-440x280.png, promo/marquee-1400x560.png  RGB
    icon/store-icon-128.png                                   128x128 RGBA
Intermediate files go to store-assets/tools/compose/build/ (git-ignored).

Steps
  1. prepare(): crop the raw captures (store-assets/raw, deviceScaleFactor=2) into
     build/*.png. Crops only - no pixel of the third-party UI is repainted. The selected
     option is located by pixel analysis (red cell on ギガファイル便, cyan button on
     ギガワタス) and written to build/geometry.js, which common.js uses to place rings.
     The font file is referenced from build/font.css, which common.css imports.
  2. render(): every HTML page is laid out at its final CSS size, captured by headless
     Chrome at deviceScaleFactor=2 and downsampled with LANCZOS to the exact size.
     Screenshots / tiles are flattened to 24-bit RGB.
  3. store icon: images/icon128.png -> bbox of alpha>0, i.e. (2,2)-(126,126) -> 96x96
     LANCZOS -> centred on a transparent 128x128 canvas (16px margin).
     Note: the source has a faint alpha 1-2 fringe at x/y=2 and 125 (ringing from its own
     downscale); it is inside that bbox and stays invisible (alpha<=4) in the output.
  4. verify(): sizes and modes of every output.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
ASSETS = TOOLS.parent
REPO = ASSETS.parent
RAW = ASSETS / "raw"
BUILD = HERE / "build"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEFAULT_FONT = os.environ.get("GIGAHIRO_FONT", r"C:\Windows\Fonts\NotoSansJP-VF.ttf")

sys.path.insert(0, str(TOOLS))
import cdp  # noqa: E402

DPR = 2

PAGES = [
    # (html, output relative to --out-root, (w, h)) - screenshots and tiles are saved as RGB
    ("01-hero.html", Path("screenshots") / "01-hero.png", (1280, 800)),
    ("02-popup.html", Path("screenshots") / "02-popup.png", (1280, 800)),
    ("03-gigafile.html", Path("screenshots") / "03-gigafile.png", (1280, 800)),
    ("04-gigawatasu.html", Path("screenshots") / "04-gigawatasu.png", (1280, 800)),
    ("05-privacy.html", Path("screenshots") / "05-privacy.png", (1280, 800)),
    ("small-tile.html", Path("promo") / "small-tile-440x280.png", (440, 280)),
    ("marquee.html", Path("promo") / "marquee-1400x560.png", (1400, 560)),
]
STORE_ICON = Path("icon") / "store-icon-128.png"

FOOTNOTE = "※非公式ツールです。ギガファイル便・ギガワタスの運営会社とは関係ありません。"
# approved copy - compared character for character (whitespace/line breaks ignored).
# Every headline / sub-headline must match the table in listing.md. The 05 headline is worded per
# service ("ギガファイル便とギガワタス") because the manifest has 3 match patterns incl. the old gigafile.jp.
EXPECTED = {
    "01-hero.html": {".headline": "開くだけで、いつもの日数に",
                     ".sub": "ギガファイル便の保持期限とギガワタスの共有期限を自動選択", ".footnote": FOOTNOTE},
    "02-popup.html": {".headline": "日数はサービスごとに設定",
                      ".sub": "選んで「設定を保存」するだけ。Chromeの同期にも対応", ".footnote": FOOTNOTE},
    "03-gigafile.html": {".headline": "ギガファイル便は開けば選択済み",
                         ".sub": "画面を開くと、設定した保持期限が選ばれた状態に", ".footnote": FOOTNOTE,
                         ".fig-cap": "ギガひろで保持期限を14日に設定した場合"},
    "04-gigawatasu.html": {".headline": "ギガワタスは画面を移っても選択",
                           ".sub": "サイト内でアップロード画面へ移るたびに、共有期限を選択", ".footnote": FOOTNOTE,
                           ".fig-cap": "ギガひろで共有期限を30日に設定した場合"},
    "05-privacy.html": {".headline": "動くのはギガファイル便とギガワタスだけ",
                        ".sub": "権限は設定の保存と対象サイトへのアクセスだけ。外部送信・収集なし", ".footnote": FOOTNOTE},
    "small-tile.html": {".name": "ギガひろ", ".tag": "いつもの日数を自動で選択"},
    "marquee.html": {".name": "ギガひろ", ".tagline": "開くだけで、いつもの日数を自動で選択",
                     ".support": "ギガファイル便・ギガワタス対応（非公式ツール）"},
}


def show(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# pixel analysis helpers
# ---------------------------------------------------------------------------
def load(name: str) -> tuple[Image.Image, np.ndarray]:
    im = Image.open(RAW / name).convert("RGB")
    return im, np.asarray(im).astype(int)


def near(a: np.ndarray, rgb, tol: int) -> np.ndarray:
    return np.abs(a - np.array(rgb)).max(axis=-1) <= tol


def runs(mask_1d: np.ndarray) -> list[tuple[int, int]]:
    """[(start, end_exclusive)] of True runs."""
    out, start = [], None
    for i, v in enumerate(mask_1d):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask_1d)))
    return out


def bbox(mask: np.ndarray) -> list[int]:
    ys, xs = np.where(mask)
    if not len(xs):
        raise RuntimeError("empty mask")
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def largest_block(mask: np.ndarray, min_count: int = 20) -> list[int]:
    """bbox of the dense block (rows/cols with at least min_count hits)."""
    cols = np.where(mask.sum(axis=0) >= min_count)[0]
    rows = np.where(mask.sum(axis=1) >= min_count)[0]
    if not len(cols) or not len(rows):
        raise RuntimeError("no dense block")
    return [int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1]


def shift(box, dx, dy):
    return [box[0] - dx, box[1] - dy, box[2] - dx, box[3] - dy]


GF_TAB_BLUE = (26, 115, 232)   # underline of the tab row above the gigafile panel
GF_PANEL = (40, 114, 159)      # gigafile panel / page background
GF_BAR = (68, 68, 68)          # dark retention bar
GF_RED = (231, 76, 60)         # selected retention cell
GF_DROP = (240, 245, 255)      # light drop area below the retention bar
WT_CYAN = (19, 172, 213)       # selected giga-watasu day button
WHITE = (255, 255, 255)


def gf_red_cell(a: np.ndarray, y_limit: int) -> list[int]:
    sub = a[:y_limit]
    m = (sub[..., 0] > 180) & (sub[..., 1] < 110) & (sub[..., 2] < 100)  # red + darker red edge
    return bbox(m)


def prepare(font: Path) -> dict:
    BUILD.mkdir(parents=True, exist_ok=True)
    geom: dict = {}

    def save(name: str, im: Image.Image, boxes: dict, **extra):
        im.save(BUILD / f"{name}.png", optimize=True)
        geom[name] = {"w": im.width, "h": im.height, "boxes": boxes, **extra}

    # --- ギガファイル便 upload panel (14日 selected) ---------------------------------
    # Retention bar + the first three lines of the drop area ("ここにファイルを…",
    # "フォルダごと…", "1ファイル300Gまで、個数無制限"). The line after them is the
    # site's own promotion of its ad-free plan (with a link) and is cut off, as are the
    # form fields and buttons below it.
    im, a = load("gigafile_after_upload_panel_crop.png")
    mid = a.shape[1] // 2
    top_blue = runs(near(a[:, mid], GF_TAB_BLUE, 6))
    assert top_blue and top_blue[0][0] == 0, "expected tab underline at the top edge"
    y0 = top_blue[0][1]                                    # cut the tab underline
    probe_y = a.shape[0] // 2
    white_cols = runs(near(a[probe_y], WHITE, 4))
    x0 = white_cols[0][0]                                  # left white frame of the panel
    x1 = white_cols[-1][1]                                 # right white frame of the panel
    side = x0 + 40                                         # inside the drop area, left of its centred text
    drop_rows = [r for r in runs(near(a[:, side], GF_DROP, 4)) if r[0] > y0]
    drop = drop_rows[0]                                    # first light area under the bar
    drop_cols = runs(near(a[drop[0] + 8], GF_DROP, 4))
    dx0, dx1 = drop_cols[0][0], drop_cols[-1][1]
    ink = ~near(a[drop[0]:drop[1], dx0 + 8:dx1 - 8], GF_DROP, 4)
    lines = [(drop[0] + s0, drop[0] + s1) for s0, s1 in runs(ink.any(axis=1))]
    assert len(lines) == 4, f"expected 3 text lines + the promotion line in the drop area, got {lines}"
    gap = lines[3][0] - lines[2][1]
    y1 = lines[2][1] + min(26, gap - 10)                   # ~13 CSS px below the 3rd line
    assert lines[2][1] < y1 < lines[3][0] - 8, (lines, y1)
    cell = gf_red_cell(a, drop[0])
    crop = im.crop((x0, y0, x1, y1))
    save("gf_panel_after", crop, {"cell": shift(cell, x0, y0)},
         src="gigafile_after_upload_panel_crop.png", crop=[x0, y0, x1, y1])

    # --- ギガファイル便 retention bar only (before / after) --------------------------
    for tag in ("after", "before"):
        im, a = load(f"gigafile_{tag}_lifetime_crop.png")
        mid = a.shape[1] // 2
        col = a[:, mid]
        blue = runs(near(col, GF_TAB_BLUE, 6))
        assert blue, "tab underline not found"
        white_after_blue = [r for r in runs(near(col, WHITE, 4)) if r[0] >= blue[0][1]]
        panel_top = white_after_blue[0][1]                  # first row inside the panel frame
        # dark bar rows / columns
        bar = near(a, GF_BAR, 6)
        bx = largest_block(bar, 10)
        # label (white box left of the bar) rows
        probe_y = (bx[1] + bx[3]) // 2
        row_white = runs(near(a[probe_y], WHITE, 4))
        frame_l = row_white[0]                              # left frame of the panel
        label = [r for r in row_white if r[0] > frame_l[1] and r[1] <= bx[0]][0]
        # light drop area below the bar
        drop = runs(near(col, GF_DROP, 4))
        drop_top = [r for r in drop if r[0] > bx[3]][0][0]
        pad = label[0] - frame_l[1]                         # the site's own inner padding (20px)
        cx0 = frame_l[1]
        cx1 = min(bx[2] + pad, a.shape[1])
        vpad = min(bx[1] - panel_top, drop_top - bx[3])
        cy0, cy1 = bx[1] - vpad, bx[3] + vpad
        assert cy0 >= panel_top and cy1 <= drop_top
        cell = gf_red_cell(a, drop_top)
        crop = im.crop((cx0, cy0, cx1, cy1))
        save(f"gf_bar_{tag}", crop, {"cell": shift(cell, cx0, cy0), "bar": shift(bx, cx0, cy0)},
             src=f"gigafile_{tag}_lifetime_crop.png", crop=[cx0, cy0, cx1, cy1])

    # --- ギガワタス settings block after in-site navigation (30日 selected) ---------
    # From the 共有期限 label down to the パスワード保護 input. The block heading and the
    # ラベル名 field above it, and ダウンロード回数制限 / ワンタイムダウンロード below it,
    # are left out, so the selected day is the top of the capture (as on screen 3) and
    # the chip + wire can stay outside the capture.
    im, a = load("watasu_spa_after_settings_crop.png")
    nonwhite = ~near(a, WHITE, 6)
    content = bbox(nonwhite)
    p = 24
    btn = largest_block(near(a, WT_CYAN, 12))
    row_runs = runs(nonwhite[:, content[0] + 40:].any(axis=1))   # skip the block's left rule
    above = [r for r in row_runs if r[1] <= btn[1]]
    below = [r for r in row_runs if r[0] >= btn[3]]
    label = above[-1]                                       # 共有期限 label
    assert btn[1] - label[1] < 60, (label, btn)
    assert len(below) >= 2, below
    pw_input = below[1]                                     # [0] パスワード保護 label, [1] its input
    cx0, cx1 = max(content[0] - p, 0), min(content[2] + p, a.shape[1])
    cy0 = label[0] - p
    cy1 = pw_input[1] + p
    assert cy0 > above[-2][1] and (len(below) < 3 or cy1 < below[2][0]), (cy0, cy1, row_runs)
    crop = im.crop((cx0, cy0, cx1, cy1))
    save("wt_settings_spa", crop, {"cell": shift(btn, cx0, cy0)},
         src="watasu_spa_after_settings_crop.png", crop=[cx0, cy0, cx1, cy1])

    # --- ギガワタス 共有期限 row (hero card) ----------------------------------------
    for tag, src in (("after", "watasu_after_days_crop.png"),):
        im, a = load(src)
        content = bbox(~near(a, WHITE, 6))
        p = 20
        cx0, cy0 = max(content[0] - p, 0), max(content[1] - p, 0)
        cx1, cy1 = min(content[2] + p, a.shape[1]), min(content[3] + p, a.shape[0])
        btn = largest_block(near(a, WT_CYAN, 12))
        crop = im.crop((cx0, cy0, cx1, cy1))
        save(f"wt_days_{tag}", crop, {"cell": shift(btn, cx0, cy0)}, src=src, crop=[cx0, cy0, cx1, cy1])

    # --- popup (our own UI) --------------------------------------------------------
    im, a = load("popup_saved.png")
    midx = a.shape[1] // 2
    card = bbox(near(a, WHITE, 0))
    border = runs(near(a[:, midx], (203, 213, 225), 3))      # select borders (2 rows each)
    assert len(border) >= 4, border
    dd1 = [None, border[0][0], None, border[1][1]]
    dd2 = [None, border[2][0], None, border[3][1]]
    row = (dd1[1] + dd1[3]) // 2
    brow = runs(near(a[row], (203, 213, 225), 3))
    dd1[0], dd1[2] = brow[0][0], brow[-1][1]
    dd2[0], dd2[2] = dd1[0], dd1[2]
    save_btn = largest_block(near(a, (59, 130, 246), 3), 20)
    g = a[..., 1]
    green = (g > 150) & (a[..., 0] < 120) & (a[..., 2] < 160) & (g - a[..., 0] > 60)
    msg = bbox(green)
    shutil.copyfile(RAW / "popup_saved.png", BUILD / "popup_saved.png")
    geom["popup_saved"] = {"w": im.width, "h": im.height,
                           "boxes": {"card": card, "dd1": dd1, "dd2": dd2, "save": save_btn, "msg": msg},
                           "src": "popup_saved.png"}

    # --- icon ------------------------------------------------------------------------
    shutil.copyfile(REPO / "images" / "icon128.png", BUILD / "icon128.png")

    # --- font (imported by common.css) -------------------------------------------------
    (BUILD / "font.css").write_text(
        "/* generated by render_assets.py (--font / GIGAHIRO_FONT) */\n"
        "@font-face {\n"
        '  font-family: "GH Sans";\n'
        f'  src: url("{font.resolve().as_uri()}") format("truetype");\n'
        "  font-weight: 100 900;\n"
        "  font-display: block;\n"
        "}\n", encoding="utf-8")

    (BUILD / "geometry.js").write_text(
        "// generated by render_assets.py - pixel boxes are [x0, y0, x1, y1) in each build/*.png\n"
        "window.GEOM = " + json.dumps(geom, ensure_ascii=False, indent=1) + ";\n",
        encoding="utf-8")
    return geom


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def render(pages, out_root: Path, browser_path: str, preview_dir: Path | None = None,
           tmp_dir: Path | None = None) -> list[str]:
    problems: list[str] = []
    tmp_root = Path(tmp_dir or tempfile.gettempdir())
    tmp_root.mkdir(parents=True, exist_ok=True)
    profile = tmp_root / "gigahiro-compose-profile"
    cdp.kill_leftovers(str(profile))
    shutil.rmtree(profile, ignore_errors=True)
    browser = cdp.Browser(browser_path, str(profile), extra_args=[
        "--allow-file-access-from-files", "--hide-scrollbars", "--force-color-profile=srgb",
        "--disable-lcd-text", "--font-render-hinting=none", "--disable-gpu",
        "--disable-default-apps", "--disable-extensions", "--disable-sync",
    ])
    try:
        for html, rel_out, (w, h) in pages:
            out = out_root / rel_out
            s = browser.new_page(width=w, height=h, dpr=DPR)
            try:
                s.navigate((HERE / html).as_uri())
                s.wait_for("window.__READY === true", timeout=30)
                issues = s.evaluate("window.__ISSUES") or []
                notes = s.evaluate("window.__NOTES") or []
                for sel, want in EXPECTED.get(html, {}).items():
                    got = s.evaluate(f"(document.querySelector({json.dumps(sel)})||{{}}).textContent")
                    got = "".join((got or "").split())
                    if got != want:
                        issues.append(f"copy mismatch in {sel}: {got!r} != {want!r}")
                size = s.evaluate("[document.querySelector('.canvas').offsetWidth, document.querySelector('.canvas').offsetHeight]")
                if list(size) != [w, h]:
                    issues.append(f"canvas is {size}, expected {[w, h]}")
                tmp = tmp_root / f"gigahiro-{Path(html).stem}@2x.png"
                s.screenshot(str(tmp))
            finally:
                browser.close_page(s)
            big = Image.open(tmp).convert("RGB")
            if big.size != (w * DPR, h * DPR):
                issues.append(f"capture is {big.size}")
            final = big.resize((w, h), Image.LANCZOS).convert("RGB")
            out.parent.mkdir(parents=True, exist_ok=True)
            final.save(out, optimize=True)
            tmp.unlink(missing_ok=True)
            status = "OK" if not issues else "ISSUES"
            print(f"[{status}] {show(out)}  {final.size} {final.mode}")
            for n in notes:
                print("       ", n)
            for i in issues:
                print("   !!", i)
                problems.append(f"{html}: {i}")
            if preview_dir:
                preview_dir.mkdir(parents=True, exist_ok=True)
                pw, ph = (640, 400) if (w, h) == (1280, 800) else (w // 2, h // 2)
                final.resize((pw, ph), Image.LANCZOS).save(preview_dir / f"preview-{out.stem}-{pw}x{ph}.png")
    finally:
        browser.close()
        cdp.kill_leftovers(str(profile))
        for _ in range(40):                         # Chrome may hold file locks briefly
            shutil.rmtree(profile, ignore_errors=True)
            if not profile.exists():
                break
            time.sleep(0.25)
    return problems


def make_store_icon(out_root: Path):
    src = Image.open(REPO / "images" / "icon128.png").convert("RGBA")
    box = src.getchannel("A").getbbox()            # opaque artwork bounds
    art = src.crop(box)
    art = art.resize((96, 96), Image.LANCZOS)      # Pillow resamples RGBA premultiplied
    out = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    out.paste(art, (16, 16))                       # plain copy: canvas is fully transparent
    path = out_root / STORE_ICON
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path, optimize=True)
    print(f"[OK] {show(path)}  {out.size} {out.mode}  (artwork bbox {box} -> 96x96 @16,16)")


def verify(out_root: Path, pages) -> list[str]:
    bad = []
    icon = out_root / STORE_ICON
    expect = [(out_root / out, size, "RGB") for _, out, size in pages] + [(icon, (128, 128), "RGBA")]
    for path, size, mode in expect:
        im = Image.open(path)
        ok = im.size == size and im.mode == mode
        if path == icon and ok:
            alpha = np.asarray(im.getchannel("A"))
            margins_clear = alpha[:16].max() == 0 and alpha[-16:].max() == 0 and alpha[:, :16].max() == 0 and alpha[:, -16:].max() == 0
            ok = margins_clear and im.getchannel("A").getbbox() == (16, 16, 112, 112)
        print(f"  verify {'ok ' if ok else 'BAD'} {show(path)} {im.size} {im.mode}")
        if not ok:
            bad.append(str(path))
    return bad


def main():
    ap = argparse.ArgumentParser(description="Render the Chrome Web Store images for Giga-Hiro.")
    ap.add_argument("--out-root", type=Path, default=ASSETS,
                    help="write screenshots/, promo/ and icon/ under this folder (default: store-assets/, overwritten)")
    ap.add_argument("--browser", default=CHROME, help=f"Chrome executable (default: {CHROME})")
    ap.add_argument("--font", type=Path, default=Path(DEFAULT_FONT),
                    help="Noto Sans JP variable font (default: GIGAHIRO_FONT or C:\\Windows\\Fonts\\NotoSansJP-VF.ttf)")
    ap.add_argument("--preview-dir", type=Path, default=None, help="also write 640x400 / half-size previews here")
    ap.add_argument("--only", nargs="*", help="render only these html files")
    ap.add_argument("--tmp-dir", type=Path, default=None, help="temporary files (default: system temp)")
    args = ap.parse_args()
    if not Path(args.browser).is_file():
        sys.exit(f"Chrome not found: {args.browser} (use --browser)")
    if not args.font.is_file():
        sys.exit(f"font not found: {args.font} (use --font or GIGAHIRO_FONT)")
    out_root = args.out_root.resolve()
    geom = prepare(args.font)
    for k, v in geom.items():
        print(f"  geometry {k}: {v['w']}x{v['h']} {v['boxes']}")
    pages = [p for p in PAGES if not args.only or p[0] in args.only]
    if args.only and len(pages) != len(args.only):
        sys.exit(f"unknown page in --only: {sorted(set(args.only) - {p[0] for p in PAGES})}")
    t = time.time()
    problems = render(pages, out_root, args.browser, args.preview_dir, args.tmp_dir)
    make_store_icon(out_root)
    bad = verify(out_root, pages)
    print(f"done in {time.time() - t:.1f}s")
    if problems or bad:
        print("FAILED:", *problems, *bad, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
