"""Capture raw (un-composited) Chrome Web Store screenshot material for Giga-Hiro.

    python store-assets/tools/capture_raw.py                 # real extension (default)
    python store-assets/tools/capture_raw.py --method isolated   # fallback, see below

Method "extension" (default):
    Headless Chrome (--headless=new) is started with --remote-debugging-port and
    --enable-unsafe-extension-debugging, and the repository is installed with the
    CDP command Extensions.loadUnpacked. The real popup (chrome-extension://<id>/popup.html)
    is used to save the settings, and the real content scripts do the selection.

Method "isolated" (fallback when loadUnpacked is unavailable):
    The extension's own settings.js + content script are evaluated unchanged in an
    isolated world (Page.createIsolatedWorld) with a chrome.storage stub that returns
    the showcase values. The popup is opened from file:// with the same stub.

"Before" shots are always taken before the extension is loaded (plain browser, fresh
profile), so they show the site's own default.

Safety: nothing is uploaded, no form is submitted, no login / consent click. The only
clicks are the popup's own save button, the retention-day selection performed by the
extension, and one in-site navigation link on giga-watasu.jp (top page -> /upload).

Output: store-assets/raw/*.png (deviceScaleFactor=2) + store-assets/raw/manifest.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(TOOLS_DIR, "..", ".."))
DEFAULT_OUT = os.path.join(REPO, "store-assets", "raw")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

VIEW_W, VIEW_H, DPR = 1280, 800, 2
GIGAFILE_URL = "https://gigafile.nu/"
WATASU_TOP_URL = "https://giga-watasu.jp/"
WATASU_UPLOAD_URL = "https://giga-watasu.jp/upload"

log: dict = {"shots": [], "console": [], "notes": []}


def note(msg: str):
    print(msg)
    log["notes"].append(msg)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def settle(s: cdp.Session, extra: float = 2.5):
    try:
        s.evaluate("document.fonts ? document.fonts.ready.then(() => true) : true", timeout=15)
    except Exception:
        pass
    time.sleep(extra)


def new_page(b: cdp.Browser) -> cdp.Session:
    s = b.new_page(width=VIEW_W, height=VIEW_H, dpr=DPR)
    s.send("Emulation.setScrollbarsHidden", {"hidden": True})
    return s


def record_console(b: cdp.Browser, since: int, where: str):
    out = []
    for ev in b.cdp.events[since:]:
        if ev.get("method") != "Runtime.consoleAPICalled":
            continue
        args = ev["params"].get("args", [])
        text = " ".join(str(a.get("value", a.get("description", ""))) for a in args)
        if text.startswith(("ギガファイル便:", "ギガワタス:")):
            out.append(text)
    for t in out:
        log["console"].append({"where": where, "text": t})
    return out


def viewport_shot(s: cdp.Session, out_dir: str, name: str, desc: str) -> str:
    path = os.path.join(out_dir, name)
    s.screenshot(path)
    add_shot(path, desc)
    return path


def crop_shot(src: str, rect: dict, pad, out_dir: str, name: str, desc: str) -> str:
    """Crop a region (CSS px, viewport-relative) out of a viewport PNG.

    pad: a number, or (left, top, right, bottom) in CSS px.
    """
    pl, pt, pr, pb = (pad,) * 4 if isinstance(pad, (int, float)) else pad
    img = Image.open(src)
    x0 = max(0, int(round((rect["x"] - pl) * DPR)))
    y0 = max(0, int(round((rect["y"] - pt) * DPR)))
    x1 = min(img.width, int(round((rect["x"] + rect["width"] + pr) * DPR)))
    y1 = min(img.height, int(round((rect["y"] + rect["height"] + pb) * DPR)))
    path = os.path.join(out_dir, name)
    img.crop((x0, y0, x1, y1)).save(path, optimize=True)
    add_shot(path, desc, crop_box_px=[x0, y0, x1, y1], source=os.path.basename(src))
    return path


def add_shot(path: str, desc: str, **extra):
    with Image.open(path) as im:
        w, h = im.size
    log["shots"].append(dict(file=os.path.basename(path), width=w, height=h, dpr=DPR,
                             description=desc, **extra))
    print(f"  saved {os.path.basename(path)} ({w}x{h})")


def rect_of(s: cdp.Session, js_element: str) -> dict:
    return s.evaluate(f"(() => {{ const e = {js_element}; if (!e) return null; "
                      "const r = e.getBoundingClientRect(); "
                      "return {x: r.x, y: r.y, width: r.width, height: r.height}; })()")


# ---------------------------------------------------------------------------
# isolated-world fallback (method B)
# ---------------------------------------------------------------------------
def storage_stub_js(values: dict) -> str:
    return """
(() => {
  const store = %s;
  const listeners = [];
  const pick = (keys) => {
    if (keys == null) return Object.assign({}, store);
    if (typeof keys === 'string') keys = [keys];
    if (Array.isArray(keys)) { const o = {}; for (const k of keys) if (k in store) o[k] = store[k]; return o; }
    const o = {}; for (const k of Object.keys(keys)) o[k] = (k in store) ? store[k] : keys[k]; return o;
  };
  const sync = {
    get(keys, cb) { const r = pick(keys); if (cb) { setTimeout(() => cb(r), 0); return; } return Promise.resolve(r); },
    set(obj, cb) {
      const changes = {};
      for (const k of Object.keys(obj)) { changes[k] = {oldValue: store[k], newValue: obj[k]}; store[k] = obj[k]; }
      setTimeout(() => { for (const f of listeners) f(changes, 'sync'); if (cb) cb(); }, 0);
      if (!cb) return Promise.resolve();
    }
  };
  globalThis.chrome = globalThis.chrome || {};
  globalThis.chrome.storage = { sync, onChanged: { addListener(f) { listeners.push(f); } } };
  globalThis.chrome.runtime = globalThis.chrome.runtime || {};
  return true;
})();
""" % json.dumps(values)


def read_src(name: str) -> str:
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def inject_isolated(s: cdp.Session, values: dict, content_script: str) -> int:
    frame_id = s.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
    ctx = s.send("Page.createIsolatedWorld",
                 {"frameId": frame_id, "worldName": "giga-hiro-content-script"})["executionContextId"]
    s.evaluate(storage_stub_js(values), context_id=ctx)
    s.evaluate(read_src("settings.js"), context_id=ctx, await_promise=False)
    s.evaluate(read_src(content_script), context_id=ctx, await_promise=False)
    return ctx


# ---------------------------------------------------------------------------
# site specific
# ---------------------------------------------------------------------------
GF_STATE_JS = """(() => {
  const lis = [...document.querySelectorAll('li[data-lifetime-val]')];
  const t = document.getElementById('lifetime_text');
  const m = document.getElementById('lifetime_meter');
  return {options: lis.map(li => li.dataset.lifetimeVal), text: t ? t.textContent.trim() : null,
          meter: m ? m.getAttribute('style') : null};
})()"""

WS_STATE_JS = r"""(() => {
  const btns = [...document.querySelectorAll('button')].filter(b => /^\d+日$/.test(b.textContent.trim()));
  const sel = btns.filter(b => b.classList.contains('text-white') || b.classList.contains('bg-brand-light'));
  return {path: location.pathname, options: btns.map(b => b.textContent.trim()),
          selected: sel.map(b => b.textContent.trim())};
})()"""

WS_BUTTON_JS = r"[...document.querySelectorAll('button')].find(b => /^\d+日$/.test(b.textContent.trim()))"


GF_INPANEL_AD_JS = """(() => { const a = document.querySelector('#contents_upload .insert_ads');
  return !!a && a.getBoundingClientRect().height > 0; })()"""

# Returns '' when every given element is visible (not covered by a modal / overlay ad) at
# several sample points, otherwise a short reason. Elements are passed as JS expressions.
COVERED_JS = """(() => { const els = [%s];
  for (const e of els) { if (!e) return 'element missing';
    const r = e.getBoundingClientRect();
    for (const [fx, fy] of [[0.5, 0.5], [0.08, 0.5], [0.92, 0.5]]) {
      const h = document.elementFromPoint(r.x + r.width * fx, r.y + r.height * fy);
      if (!h || !e.contains(h)) return 'covered by ' + (h ? h.tagName + '#' + h.id + '.' + String(h.className).slice(0, 40) : 'nothing');
    } }
  return ''; })()"""

GF_CHECK = ["document.getElementById('lifetime_meter_box')", "document.getElementById('file_list')"]
WS_CHECK = [f"({WS_BUTTON_JS}).parentElement", f"({WS_BUTTON_JS}).closest('details')"]


def covered(s: cdp.Session, element_exprs: list[str]) -> str:
    return s.evaluate(COVERED_JS % ", ".join(element_exprs))


def clear_viewport_shot(s: cdp.Session, out_dir: str, name: str, desc: str, element_exprs: list[str]):
    """Viewport screenshot, kept only if the target UI is not covered before and after it."""
    why = covered(s, element_exprs)
    if why:
        return None, why
    path = os.path.join(out_dir, name)
    tmp = path + ".tmp.png"
    s.screenshot(tmp)
    why = covered(s, element_exprs)
    if why:
        os.remove(tmp)
        return None, why
    os.replace(tmp, path)
    add_shot(path, desc)
    return path, ""


def gigafile_capture(s: cdp.Session, out_dir: str, state: str, expect: str | None,
                     values: dict | None = None, method: str = "extension", attempts: int = 8):
    # gigafile.nu sometimes (a) inserts a 250px ad block inside the upload panel above the
    # lifetime bar, or (b) shows a full-page "コンテンツを見る / 短い広告を見る" ad modal.
    # Neither is clicked or removed: the page is simply reloaded (site data cleared) until
    # the selector UI is visible and unobstructed. The page itself is never modified.
    label = "拡張なし（サイト既定）" if state == "before" else f"ギガひろが {expect}日 を自動選択"
    vp, st, rejected = None, None, []
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            s.send("Storage.clearDataForOrigin", {"origin": "https://gigafile.nu", "storageTypes": "all"})
        s.navigate(GIGAFILE_URL)
        s.wait_for("document.querySelectorAll('li[data-lifetime-val]').length > 0", timeout=20)
        if method == "isolated" and values:
            inject_isolated(s, values, "content-gigafile.js")
        if expect:
            s.wait_for(f"(document.getElementById('lifetime_text')||{{}}).textContent === '{expect}日'",
                       timeout=15)
        settle(s)
        in_panel_ad = s.evaluate(GF_INPANEL_AD_JS)
        last = attempt == attempts
        if in_panel_ad and not last:
            rejected.append("in-panel ad block")
            print(f"  attempt {attempt}: in-panel ad block, reloading")
            continue
        st = s.evaluate(GF_STATE_JS)
        # anchor the scroll on the lifetime bar (150px from the top = upload box with its
        # tabs fully visible when there is no in-panel ad)
        s.evaluate("(() => { const m = document.getElementById('lifetime_meter_box'); "
                   "window.scrollTo(0, m.getBoundingClientRect().top + scrollY - 150); })()")
        time.sleep(1.5)
        vp, why = clear_viewport_shot(
            s, out_dir, f"gigafile_{state}_viewport.png",
            f"ギガファイル便 gigafile.nu アップロード欄までスクロールした 1280x800 ビューポート全体 / {label} / "
            f"保持期限表示={st['text']}（右側・下端に第三者の固定広告あり）", GF_CHECK)
        if vp:
            break
        rejected.append(why)
        print(f"  attempt {attempt}: selector UI obstructed ({why}), reloading")
    if not vp:
        raise RuntimeError(f"gigafile {state}: could not get an unobstructed capture: {rejected}")
    st.update(in_panel_ad=in_panel_ad, load_attempts=attempt, rejected_loads=rejected)
    r = rect_of(s, "document.getElementById('lifetime_meter_box')")
    crop_shot(vp, r, 24, out_dir, f"gigafile_{state}_lifetime_crop.png",
              f"ギガファイル便 保持期限変更バー（#lifetime_meter_box, ul#lifetime_divisions の親）を余白24pxでクロップ / {label}")
    panel = rect_of(s, "document.getElementById('contents_upload')")
    cap = rect_of(s, "document.querySelector('#contents_upload .upload_panle_caution')")
    top = r["y"] - 14
    bottom = min((cap["y"] + cap["height"]) if cap else panel["y"] + panel["height"], 700 - 14) + 14
    crop_shot(vp, {"x": panel["x"], "y": top, "width": panel["width"], "height": bottom - top}, (12, 0, 12, 0),
              out_dir, f"gigafile_{state}_upload_panel_crop.png",
              f"ギガファイル便 アップロードパネル（保持期限バー〜ドロップ欄〜ファイル選択ボタン）をクロップ / {label}")
    if not in_panel_ad:
        box = rect_of(s, "document.getElementById('contents_box')")
        box_crop = dict(box)
        box_crop["height"] = min(box["height"], 700 - box["y"])  # stay above the fixed bottom overlay ad
        crop_shot(vp, box_crop, 12, out_dir, f"gigafile_{state}_upload_box_crop.png",
                  f"ギガファイル便 アップロード枠全体（#contents_box：新着情報・タブ・保持期限バー・ドロップ欄）をクロップ / {label}")
    else:
        note(f"gigafile {state}: in-panel ad was present on every load; upload_box crop skipped")
    return st


def watasu_scroll_to_settings(s: cdp.Session):
    s.evaluate("(() => { const b = %s; const d = b.closest('details') || b.parentElement.parentElement; "
               "const r = d.getBoundingClientRect(); "
               "window.scrollTo(0, r.bottom + scrollY - innerHeight + 48); })()" % WS_BUTTON_JS)
    time.sleep(1.2)


def watasu_crops(s: cdp.Session, vp: str, out_dir: str, state: str, label: str):
    r = rect_of(s, f"({WS_BUTTON_JS}).parentElement.parentElement")
    # left padding kept small so the settings block's left border line stays out of the crop
    crop_shot(vp, r, (16, 24, 24, 24), out_dir, f"watasu_{state}_days_crop.png",
              f"ギガワタス「共有期限」の日数ボタン群を余白24pxでクロップ / {label}")
    d = rect_of(s, f"({WS_BUTTON_JS}).closest('details')")
    if d:
        crop_shot(vp, d, 20, out_dir, f"watasu_{state}_settings_crop.png",
                  f"ギガワタス「アップロード設定」ブロック全体をクロップ / {label}")


def watasu_capture(s: cdp.Session, out_dir: str, state: str, expect: str | None,
                   values: dict | None = None, method: str = "extension", attempts: int = 4):
    label = "拡張なし（サイト既定）" if state == "before" else f"ギガひろが {expect}日 を自動選択"
    vp, rejected = None, []
    for attempt in range(1, attempts + 1):
        s.navigate(WATASU_UPLOAD_URL)
        s.wait_for(f"!!({WS_BUTTON_JS})", timeout=20)
        if method == "isolated" and values:
            inject_isolated(s, values, "content-gigawatasu.js")
        if expect:
            s.wait_for(f"JSON.stringify(({WS_STATE_JS}).selected) === '[\"{expect}日\"]'", timeout=20)
        settle(s)
        st = s.evaluate(WS_STATE_JS)
        watasu_scroll_to_settings(s)
        vp, why = clear_viewport_shot(
            s, out_dir, f"watasu_{state}_viewport.png",
            f"ギガワタス giga-watasu.jp/upload アップロード設定までスクロールした 1280x800 ビューポート全体 / "
            f"{label} / 選択中={st['selected']}（左に第三者の広告枠あり）", WS_CHECK)
        if vp:
            break
        rejected.append(why)
        print(f"  attempt {attempt}: settings UI obstructed ({why}), reloading")
    if not vp:
        raise RuntimeError(f"watasu {state}: could not get an unobstructed capture: {rejected}")
    st.update(load_attempts=attempt, rejected_loads=rejected)
    watasu_crops(s, vp, out_dir, state, label)
    return st


def watasu_spa_capture(b: cdp.Browser, s: cdp.Session, out_dir: str, expect: str,
                       values: dict | None = None, method: str = "extension", attempts: int = 3):
    rejected = []
    top_path = os.path.join(out_dir, "watasu_spa_before_top_viewport.png")
    for attempt in range(1, attempts + 1):
        s.navigate(WATASU_TOP_URL)
        s.wait_for("!!document.querySelector('a[href=\"/upload\"]')", timeout=20)
        if method == "isolated" and values:
            inject_isolated(s, values, "content-gigawatasu.js")
        settle(s, 1.5)
        s.screenshot(top_path + ".tmp.png")
        link = s.evaluate("(() => { const a = [...document.querySelectorAll('a[href=\"/upload\"]')][0]; "
                          "return a ? a.textContent.trim() : null; })()")
        m = b.cdp.mark()
        # follow the in-site link (navigation only; nothing is submitted)
        s.evaluate("document.querySelectorAll('a[href=\"/upload\"]')[0].click(); true")
        s.wait_for("location.pathname === '/upload'", timeout=20)
        s.wait_for(f"!!({WS_BUTTON_JS})", timeout=20)
        s.wait_for(f"JSON.stringify(({WS_STATE_JS}).selected) === '[\"{expect}日\"]'", timeout=20)
        kinds = [ev["method"] for ev in b.cdp.events[m:]
                 if ev.get("sessionId") == s.session_id and ev.get("method") in
                 ("Page.navigatedWithinDocument", "Page.frameNavigated")]
        spa = "Page.navigatedWithinDocument" in kinds and "Page.frameNavigated" not in kinds
        settle(s, 1.5)
        st = s.evaluate(WS_STATE_JS)
        watasu_scroll_to_settings(s)
        label = f"トップページのリンク「{link}」から SPA 遷移した直後、ギガひろが {expect}日 を自動選択"
        vp, why = clear_viewport_shot(s, out_dir, "watasu_spa_after_viewport.png",
                                      f"ギガワタス {label}（1280x800 ビューポート全体）/ 選択中={st['selected']}",
                                      WS_CHECK)
        if vp:
            break
        os.remove(top_path + ".tmp.png")
        rejected.append(why)
        print(f"  attempt {attempt}: settings UI obstructed ({why}), retrying")
    if not vp:
        raise RuntimeError(f"watasu SPA: could not get an unobstructed capture: {rejected}")
    os.replace(top_path + ".tmp.png", top_path)
    add_shot(top_path, "ギガワタス トップページ giga-watasu.jp/（1280x800 ファーストビュー）。ギガひろ有効。"
                       f"ここからヘッダーの「{link}」リンクで /upload へ SPA 遷移する直前")
    # keep the manifest in chronological order (top page before the /upload shot)
    log["shots"].insert(len(log["shots"]) - 2, log["shots"].pop())
    watasu_crops(s, vp, out_dir, "spa_after", label)
    return {"link_text": link, "navigation_events": kinds, "spa": spa, "state": st,
            "load_attempts": attempt, "rejected_loads": rejected}


# ---------------------------------------------------------------------------
# popup
# ---------------------------------------------------------------------------
def popup_capture(s: cdp.Session, out_dir: str, popup_url: str, values: dict, before_reopen=None):
    body = "document.body"

    def shot(name, desc):
        tmp = os.path.join(out_dir, "_popup_tmp.png")
        s.screenshot(tmp)
        r = rect_of(s, body)
        crop_shot(tmp, r, 0, out_dir, name, desc)
        os.remove(tmp)
        log["shots"][-1].pop("source", None)

    # 1) choose the showcase values in the real popup UI and press 「設定を保存」
    s.navigate(popup_url)
    s.wait_for("document.getElementById('gigafileLifetimeSelect').value !== ''")
    settle(s, 0.5)
    s.evaluate("""(() => {
      const set = (id, v) => { const e = document.getElementById(id); e.value = v;
                               e.dispatchEvent(new Event('change', {bubbles: true})); };
      set('gigafileLifetimeSelect', %s); set('gigaWatasuDaysSelect', %s); return true; })()"""
               % (json.dumps(values["gigafileLifetime"]), json.dumps(values["gigaWatasuDays"])))
    s.evaluate("document.getElementById('saveButton').click(); true")
    s.wait_for("document.getElementById('status').textContent === '設定を保存しました！'", timeout=5)
    time.sleep(0.3)
    shot("popup_saved.png",
         f"ポップアップ「ギガひろの設定」: ギガファイル便 {values['gigafileLifetime']}日 / ギガワタス "
         f"{values['gigaWatasuDays']}日 を選び「設定を保存」を押した直後（『設定を保存しました！』表示）。body 320px をクロップ")
    time.sleep(3.2)  # message disappears after 3 s

    # 2) reopen: values are loaded back from chrome.storage.sync
    if before_reopen:
        before_reopen()
    s.navigate(popup_url)
    s.wait_for("document.getElementById('gigafileLifetimeSelect').value === %s && "
               "document.getElementById('gigaWatasuDaysSelect').value === %s"
               % (json.dumps(values["gigafileLifetime"]), json.dumps(values["gigaWatasuDays"])))
    settle(s, 0.8)
    shot("popup_loaded.png",
         f"ポップアップ「ギガひろの設定」を開き直し、保存済みの設定（ギガファイル便 {values['gigafileLifetime']}日 / "
         f"ギガワタス {values['gigaWatasuDays']}日）が読み込まれた状態。body 320px をクロップ")
    return s.evaluate("({g: document.getElementById('gigafileLifetimeSelect').value, "
                      "w: document.getElementById('gigaWatasuDaysSelect').value})")


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--method", choices=["extension", "isolated", "auto"], default="auto",
                    help="auto = try Extensions.loadUnpacked, fall back to isolated world")
    ap.add_argument("--browser", default=CHROME, help="browser executable")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--gigafile-days", default="14")
    ap.add_argument("--watasu-days", default="30")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--profile-dir", default=None, help="temporary Chrome user-data-dir")
    args = ap.parse_args()

    values = {"gigafileLifetime": args.gigafile_days, "gigaWatasuDays": args.watasu_days}
    os.makedirs(args.out, exist_ok=True)
    tmp_root = args.profile_dir or tempfile.mkdtemp(prefix="gigahiro-capture-")
    udd = os.path.join(tmp_root, "chrome-profile")
    shutil.rmtree(udd, ignore_errors=True)

    log.update(started=dt.datetime.now().isoformat(timespec="seconds"), values=values,
               viewport=[VIEW_W, VIEW_H], dpr=DPR)
    b = cdp.Browser(args.browser, udd, ["--enable-unsafe-extension-debugging"],
                    headless=not args.headed)
    try:
        log["browser"] = b.send("Browser.getVersion")
        s = new_page(b)

        # ---- before: plain browser, extension not loaded yet -------------------------
        print("[before] gigafile.nu")
        log["gigafile_before"] = gigafile_capture(s, args.out, "before", None)
        print("[before] giga-watasu.jp/upload")
        log["watasu_before"] = watasu_capture(s, args.out, "before", None)
        # start the "after" part without any site state left from the "before" visit
        for origin in ("https://gigafile.nu", "https://giga-watasu.jp"):
            s.send("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"})
        b.close_page(s)

        # ---- load the real extension -----------------------------------------------
        method = args.method
        ext_id = None
        if method in ("extension", "auto"):
            try:
                ext_id = b.send("Extensions.loadUnpacked", {"path": REPO})["id"]
                method = "extension"
                note(f"Extensions.loadUnpacked OK: id={ext_id}")
            except cdp.CDPError as e:
                if method == "extension":
                    raise
                note(f"Extensions.loadUnpacked failed ({e}); falling back to isolated world")
                method = "isolated"
        log["method"] = method
        log["extension_id"] = ext_id

        # ---- popup -----------------------------------------------------------------
        print("[popup]")
        p = new_page(b)
        reopen_hook = None
        if method == "extension":
            popup_url = f"chrome-extension://{ext_id}/popup.html"
        else:
            # the stub starts from the extension defaults; after 「設定を保存」 the page is
            # reopened with the saved values, like chrome.storage.sync would return them
            sid = p.send("Page.addScriptToEvaluateOnNewDocument",
                         {"source": storage_stub_js({"gigafileLifetime": "5", "gigaWatasuDays": "7"})})

            def reopen_hook():
                p.send("Page.removeScriptToEvaluateOnNewDocument", {"identifier": sid["identifier"]})
                p.send("Page.addScriptToEvaluateOnNewDocument", {"source": storage_stub_js(values)})
            popup_url = "file:///" + os.path.join(REPO, "popup.html").replace("\\", "/")
        log["popup_values_after_reload"] = popup_capture(p, args.out, popup_url, values, reopen_hook)
        b.close_page(p)

        # ---- after: extension active ---------------------------------------------------
        m = b.cdp.mark()
        print("[after] giga-watasu.jp top -> /upload (SPA link)")
        s = new_page(b)
        log["watasu_spa"] = watasu_spa_capture(b, s, args.out, values["gigaWatasuDays"], values, method)
        log["watasu_spa"]["content_script_console"] = record_console(b, m, "watasu_spa")
        s.send("Storage.clearDataForOrigin", {"origin": "https://giga-watasu.jp", "storageTypes": "all"})
        b.close_page(s)

        m = b.cdp.mark()
        print("[after] giga-watasu.jp/upload")
        s = new_page(b)
        log["watasu_after"] = watasu_capture(s, args.out, "after", values["gigaWatasuDays"], values, method)
        log["watasu_after"]["content_script_console"] = record_console(b, m, "watasu_after")
        b.close_page(s)

        m = b.cdp.mark()
        print("[after] gigafile.nu")
        s = new_page(b)
        log["gigafile_after"] = gigafile_capture(s, args.out, "after", values["gigafileLifetime"], values, method)
        log["gigafile_after"]["content_script_console"] = record_console(b, m, "gigafile_after")
        b.close_page(s)
    finally:
        b.close()
        left = cdp.kill_leftovers(udd)
        if left:
            note(f"killed {left} leftover browser processes")
        if not args.profile_dir:
            shutil.rmtree(tmp_root, ignore_errors=True)
        log["finished"] = dt.datetime.now().isoformat(timespec="seconds")
        with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)
    print("done ->", args.out)


if __name__ == "__main__":
    main()
