/* 本机界面偏好桥接层（在 app.js 之前加载）
 *
 * 背景：隐藏列表、卡片外观等原本只存浏览器 localStorage，换渲染外壳（浏览器→WebView2）
 * 会因数据目录不同而全部丢失。这里把 localStorage 变成「后端落盘（configs/ui_prefs.json）
 * + 本机缓存」：
 *   1. 页面加载时，后端已通过 /api/ui-prefs.js 注入 window.__UI_PREFS，这里把它写回
 *      localStorage，保证 app.js 里所有同步 getItem 仍能读到正确值；
 *   2. 劫持 Storage.prototype.setItem/removeItem，把每次写入增量同步到后端；
 *   3. 若后端为空而当前 localStorage 非空（典型场景：在旧浏览器里首次打开），
 *      自动把整份 localStorage 一次性导入后端，用于无痛迁移。
 *
 * 手动迁移：可在旧浏览器控制台执行 window.__importLocalPrefs()，或在「系统设置」里点
 * 「导入本机设置」。
 */
(function () {
  "use strict";

  var LS = window.localStorage;
  var server = window.__UI_PREFS || {};
  var serverHasKeys = Object.keys(server).length > 0;

  for (var k in server) {
    if (Object.prototype.hasOwnProperty.call(server, k)) {
      try { LS.setItem(k, server[k]); } catch (e) { /* ignore */ }
    }
  }

  var origSet = Storage.prototype.setItem;
  var origRemove = Storage.prototype.removeItem;
  var queue = Object.create(null);
  var timer = null;

  function flush() {
    timer = null;
    var updates = queue;
    queue = Object.create(null);
    if (!Object.keys(updates).length) return;
    try {
      fetch("/api/ui-prefs", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ updates: updates }),
        keepalive: true,
      }).catch(function () { /* 离线/失败时本机 localStorage 仍有值 */ });
    } catch (e) { /* ignore */ }
  }

  function enqueue(key, value) {
    queue[key] = value;
    if (timer === null) timer = setTimeout(flush, 250);
  }

  Storage.prototype.setItem = function (key, value) {
    origSet.call(this, key, value);
    if (this === LS) enqueue(String(key), String(value));
  };

  Storage.prototype.removeItem = function (key) {
    origRemove.call(this, key);
    if (this === LS) enqueue(String(key), null);
  };

  window.__importLocalPrefs = function () {
    var data = Object.create(null);
    for (var i = 0; i < LS.length; i++) {
      var key = LS.key(i);
      data[key] = LS.getItem(key);
    }
    return fetch("/api/ui-prefs", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ replace: true, data: data }),
      keepalive: true,
    });
  };

  if (!serverHasKeys) {
    var bulk = Object.create(null);
    for (var j = 0; j < LS.length; j++) {
      var kk = LS.key(j);
      bulk[kk] = LS.getItem(kk);
    }
    if (Object.keys(bulk).length) {
      try {
        fetch("/api/ui-prefs", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ replace: true, data: bulk }),
          keepalive: true,
        }).catch(function () { /* ignore */ });
      } catch (e) { /* ignore */ }
    }
  }
})();
