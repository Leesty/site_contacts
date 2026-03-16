/**
 * Ускоренный предпросмотр видео: prefetch URL на hover, preload контента, кэширование.
 *
 * Требования к HTML:
 *   - Кнопки: .js-video-preview[data-video-url]
 *   - Модалка: #videoPreviewModal с <video id="videoPreviewPlayer">
 *   - Опционально: #videoLeadId (span), #videoDownloadLink (a)
 */
(function () {
  'use strict';

  var modal = document.getElementById('videoPreviewModal');
  var video = document.getElementById('videoPreviewPlayer');
  if (!modal || !video) return;

  var leadIdSpan = document.getElementById('videoLeadId');
  var downloadLink = document.getElementById('videoDownloadLink');

  // Кэш: Django-URL → S3-URL
  var urlCache = {};
  // Текущий запрос (чтобы не дублировать)
  var pendingFetches = {};

  // ---------- helpers ----------

  function fetchS3Url(djangoUrl, cb) {
    if (urlCache[djangoUrl]) {
      cb(urlCache[djangoUrl]);
      return;
    }
    if (pendingFetches[djangoUrl]) {
      pendingFetches[djangoUrl].push(cb);
      return;
    }
    pendingFetches[djangoUrl] = [cb];
    fetch(djangoUrl, {headers: {'X-Requested-With': 'XMLHttpRequest'}})
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var s3url = data.url || djangoUrl;
        urlCache[djangoUrl] = s3url;
        var cbs = pendingFetches[djangoUrl] || [];
        delete pendingFetches[djangoUrl];
        cbs.forEach(function (fn) { fn(s3url); });
      })
      .catch(function () {
        urlCache[djangoUrl] = djangoUrl; // fallback
        var cbs = pendingFetches[djangoUrl] || [];
        delete pendingFetches[djangoUrl];
        cbs.forEach(function (fn) { fn(djangoUrl); });
      });
  }

  var preloadedUrls = {};
  function addPreloadHint(url) {
    if (preloadedUrls[url]) return;
    preloadedUrls[url] = true;
    var link = document.createElement('link');
    link.rel = 'preload';
    link.as = 'video';
    link.href = url;
    link.crossOrigin = 'anonymous';
    document.head.appendChild(link);
  }

  // ---------- спиннер ----------

  var spinnerOverlay = document.createElement('div');
  spinnerOverlay.id = 'videoSpinnerOverlay';
  spinnerOverlay.style.cssText =
    'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
    'background:rgba(0,0,0,.6);z-index:10;pointer-events:none;';
  spinnerOverlay.innerHTML =
    '<div style="width:48px;height:48px;border:4px solid rgba(255,255,255,.2);' +
    'border-top-color:#fff;border-radius:50%;animation:vpSpin .8s linear infinite;"></div>';

  // CSS-анимация для спиннера
  var style = document.createElement('style');
  style.textContent = '@keyframes vpSpin{to{transform:rotate(360deg)}}';
  document.head.appendChild(style);

  var modalBody = video.parentElement;
  if (modalBody) modalBody.style.position = 'relative';

  function showSpinner() {
    if (modalBody && !modalBody.contains(spinnerOverlay)) {
      modalBody.appendChild(spinnerOverlay);
    }
  }

  function hideSpinner() {
    if (spinnerOverlay.parentElement) {
      spinnerOverlay.remove();
    }
  }

  // ---------- открытие видео ----------

  function openVideo(s3url, leadId) {
    if (leadIdSpan) leadIdSpan.textContent = leadId || '';
    if (downloadLink) downloadLink.href = s3url;

    showSpinner();
    video.preload = 'auto';
    video.src = s3url;

    var bsModal = bootstrap.Modal.getOrCreateInstance(modal);
    bsModal.show();
  }

  // Скрыть спиннер, когда видео готово
  video.addEventListener('canplay', hideSpinner);
  video.addEventListener('error', hideSpinner);

  // ---------- события кнопок ----------

  document.querySelectorAll('.js-video-preview').forEach(function (btn) {
    var djangoUrl = btn.getAttribute('data-video-url');
    if (!djangoUrl) return;

    // Prefetch на hover — запрос уходит ещё до клика
    btn.addEventListener('mouseenter', function () {
      fetchS3Url(djangoUrl, function (s3url) {
        addPreloadHint(s3url);
      });
    });

    // Мобилка: prefetch на touchstart
    btn.addEventListener('touchstart', function () {
      fetchS3Url(djangoUrl, function (s3url) {
        addPreloadHint(s3url);
      });
    }, {passive: true});

    // Клик: URL уже кэширован (или фетчим)
    btn.addEventListener('click', function () {
      var leadId = btn.getAttribute('data-lead-id') || '';
      fetchS3Url(djangoUrl, function (s3url) {
        openVideo(s3url, leadId);
      });
    });
  });

  // ---------- очистка при закрытии ----------

  modal.addEventListener('hidden.bs.modal', function () {
    if (video) {
      video.pause();
      video.removeAttribute('src');
      video.load(); // сброс буфера
    }
    hideSpinner();
  });
})();
