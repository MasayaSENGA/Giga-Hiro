// ギガワタスは SPA なので、ページ遷移でスクリプトが再読み込みされない。
// そのため「サイト全体に常駐して、URL と DOM の変化を監視し続ける」方式にする。

const UPLOAD_PATH = '/upload';
const CLICK_COOLDOWN_MS = 400; // 連続クリック防止
const MAX_CLICKS_PER_SCREEN = 5; // 万一クラス判定が効かない場合の暴走防止

let targetDays = null; // 設定の読み込みが終わるまでは選択しない
let done = false; // 現在の画面で選択済みか
let clickCount = 0;
let lastClickAt = 0;
let debounceTimer = null;
let retryTimer = null;

function isUploadScreen() {
  return location.pathname.replace(/\/+$/, '') === UPLOAD_PATH;
}

function isSelected(button) {
  return button.classList.contains('text-white') || button.classList.contains('bg-brand-light');
}

function clickTargetButton() {
  const label = `${targetDays}日`;
  for (const button of document.querySelectorAll('button')) {
    if (button.textContent.trim() !== label) continue;

    // すでに選択状態なら何もしない
    if (isSelected(button)) return true;

    const now = Date.now();
    if (now - lastClickAt < CLICK_COOLDOWN_MS) return false;
    if (clickCount >= MAX_CLICKS_PER_SCREEN) return true;

    lastClickAt = now;
    clickCount++;
    button.click();
    console.log(`ギガワタス: 保存日数を ${targetDays} 日に設定しました。`);
    return true;
  }
  return false;
}

function ensureSelection() {
  if (done || targetDays === null || !isUploadScreen()) return;
  if (clickTargetButton()) done = true;
}

// 画面が切り替わったら、また選択しに行く
function handleNavigation() {
  done = false;
  clickCount = 0;
  clearInterval(retryTimer);

  if (!isUploadScreen()) return;

  // 遷移直後は DOM がまだ描かれていないことがあるので、少しの間くり返し試す
  ensureSelection();
  let elapsed = 0;
  retryTimer = setInterval(() => {
    elapsed += 200;
    if (done || elapsed >= 15000 || !isUploadScreen()) {
      clearInterval(retryTimer);
      return;
    }
    ensureSelection();
  }, 200);
}

// --- URL の変化を検知する ---
// content script は isolated world で動くため、history.pushState を上書きしても
// ページ側の遷移は拾えない。popstate / hashchange に加え、DOM の変化時と
// 定期ポーリングで URL を確認する。
let lastUrl = location.href;

function onUrlMaybeChanged() {
  if (location.href === lastUrl) return;
  lastUrl = location.href;
  handleNavigation();
}

window.addEventListener('popstate', onUrlMaybeChanged);
window.addEventListener('hashchange', onUrlMaybeChanged);
setInterval(onUrlMaybeChanged, 500);

// --- DOM の変化を常時監視（監視は切らない：後続の画面遷移にも対応するため） ---
// 描画のたびに走らせないよう、変化はまとめて処理する
function scheduleCheck() {
  if (debounceTimer !== null) return;
  debounceTimer = setTimeout(() => {
    debounceTimer = null;
    onUrlMaybeChanged();
    ensureSelection();
  }, 50);
}

const observer = new MutationObserver(scheduleCheck);
observer.observe(document.documentElement, { childList: true, subtree: true });

// --- 設定変更を即反映（拡張機能のポップアップで保存した直後から効くように） ---
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'sync' || !changes.gigaWatasuDays) return;
  const newDays = String(changes.gigaWatasuDays.newValue || DEFAULT_SETTINGS.gigaWatasuDays);
  // ポップアップは両サービスの値をまとめて保存するため、初回保存時は日数が同じでも変更通知が来る。
  // 日数が変わっていなければ、手動で選び直した日数を上書きしないよう何もしない
  if (newDays === targetDays) return;
  targetDays = newDays;
  handleNavigation(); // 新しい日数で選び直す
});

// 初期化
chrome.storage.sync.get({ gigaWatasuDays: DEFAULT_SETTINGS.gigaWatasuDays }, (data) => {
  targetDays = String(data.gigaWatasuDays);
  handleNavigation();
});
