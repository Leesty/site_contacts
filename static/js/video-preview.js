/**
 * Моментальный предпросмотр видео: фоновая предзагрузка через скрытые <video>,
 * мгновенное воспроизведение при клике, автоплей без задержек.
 *
 * Логика:
 *   1. При загрузке страницы — резолвим S3-URL и создаём скрытые <video preload="auto">
 *      (по 2 параллельно, чтобы не забить канал).
 *   2. Кнопка становится btn-primary когда видео полностью прогрузилось.
 *   3. При клике — если видео в кеше → моментальный autoplay. Если ещё грузится → стримим.
 *
 * Требования к HTML:
 *   - Кнопки: .js-video-preview[data-video-url][data-lead-id]
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

  // Кэш: djangoUrl → s3Url
  var urlCache = {};
  // Кэш: s3Url → скрытый <video> (предзагруженный)
  var videoCache = {};
  // Очередь кнопок для предзагрузки
  var preloadQueue = [];
  var activePreloads = 0;
  var MAX_PARALLEL = 2;

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

  // ---------- резолв S3-URL ----------

  var pendingFetches = {};

  function resolveS3Url(djangoUrl, cb) {
    if (urlCache[djangoUrl]) return cb(urlCache[djangoUrl]);
    if (pendingFetches[djangoUrl]) {
      pendingFetches[djangoUrl].push(cb);
      return;
    }
    pendingFetches[djangoUrl] = [cb];
    fetch(djangoUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var s3url = data.url || djangoUrl;
        urlCache[djangoUrl] = s3url;
        var cbs = pendingFetches[djangoUrl] || [];
        delete pendingFetches[djangoUrl];
        cbs.forEach(function (fn) { fn(s3url); });
      })
      .catch(function () {
        urlCache[djangoUrl] = djangoUrl;
        var cbs = pendingFetches[djangoUrl] || [];
        delete pendingFetches[djangoUrl];
        cbs.forEach(function (fn) { fn(djangoUrl); });
      });
  }

  // ---------- фоновая предзагрузка ----------

  function preloadNext() {
    while (activePreloads < MAX_PARALLEL && preloadQueue.length > 0) {
      var item = preloadQueue.shift();
      startPreload(item.btn, item.djangoUrl);
    }
  }

  function startPreload(btn, djangoUrl) {
    activePreloads++;
    resolveS3Url(djangoUrl, function (s3url) {
      if (videoCache[s3url]) {
        markReady(btn);
        activePreloads--;
        preloadNext();
        return;
      }
      var hiddenVideo = document.createElement('video');
      hiddenVideo.preload = 'auto';
      hiddenVideo.muted = true;
      hiddenVideo.style.cssText = 'position:absolute;width:0;height:0;opacity:0;pointer-events:none;';
      hiddenVideo.src = s3url;
      document.body.appendChild(hiddenVideo);
      videoCache[s3url] = hiddenVideo;

      hiddenVideo.addEventListener('canplaythrough', function onReady() {
        hiddenVideo.removeEventListener('canplaythrough', onReady);
        markReady(btn);
        activePreloads--;
        preloadNext();
      });
      hiddenVideo.addEventListener('error', function () {
        activePreloads--;
        preloadNext();
      });
    });
  }

  function markReady(btn) {
    btn.classList.remove('btn-outline-primary');
    btn.classList.add('btn-primary');
  }

  // ---------- открытие видео ----------

  function openVideo(s3url, leadId) {
    if (leadIdSpan) leadIdSpan.textContent = leadId || '';
    if (downloadLink) downloadLink.href = s3url;

    var cached = videoCache[s3url];
    var isReady = cached && cached.readyState >= 3;

    if (!isReady) showSpinner();

    player.preload = 'auto';
    player.src = s3url;
    player.autoplay = true;

    var bsModal = bootstrap.Modal.getOrCreateInstance(modal);
    bsModal.show();

    // Пробуем играть сразу
    var playPromise = player.play();
    if (playPromise && playPromise.catch) {
      playPromise.catch(function () {
        // Автоплей заблокирован браузером — пользователь нажмёт play сам
      });
    }
  }

  player.addEventListener('canplay', function () {
    hideSpinner();
    // Ещё раз пробуем play на случай если при открытии не сработало
    var p = player.play();
    if (p && p.catch) p.catch(function () {});
  });
  player.addEventListener('error', hideSpinner);

  // ---------- инициализация: подписка на кнопки + очередь предзагрузки ----------

  buttons.forEach(function (btn) {
    var djangoUrl = btn.getAttribute('data-video-url');
    if (!djangoUrl) return;

    // Добавляем в очередь предзагрузки
    preloadQueue.push({ btn: btn, djangoUrl: djangoUrl });

    // Клик
    btn.addEventListener('click', function () {
      var leadId = btn.getAttribute('data-lead-id') || '';
      resolveS3Url(djangoUrl, function (s3url) {
        openVideo(s3url, leadId);
      });
    });
  });

  // Запускаем предзагрузку
  preloadNext();

  // ---------- очистка при закрытии ----------

  modal.addEventListener('hidden.bs.modal', function () {
    player.pause();
    player.removeAttribute('src');
    player.autoplay = false;
    player.load();
    hideSpinner();
  });
})();
