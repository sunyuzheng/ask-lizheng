import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import ReactMarkdown from 'react-markdown';
import {Check, LoaderCircle, X} from 'lucide-react';
import './style.css';
import {askLoginHere, beginAskLogin, finishAskLogin, logoutAsk, readAskAccount, takeAskDraft} from './account-client.js';
import {askedAgo, askedLastDay, discoveryDetail, discoveryLists, discoveryPage, discoveryView, likeState, readLikes, rememberLike, voteDiscovery, DISCOVERY_LIST_SIZE} from './discovery.js';
import {forgetAll, forgetAsked, keepAsked, readHistory} from './history.js';
import {IN_APP} from './in-app.js';
import {copyWhenReady, createShareLink, IN_WECHAT, shareDisplay} from './share-link.js';
import {markUsage, startUsage, watchUsage} from './usage.js';
import {MARK_PATHS} from './mark.js';
import appStoreBadge from './badges/app-store-zh.svg';
import googlePlayBadge from './badges/google-play-zh.png';
import {answerService, PUBLIC_QA, publicQaCopy} from './public-qa.js';
import {isMemberCourse, isMemberVideo, memberJoinUrl, memberVideoUrl, sourceAccessNote, sourceCopyText, sourceTypeLabel, transcriptQualityNote} from './source-access.js';

// Every user-facing mode, label and message lives here, so the wording can be reviewed in one place.
// Two ways to ask. 想明白 explains (intent understand); with the user's situation switched on it
// applies the material to them (intent apply). 从哪读起 picks what to read first (intent find).
const MODES = [
  {id: 'ask', label: '想明白', hint: '问一个概念、一个判断，或一直没想通的地方。', placeholder: '有什么你一直想弄明白的问题？',
    personalHint: '回答会结合你的目标、现状和卡点，也会指出还缺什么信息。', personalPlaceholder: '说说你在做的事，以及卡在哪里。'},
  {id: 'find', label: '从哪读起', hint: '说一个话题，AI挑出最值得先读的文章和视频，说明各讲什么、从哪篇开始。', placeholder: '比如：想了解AI时代怎么学习，先读哪几篇？'},
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
// Every answer is labeled as AI-written at the top, so only the sections that differ say so:
// a faithful paraphrase of the material, or the AI applying it (to the reader's situation when given).
const kindLabel = (kind, personal) => kind === 'source' ? '材料里的观点'
  : kind === 'application' ? (personal ? '结合你的处境' : 'AI推演') : '';
const NOTICE = '提问会保存30天，用于改进回答。请勿填写私密信息。';
// v4, public Q&A: the notice, 说明 and the situation note come from public-qa.js, shared with
// lizheng.ai. The situation is not kept since 2026-10-04 (/api/meta says context_archive: false).
const publicQa = (meta, contextKept) => publicQaCopy('zh', {owner: '立正', self: '他', answerer: answerService(meta?.model), contextKept});
// v3, today's notice: answers are kept to improve them and only 立正 sees the records. Purpose first, in the same four parts.
const V3_PARTS = [
  ['为什么保存', '看哪些问题答得不好、缺哪些材料，把回答做得更好；也让立正知道大家关心什么。'],
  ['保存什么', '提问、完整回答和所用出处，以及匿名的使用统计。记录只有立正能看到。'],
  ['你的隐私', '提问记录不关联邮箱、账号或IP；但输入文字仍可能识别个人，发给AI前不会自动去掉。登录只用来核验Founding身份，不会和提问记在一起，也不交给模型。「结合我的处境」里填的内容不单独保存，但回答可能会提到它，所以请别填写私密信息。'],
  ['另外', '回答由AI根据立正公开的文章和视频整理，不是立正本人回复。提问和必要背景会发给Builder Space的模型服务处理。当前对话只在这个页面里，刷新就会清除。'],
];
const OPS_NOTICE = '问答会保存下来，用来改进回答；请勿填写私密信息。';
const LINKS = {
  context: 'https://github.com/sunyuzheng/lizheng-open-context',
  site: 'https://www.lizheng.ai/',
  community: 'https://www.superlinear.academy/c/tools/lizheng-context',
  // Feedback and reports, in the open (2026-10-06, the user): comments under the community post
  // that introduced the Ask.
  feedback: 'https://www.superlinear.academy/c/tools/ask-lizheng',
  stay: 'https://stay.superlinear.academy/',
  privacy: 'https://www.lizheng.ai/ask/privacy',
  // The apps: the App Store, Google Play, and the APK on lizheng.ai that mainland China installs
  // from (its page tells WeChat users to open it in a browser first).
  ios: 'https://apps.apple.com/app/id6818687873',
  play: 'https://play.google.com/store/apps/details?id=ai.lizheng.ask',
  apk: 'https://www.lizheng.ai/ask/android/ask-lizheng.apk',
  android: 'https://www.lizheng.ai/ask/android',
};
// 下载App under the question box (2026-10-07, the user: the stores' logos, somewhere easy to see).
// An iPhone sees the App Store; an Android phone Google Play and the APK (in WeChat, which blocks APK
// downloads, only the APK's page); a computer all three. Inside the apps there are none.
const APP_BADGES = (() => {
  if (IN_APP || typeof navigator === 'undefined') return [];
  const ua = navigator.userAgent;
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const store = {key: 'ios', href: LINKS.ios}, play = {key: 'play', href: LINKS.play};
  const apk = {key: 'apk', href: IN_WECHAT ? LINKS.android : LINKS.apk};
  return ios ? [store] : /Android/.test(ua) ? (IN_WECHAT ? [apk] : [play, apk]) : [store, play, apk];
})();
// Where a link to the membership page sits, so its visits can be told apart there.
const stayLink = medium => `${LINKS.stay}?utm_source=ask-lizheng&utm_medium=${medium}`;
// Visits in the iPhone app count as their own surface.
const SURFACE = IN_APP ? 'app' : 'ask';
// The first screen (2026-10-04, the user: the subtitle is where the product says what is different
// about it, not an inventory of the material): answers only from his own work, each paragraph back
// to its source, and the questions are public, the newest beside the question box. The material
// itself is further down, in 回答从哪里来. No punctuation in the title: at this size a comma and a
// full stop read as shapes, not pauses.
const START = {
  title: ['卡住的时候', '问问立正'],
  lines: ['回答只从立正讲过、写过的东西里来，每一段都能点回原文。', '这里的问答是公开的：别人刚问了什么，你都能看到。'],
  identity: '这是AI回答，不是立正本人实时回复。',
};
// Questions others asked, published as they were asked (2026-10-05; before, with personal details removed).
const DISCOVERY = {
  title: '别人在问什么',
  note: '真实的提问和回答。你想问的，可能已经有人问过。',
  attribution: 'AI整理，不是立正本人回复。',
  live: '别人正在问',
  liveMore: '看看他们得到的回答',
  similar: n => `另外${n}个类似提问`,
  feedback: '反馈或举报',
  feedbackCopied: '这条问答的链接已复制，留言时贴上就行。',
};
// 我的提问 (history.js): what was asked on this device. Its note is where the page says what the
// privacy policy means for the person: we cannot tell which questions were theirs.
const HISTORY = {
  title: '我的提问',
  note: '只保存在这台设备上。提问不和账号、邮箱或IP记在一起，我们不知道哪个问题是谁问的，所以这份记录只在你手里。换了设备或浏览器，或者清除了浏览器数据，就看不到了。',
  empty: '还没有提问。',
  clear: '全部清除',
  confirm: n => `清除这台设备上的${n}条提问？`,
};
// 回答从哪里来: what the answers are made from, and that it is distilled, not just raw text (the
// user, 2026-10-04: the Statsig posts are articles like the rest; end the list with 等). Counted from
// what the service answers from (/api/meta counts), so the numbers follow Open Context; before they
// arrive, the same sentences without them.
const count = (counts, key) => (Number.isInteger(counts?.[key]) ? counts[key] : null);
function materialsText(counts) {
  const n = key => count(counts, key);
  const videos = n('video_transcripts'), member = n('member_video_transcripts'), lessons = n('member_course_lessons');
  const articles = ['community_posts_full_text', 'knowledge_bank_full_text', 'blog_posts_zh'].map(n);
  const parts = [
    videos ? `${videos}期视频的字幕${member ? `（其中${member}期是会员视频）` : ''}` : '四百多期视频的字幕（一半是会员视频）',
    articles.every(value => value !== null) ? `${articles.reduce((a, b) => a + b, 0)}篇文章` : '三百来篇文章',
    lessons ? `《真本事》整门课的${lessons}讲文字稿` : '《真本事》整门课的文字稿',
    // Word joiners keep 中文版等 on one line after the long English title.
    ...(n('book_chapters_zh') === 0 ? [] : ['《Growth Data Analytics Playbook》中\u2060文\u2060版']),
  ];
  return `立正六年里讲过、写过的东西：${parts.join('、')}\u2060等。`;
}
function refinedText(counts) {
  const cards = count(counts, 'reasoning_cards'), talks = count(counts, 'conversation_excerpt_files'), excerpts = count(counts, 'conversation_excerpts');
  return `这些原文还经过提炼：他反复讲的判断，整理成了${cards ? `${cards}张` : ''}判断卡，每张都连着说这些话的原文；`
    + (talks && excerpts ? `${talks}场重要对话里他本人说的${excerpts}段话，也逐段核对了出来。` : '重要对话里他本人说的话，也逐段核对了出来。');
}
const MATERIALS = {
  title: '回答从哪里来',
  how: '回答时先找到相关的判断，再回到原文，整理成段落。每一段都标明出处：文章附日期，视频跳到他讲这段的地方。',
  open: '这些材料都开源在GitHub',
};
const MESSAGES = {
  quota: '今天的3次已经用完。北京时间每天0点恢复；Founding Member验证后不限次。',
  networkQuota: '今天来自这个网络的免费提问已经很多了，北京时间每天0点恢复；Founding Member验证后不限次。',
  membership: '暂时无法核验Founding Member身份，请稍后再试。这次不扣次数。',
  busy: '现在提问的人有点多，请稍等一会儿再试。',
  unreachable: '暂时连不上，请稍后再试。',
  stream: '回答没能完整读到，可以重新生成。',
  failed: '回答没有完成。问题和已找到的材料都还在，可以重新生成。',
  lost: '连接中断了。问题和已找到的材料都还在，可以重新生成。',
  timeout: '这次等得太久，已经停止等待。问题和材料都还在，可以重新生成。',
  stopped: '已停止。问题还在，可以改一改再发。',
  copy: '浏览器没有允许复制，可以直接选中文字复制。',
  export: '这次没能生成文件，可以稍后再试。',
};

// 分享这条回答: an answered question gets its own public page (share-link.js); the long image and
// the PDF are saved from the same place. Sharing may give back one of today's questions; that offer
// never shows inside WeChat or the iPhone app.
const SHARE = {
  label: '分享这条回答',
  bonusLabel: '分享这条回答，今天多问一次',
  quota: '分享上面的回答，今天多问一次',
  what: PUBLIC_QA.zh.share,
  create: '复制分享链接',
  creating: '正在生成链接…',
  copied: '链接已复制。',
  select: '没能自动复制，长按或选中链接就能复制。',
  bonus: '今天多了一次提问机会。',
  send: '发给朋友…',
  copy: '复制链接',
  copiedAgain: '已复制',
  failed: '这次没能生成链接，可以稍后再试。',
  wechat: '打开链接后，点右上角「···」就能发给朋友。',
};

// Visits and a few clicks are counted by Vercel Web Analytics, which lizheng.ai's hosts serve;
// the counts carry no question text. Elsewhere, such as a local build, nothing loads.
const analytics = /(^|\.)lizheng\.ai$/.test(location.hostname);
if (analytics) {
  window.va = window.va || function () { (window.vaq = window.vaq || []).push(arguments); };
  document.head.append(Object.assign(document.createElement('script'), {defer: true, src: '/_vercel/insights/script.js'}));
}
const track = (name, data) => { if (analytics) window.va('event', {name, data}); };
const sourceKind = url => {
  try {
    const host = new URL(url).hostname;
    return /(^|\.)lizheng\.ai$/.test(host) ? 'article' : /youtube\.com$|youtu\.be$|bilibili\.com$/.test(host) ? 'video'
      : /superlinear\.academy$|circle\.so$/.test(host) ? 'community' : 'other';
  } catch { return 'other'; }
};
const trackSource = url => { markUsage('source'); track('Ask Source Click', {surface: SURFACE, kind: sourceKind(url)}); };

const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const scrollBehavior = () => (reducedMotion() ? 'auto' : 'smooth');
const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const contextText = background => BACKGROUND
  .filter(field => background[field.key].trim())
  .map(field => `${field.prompt}：${background[field.key].trim()}`)
  .join('\n');

function Mark({className = ''}) {
  return <svg className={`mark ${className}`} viewBox="0 0 1219.044577 649.234004" aria-hidden="true" focusable="false">
    <g transform="translate(-17.980429,953.72375) scale(0.1,-0.1)" fill="currentColor">{MARK_PATHS.map(d => <path key={d.slice(0, 16)} d={d}/>)}</g>
  </svg>;
}

// Type, not icons: ↗ marks a link that leaves the page, a small chevron what opens in place.
const Ext = () => <span className="ext" aria-hidden="true">↗</span>;
// The stores' own badges at their own proportions; the APK gets one drawn to match.
function AppBadges() {
  const open = key => track('Ask App Link', {surface: SURFACE, app: key, location: 'stage'});
  return <div className="apps">
    <span className="apps-label">下载App</span>
    {APP_BADGES.map(({key, href}) => key === 'apk'
      ? <a key={key} className="app-badge apk" href={href} onClick={() => open(key)}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v11m0 0-4.5-4.5M12 14l4.5-4.5M4.5 15.5V19a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-3.5" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"/></svg>
          <span><small>国内直接下载</small><b>安卓安装包</b></span>
        </a>
      : <a key={key} className="app-badge" href={href} target="_blank" rel="noopener noreferrer" onClick={() => open(key)}>
          {key === 'ios' ? <img src={appStoreBadge} alt="在 App Store 下载" width="109" height="40"/>
            : <img src={googlePlayBadge} alt="在 Google Play 下载" width="135" height="40"/>}
        </a>)}
  </div>;
}
const Chev = () => <span className="chev" aria-hidden="true"/>;

// Chinese text may break anywhere; keep each phrase together and break after punctuation.
function Phrases({text}) {
  const parts = String(text).match(/[^，。：；、？！]+[，。：；、？！]*/g) || [text];
  return parts.length < 2 ? text : parts.map((part, index) => <span key={index} className="phrase">{part}</span>);
}

function Kind({kind, personal}) {
  const label = kindLabel(kind, personal);
  return label ? <span className={`kind kind-${kind}`}>{label}</span> : null;
}
const isVideo = source => source.source_type?.includes('video');
const sourceLabel = sourceTypeLabel;
const sourceDate = source => source.date?.slice(0, 10) || '日期未标明';

function SourceCard({source, selected, onOpen, prefix}) {
  const member = isMemberVideo(source) || isMemberCourse(source);
  return <article id={`${prefix}-source-${source.id}`} className={`source ${member ? 'source-member' : ''} ${selected ? 'selected' : ''}`}>
    <div className="source-meta">
      <span className="source-num">{source.id.slice(1)}</span>
      <span className={member ? 'member-video-badge' : undefined}>{sourceLabel(source)}</span>
      <time>{sourceDate(source)}</time>
    </div>
    <a className="source-title" href={source.url} target="_blank" rel="noreferrer" onClick={() => trackSource(source.url)}>{source.title}<Ext/></a>
    {member && <p className="source-access-note">{sourceAccessNote(source)}</p>}
    {member && <div className="source-access-links">
      {source.text_access === 'public' && source.public_copy_url && <a className="public-copy" href={source.public_copy_url} target="_blank" rel="noopener noreferrer">阅读公开文字稿<Ext/></a>}
      {memberVideoUrl(source) && <a className="source-video-cta" href={memberVideoUrl(source)} target="_blank" rel="noopener noreferrer" onClick={() => trackSource(source.url)}>观看会员完整视频<Ext/></a>}
    </div>}
    {memberJoinUrl([source]) && <p className="source-join">
      <a href={memberJoinUrl([source])} target="_blank" rel="noopener noreferrer">加入 YouTube 频道会员<Ext/></a>
      <span>与 Founding Member 的提问次数无关</span>
    </p>}
    {source.timecode && <a className="source-time" href={source.url} target="_blank" rel="noreferrer" onClick={() => trackSource(source.url)}>从 {source.timecode} 开始看</a>}
    {source.reason && <p className="source-reason">{source.reason}</p>}
    <details onToggle={event => { if (event.currentTarget.open) onOpen?.(source.id); }}>
      <summary>看片段<Chev/></summary>
      <p className="excerpt">{source.excerpt}</p>
      <p className="attribution">{source.author && `${source.author} · `}{source.attribution_note}</p>
      {transcriptQualityNote(source) && <p className="source-quality-note">{transcriptQualityNote(source)}</p>}
      {source.public_copy_url && !((isMemberVideo(source) || isMemberCourse(source)) && source.text_access === 'public') && <a className="public-copy" href={source.public_copy_url} target="_blank" rel="noreferrer">阅读公开资料副本<Ext/></a>}
    </details>
  </article>;
}

function Markdown({text, sources, onSelect}) {
  const content = String(text || '').replace(/\[(S\d+)\](?!\()/g, '[$1](#cite-$1)');
  return <ReactMarkdown components={{a: ({href, children}) => {
    if (href?.startsWith('#cite-')) {
      const id = href.slice(6);
      return sources.some(source => source.id === id)
        // A footnote mark: an inline link (a button would sit apart in the line), joined to the word it marks.
        ? <>{'\u2060'}<a className="cite" href={`#source-${id}`} onClick={event => { event.preventDefault(); onSelect(id); }} aria-label={`查看出处${id.slice(1)}`}>{id.slice(1)}</a></>
        : null;
    }
    return <span>{children}</span>;
  }}}>{content}</ReactMarkdown>;
}

// A source named in the boundary note shows as its number, like in the text.
function Limits({text, sources, onSelect}) {
  return <div className="limits"><div><b>这个回答的边界</b><Markdown text={text.replace(/\[?\b(S\d+)\b\]?/g, '[$1]')} sources={sources} onSelect={onSelect}/></div></div>;
}

// Sources show as numbers in the text; a section that cites none in its text lists them below.
function SectionBlock({section, sources, onSelect, personal}) {
  return <section className="answer-section">
    <div className="section-head"><h3>{section.heading}</h3><Kind kind={section.kind} personal={personal}/></div>
    <Markdown text={section.body} sources={sources} onSelect={onSelect}/>
    {section.source_ids?.length > 0 && !/\[S\d+\]/.test(section.body) && <div className="section-sources">
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
      <button type="button" className="ghost-button" onClick={onStop}>停止</button>
    </div>
    <ol className="steps" aria-label="处理步骤">
      {STEPS.map((label, index) => <li key={label} className={index < step ? 'done' : index === step ? 'current' : ''} aria-current={index === step ? 'step' : undefined}>
        <span className="step-dot">{index < step ? <Check size={11} strokeWidth={3}/> : null}</span>{label}
      </li>)}
    </ol>
    <p className="working-note">
      <span>{message.model} · 已用{elapsed}秒</span>
      <span>{!message.lastActivity ? (elapsed >= 5 ? '问答服务闲置时会休眠，正在唤醒，通常十几秒…' : '正在连接服务…') : stale ? '有一会儿没收到新进展了，还在等。' : candidates.length ? '回答还在整理，可以先读找到的材料。' : '找到材料后，会先在这里给你看。'}</span>
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
      {message.partial.sections.map((section, index) => <SectionBlock key={index} section={section} sources={message.partial.sources} onSelect={onSelect} personal={!!message.context}/>)}
    </div>}
    {candidates.length > 0 && <Candidates sources={candidates} turnId={message.id} onSelect={onSelect}/>}
  </section>;
}

function Candidates({sources, turnId, onSelect}) {
  return <div className="candidates">
    <div className="candidates-head"><h3>已找到的材料</h3><span>候选，不一定都会用上</span></div>
    {sources.slice(0, 2).map(source => <article className="candidate" key={`${source.id}-${source.url}`}>
      {(isMemberVideo(source) || isMemberCourse(source)) && <span className="member-video-badge">{sourceLabel(source)}</span>}
      <a href={source.url} target="_blank" rel="noreferrer" onClick={() => trackSource(source.url)}>{source.title}<Ext/></a>
      <time>{sourceDate(source)}</time>
      {(isMemberVideo(source) || isMemberCourse(source)) && <p className="source-access-note">{sourceAccessNote(source)}</p>}
      <p>{source.excerpt?.slice(0, 150)}{source.excerpt?.length > 150 ? '…' : ''}</p>
    </article>)}
    {sources.length > 2 && <details className="candidates-all">
      <summary>看全部{sources.length}份候选材料<Chev/></summary>
      {sources.map(source => <SourceCard key={`${source.id}-${source.url}`} source={source} prefix={`turn-${turnId}`} onOpen={id => onSelect(id)}/>)}
    </details>}
  </div>;
}

// `step` is where a Founding verification stands, so it never looks like nothing happened.
function AccountLine({account, waking, busy, step, foundingOpen, onToggleFounding, onLogin, onLoginHere, onLogout, onRetry}) {
  if (!account && waking) return <p className="account" aria-live="polite"><span className="account-pending">正在唤醒问答服务…</span></p>;
  if (!account?.enabled) return null;
  if (account.unavailable) return <p className="account" aria-live="polite"><span>暂时读不到今天的次数。</span><button type="button" className="text-button" onClick={onRetry}>重试</button></p>;
  if (account.founding) return <p className="account" aria-live="polite">{step === 'verified' ? <b className="account-verified">验证成功 · Founding Member · 不限次</b> : <span className="account-strong">Founding Member · 不限次</span>}<button type="button" className="text-button" disabled={busy} onClick={onLogout}>退出</button></p>;
  const remaining = account.remaining ?? 3;
  const howToJoin = IN_APP ? null : <button type="button" className="text-button account-toggle" aria-expanded={foundingOpen} aria-controls="founding-info" onClick={onToggleFounding}>如何成为</button>;
  return <p className="account" aria-live="polite">
    <span className={remaining === 0 ? 'account-empty' : 'account-strong'}>{remaining === 0 ? '今天的3次已用完，北京时间0点恢复' : `今天还能问${remaining}次`}</span>
    {account.authenticated
      ? <><span className={step === 'member' ? 'account-notice' : undefined}>已登录，未核验到Founding资格</span>{howToJoin}<button type="button" className="text-button" disabled={busy} onClick={onLogout}>退出</button></>
      : step === 'pending'
        ? <><span className="account-pending">请在弹出的窗口里完成验证</span><button type="button" className="text-button" onClick={onLoginHere}>没看到窗口？在本页验证</button></>
        : account.login_ready && <>{step === 'incomplete' && <span>这次没有完成验证</span>}<span className="account-offer">Founding Member不限次：<button type="button" className="text-button" disabled={busy} onClick={onLogin}>验证身份</button>{howToJoin && <><span className="account-sep" aria-hidden="true">·</span>{howToJoin}</>}</span></>}
  </p>;
}

// Sharing one answer: its own page (what that page shows, then its link and how to send it), a long
// image or a PDF. `link` is false for an answer that cannot have a page; the image and PDF remain.
// Sharing an answer: its page, a long image or a PDF. A question others asked already has its
// page: only its link and how to send it (no onExport).
function SharePanel({state, link, exporting, onCreate, onCopy, onSend, onExport}) {
  const ready = link && state.phase === 'ready';
  return <div className="share-panel">
    {ready ? <>
      <a className="share-url" href={state.url} target="_blank" rel="noopener">{shareDisplay(state.url)}<Ext/></a>
      <p className="share-status" role="status">{state.copied ? SHARE.copied : SHARE.select}{state.bonus === 'granted' && <b>{SHARE.bonus}</b>}</p>
    </> : link && <p>{SHARE.what}</p>}
    {state.error && <p className="share-error" role="alert">{state.error}</p>}
    <div className="share-actions">
      {ready ? <>
        {typeof navigator.share === 'function' && <button type="button" className="pill-button" onClick={onSend}>{SHARE.send}</button>}
        <button type="button" className="pill-button" onClick={onCopy}>{state.copied ? SHARE.copiedAgain : SHARE.copy}</button>
      </> : link && <button type="button" className="pill-button" disabled={state.phase === 'working'} onClick={onCreate}>{state.phase === 'working' ? SHARE.creating : SHARE.create}</button>}
      {onExport && <>
        <button type="button" className="pill-button" disabled={!!exporting} onClick={() => onExport('png')}>{exporting === 'png' ? '正在生成…' : '保存图片'}</button>
        <button type="button" className="pill-button" disabled={!!exporting} onClick={() => onExport('pdf')}>{exporting === 'pdf' ? '正在生成…' : '下载PDF'}</button>
      </>}
    </div>
    {ready && IN_WECHAT && <p className="share-hint">{SHARE.wechat}</p>}
  </div>;
}

// What a Founding Member is and how to become one, opened from the count line.
function FoundingInfo({account, busy, step, onLogin}) {
  return <div className="founding-info" id="founding-info">
    <p className="founding-title">什么是Founding Member</p>
    <p>Stay Superlinear前3,000位新年费会员是Founding Member，一年$149/¥999。AI Builder、AI Architect的老学员也是。</p>
    <p>会员每年有12+场嘉宾大师课，每个月和鸭哥与立正直播答疑，问问立正也不限次。</p>
    <p className="founding-actions">
      <a className="founding-join" href={stayLink('founding_panel')} target="_blank" rel="noopener noreferrer" onClick={() => track('Ask Membership Click', {surface: SURFACE, location: 'founding_panel'})}>了解会员<Ext/></a>
      {!account.authenticated && account.login_ready && step !== 'pending' && <button type="button" className="text-button" disabled={busy} onClick={onLogin}>已经是？验证身份</button>}
    </p>
  </div>;
}

// One published question. Its answer opens in place and reads like one in a conversation.
// In 最常问 a card leads with how many similar askings it stands for; in 最近问 with when it was
// asked, showing that count below when it says more than one.
const DISCOVERY_VIEWS = [['recent', '最近问'], ['frequent', '最常问']];
// Wide enough for 别人正在问 beside the question box (style.css uses the same width).
const WIDE = window.matchMedia('(min-width: 1100px)');

// A like, as Product Hunt and Reddit show one: an arrow over the count, beside the question. Anyone
// may like, counted by browser (2026-10-05); green once liked, and no count until someone has.
function Upvote({like, nested, onLike}) {
  return <button type="button" className={`upvote ${like.voted ? 'on' : ''} ${nested ? 'nested' : ''}`} aria-pressed={like.voted}
    aria-label={like.likes > 0 ? `有帮助，${like.likes}人` : '有帮助'} title={like.voted ? '取消' : '有帮助'} onClick={onLike}>
    <span className="upvote-arrow" aria-hidden="true"/>
    {like.likes > 0 && <span className="upvote-count">{like.likes}</span>}
  </button>;
}

function QuestionCard({card, open, detail, like, share, feedback, selected, nested, children, onToggle, onSimilar, onLike, onShare, onCopy, onSend, onSelect, onFeedback}) {
  const prefix = `q-${card.public_id}`;
  const similar = card.similar_count ?? card.topic_question_count;
  const often = card.role === 'common' && similar >= 2;
  const meta = !often && similar >= 2 ? `${similar}次类似提问` : '';
  const select = id => onSelect(prefix, id);
  const answer = detail && typeof detail === 'object' ? detail.answer : null;
  return <article className={`qcard ${open ? 'open' : ''} ${nested ? 'nested' : ''}`} id={nested ? undefined : `qcard-${card.public_id}`}>
    <div className="qcard-row">
      <button type="button" className="qcard-head" aria-expanded={open} onClick={onToggle}>
        {often ? <span className="qcard-time often">{similar}次类似提问</span>
          : card.asked_at
          ? <time className={`qcard-time ${Date.now() - Date.parse(card.asked_at) < 3600000 ? 'fresh' : ''}`} dateTime={card.asked_at}
            title={new Date(card.asked_at).toLocaleString('zh-CN', {dateStyle: 'long', timeStyle: 'short'})}>{askedAgo(card.asked_at)}</time>
          : <span className="qcard-time common">常被问到</span>}
        <span className="qcard-question">{card.question}</span>
        {!open && !nested && card.summary && <span className="qcard-summary">{card.summary}</span>}
        {meta && <span className="qcard-meta">{meta}</span>}
      </button>
      <Upvote like={like} nested={nested} onLike={onLike}/>
    </div>
    {open && <div className="qcard-body">
      {answer ? <div className="answer">
        <div className="summary"><Markdown text={answer.summary} sources={answer.sources} onSelect={select}/></div>
        {answer.sections.map((section, index) => <SectionBlock key={index} section={section} sources={answer.sources} onSelect={select}/>)}
        {answer.limitations && <Limits text={answer.limitations} sources={answer.sources} onSelect={select}/>}
        {answer.sources.length > 0 && <details className="inline-sources">
          <summary>回到{answer.sources.length}份原文<Chev/></summary>
          <div>{answer.sources.map(source => <SourceCard key={source.id} source={source} prefix={prefix} selected={selected === `${prefix}:${source.id}`}/>)}</div>
        </details>}
        <p className="qcard-attribution">{DISCOVERY.attribution}</p>
      </div> : <p className="qcard-note">{detail === 'failed' ? '这条回答暂时打不开，请稍后再试。' : '正在打开…'}</p>}
      <div className="qcard-actions">
        {/* Where reading an answer ends, the same like as beside the question. */}
        <button type="button" className={`pill-button qcard-like ${like.voted ? 'on' : ''}`} aria-pressed={like.voted} onClick={onLike}>
          <span className="upvote-arrow" aria-hidden="true"/>有帮助{like.likes > 0 && <span className="qcard-like-count">{like.likes}</span>}
        </button>
        <button type="button" className="pill-button" onClick={onSimilar}>问个类似的</button>
        <button type="button" className="pill-button" aria-expanded={!!share?.open} onClick={onShare}>分享</button>
        {/* Feedback and reports go to the community post, with this answer's link on the clipboard. */}
        <a className="qcard-feedback" href={LINKS.feedback} target="_blank" rel="noopener noreferrer" onClick={onFeedback}>{DISCOVERY.feedback}<Ext/></a>
      </div>
      {feedback && <p className="qcard-feedback-note" role="status">{DISCOVERY.feedbackCopied}</p>}
      {share?.open && <SharePanel state={share} link onCopy={onCopy} onSend={onSend}/>}
    </div>}
    {children}
  </article>;
}

// When a question on this device was asked: 今天 14:32, 10月3日 9:05, then the year when it is not this one.
function askedOn(iso, now = new Date()) {
  const date = new Date(iso);
  const time = date.toLocaleTimeString('zh-CN', {hour: 'numeric', minute: '2-digit'});
  if (date.toDateString() === now.toDateString()) return `今天 ${time}`;
  return `${date.getFullYear() === now.getFullYear() ? '' : `${date.getFullYear()}年`}${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
}

// 我的提问: the questions asked on this device, newest first. Opening one shows it as a conversation,
// where it can be followed up and shared; each can be deleted, or all of them.
function MyQuestions({items, busy, close, onOpen, onForget, onForgetAll}) {
  const ref = useRef(null);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.focus();
    const handler = event => {
      if (event.key === 'Escape') close();
      if (event.key !== 'Tab') return;
      const all = ref.current?.querySelectorAll('button:not(:disabled),a');
      const first = all?.[0], last = all?.[all.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', handler);
    return () => { document.removeEventListener('keydown', handler); previous?.focus(); };
  }, []);
  return <div className="backdrop" onClick={close}>
    <section className="dialog history" role="dialog" aria-modal="true" aria-labelledby="history-title" tabIndex={-1} ref={ref} onClick={event => event.stopPropagation()}>
      <button type="button" className="icon-button dialog-close" aria-label="关闭" onClick={close}><X size={20}/></button>
      <h2 id="history-title">{HISTORY.title}</h2>
      <p className="dialog-intro">{HISTORY.note}<a className="inline-link" href={LINKS.privacy} target="_blank" rel="noopener noreferrer">隐私政策<Ext/></a></p>
      {items.length ? <ol className="history-list">{items.map(item => <li key={item.id}>
        <button type="button" className="history-item" disabled={busy} onClick={() => onOpen(item)}>
          <time dateTime={item.asked_at}>{askedOn(item.asked_at)}</time>
          <span>{item.question}</span>
        </button>
        <button type="button" className="icon-button history-forget" aria-label={`删除这条提问：${item.question.slice(0, 30)}`} title="删除" onClick={() => onForget(item.id)}><X size={16}/></button>
      </li>)}</ol> : <p className="history-empty">{HISTORY.empty}</p>}
      {items.length > 0 && <div className="history-foot">
        {confirming ? <p role="alert">{HISTORY.confirm(items.length)}
          <button type="button" className="text-button" onClick={() => { setConfirming(false); onForgetAll(); }}>清除</button>
          <button type="button" className="text-button" onClick={() => setConfirming(false)}>取消</button>
        </p> : <button type="button" className="text-button" onClick={() => setConfirming(true)}>{HISTORY.clear}</button>}
      </div>}
    </section>
  </div>;
}

// The iPhone app asks once before the first question goes to the AI service, as App Store rules
// require explicit permission before sharing personal data with a third-party AI.
const CONSENT_KEY = 'ask-app-ai-consent';
// Retire earlier permission when recipients, public-use disclosure or archive mode change.
const consentScope = meta => `v4:${meta?.model || 'AI'}:${answerService(meta?.model) || 'unknown'}:OpenAI:${meta?.ops_logging?.enabled === true}:${meta?.ops_logging?.notice || 'v1'}:${meta?.ops_logging?.answer_archive === true}:${meta?.ops_logging?.public_display || 'none'}:${meta?.ops_logging?.context_archive === true}`;
const readConsent = scope => { try { return localStorage.getItem(CONSENT_KEY) === scope; } catch { return false; } };
const saveConsent = scope => { try { localStorage.setItem(CONSENT_KEY, scope); } catch { /* asked again next visit */ } };
const clearConsent = () => {
  try { localStorage.setItem(CONSENT_KEY, 'revoked'); return true; } catch {}
  try { localStorage.removeItem(CONSENT_KEY); return true; } catch { return false; }
};

function AppConsent({meta, publicArchive, contextKept, onAgree, onCancel}) {
  const ref = useRef(null);
  const recipient = answerService(meta?.model);
  useEffect(() => {
    const previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    ref.current?.focus();
    const handler = event => {
      if (event.key === 'Escape') onCancel();
      if (event.key !== 'Tab') return;
      const items = ref.current?.querySelectorAll('button:not(:disabled),a');
      const first = items?.[0], last = items?.[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', handler);
    return () => {
      document.removeEventListener('keydown', handler);
      document.documentElement.style.overflow = previousOverflow;
    };
  }, []);
  return <div className="backdrop" onClick={onCancel}>
    <section className="dialog consent" role="dialog" aria-modal="true" aria-labelledby="consent-title" tabIndex={-1} ref={ref} onClick={event => event.stopPropagation()}>
      <h2 id="consent-title">{publicArchive ? '参与公开问答' : '同意AI处理你的输入'}</h2>
      <p>{publicArchive ? `${PUBLIC_QA.zh.notice} 同意后保存问题、完整回答与出处，用于帮助别人、改进回答和内容选题。` : '提问按输入旁的说明和隐私政策保存，用于改进回答及内容选题。'}</p>
      <p><b>AI处理</b><br/>Builder Space转交输入给{recipient || '待确认的服务商'}生成回答、OpenAI匹配资料。回答AI接收问题、选填处境、最多6轮问题与回答摘要及公开资料；OpenAI接收用于匹配的问题、处境及必要时的上一轮问题。{recipient === 'DeepSeek' && 'DeepSeek可能保留输入并用于改进模型，没有训练退出选项。'}</p>
      <p><b>公开范围</b><br/>{publicArchive ? `常见问答由同一AI挑选后原样展示；分享页显示问题、完整回答与出处，可被搜索找到。${contextKept ? '处境原文只用于分析。' : '不保存处境原文。'}回答引用的细节也可能公开。` : '当前保存说明不包含公开使用。'}</p>
      <p>发送前不会自动去掉个人信息；邮箱和登录信息不交给AI。<a className="inline-link" href={LINKS.privacy} target="_blank" rel="noopener noreferrer">隐私政策<Ext/></a> · <a className="inline-link" href="https://space.ai-builders.com/privacy" target="_blank" rel="noopener noreferrer">服务商数据说明<Ext/></a></p>
      {!recipient && <p role="alert">回答服务商尚未确认，暂时不能发送。你仍可浏览公开问答。</p>}
      <p className="consent-choice">不同意也能浏览问答；可在「说明」里撤回AI同意。</p>
      <div className="consent-actions">
        <button type="button" className="ghost-button" onClick={onCancel}>不同意</button>
        <button type="button" className="consent-agree" disabled={!recipient} onClick={onAgree}>{publicArchive ? '同意AI处理与公开使用' : '同意AI处理并提问'}</button>
      </div>
    </section>
  </div>;
}

function About({close, meta, account, focusInput, publicArchive, contextKept, onRevokeConsent}) {
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
      {publicArchive ? <div className="about-row" id="about-input"><h3>{PUBLIC_QA.zh.title}</h3>
        {publicQa(meta, contextKept).about.map(text => <p key={text}>{text}</p>)}
      </div> : meta?.ops_logging?.enabled ? <div className="about-row" id="about-input"><h3>你的提问会怎么用</h3>
        {V3_PARTS.map(([title, text]) => <p key={title}><b>{title}</b>　{text}</p>)}
      </div> : <div className="about-row" id="about-input"><h3>关于你的输入</h3><ul>
        <li>提问文本会保存30天，用于改进回答，同时记录提问时间、模型、回答状态和耗时，30天后自动删除。</li>
        <li>不保存补充背景和对话历史原文、模型内部推理；提问记录不关联邮箱、账号或IP。完整回答可能概括你提供的处境。</li>
        <li>当前对话只在这个页面里，刷新就会清除。</li>
        <li>提问和必要的上下文会发送给Builder Space的模型服务处理，处理规则由该服务管理。请只写愿意交给AI处理的内容。</li>
        <li>账号只用于登录和Founding资格核验，不交给模型。如果浏览器拦截了登录窗口，未发送的输入会在本机临时保留，恢复后清除，最长10分钟。</li>
      </ul></div>}
      {account?.enabled && <div className="about-row"><h3>次数</h3><p>每天可以问3次，北京时间0点恢复。Superlinear的Founding Member用邮箱验证后不限次。没有完成的回答不扣次数。</p>{!IN_APP && <p>Stay Superlinear前3,000位新年费会员，以及AI Builder、AI Architect的老学员，都是Founding Member。<a className="inline-link" href={stayLink('about')} target="_blank" rel="noopener noreferrer" onClick={() => track('Ask Membership Click', {surface: SURFACE, location: 'about'})}>了解会员<Ext/></a></p>}</div>}
      {IN_APP && <div className="about-row"><h3>AI处理与公开问答</h3><p>Builder Space转发输入给{answerService(meta?.model) || '待确认的回答服务商'}生成回答（{meta?.model || 'AI'}），给OpenAI匹配资料（text-embedding-3-small）。回答AI接收问题、选填处境及最近最多6轮问题与回答摘要；OpenAI接收问题、处境及上一轮问题的文本。发送前不会自动去掉个人信息；邮箱和登录信息不交给AI。</p><p>提问前会明确征求同意。撤回后，新提问和重新生成都需要再次同意。已经发出的内容和已公开的页面不会因此收回；删除记录或撤下页面请按隐私政策联系我们。</p><button type="button" className="pill-button" onClick={onRevokeConsent}>撤回AI数据发送同意</button></div>}
      <div className="dialog-foot">
        <span>材料更新于{meta?.context_date || '…'}</span>
        <a href={LINKS.context} target="_blank" rel="noreferrer">Open Context<Ext/></a>
        <a href={LINKS.site} target="_blank" rel="noreferrer">lizheng.ai<Ext/></a>
        <a href={LINKS.privacy} target="_blank" rel="noopener noreferrer">隐私政策<Ext/></a>
        <a href={LINKS.feedback} target="_blank" rel="noopener noreferrer">反馈或举报<Ext/></a>
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
  const [asking, setAsking] = useState(null);
  const consented = useRef('');
  const revokedConsent = useRef(false);
  const [selected, setSelected] = useState('');
  const [sourceTurn, setSourceTurn] = useState(null);
  const [copied, setCopied] = useState(null);
  // Each answer's share, by turn: whether its panel is open, and how far it got.
  const [shares, setShares] = useState({});
  const [exporting, setExporting] = useState('');
  const [account, setAccount] = useState(null);
  const [loginStep, setLoginStep] = useState('');
  const [foundingOpen, setFoundingOpen] = useState(false);
  // Questions others asked: 最近问 and 最常问, the same lists for everyone; each answer once opened,
  // and this visit's likes and shares.
  const [questionLists, setQuestionLists] = useState(null);
  // loading until the list arrives, then ready; none when it cannot be read here (other hosts,
  // Ops down) or takes past eight seconds, and only then the examples show in its place.
  const [discoveryState, setDiscoveryState] = useState('loading');
  // Anonymous usage of this page view for the owner's dashboard (usage.js): reading time, scroll
  // depth, whether the list of questions others asked was seen, and what was used.
  useEffect(() => startUsage(IN_APP ? 'app' : 'ask'), []);
  // Which list shows (最近问 unless only common questions exist yet), its first four or all of it.
  const [view, setView] = useState('recent');
  const [expanded, setExpanded] = useState(false);
  const shownList = useMemo(() => (questionLists ? discoveryView(questionLists, view) : []), [questionLists, view]);
  const discovery = expanded ? shownList : shownList.slice(0, 4);
  // 别人正在问: the three newest questions people really asked (not the common ones written fresh),
  // as many as sit beside the question box.
  const liveCards = useMemo(() => (questionLists ? discoveryView(questionLists, 'recent').filter(card => card.asked_at).slice(0, 3) : []), [questionLists]);
  const hasDiscovery = discovery.length > 0;
  useEffect(() => {
    if (!hasDiscovery) return;
    markUsage('d_shown');
    watchUsage(document.getElementById('questions'), 'd_seen');
  }, [hasDiscovery]);
  const [askedRecently, setAskedRecently] = useState(0);
  const [openCard, setOpenCard] = useState('');
  // In 最常问: whose similar questions are open (any number), and which of those shows its answer.
  const [openSimilar, setOpenSimilar] = useState(() => new Set());
  const [openNested, setOpenNested] = useState('');
  const [cardDetails, setCardDetails] = useState({});
  // Likes: what this browser liked (kept on this device), and the votes on their way, shown at once.
  const [likes, setLikes] = useState(readLikes);
  const [voting, setVoting] = useState({});
  const votingNow = useRef(new Set());
  // 我的提问: the questions asked on this device (history.js).
  const [askedHere, setAskedHere] = useState(readHistory);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [cardShares, setCardShares] = useState({});
  // Which answers' links were copied on the way to the community post for feedback.
  const [cardFeedback, setCardFeedback] = useState({});
  const [cardSource, setCardSource] = useState('');
  // The count comes from Builder, which sleeps when idle: say so while it wakes.
  const [accountWaking, setAccountWaking] = useState(false);
  const input = useRef(null), abort = useRef(null), loginCleanup = useRef(null);
  const conversationId = useRef(null);
  // How the question box was last filled, counted with each question: typed, example, card, followup or clarify.
  const questionFrom = useRef('typed');
  const metadataAbort = useRef(null);
  // The page loads from lizheng.ai at once, but the settings come from Builder, which sleeps when
  // idle and takes up to half a minute to wake: say so while it wakes, and keep asking.
  const [metaWaking, setMetaWaking] = useState(false);
  const metaTimers = useRef([]);
  // The page shows v4's notice only once the service says it keeps to v4.
  const knownNotice = ops => ops.answer_archive === true && (ops.notice === 'v3'
    || (ops.notice === 'v4' && ops.retention === 'until_deleted' && typeof ops.context_archive === 'boolean'
      && (ops.public_display === 'as_asked' || ops.public_display === 'deidentified')));
  const storageConfirmed = value => value?.query_logging?.enabled === true && typeof value?.ops_logging?.enabled === 'boolean'
    && (!value.ops_logging.enabled || knownNotice(value.ops_logging));
  const storageReady = storageConfirmed(meta) && !meta?.settings_error;
  const publicArchive = storageReady && meta.ops_logging.enabled && meta.ops_logging.notice === 'v4';
  const contextKept = publicArchive && meta.ops_logging.context_archive === true;
  // Asks for up to a minute, 20 seconds a try, then offers 重试.
  const refreshMeta = () => {
    metadataAbort.current?.abort();
    metaTimers.current.forEach(clearTimeout);
    setMeta(null);
    setMetaWaking(false);
    const started = Date.now();
    metaTimers.current = [setTimeout(() => setMetaWaking(true), 3000)];
    const attempt = () => {
      const controller = new AbortController();
      metadataAbort.current = controller;
      const deadline = setTimeout(() => controller.abort(), 20000);
      fetch('/api/meta', {signal: controller.signal, cache: 'no-store', credentials: 'same-origin'})
        .then(response => response.ok ? response.json() : Promise.reject())
        .then(value => {
          if (metadataAbort.current !== controller) return;
          metadataAbort.current = null;
          metaTimers.current.forEach(clearTimeout);
          setMetaWaking(false);
          const ready = storageConfirmed(value);
          setMeta(ready ? value : {...value, settings_error: true});
        })
        .catch(() => {
          if (metadataAbort.current !== controller) return;
          if (Date.now() - started < 60000) { metaTimers.current.push(setTimeout(attempt, 2000)); return; }
          metadataAbort.current = null;
          metaTimers.current.forEach(clearTimeout);
          setMetaWaking(false);
          setMeta({settings_error: true});
        })
        .finally(() => clearTimeout(deadline));
    };
    attempt();
  };
  const refreshAccount = () => { void readAskAccount().then(setAccount); };
  const showLoginResult = next => {
    setAccount(next);
    const step = !next?.enabled || next.unavailable ? '' : next.founding ? 'verified' : next.authenticated ? 'member' : 'incomplete';
    setLoginStep(step);
    if (step) track('Ask Verify Result', {surface: SURFACE, result: step});
  };

  useEffect(() => {
    const signedIn = finishAskLogin();
    // A draft means this tab is back from signing in on this page.
    const draft = takeAskDraft();
    if (draft) {
      setQuestion(draft.question);
      setMode(draft.intent === 'find' ? 'find' : 'ask');
      setPersonal(draft.intent === 'apply' || !!draft.context);
      if (draft.context) setBackground({goal: '', facts: draft.context, tried: ''});
    }
    // A question brought from a public answer page (www.lizheng.ai/ask/…, ?q=): in the box, not sent.
    const params = new URLSearchParams(location.search);
    if (params.has('q')) {
      const brought = (params.get('q') || '').trim().slice(0, 300);
      if (!draft && brought) { setQuestion(brought); questionFrom.current = 'page'; }
      history.replaceState(null, '', location.pathname + location.hash);
    }
    let stopped = false;
    const slow = setTimeout(() => setAccountWaking(true), 1500);
    // A read that times out while Builder wakes gets one more try a second later.
    const load = again => void readAskAccount().then(next => {
      if (stopped) return;
      if (next?.unavailable && again) { setTimeout(() => load(false), 1000); return; }
      clearTimeout(slow);
      setAccountWaking(false);
      (signedIn || draft ? showLoginResult : setAccount)(next);
    });
    load(true);
    const onFocus = () => { if (!abort.current) refreshAccount(); };
    window.addEventListener('focus', onFocus);
    return () => { stopped = true; clearTimeout(slow); window.removeEventListener('focus', onFocus); loginCleanup.current?.(); };
  }, []);
  useEffect(() => {
    refreshMeta();
    return () => { metadataAbort.current?.abort(); metadataAbort.current = null; metaTimers.current.forEach(clearTimeout); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const late = setTimeout(() => setDiscoveryState(state => (state === 'loading' ? 'none' : state)), 8000);
    void discoveryLists(controller.signal).then(lists => {
      clearTimeout(late);
      if (controller.signal.aborted) return;
      if (!lists || (!lists.recent.length && !lists.frequent.length)) { setDiscoveryState('none'); return; }
      // Until someone has asked, the common questions written fresh are all there is.
      if (!openList.current && !discoveryView(lists, 'recent').length) setView('frequent');
      // On a wide screen the newest are already beside the question box (别人正在问), so the list
      // below starts with the most asked rather than showing the same questions twice.
      else if (!openList.current && WIDE.matches) setView('frequent');
      setAskedRecently(askedLastDay(lists.recent));
      setQuestionLists(lists);
      setDiscoveryState('ready');
    });
    return () => { clearTimeout(late); controller.abort(); };
  }, []);
  // lizheng.ai links here to see a whole list: #recent (or the older #questions) or #frequent.
  // Once it is in and the page is long enough, scroll to it.
  const openList = useRef({'#questions': 'recent', '#recent': 'recent', '#frequent': 'frequent'}[location.hash] || '');
  useEffect(() => { if (openList.current) { setView(openList.current); setExpanded(true); } }, []);
  useLayoutEffect(() => {
    if (!openList.current || !questionLists) return;
    openList.current = '';
    document.getElementById('questions')?.scrollIntoView({block: 'start'});
  }, [questionLists]);
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
  // The iPhone app's pull-to-refresh brings a new set of questions; it is offered only while
  // nothing on screen would be lost: no conversation, no unsent question or situation.
  const canRefresh = messages.length === 0 && !question.trim() && !Object.values(background).some(value => value.trim());
  useEffect(() => {
    if (IN_APP) window.webkit?.messageHandlers?.askApp?.postMessage({canRefresh});
  }, [canRefresh]);

  const intent = intentOf(mode, personal);
  const situation = mode === 'ask' && personal ? contextText(background) : '';
  const login = () => {
    loginCleanup.current?.();
    track('Ask Verify Start', {surface: SURFACE});
    setLoginStep('pending');
    loginCleanup.current = beginAskLogin({question, context: situation, intent}, () => { void readAskAccount().then(showLoginResult); });
  };
  const loginHere = () => {
    loginCleanup.current?.(true);
    askLoginHere({question, context: situation, intent});
  };
  const logout = () => { setLoginStep(''); void logoutAsk().then(refreshAccount); };
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
  const prefill = (value, nextIntent, from = 'followup') => {
    if (nextIntent) { setMode(nextIntent === 'find' ? 'find' : 'ask'); setPersonal(nextIntent === 'apply'); }
    questionFrom.current = from;
    setQuestion(value); setError(''); focusInput();
  };
  const toggleCard = (card, from = view, nested = false) => {
    const [shown, show] = nested ? [openNested, setOpenNested] : [openCard, setOpenCard];
    if (shown === card.public_id) { show(''); return; }
    show(card.public_id);
    markUsage('d_open');
    track('Ask Discovery Open', {surface: SURFACE, view: nested ? 'similar' : from});
    const known = cardDetails[card.public_id];
    if (known && known !== 'failed') return;
    setCardDetails(prev => ({...prev, [card.public_id]: 'loading'}));
    void discoveryDetail(card.public_id).then(detail => setCardDetails(prev => ({...prev, [card.public_id]: detail || 'failed'})));
  };
  const askSimilar = card => {
    markUsage('d_similar');
    track('Ask Discovery Similar', {surface: SURFACE, view});
    prefill(card.question, undefined, 'card');
  };
  const likeOf = card => voting[card.public_id] || likeState(likes, card.public_id, card.likes);
  // A like shows at once; if it does not go through, it goes back.
  const likeCard = async card => {
    const id = card.public_id;
    if (votingNow.current.has(id)) return;
    votingNow.current.add(id);
    const shown = likeOf(card), vote = !shown.voted;
    setVoting(prev => ({...prev, [id]: {voted: vote, likes: Math.max(0, shown.likes + (vote ? 1 : -1))}}));
    const result = await voteDiscovery(id, card.revision, vote);
    votingNow.current.delete(id);
    if (result) { setLikes(prev => rememberLike(prev, id, result)); track('Ask Discovery Vote', {surface: SURFACE, vote}); }
    setVoting(prev => { const next = {...prev}; delete next[id]; return next; });
  };
  const feedbackCard = card => {
    track('Ask Feedback', {surface: SURFACE, location: 'card'});
    void copyWhenReady(discoveryPage(card.public_id)).then(copied => { if (copied) setCardFeedback(prev => ({...prev, [card.public_id]: true})); });
  };
  const toggleSimilar = card => {
    const open = !openSimilar.has(card.public_id);
    setOpenSimilar(prev => { const next = new Set(prev); if (open) next.add(card.public_id); else next.delete(card.public_id); return next; });
    if (open) track('Ask Discovery Similar List', {surface: SURFACE});
  };
  // 别人正在问, beside the question box: a question there opens in the list below, among the newest.
  function openFromLive(card) {
    setView('recent');
    if (openCard !== card.public_id) toggleCard(card, 'live');
    requestAnimationFrame(() => document.getElementById(`qcard-${card.public_id}`)?.scrollIntoView({block: 'start', behavior: scrollBehavior()}));
  }
  function toQuestions(event) {
    event.preventDefault();
    document.getElementById('questions')?.scrollIntoView({block: 'start', behavior: scrollBehavior()});
  }
  function showView(target) {
    if (view === target) return;
    setView(target);
    setOpenCard(''); setOpenNested(''); setOpenSimilar(new Set());
    const top = document.getElementById('questions');
    if (top && top.getBoundingClientRect().top < 0) top.scrollIntoView({block: 'start', behavior: scrollBehavior()});
    track('Ask Discovery Sort', {surface: SURFACE, sort: target});
  }
  function showMore() {
    setExpanded(true);
    markUsage('d_more');
    track('Ask Discovery More', {surface: SURFACE, view});
  }
  // A question others asked already has its public page: sharing copies its link at once, and a
  // phone can also send it on.
  const setCardShare = (id, next) => setCardShares(prev => ({...prev, [id]: {...prev[id], ...next}}));
  async function shareCard(card) {
    if (cardShares[card.public_id]?.open) { setCardShare(card.public_id, {open: false}); return; }
    const url = discoveryPage(card.public_id);
    setCardShare(card.public_id, {open: true, phase: 'ready', url});
    markUsage('d_share');
    track('Ask Discovery Share', {surface: SURFACE, view});
    setCardShare(card.public_id, {copied: await copyWhenReady(url)});
  }
  const copyCard = async card => setCardShare(card.public_id, {copied: await copyWhenReady(discoveryPage(card.public_id))});
  const sendCard = async card => {
    try { await navigator.share({title: card.question, url: discoveryPage(card.public_id)}); } catch { /* Closed, or not allowed here. */ }
  };
  const selectCardSource = (prefix, id) => {
    setCardSource(`${prefix}:${id}`);
    requestAnimationFrame(() => {
      const target = document.getElementById(`${prefix}-source-${id}`);
      target?.closest('details:not([open])')?.setAttribute('open', '');
      target?.scrollIntoView({behavior: scrollBehavior(), block: 'center'});
    });
  };
  const newChat = () => {
    if (busy) return;
    setMessages([]); setQuestion(''); setBackground({goal: '', facts: '', tried: ''}); setPersonal(false); setEditingSituation(false);
    questionFrom.current = 'typed';
    setSelected(''); setSourceTurn(null); setError('');
    conversationId.current = null;
    window.scrollTo({top: 0, behavior: scrollBehavior()});
    focusInput();
  };
  // A question from 我的提问 opens as the conversation, its answer as it came; it can be followed up.
  function openAsked(item) {
    if (busy) return;
    setHistoryOpen(false);
    setMessages([{id: Number(item.id) || Date.now(), question: item.question, intent: item.intent, context: '', personal: item.personal,
      model: item.model, result: item.result, askedAt: item.asked_at}]);
    setQuestion(''); setError(''); setSelected(''); setSourceTurn(null); setEditingSituation(false);
    conversationId.current = null;
    track('Ask History Open', {surface: SURFACE});
    window.scrollTo({top: 0});
  }
  const revokeConsent = () => {
    const saved = clearConsent();
    consented.current = '';
    revokedConsent.current = true;
    setAbout(null);
    setError(saved ? '已撤回AI数据发送同意。再次提问前会重新征得你的同意；仍可浏览已有问答。'
      : '本次已撤回AI数据发送同意，但本机没能保存撤回状态。下次打开时请再次撤回；仍可浏览已有问答。');
    focusInput();
  };

  async function submit(event, retry) {
    event?.preventDefault();
    if (abort.current || !storageReady || (!retry && !question.trim())) return;
    const scope = consentScope(meta);
    if (IN_APP && (!answerService(meta?.model) || (consented.current !== scope && (revokedConsent.current || !readConsent(scope))))) { setAsking({retry}); return; }
    if (loginStep !== 'pending') setLoginStep('');
    const history = messages.filter(m => m.result).slice(-6).map(m => ({question: m.question, summary: m.result.summary}));
    const ops = meta?.ops_logging?.enabled === true;
    if (ops && !conversationId.current) conversationId.current = crypto.randomUUID();
    const payload = {...(retry?.request || {question: question.trim(), context: situation, intent, history,
      query_log_notice: ops ? (publicArchive ? 'v4' : 'v3') : 'v1', ...(ops ? {conversation_id: conversationId.current} : {})}),
      ...(IN_APP ? {ai_consent_model: meta?.model || 'AI'} : {})};
    if (!retry) { markUsage('ask'); track('Ask Question', {surface: SURFACE, from: questionFrom.current}); questionFrom.current = 'typed'; }
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
        if (body?.code === 'ai_consent_changed') {
          consented.current = '';
          if (typeof body.model === 'string' && body.model.length <= 200) setMeta(prev => ({...prev, model: body.model}));
          else refreshMeta();
          throw failure(body.code, 'AI模型已更新，这次没有交给AI处理，也不扣次数。重新生成前会请你再次同意。');
        }
        // `scope: network`: a shared network's daily guest limit ran out, not this person's own 3.
        if (body?.code === 'quota_exhausted' && body.scope === 'network') throw failure('quota_exhausted', MESSAGES.networkQuota);
        if (body?.code === 'quota_exhausted') { seeQuota({remaining: 0}); throw failure('quota_exhausted', MESSAGES.quota); }
        if (body?.code === 'ops_storage_unavailable') throw failure(body.code, '未能确认问题保存，这次没有开始生成，也不扣次数。请重试。');
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
          if (value.status === 'answered') {
            markUsage('answer');
            setAskedHere(keepAsked({id: String(id), asked_at: new Date(id).toISOString(), question: payload.question, intent: payload.intent,
              personal: !!payload.context, model: meta?.model || 'AI', result: value}));
          }
          seeQuota(value.quota);
          update({result: value, partial: undefined, elapsed: Math.round((performance.now() - started) / 1000)});
        } else if (name === 'error') throw failure(value.code, value.code === 'answer_archive_failed' ? '这次回答未能确认归档，请重试；问题和已找到的材料仍保留。' : MESSAGES.failed);
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

  // A long image shares well in chat apps; the PDF keeps the source links clickable.
  async function exportTurn(kind, m) {
    markUsage('export');
    setExporting(`${m.id}-${kind}`);
    try {
      const {exportAnswer} = await import('./share.js');
      const {blob, name, type} = await exportAnswer(kind, {question: m.question, result: m.result, date: new Date(), personal: !!(m.context || m.personal)});
      const file = new File([blob], name, {type});
      if (kind === 'png' && window.matchMedia('(pointer: coarse)').matches && navigator.canShare?.({files: [file]})) {
        try { await navigator.share({files: [file], title: '问问立正'}); return; }
        catch (err) { if (err?.name === 'AbortError') return; }
      }
      const url = URL.createObjectURL(blob);
      const link = Object.assign(document.createElement('a'), {href: url, download: name});
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch { setError(MESSAGES.export); }
    finally { setExporting(''); }
  }

  // 分享这条回答. Sharing may give back one of today's questions, once a day, after one was used.
  const bonusOffer = !IN_APP && !IN_WECHAT && !!account?.enabled && !account.unavailable && !account.founding
    && account.share_bonus === true && (account.remaining ?? 3) < 3;
  const setShare = (id, patch) => setShares(prev => ({...prev, [id]: {...prev[id], ...patch}}));
  const toggleShare = m => {
    const open = !shares[m.id]?.open;
    setShare(m.id, {open, ...(!shares[m.id] ? {phase: 'confirm', error: ''} : {})});
    if (open) track('Ask Share', {surface: SURFACE, action: 'open'});
  };
  const openShare = m => {
    setShare(m.id, {open: true, ...(shares[m.id]?.phase === 'ready' ? {} : {phase: 'confirm', error: ''})});
    requestAnimationFrame(() => document.getElementById(`share-${m.id}`)?.scrollIntoView({behavior: scrollBehavior(), block: 'center'}));
  };
  async function createShare(m) {
    setShare(m.id, {phase: 'working', error: ''});
    const link = createShareLink(m.result.share, {surface: SURFACE, bonus: bonusOffer});
    const copiedLink = copyWhenReady(link.then(value => value.url));
    try {
      const value = await link;
      if (value.bonus === 'granted' || value.bonus === 'claimed')
        setAccount(prev => (prev ? {...prev, share_bonus: false, ...(Number.isFinite(value.remaining) ? {remaining: value.remaining} : {})} : prev));
      setShare(m.id, {phase: 'ready', url: value.url, bonus: value.bonus, copied: await copiedLink});
      track('Ask Share', {surface: SURFACE, action: value.bonus === 'granted' ? 'bonus' : 'link'});
    } catch {
      setShare(m.id, {phase: 'confirm', error: SHARE.failed});
    }
  }
  async function copyShare(m) {
    const done = await copyWhenReady(shares[m.id].url);
    setShare(m.id, {copied: done});
  }
  async function sendShare(m) {
    track('Ask Share', {surface: SURFACE, action: 'send'});
    try { await navigator.share({title: m.question, url: shares[m.id].url}); } catch { /* Closed, or not allowed here. */ }
  }
  // The answer above that the count line offers to share once the day's questions are used.
  const shareable = bonusOffer ? messages.filter(m => m.result?.share).at(-1) : undefined;

  async function copy(result) {
    const text = [
      result.summary,
      ...(result.sections || []).map(section => `${section.heading}\n${section.body}`),
      ...(result.sources || []).map(sourceCopyText),
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
        onChange={event => { setQuestion(event.target.value); if (!event.target.value.trim()) questionFrom.current = 'typed'; }} placeholder={conversation ? (mode === 'find' ? '还想读哪方面的？' : '接着问，或者换一个问题') : placeholder}
        onKeyDown={event => {
          if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); submit(); }
        }}/>
      {mode === 'ask' && personal && !showFields && <p className="situation-line">
        <span>结合你的处境：{filled.length ? filled.join('；') : '还没有填写'}</span>
        <button type="button" className="text-button" onClick={() => setEditingSituation(true)}>{filled.length ? '修改' : '填写'}</button>
      </p>}
      {showFields && <div className="background">
        <p>说清处境，比把问题包装好更有用。三项都可以空着，只写你愿意分享的部分。</p>
        {publicArchive && <p className="situation-note">{publicQa(meta, contextKept).situation}</p>}
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
            <Chev/>
          </label>}
        </div>
        <div className="send-group">
          {!busy && <kbd className="shortcut">{isMac ? '⌘ Enter' : 'Ctrl Enter'} 发送</kbd>}
          {busy
            ? <button type="button" className="send stop" onClick={() => abort.current?.abort()} aria-label="停止回答">停止</button>
            : <button type="submit" className="send" disabled={!storageReady || !question.trim() || outOfQuota} aria-label="发送问题">提问</button>}
        </div>
      </div>
    </form>
    <div className="composer-meta">
      <p className="notice">{storageReady ? (meta.ops_logging.enabled ? (publicArchive ? PUBLIC_QA.zh.notice : OPS_NOTICE) : NOTICE) : (meta?.settings_error ? '保存设置尚未确认。' : metaWaking ? '问答服务正在唤醒，通常十几秒，可以先写问题。' : '正在确认保存设置…')}<button type="button" className="text-button" onClick={() => openAbout(true)}>说明</button></p>
      <AccountLine account={account} waking={accountWaking} busy={busy} step={loginStep} foundingOpen={foundingOpen} onToggleFounding={() => { if (!foundingOpen) track('Ask Founding Info', {surface: SURFACE}); setFoundingOpen(open => !open); }} onLogin={login} onLoginHere={loginHere} onLogout={logout} onRetry={refreshAccount}/>
    </div>
    {outOfQuota && shareable && <p className="share-nudge"><button type="button" className="text-button" onClick={() => openShare(shareable)}>{SHARE.quota}</button></p>}
    {!IN_APP && foundingOpen && account?.enabled && !account.unavailable && !account.founding && <FoundingInfo account={account} busy={busy} step={loginStep} onLogin={login}/>}
    {error && messages.at(-1)?.error !== error && <p className="form-error" role="alert">{error}</p>}
    {meta?.settings_error && <p className="form-error">保存设置还未确认，暂时不能发送。<button type="button" className="text-button" onClick={refreshMeta}>重试</button></p>}
    {meta?.mode === 'search-only' && <p className="form-note">现在只能检索原文，模型连上后才能生成回答。</p>}
  </div>;

  // One question others asked; in 最常问, the topic's other questions open under it, each with its
  // own like and answer.
  const questionCard = (card, nested = false) => <QuestionCard key={card.public_id} card={card} nested={nested}
    open={(nested ? openNested : openCard) === card.public_id} detail={cardDetails[card.public_id]} like={likeOf(card)} share={cardShares[card.public_id]}
    feedback={cardFeedback[card.public_id]} selected={cardSource} onToggle={() => toggleCard(card, view, nested)}
    onSimilar={() => askSimilar(card)} onLike={() => void likeCard(card)} onShare={() => void shareCard(card)}
    onCopy={() => void copyCard(card)} onSend={() => void sendCard(card)} onSelect={selectCardSource} onFeedback={() => feedbackCard(card)}>
    {!nested && card.role === 'common' && card.similar?.length > 0 && <div className="qcard-similar">
      <button type="button" className="qcard-similar-toggle" aria-expanded={openSimilar.has(card.public_id)} onClick={() => toggleSimilar(card)}>
        {DISCOVERY.similar(card.similar.length)}<Chev/>
      </button>
      {openSimilar.has(card.public_id) && <div className="qcard-similar-list">{card.similar.map(item => questionCard(item, true))}</div>}
    </div>}
  </QuestionCard>;

  return <div className={`app ${conversation ? 'in-conversation' : ''}`}>
    <a className="skip-link" href="#main-content">跳到提问与回答</a>
    <header className="header">
      <div className="header-inner">
        <button type="button" className="brand" onClick={() => window.scrollTo({top: 0, behavior: scrollBehavior()})} aria-label="问问立正，回到页面顶部">
          <Mark/><span>问问立正</span>
        </button>
        <nav className="header-nav" aria-label="页面">
          {conversation && <button type="button" className="header-link new-chat" onClick={newChat} disabled={busy}>新问题</button>}
          {askedHere.length > 0 && <button type="button" className="header-link" aria-haspopup="dialog" onClick={() => setHistoryOpen(true)}>{HISTORY.title}</button>}
          <button type="button" className="header-link" onClick={() => openAbout(false)} aria-label="怎样用好它"><span className="long">怎样用好它</span><span className="short">说明</span></button>
          <a className="header-link site-link" href={LINKS.site} target="_blank" rel="noreferrer">lizheng.ai<Ext/></a>
        </nav>
      </div>
    </header>

    <main id="main-content" tabIndex={-1}>
      {!conversation ? <div className="home">
        <div className="stage"><div className={`stage-inner ${discoveryState !== 'none' ? 'with-live' : ''}`}>
        <section className="hero">
          <h1>{START.title[0]}<br/>{START.title[1]}</h1>
          <p className="intro">{START.lines.map(line => <span key={line} className="intro-line"><Phrases text={line}/></span>)}</p>
          {hasDiscovery && <a className="hero-live" href="#questions" onClick={toQuestions}>
            {askedRecently >= 3 ? `最近24小时 ${askedRecently >= DISCOVERY_LIST_SIZE ? `${DISCOVERY_LIST_SIZE}+` : askedRecently} 个新问题` : DISCOVERY.title}<span aria-hidden="true">↓</span>
          </a>}
          <p className="identity"><Phrases text={START.identity}/></p>
        </section>
        {composer}
        {APP_BADGES.length > 0 && <AppBadges/>}
        {discoveryState !== 'none' && <aside className="live" aria-labelledby="live-title" aria-busy={!questionLists}>
          <h2 id="live-title">{DISCOVERY.live}</h2>
          {askedRecently >= 3 && <p className="live-count">最近24小时 {askedRecently >= DISCOVERY_LIST_SIZE ? `${DISCOVERY_LIST_SIZE}+` : askedRecently} 个新问题</p>}
          <ol>{!questionLists ? [0, 1, 2].map(i => <li key={i} className="live-placeholder" aria-hidden="true"><i/><b/></li>)
            : liveCards.map(card => <li key={card.public_id}><button type="button" onClick={() => openFromLive(card)}>
              <time dateTime={card.asked_at} className={Date.now() - Date.parse(card.asked_at) < 3600000 ? 'fresh' : ''}>{askedAgo(card.asked_at)}</time>
              <span>{card.question}</span>
            </button></li>)}</ol>
          {questionLists && <a className="live-more" href="#questions" onClick={toQuestions}>{DISCOVERY.liveMore}<span aria-hidden="true">↓</span></a>}
        </aside>}
        </div></div>
        <div className="home-body">
        {discoveryState !== 'none' ? <section className="starters discovery" id="questions" aria-labelledby="discovery-title">
          <div className="starters-head"><h2 id="discovery-title">{DISCOVERY.title}</h2><p>{DISCOVERY.note}</p>
            <div className="discovery-views" role="group" aria-label="怎样看这些问题">
              {DISCOVERY_VIEWS.map(([id, label]) => <button type="button" key={id} aria-pressed={view === id} onClick={() => showView(id)}>{label}</button>)}
            </div></div>
          <div className="discovery-list" aria-busy={!questionLists}>
            {/* While the questions load, the rows they will fill; never the examples first. */}
            {!questionLists ? [0, 1, 2, 3].map(i => <div key={i} className="qcard-placeholder" aria-hidden="true"><i/><b/></div>)
              : discovery.map(card => questionCard(card))}
          </div>
          {/* The rest of the list here; past it, every question on lizheng.ai's public pages. */}
          {questionLists && (shownList.length > discovery.length
            ? <button type="button" className="pill-button discovery-more" onClick={showMore}>看更多问题<Chev/></button>
            : <a className="pill-button discovery-more" href="https://www.lizheng.ai/ask" target="_blank" rel="noopener"
              onClick={() => track('Ask Discovery All', {surface: SURFACE})}>看全部问题<Ext/></a>)}
        </section> : <section className="starters" aria-labelledby="starters-title">
          <div className="starters-head"><h2 id="starters-title">不知道从哪问起？</h2><p>选一个，改成你自己的问题。</p></div>
          <div className="starter-grid">
            {EXAMPLES.map(example => <button type="button" key={example.question} className="starter" onClick={() => prefill(example.question, example.intent, 'example')}>
              <span className="starter-tag">{example.tag}</span><span><Phrases text={example.question}/></span>
            </button>)}
          </div>
        </section>}
        <section className="materials" aria-labelledby="materials-title">
          <h2 id="materials-title">{MATERIALS.title}</h2>
          <div>
            <p className="materials-list"><Phrases text={materialsText(meta?.counts)}/></p>
            <p><Phrases text={refinedText(meta?.counts)}/></p>
            <p><Phrases text={MATERIALS.how}/></p>
            <p><a className="inline-link" href={LINKS.context} target="_blank" rel="noreferrer">{MATERIALS.open}<Ext/></a></p>
          </div>
        </section>
        <footer className="footer">
          <p>回答由AI根据公开材料整理，不是立正本人回复。材料更新于{meta?.context_date || '…'}。</p>
          <p><a href={LINKS.feedback} target="_blank" rel="noopener noreferrer" onClick={() => track('Ask Feedback', {surface: SURFACE, location: 'footer'})}>反馈或举报<Ext/></a><a href={LINKS.context} target="_blank" rel="noreferrer">材料开源在GitHub<Ext/></a><a href={LINKS.site} target="_blank" rel="noreferrer">lizheng.ai<Ext/></a></p>
        </footer>
        </div>
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
                  <span className="sr-only">你的问题</span>
                  <h2><Phrases text={m.question}/></h2>
                  {m.askedAt && <p className="question-when"><time dateTime={m.askedAt}>{askedOn(m.askedAt)}</time>问的，保存在这台设备上</p>}
                  {m.context && <details className="question-context"><summary>你的处境<Chev/></summary><p>{m.context}</p></details>}
                </header>
                {!m.result && (working || m.previewSources?.length > 0) && (working
                  ? <Working message={m} elapsed={elapsed} onStop={() => abort.current?.abort()} onSelect={select}/>
                  : <section className="working idle"><Candidates sources={m.previewSources} turnId={m.id} onSelect={select}/></section>)}
                {m.result && <div className="answer">
                  {m.result.status === 'sources-only'
                    ? <p className="answer-meta"><span>找到的材料</span></p>
                    : <p className="answer-meta"><span>AI根据公开材料整理</span><small>{m.model}{m.elapsed ? ` · ${m.elapsed}秒` : ''}</small></p>}
                  <div className="summary"><Markdown text={m.result.summary} sources={sources} onSelect={select}/></div>
                  {(m.result.sections || []).map((section, i) => <SectionBlock key={i} section={section} sources={sources} onSelect={select} personal={!!(m.context || m.personal)}/>)}
                  {m.result.clarifying_questions?.length > 0 && <div className="clarify">
                    <p>再补充一点，回答会更贴合你</p>
                    {m.result.clarifying_questions.map(item => <button type="button" key={item} onClick={() => {
                      setMode('ask'); setQuestion(m.question); setPersonal(true); setEditingSituation(true);
                      questionFrom.current = 'clarify';
                      setBackground(prev => ({...prev, facts: prev.facts ? `${prev.facts}\n${item}：` : `${item}：`}));
                      requestAnimationFrame(() => document.getElementById('context-facts')?.focus());
                    }}>{item}</button>)}
                  </div>}
                  {m.result.limitations && <Limits text={m.result.limitations} sources={sources} onSelect={select}/>}
                  {sources.length > 0 && <details className="inline-sources" open>
                    <summary>回到{sources.length}份原文<Chev/></summary>
                    <div>{sources.map(source => <SourceCard key={source.id} source={source} prefix={`turn-${m.id}`} selected={sourceTurn === m.id && selected === source.id} onOpen={id => { setSelected(id); setSourceTurn(m.id); }}/>)}</div>
                  </details>}
                  <div className="answer-actions" id={`share-${m.id}`}>
                    {m.result.status === 'answered' && <button type="button" className="ghost-button share-button" aria-expanded={!!shares[m.id]?.open} onClick={() => toggleShare(m)}>{bonusOffer && m.result.share ? SHARE.bonusLabel : SHARE.label}</button>}
                    <button type="button" className="ghost-button" onClick={() => copy(m.result)}>{copied === m.result ? '已复制' : m.result.status === 'sources-only' ? '复制这些出处' : '复制回答和出处'}</button>
                  </div>
                  {m.result.status === 'answered' && shares[m.id]?.open && <SharePanel state={shares[m.id]} link={!!m.result.share}
                    exporting={exporting === `${m.id}-png` ? 'png' : exporting === `${m.id}-pdf` ? 'pdf' : exporting ? 'other' : ''}
                    onCreate={() => createShare(m)} onCopy={() => copyShare(m)} onSend={() => sendShare(m)} onExport={kind => exportTurn(kind, m)}/>}
                  {last && m.result.followups?.length > 0 && <div className="followups">
                    <p>可以接着问<span>点一下放进输入框，改好再发</span></p>
                    {m.result.followups.map(item => <button type="button" key={item} onClick={() => prefill(item)}>{item}</button>)}
                  </div>}
                </div>}
                {m.error && (m.errorCode === 'quota_exhausted'
                  ? <div className="turn-alert quota" role="alert">
                      <p>{m.error}</p>
                      {account?.founding
                        ? loginStep === 'verified' && <b className="account-verified">验证成功，可以重新提问了。</b>
                        : <span className="quota-actions">
                            {account?.login_ready && !account.authenticated && <button type="button" className="pill-button" disabled={loginStep === 'pending'} onClick={login}>{loginStep === 'pending' ? '正在等待验证…' : '验证Founding身份'}</button>}
                            {!IN_APP && <a className="inline-link" href={stayLink('quota_card')} target="_blank" rel="noopener noreferrer" onClick={() => track('Ask Membership Click', {surface: SURFACE, location: 'quota_card'})}>如何成为Founding Member<Ext/></a>}
                          </span>}
                    </div>
                  : <p className={`turn-alert ${m.errorCode === 'stopped' ? 'muted' : ''}`} role="alert">{m.error}</p>)}
                {!busy && last && (m.errorCode !== 'quota_exhausted' || account?.founding || account?.remaining > 0) && (m.error || m.result?.retryable) && <button type="button" className="ghost-button retry" disabled={!storageReady} onClick={() => submit(undefined, m)}>重新生成回答</button>}
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
          <a className="rail-foot" href={LINKS.community} target="_blank" rel="noreferrer" onClick={() => track('Ask Community Click', {surface: SURFACE, location: 'rail'})}>去社区接着聊<Ext/></a>
        </aside>}
      </div>}
    </main>
    {historyOpen && <MyQuestions items={askedHere} busy={busy} close={() => setHistoryOpen(false)} onOpen={openAsked}
      onForget={id => setAskedHere(forgetAsked(id))} onForgetAll={() => { setAskedHere(forgetAll()); setHistoryOpen(false); }}/>}
    {about && <About close={() => setAbout(null)} meta={meta} account={account} focusInput={about.focusInput} publicArchive={publicArchive} contextKept={contextKept} onRevokeConsent={revokeConsent}/>}
    {asking && <AppConsent meta={meta} publicArchive={publicArchive} contextKept={contextKept} onCancel={() => { setAsking(null); focusInput(); }} onAgree={() => { const retry = asking.retry; consented.current = consentScope(meta); revokedConsent.current = false; saveConsent(consented.current); setAsking(null); void submit(undefined, retry); }}/>}
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
// Title fonts arrive after first paint; Songti and STSong stand in until then.
// They are self-hosted: Google Fonts is blocked in mainland China. Noto Serif SC is
// split by unicode-range, so browsers fetch only the slices a page uses.
import('@fontsource/noto-serif-sc/700.css');
import('@fontsource/noto-serif-sc/900.css');
