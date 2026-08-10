const DEFAULT_LIFETIME = '5'; // content.js のデフォルトと一致させること

document.addEventListener('DOMContentLoaded', function() {
  const lifetimeSelect = document.getElementById('lifetimeSelect');
  const saveButton = document.getElementById('saveButton');
  const statusDiv = document.getElementById('status');

  chrome.storage.sync.get({ gigafileLifetime: DEFAULT_LIFETIME }, function(data) {
    lifetimeSelect.value = data.gigafileLifetime;
  });

  saveButton.addEventListener('click', function() {
    const selectedValue = lifetimeSelect.value;
    chrome.storage.sync.set({ gigafileLifetime: selectedValue }, function() {
      if (chrome.runtime.lastError) {
        statusDiv.textContent = '保存に失敗しました: ' + chrome.runtime.lastError.message;
        statusDiv.style.color = '#ef4444';
      } else {
        statusDiv.textContent = '設定を保存しました！';
        statusDiv.style.color = '';
      }
      setTimeout(function() {
        statusDiv.textContent = '';
      }, 3000);
    });
  });
});
