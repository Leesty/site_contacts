/**
 * Предпросмотр видео с лёгкой предзагрузкой.
 *
 * - S3 URL берётся из data-s3-url (без AJAX к Django).
 * - Предзагрузка: только metadata (не полное видео!), макс. 3 вперёд.
 * - Кнопка становится btn-primary когда метаданные загружены.
 * - При клике: стримим видео, без фоновой буферизации всех файлов.
 *
 * Требования к HTML:
 *   - Кнопки: .js-video-preview[data-video-url][data-lead-id]
 *   - Опционально: data-s3-url (прямой S3 URL, встроен в шаблон)
 *   - Модалка: #videoPreviewModal с <video id="videoPreviewPlayer">
 *   - Опционально: #videoLeadId (span), #videoDownloadLink (a)
 */
(function () {
  'use strict';

  var modal = document.getElementById('videoPreviewModal');
  var player = document.getElementById('videoPreviewPlayer');
  if (!modal || !player) return;

  var leadIdSpan = document.getElementById('videoLeadId');
  var downloadLink = document.getElementById('videoDownloadLink');
  var buttons = document.querySelectorAll('.js-video-preview');
  if (!buttons.length) return;

  var MAX_PRELOAD = 3; // предзагружать metadata только для N ближайших
  var PRELOAD_DELAY = 3000;
  var preloadQueue = [];
  var preloading = false;
  var preloaded = 0;

  // ---------- CSS ----------

  var style = document.createElement('style');
  style.textContent = '@keyframes vpSpin{to{transform:rotate(360deg)}}';
  document.head.appendChild(style);

  // ---------- спиннер ----------

  var spinnerOverlay = document.createElement('div');
  spinnerOverlay.style.cssText =
    'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
    'background:rgba(0,0,0,.6);z-index:10;pointer-events:none;';
  spinnerOverlay.innerHTML =
    '<div style="width:48px;height:48px;border:4px solid rgba(255,255,255,.2);' +
    'border-top-color:#fff;border-radius:50%;animation:vpSpin .8s linear infinite;"></div>';

  var modalBody = player.parentElement;
  if (modalBody) modalBody.style.position = 'relative';

  function showSpinner() {
    if (modalBody && !modalBody.contains(spinnerOverlay)) {
      modalBody.appendChild(spinnerOverlay);
    }
  }

  function hideSpinner() {
    if (spinnerOverlay.parentElement) spinnerOverlay.remove();
  }

  // ---------- резолв URL ----------

  var urlCache = {};

  function getVideoUrl(btn, cb) {
    var s3url = btn.getAttribute('data-s3-url');
    if (s3url) return cb(s3url);

    var djangoUrl = btn.getAttribute('data-video-url');
    if (urlCache[djangoUrl]) return cb(urlCache[djangoUrl]);

    fetch(djangoUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var url = data.url || djangoUrl;
        urlCache[djangoUrl] = url;
        cb(url);
      })
      .catch(function () {
        urlCache[djangoUrl] = djangoUrl;
        cb(djangoUrl);
      });
  }

  // ---------- лёгкая предзагрузка (только metadata, макс. N штук) ----------

  function preloadNext() {
    if (preloading || preloadQueue.length === 0 || preloaded >= MAX_PRELOAD) return;
    preloading = true;

    var item = preloadQueue.shift();

    getVideoUrl(item.btn, function (url) {
      // Резолвим URL и кэшируем, но НЕ создаём hidden video
      // (это позволяет убрать задержку при клике на кнопку, без фоновой загрузки гигабайтов)
      markReady(item.btn);
      preloaded++;
      preloading = false;
      setTimeout(preloadNext, PRELOAD_DELAY);
    });
  }

  function markReady(btn) {
    btn.classList.remove('btn-outline-primary');
    btn.classList.add('btn-primary');
  }

  // ---------- открытие видео ----------

  function openVideo(url, leadId) {
    if (leadIdSpan) leadIdSpan.textContent = leadId || '';
    if (downloadLink) downloadLink.href = url;

    showSpinner();

    player.preload = 'auto';
    player.src = url;
    player.autoplay = true;

    var bsModal = bootstrap.Modal.getOrCreateInstance(modal);
    bsModal.show();

    var playPromise = player.play();
    if (playPromise && playPromise.catch) {
      playPromise.catch(function () {});
    }
  }

  player.addEventListener('canplay', function () {
    hideSpinner();
    var p = player.play();
    if (p && p.catch) p.catch(function () {});
  });
  player.addEventListener('error', hideSpinner);

  // ---------- инициализация ----------

  buttons.forEach(function (btn) {
    preloadQueue.push({ btn: btn });

    btn.addEventListener('click', function () {
      var leadId = btn.getAttribute('data-lead-id') || '';
      getVideoUrl(btn, function (url) {
        openVideo(url, leadId);
      });
    });
  });

  // Старт предзагрузки URL через 1 сек после загрузки страницы
  setTimeout(preloadNext, 1000);

  // ---------- очистка при закрытии ----------

  modal.addEventListener('hidden.bs.modal', function () {
    player.pause();
    player.removeAttribute('src');
    player.autoplay = false;
    player.load();
    hideSpinner();
  });
})();
