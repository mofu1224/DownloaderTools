const a = (t) => document.getElementById(t), l = (t) => a(t), p = (t) => a(t);
let B = "", A, V = "download", b = null, C, S = 2e3, _ = null, T = 0, x;
const v = /* @__PURE__ */ new Map(), $ = /* @__PURE__ */ new Set(["succeeded", "partial", "failed", "cancelled"]), G = { queued: "待機中", running: "取得中", stopping: "停止処理中", paused: "一時停止", blocked: "確認が必要", recovering: "復旧中", succeeded: "完了", partial: "一部成功", failed: "失敗", cancelled: "キャンセル" }, f = { TOOLSET_UNAVAILABLE: "実行ツールが未設定です。設定を確認してください。", DISK_SPACE_LOW: "空き容量が不足しています。", OUTPUT_CONFLICT: "保存先に異なるファイルがあります。", time_budget: "実行時間上限です。延長して再開できます。", ENGINE_PROTOCOL_ERROR: "エンジンの構造化出力を確認できません。", HOST_EXIT_UNPROVEN: "プロセスの終了を確認できません。", OUTPUT_OWNERSHIP_UNRESOLVED: "保存先の所有プロセスを確認できません。", shutdown: "アプリ終了に伴う一時停止", DATE_CONTRACT_UNVERIFIED: "このサイトの期間指定は未対応です。", CREDENTIAL_IN_USE: "未完了のSourceがこのCookie版を使用しています。", SOURCE_TERMINAL: "完了済みです。「再試行」を使用してください。", SESSION_REQUIRED: "起動リンクから接続してください。", REVISION_CONFLICT: "設定が更新されています。画面を再読み込みしてください。" };
f.CREDENTIAL_REPLACEMENT_REQUIRED = "元のCookie版は削除されています。新しいCookieを取り込み、ダウンロード画面で選択してから再試行してください。";
f.ENGINE_STALLED = "5分間応答がありません。自動再開は最大2回です。ログを確認できます。";
f.ARCHIVE_INVALID = "ZIPが破損・暗号化されているか、検証上限（展開量4GiB／1万項目）を超えています。";
f.INPUT_INVALID = "入力を確認してください。URLはhttp://またはhttps://で指定し、設定値を有効な範囲にしてください。入力内容は保持しています。";
f.COOKIE_INVALID = "Cookieファイルの形式または有効期限が不正です。サイトからNetscape TXTまたは対応JSONで書き出し直してください。登録は行っていません。";
f.BOOTSTRAP_INVALID = "起動リンクが無効または使用済みです。接続済みのブラウザを使うか、アプリを再起動して新しいリンクで接続してください。";
f.TIME_BUDGET_INVALID = "実行上限は1分〜168時間の数値で指定してください。現在の実行上限は変更していません。";
f.OUTPUT_UNAVAILABLE = "保存先へ書き込めないか、別の処理が使用しています。ローカルの保存先とアクセス権を確認してください。登録は行っていません。";
function d(t, o = !1) {
  const n = a("notice");
  n.textContent = t, n.hidden = !1, n.classList.toggle("error", o);
}
function H(t) {
  document.documentElement.dataset.theme = t ? "dark" : "light", a("theme").setAttribute("aria-pressed", String(t)), a("theme").title = t ? "ライトモードに切り替え" : "ダークモードに切り替え";
}
const F = "downloader-tools.theme";
let K = matchMedia("(prefers-color-scheme: dark)").matches;
try {
  const t = localStorage.getItem(F);
  t !== null && (K = t === "dark");
} catch {
}
H(K);
a("theme").onclick = () => {
  const t = document.documentElement.dataset.theme !== "dark";
  try {
    localStorage.setItem(F, t ? "dark" : "light"), H(t);
  } catch {
    d("テーマを保存できませんでした。ブラウザーの保存設定を確認してください。", !0);
  }
};
async function r(t, o, n, e) {
  const i = { "X-CSRF-Token": B };
  o !== void 0 && (i["Content-Type"] = "application/json"), e && (i["Idempotency-Key"] = e);
  const c = await fetch("/api/v1/" + t, { method: n || (o === void 0 ? "GET" : "POST"), headers: i, body: o === void 0 ? void 0 : JSON.stringify(o) }), u = await c.json();
  if (!c.ok) throw new Error(f[u.error?.code] || u.error?.message || `通信エラー (${c.status})`);
  return u;
}
function s(t, o, n) {
  const e = document.createElement(t);
  return o && (e.textContent = o), n && (e.className = n), e;
}
function h(t, o, n = "text-button") {
  const e = s("button", t, n);
  return e.type = "button", e.onclick = () => g(e, o), e;
}
async function g(t, o) {
  t.disabled = !0;
  try {
    await o();
  } catch (n) {
    d(String(n.message), !0);
  } finally {
    t.disabled = !1;
  }
}
function R(t, o, n) {
  const e = s("div", void 0, "empty-state");
  e.append(s("h3", o), s("p", n)), t.replaceChildren(e);
}
const E = (t) => t == null ? "—" : t >= 1024 ** 3 ? (t / 1024 ** 3).toFixed(1) + " GiB" : t >= 1024 ** 2 ? (t / 1024 ** 2).toFixed(1) + " MiB" : (t / 1024).toFixed(0) + " KiB";
function j(t) {
  const o = s("article", void 0, "source-card");
  o.dataset.id = t.id;
  const n = s("div", t.kind === "video" ? "▶" : "▧", "source-icon"), e = s("div", void 0, "source-info"), i = s("div", void 0, "source-title");
  i.append(s("strong", t.display_url), s("span", G[t.state] || t.state, "state state-" + t.state));
  const c = s("p", `${t.provider.replace("generic:", "")} · 保存 ${t.completed_count}件 · スキップ ${t.skipped_count}件`, "source-meta");
  t.reason && c.append(s("span", " · " + (f[t.reason] || t.reason)));
  const u = s("div", void 0, "progress-track"), m = s("progress");
  m.max = 1, t.progress.total_bytes ? m.value = Math.min(1, (t.progress.downloaded_bytes || 0) / t.progress.total_bytes) : t.state !== "running" && (m.value = $.has(t.state) ? 1 : 0), m.setAttribute("aria-label", "この項目の転送進捗"), u.append(m);
  const U = s("span", void 0, "source-speed");
  U.textContent = t.state === "running" ? `${E(t.progress.downloaded_bytes)} / ${E(t.progress.total_bytes)} · ${E(t.progress.speed)}/s` : t.counts_complete ? "件数確定" : "総件数は未確定", e.append(i, c, u, U);
  const y = s("div", void 0, "source-actions"), L = async (D) => {
    let P = `sources/${t.id}/${D}`;
    if (D === "resume" && t.reason === "time_budget") {
      const M = prompt("新しい累積実行上限（時間）", "12");
      if (!M) return;
      P += "?budget=" + Math.floor(Number(M) * 3600);
    }
    await r(P, {}), await w();
  };
  return $.has(t.state) ? t.state !== "succeeded" && y.append(h("再試行", async () => {
    await r("retries", { source_id: t.id, credential_id: p("credential").value || null }, "POST", crypto.randomUUID()), d("新しいSourceを追加しました。"), await w();
  })) : ((t.state === "queued" || t.state === "running") && y.append(h("一時停止", () => L("pause"))), (t.state === "paused" || t.state === "blocked" || t.state === "stopping") && y.append(h("再開", () => L("resume"))), y.append(h("キャンセル", () => L("cancel")))), y.append(h("詳細", () => X(t.id))), o.append(n, e, y), o;
}
function J(t) {
  const o = a("queue-list"), n = new Set(t.sources.map((e) => e.id));
  for (const e of v.keys()) n.has(e) || v.delete(e);
  for (const e of t.sources) {
    const i = v.get(e.id);
    (!i || e.revision >= i.revision) && v.set(e.id, e);
  }
  o.replaceChildren(...[...v.values()].map(j)), v.size || R(o, "キューは空です", "保存したいメディアのURLを追加してください。"), a("running-count").replaceChildren(document.createTextNode(String(t.diagnostics.running)), s("small", " 件")), a("waiting-count").replaceChildren(document.createTextNode(String(t.sources.filter((e) => e.state === "queued" || e.state === "paused").length)), s("small", " 件")), a("saved-count").replaceChildren(document.createTextNode(String(t.sources.reduce((e, i) => e + i.completed_count, 0))), s("small", " ファイル")), t.diagnostics.error && d("実行を保留しています: " + t.diagnostics.error, !0);
}
async function w() {
  J(await r("snapshot"));
}
function W(t) {
  const o = v.get(t.source_id);
  if (!o || $.has(o.state) || o.attempt_id !== t.progress.attempt_id || (o.progress.seq || 0) >= (t.progress.seq || 0)) return;
  o.progress = t.progress;
  const n = [...a("queue-list").children].find((e) => e.dataset.id === o.id);
  if (n) {
    const e = n.querySelector("progress");
    o.progress.total_bytes && (e.value = Math.min(1, (o.progress.downloaded_bytes || 0) / o.progress.total_bytes)), n.querySelector(".source-speed").textContent = `${E(o.progress.downloaded_bytes)} / ${E(o.progress.total_bytes)} · ${E(o.progress.speed)}/s`;
  }
}
async function O(t = !0) {
  t && (b = null, a("history-list").replaceChildren());
  const o = await r("sources?limit=50" + (b ? "&cursor=" + b : ""));
  a("history-list").append(...o.items.map(j)), b = o.next_cursor, a("more-history").hidden = !b, a("history-list").children.length || R(a("history-list"), "履歴はまだありません", "受付済みのSourceがここに表示されます。");
}
async function z(t) {
  if (_ !== t || !a("detail-dialog").open) return;
  let o = 1e3;
  try {
    const n = await r(`sources/${t}/logs?offset=${T}`);
    if (_ !== t) return;
    const e = a("detail-log"), i = e.scrollHeight - e.scrollTop - e.clientHeight < 40, c = T === 0 ? "" : e.textContent || "";
    e.textContent = (c + n.text).slice(-2e5) || "ログの出力を待っています…", i && (e.scrollTop = e.scrollHeight), o = n.next_offset - T >= 65532 ? 100 : 1e3, T = n.next_offset;
  } catch (n) {
    _ === t && d(`ログ取得: ${n.message}`, !0), o = 5e3;
  }
  _ === t && (x = window.setTimeout(() => {
    z(t);
  }, o));
}
async function X(t) {
  const o = await r("sources/" + t), n = a("detail-content");
  n.replaceChildren(s("p", `${G[o.state]} · 保存 ${o.completed_count}件`), s("p", `ツールセット: ${o.toolset_id}`), s("p", `参照Batch: ${o.references}件（停止操作はすべての参照先へ影響します）`));
  const e = { DATE_UNKNOWN: "投稿日時不明", DATE_OUTSIDE_RANGE: "指定期間外", MEDIA_POLICY_IMAGES_ONLY: "画像以外" };
  for (const { reason: c, count: u } of o.skip_reasons) n.append(s("p", `${e[c] || c}でスキップ: ${u}件`));
  const i = s("pre", JSON.stringify(o.profile, null, 2));
  n.append(i, h("保存先を開く", () => r("outputs/open", { output_root_id: o.output_root_id }), "secondary"));
  for (const c of o.artifacts) n.append(s("p", (c.valid ? "✓ " : "欠損・変更あり: ") + c.relative_path));
  clearTimeout(x), _ = t, T = 0, a("detail-log").textContent = "ログを読み込んでいます…", a("detail-dialog").showModal(), z(t);
}
async function I() {
  const t = await r("credentials"), o = [new Option("使用しない", ""), ...t.map((e) => new Option(e.name, e.id))];
  p("credential").replaceChildren(...o);
  const n = a("credential-list");
  n.replaceChildren(...t.map((e) => {
    const i = s("div", void 0, "credential-row");
    return i.append(s("div", `${e.name} · v${e.version}`), s("small", e.domains.join(", ")), h("名前を変更", async () => {
      const c = prompt("新しい表示名", e.name);
      c && (await r("credentials/" + e.id, { name: c }, "PATCH"), await I());
    }), h("削除", async () => {
      await r("credentials/" + e.id, void 0, "DELETE"), await I();
    })), i;
  })), t.length || R(n, "Cookieは未登録です", "認証が必要なサイトを利用する場合に取り込んでください。");
}
async function k() {
  const [t, o, n] = await Promise.all([r("settings"), r("output-roots"), r("diagnostics")]);
  A = t, l("total-slots").value = String(t.total_slots), l("video-slots").value = String(t.video_slots), l("gallery-slots").value = String(t.gallery_slots), l("time-budget").value = String(t.time_budget / 3600), p("output-root").replaceChildren(...o.map((e) => new Option((e.id === "default" ? "標準 · " : "") + e.path, e.id))), a("roots-list").replaceChildren(...o.map((e) => {
    const i = s("div", void 0, "credential-row");
    return i.append(s("code", e.path), h("開く", () => r("outputs/open", { output_root_id: e.id }))), i;
  })), p("toolset-choice").replaceChildren(...n.tools.map((e) => new Option(e.id + (e.active ? "（使用中）" : ""), e.id))), a("toolsets-list").replaceChildren(...n.tools.map((e) => s("p", `${e.active ? "● 使用中" : "○"} ${e.id} · ${Object.entries(e.versions).map(([i, c]) => `${i} ${c}`).join(" / ")}`, "help"))), n.tools.length || (a("toolsets-list").textContent = "検証済みtoolsetがありません。セットアップを実行してください。"), a("diagnostics").textContent = JSON.stringify(n, null, 2);
}
const q = { download: ["ダウンロード", "URLを追加して、動画と画像をまとめて保存。"], history: ["履歴", "取得結果と保存済みファイルを確認。"], credentials: ["認証", "サイトごとのCookieを、このPC内で管理。"], settings: ["設定", "実行予算と保存先、ツールセットを管理。"] };
async function Q(t) {
  V = t;
  for (const o of Object.keys(q)) a("panel-" + o).hidden = o !== t;
  for (const o of document.querySelectorAll("[data-tab]"))
    o.dataset.tab === t ? o.setAttribute("aria-current", "page") : o.removeAttribute("aria-current");
  [a("page-title").textContent, a("page-description").textContent] = q[t], t === "history" && await O(), t === "credentials" && await I(), t === "settings" && await k();
}
async function Y() {
  const t = location.hash.startsWith("#token=") ? decodeURIComponent(location.hash.slice(7)) : null;
  Z();
  let o;
  try {
    o = await r("session");
  } catch (e) {
    if (!t) throw e;
    o = await r("session", { token: t });
  }
  B = o.csrf, await Promise.all([w(), I(), k()]);
  const n = new EventSource("/api/v1/events");
  n.addEventListener("snapshot", (e) => {
    J(JSON.parse(e.data)), a("connection").textContent = "接続済み", clearTimeout(C), C = void 0, S = 2e3;
  }), n.addEventListener("refresh", () => {
    w().catch((e) => d(e.message, !0)), V === "history" && O().catch((e) => d(e.message, !0));
  }), n.addEventListener("progress", (e) => W(JSON.parse(e.data))), n.onerror = () => {
    if (a("connection").textContent = "再接続しています…", !C) {
      const e = async () => {
        try {
          await w();
        } catch {
        }
        S = Math.min(3e4, S * 2), C = window.setTimeout(e, document.hidden ? 3e4 : S);
      };
      C = window.setTimeout(e, S);
    }
  };
}
function Z() {
  window.history.replaceState(null, "", location.pathname + location.search);
}
for (const t of document.querySelectorAll("[data-tab]")) t.onclick = () => g(t, () => Q(t.dataset.tab));
function N() {
  const o = a("urls").value.trim().split(/\s+/).every((n) => {
    try {
      const e = new URL(n);
      return ["pixiv.net", "www.pixiv.net"].includes(e.hostname) && /^\/(?:en\/)?(?:artworks\/\d+|users\/\d+\/artworks)\/?$/.test(e.pathname);
    } catch {
      return !1;
    }
  });
  return a("gallery-dates").disabled = !o && !l("date-from").value && !l("date-to").value, o;
}
a("urls").addEventListener("input", N);
for (const t of document.querySelectorAll('[name="kind"]')) t.onchange = () => {
  a("video-options").hidden = t.value !== "video", a("gallery-options").hidden = t.value !== "gallery", N();
};
a("download-form").onsubmit = (t) => {
  t.preventDefault(), g(a("submit-download"), async () => {
    const o = document.querySelector('[name="kind"]:checked').value, n = a("urls").value.split(/\r?\n/).map((m) => m.trim()).filter(Boolean);
    if (!n.length || n.length > 100) throw new Error("URLは1〜100件で入力してください。");
    const e = {};
    if (o === "gallery" && (l("date-from").value || l("date-to").value)) {
      if (!N()) throw new Error("期間指定はPixivの作品・作品一覧のみ対応しています。期間を解除するか、対応URLだけを入力してください。");
      if (e.date_from = l("date-from").value || null, e.date_to = l("date-to").value || null, e.date_from && e.date_to && e.date_from > e.date_to) throw new Error("終了日は開始日以降を指定してください。");
      e.timezone = l("date-zone").value.trim();
    }
    const i = await r("profiles", { name: "カスタム", kind: o, container: o === "video" ? p("container").value : "original", height: Number(p("height").value), playlist: l("playlist").checked, media_policy: p("media-policy").value, ...e }), u = (await r("batches", { kind: o, urls: n, profile_id: i.id, credential_id: p("credential").value || null, output_root_id: p("output-root").value }, "POST", crypto.randomUUID())).sources.filter((m) => m.shared).length;
    d(`キューに追加しました。${u ? ` ${u}件は既存Sourceへの参照です。` : ""}`), a("urls").value = "", N(), await w();
  });
};
a("cookie-form").onsubmit = (t) => {
  t.preventDefault(), g(t.submitter, async () => {
    const o = l("cookie-file").files?.[0];
    if (!o || o.size > 262144) throw new Error("256 KiB以下の認証ファイルを選択してください。");
    await r("credentials", { name: l("cookie-name").value, content: await o.text() }), l("cookie-file").value = "", l("cookie-name").value = "", d("認証情報を新しい版として取り込みました。"), await I();
  });
};
a("settings-form").onsubmit = (t) => {
  t.preventDefault(), g(t.submitter, async () => {
    A = await r("settings", { ...A, total_slots: Number(l("total-slots").value), video_slots: Number(l("video-slots").value), gallery_slots: Number(l("gallery-slots").value), time_budget: Number(l("time-budget").value) * 3600 }, "PATCH"), d("実行予算を保存しました。");
  });
};
a("root-form").onsubmit = (t) => {
  t.preventDefault(), g(t.submitter, async () => {
    await r("output-roots", { path: l("root-path").value }), l("root-path").value = "", await k(), d("保存先を検証して登録しました。");
  });
};
a("refresh").onclick = () => {
  w().catch((t) => d(t.message, !0));
};
a("history-refresh").onclick = () => {
  O().catch((t) => d(t.message, !0));
};
a("more-history").onclick = () => {
  O(!1).catch((t) => d(t.message, !0));
};
a("close-detail").onclick = () => a("detail-dialog").close();
a("detail-dialog").addEventListener("close", () => {
  _ = null, clearTimeout(x);
});
a("backup").onclick = () => g(a("backup"), async () => {
  await r("backups", {}), d("data/backupsにバックアップを保存しました。");
});
a("activate-toolset").onclick = () => g(a("activate-toolset"), async () => {
  await r("toolset-updates", { toolset_id: p("toolset-choice").value }), await k(), d("新規受付用のツールセットを切り替えました。");
});
a("update-toolset").onclick = () => g(a("update-toolset"), async () => {
  const t = await r("toolset-updates", {});
  a("update-status").textContent = "公式安定版を取得し、独立環境で検証しています…", await new Promise((o, n) => {
    const e = window.setInterval(async () => {
      try {
        const i = await r("toolset-updates/" + t.id);
        i.state === "completed" ? (a("update-status").textContent = "検証完了: " + i.toolset_id, clearInterval(e), await k(), o()) : i.state === "failed" && (clearInterval(e), n(new Error("ツールセット更新に失敗しました。旧版を引き続き使用します。")));
      } catch (i) {
        clearInterval(e), n(i);
      }
    }, 3e3);
  });
});
Y().catch((t) => {
  a("connection").textContent = "接続できません", d(t.message, !0);
});
