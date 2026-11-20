const i = (t) => document.getElementById(t), d = (t) => i(t), p = (t) => i(t);
let B = "", L, M = "download", C = null, E, S = 2e3, w = null, T = 0, $;
const h = /* @__PURE__ */ new Map(), A = /* @__PURE__ */ new Set(["succeeded", "partial", "failed", "cancelled"]), V = { queued: "待機中", running: "取得中", stopping: "停止処理中", paused: "一時停止", blocked: "確認が必要", recovering: "復旧中", succeeded: "完了", partial: "一部成功", failed: "失敗", cancelled: "キャンセル" }, f = { TOOLSET_UNAVAILABLE: "実行ツールが未設定です。設定を確認してください。", DISK_SPACE_LOW: "空き容量が不足しています。", OUTPUT_CONFLICT: "保存先に異なるファイルがあります。", time_budget: "実行時間上限です。延長して再開できます。", ENGINE_PROTOCOL_ERROR: "エンジンの構造化出力を確認できません。", HOST_EXIT_UNPROVEN: "プロセスの終了を確認できません。", OUTPUT_OWNERSHIP_UNRESOLVED: "保存先の所有プロセスを確認できません。", shutdown: "アプリ終了に伴う一時停止", DATE_CONTRACT_UNVERIFIED: "このサイトの期間指定は未対応です。", CREDENTIAL_IN_USE: "未完了のSourceがこのCookie版を使用しています。", SOURCE_TERMINAL: "完了済みです。「再試行」を使用してください。", SESSION_REQUIRED: "起動リンクから接続してください。", REVISION_CONFLICT: "設定が更新されています。画面を再読み込みしてください。" };
f.CREDENTIAL_REPLACEMENT_REQUIRED = "元のCookie版は削除されています。新しいCookieを取り込み、ダウンロード画面で選択してから再試行してください。";
f.ENGINE_STALLED = "5分間応答がありません。自動再開は最大2回です。ログを確認できます。";
f.ARCHIVE_INVALID = "ZIPが破損・暗号化されているか、検証上限（展開量4GiB／1万項目）を超えています。";
f.INPUT_INVALID = "入力を確認してください。URLはhttp://またはhttps://で指定し、設定値を有効な範囲にしてください。入力内容は保持しています。";
f.COOKIE_INVALID = "Cookieファイルの形式または有効期限が不正です。サイトからNetscape TXTまたは対応JSONで書き出し直してください。登録は行っていません。";
f.BOOTSTRAP_INVALID = "起動リンクが無効または使用済みです。接続済みのブラウザを使うか、アプリを再起動して新しいリンクで接続してください。";
f.TIME_BUDGET_INVALID = "実行上限は1分〜168時間の数値で指定してください。現在の実行上限は変更していません。";
f.OUTPUT_UNAVAILABLE = "保存先へ書き込めないか、別の処理が使用しています。ローカルの保存先とアクセス権を確認してください。登録は行っていません。";
function l(t, o = !1) {
  const a = i("notice");
  a.textContent = t, a.hidden = !1, a.classList.toggle("error", o);
}
function H(t) {
  document.documentElement.dataset.theme = t ? "dark" : "light", i("theme").setAttribute("aria-pressed", String(t)), i("theme").title = t ? "ライトモードに切り替え" : "ダークモードに切り替え";
}
const F = "downloader-tools.theme";
let j = matchMedia("(prefers-color-scheme: dark)").matches;
try {
  const t = localStorage.getItem(F);
  t !== null && (j = t === "dark");
} catch {
}
H(j);
i("theme").onclick = () => {
  const t = document.documentElement.dataset.theme !== "dark";
  try {
    localStorage.setItem(F, t ? "dark" : "light"), H(t);
  } catch {
    l("テーマを保存できませんでした。ブラウザーの保存設定を確認してください。", !0);
  }
};
async function r(t, o, a, e) {
  const n = { "X-CSRF-Token": B };
  o !== void 0 && (n["Content-Type"] = "application/json"), e && (n["Idempotency-Key"] = e);
  const c = await fetch("/api/v1/" + t, { method: a || (o === void 0 ? "GET" : "POST"), headers: n, body: o === void 0 ? void 0 : JSON.stringify(o) }), u = await c.json();
  if (!c.ok) throw new Error(f[u.error?.code] || u.error?.message || `通信エラー (${c.status})`);
  return u;
}
function s(t, o, a) {
  const e = document.createElement(t);
  return o && (e.textContent = o), a && (e.className = a), e;
}
function m(t, o, a = "text-button") {
  const e = s("button", t, a);
  return e.type = "button", e.onclick = () => g(e, o), e;
}
async function g(t, o) {
  t.disabled = !0;
  try {
    await o();
  } catch (a) {
    l(String(a.message), !0);
  } finally {
    t.disabled = !1;
  }
}
function x(t, o, a) {
  const e = s("div", void 0, "empty-state");
  e.append(s("h3", o), s("p", a)), t.replaceChildren(e);
}
const _ = (t) => t == null ? "—" : t >= 1024 ** 3 ? (t / 1024 ** 3).toFixed(1) + " GiB" : t >= 1024 ** 2 ? (t / 1024 ** 2).toFixed(1) + " MiB" : (t / 1024).toFixed(0) + " KiB";
function G(t) {
  const o = s("article", void 0, "source-card");
  o.dataset.id = t.id;
  const a = s("div", t.kind === "video" ? "▶" : "▧", "source-icon"), e = s("div", void 0, "source-info"), n = s("div", void 0, "source-title");
  n.append(s("strong", t.display_url), s("span", V[t.state] || t.state, "state state-" + t.state));
  const c = s("p", `${t.provider.replace("generic:", "")} · 保存 ${t.completed_count}件 · スキップ ${t.skipped_count}件`, "source-meta");
  t.reason && c.append(s("span", " · " + (f[t.reason] || t.reason)));
  const u = s("div", void 0, "progress-track"), b = s("progress");
  b.max = 1, t.progress.total_bytes ? b.value = Math.min(1, (t.progress.downloaded_bytes || 0) / t.progress.total_bytes) : t.state !== "running" && (b.value = A.has(t.state) ? 1 : 0), b.setAttribute("aria-label", "この項目の転送進捗"), u.append(b);
  const R = s("span", void 0, "source-speed");
  R.textContent = t.state === "running" ? `${_(t.progress.downloaded_bytes)} / ${_(t.progress.total_bytes)} · ${_(t.progress.speed)}/s` : t.counts_complete ? "件数確定" : "総件数は未確定", e.append(n, c, u, R);
  const y = s("div", void 0, "source-actions"), O = async (U) => {
    let D = `sources/${t.id}/${U}`;
    if (U === "resume" && t.reason === "time_budget") {
      const P = prompt("新しい累積実行上限（時間）", "12");
      if (!P) return;
      D += "?budget=" + Math.floor(Number(P) * 3600);
    }
    await r(D, {}), await v();
  };
  return A.has(t.state) ? t.state !== "succeeded" && y.append(m("再試行", async () => {
    await r("retries", { source_id: t.id, credential_id: p("credential").value || null }, "POST", crypto.randomUUID()), l("新しいSourceを追加しました。"), await v();
  })) : ((t.state === "queued" || t.state === "running") && y.append(m("一時停止", () => O("pause"))), (t.state === "paused" || t.state === "blocked" || t.state === "stopping") && y.append(m("再開", () => O("resume"))), y.append(m("キャンセル", () => O("cancel")))), y.append(m("詳細", () => W(t.id))), o.append(a, e, y), o;
}
function J(t) {
  const o = i("queue-list"), a = new Set(t.sources.map((e) => e.id));
  for (const e of h.keys()) a.has(e) || h.delete(e);
  for (const e of t.sources) {
    const n = h.get(e.id);
    (!n || e.revision >= n.revision) && h.set(e.id, e);
  }
  o.replaceChildren(...[...h.values()].map(G)), h.size || x(o, "キューは空です", "保存したいメディアのURLを追加してください。"), i("running-count").replaceChildren(document.createTextNode(String(t.diagnostics.running)), s("small", " 件")), i("waiting-count").replaceChildren(document.createTextNode(String(t.sources.filter((e) => e.state === "queued" || e.state === "paused").length)), s("small", " 件")), i("saved-count").replaceChildren(document.createTextNode(String(t.sources.reduce((e, n) => e + n.completed_count, 0))), s("small", " ファイル")), t.diagnostics.error && l("実行を保留しています: " + t.diagnostics.error, !0);
}
async function v() {
  J(await r("snapshot"));
}
function z(t) {
  const o = h.get(t.source_id);
  if (!o || A.has(o.state) || o.attempt_id !== t.progress.attempt_id || (o.progress.seq || 0) >= (t.progress.seq || 0)) return;
  o.progress = t.progress;
  const a = [...i("queue-list").children].find((e) => e.dataset.id === o.id);
  if (a) {
    const e = a.querySelector("progress");
    o.progress.total_bytes && (e.value = Math.min(1, (o.progress.downloaded_bytes || 0) / o.progress.total_bytes)), a.querySelector(".source-speed").textContent = `${_(o.progress.downloaded_bytes)} / ${_(o.progress.total_bytes)} · ${_(o.progress.speed)}/s`;
  }
}
async function N(t = !0) {
  t && (C = null, i("history-list").replaceChildren());
  const o = await r("sources?limit=50" + (C ? "&cursor=" + C : ""));
  i("history-list").append(...o.items.map(G)), C = o.next_cursor, i("more-history").hidden = !C, i("history-list").children.length || x(i("history-list"), "履歴はまだありません", "受付済みのSourceがここに表示されます。");
}
async function K(t) {
  if (w !== t || !i("detail-dialog").open) return;
  let o = 1e3;
  try {
    const a = await r(`sources/${t}/logs?offset=${T}`);
    if (w !== t) return;
    const e = i("detail-log"), n = e.scrollHeight - e.scrollTop - e.clientHeight < 40, c = T === 0 ? "" : e.textContent || "";
    e.textContent = (c + a.text).slice(-2e5) || "ログの出力を待っています…", n && (e.scrollTop = e.scrollHeight), o = a.next_offset - T >= 65532 ? 100 : 1e3, T = a.next_offset;
  } catch (a) {
    w === t && l(`ログ取得: ${a.message}`, !0), o = 5e3;
  }
  w === t && ($ = window.setTimeout(() => {
    K(t);
  }, o));
}
async function W(t) {
  const o = await r("sources/" + t), a = i("detail-content");
  a.replaceChildren(s("p", `${V[o.state]} · 保存 ${o.completed_count}件`), s("p", `ツールセット: ${o.toolset_id}`), s("p", `参照Batch: ${o.references}件（停止操作はすべての参照先へ影響します）`));
  const e = s("pre", JSON.stringify(o.profile, null, 2));
  a.append(e, m("保存先を開く", () => r("outputs/open", { output_root_id: o.output_root_id }), "secondary"));
  for (const n of o.artifacts) a.append(s("p", (n.valid ? "✓ " : "欠損・変更あり: ") + n.relative_path));
  clearTimeout($), w = t, T = 0, i("detail-log").textContent = "ログを読み込んでいます…", i("detail-dialog").showModal(), K(t);
}
async function k() {
  const t = await r("credentials"), o = [new Option("使用しない", ""), ...t.map((e) => new Option(e.name, e.id))];
  p("credential").replaceChildren(...o);
  const a = i("credential-list");
  a.replaceChildren(...t.map((e) => {
    const n = s("div", void 0, "credential-row");
    return n.append(s("div", `${e.name} · v${e.version}`), s("small", e.domains.join(", ")), m("名前を変更", async () => {
      const c = prompt("新しい表示名", e.name);
      c && (await r("credentials/" + e.id, { name: c }, "PATCH"), await k());
    }), m("削除", async () => {
      await r("credentials/" + e.id, void 0, "DELETE"), await k();
    })), n;
  })), t.length || x(a, "Cookieは未登録です", "認証が必要なサイトを利用する場合に取り込んでください。");
}
async function I() {
  const [t, o, a] = await Promise.all([r("settings"), r("output-roots"), r("diagnostics")]);
  L = t, d("total-slots").value = String(t.total_slots), d("video-slots").value = String(t.video_slots), d("gallery-slots").value = String(t.gallery_slots), d("time-budget").value = String(t.time_budget / 3600), p("output-root").replaceChildren(...o.map((e) => new Option((e.id === "default" ? "標準 · " : "") + e.path, e.id))), i("roots-list").replaceChildren(...o.map((e) => {
    const n = s("div", void 0, "credential-row");
    return n.append(s("code", e.path), m("開く", () => r("outputs/open", { output_root_id: e.id }))), n;
  })), p("toolset-choice").replaceChildren(...a.tools.map((e) => new Option(e.id + (e.active ? "（使用中）" : ""), e.id))), i("toolsets-list").replaceChildren(...a.tools.map((e) => s("p", `${e.active ? "● 使用中" : "○"} ${e.id} · ${Object.entries(e.versions).map(([n, c]) => `${n} ${c}`).join(" / ")}`, "help"))), a.tools.length || (i("toolsets-list").textContent = "検証済みtoolsetがありません。セットアップを実行してください。"), i("diagnostics").textContent = JSON.stringify(a, null, 2);
}
const q = { download: ["ダウンロード", "URLを追加して、動画と画像をまとめて保存。"], history: ["履歴", "取得結果と保存済みファイルを確認。"], credentials: ["認証", "サイトごとのCookieを、このPC内で管理。"], settings: ["設定", "実行予算と保存先、ツールセットを管理。"] };
async function X(t) {
  M = t;
  for (const o of Object.keys(q)) i("panel-" + o).hidden = o !== t;
  for (const o of document.querySelectorAll("[data-tab]"))
    o.dataset.tab === t ? o.setAttribute("aria-current", "page") : o.removeAttribute("aria-current");
  [i("page-title").textContent, i("page-description").textContent] = q[t], t === "history" && await N(), t === "credentials" && await k(), t === "settings" && await I();
}
async function Q() {
  const t = location.hash.startsWith("#token=") ? decodeURIComponent(location.hash.slice(7)) : null;
  Z();
  let o;
  try {
    o = await r("session");
  } catch (e) {
    if (!t) throw e;
    o = await r("session", { token: t });
  }
  B = o.csrf, await Promise.all([v(), k(), I()]);
  const a = new EventSource("/api/v1/events");
  a.addEventListener("snapshot", (e) => {
    J(JSON.parse(e.data)), i("connection").textContent = "接続済み", clearTimeout(E), E = void 0, S = 2e3;
  }), a.addEventListener("refresh", () => {
    v().catch((e) => l(e.message, !0)), M === "history" && N().catch((e) => l(e.message, !0));
  }), a.addEventListener("progress", (e) => z(JSON.parse(e.data))), a.onerror = () => {
    if (i("connection").textContent = "再接続しています…", !E) {
      const e = async () => {
        try {
          await v();
        } catch {
        }
        S = Math.min(3e4, S * 2), E = window.setTimeout(e, document.hidden ? 3e4 : S);
      };
      E = window.setTimeout(e, S);
    }
  };
}
function Z() {
  window.history.replaceState(null, "", location.pathname + location.search);
}
for (const t of document.querySelectorAll("[data-tab]")) t.onclick = () => g(t, () => X(t.dataset.tab));
for (const t of document.querySelectorAll('[name="kind"]')) t.onchange = () => {
  i("video-options").hidden = t.value !== "video", i("gallery-options").hidden = t.value !== "gallery";
};
i("download-form").onsubmit = (t) => {
  t.preventDefault(), g(i("submit-download"), async () => {
    const o = document.querySelector('[name="kind"]:checked').value, a = i("urls").value.split(/\r?\n/).map((u) => u.trim()).filter(Boolean);
    if (!a.length || a.length > 100) throw new Error("URLは1〜100件で入力してください。");
    const e = await r("profiles", { name: "カスタム", kind: o, container: o === "video" ? p("container").value : "original", height: Number(p("height").value), playlist: d("playlist").checked, media_policy: p("media-policy").value }), c = (await r("batches", { kind: o, urls: a, profile_id: e.id, credential_id: p("credential").value || null, output_root_id: p("output-root").value }, "POST", crypto.randomUUID())).sources.filter((u) => u.shared).length;
    l(`キューに追加しました。${c ? ` ${c}件は既存Sourceへの参照です。` : ""}`), i("urls").value = "", await v();
  });
};
i("cookie-form").onsubmit = (t) => {
  t.preventDefault(), g(t.submitter, async () => {
    const o = d("cookie-file").files?.[0];
    if (!o || o.size > 262144) throw new Error("256 KiB以下のCookieファイルを選択してください。");
    await r("credentials", { name: d("cookie-name").value, content: await o.text() }), d("cookie-file").value = "", d("cookie-name").value = "", l("Cookieを新しい版として取り込みました。"), await k();
  });
};
i("settings-form").onsubmit = (t) => {
  t.preventDefault(), g(t.submitter, async () => {
    L = await r("settings", { ...L, total_slots: Number(d("total-slots").value), video_slots: Number(d("video-slots").value), gallery_slots: Number(d("gallery-slots").value), time_budget: Number(d("time-budget").value) * 3600 }, "PATCH"), l("実行予算を保存しました。");
  });
};
i("root-form").onsubmit = (t) => {
  t.preventDefault(), g(t.submitter, async () => {
    await r("output-roots", { path: d("root-path").value }), d("root-path").value = "", await I(), l("保存先を検証して登録しました。");
  });
};
i("refresh").onclick = () => {
  v().catch((t) => l(t.message, !0));
};
i("history-refresh").onclick = () => {
  N().catch((t) => l(t.message, !0));
};
i("more-history").onclick = () => {
  N(!1).catch((t) => l(t.message, !0));
};
i("close-detail").onclick = () => i("detail-dialog").close();
i("detail-dialog").addEventListener("close", () => {
  w = null, clearTimeout($);
});
i("backup").onclick = () => g(i("backup"), async () => {
  await r("backups", {}), l("data/backupsにバックアップを保存しました。");
});
i("activate-toolset").onclick = () => g(i("activate-toolset"), async () => {
  await r("toolset-updates", { toolset_id: p("toolset-choice").value }), await I(), l("新規受付用のツールセットを切り替えました。");
});
i("update-toolset").onclick = () => g(i("update-toolset"), async () => {
  const t = await r("toolset-updates", {});
  i("update-status").textContent = "公式安定版を取得し、独立環境で検証しています…", await new Promise((o, a) => {
    const e = window.setInterval(async () => {
      try {
        const n = await r("toolset-updates/" + t.id);
        n.state === "completed" ? (i("update-status").textContent = "検証完了: " + n.toolset_id, clearInterval(e), await I(), o()) : n.state === "failed" && (clearInterval(e), a(new Error("ツールセット更新に失敗しました。旧版を引き続き使用します。")));
      } catch (n) {
        clearInterval(e), a(n);
      }
    }, 3e3);
  });
});
Q().catch((t) => {
  i("connection").textContent = "接続できません", l(t.message, !0);
});
