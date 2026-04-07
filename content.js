chrome.storage.sync.get('gigafileLifetime', function(data) {
  const selectedLifetime = data.gigafileLifetime || '30';

  const targetLi = document.querySelector(`li[data-lifetime-val="${selectedLifetime}"]`);

  if (targetLi) {
    targetLi.click();
    console.log(`ギガファイル便: 保持期限を ${selectedLifetime} 日に設定しました。`);
  } else {
    console.warn(`ギガファイル便: 指定された保持期限 ${selectedLifetime} 日の要素が見つかりませんでした。`);
  }
});