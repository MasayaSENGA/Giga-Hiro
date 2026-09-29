document.addEventListener('DOMContentLoaded', function() {
  const gigafileLifetimeSelect = document.getElementById('gigafileLifetimeSelect');
  const gigaWatasuDaysSelect = document.getElementById('gigaWatasuDaysSelect');
  const saveButton = document.getElementById('saveButton');
  const statusDiv = document.getElementById('status');

  chrome.storage.sync.get(DEFAULT_SETTINGS, function(data) {
    gigafileLifetimeSelect.value = data.gigafileLifetime;
    gigaWatasuDaysSelect.value = data.gigaWatasuDays;
  });

  saveButton.addEventListener('click', function() {
    const settings = {
      gigafileLifetime: gigafileLifetimeSelect.value,
      gigaWatasuDays: gigaWatasuDaysSelect.value
    };
    chrome.storage.sync.set(settings, function() {
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
