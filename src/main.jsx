import React, {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import ReactMarkdown from 'react-markdown';
import {ArrowUp, ArrowUpRight, BookOpen, Check, ChevronDown, Copy, CornerDownRight, FileText, Info, LoaderCircle, Plus, RotateCcw, Sparkles, Square, Video, X} from 'lucide-react';
import './style.css';
import {beginAskLogin, finishAskLogin, logoutAsk, readAskAccount, takeAskDraft} from './account-client.js';

// Every user-facing mode, label and message lives here, so the wording can be reviewed in one place.
// Two ways to ask. 想明白 explains (intent understand); with the user's situation switched on it
// applies the material to them (intent apply). 从哪读起 picks what to read first (intent find).
const MODES = [
  {id: 'ask', label: '想明白', hint: '问一个概念、一个判断，或一直没想通的地方。', placeholder: '有什么你一直想弄明白的问题？',
    personalHint: '回答会结合你的目标、现状和卡点，也会指出还缺什么信息。', personalPlaceholder: '说说你在做的事，以及卡在哪里。'},
  {id: 'find', label: '从哪读起', hint: '说一个话题，AI挑出最值得先读的文章和视频，说明各讲什么、从哪篇开始。', placeholder: '比如：想系统了解fake work，从哪几篇读起？'},
];
const intentOf = (mode, personal) => (mode === 'find' ? 'find' : personal ? 'apply' : 'understand');
const EXAMPLES = [
  {tag: '学习与成长', question: '我做出了几个AI项目，怎么知道自己是真的学会了？', intent: 'apply'},
  {tag: '工作与价值', question: '用AI效率变高了，为什么我的工作价值没变？', intent: 'understand'},
  {tag: '产品与判断', question: '有一个能跑的demo，怎么判断值不值得继续做？', intent: 'apply'},
  {tag: '创作与表达', question: '想开始做自媒体，最应该先想清楚什么？', intent: 'understand'},
  {tag: '认知与人生', question: '立正说的「良质」是什么意思，和AI有什么关系？', intent: 'understand'},
  {tag: '从哪读起', question: '想了解立正怎么看职业选择和个人价值，先读哪几篇、看哪几期？', intent: 'find'},
];
const BACKGROUND = [
  {key: 'goal', label: '希望达到什么结果', prompt: '希望达到的结果', placeholder: '例如：让产品有第一批持续使用的用户', max: 700},
  {key: 'facts', label: '现在的情况、卡点或限制', prompt: '目前的情况与限制', placeholder: '发生了什么？你觉得卡在哪？有哪些不能忽略的条件？', max: 1000, multiline: true},
  {key: 'tried', label: '已经试过什么', prompt: '已经试过的办法', placeholder: '做过哪些尝试，得到什么反馈？', max: 700},
];
const STEPS = ['查找原文', '匹配材料', '整理回答', '核对出处'];
const STAGE = {retrieving: 0, matching: 1, thinking: 2, drafting: 2, checking: 3, repairing: 3};
const KIND = {application: '结合你的处境', source: '材料里的观点', synthesis: 'AI综合'};
const NOTICE = '提问会保存30天，用于改进回答。请勿填写私密信息。';
const LINKS = {
  context: 'https://github.com/sunyuzheng/lizheng-open-context',
  site: 'https://www.lizheng.ai/',
  community: 'https://www.superlinear.academy/c/tools/lizheng-context',
};
const MESSAGES = {
  quota: '今天的3次已经用完。北京时间每天0点恢复；Founding Member验证后不限次。',
  membership: '暂时无法核验Founding Member身份，请稍后再试。这次不扣次数。',
  busy: '现在提问的人有点多，请稍等一会儿再试。',
  unreachable: '暂时连不上，请稍后再试。',
  stream: '回答没能完整读到，可以重新生成。',
  failed: '回答没有完成。问题和已找到的材料都还在，可以重新生成。',
  lost: '连接中断了。问题和已找到的材料都还在，可以重新生成。',
  timeout: '这次等得太久，已经停止等待。问题和材料都还在，可以重新生成。',
  stopped: '已停止。问题还在，可以改一改再发。',
  copy: '浏览器没有允许复制，可以直接选中文字复制。',
};

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const scrollBehavior = () => (reducedMotion() ? 'auto' : 'smooth');
const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const contextText = background => BACKGROUND
  .filter(field => background[field.key].trim())
  .map(field => `${field.prompt}：${background[field.key].trim()}`)
  .join('\n');

// The 立正 seal from lizheng.ai (potrace of the handwritten mark).
const MARK_PATHS = [
  'M2555 9534 c-148 -22 -200 -45 -219 -98 -11 -30 108 -234 195 -334 116 -134 197 -334 234 -582 28 -187 91 -280 233 -348 213 -101 471 -15 724 241 163 164 193 243 176 467 -21 269 -409 563 -838 635 -91 16 -440 29 -505 19z',
  'M10920 8853 c-22 -4 -96 -31 -295 -108 -283 -110 -1112 -277 -1830 -370 -297 -39 -319 -42 -460 -65 -71 -11 -161 -24 -200 -29 -89 -11 -118 -25 -150 -72 -88 -126 -14 -261 334 -610 189 -189 255 -206 516 -134 368 102 423 116 510 134 49 11 92 18 96 15 22 -13 50 -179 71 -429 13 -148 20 -2857 8 -2868 -3 -2 -106 -14 -230 -26 -124 -12 -236 -24 -249 -27 l-24 -5 7 403 c4 222 9 437 11 478 3 41 9 170 15 285 6 116 15 271 21 345 26 320 6 408 -119 535 -240 243 -590 357 -797 261 -137 -64 -162 -143 -95 -296 111 -254 142 -398 165 -775 12 -203 5 -1336 -9 -1352 -3 -3 -192 -30 -411 -58 -238 -31 -491 -43 -550 -26 -76 22 -226 26 -282 7 -128 -42 -197 -158 -168 -281 21 -90 87 -185 294 -419 231 -261 324 -293 594 -202 573 193 1180 330 1772 400 348 41 333 40 810 61 178 8 647 5 760 -5 306 -27 432 -50 714 -131 334 -97 479 -72 565 99 94 185 70 372 -67 533 -172 203 -618 493 -714 465 -10 -2 -76 -23 -148 -45 -259 -81 -442 -112 -855 -147 -36 -3 -71 -7 -78 -10 -14 -4 -10 960 4 1211 l6 100 76 1 c43 1 154 13 247 28 94 15 303 40 465 56 367 36 404 46 512 148 114 106 162 275 110 379 -34 66 -170 182 -352 298 -203 130 -271 133 -516 24 -187 -83 -506 -190 -522 -175 -9 10 16 379 38 548 48 383 0 504 -266 672 -128 81 -118 75 -113 80 21 21 595 102 964 136 375 35 415 45 507 135 117 115 162 313 94 416 -69 103 -199 196 -465 331 -182 92 -213 100 -321 81z',
  'M4090 8234 c-30 -8 -104 -31 -164 -50 -235 -76 -471 -128 -1036 -228 -847 -150 -1321 -208 -1522 -186 -173 19 -276 -4 -351 -77 -124 -120 -103 -231 84 -444 374 -424 509 -513 694 -459 39 12 91 27 116 34 25 7 104 36 175 63 628 244 1608 430 2224 422 295 -4 290 -4 350 16 133 44 222 207 200 365 -23 163 -126 272 -434 458 -167 102 -223 116 -336 86z',
  'M3583 7060 c-137 -29 -177 -94 -138 -226 81 -277 -22 -784 -308 -1519 -133 -341 -387 -874 -448 -941 -16 -17 -44 -24 -152 -38 -72 -10 -240 -33 -372 -52 -132 -19 -409 -59 -615 -89 -608 -88 -927 -115 -1014 -86 -131 44 -312 -41 -346 -163 -27 -97 -3 -179 89 -296 28 -36 70 -90 93 -120 23 -30 95 -119 161 -198 224 -270 355 -332 542 -258 99 39 165 66 200 81 17 7 71 28 120 45 50 17 101 36 115 41 80 29 438 129 535 149 22 4 108 22 190 39 491 102 1123 161 1729 161 438 0 689 -24 986 -93 188 -44 320 -45 399 -4 128 68 231 232 239 382 8 146 -56 242 -328 494 -312 289 -369 303 -815 206 -124 -27 -365 -62 -690 -99 -121 -15 -242 -29 -270 -32 l-50 -7 76 84 c314 350 594 792 749 1184 17 42 44 82 90 130 298 317 236 607 -211 976 -268 222 -402 282 -556 249z',
  'M1692 6530 c-39 -16 -74 -119 -121 -359 -118 -591 -35 -1006 272 -1363 323 -374 673 -205 657 317 -14 436 12 540 246 1015 74 150 134 276 134 281 0 34 -42 9 -177 -106 -191 -164 -309 -259 -407 -329 l-80 -57 -72 144 c-170 342 -332 505 -452 457z',
];
function Mark({className = ''}) {
  return <svg className={`mark ${className}`} viewBox="0 0 1219.044577 649.234004" aria-hidden="true" focusable="false">
    <g transform="translate(-17.980429,953.72375) scale(0.1,-0.1)" fill="currentColor">{MARK_PATHS.map(d => <path key={d.slice(0, 16)} d={d}/>)}</g>
  </svg>;
}

// Chinese text may break anywhere; keep each phrase together and break after punctuation.
function Phrases({text}) {
  const parts = String(text).match(/[^，。：；、？！]+[，。：；、？！]*/g) || [text];
  return parts.length < 2 ? text : parts.map((part, index) => <span key={index} className="phrase">{part}</span>);
}

function Kind({kind}) {
  return <span className={`kind kind-${kind || 'synthesis'}`}>{KIND[kind] || KIND.synthesis}</span>;
}
const isVideo = source => source.source_type?.includes('video');
const sourceLabel = source => (isVideo(source) ? '视频' : source.source_type === 'context' ? 'AI整理' : '文章');
const sourceDate = source => source.date?.slice(0, 10) || '日期未标明';

function SourceCard({source, selected, onOpen, prefix}) {
  return <article id={`${prefix}-source-${source.id}`} className={`source ${selected ? 'selected' : ''}`}>
    <div className="source-meta">
      <span className="source-num">{source.id.slice(1)}</span>
      <span>{isVideo(source) ? <Video size={14}/> : <FileText size={14}/>}{sourceLabel(source)}</span>
      <time>{sourceDate(source)}</time>
    </div>
    <a className="source-title" href={source.url} target="_blank" rel="noreferrer">{source.title}<ArrowUpRight size={15}/></a>
    {source.timecode && <a className="source-time" href={source.url} target="_blank" rel="noreferrer">从 {source.timecode} 开始看</a>}
    {source.reason && <p className="source-reason">{source.reason}</p>}
    <details onToggle={event => { if (event.currentTarget.open) onOpen?.(source.id); }}>
      <summary>看片段<ChevronDown size={14}/></summary>
      <p className="excerpt">{source.excerpt}</p>
      <p className="attribution">{source.author && `${source.author} · `}{source.attribution_note}</p>
      {source.public_copy_url && <a className="public-copy" href={source.public_copy_url} target="_blank" rel="noreferrer">阅读公开资料副本<ArrowUpRight size={13}/></a>}
    </details>
  </article>;
}

function Markdown({text, sources, onSelect}) {
  const content = String(text || '').replace(/\[(S\d+)\](?!\()/g, '[$1](#cite-$1)');
  return <ReactMarkdown components={{a: ({href, children}) => {
    if (href?.startsWith('#cite-')) {
      const id = href.slice(6);
      return sources.some(source => source.id === id)
        ? <button type="button" className="cite" onClick={() => onSelect(id)} aria-label={`查看出处${id.slice(1)}`}>{id.slice(1)}</button>
        : null;
    }
    return <span>{children}</span>;
  }}}>{content}</ReactMarkdown>;
}

function SectionBlock({section, sources, onSelect}) {
  return <section className="answer-section">
    <div className="section-head"><h3>{section.heading}</h3><Kind kind={section.kind}/></div>
    <Markdown text={section.body} sources={sources} onSelect={onSelect}/>
    {section.source_ids?.length > 0 && <div className="section-sources">
      <span>出处</span>
      {section.source_ids.map(id => <button type="button" key={id} onClick={() => onSelect(id)} aria-label={`查看出处${id.slice(1)}`}>{id.slice(1)}</button>)}
    </div>}
  </section>;
}

function Working({message, elapsed, onStop, onSelect}) {
  const step = STAGE[message.progress?.stage] ?? 0;
  const candidates = message.previewSources || [];
  const stale = message.lastActivity && Date.now() - message.lastActivity > 12000;
  return <section className="working" aria-label="回答进度">
    <div className="working-top">
      <LoaderCircle size={18} className="spin" aria-hidden="true"/>
      <p role="status">{message.progress?.message || '正在查找相关公开材料…'}</p>
      <button type="button" className="ghost-button" onClick={onStop}><Square size={11} fill="currentColor"/>停止</button>
    </div>
    <ol className="steps" aria-label="处理步骤">
      {STEPS.map((label, index) => <li key={label} className={index < step ? 'done' : index === step ? 'current' : ''} aria-current={index === step ? 'step' : undefined}>
        <span className="step-dot">{index < step ? <Check size={11} strokeWidth={3}/> : null}</span>{label}
      </li>)}
    </ol>
    <p className="working-note">
      <span>{message.model} · 已用{elapsed}秒</span>
      <span>{!message.lastActivity ? '正在连接服务…' : stale ? '有一会儿没收到新进展了，还在等。' : candidates.length ? '回答还在整理，可以先读找到的材料。' : '找到材料后，会先在这里给你看。'}</span>
    </p>
    {elapsed >= 30 && <p className="working-note">这次整理得久一些。可以先读原文；停止也会保留问题和已找到的材料。</p>}
    {message.approach && <div className="approach">
      <h3>回答思路</h3>
      <p>{message.approach.summary}</p>
      {message.approach.questions?.length > 0 && <ul>{message.approach.questions.map(item => <li key={item}>{item}</li>)}</ul>}
      {message.approach.note && <small>{message.approach.note}</small>}
    </div>}
    {message.partial && <div className="partial">
      <h3>正在写的回答</h3>
      <small>这些段落的出处编号已经核对，完整回答还在生成。</small>
      {message.partial.sections.map((section, index) => <SectionBlock key={index} section={section} sources={message.partial.sources} onSelect={onSelect}/>)}
    </div>}
    {candidates.length > 0 && <Candidates sources={candidates} turnId={message.id} onSelect={onSelect}/>}
  </section>;
}

function Candidates({sources, turnId, onSelect}) {
  return <div className="candidates">
    <div className="candidates-head"><BookOpen size={15}/><h3>已找到的材料</h3><span>候选，不一定都会用上</span></div>
    {sources.slice(0, 2).map(source => <article className="candidate" key={`${source.id}-${source.url}`}>
      <a href={source.url} target="_blank" rel="noreferrer">{source.title}<ArrowUpRight size={14}/></a>
      <time>{sourceDate(source)}</time>
      <p>{source.excerpt?.slice(0, 150)}{source.excerpt?.length > 150 ? '…' : ''}</p>
    </article>)}
    {sources.length > 2 && <details className="candidates-all">
      <summary>看全部{sources.length}份候选材料<ChevronDown size={14}/></summary>
      {sources.map(source => <SourceCard key={`${source.id}-${source.url}`} source={source} prefix={`turn-${turnId}`} onOpen={id => onSelect(id)}/>)}
    </details>}
  </div>;
}

function AccountLine({account, busy, onLogin, onLogout, onRetry}) {
  if (!account?.enabled) return null;
  if (account.unavailable) return <p className="account"><span>暂时读不到今天的次数。</span><button type="button" className="text-button" onClick={onRetry}>重试</button></p>;
  if (account.founding) return <p className="account"><span className="account-strong">Founding Member · 不限次</span><button type="button" className="text-button" disabled={busy} onClick={onLogout}>退出</button></p>;
  const remaining = account.remaining ?? 3;
  return <p className="account">
    <span className={remaining === 0 ? 'account-empty' : 'account-strong'}>{remaining === 0 ? '今天的3次已用完，北京时间0点恢复' : `今天还能问${remaining}次`}</span>
    {account.authenticated
      ? <><span>已登录，未核验到Founding资格</span><button type="button" className="text-button" disabled={busy} onClick={onLogout}>退出</button></>
      : account.login_ready && <button type="button" className="text-button" disabled={busy} onClick={onLogin}>Founding Member？验证后不限次</button>}
  </p>;
}

function About({close, meta, account, focusInput}) {
  const ref = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.focus();
    if (focusInput) requestAnimationFrame(() => document.getElementById('about-input')?.scrollIntoView({block: 'start'}));
    const handler = event => {
      if (event.key === 'Escape') close();
      if (event.key !== 'Tab') return;
      const items = ref.current?.querySelectorAll('button,a');
      const first = items?.[0], last = items?.[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', handler);
    return () => { document.removeEventListener('keydown', handler); previous?.focus(); };
  }, []);
  return <div className="backdrop" onClick={close}>
    <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="about-title" tabIndex={-1} ref={ref} onClick={event => event.stopPropagation()}>
      <button type="button" className="icon-button dialog-close" aria-label="关闭说明" onClick={close}><X size={20}/></button>
      <p className="eyebrow">关于问问立正</p>
      <h2 id="about-title">怎样用好它</h2>
      <p className="dialog-intro">它根据立正公开的文章、视频和《真本事》，帮你找到相关内容、理解观点，再联系自己的处境往下想。回答由AI生成，立正本人没有实时参与。</p>
      <div className="about-row"><h3>两种问法</h3><p><b>想明白</b>：解释一个观点、一个判断背后的关系，以及它在什么条件下成立。打开「结合我的处境」，回答会落到你的目标和卡点上。</p><p><b>从哪读起</b>：说一个话题，AI挑出最值得先读的文章和视频，说明各讲什么、从哪篇开始。记得标题或关键词时，直接在lizheng.ai搜索更快。</p></div>
      <div className="about-row"><h3>问得具体一点</h3><p>与其问「我该怎么办」，不如打开「结合我的处境」，说清想达到什么、发生了什么、试过什么，以及你觉得卡在哪里。不用先把问题想得很完美。</p></div>
      <div className="about-row"><h3>把答案带回现实</h3><p>AI可以整理材料、提出假设，但你的具体情况未必在材料里。建议是否适用，要靠你的行动和反馈来判断。也可以直接追问：这个判断成立的条件是什么？</p></div>
      <div className="about-row"><h3>随时回到出处</h3><p>文章保留日期，视频尽量链接到具体时间点。嘉宾的观点归嘉宾，AI的整理和推断也会标出来。AI可能读错或漏掉条件，重要的判断请打开原文核对；材料里没有的内容，它会说明材料不足。</p></div>
      <div className="about-row" id="about-input"><h3>关于你的输入</h3><ul>
        <li>提问文本会保存30天，用于改进回答，同时记录提问时间、模型、回答状态和耗时，30天后自动删除。</li>
        <li>不保存补充背景、对话历史和完整回答；提问记录不关联邮箱、账号或IP。</li>
        <li>当前对话只在这个页面里，刷新就会清除。</li>
        <li>提问和必要的上下文会发送给Builder Space的模型服务处理，处理规则由该服务管理。请只写愿意交给AI处理的内容。</li>
        <li>账号只用于登录和Founding资格核验，不交给模型。如果浏览器拦截了登录窗口，未发送的输入会在本机临时保留，恢复后清除，最长10分钟。</li>
      </ul></div>
      {account?.enabled && <div className="about-row"><h3>次数</h3><p>每天可以问3次，北京时间0点恢复。Superlinear的Founding Member用邮箱验证后不限次。没有完成的回答不扣次数。</p></div>}
      <div className="dialog-foot">
        <span>材料更新于{meta?.context_date || '…'}</span>
        <a href={LINKS.context} target="_blank" rel="noreferrer">Open Context<ArrowUpRight size={14}/></a>
        <a href={LINKS.site} target="_blank" rel="noreferrer">lizheng.ai<ArrowUpRight size={14}/></a>
      </div>
    </section>
  </div>;
}

function App() {
  const [meta, setMeta] = useState(null);
  const [mode, setMode] = useState('ask');
  const [question, setQuestion] = useState('');
  const [personal, setPersonal] = useState(false);
  const [editingSituation, setEditingSituation] = useState(false);
  const [background, setBackground] = useState({goal: '', facts: '', tried: ''});
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState('');
  const [about, setAbout] = useState(null);
  const [selected, setSelected] = useState('');
  const [sourceTurn, setSourceTurn] = useState(null);
  const [copied, setCopied] = useState(null);
  const [account, setAccount] = useState(null);
  const input = useRef(null), abort = useRef(null), loginCleanup = useRef(null);
  const refreshAccount = () => { void readAskAccount().then(setAccount); };

  useEffect(() => {
    finishAskLogin();
    const draft = takeAskDraft();
    if (draft) {
      setQuestion(draft.question);
      setMode(draft.intent === 'find' ? 'find' : 'ask');
      setPersonal(draft.intent === 'apply' || !!draft.context);
      if (draft.context) setBackground({goal: '', facts: draft.context, tried: ''});
    }
    refreshAccount();
    const onFocus = () => { if (!abort.current) refreshAccount(); };
    window.addEventListener('focus', onFocus);
    return () => { window.removeEventListener('focus', onFocus); loginCleanup.current?.(); };
  }, []);
  useEffect(() => {
    fetch('/api/meta').then(response => (response.ok ? response.json() : Promise.reject())).then(setMeta).catch(() => setMeta({offline: true}));
  }, []);
  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    setElapsed(0);
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [busy]);
  useEffect(() => () => abort.current?.abort(), []);
  // The question box grows with its text, up to a third of the screen.
  useLayoutEffect(() => {
    const box = input.current;
    if (!box) return;
    box.style.height = 'auto';
    box.style.height = `${Math.min(box.scrollHeight, Math.max(160, window.innerHeight / 3))}px`;
  }, [question, messages.length > 0]);

  const intent = intentOf(mode, personal);
  const situation = mode === 'ask' && personal ? contextText(background) : '';
  const login = () => {
    loginCleanup.current?.();
    loginCleanup.current = beginAskLogin({question, context: situation, intent}, refreshAccount);
  };
  const logout = () => { void logoutAsk().then(refreshAccount); };
  const openAbout = (focusInput = false) => setAbout({focusInput});
  const latestWithSources = messages.find(m => m.id === sourceTurn) || (busy ? messages.at(-1) : messages.filter(m => m.result || m.previewSources?.length).at(-1));
  const railSources = latestWithSources?.result?.sources || latestWithSources?.previewSources || [];
  const provisional = !!latestWithSources && !latestWithSources.result;
  const sourcesOnly = latestWithSources?.result?.status === 'sources-only';

  const selectSource = (id, turnId) => {
    setSourceTurn(turnId);
    setSelected(id);
    requestAnimationFrame(() => {
      const rail = document.getElementById(`rail-source-${id}`);
      const inline = document.getElementById(`turn-${turnId}-source-${id}`);
      if (rail?.getClientRects().length) {
        const box = rail.closest('.rail');
        box?.scrollTo({top: rail.offsetTop - box.clientHeight / 2 + rail.clientHeight / 2, behavior: scrollBehavior()});
        return;
      }
      inline?.closest('details:not([open])')?.setAttribute('open', '');
      inline?.scrollIntoView({behavior: scrollBehavior(), block: 'center'});
    });
  };
  const focusInput = () => requestAnimationFrame(() => {
    input.current?.focus();
    input.current?.scrollIntoView({behavior: scrollBehavior(), block: 'nearest'});
  });
  // Starters carry their intent; follow-ups stay in the current mode.
  const prefill = (value, nextIntent) => {
    if (nextIntent) { setMode(nextIntent === 'find' ? 'find' : 'ask'); setPersonal(nextIntent === 'apply'); }
    setQuestion(value); setError(''); focusInput();
  };
  const newChat = () => {
    if (busy) return;
    setMessages([]); setQuestion(''); setBackground({goal: '', facts: '', tried: ''}); setPersonal(false); setEditingSituation(false);
    setSelected(''); setSourceTurn(null); setError('');
    window.scrollTo({top: 0, behavior: scrollBehavior()});
    focusInput();
  };

  async function submit(event, retry) {
    event?.preventDefault();
    if (abort.current || (!retry && !question.trim())) return;
    const history = messages.filter(m => m.result).slice(-6).map(m => ({question: m.question, summary: m.result.summary}));
    const payload = retry?.request || {question: question.trim(), context: situation, intent, history, query_log_notice: 'v1'};
    const id = retry?.id || Date.now(), started = performance.now(), controller = new AbortController();
    let timedOut = false;
    const deadline = setTimeout(() => { timedOut = true; controller.abort(); }, 110000);
    abort.current = controller;
    setMessages(prev => [...prev.filter(m => m.id !== id), {id, question: payload.question, intent: payload.intent, context: payload.context, request: payload, previewSources: retry?.previewSources || [], model: meta?.model || 'AI', progress: {stage: 'retrieving', message: '正在查找相关公开材料…'}}]);
    setQuestion(prev => (!retry || prev.trim() === payload.question ? '' : prev));
    setBusy(true); setError(''); setSelected(''); setSourceTurn(null); setEditingSituation(false);
    requestAnimationFrame(() => document.getElementById(`turn-${id}`)?.scrollIntoView({behavior: scrollBehavior(), block: 'start'}));
    const update = patch => setMessages(prev => prev.map(m => (m.id === id ? {...m, ...(typeof patch === 'function' ? patch(m) : patch)} : m)));
    const seeQuota = value => {
      if (value && Number.isFinite(value.remaining)) setAccount(prev => (prev?.enabled && !prev.founding ? {...prev, remaining: value.remaining} : prev));
    };
    const failure = (code, message) => Object.assign(new Error(message), {code});
    try {
      const response = await fetch('/api/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload), signal: controller.signal, cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok) {
        let body;
        try { body = await response.json(); } catch {}
        if (body?.code === 'quota_exhausted') { seeQuota({remaining: 0}); throw failure('quota_exhausted', MESSAGES.quota); }
        if (body?.code === 'membership_unavailable') throw failure(body.code, MESSAGES.membership);
        throw failure(body?.code, response.status === 429 ? MESSAGES.busy : MESSAGES.unreachable);
      }
      if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw failure('invalid_stream', MESSAGES.stream);
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = '', received = false, lastActivity = 0;
      const process = frame => {
        let name = 'message';
        const data = [];
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith('event:')) name = line.slice(6).trim();
          if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        }
        if (!data.length) return;
        let value;
        try { value = JSON.parse(data.join('\n')); } catch { throw failure('invalid_stream', MESSAGES.stream); }
        if (controller.signal.aborted) return;
        if (name === 'progress') update({progress: value, ...(value.stage === 'repairing' ? {partial: undefined} : {})});
        else if (name === 'partial') update(m => ({partial: value, previewSources: [...new Map([...(m.previewSources || []), ...value.sources].map(source => [source.id, source])).values()]}));
        else if (name === 'approach') update({approach: value});
        else if (name === 'sources') update({previewSources: value.sources || []});
        else if (name === 'quota') seeQuota(value);
        else if (name === 'result') {
          received = true;
          seeQuota(value.quota);
          update({result: value, partial: undefined, elapsed: Math.round((performance.now() - started) / 1000)});
        } else if (name === 'error') throw failure(value.code, MESSAGES.failed);
      };
      try {
        while (!received) {
          const {done, value} = await reader.read();
          if (value?.length && Date.now() - lastActivity >= 1000) { lastActivity = Date.now(); update({lastActivity}); }
          buffer += decoder.decode(value, {stream: !done});
          if (buffer.length > 512000) throw failure('invalid_stream', MESSAGES.stream);
          let boundary;
          while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
            process(buffer.slice(0, boundary.index));
            buffer = buffer.slice(boundary.index + boundary[0].length);
            if (received) break;
          }
          if (done) { if (!received && buffer.trim()) process(buffer); break; }
        }
        if (!received) throw failure('connection_lost', MESSAGES.lost);
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
    } catch (err) {
      const message = timedOut || err.code === 'relay_timeout' ? MESSAGES.timeout
        : controller.signal.aborted ? MESSAGES.stopped
        : err instanceof TypeError ? MESSAGES.lost
        : String(err.message || MESSAGES.failed);
      setError(message);
      update({error: message, errorCode: controller.signal.aborted && !timedOut ? 'stopped' : err.code});
      setQuestion(prev => (prev.trim() ? prev : payload.question));
    } finally {
      clearTimeout(deadline);
      if (abort.current === controller) { controller.abort(); setBusy(false); abort.current = null; refreshAccount(); }
    }
  }

  async function copy(result) {
    const text = [
      result.summary,
      ...(result.sections || []).map(section => `${section.heading}\n${section.body}`),
      ...(result.sources || []).map(source => `${source.title}（${source.date?.slice(0, 10) || ''}）\n${source.url}`),
      '由问问立正AI根据公开材料整理，不是立正本人实时回复。',
    ].join('\n\n');
    try {
      await navigator.clipboard.writeText(text);
      setCopied(result);
      setTimeout(() => setCopied(null), 1800);
    } catch { setError(MESSAGES.copy); }
  }

  const conversation = messages.length > 0;
  const current = MODES.find(m => m.id === mode);
  // In a conversation the situation folds into one line after it has been sent once.
  const showFields = mode === 'ask' && personal && (!conversation || editingSituation);
  const filled = BACKGROUND.filter(field => background[field.key].trim()).map(field => background[field.key].trim());
  const hint = mode === 'ask' && personal ? current.personalHint : current.hint;
  const placeholder = mode === 'ask' && personal ? current.personalPlaceholder : current.placeholder;
  const outOfQuota = account?.enabled && !account.unavailable && !account.founding && account.remaining === 0;
  const composer = <div className="composer-area" id="ask-lizheng">
    <form className={`composer ${busy ? 'is-busy' : ''}`} onSubmit={submit} aria-busy={busy}>
      <div className="modes" role="group" aria-label="这次想怎样问">
        {MODES.map(m => <button key={m.id} type="button" aria-pressed={mode === m.id} onClick={() => setMode(m.id)}>{m.label}</button>)}
      </div>
      {!conversation && <p className="mode-hint">{hint}</p>}
      <label className="sr-only" htmlFor="question">你的问题</label>
      <textarea id="question" ref={input} maxLength={2000} value={question} rows={conversation ? 1 : 3}
        onChange={event => setQuestion(event.target.value)} placeholder={conversation ? (mode === 'find' ? '还想读哪方面的？' : '接着问，或者换一个问题') : placeholder}
        onKeyDown={event => {
          if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); }
        }}/>
      {mode === 'ask' && personal && !showFields && <p className="situation-line">
        <span>结合你的处境：{filled.length ? filled.join('；') : '还没有填写'}</span>
        <button type="button" className="text-button" onClick={() => setEditingSituation(true)}>{filled.length ? '修改' : '填写'}</button>
      </p>}
      {showFields && <div className="background">
        <p>说清处境，比把问题包装好更有用。三项都可以空着，只写你愿意分享的部分。</p>
        {BACKGROUND.map(field => <label key={field.key}>
          {field.label}
          {field.multiline
            ? <textarea id={`context-${field.key}`} maxLength={field.max} rows={2} value={background[field.key]} placeholder={field.placeholder} onChange={event => setBackground({...background, [field.key]: event.target.value})}/>
            : <input id={`context-${field.key}`} maxLength={field.max} value={background[field.key]} placeholder={field.placeholder} onChange={event => setBackground({...background, [field.key]: event.target.value})}/>}
        </label>)}
      </div>}
      <div className="composer-foot">
        <div className="foot-tools">
          {mode === 'ask' && <button type="button" className="personal-toggle" aria-pressed={personal} onClick={() => { setPersonal(!personal); setEditingSituation(!personal); }}>
            <span className="switch" aria-hidden="true"/>结合我的处境
          </button>}
          {conversation && <label className="mode-select">
            <span className="sr-only">这次想怎样问</span>
            <select value={mode} onChange={event => setMode(event.target.value)}>
              {MODES.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
            <ChevronDown size={14} aria-hidden="true"/>
          </label>}
        </div>
        <div className="send-group">
          {!busy && <kbd className="shortcut">{isMac ? '⌘ Enter' : 'Ctrl Enter'} 发送</kbd>}
          {busy
            ? <button type="button" className="send stop" onClick={() => abort.current?.abort()} aria-label="停止回答"><Square size={14} fill="currentColor"/></button>
            : <button type="submit" className="send" disabled={!question.trim() || outOfQuota} aria-label="发送问题"><ArrowUp size={20}/></button>}
        </div>
      </div>
    </form>
    <div className="composer-meta">
      <p className="notice">{NOTICE}<button type="button" className="text-button" onClick={() => openAbout(true)}>说明</button></p>
      <AccountLine account={account} busy={busy} onLogin={login} onLogout={logout} onRetry={refreshAccount}/>
    </div>
    {error && messages.at(-1)?.error !== error && <p className="form-error" role="alert">{error}</p>}
    {meta?.offline && <p className="form-error">暂时连不上服务。问题可以先写好，恢复后再发送。</p>}
    {meta?.mode === 'search-only' && <p className="form-note">现在只能检索原文，模型连上后才能生成回答。</p>}
  </div>;

  return <div className={`app ${conversation ? 'in-conversation' : ''}`}>
    <a className="skip-link" href="#main-content">跳到提问与回答</a>
    <header className="header">
      <div className="header-inner">
        <button type="button" className="brand" onClick={() => window.scrollTo({top: 0, behavior: scrollBehavior()})} aria-label="问问立正，回到页面顶部">
          <Mark/><span>问问立正</span>
        </button>
        <nav className="header-nav" aria-label="页面">
          {conversation && <button type="button" className="pill-button" onClick={newChat} disabled={busy}><Plus size={15}/>新问题</button>}
          <button type="button" className="header-link" onClick={() => openAbout(false)} aria-label="怎样用好它"><Info size={16}/><span>怎样用好它</span></button>
          <a className="header-link site-link" href={LINKS.site} target="_blank" rel="noreferrer">lizheng.ai<ArrowUpRight size={14}/></a>
        </nav>
      </div>
    </header>

    <main id="main-content" tabIndex={-1}>
      {!conversation ? <div className="home">
        <section className="hero">
          <p className="eyebrow">AI问答 · 基于立正公开的文章和视频</p>
          <h1>把一个问题，<br/>问得更明白。</h1>
          <p className="intro"><Phrases text="想理解一个观点，或者用到自己的处境里？AI会从立正公开的文章和视频里找相关内容，整理成回答，并标明出处。"/></p>
          <p className="identity"><Phrases text="这是AI回答，不是立正本人实时回复；重要的判断，请回到原文核对。"/></p>
        </section>
        {composer}
        <section className="starters" aria-labelledby="starters-title">
          <div className="starters-head"><h2 id="starters-title">不知道从哪问起？</h2><p>选一个，改成你自己的问题。</p></div>
          <div className="starter-grid">
            {EXAMPLES.map(example => <button type="button" key={example.question} className="starter" onClick={() => prefill(example.question, example.intent)}>
              <span className="starter-tag">{example.tag}</span><span><Phrases text={example.question}/></span>
            </button>)}
          </div>
        </section>
        <footer className="footer">
          <p>回答由AI根据公开材料整理，不是立正本人回复。材料更新于{meta?.context_date || '…'}。</p>
          <p><a href={LINKS.context} target="_blank" rel="noreferrer">材料开源在GitHub<ArrowUpRight size={13}/></a><a href={LINKS.site} target="_blank" rel="noreferrer">lizheng.ai<ArrowUpRight size={13}/></a></p>
        </footer>
      </div> : <div className="layout">
        <div className="thread-column">
          <h1 className="sr-only">问问立正，当前对话</h1>
          <section className="thread" aria-label="当前对话">
            {messages.map((m, index) => {
              const last = index === messages.length - 1;
              const working = busy && last;
              const sources = m.result?.sources || [];
              const select = id => selectSource(id, m.id);
              return <article key={m.id} id={`turn-${m.id}`} className="turn">
                <header className="question">
                  <span>你的问题</span>
                  <h2><Phrases text={m.question}/></h2>
                  {m.context && <details className="question-context"><summary>你的处境<ChevronDown size={13}/></summary><p>{m.context}</p></details>}
                </header>
                {!m.result && (working || m.previewSources?.length > 0) && (working
                  ? <Working message={m} elapsed={elapsed} onStop={() => abort.current?.abort()} onSelect={select}/>
                  : <section className="working idle"><Candidates sources={m.previewSources} turnId={m.id} onSelect={select}/></section>)}
                {m.result && <div className="answer">
                  {m.result.status === 'sources-only'
                    ? <p className="answer-meta"><BookOpen size={15}/><span>找到的材料</span></p>
                    : <p className="answer-meta"><Sparkles size={15}/><span>AI根据公开材料整理</span><small>{m.model}{m.elapsed ? ` · ${m.elapsed}秒` : ''}</small></p>}
                  <div className="summary"><Markdown text={m.result.summary} sources={sources} onSelect={select}/></div>
                  {(m.result.sections || []).map((section, i) => <SectionBlock key={i} section={section} sources={sources} onSelect={select}/>)}
                  {m.result.clarifying_questions?.length > 0 && <div className="clarify">
                    <p>再补充一点，回答会更贴合你</p>
                    {m.result.clarifying_questions.map(item => <button type="button" key={item} onClick={() => {
                      setMode('ask'); setQuestion(m.question); setPersonal(true); setEditingSituation(true);
                      setBackground(prev => ({...prev, facts: prev.facts ? `${prev.facts}\n${item}：` : `${item}：`}));
                      requestAnimationFrame(() => document.getElementById('context-facts')?.focus());
                    }}><Plus size={15}/>{item}</button>)}
                  </div>}
                  {m.result.limitations && <p className="limits"><Info size={15}/><span><b>这个回答的边界</b>{m.result.limitations}</span></p>}
                  {sources.length > 0 && <details className="inline-sources" open>
                    <summary><BookOpen size={16}/>回到{sources.length}份原文<ChevronDown size={16}/></summary>
                    <div>{sources.map(source => <SourceCard key={source.id} source={source} prefix={`turn-${m.id}`} selected={sourceTurn === m.id && selected === source.id} onOpen={id => { setSelected(id); setSourceTurn(m.id); }}/>)}</div>
                  </details>}
                  <div className="answer-actions">
                    <button type="button" className="ghost-button" onClick={() => copy(m.result)}>{copied === m.result ? <Check size={15}/> : <Copy size={15}/>}{copied === m.result ? '已复制' : m.result.status === 'sources-only' ? '复制这些出处' : '复制回答和出处'}</button>
                  </div>
                  {last && m.result.followups?.length > 0 && <div className="followups">
                    <p>可以接着问<span>点一下放进输入框，改好再发</span></p>
                    {m.result.followups.map(item => <button type="button" key={item} onClick={() => prefill(item)}><CornerDownRight size={15}/>{item}</button>)}
                  </div>}
                </div>}
                {m.error && (m.errorCode === 'quota_exhausted'
                  ? <div className="turn-alert quota" role="alert">
                      <p>{m.error}</p>
                      {account?.login_ready && !account.authenticated && <button type="button" className="pill-button" onClick={login}>验证Founding身份</button>}
                    </div>
                  : <p className={`turn-alert ${m.errorCode === 'stopped' ? 'muted' : ''}`} role="alert">{m.error}</p>)}
                {!busy && last && m.errorCode !== 'quota_exhausted' && (m.error || m.result?.retryable) && <button type="button" className="ghost-button retry" onClick={() => submit(undefined, m)}><RotateCcw size={15}/>重新生成回答</button>}
              </article>;
            })}
          </section>
          <div className="dock">{composer}</div>
        </div>
        {railSources.length > 0 && <aside className="rail" aria-label="出处">
          <div className="rail-head">
            <h2>{provisional ? '已找到的材料' : sourcesOnly ? '找到的原文' : '出处'}</h2>
            <p>{provisional ? (busy && latestWithSources === messages.at(-1) ? '候选材料，回答还在整理。' : '检索到的材料，可以先读原文。') : sourcesOnly ? `共${railSources.length}份，点开阅读。` : `回答用到的${railSources.length}份原文，点开核对。`}</p>
          </div>
          {railSources.map(source => <SourceCard key={`${source.id}-${source.url}`} source={source} prefix="rail" selected={selected === source.id} onOpen={setSelected}/>)}
          <a className="rail-foot" href={LINKS.community} target="_blank" rel="noreferrer">去社区接着聊<ArrowUpRight size={14}/></a>
        </aside>}
      </div>}
    </main>
    {about && <About close={() => setAbout(null)} meta={meta} account={account} focusInput={about.focusInput}/>}
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
// Title fonts arrive after first paint; Songti and STSong stand in until then.
// They are self-hosted: Google Fonts is blocked in mainland China. Noto Serif SC is
// split by unicode-range, so browsers fetch only the slices a page uses.
import('@fontsource/noto-serif-sc/700.css');
import('@fontsource/noto-serif-sc/900.css');
