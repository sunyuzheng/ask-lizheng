import React, {useEffect, useMemo, useRef, useState} from 'react';
import {AnswerMap, loadAnswerMap, sourceItem} from './answer-map.js';

const STEPS = ['查找原文', '匹配材料', '整理回答', '核对出处'];
const STAGE = {retrieving: 0, matching: 1, thinking: 2, drafting: 2, checking: 3, repairing: 3};

/**
 * What the stream has shown so far, in the map's terms: every candidate the answer may draw on,
 * and the sources it cites with the numbers its text uses. The final answer numbers its sources
 * 1 to n afresh, so a cited source finds its light by its own public copy, never by number.
 * Saved answers carry only their sources.
 */
export function answerMapState(map, message, live, selected = null) {
  const result = message.result;
  const shown = result ? result.sources || [] : [...(message.previewSources || []), ...(message.partial?.sources || [])];
  const sources = new Map(shown.map(source => [source.id, source]));
  const candidates = message.mapCandidates?.length
    ? message.mapCandidates.map(row => ({id: row.id, item: sourceItem(map, null, row.key)}))
    : (message.previewSources || []).map(source => ({id: source.id, item: sourceItem(map, source)}));
  // While it is written, the partial answer keeps the candidates' numbers; afterwards the answer's own.
  const cited = (result ? result.sources || [] : message.partial?.sources || [])
    .map(source => ({id: source.id, item: result ? sourceItem(map, source) : candidates.find(row => row.id === source.id)?.item ?? sourceItem(map, source)}));
  const cards = (message.approach?.cards || []).filter(card => map.cardIndex.has(card.id));
  return {
    live: live && !result, done: !!result,
    stage: result ? 'done' : message.progress?.stage || 'retrieving',
    candidates, cards, cited, sources, selected,
  };
}

/**
 * The answer map with what it means: steps while the answer is found, then what the answer
 * used. `live` while the stream runs; afterwards the cited sources keep their numbers, and a
 * number, like a citation in the text, opens its source. `selected` is the source open beside
 * the answer, whichever way it was opened; its light is marked.
 */
export function AnswerMapPanel({message, live = false, selected = null, onSelect}) {
  const canvas = useRef(null);
  const view = useRef(null);
  const select = useRef(onSelect);
  select.current = onSelect;
  const [map, setMap] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    loadAnswerMap().then(data => { if (alive) setMap(data); }, () => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!map || !canvas.current) return undefined;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    view.current = new AnswerMap(canvas.current, map, {onSelect: id => select.current?.(id), reduce});
    return () => { view.current?.destroy(); view.current = null; };
  }, [map]);

  const state = useMemo(() => (map ? answerMapState(map, message, live, selected) : null),
    [map, message.previewSources, message.mapCandidates, message.approach, message.partial, message.result, message.progress?.stage, live, selected]);
  useEffect(() => { if (state) view.current?.update(state); }, [state]);

  const step = STAGE[message.progress?.stage] ?? 0;
  const steps = <ol className="answer-map-steps" aria-label="处理步骤">
    {STEPS.map((label, index) => <li key={label} className={index < step ? 'done' : index === step ? 'current' : ''} aria-current={index === step ? 'step' : undefined}>{label}</li>)}
  </ol>;
  // Without the map the steps still say where the answer is.
  if (failed) return live ? <div className="answer-map-fallback">{steps}</div> : null;
  const found = state?.candidates.filter(row => row.item >= 0).length || 0;
  const cards = state?.cards || [];
  const numbered = message.result?.sources?.length || 0;

  return <figure className={`answer-map ${live ? 'answer-map-live' : 'answer-map-done'}`}>
    <div className="answer-map-stage">
      <canvas ref={canvas} aria-hidden="true"/>
    </div>
    <figcaption>
      {live ? steps : numbered > 0 && <p className="answer-map-lead">
        图上标号的是{message.result.status === 'sources-only' ? '找到' : '回答引用'}的{numbered}份材料，点编号看原文。
      </p>}
      {((live && found > 0) || cards.length > 0) && <p className="answer-map-counts">
        {live && found > 0 && <span>{found}份候选材料</span>}
        {cards.map(card => <span key={card.id} className="answer-map-card"><i aria-hidden="true"/>判断卡：{card.title}</span>)}
      </p>}
      <p className="answer-map-legend">
        <span><i className="dot video"/>视频与对谈</span><span><i className="dot text"/>文字</span><span><i className="dot course"/>课程与书</span><span><i className="diamond"/>判断卡</span>
        <span className="answer-map-axes">越往外越新，方向是主题</span>
      </p>
    </figcaption>
  </figure>;
}

/**
 * The start screen's map: every material, turning slowly, with the sources of one recent public
 * answer lit (`sources`, null while it loads). Not pointed at; the list beside it chooses.
 */
export function AmbientMap({sources}) {
  const canvas = useRef(null);
  const view = useRef(null);
  const [map, setMap] = useState(null);

  useEffect(() => {
    let alive = true;
    loadAnswerMap().then(data => { if (alive) setMap(data); }, () => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!map || !canvas.current) return undefined;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    view.current = new AnswerMap(canvas.current, map, {ambient: true, reduce});
    return () => { view.current?.destroy(); view.current = null; };
  }, [map]);

  useEffect(() => {
    if (!map || !view.current) return;
    const candidates = (sources || []).map(source => ({id: source.id, item: sourceItem(map, source)}));
    view.current.update({live: false, done: false, stage: 'ambient', candidates, cards: [], cited: [], sources: new Map(), selected: null});
  }, [map, sources]);

  return <div className="ambient-map"><canvas ref={canvas} aria-hidden="true"/></div>;
}
