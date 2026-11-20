import './style.css';

type Source = {id:string; display_url:string; kind:string; provider:string; state:string; reason:string|null;
  phase:string; revision:number; attempt_id:string|null; completed_count:number; skipped_count:number;
  counts_complete:boolean; toolset_id:string; output_root_id:string; progress:Progress; references?:number};
type Progress = {attempt_id?:string; seq?:number; downloaded_bytes?:number; total_bytes?:number; speed?:number; eta?:number};
type Snapshot = {sources:Source[]; diagnostics:{error:string|null;running:number}};
type Settings = {revision:number;total_slots:number;video_slots:number;gallery_slots:number;reserve_bytes:number;time_budget:number};
type Credential = {id:string;name:string;domains:string[];version:number};
type Root = {id:string;path:string};
type Tool = {id:string;active:boolean;versions:Record<string,string>};
const $ = <T extends HTMLElement=HTMLElement>(id:string):T => document.getElementById(id) as T;
const input = (id:string) => $<HTMLInputElement>(id);
const select = (id:string) => $<HTMLSelectElement>(id);
let csrf = '';
let settings:Settings;
let currentTab = 'download';
let historyCursor:number|null = null;
let polling:number|undefined;
let pollDelay = 2000;
let detailSource:string|null = null;
let detailOffset = 0;
let detailTimer:number|undefined;
const sourceMap = new Map<string,Source>();
const terminal = new Set(['succeeded','partial','failed','cancelled']);
const states:Record<string,string> = {queued:'待機中',running:'取得中',stopping:'停止処理中',paused:'一時停止',blocked:'確認が必要',recovering:'復旧中',succeeded:'完了',partial:'一部成功',failed:'失敗',cancelled:'キャンセル'};
const reasons:Record<string,string> = {TOOLSET_UNAVAILABLE:'実行ツールが未設定です。設定を確認してください。',DISK_SPACE_LOW:'空き容量が不足しています。',OUTPUT_CONFLICT:'保存先に異なるファイルがあります。',time_budget:'実行時間上限です。延長して再開できます。',ENGINE_PROTOCOL_ERROR:'エンジンの構造化出力を確認できません。',HOST_EXIT_UNPROVEN:'プロセスの終了を確認できません。',OUTPUT_OWNERSHIP_UNRESOLVED:'保存先の所有プロセスを確認できません。',shutdown:'アプリ終了に伴う一時停止',DATE_CONTRACT_UNVERIFIED:'このサイトの期間指定は未対応です。',CREDENTIAL_IN_USE:'未完了のSourceがこのCookie版を使用しています。',SOURCE_TERMINAL:'完了済みです。「再試行」を使用してください。',SESSION_REQUIRED:'起動リンクから接続してください。',REVISION_CONFLICT:'設定が更新されています。画面を再読み込みしてください。'};
reasons.CREDENTIAL_REPLACEMENT_REQUIRED='元のCookie版は削除されています。新しいCookieを取り込み、ダウンロード画面で選択してから再試行してください。';
reasons.ENGINE_STALLED='5分間応答がありません。自動再開は最大2回です。ログを確認できます。';
reasons.ARCHIVE_INVALID='ZIPが破損・暗号化されているか、検証上限（展開量4GiB／1万項目）を超えています。';
reasons.INPUT_INVALID='入力を確認してください。URLはhttp://またはhttps://で指定し、設定値を有効な範囲にしてください。入力内容は保持しています。';
reasons.COOKIE_INVALID='Cookieファイルの形式または有効期限が不正です。サイトからNetscape TXTまたは対応JSONで書き出し直してください。登録は行っていません。';
reasons.BOOTSTRAP_INVALID='起動リンクが無効または使用済みです。接続済みのブラウザを使うか、アプリを再起動して新しいリンクで接続してください。';
reasons.TIME_BUDGET_INVALID='実行上限は1分〜168時間の数値で指定してください。現在の実行上限は変更していません。';
reasons.OUTPUT_UNAVAILABLE='保存先へ書き込めないか、別の処理が使用しています。ローカルの保存先とアクセス権を確認してください。登録は行っていません。';

function notice(message:string, error=false) { const el=$('notice'); el.textContent=message; el.hidden=false; el.classList.toggle('error',error); }
function applyTheme(dark:boolean) {
  document.documentElement.dataset.theme=dark?'dark':'light';
  $('theme').setAttribute('aria-pressed',String(dark));
  $('theme').title=dark?'ライトモードに切り替え':'ダークモードに切り替え';
}
const themeKey='downloader-tools.theme';
let initialDark=matchMedia('(prefers-color-scheme: dark)').matches;
try { const saved=localStorage.getItem(themeKey); if(saved!==null)initialDark=saved==='dark'; } catch {}
applyTheme(initialDark);
$('theme').onclick=()=>{
  const dark=document.documentElement.dataset.theme!=='dark';
  try {localStorage.setItem(themeKey,dark?'dark':'light');applyTheme(dark);}
  catch {notice('テーマを保存できませんでした。ブラウザーの保存設定を確認してください。',true);}
};
async function api<T=any>(path:string, body?:unknown, method?:string, key?:string):Promise<T> {
  const headers:Record<string,string>={'X-CSRF-Token':csrf};
  if(body!==undefined) headers['Content-Type']='application/json';
  if(key) headers['Idempotency-Key']=key;
  const response=await fetch('/api/v1/'+path,{method:method||(body===undefined?'GET':'POST'),headers,body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok) throw new Error(reasons[data.error?.code]||data.error?.message||`通信エラー (${response.status})`);
  return data as T;
}
function node<K extends keyof HTMLElementTagNameMap>(tag:K,text?:string,cls?:string) {const el=document.createElement(tag);if(text)el.textContent=text;if(cls)el.className=cls;return el;}
function button(text:string,action:()=>Promise<unknown>|unknown,cls='text-button') {const b=node('button',text,cls);b.type='button';b.onclick=()=>run(b,action);return b;}
async function run(button:HTMLButtonElement,action:()=>Promise<unknown>|unknown) {button.disabled=true;try{await action();}catch(error){notice(String((error as Error).message),true);}finally{button.disabled=false;}}
function empty(target:HTMLElement,title:string,description:string) {const el=node('div',undefined,'empty-state');el.append(node('h3',title),node('p',description));target.replaceChildren(el);}
const bytes=(value?:number)=>value==null?'—':value>=1024**3?(value/1024**3).toFixed(1)+' GiB':value>=1024**2?(value/1024**2).toFixed(1)+' MiB':(value/1024).toFixed(0)+' KiB';
function sourceCard(source:Source) {
  const article=node('article',undefined,'source-card');article.dataset.id=source.id;
  const icon=node('div',source.kind==='video'?'▶':'▧','source-icon');
  const info=node('div',undefined,'source-info');
  const title=node('div',undefined,'source-title');title.append(node('strong',source.display_url),node('span',states[source.state]||source.state,'state state-'+source.state));
  const meta=node('p',`${source.provider.replace('generic:','')} · 保存 ${source.completed_count}件 · スキップ ${source.skipped_count}件`,'source-meta');
  if(source.reason)meta.append(node('span',' · '+(reasons[source.reason]||source.reason)));
  const progress=node('div',undefined,'progress-track');const bar=node('progress');bar.max=1;
  if(source.progress.total_bytes)bar.value=Math.min(1,(source.progress.downloaded_bytes||0)/source.progress.total_bytes);
  else if(source.state!=='running')bar.value=terminal.has(source.state)?1:0;
  bar.setAttribute('aria-label','この項目の転送進捗');progress.append(bar);
  const speed=node('span',undefined,'source-speed');speed.textContent=source.state==='running'?`${bytes(source.progress.downloaded_bytes)} / ${bytes(source.progress.total_bytes)} · ${bytes(source.progress.speed)}/s`:source.counts_complete?'件数確定':'総件数は未確定';
  info.append(title,meta,progress,speed);
  const actions=node('div',undefined,'source-actions');
  const control=async(action:string)=>{let path=`sources/${source.id}/${action}`;if(action==='resume'&&source.reason==='time_budget'){const hours=prompt('新しい累積実行上限（時間）','12');if(!hours)return;path+='?budget='+Math.floor(Number(hours)*3600);}await api(path,{});await refresh();};
  if(!terminal.has(source.state)) {
    if(source.state==='queued'||source.state==='running')actions.append(button('一時停止',()=>control('pause')));
    if(source.state==='paused'||source.state==='blocked'||source.state==='stopping')actions.append(button('再開',()=>control('resume')));
    actions.append(button('キャンセル',()=>control('cancel')));
  } else if(source.state!=='succeeded') actions.append(button('再試行',async()=>{await api('retries',{source_id:source.id,credential_id:select('credential').value||null},'POST',crypto.randomUUID());notice('新しいSourceを追加しました。');await refresh();}));
  actions.append(button('詳細',()=>detail(source.id)));
  article.append(icon,info,actions);return article;
}
function renderSnapshot(snapshot:Snapshot){
  const list=$('queue-list');
  const incoming=new Set(snapshot.sources.map(s=>s.id));
  for(const id of sourceMap.keys())if(!incoming.has(id))sourceMap.delete(id);
  for(const source of snapshot.sources){const old=sourceMap.get(source.id);if(!old||source.revision>=old.revision)sourceMap.set(source.id,source);}
  list.replaceChildren(...[...sourceMap.values()].map(sourceCard));
  if(!sourceMap.size)empty(list,'キューは空です','保存したいメディアのURLを追加してください。');
  $('running-count').replaceChildren(document.createTextNode(String(snapshot.diagnostics.running)),node('small',' 件'));
  $('waiting-count').replaceChildren(document.createTextNode(String(snapshot.sources.filter(s=>s.state==='queued'||s.state==='paused').length)),node('small',' 件'));
  $('saved-count').replaceChildren(document.createTextNode(String(snapshot.sources.reduce((n,s)=>n+s.completed_count,0))),node('small',' ファイル'));
  if(snapshot.diagnostics.error)notice('実行を保留しています: '+snapshot.diagnostics.error,true);
}
async function refresh(){renderSnapshot(await api<Snapshot>('snapshot'));}
function updateProgress(message:{source_id:string;progress:Progress}){
  const source=sourceMap.get(message.source_id);if(!source||terminal.has(source.state)||source.attempt_id!==message.progress.attempt_id)return;
  if((source.progress.seq||0)>=(message.progress.seq||0))return;
  source.progress=message.progress;
  const card=[...$('queue-list').children].find(el=>(el as HTMLElement).dataset.id===source.id);
  if(card){const progress=card.querySelector('progress')!;if(source.progress.total_bytes)progress.value=Math.min(1,(source.progress.downloaded_bytes||0)/source.progress.total_bytes);card.querySelector('.source-speed')!.textContent=`${bytes(source.progress.downloaded_bytes)} / ${bytes(source.progress.total_bytes)} · ${bytes(source.progress.speed)}/s`;}
}
async function history(reset=true){if(reset){historyCursor=null;$('history-list').replaceChildren();}const page=await api<{items:Source[];next_cursor:number|null}>('sources?limit=50'+(historyCursor?'&cursor='+historyCursor:''));$('history-list').append(...page.items.map(sourceCard));historyCursor=page.next_cursor;$('more-history').hidden=!historyCursor;if(!$('history-list').children.length)empty($('history-list'),'履歴はまだありません','受付済みのSourceがここに表示されます。');}
async function followLog(id:string){
  if(detailSource!==id||!$<HTMLDialogElement>('detail-dialog').open)return;
  let delay=1000;
  try{
    const log=await api<{text:string;next_offset:number}>(`sources/${id}/logs?offset=${detailOffset}`);
    if(detailSource!==id)return;
    const target=$('detail-log');
    const atBottom=target.scrollHeight-target.scrollTop-target.clientHeight<40;
    const previous=detailOffset===0?'':target.textContent||'';
    target.textContent=(previous+log.text).slice(-200000)||'ログの出力を待っています…';
    if(atBottom)target.scrollTop=target.scrollHeight;
    delay=log.next_offset-detailOffset>=65532?100:1000;
    detailOffset=log.next_offset;
  }catch(error){if(detailSource===id)notice(`ログ取得: ${(error as Error).message}`,true);delay=5000;}
  if(detailSource===id)detailTimer=window.setTimeout(()=>void followLog(id),delay);
}
async function detail(id:string){const source=await api<Source&{profile:unknown;artifacts:{relative_path:string;valid:boolean}[];references:number}>('sources/'+id);const content=$('detail-content');content.replaceChildren(node('p',`${states[source.state]} · 保存 ${source.completed_count}件`),node('p',`ツールセット: ${source.toolset_id}`),node('p',`参照Batch: ${source.references}件（停止操作はすべての参照先へ影響します）`));const pre=node('pre',JSON.stringify(source.profile,null,2));content.append(pre,button('保存先を開く',()=>api('outputs/open',{output_root_id:source.output_root_id}),'secondary'));for(const artifact of source.artifacts)content.append(node('p',(artifact.valid?'✓ ':'欠損・変更あり: ')+artifact.relative_path));clearTimeout(detailTimer);detailSource=id;detailOffset=0;$('detail-log').textContent='ログを読み込んでいます…';$<HTMLDialogElement>('detail-dialog').showModal();void followLog(id);}
async function loadCredentials(){const credentials=await api<Credential[]>('credentials');const options=[new Option('使用しない',''),...credentials.map(c=>new Option(c.name,c.id))];select('credential').replaceChildren(...options);const target=$('credential-list');target.replaceChildren(...credentials.map(c=>{const row=node('div',undefined,'credential-row');row.append(node('div',`${c.name} · v${c.version}`),node('small',c.domains.join(', ')),button('名前を変更',async()=>{const name=prompt('新しい表示名',c.name);if(name){await api('credentials/'+c.id,{name},'PATCH');await loadCredentials();}}),button('削除',async()=>{await api('credentials/'+c.id,undefined,'DELETE');await loadCredentials();}));return row;}));if(!credentials.length)empty(target,'Cookieは未登録です','認証が必要なサイトを利用する場合に取り込んでください。');}
async function loadSettings(){
  const [s,roots,diagnostics]=await Promise.all([api<Settings>('settings'),api<Root[]>('output-roots'),api<{tools:Tool[]}>('diagnostics')]);settings=s;
  input('total-slots').value=String(s.total_slots);input('video-slots').value=String(s.video_slots);input('gallery-slots').value=String(s.gallery_slots);input('time-budget').value=String(s.time_budget/3600);
  select('output-root').replaceChildren(...roots.map(r=>new Option((r.id==='default'?'標準 · ':'')+r.path,r.id)));
  $('roots-list').replaceChildren(...roots.map(r=>{const row=node('div',undefined,'credential-row');row.append(node('code',r.path),button('開く',()=>api('outputs/open',{output_root_id:r.id})));return row;}));
  select('toolset-choice').replaceChildren(...diagnostics.tools.map(t=>new Option(t.id+(t.active?'（使用中）':''),t.id)));
  $('toolsets-list').replaceChildren(...diagnostics.tools.map(t=>node('p',`${t.active?'● 使用中':'○'} ${t.id} · ${Object.entries(t.versions).map(([k,v])=>`${k} ${v}`).join(' / ')}`,'help')));
  if(!diagnostics.tools.length)$('toolsets-list').textContent='検証済みtoolsetがありません。セットアップを実行してください。';
  $('diagnostics').textContent=JSON.stringify(diagnostics,null,2);
}
const descriptions:Record<string,[string,string]>={download:['ダウンロード','URLを追加して、動画と画像をまとめて保存。'],history:['履歴','取得結果と保存済みファイルを確認。'],credentials:['認証','サイトごとのCookieを、このPC内で管理。'],settings:['設定','実行予算と保存先、ツールセットを管理。']};
async function tab(name:string){currentTab=name;for(const key of Object.keys(descriptions))$('panel-'+key).hidden=key!==name;for(const button of document.querySelectorAll<HTMLButtonElement>('[data-tab]')){if(button.dataset.tab===name)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}[$('page-title').textContent,$('page-description').textContent]=descriptions[name];if(name==='history')await history();if(name==='credentials')await loadCredentials();if(name==='settings')await loadSettings();}
async function initialize(){
  const bootstrap=location.hash.startsWith('#token=')?decodeURIComponent(location.hash.slice(7)):null;
  historyApiReplace();
  let session;
  try { session=await api('session'); } catch(error) { if(!bootstrap)throw error;session=await api('session',{token:bootstrap}); }
  csrf=session.csrf;
  await Promise.all([refresh(),loadCredentials(),loadSettings()]);
  const stream=new EventSource('/api/v1/events');
  stream.addEventListener('snapshot',event=>{renderSnapshot(JSON.parse((event as MessageEvent).data));$('connection').textContent='接続済み';clearTimeout(polling);polling=undefined;pollDelay=2000;});
  stream.addEventListener('refresh',()=>{void refresh().catch(e=>notice(e.message,true));if(currentTab==='history')void history().catch(e=>notice(e.message,true));});
  stream.addEventListener('progress',event=>updateProgress(JSON.parse((event as MessageEvent).data)));
  stream.onerror=()=>{$('connection').textContent='再接続しています…';if(!polling){const poll=async()=>{try{await refresh();}catch{}pollDelay=Math.min(30000,pollDelay*2);polling=window.setTimeout(poll,document.hidden?30000:pollDelay);};polling=window.setTimeout(poll,pollDelay);}};
}
function historyApiReplace(){window.history.replaceState(null,'',location.pathname+location.search);}
for(const nav of document.querySelectorAll<HTMLButtonElement>('[data-tab]'))nav.onclick=()=>run(nav,()=>tab(nav.dataset.tab!));
for(const radio of document.querySelectorAll<HTMLInputElement>('[name="kind"]'))radio.onchange=()=>{$('video-options').hidden=radio.value!=='video';$('gallery-options').hidden=radio.value!=='gallery';};
$<HTMLFormElement>('download-form').onsubmit=event=>{event.preventDefault();void run($<HTMLButtonElement>('submit-download'),async()=>{const kind=document.querySelector<HTMLInputElement>('[name="kind"]:checked')!.value;const urls=$<HTMLTextAreaElement>('urls').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);if(!urls.length||urls.length>100)throw new Error('URLは1〜100件で入力してください。');const profile=await api('profiles',{name:'カスタム',kind,container:kind==='video'?select('container').value:'original',height:Number(select('height').value),playlist:input('playlist').checked,media_policy:select('media-policy').value});const result=await api('batches',{kind,urls,profile_id:profile.id,credential_id:select('credential').value||null,output_root_id:select('output-root').value},'POST',crypto.randomUUID());const shared=result.sources.filter((s:{shared:boolean})=>s.shared).length;notice(`キューに追加しました。${shared?` ${shared}件は既存Sourceへの参照です。`:''}`);$<HTMLTextAreaElement>('urls').value='';await refresh();});};
$<HTMLFormElement>('cookie-form').onsubmit=event=>{event.preventDefault();void run((event.submitter as HTMLButtonElement),async()=>{const file=input('cookie-file').files?.[0];if(!file||file.size>262144)throw new Error('256 KiB以下のCookieファイルを選択してください。');await api('credentials',{name:input('cookie-name').value,content:await file.text()});input('cookie-file').value='';input('cookie-name').value='';notice('Cookieを新しい版として取り込みました。');await loadCredentials();});};
$<HTMLFormElement>('settings-form').onsubmit=event=>{event.preventDefault();void run(event.submitter as HTMLButtonElement,async()=>{settings=await api('settings',{...settings,total_slots:Number(input('total-slots').value),video_slots:Number(input('video-slots').value),gallery_slots:Number(input('gallery-slots').value),time_budget:Number(input('time-budget').value)*3600},'PATCH');notice('実行予算を保存しました。');});};
$<HTMLFormElement>('root-form').onsubmit=event=>{event.preventDefault();void run(event.submitter as HTMLButtonElement,async()=>{await api('output-roots',{path:input('root-path').value});input('root-path').value='';await loadSettings();notice('保存先を検証して登録しました。');});};
$('refresh').onclick=()=>void refresh().catch(e=>notice(e.message,true));$('history-refresh').onclick=()=>void history().catch(e=>notice(e.message,true));$('more-history').onclick=()=>void history(false).catch(e=>notice(e.message,true));
$('close-detail').onclick=()=>$<HTMLDialogElement>('detail-dialog').close();
$<HTMLDialogElement>('detail-dialog').addEventListener('close',()=>{detailSource=null;clearTimeout(detailTimer);});
$<HTMLButtonElement>('backup').onclick=()=>run($('backup'),async()=>{await api('backups',{});notice('data/backupsにバックアップを保存しました。');});
$<HTMLButtonElement>('activate-toolset').onclick=()=>run($('activate-toolset'),async()=>{await api('toolset-updates',{toolset_id:select('toolset-choice').value});await loadSettings();notice('新規受付用のツールセットを切り替えました。');});
$<HTMLButtonElement>('update-toolset').onclick=()=>run($('update-toolset'),async()=>{const job=await api('toolset-updates',{});$('update-status').textContent='公式安定版を取得し、独立環境で検証しています…';await new Promise<void>((resolve,reject)=>{const timer=window.setInterval(async()=>{try{const result=await api('toolset-updates/'+job.id);if(result.state==='completed'){$('update-status').textContent='検証完了: '+result.toolset_id;clearInterval(timer);await loadSettings();resolve();}else if(result.state==='failed'){clearInterval(timer);reject(new Error('ツールセット更新に失敗しました。旧版を引き続き使用します。'));}}catch(error){clearInterval(timer);reject(error);}},3000);});});
void initialize().catch(error=>{$('connection').textContent='接続できません';notice(error.message,true);});
