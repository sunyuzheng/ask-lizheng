import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import ReactMarkdown from 'react-markdown';
import {ArrowUp, ArrowUpRight, BookOpen, Check, ChevronDown, ChevronRight, Copy, FileText, Info, LoaderCircle, Menu, MessageCircle, Plus, Search, Sparkles, Square, Video, X} from 'lucide-react';
import './style.css';

const MODES = [
  {id:'understand',label:'想明白',icon:Sparkles,placeholder:'有什么你一直想弄明白的问题？'},
  {id:'apply',label:'聊聊我的问题',icon:MessageCircle,placeholder:'说说你正在做的事，以及卡住的地方。'},
  {id:'find',label:'找内容',icon:Search,placeholder:'想找立正讲过的哪个话题、例子或观点？'},
];
const EXAMPLES = [
  {tag:'学习与成长',question:'我做出了几个 AI 项目，怎么知道自己是真的学会了？',intent:'apply',hint:'从「做出来」到「学到自己身上」'},
  {tag:'工作与价值',question:'用 AI 效率变高了，为什么我的工作价值没变？',intent:'understand',hint:'效率、成果，以及 fake work'},
  {tag:'产品与判断',question:'有一个能跑的 demo，怎么判断值不值得继续做？',intent:'apply',hint:'把展示变成真实使用，要跨过什么'},
  {tag:'创作与表达',question:'想开始做自媒体，最应该先想清楚什么？',intent:'understand',hint:'先想清楚，你想放大什么'},
  {tag:'认知与人生',question:'立正说的「良质」是什么意思，和 AI 有什么关系？',intent:'understand',hint:'从一个概念，回到具体经验'},
  {tag:'回到原文',question:'帮我找立正关于职业选择和个人价值的文章与视频。',intent:'find',hint:'找到文章，也找到视频里的那一刻'},
];
const LINKS = {context:'https://github.com/sunyuzheng/lizheng-open-context',site:'https://www.lizheng.ai',community:'https://www.superlinear.academy/c/tools/lizheng-context'};

function Mark({small=false}) {return <span className={`brand-mark ${small?'small':''}`} aria-hidden="true"><MessageCircle strokeWidth={1.8}/><i/></span>;}
function Kind({kind}) {return <span className={`kind ${kind}`}>{kind==='application'?'结合你的处境':kind==='source'?'材料中的观点':'AI 综合'}</span>;}
function sourceKind(source) {return source.source_type?.includes('video')?<Video size={15}/>:<FileText size={15}/>;}
function sourceLabel(source) {return source.source_type?.includes('video')?'视频':source.source_type==='context'?'AI 整理':'文章';}
function SourceCard({source,selected,onSelect,prefix='rail'}) {
  return <article id={`${prefix}-source-${source.id}`} className={`source-card ${selected===source.id?'selected':''}`}>
    <div className="source-meta"><span>{sourceKind(source)}{sourceLabel(source)}</span><time>{source.date?.slice(0,10)||'日期未标明'}</time></div>
    <a className="source-title" href={source.url} target="_blank" rel="noreferrer">{source.title}<ArrowUpRight size={17}/></a>
    {source.timecode&&<a className="time-link" href={source.url} target="_blank" rel="noreferrer">从 {source.timecode} 开始看 <ChevronRight size={13}/></a>}
    {source.reason&&<p className="source-reason">{source.reason}</p>}
    <details onToggle={e=>{if(e.currentTarget.open)onSelect(source.id);}}><summary>查看材料片段 <ChevronDown size={13}/></summary><p className="excerpt">{source.excerpt}</p><p className="attribution">{source.author&&`${source.author} · `}{source.attribution_note}</p>{source.public_copy_url&&<a className="public-copy" href={source.public_copy_url} target="_blank" rel="noreferrer">阅读公开资料副本 <ArrowUpRight size={13}/></a>}</details>
  </article>;
}
function Markdown({text,sources,onSelect}) {
  const content=String(text||'').replace(/\[(S\d+)\](?!\()/g,'[$1](#cite-$1)');
  return <ReactMarkdown components={{a:({href,children})=>{
    if(href?.startsWith('#cite-')) {const id=href.slice(6);return sources.some(s=>s.id===id)?<button className="cite" onClick={()=>onSelect(id)} aria-label={`查看来源 ${id}`}>{id.slice(1)}</button>:null;}
    return <span>{children}</span>;
  }}}>{content}</ReactMarkdown>;
}

function About({close,meta}) {
  const ref=useRef(null);
  useEffect(()=>{const previous=document.activeElement;ref.current?.focus();const handler=e=>{if(e.key==='Escape')close();if(e.key==='Tab'){const items=ref.current?.querySelectorAll('button,a');const first=items?.[0],last=items?.[items.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}};document.addEventListener('keydown',handler);return()=>{document.removeEventListener('keydown',handler);previous?.focus();};},[]);
  return <div className="modal-backdrop" onClick={close}><section className="about-modal" role="dialog" aria-modal="true" aria-labelledby="about-title" tabIndex={-1} ref={ref} onClick={e=>e.stopPropagation()}><button className="icon-button modal-close" aria-label="关闭说明" onClick={close}><X size={20}/></button><Mark/><p className="eyebrow">一个通向公开材料的入口</p><h2 id="about-title">怎样用好问问立正</h2><p>它根据立正公开的文章、视频与《真本事》框架，帮你找到相关内容、理解观点，并联系自己的处境继续思考。回答由 AI 生成，立正本人没有实时参与。</p><div className="about-row"><span>问得更具体</span><p>与其问「我该怎么办」，可以说想达到什么、发生了什么、试过什么，以及自己认为卡点在哪里。不需要先把问题整理完美。</p></div><div className="about-row"><span>把答案带回现实</span><p>AI 可以整理材料、提出假设。你的具体情况未必在材料里；建议是否适用，需要你通过行动和反馈判断。也欢迎直接追问：这个判断成立的条件是什么？</p></div><div className="about-row"><span>随时回到出处</span><p>文章保留日期，视频尽可能链接到具体时间点。嘉宾的观点归嘉宾；AI 整理与推断也会标明。AI 也可能读错或漏掉条件；遇到重要判断，请打开出处核对。现有材料没有涉及的事，会说明资料不足。</p></div><div className="about-row"><span>关于你的输入</span><p>本项目不建立对话数据库，不记录问题正文；当前对话仅在页面内存里，刷新就会清除。提问内容与必要上下文会发送给 Builder Space 的模型服务处理，其处理规则由该服务管理。请只提供愿意交给 AI 处理的信息。</p></div><div className="about-footer"><span>材料版本 {meta?.context_date||'读取中'}</span><a href={LINKS.context} target="_blank" rel="noreferrer">查看 Open Context <ArrowUpRight size={14}/></a></div></section></div>;
}

function App() {
  const [meta,setMeta]=useState(null),[intent,setIntent]=useState('understand'),[question,setQuestion]=useState('');
  const [showContext,setShowContext]=useState(false),[background,setBackground]=useState({goal:'',facts:'',tried:''});
  const [messages,setMessages]=useState([]),[busy,setBusy]=useState(false),[stage,setStage]=useState(''),[error,setError]=useState('');
  const [about,setAbout]=useState(false),[nav,setNav]=useState(false),[selected,setSelected]=useState(''),[sourceTurn,setSourceTurn]=useState(null),[copied,setCopied]=useState(false);
  const input=useRef(null),abort=useRef(null),end=useRef(null);
  useEffect(()=>{fetch('/api/meta').then(r=>r.ok?r.json():Promise.reject()).then(setMeta).catch(()=>setMeta({offline:true}));},[]);
  useEffect(()=>{if(messages.length)end.current?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'end'});},[messages.length]);
  useEffect(()=>{if(!busy)return;const timer=setTimeout(()=>setStage('还在结合材料整理，你可以随时停止。'),20000);return()=>clearTimeout(timer);},[busy]);
  const latest=messages.filter(m=>m.result).at(-1)?.result;
  const sources=(messages.find(m=>m.id===sourceTurn)?.result||latest)?.sources||[];
  const selectSource=(id,turnId)=>{setSourceTurn(turnId);setSelected(id);requestAnimationFrame(()=>{const rail=document.getElementById(`rail-source-${id}`);const target=rail?.getClientRects().length?rail:document.getElementById(`turn-${turnId}-source-${id}`);target?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});});};
  const prefill=(value,mode=intent)=>{setIntent(mode);setQuestion(value);setError('');setNav(false);requestAnimationFrame(()=>{input.current?.focus();input.current?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});});};
  const newChat=()=>{if(busy)return;setMessages([]);setQuestion('');setBackground({goal:'',facts:'',tried:''});setShowContext(false);setSelected('');setSourceTurn(null);setError('');setNav(false);input.current?.focus();};
  async function submit(e) {
    e?.preventDefault();if(busy||!question.trim())return;
    const text=question.trim(),context=Object.entries(background).filter(([,v])=>v.trim()).map(([k,v])=>`${{goal:'希望达到的结果',facts:'目前的情况与限制',tried:'已经试过的办法'}[k]}：${v.trim()}`).join('\n');
    const history=messages.filter(m=>m.result).slice(-6).map(m=>({question:m.question,summary:m.result.summary}));
    const id=Date.now();setMessages(prev=>[...prev,{id,question:text,intent,context}]);setQuestion('');setBusy(true);setError('');setStage('正在找相关的文章与视频…');setSelected('');setSourceTurn(null);
    abort.current=new AbortController();
    try {
      const response=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:text,context,intent,history}),signal:abort.current.signal});
      if(!response.ok){let body;try{body=await response.json();}catch{}throw new Error(body?.detail||body?.message||(response.status===429?'现在提问的人有点多，请稍后再试。':'暂时没能连接，请稍后重试。'));}
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',received=false;
      const process=frame=>{let event='message',data=[];for(const line of frame.split('\n')){if(line.startsWith('event:'))event=line.slice(6).trim();if(line.startsWith('data:'))data.push(line.slice(5).trim());}if(!data.length)return;const value=JSON.parse(data.join('\n'));if(event==='progress')setStage(value.message||'正在整理回答…');else if(event==='result'){received=true;setMessages(prev=>prev.map(m=>m.id===id?{...m,result:value}:m));}else if(event==='error')throw new Error(value.message||'回答暂时未完成，可以重试。');};
      while(true){const {done,value}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});buffer=buffer.replace(/\r\n/g,'\n');let index;while((index=buffer.indexOf('\n\n'))!==-1){process(buffer.slice(0,index));buffer=buffer.slice(index+2);}if(done){if(buffer.trim())process(buffer);break;}}
      if(!received)throw new Error('连接中断了，还没有得到完整回答。你的问题已保留，可以重试。');
    } catch(err) {const message=err.name==='AbortError'?'已停止。你的问题还在，可以修改后重试。':String(err.message||'暂时没有完成，请重试。');setError(message);setMessages(prev=>prev.map(m=>m.id===id?{...m,error:message}:m));setQuestion(text);}
    finally {setBusy(false);setStage('');abort.current=null;}
  }
  async function copy(result){const text=[result.summary,...(result.sections||[]).map(s=>`${s.heading}\n${s.body}`),...(result.sources||[]).map(s=>`${s.title}（${s.date?.slice(0,10)||''}）\n${s.url}`),'由问问立正 AI 根据公开材料整理，非本人实时回复。'].join('\n\n');try{await navigator.clipboard.writeText(text);setCopied(result);setTimeout(()=>setCopied(false),1800);}catch{setError('浏览器未允许复制，请直接选择回答文字。');}}
  const mode=MODES.find(m=>m.id===intent);
  return <div className={`app ${messages.length?'conversation':''}`}><a className="skip-link" href="#main-content">跳到提问与回答</a>
    {nav&&<div className="nav-shade" onClick={()=>setNav(false)}/>}
    <aside className={`sidebar ${nav?'open':''}`}>
      <button className="brand" onClick={newChat} aria-label="问问立正，开始新对话"><Mark small/><span>问问立正<small>ASK LIZHENG</small></span></button>
      <button className="new-chat" onClick={newChat} disabled={busy}><Plus size={18}/> 开始一个新问题</button>
      <div className="side-section"><span className="side-label">从这里开始</span><button onClick={()=>prefill('立正说的「做点真东西」，到底是什么意思？','understand')}><BookOpen size={16}/>理解一个观点</button><button onClick={()=>{setIntent('apply');setShowContext(true);input.current?.focus();setNav(false);}}><MessageCircle size={16}/>带着自己的处境来</button><button onClick={()=>{setIntent('find');input.current?.focus();setNav(false);}}><Search size={16}/>找文章与视频</button></div>
      {!!messages.length&&<div className="side-section recent"><span className="side-label">这次聊到的</span>{messages.slice(-5).map(m=><button key={m.id} onClick={()=>{document.getElementById(`turn-${m.id}`)?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});setNav(false);}}>{m.question}</button>)}</div>}
      <div className="sidebar-bottom"><div className="context-note"><span className="live-dot"/>来自 Open Context<p>公开材料，有出处，也有时间。</p></div><a href={LINKS.context} target="_blank" rel="noreferrer">浏览原始资料 <ArrowUpRight size={15}/></a><button onClick={()=>setAbout(true)}><Info size={15}/> 关于问问立正</button><div className="side-signature">学点真本事，做点真东西。</div></div>
    </aside>
    <div className="workspace"><header className="topbar"><button className="icon-button mobile-menu" aria-label="打开导航" onClick={()=>setNav(true)}><Menu size={21}/></button><b className="mobile-name">问问立正</b><span>一个问题，可以从这里继续想。</span><button onClick={()=>setAbout(true)}>怎样用好它 <ArrowUpRight size={14}/></button></header>
      <main id="main-content" className="main-area" tabIndex={-1}>{messages.length>0&&<h1 className="sr-only">问问立正，当前对话</h1>}
        {!messages.length?<section className="welcome"><p className="eyebrow"><span className="live-dot"/> 从公开材料出发</p><h1>把一个问题，<br/><em>问得更明白。</em></h1><p className="intro">读过一些文章，看过一些视频，<br className="mobile-break"/>不妨带着自己的问题，再往前想一步。</p><p className="identity">根据立正公开内容回答的 AI · 不是本人实时回复</p></section>:<section className="thread" aria-label="当前对话">
          {messages.map((m,index)=><article key={m.id} id={`turn-${m.id}`} className="turn"><div className="user-question"><span className="question-label">你的问题</span><h2>{m.question}</h2>{m.context&&<details className="submitted-context"><summary>补充的背景 <ChevronDown size={13}/></summary><p>{m.context}</p></details>}</div>
            {m.result&&<div className="answer"><div className="answer-meta"><Mark small/><span>问问立正 <small>{m.result.status==='sources-only'?'找到的材料':'AI 根据公开材料整理'}</small></span></div><div className="answer-summary"><Markdown text={m.result.summary} sources={m.result.sources||[]} onSelect={id=>selectSource(id,m.id)}/></div>
              {(m.result.sections||[]).map((s,i)=><section className="answer-section" key={i}><div className="section-title"><h3>{s.heading}</h3><Kind kind={s.kind}/></div><Markdown text={s.body} sources={m.result.sources||[]} onSelect={id=>selectSource(id,m.id)}/>{s.source_ids?.length>0&&<div className="section-citations">相关材料 {s.source_ids.map(id=><button key={id} onClick={()=>selectSource(id,m.id)}>{id.slice(1)}</button>)}</div>}</section>)}
              {m.result.clarifying_questions?.length>0&&<div className="clarifications"><p>再补充一点，就能继续往下想</p>{m.result.clarifying_questions.map(q=><button key={q} onClick={()=>{setIntent('apply');setQuestion(m.question);setShowContext(true);setBackground(prev=>({...prev,facts:prev.facts?`${prev.facts}\n${q}：`:`${q}：`}));requestAnimationFrame(()=>document.getElementById('context-facts')?.focus());}}>{q}<Plus size={15}/></button>)}</div>}
              {m.result.limitations&&<p className="limitations">{m.result.limitations}</p>}
              {m.result.sources?.length>0&&<details className="inline-sources" open><summary><BookOpen size={16}/>回到 {m.result.sources.length} 份相关材料<ChevronDown size={16}/></summary><div>{m.result.sources.map(s=><SourceCard key={s.id} source={s} prefix={`turn-${m.id}`} selected={sourceTurn===m.id?selected:''} onSelect={id=>{setSelected(id);setSourceTurn(m.id);}}/>)}</div></details>}
              <div className="answer-actions"><button onClick={()=>copy(m.result)}>{copied===m.result?<Check size={15}/>:<Copy size={15}/>} {copied===m.result?'已复制':'复制回答与出处'}</button><span>建议的适用条件，欢迎继续追问。</span></div>
              {index===messages.length-1&&m.result.followups?.length>0&&<div className="followups"><p>可以继续问</p>{m.result.followups.map(q=><button key={q} onClick={()=>prefill(q,'apply')}>{q}<ArrowUpRight size={15}/></button>)}</div>}
            </div>}
            {m.error&&<p className="turn-error" role="alert">{m.error}</p>}
          </article>)}
          {busy&&<div className="progress" role="status"><LoaderCircle size={18} className="spin"/><span>{stage}</span><button onClick={()=>abort.current?.abort()}><Square size={12}/>停止</button></div>}
          <div ref={end}/>
        </section>}
        <div className="composer-wrap"><form className={`composer ${busy?'is-busy':''}`} onSubmit={submit}><div className="mode-tabs" role="group" aria-label="这次想怎样使用"><div>{MODES.map(m=><button key={m.id} type="button" aria-pressed={intent===m.id} className={intent===m.id?'active':''} onClick={()=>setIntent(m.id)}><m.icon size={15}/>{m.label}</button>)}</div><span className="context-version">材料 {meta?.context_date||'版本读取中'}</span></div><label className="sr-only" htmlFor="question">你的问题</label><textarea id="question" ref={input} maxLength={2000} value={question} onChange={e=>setQuestion(e.target.value)} placeholder={mode.placeholder} rows={messages.length?2:3} onKeyDown={e=>{if((e.metaKey||e.ctrlKey)&&e.key==='Enter'&&!e.nativeEvent.isComposing){e.preventDefault();submit();}}}/>
          <div className="composer-bottom"><button type="button" className={`background-toggle ${showContext?'active':''}`} aria-expanded={showContext} onClick={()=>setShowContext(!showContext)}><Plus size={16}/>补充一点背景 <span>选填</span></button><div><span className="keyboard-hint">⌘ / Ctrl + Enter</span><button className="send" type="submit" disabled={busy||!question.trim()} aria-label="发送问题">{busy?<LoaderCircle size={18} className="spin"/>:<ArrowUp size={20}/>}</button></div></div>
          {showContext&&<div className="context-fields"><p>说清处境，比把问题包装好更有帮助。只写你愿意分享的部分。</p><label>希望达到什么结果<input id="context-goal" maxLength={700} value={background.goal} onChange={e=>setBackground({...background,goal:e.target.value})} placeholder="例如：让产品获得第一批持续使用的用户"/></label><label>现在的情况、卡点或限制<textarea id="context-facts" maxLength={1000} value={background.facts} onChange={e=>setBackground({...background,facts:e.target.value})} placeholder="发生了什么？你认为卡在哪？有哪些不能忽略的条件？" rows={2}/></label><label>已经试过什么<input id="context-tried" maxLength={700} value={background.tried} onChange={e=>setBackground({...background,tried:e.target.value})} placeholder="做过哪些尝试，得到什么反馈？"/></label></div>}
        </form>{error&&messages.at(-1)?.error!==error&&<p className="form-error" role="alert">{error}</p>}<p className="composer-caption">{intent==='find'?'会优先帮你找到相关出处；视频尽可能定位到具体时间。':intent==='apply'?'说说目标、事实和你的判断。AI 会联系材料，也会指出还缺什么。':'可以问一个概念、一个分歧，也可以直接说：这和我的情况有什么关系？'}</p>{meta?.offline&&<p className="form-error">后端尚未连接。问题可以先写好，连接恢复后再发送。</p>}{meta?.mode==='search-only'&&<p className="search-only-note">当前仅检索公开材料，模型连接后可生成回答。</p>}</div>
        {!messages.length&&<section className="starters"><div className="starters-heading"><h2>还没想好从哪问？</h2><span>选一个，再改成自己的问题。</span></div><div className="example-grid">{EXAMPLES.map((e,i)=><button key={e.question} className="example" onClick={()=>prefill(e.question,e.intent)}><span className="example-top"><span>{e.tag}</span><ArrowUpRight size={17}/></span><h3>{e.question}</h3><p>{e.hint}</p></button>)}</div></section>}
        {!messages.length&&<footer className="home-footer"><span>Context 开放，问题也开放。</span><a href={LINKS.context} target="_blank" rel="noreferrer">自己做一个版本 <ArrowUpRight size={14}/></a></footer>}
      </main>
    </div>
    {!!sources.length&&<aside className="source-rail"><div className="rail-heading"><BookOpen size={18}/><div><h2>回到出处</h2><p>看原文，也看它为何相关。</p></div></div>{sources.map(s=><SourceCard key={s.id} source={s} selected={selected} onSelect={setSelected}/>)}<a className="rail-footer" href={LINKS.community} target="_blank" rel="noreferrer">把新的问题带回社区<ArrowUpRight size={15}/></a></aside>}
    {about&&<About close={()=>setAbout(false)} meta={meta}/>}
  </div>;
}
createRoot(document.getElementById('root')).render(<App/>);
