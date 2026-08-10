const DEFAULT_LIFETIME = '5'; // popup.js のデフォルトと一致させること

chrome.storage.sync.get({ gigafileLifetime: DEFAULT_LIFETIME }, function(data) {
  const selectedLifetime = String(data.gigafileLifetime);
  const selector = `li[data-lifetime-val="${CSS.escape(selectedLifetime)}"]`;

  function trySelect() {
    const targetLi = document.querySelector(selector);
    if (targetLi) {
      targetLi.click();
      console.log(`ギガファイル便: 保持期限を ${selectedLifetime} 日に設定しました。`);
      return true;
    }
    return false;
  }

  if (trySelect()) {
    return;
  }

  // 選択リストが遅れて描画されるページに備え、一定時間だけ DOM の変化を監視する
  const observer = new MutationObserver(function() {
    if (trySelect()) {
      observer.disconnect();
      clearTimeout(timeoutId);
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });

  const timeoutId = setTimeout(function() {
    observer.disconnect();
    console.warn(`ギガファイル便: 指定された保持期限 ${selectedLifetime} 日の要素が見つかりませんでした。`);
  }, 10000);
});
